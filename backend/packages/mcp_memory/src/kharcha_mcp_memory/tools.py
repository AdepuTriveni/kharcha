"""mcp-memory tools (PROJECT_SPEC §20). Every query is scoped to the token's user.

Write path: embed -> if an ACTIVE memory of the same kind is >= 0.9 similar, update it and raise
its confidence instead of inserting; ``replaces`` marks a contradicted memory FORGOTTEN; at most
50 ACTIVE memories per user, evicting the lowest confidence x recency.
Read path: score = similarity x recency decay x confidence; reading marks ``last_used_at``.
FORGOTTEN memories are never returned.
"""

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import Memory
from kharcha_common.ids import uuid7
from kharcha_mcp_memory.embed import Embedder, HashEmbedder, cosine
from kharcha_runtime.tools import Tool, tool
from kharcha_runtime.types import RunContext

Kind = Literal["FACT", "PREFERENCE", "COMMITMENT", "EPISODE"]
MERGE_SIMILARITY = 0.9
MAX_ACTIVE = 50
HALF_LIFE_DAYS = 60.0
CANDIDATES = 20

EMBEDDER: Embedder = HashEmbedder()  # replaced at server start from settings


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SearchArgs(_Args):
    query: str = Field(min_length=1, max_length=300)
    kinds: list[Kind] | None = None
    k: int = Field(default=5, ge=1, le=10)


class NoArgs(_Args):
    pass


class WriteArgs(_Args):
    kind: Kind
    content: str = Field(min_length=3, max_length=300)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    due_at: datetime | None = Field(default=None, alias="dueAt")
    replaces: str | None = Field(default=None, description="id of a memory this contradicts")


class UpdateArgs(_Args):
    id: str
    content: str | None = Field(default=None, min_length=3, max_length=300)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: Literal["ACTIVE", "FULFILLED", "BROKEN"] | None = None


class ForgetArgs(_Args):
    id: str


def recency(updated_at: datetime, now: datetime) -> float:
    age_days = max(0.0, (now - updated_at).total_seconds() / 86_400)
    return math.pow(0.5, age_days / HALF_LIFE_DAYS)


def _out(m: Memory, score: float | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": m.id,
        "kind": m.kind,
        "content": m.content,
        "confidence": round(m.confidence, 2),
        "status": m.status,
        "dueAt": m.due_at.isoformat() if m.due_at else None,
        "createdAt": m.created_at.isoformat() if m.created_at else None,
    }
    if score is not None:
        data["score"] = round(score, 3)
    return data


async def _get(session: AsyncSession, user_id: str, memory_id: str) -> Memory | None:
    row = await session.get(Memory, memory_id)
    return row if row is not None and row.user_id == user_id else None


@tool("search_memories", "Find the user's memories relevant to a query.", SearchArgs)
async def search_memories(
    session: AsyncSession, ctx: RunContext, args: SearchArgs
) -> dict[str, Any]:
    now = ctx.clock()
    query = await EMBEDDER.embed(args.query)
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == ctx.user_id, Memory.status == "ACTIVE", Memory.embedding.is_not(None)
        )
        .order_by(Memory.embedding.cosine_distance(query))
        .limit(CANDIDATES)
    )
    if args.kinds:
        stmt = stmt.where(Memory.kind.in_(args.kinds))
    rows = list((await session.scalars(stmt)).all())
    scored = sorted(
        (
            (cosine(query, list(m.embedding or [])) * recency(m.updated_at, now) * m.confidence, m)
            for m in rows
        ),
        key=lambda pair: -pair[0],
    )[: args.k]
    for _, m in scored:
        m.last_used_at = now
    await session.commit()
    return {"memories": [_out(m, score) for score, m in scored]}


@tool("list_commitments", "The user's active commitments with due dates.", NoArgs)
async def list_commitments(session: AsyncSession, ctx: RunContext, args: NoArgs) -> dict[str, Any]:
    rows = (
        await session.scalars(
            select(Memory)
            .where(
                Memory.user_id == ctx.user_id,
                Memory.kind == "COMMITMENT",
                Memory.status == "ACTIVE",
            )
            .order_by(Memory.due_at.asc().nulls_last(), Memory.created_at)
        )
    ).all()
    return {"commitments": [_out(m) for m in rows]}


