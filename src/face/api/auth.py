"""Bearer-token auth guard for /api/v1 routes.

Single-user system — one static token configured via API_TOKEN env var.
Empty token disables auth entirely (dev/local convenience).
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

from src.face.config import settings

_bearer_scheme = HTTPBearer(auto_error=False)


async def require_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> None:
    token = settings.api_token
    if not token:
        return
    if credentials is None or credentials.credentials != token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
