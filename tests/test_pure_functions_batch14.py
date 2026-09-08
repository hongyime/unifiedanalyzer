"""
Pure-function tests — batch 14.

Covers previously untested modules with no DB or I/O:
- pipeline.entity_resolver: normalize_username, normalize_username_strict, constants
- pipeline.location_inference: _decode_meta, _parse_strava_timezone, _parse_latlng,
  _latlng_to_region
- pipeline.timeline_builder: _valid_timeline_time
- pipeline.run_interaction_subset: _csv_set
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta


# ---------------------------------------------------------------------------
# entity_resolver: normalize_username, normalize_username_strict, constants
# ---------------------------------------------------------------------------

class TestNormalizeUsername:
    def _n(self, username):
        from src.pipeline.entity_resolver import normalize_username
        return normalize_username(username)

    def test_basic_username(self):
        assert self._n("alice") == "alice"

    def test_strips_dots_underscores_dashes(self):
        assert self._n("al.ic_e-x") == "alicex"

    def test_lowercases(self):
        assert self._n("Alice") == "alice"

    def test_strips_trailing_digits(self):
        assert self._n("alice123") == "alice"

    def test_generic_user_returns_none(self):
        assert self._n("user123") is None
        assert self._n("user") is None

    def test_contains_space_returns_none(self):
        assert self._n("alice bob") is None

    def test_none_returns_none(self):
        assert self._n(None) is None

    def test_empty_returns_none(self):
        assert self._n("") is None

    def test_too_short_after_strip_returns_none(self):
        # "a_b" → "ab" → length 2 < MIN_NORMALIZED_LENGTH=3
        assert self._n("a_b") is None

    def test_at_prefix_not_stripped(self):
        # @ is not in USERNAME_STRIP_CHARS — stays in result
        result = self._n("@alice")
        # After lowercase: "@alice", strip ._- → "@alice", strip trailing digits → "@alice"
        # len >= 3 and not DEFAULT_USERNAME_RE → valid
        assert result is not None


class TestNormalizeUsernameStrict:
    def _n(self, username):
        from src.pipeline.entity_resolver import normalize_username_strict
        return normalize_username_strict(username)

    def test_keeps_trailing_digits(self):
        # strict keeps digits: "alice123" → "alice123"
        assert self._n("alice123") == "alice123"

    def test_strips_punctuation(self):
        assert self._n("al.ic_e") == "alicex" if False else self._n("al.ic_e") == "alice"

    def test_lowercases(self):
        assert self._n("Alice") == "alice"

    def test_generic_user_returns_none(self):
        assert self._n("user1") is None

    def test_space_returns_none(self):
        assert self._n("alice bob") is None

    def test_none_returns_none(self):
        assert self._n(None) is None


class TestEntityResolverConstants:
    def test_common_username_accounts_positive(self):
        from src.pipeline.entity_resolver import COMMON_USERNAME_ACCOUNTS
        assert COMMON_USERNAME_ACCOUNTS > 0

    def test_confidence_threshold_in_range(self):
        from src.pipeline.entity_resolver import CONFIDENCE_THRESHOLD
        assert 0.0 < CONFIDENCE_THRESHOLD <= 1.0

    def test_name_fuzzy_min_score_positive(self):
        from src.pipeline.entity_resolver import NAME_FUZZY_MIN_SCORE
        assert NAME_FUZZY_MIN_SCORE > 0

    def test_min_name_tokens_positive(self):
        from src.pipeline.entity_resolver import MIN_NAME_TOKENS
        assert MIN_NAME_TOKENS >= 2


# ---------------------------------------------------------------------------
# location_inference: _decode_meta, _parse_strava_timezone, _parse_latlng,
#                     _latlng_to_region
# ---------------------------------------------------------------------------

class TestLocationDecodeMeta:
    def _d(self, raw):
        from src.pipeline.location_inference import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        assert self._d({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._d('{"a": 1}') == {"a": 1}

    def test_invalid_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


class TestParseStravaTimezone:
    def _p(self, s):
        from src.pipeline.location_inference import _parse_strava_timezone
        return _parse_strava_timezone(s)

    def test_standard_strava_format(self):
        assert self._p("(GMT+08:00) Asia/Singapore") == "Asia/Singapore"

    def test_utc_format(self):
        assert self._p("(GMT+00:00) UTC") == "UTC"

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_empty_returns_none(self):
        assert self._p("") is None

    def test_no_paren_returns_none(self):
        assert self._p("Asia/Singapore") is None

    def test_strips_whitespace(self):
        result = self._p("(GMT+08:00)  Asia/Singapore  ")
        assert result == "Asia/Singapore"


class TestParseLatlng:
    def _p(self, raw):
        from src.pipeline.location_inference import _parse_latlng
        return _parse_latlng(raw)

    def test_list_input(self):
        result = self._p([1.3, 103.8])
        assert result == (1.3, 103.8)

    def test_tuple_input(self):
        result = self._p((1.3, 103.8))
        assert result == (1.3, 103.8)

    def test_string_input(self):
        result = self._p("[1.3, 103.8]")
        assert result is not None
        assert abs(result[0] - 1.3) < 1e-9

    def test_string_without_brackets(self):
        result = self._p("1.3, 103.8")
        assert result is not None

    def test_empty_string_returns_none(self):
        assert self._p("") is None

    def test_null_string_returns_none(self):
        assert self._p("null") is None

    def test_empty_brackets_returns_none(self):
        assert self._p("[]") is None

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_invalid_list_returns_none(self):
        assert self._p(["bad", "values"]) is None

    def test_three_element_list_returns_none(self):
        assert self._p([1.0, 2.0, 3.0]) is None


class TestLatlngToRegion:
    def _r(self, lat, lng):
        from src.pipeline.location_inference import _latlng_to_region
        return _latlng_to_region(lat, lng)

    def test_singapore_sea(self):
        # Singapore: 1.3521, 103.8198
        assert self._r(1.35, 103.82) == "SEA"

    def test_london_europe(self):
        # London: 51.5, -0.12
        assert self._r(51.5, -0.12) == "Europe"

    def test_new_york_north_america(self):
        # New York: 40.7, -74.0
        assert self._r(40.7, -74.0) == "North America"

    def test_sydney_oceania(self):
        # Sydney: -33.87, 151.21
        assert self._r(-33.87, 151.21) == "Oceania"

    def test_middle_of_ocean_returns_none(self):
        # Mid-Pacific: 0, -150 — no region
        result = self._r(0.0, -150.0)
        assert result is None

    def test_none_lat_returns_none(self):
        assert self._r(None, 103.8) is None

    def test_none_lng_returns_none(self):
        assert self._r(1.3, None) is None


# ---------------------------------------------------------------------------
# timeline_builder: _valid_timeline_time
# ---------------------------------------------------------------------------

class TestValidTimelineTime:
    def _v(self, dt, now=None):
        from src.pipeline.timeline_builder import _valid_timeline_time
        return _valid_timeline_time(dt, now=now)

    def _now(self):
        return datetime(2026, 9, 8, tzinfo=timezone.utc)

    def test_valid_recent(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert self._v(dt, now=self._now()) is True

    def test_none_returns_false(self):
        assert self._v(None, now=self._now()) is False

    def test_before_min_date_returns_false(self):
        dt = datetime(2004, 12, 31, tzinfo=timezone.utc)
        assert self._v(dt, now=self._now()) is False

    def test_epoch_zero_returns_false(self):
        dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
        assert self._v(dt, now=self._now()) is False

    def test_future_beyond_tolerance_returns_false(self):
        # More than TIMELINE_MAX_FUTURE=1 day in future
        now = self._now()
        dt = now + timedelta(days=2)
        assert self._v(dt, now=now) is False

    def test_slightly_future_within_tolerance_valid(self):
        now = self._now()
        dt = now + timedelta(hours=12)  # < 1 day — within tolerance
        assert self._v(dt, now=now) is True

    def test_naive_datetime_treated_as_utc(self):
        # naive dt should be treated as UTC (gets tzinfo attached)
        dt = datetime(2026, 1, 1)  # naive
        assert self._v(dt, now=self._now()) is True

    def test_min_date_boundary_valid(self):
        from src.pipeline.timeline_builder import TIMELINE_MIN_DATE
        assert self._v(TIMELINE_MIN_DATE, now=self._now()) is True


# ---------------------------------------------------------------------------
# run_interaction_subset: _csv_set (same impl as run_timeline_subset)
# ---------------------------------------------------------------------------

class TestRunInteractionCsvSet:
    def _c(self, raw):
        from src.pipeline.run_interaction_subset import _csv_set
        return _csv_set(raw)

    def test_none_returns_none(self):
        assert self._c(None) is None

    def test_single_value(self):
        assert self._c("telegram") == {"telegram"}

    def test_multiple_values(self):
        assert self._c("telegram,instagram") == {"telegram", "instagram"}

    def test_strips_whitespace(self):
        assert self._c(" telegram , instagram ") == {"telegram", "instagram"}

    def test_empty_string_returns_none(self):
        assert self._c("") is None
