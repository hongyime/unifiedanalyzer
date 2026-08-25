from src.scheduler.scheduler import _run_skipped_due_lock


def test_run_skipped_due_lock_detects_only_lock_skip_payloads():
    assert _run_skipped_due_lock({"skipped": True}) is True
    assert _run_skipped_due_lock({"skipped": False}) is False
    assert _run_skipped_due_lock({"ok": True}) is False
    assert _run_skipped_due_lock(None) is False
