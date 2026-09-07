"""
Pure-function tests — batch 7.

Covers previously untested modules with no DB or I/O:
- pipeline.calibration_watchdog: _int_env, _float_env
- face.engine.quality: QualityScorer.compute_quality_score, is_acceptable
- face.identity.verification: VerificationPriority, VerificationAction enums,
  VerificationTask dataclass, VerificationQueue.get_queue_status / get_audit_log
- api.routes.triage: _decode
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# calibration_watchdog: _int_env, _float_env
# ---------------------------------------------------------------------------

class TestIntEnvWatchdog:
    def _e(self, key, default):
        from src.pipeline.calibration_watchdog import _int_env
        return _int_env(key, default)

    def test_reads_env_var(self, monkeypatch):
        monkeypatch.setenv("_TEST_WATCHDOG_INT", "77")
        assert self._e("_TEST_WATCHDOG_INT", 10) == 77

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_WATCHDOG_INT", raising=False)
        assert self._e("_TEST_WATCHDOG_INT", 42) == 42

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("_TEST_WATCHDOG_INT", "bad")
        assert self._e("_TEST_WATCHDOG_INT", 5) == 5

    def test_zero_allowed(self, monkeypatch):
        monkeypatch.setenv("_TEST_WATCHDOG_INT", "0")
        assert self._e("_TEST_WATCHDOG_INT", 5) == 0


class TestFloatEnvWatchdog:
    def _e(self, key, default):
        from src.pipeline.calibration_watchdog import _float_env
        return _float_env(key, default)

    def test_reads_float(self, monkeypatch):
        monkeypatch.setenv("_TEST_WATCHDOG_FLOAT", "0.05")
        assert abs(self._e("_TEST_WATCHDOG_FLOAT", 0.1) - 0.05) < 1e-9

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_WATCHDOG_FLOAT", raising=False)
        assert abs(self._e("_TEST_WATCHDOG_FLOAT", 0.5) - 0.5) < 1e-9

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("_TEST_WATCHDOG_FLOAT", "not-a-float")
        assert self._e("_TEST_WATCHDOG_FLOAT", 0.99) == 0.99

    def test_integer_string_parsed(self, monkeypatch):
        monkeypatch.setenv("_TEST_WATCHDOG_FLOAT", "1")
        assert self._e("_TEST_WATCHDOG_FLOAT", 0.0) == 1.0


# ---------------------------------------------------------------------------
# face.engine.quality: QualityScorer.compute_quality_score, is_acceptable
# (pure math — no cv2/numpy image processing)
# ---------------------------------------------------------------------------

class TestQualityScorer:
    def _scorer(self, lw=0.4, aw=0.3, cw=0.3):
        from src.face.engine.quality import QualityScorer
        return QualityScorer(laplacian_weight=lw, area_weight=aw, confidence_weight=cw)

    def test_perfect_scores(self):
        s = self._scorer()
        # max laplacian → 1.0, area=1.0, conf=1.0 → all components = 1.0
        score = s.compute_quality_score(500.0, 1.0, 1.0, max_laplacian=500.0)
        assert abs(score - 1.0) < 1e-9

    def test_zero_scores(self):
        s = self._scorer()
        score = s.compute_quality_score(0.0, 0.0, 0.0)
        assert score == 0.0

    def test_weighted_combination(self):
        s = self._scorer(lw=0.5, aw=0.3, cw=0.2)
        # laplacian_score=0.5 (250/500), area=1.0, conf=1.0
        score = s.compute_quality_score(250.0, 1.0, 1.0, max_laplacian=500.0)
        expected = 0.5 * 0.5 + 0.3 * 1.0 + 0.2 * 1.0
        assert abs(score - expected) < 1e-9

    def test_capped_at_1(self):
        s = self._scorer()
        score = s.compute_quality_score(9999.0, 1.0, 1.0)
        assert score <= 1.0

    def test_never_negative(self):
        s = self._scorer()
        score = s.compute_quality_score(-100.0, -1.0, -1.0)
        assert score >= 0.0

    def test_laplacian_capped_at_max(self):
        s = self._scorer(lw=1.0, aw=0.0, cw=0.0)
        # laplacian 1000 with max=500 → score capped at 1.0
        score = s.compute_quality_score(1000.0, 0.0, 0.0, max_laplacian=500.0)
        assert abs(score - 1.0) < 1e-9


class TestQualityScorerIsAcceptable:
    def _scorer(self):
        from src.face.engine.quality import QualityScorer
        return QualityScorer()

    def test_above_threshold_acceptable(self):
        assert self._scorer().is_acceptable(0.5) is True

    def test_below_threshold_not_acceptable(self):
        assert self._scorer().is_acceptable(0.1) is False

    def test_at_threshold_acceptable(self):
        assert self._scorer().is_acceptable(0.3) is True

    def test_custom_threshold(self):
        assert self._scorer().is_acceptable(0.4, min_score=0.5) is False


# ---------------------------------------------------------------------------
# face.identity.verification: enums, dataclass, VerificationQueue pure methods
# ---------------------------------------------------------------------------

class TestVerificationEnums:
    def test_priority_values(self):
        from src.face.identity.verification import VerificationPriority
        assert VerificationPriority.LOW.value < VerificationPriority.MEDIUM.value
        assert VerificationPriority.MEDIUM.value < VerificationPriority.HIGH.value

    def test_action_values(self):
        from src.face.identity.verification import VerificationAction
        assert VerificationAction.CONFIRM.value == "confirm"
        assert VerificationAction.REJECT.value == "reject"
        assert VerificationAction.MERGE.value == "merge"
        assert VerificationAction.SPLIT.value == "split"

    def test_all_actions_present(self):
        from src.face.identity.verification import VerificationAction
        names = {a.value for a in VerificationAction}
        assert {"confirm", "reject", "merge", "split", "rename"} == names


class TestVerificationTask:
    def test_default_status_pending(self):
        from src.face.identity.verification import VerificationTask, VerificationAction, VerificationPriority
        task = VerificationTask(
            task_id="t1",
            identity_id="id1",
            face_ids=["f1"],
            action=VerificationAction.CONFIRM,
            priority=VerificationPriority.HIGH,
        )
        assert task.status == "pending"

    def test_metadata_defaults_empty(self):
        from src.face.identity.verification import VerificationTask, VerificationAction, VerificationPriority
        task = VerificationTask(
            task_id="t1",
            identity_id=None,
            face_ids=[],
            action=VerificationAction.REJECT,
            priority=VerificationPriority.LOW,
        )
        assert task.metadata == {}


class TestVerificationQueueGetStatus:
    def test_initial_status(self):
        from src.face.identity.verification import VerificationQueue
        q = VerificationQueue()
        status = q.get_queue_status()
        assert status["pending_tasks"] == 0
        assert status["processing"] is False
        assert status["undo_stack_size"] == 0
        assert status["total_audit_entries"] == 0

    def test_priority_breakdown_present(self):
        from src.face.identity.verification import VerificationQueue
        q = VerificationQueue()
        status = q.get_queue_status()
        assert "LOW" in status["priority_breakdown"]
        assert "MEDIUM" in status["priority_breakdown"]
        assert "HIGH" in status["priority_breakdown"]


class TestVerificationQueueGetAuditLog:
    def test_empty_log(self):
        from src.face.identity.verification import VerificationQueue
        q = VerificationQueue()
        assert q.get_audit_log() == []

    def test_filter_by_identity_id_no_match(self):
        from src.face.identity.verification import VerificationQueue
        q = VerificationQueue()
        assert q.get_audit_log(identity_id="nonexistent") == []

    def test_limit_respected(self):
        from src.face.identity.verification import VerificationQueue, AuditLogEntry, VerificationAction
        from datetime import datetime, timezone
        q = VerificationQueue()
        # Manually insert entries into the audit log
        for i in range(5):
            q._audit_log.append(AuditLogEntry(
                timestamp=datetime.now(timezone.utc),
                action=VerificationAction.CONFIRM,
                identity_id="id1",
                face_ids=[f"f{i}"],
                user="test",
            ))
        result = q.get_audit_log(limit=3)
        assert len(result) == 3

    def test_log_entry_shape(self):
        from src.face.identity.verification import VerificationQueue, AuditLogEntry, VerificationAction
        from datetime import datetime, timezone
        q = VerificationQueue()
        q._audit_log.append(AuditLogEntry(
            timestamp=datetime.now(timezone.utc),
            action=VerificationAction.MERGE,
            identity_id="id1",
            face_ids=["f1"],
            user="admin",
            details={"note": "test"},
        ))
        entries = q.get_audit_log()
        assert len(entries) == 1
        entry = entries[0]
        for key in ("timestamp", "action", "identity_id", "face_ids", "user", "details", "undone"):
            assert key in entry
        assert entry["action"] == "merge"
        assert entry["undone"] is False


# ---------------------------------------------------------------------------
# api.routes.triage: _decode
# ---------------------------------------------------------------------------

class TestTriageDecode:
    def _d(self, raw):
        from src.api.routes.triage import _decode
        return _decode(raw)

    def test_dict_passthrough(self):
        assert self._d({"a": 1}) == {"a": 1}

    def test_json_string(self):
        assert self._d('{"x": 2}') == {"x": 2}

    def test_invalid_json_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}

    def test_json_array_returns_empty(self):
        assert self._d("[1, 2]") == {}

    def test_empty_string_returns_empty(self):
        assert self._d("") == {}
