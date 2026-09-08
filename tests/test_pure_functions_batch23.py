"""
Pure-function tests — batch 23.

Covers previously untested modules with no DB or I/O:
- eval.runner: _json_obj, _factory_items, materialize_seed_items,
  SUPPORTED_TASKS constant
- scheduler.scheduler: _env_flag, _env_int, _run_skipped_due_lock, _backup_window_open
- api.websocket: PUSH_INTERVAL_SECONDS constant
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# eval.runner: _json_obj, _factory_items, materialize_seed_items, SUPPORTED_TASKS
# ---------------------------------------------------------------------------

class TestRunnerJsonObj:
    def _j(self, v):
        from src.eval.runner import _json_obj
        return _json_obj(v)

    def test_dict_passthrough(self):
        assert self._j({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._j('{"a": 1}') == {"a": 1}

    def test_invalid_json_returns_empty(self):
        assert self._j("bad") == {}

    def test_array_json_returns_empty(self):
        assert self._j("[1, 2]") == {}

    def test_none_returns_empty(self):
        assert self._j(None) == {}

    def test_int_returns_empty(self):
        assert self._j(42) == {}


class TestRunnerFactoryItems:
    def _f(self, item_set):
        from src.eval.runner import _factory_items
        return _factory_items(item_set)

    def test_no_factory_returns_empty(self):
        assert self._f({}) == []

    def test_unknown_factory_returns_empty(self):
        assert self._f({"factory": {"name": "unknown"}}) == []

    def test_sentiment_factory_generates_items(self):
        result = self._f({"factory": {"name": "sentiment_examples", "count": 20}})
        assert len(result) == 20
        for item in result:
            assert "input_json" in item
            assert "expected_json" in item
            assert item["label_source"] == "synthetic_factory"

    def test_search_factory_generates_items(self):
        result = self._f({"factory": {"name": "search_queries", "count": 10}})
        assert len(result) == 10
        for item in result:
            assert "query" in item["input_json"]
            assert "event_ids" in item["expected_json"]

    def test_alert_factory_generates_items(self):
        result = self._f({"factory": {"name": "alert_fixtures", "count": 5}})
        assert len(result) == 5

    def test_sentiment_default_count(self):
        result = self._f({"factory": {"name": "sentiment_examples"}})
        assert len(result) == 100

    def test_sentiment_labels_coverage(self):
        result = self._f({"factory": {"name": "sentiment_examples", "count": 20}})
        labels = {item["expected_json"]["label"] for item in result}
        assert "positive" in labels
        assert "negative" in labels


class TestMaterializeSeedItems:
    def _m(self, item_set):
        from src.eval.runner import materialize_seed_items
        return materialize_seed_items(item_set)

    def test_explicit_items_only(self):
        item_set = {"items": [{"input_json": {}, "expected_json": {}}]}
        result = self._m(item_set)
        assert len(result) == 1

    def test_factory_items_added(self):
        item_set = {"factory": {"name": "search_queries", "count": 5}}
        result = self._m(item_set)
        assert len(result) == 5

    def test_both_combined(self):
        item_set = {
            "items": [{"input_json": {}, "expected_json": {}}],
            "factory": {"name": "search_queries", "count": 3},
        }
        result = self._m(item_set)
        assert len(result) == 4

    def test_empty_set_returns_empty(self):
        assert self._m({}) == []


class TestSupportedTasks:
    def test_non_empty(self):
        from src.eval.runner import SUPPORTED_TASKS
        assert len(SUPPORTED_TASKS) > 0

    def test_search_present(self):
        from src.eval.runner import SUPPORTED_TASKS
        assert "search" in SUPPORTED_TASKS

    def test_identity_present(self):
        from src.eval.runner import SUPPORTED_TASKS
        assert "identity" in SUPPORTED_TASKS

    def test_all_strings(self):
        from src.eval.runner import SUPPORTED_TASKS
        assert all(isinstance(t, str) for t in SUPPORTED_TASKS)


# ---------------------------------------------------------------------------
# scheduler.scheduler: _env_flag, _env_int, _run_skipped_due_lock, _backup_window_open
# ---------------------------------------------------------------------------

class TestSchedulerEnvFlag:
    def test_true_values(self, monkeypatch):
        from src.scheduler.scheduler import _env_flag
        for val in ("1", "true", "yes", "on", "True", "YES"):
            monkeypatch.setenv("_TEST_SCHED_FLAG", val)
            assert _env_flag("_TEST_SCHED_FLAG") is True, f"Failed for {val!r}"

    def test_false_values(self, monkeypatch):
        from src.scheduler.scheduler import _env_flag
        for val in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("_TEST_SCHED_FLAG", val)
            assert _env_flag("_TEST_SCHED_FLAG") is False, f"Failed for {val!r}"

    def test_default_used_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_SCHED_FLAG", raising=False)
        from src.scheduler.scheduler import _env_flag
        assert _env_flag("_TEST_SCHED_FLAG", "1") is True
        assert _env_flag("_TEST_SCHED_FLAG", "0") is False


class TestSchedulerEnvInt:
    def test_reads_int(self, monkeypatch):
        monkeypatch.setenv("_TEST_SCHED_INT", "42")
        from src.scheduler.scheduler import _env_int
        assert _env_int("_TEST_SCHED_INT", 10) == 42

    def test_default_on_invalid(self, monkeypatch):
        monkeypatch.setenv("_TEST_SCHED_INT", "bad")
        from src.scheduler.scheduler import _env_int
        assert _env_int("_TEST_SCHED_INT", 7) == 7

    def test_minimum_enforced(self, monkeypatch):
        monkeypatch.setenv("_TEST_SCHED_INT", "1")
        from src.scheduler.scheduler import _env_int
        assert _env_int("_TEST_SCHED_INT", 5, minimum=3) == 3

    def test_maximum_enforced(self, monkeypatch):
        monkeypatch.setenv("_TEST_SCHED_INT", "100")
        from src.scheduler.scheduler import _env_int
        assert _env_int("_TEST_SCHED_INT", 5, maximum=50) == 50

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_SCHED_INT", raising=False)
        from src.scheduler.scheduler import _env_int
        assert _env_int("_TEST_SCHED_INT", 99) == 99


class TestRunSkippedDueLock:
    def test_skipped_true_returns_true(self):
        from src.scheduler.scheduler import _run_skipped_due_lock
        assert _run_skipped_due_lock({"skipped": True}) is True

    def test_skipped_false_returns_false(self):
        from src.scheduler.scheduler import _run_skipped_due_lock
        assert _run_skipped_due_lock({"skipped": False}) is False

    def test_non_dict_returns_false(self):
        from src.scheduler.scheduler import _run_skipped_due_lock
        assert _run_skipped_due_lock("string") is False
        assert _run_skipped_due_lock(None) is False
        assert _run_skipped_due_lock(42) is False

    def test_empty_dict_returns_false(self):
        from src.scheduler.scheduler import _run_skipped_due_lock
        assert _run_skipped_due_lock({}) is False

    def test_skipped_none_returns_false(self):
        from src.scheduler.scheduler import _run_skipped_due_lock
        assert _run_skipped_due_lock({"skipped": None}) is False


class TestBackupWindowOpen:
    def test_hour_at_or_after_backup_hour(self):
        from src.scheduler.scheduler import _backup_window_open
        now = datetime(2026, 1, 15, 3, 0, 0, tzinfo=timezone.utc)
        assert _backup_window_open(now, backup_hour_utc=2) is True

    def test_hour_before_backup_hour(self):
        from src.scheduler.scheduler import _backup_window_open
        now = datetime(2026, 1, 15, 1, 0, 0, tzinfo=timezone.utc)
        assert _backup_window_open(now, backup_hour_utc=2) is False

    def test_exact_backup_hour(self):
        from src.scheduler.scheduler import _backup_window_open
        now = datetime(2026, 1, 15, 2, 0, 0, tzinfo=timezone.utc)
        assert _backup_window_open(now, backup_hour_utc=2) is True

    def test_midnight_zero_hour(self):
        from src.scheduler.scheduler import _backup_window_open
        now = datetime(2026, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        assert _backup_window_open(now, backup_hour_utc=0) is True


# ---------------------------------------------------------------------------
# api.websocket: PUSH_INTERVAL_SECONDS constant
# ---------------------------------------------------------------------------

class TestWebsocketConstants:
    def test_push_interval_positive(self):
        from src.api.websocket import PUSH_INTERVAL_SECONDS
        assert PUSH_INTERVAL_SECONDS > 0

    def test_push_interval_reasonable(self):
        from src.api.websocket import PUSH_INTERVAL_SECONDS
        # Should be a few seconds, not hours
        assert PUSH_INTERVAL_SECONDS <= 60
