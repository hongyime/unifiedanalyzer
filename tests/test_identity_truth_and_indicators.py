import asyncio
from datetime import datetime, timezone
import uuid

from src.pipeline.identity_truth import build_truth_assertion, corroborated_auto_truth
from src.pipeline.indicator_export import (
    export_pending_supabase_indicators,
    extract_indicators_from_text,
    normalize_indicator,
    resolve_domain_to_ips,
    SUPABASE_REMOTE_TABLE_SQL,
    supabase_export_config,
)


def _signal(**overrides):
    row = {
        "id": "00000000-0000-0000-0000-000000000001",
        "signal_type": "recon_observation",
        "source_platform": "spiderfoot",
        "source_table": "recon_observations",
        "value": "alice@example.com",
        "confidence": 0.6,
        "metadata": {},
    }
    row.update(overrides)
    return row


def test_spiderfoot_alone_is_not_auto_truth():
    ok, confidence, summary = corroborated_auto_truth([_signal()])

    assert ok is False
    assert confidence == 0
    assert summary["reason"] == "requires_spiderfoot_and_independent_hard_signal"


def test_spiderfoot_promotes_after_independent_hard_signal():
    assertion = build_truth_assertion(
        "11111111-1111-1111-1111-111111111111",
        "alice@example.com",
        [
            _signal(),
            _signal(
                id="00000000-0000-0000-0000-000000000002",
                signal_type="email_match",
                source_platform="telegram",
                source_table="telegram_users",
                confidence=0.95,
            ),
        ],
    )

    assert assertion is not None
    assert assertion["truth_state"] == "auto_truth"
    assert assertion["confidence"] >= 0.85
    assert assertion["evidence_count"] == 2


def test_extract_indicators_from_text_normalizes_core_types():
    text = 'Reach "Alice Example" at Alice@Example.COM, +1 (415) 555-0123, @Alice, example.org, 203.0.113.5'

    indicators = extract_indicators_from_text(text, default_region="US")
    values = {(item.indicator_type, item.normalized_value) for item in indicators}

    assert ("email", "alice@example.com") in values
    assert ("phone_e164", "+14155550123") in values
    assert ("username", "alice") in values
    assert ("domain", "example.org") in values
    assert ("ipv4", "203.0.113.5") in values
    assert ("full_name", "Alice Example") in values


def test_normalize_indicator_rejects_invalid_ipv4():
    assert normalize_indicator("ipv4", "999.1.1.1") is None


def test_resolve_domain_to_ips_uses_injected_resolver():
    def fake_resolver(*args):
        return [
            (None, None, None, "", ("203.0.113.8", 0)),
            (None, None, None, "", ("203.0.113.7", 0)),
        ]

    assert resolve_domain_to_ips("example.com", resolver=fake_resolver) == ["203.0.113.7", "203.0.113.8"]


def test_export_router_exposes_identity_and_indicator_status_routes():
    from src.api.routes.export import router

    paths = {getattr(route, "path", "") for route in router.routes}

    assert "/identity/truth/status" in paths
    assert "/indicators/export/supabase/status" in paths
    assert "/indicators/export/supabase/preview" in paths


def test_supabase_config_accepts_direct_database_url(monkeypatch):
    monkeypatch.setenv("SUPABASE_PROJECT_ID", "exampleproject")
    monkeypatch.setenv("SUPABASE_URL", "https://exampleproject.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "sb_publishable_example")
    monkeypatch.setenv("SUPABASE_DATABASE_URL", "postgresql://postgres:secret@example.supabase.co/postgres")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)

    config = supabase_export_config()

    assert config["configured"] is True
    assert config["publishable_configured"] is True
    assert config["database_url_configured"] is True
    assert config["write_method"] == "postgres_direct"


