"""Contract check tests ensuring UnifiedAnalyzer conforms to the Collector schema contract.

Tests run purely with fixtures and memory mappings — no live database or
network connections required.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from src.pipeline.collector_contract import (
    COLLECTOR_CONTRACT_TABLES,
    DEPRECATED_TABLES,
    FUTURE_PHASE_TABLES,
    FROZEN_COLLECTOR_HEAD,
    validate_collector_schema,
)
from src.pipeline.timeline_builder import PLATFORM_QUERIES


DOCS_PATH = Path(__file__).resolve().parents[1] / "docs" / "collector-contract.md"


def test_contract_constants_integrity() -> None:
    """Verify frozen commit and table sets."""
    assert FROZEN_COLLECTOR_HEAD == "c97391af"
    assert len(COLLECTOR_CONTRACT_TABLES) >= 35

    # Core tables that must always be defined
    essential = [
        "media_items",
        "browser_ingest_events",
        "instagram_posts",
        "instagram_profiles",
        "telegram_messages",
        "telegram_users",
        "whatsapp_messages",
        "tiktok_posts",
        "youtube_videos",
        "lemon8_posts",
        "threads_posts",
        "facebook_posts",
        "x_posts",
        "github_commits",
        "strava_activities",
        "collection_targets",
        "recon_observations",
    ]
    for table in essential:
        assert table in COLLECTOR_CONTRACT_TABLES, f"{table} must be in contract"
        assert len(COLLECTOR_CONTRACT_TABLES[table]) > 0, f"{table} columns must not be empty"


def test_schema_validator_complete_schema_passes() -> None:
    """When all contract tables and columns are present, validation passes."""
    synthetic_complete = {
        table: set(cols) for table, cols in COLLECTOR_CONTRACT_TABLES.items()
    }
    report = validate_collector_schema(synthetic_complete)
    assert report.ok is True
    assert len(report.missing_tables) == 0
    assert len(report.missing_columns) == 0
    assert len(report.errors) == 0


def test_schema_validator_missing_table_fails_loudly() -> None:
    """When a contract table is omitted, validation fails and identifies the missing table."""
    synthetic_partial = {
        table: set(cols) for table, cols in COLLECTOR_CONTRACT_TABLES.items()
        if table != "telegram_messages"
    }
    report = validate_collector_schema(synthetic_partial)
    assert report.ok is False
    assert "telegram_messages" in report.missing_tables
    assert any("telegram_messages" in err for err in report.errors)


def test_schema_validator_missing_column_fails_loudly() -> None:
    """When a required column is missing, validation fails with clear diagnostics."""
    synthetic_bad_cols = {
        table: set(cols) for table, cols in COLLECTOR_CONTRACT_TABLES.items()
    }
    # Remove 'file_path' and 'content_id' from media_items
    synthetic_bad_cols["media_items"] = synthetic_bad_cols["media_items"] - {"file_path", "content_id"}

    report = validate_collector_schema(synthetic_bad_cols)
    assert report.ok is False
    assert "media_items" in report.missing_columns
    assert "file_path" in report.missing_columns["media_items"]
    assert "content_id" in report.missing_columns["media_items"]
    assert any("media_items" in err for err in report.errors)


def test_schema_validator_flags_deprecated_and_future_tables() -> None:
    """Validator detects deprecated and upcoming tables."""
    schema = {
        table: set(cols) for table, cols in COLLECTOR_CONTRACT_TABLES.items()
    }
    schema["instagram_dm"] = {"id", "thread_id", "text"}
    schema["telegram_poll_votes"] = {"id", "poll_id", "voter_user_id"}

    report = validate_collector_schema(schema)
    assert report.ok is True
    assert "instagram_dm" in report.deprecated_found
    assert "telegram_poll_votes" in report.future_tables_detected
    assert any("instagram_dm" in w for w in report.warnings)
    assert any("telegram_poll_votes" in w for w in report.warnings)


def test_platform_queries_conform_to_contract() -> None:
    """Every source query registered in PLATFORM_QUERIES must target a recognized platform."""
    known_sources = {
        "github", "strava", "instagram", "telegram", "whatsapp",
        "tiktok", "youtube", "lemon8", "threads", "facebook",
        "website", "x", "facetracker",
    }
    for q in PLATFORM_QUERIES:
        source = q.get("source")
        assert source in known_sources, f"Unknown source in PLATFORM_QUERIES: {source}"


def test_contract_doc_matches_spec() -> None:
    """The markdown documentation in docs/collector-contract.md must reflect the frozen commit."""
    assert DOCS_PATH.exists(), "docs/collector-contract.md must exist"
    content = DOCS_PATH.read_text(encoding="utf-8")
    assert FROZEN_COLLECTOR_HEAD in content
    for table in ["media_items", "telegram_messages", "browser_ingest_events"]:
        assert table in content
