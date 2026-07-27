"""Identity-scorer calibration (#13 labeling harness + #14 logistic regression).

The scorer combines signals via noisy-OR (1 - prod(1 - wi*ci)) with hand-set
weights. That assumes signal independence (it isn't — username_exact and
real_name_fuzzy correlate) and the weights are uncalibrated, so the threshold is
guesswork. This module lets you train a calibrated classifier on a small labeled
set instead.

Workflow:
  1. `python -m src.pipeline.identity_calibration export [csv]`
       -> writes candidate pairs + per-signal feature vectors + a blank `label`
          column. You fill label with 1 (same person) / 0 (different).
  2. `python -m src.pipeline.identity_calibration train [csv] [model.joblib]`
       -> trains sklearn LogisticRegression, reports CV metrics, persists model.
  3. identity_scorer auto-loads the model (IDENTITY_MODEL_PATH) and uses its
     calibrated probability INSTEAD of noisy-OR. **No model present -> noisy-OR
     fallback**, so nothing changes until you actually train one.

The feature vector is the per-pair MAX confidence for each signal type (fixed
order below), so logistic regression learns a proper weight per signal AND can
account for correlation between them.
"""
import csv
import json
import logging
import os

logger = logging.getLogger(__name__)

# Stable feature order for CSV/model compatibility. New signal types append here
# (retrain after) so existing persisted feature snapshots keep their positions.
# Deprecated non-identity slots remain present but are forced to 0.0 below.
FEATURE_ORDER = [
    "email_match", "phone_match", "bio_mention", "cross_platform_link",
    "content_similarity", "temporal_copost", "shared_website",
    "shared_route_origin", "group_cooccurrence", "media_gps_colocation",
    "media_perceptual_match", "media_face_match", "media_device_match",
    "username_similar",
    # Axis-3 Change-3: cross-entity ArcFace kNN signal (portrait-only,
    # >=FACE_PAIR_KNN_MIN_MATCHES matches). Appended — do not reorder.
    "face_pair_knn",
    # Axis-1 MVP: entity-centroid cosine over timeline_embeddings. 16th entry —
    # append-only; do not reorder (would invalidate every persisted feature
    # snapshot in identity_labels).
    "topical_similarity",
    # NER enrichment: shared rare ORG/school/location (spaCy). Emitted by
    # src/pipeline/shared_life_context.py. Append-only (17th entry) — do not
    # reorder or every persisted feature snapshot in identity_labels breaks.
    "shared_life_context",
    # Face social graph (2026-07-08): entity B's primary face matched entity A's
    # face_association at cosine >= 0.55. Emitted by
    # src/pipeline/social_face_link.py. Append-only — must be LAST at write time
    # so existing persisted feature vectors and trained models keep their
    # positions. Tolerates any additional appends by parallel subagents.
    "social_face_link",
    # No-auto-merge resolver evidence (2026-07-20): deterministic signals that
    # used to live inside auto-clustered entities are now emitted between two
    # separate candidate entities for human review. Append-only.
    "username_exact", "real_name_fuzzy", "whatsapp_phone", "commit_email",
    "profile_photo_sha256",
]

# Context signals are useful to explain relationships and review candidates, but
# they are not same-person evidence. Keep their historical feature slots so old
# labels/models keep the same shape, but never feed a non-zero value to training
# or inference.
DEPRECATED_NON_IDENTITY_FEATURES: frozenset[str] = frozenset({
    "temporal_copost",
    "bio_mention",
    "group_cooccurrence",
    "topical_similarity",
    "social_face_link",
    "shared_life_context",
})
ACTIVE_FEATURE_ORDER = [t for t in FEATURE_ORDER if t not in DEPRECATED_NON_IDENTITY_FEATURES]

_MODEL_PATH = os.getenv(
    "IDENTITY_MODEL_PATH",
    os.path.join(os.getenv("MEDIA_DERIVED_PATH", "/app/media_derived"),
                 "models", "identity_calibration.joblib"),
)

_model_cache = None
_model_loaded = False


def pair_feature_vector(contributions: list[tuple[str, float]]) -> list[float]:
    """contributions = [(signal_type, confidence), ...] for one entity pair ->
    fixed-length feature vector (max confidence per signal type)."""
    by_type: dict[str, float] = {}
    for sig_type, conf in contributions:
        if sig_type in DEPRECATED_NON_IDENTITY_FEATURES:
            continue
        if conf > by_type.get(sig_type, 0.0):
            by_type[sig_type] = conf
    return [by_type.get(t, 0.0) for t in FEATURE_ORDER]


