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
        "/health?include_sources=true": {
            "status": "ok",
            "source_issues": [],
            "browser_extension": {
                "ingest_health": {
                    "state": "active",
                    "active_platforms": ["facebook", "instagram", "x"],
                },
                "maintenance": {
                    "state": "ok",
                    "last_terminal_state": "ok",
                    "detail": "audit and reload completed",
                },
                "issues": [],
            },
        },
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
                {
                    "service": "youtube",
                    "bucket": "search",
                    "paused": True,
                    "reset_at": "2099-01-01T00:00:00+00:00",
                    "updated_at": "2026-08-21T00:00:00+00:00",
                },
                {
                    "service": "youtube",
                    "bucket": "search",
                    "paused": True,
                    "reset_at": "2020-01-01T00:00:00+00:00",
                    "updated_at": "2020-01-01T00:00:00+00:00",
                },
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
        "/collectors/source-matrix": {
            "sources": [
                {
                    "source": "facebook",
                    "status": "live",
                    "collection_mode": "chrome extension",
                    "current_hour": {"media_items": 7, "records": 8, "messages": 0},
                    "last_24h": {"media_items": 20, "rate_limits": 0, "access_errors": 0},
                    "blocker": {"kind": "none", "severity": "ok", "summary": "Collecting normally."},
                },
                {
                    "source": "x",
                    "status": "live",
                    "collection_mode": "chrome extension",
                    "current_hour": {"media_items": 1, "records": 3, "messages": 0},
                    "last_24h": {"media_items": 9, "rate_limits": 0, "access_errors": 0},
                    "blocker": {"kind": "none", "severity": "ok", "summary": "Collecting normally."},
                },
                {
                    "source": "tiktok",
                    "status": "live",
                    "collection_mode": "chrome extension + headless",
                    "current_hour": {"media_items": 0, "records": 0, "messages": 0, "rate_limits": 0},
                    "last_24h": {"media_items": 2, "rate_limits": 54, "access_errors": 0},
                    "blocker": {"kind": "none", "severity": "ok", "summary": "Collecting normally."},
                    "rate_limit": {
                        "active_now": True,
                        "active_until": "2026-08-21T21:14:28+00:00",
                        "latest_reason": "challenge/block signature",
                    },
                },
            ],
        },
        "/optional-rollout/status?feature=spiderfoot&stage=dry-run": {
            "feature": "spiderfoot",
            "recommended_action": "dry_run",
            "can_proceed": True,
        },
    }
    cookie_vault_payload = {
        "ok": True,
        "count": 60,
        "quality_score": 5130,
        "latest_preserved": False,
        "auth_summary": {
            "facebook": ["c_user", "xs"],
            "instagram": ["sessionid"],
            "strava": ["_strava4_session"],
            "tiktok": ["sessionid", "ttwid"],
            "x": ["auth_token", "ct0"],
        },
    }

    async def fake_fetch(path, *, timeout=None):
        assert timeout is not None
        return {"reachable": True, "available": True, "payload": payloads[path]}

    async def fake_absolute_fetch(url, *, timeout=None):
        assert url.endswith("/health")
        assert timeout is not None
        return {"reachable": True, "available": True, "payload": cookie_vault_payload}

    async def fake_rolling_yield():
        return {
            "available": True,
            "window_seconds": 3600,
            "sources": [
                {
                    "source": "facebook",
                    "requests_rolling_60m": 3,
                    "observed_rolling_60m": 20,
                    "stored_rolling_60m": 10,
                    "latest_content_at": "2026-08-21T01:00:00+00:00",
                },
                {
                    "source": "x",
                    "requests_rolling_60m": 1,
                    "observed_rolling_60m": 3,
                    "stored_rolling_60m": 3,
                    "latest_content_at": "2026-08-21T01:00:00+00:00",
                },
            ],
        }

    monkeypatch.setattr(collector_health, "_fetch_collector_dashboard_endpoint", fake_fetch)
    monkeypatch.setattr(collector_health, "_fetch_collector_absolute_endpoint", fake_absolute_fetch)
    monkeypatch.setattr(collector_health, "_fetch_browser_yield_rolling_60m", fake_rolling_yield)

    result = asyncio.run(collector_health.collector_production_status())

    assert result["collector_dashboard"] == "ok"
    summary = result["summary"]
    assert summary["dashboard_health_status"] == "ok"
    assert summary["dashboard_health_effective_status"] == "ok"
    assert summary["source_issues"] == 0
    assert summary["hard_source_issues"] == 0
    assert summary["browser_ingest_state"] == "active"
    assert summary["browser_ingest_effective_state"] == "active"
    assert summary["browser_ingest_active_platforms"] == ["facebook", "instagram", "x"]
    assert summary["browser_maintenance_state"] == "ok"
    assert summary["browser_maintenance_last_terminal_state"] == "ok"
    assert summary["cookie_vault_ok"] is True
    assert summary["cookie_vault_count"] == 60
    assert summary["cookie_vault_missing_auth_platforms"] == []
    assert summary["instagram_stuck_stage"] == "telegram_upload"
    assert summary["media_yield_current_hour"][0]["source"] == "facebook"
    assert summary["media_yield_current_hour"][0]["media_current_hour"] == 7
    assert summary["media_yield_current_hour"][0]["stored_rolling_60m"] == 10
    assert summary["media_yield_current_hour"][0]["exempt"] is False
    tiktok = next(row for row in summary["media_yield_current_hour"] if row["source"] == "tiktok")
    assert tiktok["rate_limit"]["active_now"] is True
    assert tiktok["exempt"] is True
    assert summary["domain_pacing_sources"] == 2
    assert summary["domain_robots_blocked"] == 2
    assert summary["domain_429"] == 1
    assert summary["quota_paused"] == 1
    assert summary["quota_paused_samples"][0]["reset_at"] == "2099-01-01T00:00:00+00:00"
    assert summary["realtime_queue_depth"] == 3
    assert len(summary["realtime_failed_sources"]) == 2
    assert summary["optional_rollout_action"] == "dry_run"


