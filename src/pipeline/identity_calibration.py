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
import logging
import os

logger = logging.getLogger(__name__)

# Stable feature order — MUST match the scorer's _TYPE_WEIGHT keys. New signal
# types: append here (retrain after) so existing models keep working.
FEATURE_ORDER = [
    "email_match", "phone_match", "bio_mention", "cross_platform_link",
    "content_similarity", "temporal_copost", "shared_website",
    "shared_route_origin", "group_cooccurrence", "media_gps_colocation",
    "media_perceptual_match", "media_face_match",
]

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
        if conf > by_type.get(sig_type, 0.0):
            by_type[sig_type] = conf
    return [by_type.get(t, 0.0) for t in FEATURE_ORDER]


def get_model():
    """Return the trained model or None (cached). None -> caller uses noisy-OR."""
    global _model_cache, _model_loaded
    if _model_loaded:
        return _model_cache
    _model_loaded = True
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
    from src.db.connection import init_pools, close_pools, get_analyzer_pool

    await init_pools(apply_schema_ddl=False)
    analyzer = get_analyzer_pool()
    async with analyzer.acquire() as conn:
        sigs = await conn.fetch("""
            SELECT entity_id::text, signal_type, target_platform, target_record_id, confidence
            FROM identity_signals WHERE signal_type = ANY($1::text[])
        """, FEATURE_ORDER)
        links = await conn.fetch(
            "SELECT entity_id::text, source, platform_id FROM entity_platform_links")
        names = await conn.fetch("SELECT id::text, canonical_name FROM entities")
    await close_pools()

    pid_to_entity = {(l["source"], l["platform_id"]): l["entity_id"] for l in links}
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


def train_model(labeled_csv: str, model_out: str) -> dict:
    import numpy as np
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    X, y = [], []
    with open(labeled_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lab = (row.get("label") or "").strip()
            if lab not in ("0", "1"):
                continue  # unlabeled — skip
            X.append([float(row[t]) for t in FEATURE_ORDER])
            y.append(int(lab))
    if len(set(y)) < 2:
        raise ValueError(f"Need both classes labeled; got {len(X)} rows, labels={set(y)}")

    Xa, ya = np.asarray(X), np.asarray(y)
    clf = LogisticRegression(max_iter=1000, class_weight="balanced")
    # CV only if each class has enough rows.
    cv = min(5, min(np.bincount(ya)))
    metrics = {"n": len(X), "positives": int(ya.sum())}
    if cv >= 2:
        metrics["cv_accuracy"] = round(float(cross_val_score(clf, Xa, ya, cv=cv).mean()), 4)
    clf.fit(Xa, ya)
    os.makedirs(os.path.dirname(model_out) or ".", exist_ok=True)
    joblib.dump(clf, model_out)
    metrics["weights"] = {t: round(float(w), 3) for t, w in zip(FEATURE_ORDER, clf.coef_[0])}
    metrics["model_path"] = model_out
    logger.info("Trained identity calibration model: %s", metrics)
    return metrics


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
    else:
        print("usage: python -m src.pipeline.identity_calibration export [csv] | train [csv] [model.joblib]")


if __name__ == "__main__":
    _main()
