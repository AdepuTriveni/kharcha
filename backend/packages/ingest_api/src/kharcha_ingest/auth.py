"""Auth (PROJECT_SPEC §9, §28.2): Firebase ID tokens, plus static API keys from Phase 1.

A Bearer value that looks like a JWT is verified as a Firebase ID token (when a project id is
configured); the Firebase uid maps to ``users.firebase_uid`` and a new user row is created on
first sign-in. Anything else is treated as a ``khk_`` API key. The user id always comes from
the credential, never from the request body (CLAUDE.md rule 7).
"""

import hashlib
import hmac
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.db.models import User
from kharcha_common.settings import Settings
from kharcha_ingest.firebase import FirebaseVerifier, InvalidTokenError

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


def user_id_for_uid(uid: str) -> str:
    return "u_" + hashlib.sha256(f"firebase:{uid}".encode()).hexdigest()[:12]


async def user_for_firebase_uid(sessions: async_sessionmaker[AsyncSession], uid: str) -> str:
    async with sessions.begin() as session:
        existing = await session.scalar(select(User.id).where(User.firebase_uid == uid))
        if existing is not None:
            return existing
        user_id = user_id_for_uid(uid)
        await session.execute(
            insert(User).values(id=user_id, firebase_uid=uid).on_conflict_do_nothing()
        )
        return user_id


def _looks_like_jwt(value: str) -> bool:
    return value.count(".") == 2 and not value.startswith("khk_")


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    if credentials is None:
        raise _unauthorized("missing credentials")
    token = credentials.credentials
    verifier: FirebaseVerifier | None = getattr(request.app.state, "firebase", None)
    if verifier is not None and _looks_like_jwt(token):
        try:
            uid = await verifier.verify(token)
        except InvalidTokenError as exc:
            raise _unauthorized("invalid ID token") from exc
        return await user_for_firebase_uid(request.app.state.sessions, uid)
    settings: Settings = request.app.state.settings
    user_id = resolve_user(settings, token)
    if user_id is None:
        raise _unauthorized("invalid or missing API key")
    return user_id


CurrentUser = Annotated[str, Depends(current_user_id)]
