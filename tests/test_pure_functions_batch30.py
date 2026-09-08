"""
Pure-function tests — batch 30.

Covers previously untested pure functions:
- pipeline.entity_resolver: name_is_distinctive, name_block_keys,
  parse_whatsapp_phone
- pipeline.media_analysis: _dms_to_decimal
- pipeline.incremental_runner: _PHASE_RESOURCE_CLASSES constant,
  _PROCESSED_KEYS / _ATTRIBUTED_KEYS / _SKIPPED_KEYS / _ERROR_KEYS constants
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# entity_resolver: name_is_distinctive, name_block_keys, parse_whatsapp_phone
# ---------------------------------------------------------------------------

class TestNameIsDistinctive:
    def _n(self, name):
        from src.pipeline.entity_resolver import name_is_distinctive
        return name_is_distinctive(name)

    def test_full_name_distinctive(self):
        assert self._n("Jane Halloran") is True

    def test_single_token_not_distinctive(self):
        assert self._n("Alice") is False

    def test_none_not_distinctive(self):
        assert self._n(None) is False

    def test_empty_not_distinctive(self):
        assert self._n("") is False

    def test_too_short_not_distinctive(self):
        # "Al Bo" = 5 chars (MIN_NAME_LENGTH=5), but let's check boundary
        result = self._n("Al B")  # 4 chars < 5 → False
        assert result is False

    def test_three_tokens_distinctive(self):
        assert self._n("John Paul Smith") is True

    def test_whitespace_only_not_distinctive(self):
        assert self._n("   ") is False


class TestNameBlockKeys:
    def _k(self, name):
        from src.pipeline.entity_resolver import name_block_keys
        return name_block_keys(name)

    def test_first_3_chars_of_each_token(self):
        keys = self._k("Alice Smith")
        assert "ali" in keys
        assert "smi" in keys

    def test_short_token_excluded(self):
        # "Jo" has len 2 < 3 → excluded
        keys = self._k("Jo Smith")
        assert "jo" not in keys
        assert "smi" in keys

    def test_lowercased(self):
        keys = self._k("ALICE SMITH")
        assert "ali" in keys

    def test_empty_returns_empty(self):
        assert self._k("") == set()

    def test_single_long_token(self):
        keys = self._k("Alexandros")
        assert "ale" in keys

    def test_deduplicates(self):
        keys = self._k("Alice Alicia")
        assert keys == {"ali"}  # both truncate to "ali"


class TestParseWhatsappPhone:
    def _p(self, jid):
        from src.pipeline.entity_resolver import parse_whatsapp_phone
        return parse_whatsapp_phone(jid)

    def test_valid_jid(self):
        assert self._p("6591234567@s.whatsapp.net") == "6591234567"

    def test_lid_jid_returns_none(self):
        assert self._p("98765@lid") is None

    def test_status_jid_returns_none(self):
        assert self._p("status@broadcast") is None

    def test_none_returns_none(self):
        assert self._p(None) is None

    def test_empty_returns_none(self):
        assert self._p("") is None

    def test_non_numeric_phone_returns_none(self):
        assert self._p("abc@s.whatsapp.net") is None

    def test_too_short_phone_returns_none(self):
        # < 7 digits
        assert self._p("12345@s.whatsapp.net") is None


# ---------------------------------------------------------------------------
# media_analysis: _dms_to_decimal
# ---------------------------------------------------------------------------

class TestDmsToDecimal:
    def _d(self, dms, ref):
        from src.pipeline.media_analysis import _dms_to_decimal
        return _dms_to_decimal(dms, ref)

    def test_north_positive(self):
        result = self._d([1, 21, 9.0], "N")
        # 1 + 21/60 + 9/3600 ≈ 1.3525
        assert result is not None
        assert abs(result - (1 + 21/60 + 9/3600)) < 1e-6

    def test_south_negative(self):
        result = self._d([33, 52, 6.0], "S")
        assert result is not None
        assert result < 0

    def test_west_negative(self):
        result = self._d([118, 14, 37.0], "W")
        assert result is not None
        assert result < 0

    def test_east_positive(self):
        result = self._d([103, 49, 11.0], "E")
        assert result is not None
        assert result > 0

    def test_invalid_dms_returns_none(self):
        assert self._d("bad", "N") is None

    def test_none_dms_returns_none(self):
        assert self._d(None, "N") is None

    def test_zero_coords(self):
        result = self._d([0, 0, 0.0], "N")
        assert result == 0.0


# ---------------------------------------------------------------------------
# incremental_runner: _PHASE_RESOURCE_CLASSES, key tuple constants
# ---------------------------------------------------------------------------

class TestIncrementalRunnerConstants:
    def test_phase_resource_classes_non_empty(self):
        from src.pipeline.incremental_runner import _PHASE_RESOURCE_CLASSES
        assert len(_PHASE_RESOURCE_CLASSES) > 0

    def test_known_phases_present(self):
        from src.pipeline.incremental_runner import _PHASE_RESOURCE_CLASSES
        assert "resolve_entities" in _PHASE_RESOURCE_CLASSES
        assert "timeline" in _PHASE_RESOURCE_CLASSES
        assert "identity_scoring" in _PHASE_RESOURCE_CLASSES

    def test_all_values_strings(self):
        from src.pipeline.incremental_runner import _PHASE_RESOURCE_CLASSES
        assert all(isinstance(v, str) for v in _PHASE_RESOURCE_CLASSES.values())

    def test_processed_keys_non_empty(self):
        from src.pipeline.incremental_runner import _PROCESSED_KEYS
        assert len(_PROCESSED_KEYS) > 0
        assert "processed" in _PROCESSED_KEYS

    def test_attributed_keys_non_empty(self):
        from src.pipeline.incremental_runner import _ATTRIBUTED_KEYS
        assert len(_ATTRIBUTED_KEYS) > 0
        assert "attributed" in _ATTRIBUTED_KEYS

    def test_skipped_keys_non_empty(self):
        from src.pipeline.incremental_runner import _SKIPPED_KEYS
        assert len(_SKIPPED_KEYS) > 0
        assert "skipped_count" in _SKIPPED_KEYS

    def test_error_keys_non_empty(self):
        from src.pipeline.incremental_runner import _ERROR_KEYS
        assert len(_ERROR_KEYS) > 0
        assert "errors" in _ERROR_KEYS or "error_count" in _ERROR_KEYS

    def test_stale_heartbeat_minutes_positive(self):
        from src.pipeline.incremental_runner import _STALE_HEARTBEAT_MINUTES
        assert _STALE_HEARTBEAT_MINUTES > 0

    def test_heartbeat_interval_positive(self):
        from src.pipeline.incremental_runner import _HEARTBEAT_INTERVAL_SECONDS
        assert _HEARTBEAT_INTERVAL_SECONDS > 0
