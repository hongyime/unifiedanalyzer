from datetime import datetime, timedelta, timezone

from src.pipeline import timeline_builder


def _facebook_content_query():
    return next(
        spec for spec in timeline_builder.PLATFORM_QUERIES
        if spec["source"] == "facebook" and spec["event_type"] == "CONTENT_PUBLISHED"
    )


def test_facebook_content_timeline_query_registered():
    spec = _facebook_content_query()

    assert "FROM facebook_posts p" in spec["query"]
    assert "LEFT JOIN facebook_profiles pr" in spec["query"]
    assert "COALESCE(p.platform_created_at, p.collected_at)" in spec["time_col"]


def test_facebook_content_timeline_query_supports_incremental_filter():
    since = datetime(2026, 8, 21, 5, 0, tzinfo=timezone.utc)

    query, params = timeline_builder._format_platform_query(_facebook_content_query(), since)

    assert "AND COALESCE(p.platform_created_at, p.collected_at) > $1" in query
    assert params == [since.astimezone(timezone.utc).replace(tzinfo=None)]


def test_timeline_time_filter_rejects_months_future_source_clock():
    now = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)

    assert timeline_builder._valid_timeline_time(now + timedelta(hours=12), now=now) is True
    assert timeline_builder._valid_timeline_time(now + timedelta(days=2), now=now) is False
    assert timeline_builder._valid_timeline_time(datetime(2026, 12, 31, 17, 0, tzinfo=timezone.utc), now=now) is False
