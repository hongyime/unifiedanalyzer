"""
QA-lane tests for src/api/routes/uuid_validation.py: require_uuid, require_uuid_list.

These functions are used by every route handler — critical to cover.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes.uuid_validation import require_uuid, require_uuid_list

_VALID = "00000000-0000-0000-0000-000000000001"
_VALID2 = "00000000-0000-0000-0000-000000000002"


class TestRequireUuid:
    def test_valid_uuid_returned_normalized(self):
        result = require_uuid(_VALID)
        assert result == _VALID

    def test_normalizes_uppercase(self):
        upper = _VALID.upper()
        result = require_uuid(upper)
        assert result == _VALID

    def test_compact_uuid_normalized(self):
        compact = "00000000000000000000000000000001"
        result = require_uuid(compact)
        assert "-" in result

    def test_invalid_uuid_raises_404(self):
        with pytest.raises(HTTPException) as exc:
            require_uuid("not-a-uuid")
        assert exc.value.status_code == 404

    def test_empty_string_raises_404(self):
        with pytest.raises(HTTPException) as exc:
            require_uuid("")
        assert exc.value.status_code == 404

    def test_none_raises_404(self):
        with pytest.raises(HTTPException):
            require_uuid(None)

    def test_custom_label_in_detail(self):
        with pytest.raises(HTTPException) as exc:
            require_uuid("bad", label="Case")
        assert "Case" in exc.value.detail


class TestRequireUuidList:
    def test_valid_list_returned(self):
        result = require_uuid_list([_VALID, _VALID2])
        assert result == [_VALID, _VALID2]

    def test_empty_list_returned_empty(self):
        assert require_uuid_list([]) == []

    def test_invalid_entry_raises_404(self):
        with pytest.raises(HTTPException):
            require_uuid_list([_VALID, "bad-uuid"])

    def test_all_normalized(self):
        result = require_uuid_list([_VALID.upper()])
        assert result == [_VALID]
