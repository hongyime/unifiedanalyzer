from datetime import datetime, timezone
import asyncio

from src.api.routes import collector_health
from src.api.routes.collector_health import _collectors_from_source_matrix


def test_source_matrix_collector_health_reports_live_counts():
    matrix = {
        "sources": [
            {
                "source": "youtube",
                "status": "live",
                "collection_mode": "headless cookies",
                "last_24h": {
                    "records": 150,
                    "messages": 0,
                    "media_items": 23,
                    "rate_limits": 2,
                    "access_errors": 1,
                    "latest_record_at": "2026-07-31T11:00:00+00:00",
                    "latest_media_at": "2026-07-31T12:00:00+00:00",
                },
                "current_hour": {"records": 10, "media_items": 4},
                "blocker": {"kind": "media_backlog", "severity": "warning"},
                "media_freshness": {"status": "fresh"},
            }
        ]
    }
    targets = [
        {
            "source": "youtube",
            "status": "pending",
            "count": 553,
            "last_collection": datetime(2026, 7, 31, tzinfo=timezone.utc),
        }
    ]

    collectors = _collectors_from_source_matrix(matrix, targets)

    assert len(collectors) == 1
    row = collectors[0]
    assert row["source"] == "youtube"
    assert row["latest_status"] == "live"
    assert row["items_24h"] == 173
    assert row["records_24h"] == 150
    assert row["media_24h"] == 23
    assert row["failed_24h"] == 3
    assert row["last_completed"] == "2026-07-31T12:00:00+00:00"
    assert row["blocker"]["kind"] == "media_backlog"
    assert row["targets"][0]["count"] == 553


def test_source_matrix_collector_health_keeps_chat_message_counts():
    matrix = {
        "sources": [
            {
                "source": "telegram",
                "status": "live",
                "last_24h": {
                    "records": 6000,
                    "messages": 6000,
                    "media_items": 1400,
                },
            }
        ]
    }

    row = _collectors_from_source_matrix(matrix, [])[0]

    assert row["items_24h"] == 7400
    assert row["messages_24h"] == 6000
    assert row["media_24h"] == 1400


def test_collector_production_status_summarizes_dashboard_surfaces(monkeypatch):
    payloads = {
        "/instagram/health": {
            "stuck_stage": "telegram_upload",
            "cooldown": {"active": False},
        },
        "/domain-pacing/status": {
            "available": True,
            "sources": [
                {"source": "website", "robots_blocked": 2, "http_429": 1},
                {"source": "search", "robots_blocked": 0, "http_429": 0},
            ],
        },
        "/api-quotas/status": {
            "available": True,
            "snapshots": [
                {"service": "github", "bucket": "core", "paused": False},
                {"service": "youtube", "bucket": "search", "paused": True},
            ],
        },
        "/media/realtime-feed/status": {
            "available": True,
            "queue_depth": 3,
            "source_counters": {
                "youtube": {"sent": 4, "local_fallback": 1},
                "instagram": {"failed": 1},
            },
        },
        "/optional-rollout/status?feature=spiderfoot&stage=dry-run": {
            "feature": "spiderfoot",
            "recommended_action": "dry_run",
            "can_proceed": True,
        },
    }

    async def fake_fetch(path, *, timeout=None):
        assert timeout is not None
        return {"reachable": True, "available": True, "payload": payloads[path]}

    monkeypatch.setattr(collector_health, "_fetch_collector_dashboard_endpoint", fake_fetch)

    result = asyncio.run(collector_health.collector_production_status())

    assert result["collector_dashboard"] == "ok"
    summary = result["summary"]
    assert summary["instagram_stuck_stage"] == "telegram_upload"
    assert summary["domain_pacing_sources"] == 2
    assert summary["domain_robots_blocked"] == 2
    assert summary["domain_429"] == 1
    assert summary["quota_paused"] == 1
    assert summary["realtime_queue_depth"] == 3
    assert len(summary["realtime_failed_sources"]) == 2
    assert summary["optional_rollout_action"] == "dry_run"
