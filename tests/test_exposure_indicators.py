from datetime import datetime, timedelta, timezone

import pytest

from src.pipeline.exposure_indicators import (
    _collapse_indicators,
    _indicators_for_exposure_row,
    _redacted_evidence_ref,
    stage_exposure_findings_as_indicators,
)


class _AnalyzerConn:
    def __init__(self):
        self.offset_at = None
        self.offset_id = None
        self.executed = []
        self.upserts = []

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if "INSERT INTO stream_alert_offsets" in sql:
            self.offset_id = args[2]
            self.offset_at = args[3]

    async def fetchrow(self, sql, *args):
        if "FROM stream_alert_offsets" in sql:
            if self.offset_at is None:
                return None
            return {"last_seen_at": self.offset_at, "cursor_value": self.offset_id}
        return None

    async def executemany(self, sql, rows):
        self.upserts.append((sql, rows))


class _CollectorConn:
    def __init__(self, rows):
        self.rows = rows

    async def fetchval(self, sql, *args):
        if "to_regclass('public.exposure_findings')" in sql:
            return True
        return None

    async def fetch(self, sql, *args):
        if "id::text > $2" in sql:
            since = args[0]
            row_id = args[1]
            limit = args[2]
            rows = [
                row
                for row in self.rows
                if row["collected_at"] > since
                or (row["collected_at"] == since and str(row["id"]) > str(row_id))
            ]
        elif "WHERE collected_at > $1" in sql:
            since = args[0]
            limit = args[1]
            rows = [row for row in self.rows if row["collected_at"] > since]
        else:
            limit = args[0]
            rows = list(self.rows)
        return sorted(rows, key=lambda row: (row["collected_at"], str(row["id"])))[:limit]


def _row(**overrides):
    base = {
        "id": "exposure-row-1",
        "target_scope": "*.example.com",
        "query": 'site:example.com "password"',
        "url": "https://admin.example.com/.env?token=secret-value",
        "domain": "admin.example.com",
        "category": "config_or_secret_file",
        "severity": "critical",
        "confidence": 0.9,
        "title": "Example config exposed",
        "snippet": "password=super-secret admin@example.com 203.0.113.8",
        "detected_secret": True,
        "metadata": {"engine": "test"},
        "collected_at": datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        "url_hash": "urlhash123",
    }
    base.update(overrides)
    return base


def test_exposure_indicators_are_compact_and_redacted():
    row = _row()

    indicators = _indicators_for_exposure_row(row)
    keys = {(item.indicator_type, item.normalized_value) for item in indicators}
    evidence = _redacted_evidence_ref(row)

    assert ("domain", "admin.example.com") in keys
    assert ("email", "admin@example.com") in keys
    assert ("ipv4", "203.0.113.8") in keys
    assert all("super-secret" not in str(item.metadata) for item in indicators)
    assert all("token=secret-value" not in str(item.metadata) for item in indicators)
    assert "token=secret-value" not in str(evidence)
    assert evidence["url_hash"] == "urlhash123"


@pytest.mark.asyncio
async def test_stage_exposure_findings_advances_cursor_and_avoids_rescan():
    first = _row(id="row-1")
    second = _row(
        id="row-2",
        domain="backup.example.org",
        url="https://backup.example.org/dump.sql",
        collected_at=first["collected_at"] + timedelta(minutes=5),
    )
    analyzer = _AnalyzerConn()
    collector = _CollectorConn([first, second])

    report = await stage_exposure_findings_as_indicators(analyzer, collector, limit=10)
    again = await stage_exposure_findings_as_indicators(analyzer, collector, limit=10)

    assert report["scanned"] == 2
    assert report["staged"] >= 2
    assert report["unique_indicators"] >= 2
    assert analyzer.offset_at == second["collected_at"]
    assert analyzer.offset_id == second["id"]
    assert again["scanned"] == 0


@pytest.mark.asyncio
async def test_stage_exposure_cursor_does_not_skip_same_timestamp_rows():
    collected_at = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    first = _row(id="row-1", collected_at=collected_at)
    second = _row(
        id="row-2",
        domain="backup.example.org",
        url="https://backup.example.org/dump.sql",
        collected_at=collected_at,
    )
    analyzer = _AnalyzerConn()
    collector = _CollectorConn([first, second])

    first_report = await stage_exposure_findings_as_indicators(analyzer, collector, limit=1)
    second_report = await stage_exposure_findings_as_indicators(analyzer, collector, limit=1)
    done_report = await stage_exposure_findings_as_indicators(analyzer, collector, limit=1)

    assert first_report["scanned"] == 1
    assert first_report["newest_id"] == "row-1"
    assert second_report["scanned"] == 1
    assert second_report["newest_id"] == "row-2"
    assert done_report["scanned"] == 0
    assert analyzer.offset_at == collected_at
    assert analyzer.offset_id == "row-2"


def test_collapse_indicators_batches_duplicate_exposure_evidence():
    first = _indicators_for_exposure_row(_row(id="row-1", domain="same.example.com"))
    second = _indicators_for_exposure_row(_row(id="row-2", domain="same.example.com"))

    rows = _collapse_indicators([*first, *second])
    by_key = {(row[0], row[1]): row for row in rows}

    assert ("domain", "same.example.com") in by_key
    assert by_key[("domain", "same.example.com")][3] == 2
    assert all(row[4] >= 0.75 for row in rows)


@pytest.mark.asyncio
async def test_stage_exposure_requeues_exported_indicators_for_supabase():
    analyzer = _AnalyzerConn()
    collector = _CollectorConn([_row()])

    await stage_exposure_findings_as_indicators(analyzer, collector, limit=10)

    assert analyzer.upserts
    assert "export_status = CASE" in analyzer.upserts[0][0]
    assert "THEN 'pending'" in analyzer.upserts[0][0]
from src.pipeline.exposure_indicators import _collapse_indicators as collapse_indicators
from src.pipeline.timeline_builder import PLATFORM_QUERIES, _format_platform_query


def _entry(itype, value, confidence):
    class _I:
        pass
    i = _I()
    i.indicator_type = itype
    i.normalized_value = value
    i.display_value = value
    i.confidence = confidence
    i.metadata = {}
    return i


def test_low_confidence_email_is_redacted_and_exportable():
    rows = collapse_indicators([_entry("email", "john.smith@example.com", 0.4)])
    assert rows[0][6] is True
    assert "john.smith" not in rows[0][1]
    assert rows[0][1].endswith("@example.com")


def test_low_confidence_ipv4_is_truncated_to_slash24():
    rows = collapse_indicators([_entry("ipv4", "203.0.113.57", 0.4)])
    assert rows[0][6] is True
    assert rows[0][1] == "203.0.113.0/24"


def test_domain_passes_through_redaction_unchanged():
    rows = collapse_indicators([_entry("domain", "example.edu.sg", 0.4)])
    assert rows[0][6] is True and rows[0][1] == "example.edu.sg"


def test_website_content_published_query_registered():
    spec = next(s for s in PLATFORM_QUERIES if s["source"] == "website" and s["event_type"] == "CONTENT_PUBLISHED")
    assert "FROM website_pages p" in spec["query"]


def test_website_content_published_supports_incremental_filter():
    from datetime import datetime, timezone
    spec = next(s for s in PLATFORM_QUERIES if s["source"] == "website")
    query, params = _format_platform_query(spec, datetime(2026, 8, 26, tzinfo=timezone.utc))
    assert "p.collected_at > $1" in query

