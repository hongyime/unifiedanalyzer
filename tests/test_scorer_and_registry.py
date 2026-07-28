"""Sanity tests for scorer same-platform multiplier + signal-registry consistency.

Not a full end-to-end suite - runs without DB by monkey-patching the pool. The
purpose is to catch:
  * A registry drift (a signal appended to _TYPE_WEIGHT but forgotten in
    FEATURE_ORDER / _SCORING_SIGNALS, or vice versa)
  * The same-platform multiplier logic breaking under refactor
  * The phase list losing any of the four new Track-1 phases

Run: `python -m pytest tests/test_scorer_and_registry.py -v`
     (or from a fresh Docker rebuild: `pytest -q tests/`)

Requires: pytest, pytest-asyncio (installed via requirements-dev.txt or pip
install once).  If neither is present, tests skip with a clear message.
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import sys
from pathlib import Path

# Make `src.*` importable when running standalone (python tests/foo.py) as
# well as under pytest. Repo root is the parent of this file's directory.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

if importlib.util.find_spec("pytest") is None:
    print("pytest not installed; skip: `pip install pytest pytest-asyncio`")
    sys.exit(0)


# ---------------------------------------------------------------------------
# Signal registry consistency
# ---------------------------------------------------------------------------

def test_type_weight_matches_feature_order():
    """Every scorer key must remain in FEATURE_ORDER for stable snapshots.
    Non-identity context slots may still have historical weights, but calibration
    must mark them deprecated so they are forced to zero."""
    from src.pipeline.identity_scorer import _TYPE_WEIGHT
    from src.pipeline.identity_calibration import DEPRECATED_NON_IDENTITY_FEATURES, FEATURE_ORDER
    weight_keys = set(_TYPE_WEIGHT.keys())
    feature_keys = set(FEATURE_ORDER)
    assert weight_keys <= feature_keys, (
        f"Registry drift: in _TYPE_WEIGHT but not FEATURE_ORDER = {weight_keys - feature_keys}"
    )
    assert set(DEPRECATED_NON_IDENTITY_FEATURES) <= feature_keys, (
        f"Deprecated/context slots missing from FEATURE_ORDER = "
        f"{set(DEPRECATED_NON_IDENTITY_FEATURES) - feature_keys}"
    )


def test_scoring_signals_matches_type_weight():
    """auto_labeler._SCORING_SIGNALS must include every scorer key so
    confluence-based labeling can consider it."""
    from src.pipeline.identity_scorer import _TYPE_WEIGHT
    from src.pipeline.auto_labeler import _SCORING_SIGNALS
    weight_keys = set(_TYPE_WEIGHT.keys())
    scoring_keys = set(_SCORING_SIGNALS)
    assert weight_keys == scoring_keys, (
        f"Registry drift: in scorer but not auto_labeler._SCORING_SIGNALS = "
        f"{weight_keys - scoring_keys}, "
        f"in auto_labeler._SCORING_SIGNALS but not scorer = {scoring_keys - weight_keys}"
    )


def test_hard_signals_are_deterministic_only():
    """_HARD_SIGNALS is the confluence-anchor set. Weak / associative signals
    (topical_similarity, social_face_link, shared_life_context, etc.) MUST
    NOT be listed here or auto-label training gets a garbage positive rule."""
    from src.pipeline.auto_labeler import _HARD_SIGNALS
    forbidden = {"topical_similarity", "social_face_link", "shared_life_context",
                 "content_similarity", "temporal_copost", "group_cooccurrence",
                 "username_similar", "bio_mention"}
    leaked = _HARD_SIGNALS & forbidden
    assert not leaked, f"Weak signals leaked into _HARD_SIGNALS: {leaked}"


def test_temporal_copost_is_not_identity_evidence():
    """Posting at the same time can be relationship context, but it must not
    affect same-person scoring, auto-labeling, or model features."""
    from src.pipeline.auto_labeler import _SCORING_SIGNALS
    from src.pipeline.identity_calibration import (
        DEPRECATED_NON_IDENTITY_FEATURES,
        pair_feature_vector,
    )
    from src.pipeline.identity_scorer import _TYPE_WEIGHT

    assert "temporal_copost" not in _TYPE_WEIGHT
    assert "temporal_copost" not in _SCORING_SIGNALS
    assert "temporal_copost" in DEPRECATED_NON_IDENTITY_FEATURES
    features = pair_feature_vector([("temporal_copost", 0.99)])
    assert max(features) == 0.0


def test_context_signals_are_zeroed_for_calibration_features():
    from src.pipeline.identity_calibration import DEPRECATED_NON_IDENTITY_FEATURES, FEATURE_ORDER, pair_feature_vector

    features = pair_feature_vector([
        ("email_match", 0.6),
        ("bio_mention", 1.0),
        ("group_cooccurrence", 1.0),
        ("topical_similarity", 1.0),
        ("social_face_link", 1.0),
        ("shared_life_context", 1.0),
    ])
    by_name = dict(zip(FEATURE_ORDER, features))

    assert by_name["email_match"] == 0.6
    for signal_type in DEPRECATED_NON_IDENTITY_FEATURES:
        assert by_name[signal_type] == 0.0


def test_context_only_signals_do_not_create_same_person_probability():
    """Relationship/context signals can explain a candidate but cannot create one
    without at least one identity-backed signal."""
    from src.pipeline.identity_scorer import _has_identity_evidence, _identity_score_contributions

    context_only = [
        ("bio_mention", 0.9),
        ("group_cooccurrence", 0.9),
        ("topical_similarity", 0.99),
        ("social_face_link", 0.8),
        ("shared_life_context", 0.8),
    ]

    assert not _has_identity_evidence(context_only)
    assert _identity_score_contributions(context_only) == []
    assert _has_identity_evidence([*context_only, ("email_match", 0.6)])


def test_context_signals_do_not_boost_identity_score():
    """Bio mentions and shared context can be shown in Review, but the same-person
    probability must be exactly the identity-backed evidence score."""
    from src.pipeline.identity_scorer import _TYPE_WEIGHT, _identity_score_contributions

    contributions = [
        ("email_match", 0.6),
        ("bio_mention", 1.0),
        ("topical_similarity", 1.0),
        ("shared_life_context", 1.0),
    ]

    scoring = _identity_score_contributions(contributions)
    assert scoring == [("email_match", 0.6)]
    prob_none = 1.0
    for sig_type, confidence in scoring:
        prob_none *= 1 - _TYPE_WEIGHT[sig_type] * confidence
    assert round(1 - prob_none, 4) == 0.36


def test_dismissed_same_evidence_stays_suppressed():
    from src.pipeline.identity_scorer import _dismissal_suppresses_candidate

    assert _dismissal_suppresses_candidate(
        [("email_match", 0.6), ("bio_mention", 0.9)],
        {"email_match": 0.6},
    )


def test_dismissed_pair_resurfaces_on_new_identity_signal():
    from src.pipeline.identity_scorer import _dismissal_suppresses_candidate

    assert not _dismissal_suppresses_candidate(
        [("email_match", 0.6), ("phone_match", 0.7)],
        {"email_match": 0.6},
    )


def test_dismissed_pair_requires_materially_stronger_hard_signal():
    from src.pipeline.identity_scorer import (
        _DISMISS_RESURFACE_MIN_DELTA,
        _dismissal_suppresses_candidate,
    )

    previous = 0.6
    just_under = previous + _DISMISS_RESURFACE_MIN_DELTA - 0.001
    assert _dismissal_suppresses_candidate(
        [("email_match", just_under)],
        {"email_match": previous},
    )
    assert not _dismissal_suppresses_candidate(
        [("email_match", previous + _DISMISS_RESURFACE_MIN_DELTA)],
        {"email_match": previous},
    )


def test_dismissed_pair_ignores_new_weak_identity_signal():
    from src.pipeline.identity_scorer import _dismissal_suppresses_candidate

    assert _dismissal_suppresses_candidate(
        [("email_match", 0.6), ("content_similarity", 1.0), ("real_name_fuzzy", 1.0)],
        {"email_match": 0.6},
    )


def test_dismissed_pair_ignores_new_context_only_signal():
    from src.pipeline.identity_scorer import _dismissal_suppresses_candidate

    assert _dismissal_suppresses_candidate(
        [("email_match", 0.6), ("shared_life_context", 1.0), ("group_cooccurrence", 1.0)],
        {"email_match": 0.6},
    )


def test_track_c_signals_registered():
    """The Track-1/2 new-signal shipment (2026-07-08): both social_face_link
    and shared_life_context must be present in the registry."""
    from src.pipeline.identity_scorer import _TYPE_WEIGHT
    for sig in ("social_face_link", "shared_life_context"):
        assert sig in _TYPE_WEIGHT, f"{sig} missing from _TYPE_WEIGHT"


# ---------------------------------------------------------------------------
# Phase wiring
# ---------------------------------------------------------------------------

def test_phase_list_has_new_phases():
    """The four new phases must be wired into _secondary_phases()."""
    from src.pipeline.incremental_runner import _secondary_phases
    names = [name for name, _ in _secondary_phases()]
    required = {"face_associations", "social_face_link",
                "entity_enrichment", "shared_life_context"}
    missing = required - set(names)
    assert not missing, f"Missing new phases in _secondary_phases: {missing}"


def test_health_reporting_excludes_probe_run_types():
    """Production health must not be held open by explicit verification probes."""
    from src.pipeline.run_reporting import (
        is_probe_phase_name,
        is_production_run_type,
        probe_phase_names,
        production_run_types,
    )

    assert production_run_types() == ["incremental", "full_resolution"]
    assert is_production_run_type("incremental")
    assert is_production_run_type("full_resolution")
    assert not is_production_run_type("forced_phase_failure_probe")
    assert not is_production_run_type("collector_down_probe_short")
    assert not is_production_run_type("manual_verification")
    assert probe_phase_names() == ["forced_failure"]
    assert is_probe_phase_name("forced_failure")
    assert not is_probe_phase_name("timeline")


def test_collector_quiet_health_uses_schedule_cadence():
    """Collector health should not warn while a source is still inside cadence."""
    from datetime import datetime, timedelta, timezone

    from src.scheduler.scheduler import _collector_no_run_issue

    now = datetime(2026, 7, 18, 2, 0, tzinfo=timezone.utc)
    assert _collector_no_run_issue(
        "youtube", now - timedelta(hours=11, minutes=40), 12, now
    ) is None
    issue = _collector_no_run_issue(
        "youtube", now - timedelta(hours=15), 12, now
    )
    assert issue is not None
    assert issue["source"] == "youtube"
    assert "cadence 12h" in issue["message"]


def test_merge_candidate_floors_default_to_review_55_notify_55():
    """Review and Telegram notification floors both start at 55."""
    from src.merge_candidates import merge_candidate_min_weight
    from src.scheduler.scheduler import _MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE

    assert merge_candidate_min_weight() == 55
    assert _MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE == 55


def test_coordinated_posting_alerts_default_off(monkeypatch):
    import src.pipeline.alert_engine as alerts

    called = False

    async def fake_detector():
        nonlocal called
        called = True
        return 99

    monkeypatch.delenv("COORDINATED_POSTING_ALERT_ENABLED", raising=False)
    monkeypatch.setenv("SILENCE_GAP_DYNAMIC", "0")
    monkeypatch.setenv("NEW_ACTIVITY_AFTER_SILENCE_ENABLED", "0")
    monkeypatch.setenv("PROFILE_CHANGE_ALERT_ENABLED", "0")
    monkeypatch.setenv("NEW_IDENTITY_LINK_ALERT_ENABLED", "0")
    monkeypatch.setenv("LOCATION_MISMATCH_ALERT_ENABLED", "0")
    monkeypatch.setattr(alerts, "_detect_coordinated_posting", fake_detector)

    stats = asyncio.run(alerts.run_alerts())

    assert called is False
    assert stats["coordinated_posting"] == 0


def test_coordinated_posting_alerts_can_be_enabled(monkeypatch):
    import src.pipeline.alert_engine as alerts

    async def fake_detector():
        return 7

    monkeypatch.setenv("COORDINATED_POSTING_ALERT_ENABLED", "1")
    monkeypatch.setenv("SILENCE_GAP_DYNAMIC", "0")
    monkeypatch.setenv("NEW_ACTIVITY_AFTER_SILENCE_ENABLED", "0")
    monkeypatch.setenv("PROFILE_CHANGE_ALERT_ENABLED", "0")
    monkeypatch.setenv("NEW_IDENTITY_LINK_ALERT_ENABLED", "0")
    monkeypatch.setenv("LOCATION_MISMATCH_ALERT_ENABLED", "0")
    monkeypatch.setattr(alerts, "_detect_coordinated_posting", fake_detector)

    stats = asyncio.run(alerts.run_alerts())

    assert stats["coordinated_posting"] == 7


def test_collector_unavailable_phase_records_skipped(monkeypatch):
    """A collector outage should be an intentional skipped phase, not failed."""
    from src.db.connection import CollectorUnavailableError
    import src.pipeline.incremental_runner as runner

    captured = {}

    class Conn:
        async def execute(self, sql, *args):
            captured["sql"] = sql
            captured["args"] = args

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    async def phase():
        raise CollectorUnavailableError("collector down")

    monkeypatch.setattr(runner, "get_analyzer_pool", lambda: Pool())
    result = asyncio.run(
        runner._run_phase(
            "00000000-0000-0000-0000-000000000000",
            "collector_down_probe_short",
            "timeline",
            phase,
            default={},
        )
    )

    assert result["skipped"] == "collector_unavailable"
    assert captured["args"][3] == "skipped"
    assert "CollectorUnavailableError" in captured["args"][5]


def test_skipped_alert_metadata_does_not_break_alert_count():
    """Skipped alert phases return metadata strings, not only numeric counters."""
    from src.pipeline.incremental_runner import _sum_numeric_stats

    assert _sum_numeric_stats({
        "silence_gap": 2,
        "new_identity_link": 1,
        "skipped": "collector_unavailable",
        "error": "TimeoutError",
    }) == 3


def test_face_associations_before_social_face_link():
    """Ordering matters: face_associations populates the table
    social_face_link then reads."""
    from src.pipeline.incremental_runner import _secondary_phases
    names = [name for name, _ in _secondary_phases()]
    fa_idx = names.index("face_associations")
    sfl_idx = names.index("social_face_link")
    assert fa_idx < sfl_idx, (
        f"face_associations (@{fa_idx}) must precede social_face_link "
        f"(@{sfl_idx}) - the latter reads what the former writes."
    )


def test_entity_enrichment_before_shared_life_context():
    """entity_enrichment populates entities.metadata.enrichment which
    shared_life_context then reads to build the inverted index."""
    from src.pipeline.incremental_runner import _secondary_phases
    names = [name for name, _ in _secondary_phases()]
    ee_idx = names.index("entity_enrichment")
    slc_idx = names.index("shared_life_context")
    assert ee_idx < slc_idx


# ---------------------------------------------------------------------------
# Same-platform multiplier
# ---------------------------------------------------------------------------

def test_same_platform_multiplier_env_configurable():
    """SCORER_SAME_PLATFORM_MULTIPLIER must default to 0.3 and be tunable."""
    # Force a fresh module load so the module-level float reflects the env.
    os.environ["SCORER_SAME_PLATFORM_MULTIPLIER"] = "0.7"
    if "src.pipeline.identity_scorer" in sys.modules:
        importlib.reload(sys.modules["src.pipeline.identity_scorer"])
    from src.pipeline.identity_scorer import _SAME_PLATFORM_MULTIPLIER
    assert _SAME_PLATFORM_MULTIPLIER == 0.7

    # Reset back to default for other tests.
    del os.environ["SCORER_SAME_PLATFORM_MULTIPLIER"]
    importlib.reload(sys.modules["src.pipeline.identity_scorer"])
    from src.pipeline.identity_scorer import _SAME_PLATFORM_MULTIPLIER as default_mult
    assert default_mult == 0.3


def test_same_platform_penalty_source_annotates_flag():
    """A same-platform pair persisted by compute_identity_scores must set
    sources.same_platform=True in the entity_relationships row (verified by
    inspecting the code path - actual DB writes tested at integration level).
    """
    # Read the source text and verify the exact line is present. This is a
    # lightweight smoke check to catch a refactor that drops the flag.
    from pathlib import Path
    src = Path(__file__).parent.parent / "src" / "pipeline" / "identity_scorer.py"
    text = src.read_text(encoding="utf-8")
    assert '"same_platform": r["same_platform"]' in text, (
        "Scorer must persist same_platform boolean in sources JSON."
    )
    assert "_SAME_PLATFORM_MULTIPLIER" in text, "Multiplier constant missing."
    assert "if same_platform:" in text, "Multiplier not applied conditionally."


def test_identity_scoring_skips_orphaned_uuid_targets(monkeypatch):
    """Signals whose target_record_id points at a deleted entity must not break
    the same-person relationship rebuild."""
    import src.pipeline.identity_scorer as scorer

    entity_a = "00000000-0000-0000-0000-000000000001"
    entity_b = "00000000-0000-0000-0000-000000000002"
    missing = "00000000-0000-0000-0000-000000000003"
    writes = []

    class Tx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Conn:
        async def fetch(self, sql, *args):
            if "FROM identity_signals" in sql:
                return [
                    {
                        "entity_id": entity_a,
                        "signal_type": "phone_match",
                        "target_platform": None,
                        "target_record_id": entity_b,
                        "confidence": 1.0,
                    },
                    {
                        "entity_id": entity_a,
                        "signal_type": "phone_match",
                        "target_platform": None,
                        "target_record_id": missing,
                        "confidence": 1.0,
                    },
                ]
            if "FROM entity_platform_links" in sql:
                return [
                    {"entity_id": entity_a, "source": "telegram", "platform_id": "a"},
                    {"entity_id": entity_b, "source": "whatsapp", "platform_id": "b"},
                ]
            if "FROM entities" in sql:
                return [{"id": entity_a}, {"id": entity_b}]
            if "FROM identity_labels" in sql:
                return []
            raise AssertionError(f"Unexpected fetch SQL: {sql}")

        async def execute(self, sql, *args):
            writes.append(("execute", sql, args))

        async def executemany(self, sql, rows):
            writes.append(("executemany", sql, rows))

        def transaction(self):
            return Tx()

    class Acquire:
        async def __aenter__(self):
            return Conn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class Pool:
        def acquire(self):
            return Acquire()

    monkeypatch.setattr(scorer, "get_analyzer_pool", lambda: Pool())
    monkeypatch.setattr(scorer, "get_model", lambda: None)

    stats = asyncio.run(scorer.compute_identity_scores())

    persisted = [entry for entry in writes if entry[0] == "executemany"]
    assert stats["pairs_scored"] == 1
    assert stats["skipped_orphaned_targets"] == 1
    assert len(persisted) == 1
    rows = persisted[0][2]
    assert len(rows) == 1
    assert missing not in rows[0]


# ---------------------------------------------------------------------------
# New module import smoke tests
# ---------------------------------------------------------------------------

def test_new_modules_importable():
    """The four new pipeline modules must import without side-effects that
    reach out to a live DB. The functions themselves are async and won't
    execute at import; we just verify the module structure is intact."""
    from src.pipeline.face_associations import build_face_associations
    from src.pipeline.social_face_link import emit_social_face_link_signals
    from src.pipeline.entity_enrichment import enrich_entities_with_ner
    from src.pipeline.shared_life_context import emit_shared_life_context_signals
    for fn in (build_face_associations, emit_social_face_link_signals,
               enrich_entities_with_ner, emit_shared_life_context_signals):
        assert callable(fn), f"{fn} is not callable"


if __name__ == "__main__":
    # Standalone runnable without pytest: just call each function.
    all_tests = [(n, obj) for n, obj in globals().items()
                 if n.startswith("test_") and callable(obj)]
    passed, failed = 0, []
    for name, fn in all_tests:
        try:
            fn()
            print(f"  ok  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
            failed.append((name, e))
    print(f"\n{passed}/{len(all_tests)} passed")
    sys.exit(1 if failed else 0)