async def _evict(session: AsyncSession, user_id: str, now: datetime) -> None:
    active = list(
        (
            await session.scalars(
                select(Memory).where(Memory.user_id == user_id, Memory.status == "ACTIVE")
            )
        ).all()
    )
    if len(active) <= MAX_ACTIVE:
        return
    active.sort(key=lambda m: m.confidence * recency(m.updated_at, now))
    for m in active[: len(active) - MAX_ACTIVE]:
        m.status = "FORGOTTEN"
        m.updated_at = now


@tool(
    "write_memory", "Remember a fact, preference, commitment or episode about the user.", WriteArgs
)
async def write_memory(session: AsyncSession, ctx: RunContext, args: WriteArgs) -> dict[str, Any]:
    now = ctx.clock()
    vector = await EMBEDDER.embed(args.content)
    if args.replaces and (old := await _get(session, ctx.user_id, args.replaces)) is not None:
        old.status = "FORGOTTEN"
        old.updated_at = now
    nearest = (
        await session.scalars(
            select(Memory)
            .where(
                Memory.user_id == ctx.user_id,
                Memory.kind == args.kind,
                Memory.status == "ACTIVE",
                Memory.embedding.is_not(None),
            )
            .order_by(Memory.embedding.cosine_distance(vector))
            .limit(1)
        )
    ).first()
    if nearest is not None and cosine(vector, list(nearest.embedding or [])) >= MERGE_SIMILARITY:
        nearest.confidence = min(1.0, max(nearest.confidence, args.confidence) + 0.1)
        nearest.updated_at = now
        if args.due_at is not None:
            nearest.due_at = args.due_at
        await session.commit()
        return {"id": nearest.id, "action": "updated", "confidence": round(nearest.confidence, 2)}
    memory = Memory(
        id="mem_" + uuid7().hex,
        user_id=ctx.user_id,
        kind=args.kind,
        content=args.content.strip(),
        embedding=vector,
        confidence=args.confidence,
        source_run_id=ctx.task_id,
        due_at=args.due_at,
        status="ACTIVE",
        created_at=now,
        updated_at=now,
    )
    session.add(memory)
    await session.flush()
    await _evict(session, ctx.user_id, now)
    await session.commit()
    return {"id": memory.id, "action": "inserted"}


@tool("update_memory", "Change a memory's text, confidence or commitment status.", UpdateArgs)
async def update_memory(session: AsyncSession, ctx: RunContext, args: UpdateArgs) -> dict[str, Any]:
    memory = await _get(session, ctx.user_id, args.id)
    if memory is None or memory.status == "FORGOTTEN":
        return {"error": "NOT_FOUND", "message": "no such memory"}
    if args.content is not None:
        memory.content = args.content.strip()
        memory.embedding = await EMBEDDER.embed(memory.content)
    if args.confidence is not None:
        memory.confidence = args.confidence
    if args.status is not None:
        memory.status = args.status
    memory.updated_at = ctx.clock()
    await session.commit()
    return {"id": memory.id, "action": "updated", "status": memory.status}


@tool("forget_memory", "Forget a memory (it will never be returned again).", ForgetArgs)
async def forget_memory(session: AsyncSession, ctx: RunContext, args: ForgetArgs) -> dict[str, Any]:
    memory = await _get(session, ctx.user_id, args.id)
    if memory is None:
        return {"error": "NOT_FOUND", "message": "no such memory"}
    memory.status = "FORGOTTEN"
    memory.updated_at = ctx.clock()
    await session.commit()
    return {"id": memory.id, "action": "forgotten"}


async def active_count(session: AsyncSession, user_id: str) -> int:
    return int(
        await session.scalar(
            select(func.count()).where(Memory.user_id == user_id, Memory.status == "ACTIVE")
        )
        or 0
    )


TOOLS: dict[str, Tool] = {
    t.spec.name: t
    for t in (search_memories, list_commitments, write_memory, update_memory, forget_memory)
}