def test_collector_production_summary_ignores_quiet_rollup_excluded_source_issues():
    surfaces = {
        "dashboard_health": {
            "reachable": True,
            "available": True,
            "payload": {
                "status": "degraded",
                "source_issues": [
                    {
                        "source": "beeper_google_chat",
                        "parent_source": "beeper",
                        "rollup_exclude": True,
                        "status": "stale",
                        "status_label": "quiet",
                        "status_severity": "ok",
                        "blocker": {
                            "kind": "quiet_beeper_subsource",
                            "severity": "ok",
                        },
                    }
                ],
                "browser_extension": {
                    "ingest_health": {
                        "state": "active",
                        "active_platforms": ["facebook", "instagram", "x"],
                    },
                    "maintenance": {
                        "state": "ok",
                        "last_terminal_state": "ok",
                    },
                    "issues": [],
                },
            },
        },
        "browser_cookie_vault": {
            "reachable": True,
            "available": True,
            "payload": {
                "ok": True,
                "count": 89,
                "effective_latest": {
                    "restorable": True,
                    "auth_summary": {
                        "instagram": ["sessionid"],
                        "facebook": ["c_user", "xs"],
                        "x": ["auth_token", "ct0"],
                        "strava": ["_strava4_session"],
                        "tiktok": ["sessionid", "ttwid"],
                    },
                },
            },
        },
        "browser_yield_rolling_60m": {
            "reachable": True,
            "available": True,
            "payload": {
                "available": True,
                "sources": [
                    {"source": "facebook", "stored_rolling_60m": 5},
                    {"source": "instagram", "stored_rolling_60m": 5},
                    {"source": "x", "stored_rolling_60m": 5},
                ],
            },
        },
    }

    summary = collector_health._collector_production_summary(surfaces)

    assert summary["source_issues"] == 1
    assert summary["hard_source_issues"] == 0
    assert summary["dashboard_health_status"] == "degraded"
    assert summary["dashboard_health_effective_status"] == "ok"


