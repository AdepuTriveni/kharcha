"""Consumer idempotency via ``processed_events`` (CLAUDE.md rule 3).

Call :func:`mark_processed` inside the same DB transaction as the consumer's side effects.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import ProcessedEvent


async def mark_processed(session: AsyncSession, consumer: str, event_id: str) -> bool:
    """Record ``event_id`` for ``consumer``. Returns False if it was already processed."""
    stmt = (
        insert(ProcessedEvent)
        .values(consumer=consumer, event_id=uuid.UUID(event_id))
        .on_conflict_do_nothing()
        .returning(ProcessedEvent.event_id)
    )
    return (await session.execute(stmt)).first() is not None


async def is_processed(session: AsyncSession, consumer: str, event_id: str) -> bool:
    stmt = select(ProcessedEvent.event_id).where(
        ProcessedEvent.consumer == consumer, ProcessedEvent.event_id == uuid.UUID(event_id)
    )
    return (await session.execute(stmt)).first() is not None