def _feature_value(feats: dict, signal_type: str) -> float:
    if signal_type in DEPRECATED_NON_IDENTITY_FEATURES:
        return 0.0
    return float(feats.get(signal_type, 0.0))


def get_model():
    """Return the trained model or None (cached). None -> caller uses noisy-OR.

    Safety: gated by IDENTITY_MODEL_ENABLED (default '0'/off). Set to '1' only
    after `python -m src.pipeline.identity_calibration validate` shows the LR
    model beats noisy-OR on your labeled set. This prevents an accidentally
    trained model from silently replacing the hand-set weights before it has
    been proven better."""
    global _model_cache, _model_loaded
    if _model_loaded:
        return _model_cache
    _model_loaded = True
    if os.getenv("IDENTITY_MODEL_ENABLED", "0") != "1":
        _model_cache = None
        return None
    if not os.path.isfile(_MODEL_PATH):
        _model_cache = None
        return None
    try:
        import joblib
        _model_cache = joblib.load(_MODEL_PATH)
        logger.info("Identity calibration: loaded model from %s", _MODEL_PATH)
    except Exception:
        logger.exception("Failed to load identity calibration model; using noisy-OR")
        _model_cache = None
    return _model_cache


def predict_proba(model, features: list[float]) -> float:
    """Calibrated P(same person) for one feature vector."""
    import numpy as np
    return float(model.predict_proba(np.asarray([features], dtype=float))[0, 1])


# --------------------------------------------------------------------------- #
# Offline tools (run as a module, not in the scheduler loop).
# --------------------------------------------------------------------------- #
async def _aggregate_pairs() -> list[dict]:
    """Replicate the scorer's per-pair signal aggregation (so export features
    match what the scorer will feed the model). Returns rows with names + vector."""
    from src.db.connection import init_pools, close_pools, get_analyzer_pool, has_analyzer_pool

    owns_pool = not has_analyzer_pool()
    if owns_pool:
        await init_pools(apply_schema_ddl=False)
    try:
        analyzer = get_analyzer_pool()
        async with analyzer.acquire() as conn:
            sigs = await conn.fetch("""
                SELECT entity_id::text, signal_type, target_platform, target_record_id, confidence
                FROM identity_signals WHERE signal_type = ANY($1::text[])
            """, ACTIVE_FEATURE_ORDER)
            links = await conn.fetch(
                "SELECT entity_id::text, source, platform_id FROM entity_platform_links")
            names = await conn.fetch("SELECT id::text, canonical_name FROM entities")
    finally:
        if owns_pool:
            await close_pools()

    pid_to_entity = {
        (link["source"], link["platform_id"]): link["entity_id"]
        for link in links
    }
    name_of = {n["id"]: n["canonical_name"] for n in names}

    pairs: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for r in sigs:
        a = r["entity_id"]
        conf = float(r["confidence"] or 0.0)
        if r["signal_type"] == "bio_mention":
            b = pid_to_entity.get((r["target_platform"], r["target_record_id"]))
        else:
            b = r["target_record_id"]
        if not b or b == a:
            continue
        key = (a, b) if a < b else (b, a)
        pairs.setdefault(key, []).append((r["signal_type"], conf))

    out = []
    for (a, b), contribs in pairs.items():
        out.append({
            "entity_a": a, "entity_b": b,
            "name_a": name_of.get(a, ""), "name_b": name_of.get(b, ""),
            "features": pair_feature_vector(contribs),
        })
    return out


async def export_labeling_set(path: str) -> int:
    rows = await _aggregate_pairs()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["entity_a", "entity_b", "name_a", "name_b", *FEATURE_ORDER, "label"])
        for r in rows:
            w.writerow([r["entity_a"], r["entity_b"], r["name_a"], r["name_b"],
                        *[round(x, 4) for x in r["features"]], ""])
    logger.info("Exported %d candidate pairs to %s — fill the 'label' column (1=same, 0=different)", len(rows), path)
    return len(rows)