def test_collector_production_summary_treats_stale_browser_watchdog_as_warning():
    surfaces = {
        "dashboard_health": {
            "reachable": True,
            "available": True,
            "payload": {
                "status": "degraded",
                "source_issues": [
                    {
                        "source": "facebook",
                        "status": "degraded",
                        "source_health_status": "degraded",
                        "source_health_error": (
                            "browser capture stalled: browser content progress is 6829s old (> 3600s) (watchdog)"
                        ),
                        "browser_content_stale": True,
                        "browser_health_status": "post_reload_scrape_nudge_retry_scheduled",
                        "browser_health_reason": "message_timeout_content_stale",
                    },
                    {
                        "source": "x",
                        "status": "degraded",
                        "source_health_status": "degraded",
                        "detail": "browser content progress is 8134s old (> 3600s)",
                        "browser_content_stale": True,
                        "browser_health_status": "background_tab_seen",
                        "browser_health_reason": "warm_start",
                    },
                ],
                "browser_extension": {
                    "ingest_health": {
                        "state": "active_via_maintenance",
                        "active_platforms": ["facebook", "instagram", "x"],
                    },
                    "maintenance": {
                        "state": "degraded",
                        "last_terminal_state": "degraded",
                        "detail": "maintenance loop sleeping after nonzero pass",
                    },
                    "issues": [],
                },
            },
        },
        "browser_cookie_vault": {
            "reachable": True,
            "available": True,
            "payload": {
                "ok": True,
                "effective_latest": {
                    "restorable": True,
                    "auth_summary": {
                        "instagram": ["sessionid"],
                        "facebook": ["c_user", "xs"],
                        "x": ["auth_token", "ct0"],
                        "strava": ["_strava4_session"],
                        "tiktok": ["sessionid", "ttwid"],
                    },
                },
            },
        },
    }

    summary = collector_health._collector_production_summary(surfaces)

    assert summary["source_issues"] == 2
    assert summary["hard_source_issues"] == 0
    assert summary["dashboard_health_effective_status"] == "ok"


def test_collector_production_summary_prefers_effective_latest_cookie_snapshot():
    surfaces = {
        "dashboard_health": {
            "reachable": True,
            "available": True,
            "payload": {
                "status": "ok",
                "source_issues": [],
                "browser_extension": {
                    "ingest_health": {
                        "state": "active",
                        "active_platforms": ["facebook", "instagram", "x"],
                    },
                    "maintenance": {"state": "ok", "last_terminal_state": "ok"},
                    "issues": [],
                },
            },
        },
        "browser_cookie_vault": {
            "reachable": True,
            "available": True,
            "payload": {
                "ok": True,
                "count": 2,
                "quality_score": 22,
                "latest_preserved": True,
                "auth_summary": {
                    "facebook": ["datr"],
                    "x": ["guest_id"],
                },
                "effective_latest": {
                    "restorable": True,
                    "quality_score": 5131,
                    "cookie_count": 61,
                    "ts": "2026-08-21T03:31:41Z",
                    "auth_summary": {
                        "facebook": ["c_user", "xs"],
                        "instagram": ["sessionid"],
                        "strava": ["_strava4_session"],
                        "tiktok": ["sessionid", "ttwid"],
                        "x": ["auth_token", "ct0"],
                    },
                },
            },
        },
    }

    summary = collector_health._collector_production_summary(surfaces)

    assert summary["cookie_vault_latest_preserved"] is True
    assert summary["cookie_vault_effective_latest_restorable"] is True
    assert summary["cookie_vault_effective_latest_ts"] == "2026-08-21T03:31:41Z"
    assert summary["cookie_vault_quality_score"] == 5131
    assert summary["cookie_vault_missing_auth_platforms"] == []


