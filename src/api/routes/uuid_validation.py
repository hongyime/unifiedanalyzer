from uuid import UUID

from fastapi import HTTPException


def require_uuid(value: str, *, label: str = "Entity") -> str:
    try:
        return str(UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(status_code=404, detail=f"{label} not found")


def require_uuid_list(values: list[str], *, label: str = "Entity") -> list[str]:
    return [require_uuid(value, label=label) for value in values]
