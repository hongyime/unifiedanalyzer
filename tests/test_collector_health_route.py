from datetime import datetime, timezone

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