def _fit_and_save(X: list[list[float]], y: list[int], model_out: str) -> dict:
    """Shared training core: fit logistic regression on feature vectors -> labels
    and persist. Used by both the CSV (`train`) and DB (`train_from_db`) paths."""
    import numpy as np
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    if len(set(y)) < 2:
        raise ValueError(f"Need both classes labeled; got {len(X)} rows, labels={set(y)}")
    Xa, ya = np.asarray(X, dtype=float), np.asarray(y)
    clf = LogisticRegression(max_iter=1000, class_weight="balanced")
    metrics = {"n": len(X), "positives": int(ya.sum())}
    cv = min(5, int(np.bincount(ya).min()))  # CV folds bounded by smallest class
    if cv >= 2:
        metrics["cv_accuracy"] = round(float(cross_val_score(clf, Xa, ya, cv=cv).mean()), 4)
    clf.fit(Xa, ya)
    os.makedirs(os.path.dirname(model_out) or ".", exist_ok=True)
    joblib.dump(clf, model_out)
    metrics["weights"] = {t: round(float(w), 3) for t, w in zip(FEATURE_ORDER, clf.coef_[0])}
    metrics["model_path"] = model_out
    logger.info("Trained identity calibration model: %s", metrics)
    return metrics


def train_model(labeled_csv: str, model_out: str) -> dict:
    """Train from a labeled CSV (the `train` CLI / manual path)."""
    X, y = [], []
    with open(labeled_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lab = (row.get("label") or "").strip()
            if lab not in ("0", "1"):
                continue  # unlabeled — skip
            X.append([
                0.0 if t in DEPRECATED_NON_IDENTITY_FEATURES else float(row[t])
                for t in FEATURE_ORDER
            ])
            y.append(int(lab))
    return _fit_and_save(X, y, model_out)


# --------------------------------------------------------------------------- #
# Dashboard-driven labels (no CSV): captured by entity merge / "not same"
# dismiss in the UI, stored in identity_labels, trained automatically by the
# scheduler. See src/api/routes/entity_actions.py + incremental_runner.
# --------------------------------------------------------------------------- #
def _jsonload(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


async def snapshot_pair_features(conn, a: str, b: str) -> dict:
    """{signal_type: max_confidence} for an entity pair from current
    identity_signals. Snapshotted at label time so training survives the merge
    that deletes one of the entities. Stored as a dict (not a fixed vector) so
    appending new signal types to FEATURE_ORDER doesn't invalidate old labels."""
    rows = await conn.fetch("""
        SELECT signal_type, confidence FROM identity_signals
        WHERE signal_type = ANY($1::text[])
          AND ((entity_id = $2::uuid AND target_record_id = $3::text)
            OR (entity_id = $3::uuid AND target_record_id = $2::text))
    """, ACTIVE_FEATURE_ORDER, a, b)
    d: dict[str, float] = {}
    for r in rows:
        c = float(r["confidence"] or 0.0)
        if c > d.get(r["signal_type"], 0.0):
            d[r["signal_type"]] = c
    return d


async def record_label(conn, a: str, b: str, label: int, source: str) -> None:
    """Persist a same/different label for a pair (latest decision wins)."""
    lo, hi = (a, b) if a < b else (b, a)
    feats = await snapshot_pair_features(conn, lo, hi)
    await conn.execute("""
        INSERT INTO identity_labels (entity_a, entity_b, features, label, source)
        VALUES ($1::uuid, $2::uuid, $3::jsonb, $4, $5)
        ON CONFLICT (entity_a, entity_b)
        DO UPDATE SET features = EXCLUDED.features, label = EXCLUDED.label,
                      source = EXCLUDED.source, created_at = NOW()
    """, lo, hi, json.dumps(feats), int(label), source)


def _rows_to_xy(rows) -> tuple[list[list[float]], list[int]]:
    """Feature vectors + labels from identity_labels rows.

    Auto-labeled positives (source LIKE 'auto_%' AND label=1) are LOSO-expanded:
    for each non-zero feature in the base row, emit an extra training row with
    that one feature zeroed. Reasoning: an auto-positive was seeded by SIGNAL
    CONFLUENCE (>=2 signal types agreeing), so zeroing any single contributing
    signal still leaves supporting evidence. This teaches the LR that no single
    signal is required, defending against feature-leak shortcuts like
    'signal-X-fires -> positive'. Human labels and auto-negatives are trusted
    as-is (no LOSO)."""
    X, y = [], []
    for r in rows:
        feats = _jsonload(r["features"])
        base = [_feature_value(feats, t) for t in FEATURE_ORDER]
        label = int(r["label"])
        source = (r["source"] if "source" in r.keys() else "") or ""

        X.append(base)
        y.append(label)

        if label == 1 and source.startswith("auto_"):
            for i, v in enumerate(base):
                if v > 0:
                    variant = list(base)
                    variant[i] = 0.0
                    X.append(variant)
                    y.append(label)
    return X, y


async def train_from_db(model_out: str | None = None) -> dict:
    """Train from the identity_labels table (the dashboard-driven path)."""
    from src.db.connection import init_pools, close_pools, get_analyzer_pool, has_analyzer_pool
    model_out = model_out or _MODEL_PATH
    owns_pool = not has_analyzer_pool()
    if owns_pool:
        await init_pools(apply_schema_ddl=False)
    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT features, label, source FROM identity_labels")
    finally:
        if owns_pool:
            await close_pools()
    X, y = _rows_to_xy(rows)
    return _fit_and_save(X, y, model_out)


_last_label_count = -1


async def maybe_retrain(min_labels: int | None = None, retrain_every: int | None = None) -> dict:
    """Scheduler hook: retrain the calibration model from dashboard labels once
    enough have accumulated (and both classes are present), then force the
    scorer to reload it. Cheap COUNT gate keeps idle cycles free. Runs INSIDE
    the scheduler's event loop, so it reuses the existing pool (no init/close).

    Thresholds are env-configurable (default 20 / 10). Lowering IDENTITY_MIN_LABELS
    activates training earlier — e.g. set to 15 to train on today's live human
    labels without waiting for more merges. The trained model is only used by the
    scorer when IDENTITY_MODEL_ENABLED=1 (see get_model), so lowering the
    threshold is safe on its own."""
    global _last_label_count, _model_loaded, _model_cache
    from src.db.connection import get_analyzer_pool
    if min_labels is None:
        min_labels = int(os.getenv("IDENTITY_MIN_LABELS", "20"))
    if retrain_every is None:
        retrain_every = int(os.getenv("IDENTITY_RETRAIN_EVERY", "10"))
    pool = get_analyzer_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval("SELECT count(*) FROM identity_labels")
        classes = await conn.fetchval("SELECT count(DISTINCT label) FROM identity_labels")
        if n < min_labels or classes < 2:
            return {"skipped": "insufficient_labels", "labels": n, "min_labels": min_labels}
        if _last_label_count >= 0 and n - _last_label_count < retrain_every:
            return {"skipped": "not_enough_new", "labels": n}
        rows = await conn.fetch("SELECT features, label, source FROM identity_labels")
    X, y = _rows_to_xy(rows)
    res = _fit_and_save(X, y, _MODEL_PATH)
    _last_label_count = n
    _model_loaded = False  # next get_model() reloads the freshly trained model
    _model_cache = None
    return res


# --------------------------------------------------------------------------- #
# Validation: is the trained LR actually better than the hand-set noisy-OR?
# Runs on the SAME labeled pairs (leave-one-out CV) so the comparison is fair.
# --------------------------------------------------------------------------- #
def _noisy_or_probs(X: list[list[float]]) -> list[float]:
    """Per-row noisy-OR probability using the scorer's hand-set _TYPE_WEIGHT.
    Local import avoids the circular dep (identity_scorer imports us)."""
    from src.pipeline.identity_scorer import _TYPE_WEIGHT
    weights = [_TYPE_WEIGHT.get(t, 0.0) for t in FEATURE_ORDER]
    out = []
    for xrow in X:
        prob_none = 1.0
        for i, v in enumerate(xrow):
            if v > 0:
                prob_none *= (1 - weights[i] * v)
        out.append(1 - prob_none)
    return out


async def validate_calibration() -> dict:
    """Compare LR (leave-one-out CV) vs noisy-OR AUC on identity_labels.

    Bootstrap CV is unreliable with <=20 rows (a resample can lose an entire
    class); LOO is the correct choice. For each labeled row: fit LR on the
    other N-1 rows, predict on the held-out row, collect predictions, then
    compute AUC over the full held-out set. Noisy-OR uses the hand-set weights
    on the same rows (no fitting needed) as the baseline. Positive delta means
    the LR beats noisy-OR on this labeled set."""
    from src.db.connection import init_pools, close_pools, get_analyzer_pool, has_analyzer_pool
    owns_pool = not has_analyzer_pool()
    if owns_pool:
        await init_pools(apply_schema_ddl=False)
    try:
        pool = get_analyzer_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT features, label, source FROM identity_labels")
    finally:
        if owns_pool:
            await close_pools()

    if len(rows) < 2:
        return {"error": "not_enough_labels", "n_labels": len(rows)}

    X_all, y_all = _rows_to_xy(rows)  # LOSO expansion included
    n_pos = sum(1 for v in y_all if v == 1)
    n_neg = len(y_all) - n_pos
    if n_pos == 0 or n_neg == 0:
        return {"error": "one_class_only", "n_labels": len(rows),
                "n_positive": n_pos, "n_negative": n_neg}

    # NB: LOSO rows share a base pair. For fair LOO CV we treat the DB-level
    # rows (not the LOSO-expanded rows) as the holdout unit — expanding a row
    # into the train set alongside its own LOSO variants would leak. Compute
    # the un-expanded X/y for holdout selection, and re-expand the training
    # subset each fold. Cheap given the label count.
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    # Un-expanded (one row per DB pair):
    Xb, yb = [], []
    for r in rows:
        feats = _jsonload(r["features"])
        Xb.append([_feature_value(feats, t) for t in FEATURE_ORDER])
        yb.append(int(r["label"]))

    lr_preds = np.zeros(len(rows))
    for i in range(len(rows)):
        train_rows = [r for j, r in enumerate(rows) if j != i]
        X_train, y_train = _rows_to_xy(train_rows)
        if len(set(y_train)) < 2:
            # Can't fit LR without both classes in train fold. Fall back to
            # noisy-OR proba for this held-out row — records the fold but
            # doesn't unfairly favour LR.
            lr_preds[i] = _noisy_or_probs([Xb[i]])[0]
            continue
        clf = LogisticRegression(max_iter=1000, class_weight="balanced")
        clf.fit(np.asarray(X_train, dtype=float), np.asarray(y_train))
        lr_preds[i] = float(clf.predict_proba(np.asarray([Xb[i]], dtype=float))[0, 1])

    noisy_or_preds = _noisy_or_probs(Xb)
    y_np = np.asarray(yb)
    lr_auc = float(roc_auc_score(y_np, lr_preds))
    no_auc = float(roc_auc_score(y_np, noisy_or_preds))

    # Also report weights from a full-data fit (for inspection).
    full_clf = LogisticRegression(max_iter=1000, class_weight="balanced")
    full_clf.fit(np.asarray(X_all, dtype=float), np.asarray(y_all))
    weights_full = {t: round(float(w), 3)
                    for t, w in zip(FEATURE_ORDER, full_clf.coef_[0])}

    return {
        "n_labels": len(rows),
        "n_positive": sum(1 for v in yb if v == 1),
        "n_negative": sum(1 for v in yb if v == 0),
        "loso_expanded_positive": n_pos,
        "loso_expanded_negative": n_neg,
        "lr_auc_loo": round(lr_auc, 4),
        "noisy_or_auc": round(no_auc, 4),
        "delta": round(lr_auc - no_auc, 4),
        "lr_weights_full_fit": weights_full,
    }


def _main():
    import asyncio
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "export":
        path = sys.argv[2] if len(sys.argv) > 2 else "identity_pairs_to_label.csv"
        print(f"exported {asyncio.run(export_labeling_set(path))} pairs to {path}")
    elif cmd == "train":
        csv_path = sys.argv[2] if len(sys.argv) > 2 else "identity_pairs_to_label.csv"
        out = sys.argv[3] if len(sys.argv) > 3 else _MODEL_PATH
        print(train_model(csv_path, out))
    elif cmd == "train-db":
        # Train from dashboard-captured labels (identity_labels table).
        out = sys.argv[2] if len(sys.argv) > 2 else _MODEL_PATH
        print(asyncio.run(train_from_db(out)))
    elif cmd == "validate":
        # Compare LR (leave-one-out CV) vs noisy-OR baseline AUC on labeled pairs.
        # Non-zero exit if LR doesn't beat noisy-OR by --min-delta (default 0.05).
        strict = "--strict" in sys.argv[2:]
        min_delta = 0.05
        for i, tok in enumerate(sys.argv[2:]):
            if tok == "--min-delta" and i + 1 < len(sys.argv[2:]):
                min_delta = float(sys.argv[3 + i])
        result = asyncio.run(validate_calibration())
        print(json.dumps(result, indent=2))
        if strict and (result.get("delta") or 0.0) < min_delta:
            sys.exit(1)
    else:
        print("usage: python -m src.pipeline.identity_calibration "
              "export [csv] | train [csv] [model] | train-db [model] | "
              "validate [--strict] [--min-delta 0.05]")


if __name__ == "__main__":
    _main()
