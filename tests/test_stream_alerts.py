from datetime import datetime, timedelta, timezone

from src.pipeline.stream_alerts import extract_burst_terms, is_suppressed, make_alert_fingerprint, parse_cursor_datetime


def test_stream_alert_fingerprint_is_stable():
    start = datetime(2026, 8, 10, tzinfo=timezone.utc)
    end = start + timedelta(minutes=5)

    first = make_alert_fingerprint("TERM_BURST", entity_id="e1", source="telegram", bucket_key="#tag", window_start=start, window_end=end)
    second = make_alert_fingerprint("TERM_BURST", entity_id="e1", source="telegram", bucket_key="#tag", window_start=start, window_end=end)

    assert first.fingerprint == second.fingerprint


def test_suppression_matches_scope_window():
    now = datetime(2026, 8, 10, tzinfo=timezone.utc)
    suppressions = [{
        "alert_type": "TERM_BURST",
        "entity_id": None,
        "source": "telegram",
        "starts_at": now - timedelta(minutes=1),
        "ends_at": now + timedelta(minutes=1),
    }]

    assert is_suppressed(suppressions, alert_type="TERM_BURST", entity_id="x", source="telegram", now=now)
    assert not is_suppressed(suppressions, alert_type="MEDIA_BURST", entity_id="x", source="telegram", now=now)


def test_extract_burst_terms_normalizes_urls_handles_and_hashtags():
    terms = extract_burst_terms("see #Topic and @handle at https://Example.com/path")

    assert "#topic" in terms
    assert "@handle" in terms
    assert "example.com" in terms


def test_parse_cursor_datetime_accepts_iso_text():
    parsed = parse_cursor_datetime("2026-08-09T14:53:24+00:00")

    assert parsed.year == 2026
    assert parsed.tzinfo is not None
