"""Personal MCP tokens (PROJECT_SPEC §24): user-scoped, read-only, revocable, 90-day expiry.

The token is shown once; only its sha256 is stored.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import McpToken
from kharcha_common.ids import uuid7

PREFIX = "kmcp_"
LIFETIME = timedelta(days=90)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Issued:
    id: str
    token: str
    expires_at: datetime


async def issue(session: AsyncSession, user_id: str, label: str | None, now: datetime) -> Issued:
    token = PREFIX + secrets.token_urlsafe(32)
    row = McpToken(
        id="mt_" + uuid7().hex,
        user_id=user_id,
        token_hash=_hash(token),
        label=label,
        created_at=now,
        expires_at=now + LIFETIME,
    )
    session.add(row)
    await session.flush()
    return Issued(row.id, token, row.expires_at)


async def verify(session: AsyncSession, token: str, now: datetime) -> str | None:
    """User id for a valid, unrevoked, unexpired token."""
    if not token.startswith(PREFIX):
        return None
    row = (
        await session.execute(select(McpToken).where(McpToken.token_hash == _hash(token)))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    row.last_used_at = now
    return row.user_id


async def revoke(session: AsyncSession, user_id: str, token_id: str, now: datetime) -> bool:
    result = await session.execute(
        update(McpToken)
        .where(McpToken.id == token_id, McpToken.user_id == user_id, McpToken.revoked_at.is_(None))
        .values(revoked_at=now)
        .returning(McpToken.id)
    )
    return result.first() is not None


async def list_for(session: AsyncSession, user_id: str) -> list[McpToken]:
    return list(
        (
            await session.scalars(
                select(McpToken).where(McpToken.user_id == user_id).order_by(McpToken.created_at)
            )
        ).all()
    )
