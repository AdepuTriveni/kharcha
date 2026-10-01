"""``/v1/mcp-tokens`` (PROJECT_SPEC §9, §24): personal tokens for the user's own MCP clients."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common import mcp_tokens
from kharcha_common.events import CamelModel
from kharcha_common.time import utcnow
from kharcha_ingest.auth import CurrentUser

router = APIRouter(prefix="/v1")


class NewToken(CamelModel):
    label: str | None = Field(default=None, max_length=60)


class IssuedToken(CamelModel):
    id: str
    token: str = Field(description="shown once")
    expires_at: datetime


class TokenInfo(CamelModel):
    id: str
    label: str | None
    created_at: datetime
    expires_at: datetime
    revoked: bool
    last_used_at: datetime | None


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    return sessions


@router.post("/mcp-tokens", status_code=status.HTTP_201_CREATED)
async def create_token(body: NewToken, user_id: CurrentUser, request: Request) -> IssuedToken:
    async with _sessions(request).begin() as session:
        issued = await mcp_tokens.issue(session, user_id, body.label, utcnow())
    return IssuedToken(id=issued.id, token=issued.token, expires_at=issued.expires_at)


@router.get("/mcp-tokens")
async def list_tokens(user_id: CurrentUser, request: Request) -> list[TokenInfo]:
    async with _sessions(request)() as session:
        rows = await mcp_tokens.list_for(session, user_id)
    return [
        TokenInfo(
            id=r.id,
            label=r.label,
            created_at=r.created_at,
            expires_at=r.expires_at,
            revoked=r.revoked_at is not None,
            last_used_at=r.last_used_at,
        )
        for r in rows
    ]


@router.delete("/mcp-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: str, user_id: CurrentUser, request: Request) -> Response:
    async with _sessions(request).begin() as session:
        if not await mcp_tokens.revoke(session, user_id, token_id, utcnow()):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such token")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
