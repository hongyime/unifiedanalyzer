from datetime import datetime, timedelta, timezone

from src.notifications.intelligence import intelligence_run_lines, intelligence_status_lines


def test_intelligence_run_lines_are_short_and_contextual():
    lines = intelligence_run_lines({
        "text_features": 50,
        "sentiment_features": 45,
        "conversation_threads": 12,
        "alerts": 3,
        "alert_breakdown": {
            "emotional_spike": 1,
            "face_link_drift": 2,
            "silence_gap": 9,
        },
    })

    assert len(lines) == 2
    assert "50 text" in lines[0]
    assert "45 sentiment" in lines[0]
    assert "12 chat threads" in lines[0]
    assert "emotional spike 1" in lines[1]
    assert "face link drift 2" in lines[1]
    assert "silence" not in lines[1]


def test_intelligence_status_lines_do_not_emit_raw_chat_text():
    latest = datetime.now(timezone.utc) - timedelta(minutes=5)
    lines = intelligence_status_lines({
        "text_total": 100,
        "sentiment_ready": 90,
        "sentiment_pct": "90%",
        "fts_pct": "95%",
        "latest_text": latest,
        "chat_threads": 7,
        "search_mode": "semantic-fallback",
        "face_available": True,
        "face_ok": True,
        "face_entity_collisions": 0,
        "cluster_entity_collisions": 0,
        "location_total": 20,
        "location_active": 18,
        "location_suppressed": 1,
        "location_weak": 2,
    })

    joined = "\n".join(lines)
    assert len(lines) == 3
    assert "90/100 sentiment" in joined
    assert "7 Telegram threads" in joined
    assert "hybrid search keyword-ready" in joined
    assert "face audit OK" in joined
    assert "location 90% active" in joined
    assert "preview" not in joined.lower()
    assert "message" not in joined.lower()


def test_intelligence_status_lines_show_spike_alert_counts():
    lines = intelligence_status_lines({
        "text_total": 0,
        "sentiment_ready": 0,
        "sentiment_pct": "0%",
        "fts_pct": "0%",
        "chat_threads": 0,
        "search_mode": "semantic-enabled",
        "face_available": False,
        "location_total": 0,
        "location_active": 0,
        "location_suppressed": 0,
        "location_weak": 0,
        "intel_alerts_24h": 4,
        "emotional_spikes_24h": 2,
        "face_drift_24h": 1,
        "location_spikes_24h": 1,
        "intel_failed_phases": ["sentiment_emotion"],
    })

    joined = "\n".join(lines)
    assert "Intel alerts 24h: 2 emotional, 1 face drift, 1 location." in joined
    assert "Intel phase failures: sentiment_emotion." in joined
