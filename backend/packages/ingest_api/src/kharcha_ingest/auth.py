"""Phase 1 auth: static per-user API keys (PROJECT_SPEC §9). Firebase replaces this in W9.

The user id always comes from the credential, never from the request body (CLAUDE.md rule 7).
"""

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kharcha_common.settings import Settings

_bearer = HTTPBearer(auto_error=False)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> str:
    return "khk_" + secrets.token_urlsafe(32)


def resolve_user(settings: Settings, key: str) -> str | None:
    digest = hash_api_key(key)
    for stored, user_id in settings.api_keys.items():
        if hmac.compare_digest(stored, digest):
            return user_id
    return None


def current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    settings: Settings = request.app.state.settings
    user_id = resolve_user(settings, credentials.credentials) if credentials else None
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


CurrentUser = Annotated[str, Depends(current_user_id)]
