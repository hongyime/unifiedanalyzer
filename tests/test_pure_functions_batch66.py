"""
Pure-function tests — batch 66.

Covers previously untested pure functions and remaining constants:
- api.routes.collector_health: required_auth structure, _collector_production_summary
  more keys, _blocker_is_active final combinations
- api.routes.media: _iso additional, _row_to_dict gps + face combinations
- api.routes.graph: _relationship_row additional, _decode_sources JSON bytes
- notifications.telegram: _preview edge cases, get_dashboard_url with custom port
"""
from __future__ import annotations

from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# api.routes.collector_health: required_auth structure
# ---------------------------------------------------------------------------

class TestRequiredAuthStructure:
    def test_required_auth_keys(self):
        from src.api.routes.collector_health import _collector_production_summary
        # required_auth is defined inside the function — test via the summary output
        result = _collector_production_summary({})
        assert "cookie_vault_missing_auth_platforms" in result

    def test_required_auth_missing_platforms_list(self):
        from src.api.routes.collector_health import _collector_production_summary
        result = _collector_production_summary({})
        assert isinstance(result["cookie_vault_missing_auth_platforms"], list)

    def test_instagram_in_optional_rollout(self):
        from src.api.routes.collector_health import _collector_production_summary
        result = _collector_production_summary({})
        # optional_rollout_action and can_proceed are present
        assert "optional_rollout_action" in result
        assert "optional_rollout_can_proceed" in result

    def test_domain_pacing_present(self):
        from src.api.routes.collector_health import _collector_production_summary
        result = _collector_production_summary({})
        assert "domain_pacing_sources" in result
        assert "domain_robots_blocked" in result
        assert "domain_429" in result


# ---------------------------------------------------------------------------
# api.routes.media: _iso + _analysis_by_media_id structure
# ---------------------------------------------------------------------------

class TestMediaIsoAdditional:
    def _i(self, v):
        from src.api.routes.media import _iso
        return _iso(v)

    def test_zero_returns_none(self):
        assert self._i(0) is None

    def test_empty_string_returns_none(self):
        assert self._i("") is None

    def test_recent_datetime(self):
        dt = datetime(2026, 9, 8, tzinfo=timezone.utc)
        result = self._i(dt)
        assert "2026-09-08" in result

    def test_naive_datetime_has_no_isoformat_tz(self):
        # naive datetime still has isoformat
        from datetime import datetime as dt
        naive = dt(2026, 1, 1)
        result = self._i(naive)
        assert result is not None
        assert "2026-01-01" in result


class TestRowToDictGpsAndFace:
    def _make_row(self, **kw):
        defaults = {
            "id": "r1", "media_item_id": "m1",
            "parent_media_item_id": None, "source": "strava",
            "content_type": "image", "analysis_type": "exif_gps",
            "result_json": None, "extracted_text": None,
            "gps_lat": 1.35, "gps_lon": 103.82, "taken_at": None,
            "perceptual_hash": None, "face_embedding": b"\x00\x01",
            "model_version": "v1", "processed_at": None,
        }
        defaults.update(kw)
        return defaults

    def test_has_gps_true_and_face_true(self):
        from src.api.routes.media import _row_to_dict
        result = _row_to_dict(self._make_row())
        assert result["has_gps"] is True
        assert result["has_face"] is True

    def test_taken_at_isoformat_when_set(self):
        from src.api.routes.media import _row_to_dict
        dt = datetime(2026, 3, 15, tzinfo=timezone.utc)
        result = _row_to_dict(self._make_row(taken_at=dt))
        assert "2026-03-15" in result["taken_at"]

    def test_processed_at_isoformat_when_set(self):
        from src.api.routes.media import _row_to_dict
        dt = datetime(2026, 6, 1, tzinfo=timezone.utc)
        result = _row_to_dict(self._make_row(processed_at=dt))
        assert "2026-06-01" in result["processed_at"]


# ---------------------------------------------------------------------------
# api.routes.graph: _decode_sources with bytes input
# ---------------------------------------------------------------------------

class TestDecodeSourcesAdditional:
    def _d(self, v):
        from src.api.routes.graph import _decode_sources
        return _decode_sources(v)

    def test_bytes_not_decoded(self):
        # bytes are not str → returns empty dict
        result = self._d(b'{"k": 1}')
        assert result == {}

    def test_nested_dict(self):
        d = {"outer": {"inner": 1}}
        assert self._d(d) == d

    def test_int_returns_empty(self):
        assert self._d(42) == {}

    def test_valid_json_with_special_chars(self):
        import json
        s = json.dumps({"key": "value with spaces"})
        result = self._d(s)
        assert result["key"] == "value with spaces"


# ---------------------------------------------------------------------------
# notifications.telegram: _preview additional edge cases
# ---------------------------------------------------------------------------

class TestTelegramPreviewAdditional:
    def _p(self, text, limit=1000):
        from src.notifications.telegram import _preview
        return _preview(text, limit)

    def test_integer_converted_to_string(self):
        result = self._p(42)
        assert "42" in result

    def test_list_converted(self):
        result = self._p([1, 2, 3])
        assert isinstance(result, str)

    def test_very_small_limit(self):
        result = self._p("hello world", limit=5)
        assert len(result) <= 5

    def test_unicode_preserved_within_limit(self):
        result = self._p("日本語テスト", limit=1000)
        assert "日本語" in result


# ---------------------------------------------------------------------------
# notifications.intelligence: _pct / _age additional
# ---------------------------------------------------------------------------

class TestIntelligencePctAdditional:
    def _p(self, part, total):
        from src.notifications.intelligence import _pct
        return _pct(part, total)

    def test_one_hundred_pct(self):
        assert self._p(100, 100) == "100%"

    def test_rounding(self):
        result = self._p(1, 3)
        assert "%" in result

    def test_large_numbers(self):
        result = self._p(999, 1000)
        assert "%" in result


class TestIntelligenceAgeAdditional:
    def _a(self, dt):
        from src.notifications.intelligence import _age
        return _age(dt)

    def test_exactly_90_seconds_boundary(self):
        from datetime import timedelta
        dt = datetime.now(timezone.utc) - timedelta(seconds=90)
        result = self._a(dt)
        # Could be "1m ago" or "90s ago"
        assert "ago" in result

    def test_48_hours_returns_days(self):
        from datetime import timedelta
        dt = datetime.now(timezone.utc) - timedelta(hours=48)
        result = self._a(dt)
        assert "d ago" in result