def test_supabase_config_prefers_direct_database_over_service_role(monkeypatch):
    monkeypatch.setenv("SUPABASE_PROJECT_ID", "exampleproject")
    monkeypatch.setenv("SUPABASE_URL", "https://exampleproject.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-example")
    monkeypatch.setenv("SUPABASE_DATABASE_URL", "postgresql://postgres:secret@example.supabase.co/postgres")

    config = supabase_export_config()

    assert config["service_role_configured"] is True
    assert config["database_url_configured"] is True
    assert config["write_method"] == "postgres_direct"


def test_supabase_remote_schema_is_compact_indicator_table():
    assert "CREATE TABLE IF NOT EXISTS normalized_indicators" in SUPABASE_REMOTE_TABLE_SQL
    assert "normalized_value TEXT NOT NULL" in SUPABASE_REMOTE_TABLE_SQL
    assert "ALTER TABLE normalized_indicators ENABLE ROW LEVEL SECURITY" in SUPABASE_REMOTE_TABLE_SQL
    assert "REVOKE ALL ON TABLE normalized_indicators FROM anon, authenticated" in SUPABASE_REMOTE_TABLE_SQL
    assert "timeline_events" not in SUPABASE_REMOTE_TABLE_SQL
    assert "identity_signals" not in SUPABASE_REMOTE_TABLE_SQL


class _FakeLocalConn:
    def __init__(self, rows):
        self.rows = rows
        self.fetch_limit = None
        self.executed = []

    async def fetch(self, _sql, limit):
        self.fetch_limit = limit
        return self.rows

    async def execute(self, sql, *args):
        self.executed.append((sql, args))


class _FakeRemoteConn:
    def __init__(self):
        self.executed = []
        self.many = []
        self.closed = False

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def executemany(self, sql, rows):
        self.many.append((sql, rows))

    async def close(self):
        self.closed = True


def _indicator_row(**overrides):
    now = datetime(2026, 8, 14, tzinfo=timezone.utc)
    row = {
        "id": str(uuid.uuid4()),
        "indicator_type": "domain",
        "normalized_value": "example.com",
        "display_value": "example.com",
        "source_families": ["website"],
        "evidence_count": 2,
        "confidence": 0.9,
        "first_seen_at": now,
        "last_seen_at": now,
        "metadata": {"source": "test"},
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def test_supabase_export_dry_run_does_not_touch_remote():
    local = _FakeLocalConn([_indicator_row()])
    remote = _FakeRemoteConn()

    result = asyncio.run(
        export_pending_supabase_indicators(
            local,
            dry_run=True,
            mode="postgres_direct",
            remote_conn=remote,
        )
    )

    assert result["status"] == "dry_run"
    assert result["selected"] == 1
    assert result["exported"] == 0
    assert remote.executed == []
    assert remote.many == []
    assert local.executed == []


def test_supabase_export_upserts_remote_and_marks_local_exported():
    local = _FakeLocalConn([_indicator_row()])
    remote = _FakeRemoteConn()

    result = asyncio.run(
        export_pending_supabase_indicators(
            local,
            mode="postgres_direct",
            remote_conn=remote,
        )
    )

    assert result["status"] == "ok"
    assert result["selected"] == 1
    assert result["exported"] == 1
    assert "CREATE TABLE IF NOT EXISTS normalized_indicators" in remote.executed[0][0]
    assert len(remote.many) == 1
    assert "ON CONFLICT (indicator_type, normalized_value)" in remote.many[0][0]
    assert "export_status = 'exported'" in local.executed[0][0]


def test_supabase_export_can_ensure_schema_with_empty_batch():
    local = _FakeLocalConn([])
    remote = _FakeRemoteConn()

    result = asyncio.run(
        export_pending_supabase_indicators(
            local,
            mode="postgres_direct",
            remote_conn=remote,
            ensure_schema_when_empty=True,
        )
    )

    assert result["status"] == "ok"
    assert result["selected"] == 0
    assert result["exported"] == 0
    assert result["schema_ensured"] is True
    assert "CREATE TABLE IF NOT EXISTS normalized_indicators" in remote.executed[0][0]
    assert remote.many == []
    assert local.executed == []
