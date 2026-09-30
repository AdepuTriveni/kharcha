"""Telegram link codes (PROJECT_SPEC §9, §25): 6-digit, single use, 10 minutes, in Redis."""

import secrets

from redis.asyncio import Redis

LINK_TTL_S = 600
_PREFIX = "tglink:"


async def create_link_code(redis: Redis, user_id: str) -> str:
    for _ in range(10):
        code = f"{secrets.randbelow(1_000_000):06d}"
        if await redis.set(_PREFIX + code, user_id, ex=LINK_TTL_S, nx=True):
            return code
    raise RuntimeError("could not allocate a link code")


async def redeem_link_code(redis: Redis, code: str) -> str | None:
    """Return the user id for ``code`` and delete it (single use)."""
    if not (len(code) == 6 and code.isdigit()):
        return None
    value = await redis.getdel(_PREFIX + code)
    return str(value) if value is not None else None
