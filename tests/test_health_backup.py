import pytest


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

        async def fetchrow(self, sql):
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
            raise AssertionError(f"Unexpected fetchrow SQL: {sql}")

    class CollectorConn:
        async def fetchval(self, sql):
            if "SELECT 1" in sql:
                return 1
            raise AssertionError(f"Unexpected collector SQL: {sql}")

    monkeypatch.setattr(health, "get_analyzer_pool", lambda: _Pool(AnalyzerConn()))
    monkeypatch.setattr(health, "get_collector_pool", lambda: _Pool(CollectorConn()))

    result = await health.health_check()

    assert result["status"] == "ok"
    assert result["last_backup_run"]["path"] == "/app/backups/db/daily/unifiedanalyzer_daily.dump"
    assert "WHERE status = 'failed' OR path IS NOT NULL" in backup_queries[0]