def test_collector_production_summary_requires_tiktok_session_cookie():
    surfaces = {
        "dashboard_health": {
            "reachable": True,
            "available": True,
            "payload": {
                "status": "ok",
                "source_issues": [],
                "browser_extension": {
                    "ingest_health": {"state": "active", "active_platforms": ["tiktok"]},
                    "maintenance": {"state": "ok", "last_terminal_state": "ok"},
                    "issues": [],
                },
            },
        },
        "browser_cookie_vault": {
            "reachable": True,
            "available": True,
            "payload": {
                "ok": True,
                "count": 4,
                "quality_score": 120,
                "auth_summary": {
                    "facebook": ["c_user", "xs"],
                    "instagram": ["sessionid"],
                    "strava": ["_strava4_session"],
                    "tiktok": ["ttwid"],
                    "x": ["auth_token", "ct0"],
                },
            },
        },
    }

    summary = collector_health._collector_production_summary(surfaces)

    assert summary["cookie_vault_missing_auth_platforms"] == ["tiktok"]


def test_collector_production_status_softens_diagnostic_only_degraded_when_browser_ingest_active(monkeypatch):
    payloads = {
        "/health?include_sources=true": {
            "status": "degraded",
            "source_issues": [
                {
                    "source": "source_liveness",
                    "status": "unknown",
                    "message": "source liveness unavailable: TimeoutError",
                }
            ],
            "browser_extension": {
                "ingest_health": {
                    "state": "active",
                    "active": True,
                    "active_platforms": ["facebook", "instagram", "x"],
                },
                "maintenance": {
                    "state": "degraded",
                    "last_terminal_state": "degraded",
                    "detail": "stale maintenance pass",
                },
                "issues": [],
            },
        },
        "/instagram/health": {"cooldown": {"active": False}},
        "/domain-pacing/status": {"available": True, "sources": []},
        "/api-quotas/status": {"available": True, "snapshots": []},
        "/media/realtime-feed/status": {"available": True, "queue_depth": 0, "source_counters": {}},
        "/collectors/source-matrix": {
            "sources": [
                {
                    "source": "facebook",
                    "status": "live",
                    "collection_mode": "chrome extension",
                    "current_hour": {"media_items": 5, "records": 5, "messages": 0},
                    "last_24h": {"media_items": 20, "rate_limits": 0, "access_errors": 0},
                },
            ],
        },
        "/optional-rollout/status?feature=spiderfoot&stage=dry-run": {
            "feature": "spiderfoot",
            "recommended_action": "dry_run",
            "can_proceed": True,
        },
    }
    cookie_vault_payload = {
        "ok": True,
        "count": 61,
        "auth_summary": {
            "facebook": ["c_user", "xs"],
            "instagram": ["sessionid"],
            "strava": ["_strava4_session"],
            "tiktok": ["sessionid", "ttwid"],
            "x": ["auth_token", "ct0"],
        },
    }

    async def fake_fetch(path, *, timeout=None):
        return {"reachable": True, "available": True, "payload": payloads[path]}

    async def fake_absolute_fetch(url, *, timeout=None):
        return {"reachable": True, "available": True, "payload": cookie_vault_payload}

    async def fake_rolling_yield():
        return {
            "available": True,
            "window_seconds": 3600,
            "sources": [
                {
                    "source": "facebook",
                    "requests_rolling_60m": 2,
                    "observed_rolling_60m": 5,
                    "stored_rolling_60m": 5,
                },
            ],
        }

    monkeypatch.setattr(collector_health, "_fetch_collector_dashboard_endpoint", fake_fetch)
    monkeypatch.setattr(collector_health, "_fetch_collector_absolute_endpoint", fake_absolute_fetch)
    monkeypatch.setattr(collector_health, "_fetch_browser_yield_rolling_60m", fake_rolling_yield)

    result = asyncio.run(collector_health.collector_production_status())

    summary = result["summary"]
    assert summary["dashboard_health_status"] == "degraded"
    assert summary["dashboard_health_effective_status"] == "ok"
    assert summary["source_issues"] == 1
    assert summary["hard_source_issues"] == 0
    assert summary["browser_ingest_state"] == "active"
    assert summary["browser_ingest_effective_state"] == "active"
