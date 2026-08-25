import pytest
from datetime import datetime, timedelta, timezone


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


@pytest.mark.asyncio
async def test_health_uses_latest_actionable_backup_run(monkeypatch):
    from src.api.routes import health

    backup_queries: list[str] = []

    class AnalyzerConn:
        async def fetchval(self, sql):
            if "SELECT 1" in sql:
                return 1
            if "COUNT(*) FROM entities" in sql:
                return 10
            if "COUNT(*) FROM alerts" in sql:
                return 2
            raise AssertionError(f"Unexpected fetchval SQL: {sql}")

        async def fetchrow(self, sql, *args):
            if "FROM analysis_runs" in sql:
                return None
            if "FROM analyzer_backup_runs" in sql:
                backup_queries.append(sql)
                return {
                    "status": "success",
                    "kinds": ["daily"],
                    "started_at": None,
                    "finished_at": None,
                    "path": "/app/backups/db/daily/unifiedanalyzer_daily.dump",
                    "size_bytes": 123,
                    "deleted_count": 0,
                    "restore_validation": "passed: pg_restore --list",
                    "error_message": None,
                }
            if "FROM audit_log" in sql and "COUNT(*) FILTER" in sql:
                return {
                    "pending_jsonl": 2,
                    "jsonl_errors": 1,
                    "latest_jsonl_written_at": None,
                    "latest_jsonl_error_at": None,
                }
            if "FROM audit_log" in sql and "decision_jsonl_error IS NOT NULL" in sql:
                return {"decision_jsonl_error": "disk full"}
            raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    class CollectorConn:
        async def fetchval(self, sql):
            if "SELECT 1" in sql:
                return 1
            raise AssertionError(f"Unexpected collector SQL: {sql}")

    monkeypatch.setattr(health, "get_analyzer_pool", lambda: _Pool(AnalyzerConn()))
    monkeypatch.setattr(health, "get_collector_pool", lambda: _Pool(CollectorConn()))

    result = await health.health_check()

    assert result["status"] == "degraded"
    assert result["last_backup_run"]["path"] == "/app/backups/db/daily/unifiedanalyzer_daily.dump"
    assert result["decision_log"]["pending_jsonl"] == 2
    assert result["decision_log"]["jsonl_errors"] == 1
    assert result["decision_log"]["latest_jsonl_error"] == "disk full"
    assert "WHERE status = 'failed'" in backup_queries[0]
    assert "status = 'success' AND path IS NOT NULL" in backup_queries[0]


@pytest.mark.asyncio
async def test_run_freshness_accepts_fresh_running_heartbeat():
    from src.api.routes.health import _run_freshness

    now = datetime.now(timezone.utc)

    class Conn:
        async def fetchrow(self, sql, *args):
            if "status = 'completed'" in sql:
                return {"finished_at": now - timedelta(hours=12)}
            if "status = 'running'" in sql:
                return {
                    "started_at": now - timedelta(hours=1),
                    "heartbeat_at": now - timedelta(minutes=10),
                    "error_message": None,
                }
            raise AssertionError(f"Unexpected SQL: {sql}")

    result = await _run_freshness(
        Conn(),
        "incremental",
        completed_stale_after_seconds=3600,
        heartbeat_stale_after_seconds=5400,
    )

    assert result["ok"] is True
    assert result["state"] == "running"
    assert "heartbeat is fresh" in result["detail"]


@pytest.mark.asyncio
async def test_run_freshness_degrades_without_completed_or_running_progress():
    from src.api.routes.health import _run_freshness

    class Conn:
        async def fetchrow(self, sql, *args):
            return None

    result = await _run_freshness(
        Conn(),
        "incremental",
        completed_stale_after_seconds=3600,
        heartbeat_stale_after_seconds=5400,
    )

    assert result["ok"] is False
    assert result["state"] == "stale"
    assert result["detail"] == "no completed run and no fresh running heartbeat"


@pytest.mark.asyncio
async def test_supabase_export_health_ok_when_drained_and_populated(monkeypatch):
    from src.api.routes import health

    monkeypatch.setattr(
        health,
        "supabase_export_config",
        lambda: {"configured": True, "mode": "postgres_direct", "payload": "normalized_indicators_only"},
    )

    class Conn:
        async def fetchval(self, sql):
            assert "to_regclass('public.normalized_indicators')" in sql
            return True

        async def fetchrow(self, sql):
            assert "ready_to_export" in sql
            return {"ready_to_export": 0, "exported_count": 12, "pending_non_exportable": 3}

    result = await health._supabase_export_health(Conn())

    assert result["ok"] is True
    assert result["state"] == "ok"
    assert result["ready_to_export"] == 0
    assert result["exported_count"] == 12


@pytest.mark.asyncio
async def test_supabase_export_health_degrades_on_exportable_backlog(monkeypatch):
    from src.api.routes import health

    monkeypatch.setattr(
        health,
        "supabase_export_config",
        lambda: {"configured": True, "mode": "postgres_direct", "payload": "normalized_indicators_only"},
    )
    monkeypatch.setenv("ANALYZER_HEALTH_SUPABASE_READY_WARN_THRESHOLD", "0")

    class Conn:
        async def fetchval(self, sql):
            return True

        async def fetchrow(self, sql):
            return {"ready_to_export": 2, "exported_count": 12, "pending_non_exportable": 3}

    result = await health._supabase_export_health(Conn())

    assert result["ok"] is False
    assert result["state"] == "backlog"
    assert "2 exportable" in result["detail"]


@pytest.mark.asyncio
async def test_face_processing_health_reports_recent_indexed_images():
    from src.api.routes import health

    now = datetime.now(timezone.utc)

    class Conn:
        async def fetchrow(self, sql, *args):
            if "to_regclass('facetracker.images')" in sql:
                return {"images_exists": True, "faces_exists": True, "entity_faces_exists": True}
            if "facetracker.images" in sql:
                return {
                    "image_count": 10,
                    "latest_image_at": now - timedelta(minutes=5),
                    "face_count": 3,
                    "entity_face_count": 2,
                }
            raise AssertionError(f"Unexpected SQL: {sql}")

    result = await health._face_processing_health(Conn())

    assert result["ok"] is True
    assert result["state"] == "ok"
    assert result["image_count"] == 10
    assert result["face_count"] == 3
    assert result["entity_face_count"] == 2


@pytest.mark.asyncio
async def test_face_processing_health_degrades_on_stale_latest_image(monkeypatch):
    from src.api.routes import health

    monkeypatch.setenv("ANALYZER_HEALTH_FACE_PROCESSING_STALE_HOURS", "1")
    old = datetime.now(timezone.utc) - timedelta(hours=2)

    class Conn:
        async def fetchrow(self, sql, *args):
            if "to_regclass('facetracker.images')" in sql:
                return {"images_exists": True, "faces_exists": True, "entity_faces_exists": True}
            return {
                "image_count": 10,
                "latest_image_at": old,
                "face_count": 3,
                "entity_face_count": 2,
            }

    result = await health._face_processing_health(Conn())

    assert result["ok"] is False
    assert result["state"] == "stale"
