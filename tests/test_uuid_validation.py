import pytest
from fastapi import HTTPException

from src.api.routes.uuid_validation import require_uuid, require_uuid_list


def test_require_uuid_normalizes_valid_uuid():
    value = "3BEA0C54-D6C4-459A-BE38-C61676DF8868"

    assert require_uuid(value) == "3bea0c54-d6c4-459a-be38-c61676df8868"


def test_require_uuid_rejects_invalid_uuid_as_not_found():
    with pytest.raises(HTTPException) as exc:
        require_uuid("not-a-uuid")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Entity not found"


def test_require_uuid_list_uses_custom_label():
    with pytest.raises(HTTPException) as exc:
        require_uuid_list(["3bea0c54-d6c4-459a-be38-c61676df8868", "bad"], label="Link")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Link not found"
