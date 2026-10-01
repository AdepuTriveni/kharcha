"""Transactions API (PROJECT_SPEC §9, FR-12): list and user corrections.

A correction updates the row (``user_corrected = true``, ``version + 1``), republishes
``clean-transactions`` and stores a per-user override keyed by the normalized merchant text,
so the user's future payments to that merchant get the same merchant and category (§11.3:
user corrections win for that user). Global aliases are never changed by one user.
"""

import base64
import hashlib
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.categories import Category, is_essential
from kharcha_common.db.models import Merchant, MerchantAlias, TransactionRow, UserMerchantOverride
from kharcha_common.events import CamelModel, CleanTransactionEvent
from kharcha_common.kafka import EventPublisher, derived_event_id
from kharcha_common.merchants import normalize_merchant
from kharcha_common.time import utcnow
from kharcha_common.topics import Topic
from kharcha_common.transactions import clean_payload, merchant_name
from kharcha_ingest.auth import CurrentUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")
PRODUCER = "ingest-api"


class TransactionOut(CamelModel):
    id: str
    amount_paise: int
    direction: str
    kind: str
    status: str
    channel: str
    merchant_id: str | None
    merchant_name: str | None
    merchant_raw: str | None
    category: str
    is_essential: bool
    txn_time: AwareDatetime
    user_corrected: bool
    version: int


class TransactionPage(CamelModel):
    items: list[TransactionOut]
    next_cursor: str | None


class Correction(CamelModel):
    category: Category | None = None
    merchant_name: str | None = Field(default=None, min_length=1, max_length=60)

    @model_validator(mode="after")
    def _something(self) -> "Correction":
        if self.category is None and self.merchant_name is None:
            raise ValueError("give category and/or merchantName")
        return self


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    return sessions


def encode_cursor(txn_time: datetime, txn_id: str) -> str:
    return base64.urlsafe_b64encode(f"{txn_time.isoformat()}|{txn_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        when, txn_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        parsed = datetime.fromisoformat(when)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad cursor") from exc
    if parsed.tzinfo is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "bad cursor")
    return parsed, txn_id


async def _out(session: AsyncSession, row: TransactionRow) -> TransactionOut:
    return TransactionOut(
        id=row.id,
        amount_paise=row.amount_paise,
        direction=row.direction,
        kind=row.kind,
        status=row.status,
        channel=row.channel,
        merchant_id=row.merchant_id,
        merchant_name=await merchant_name(session, row.merchant_id),
        merchant_raw=row.merchant_raw,
        category=row.category,
        is_essential=row.is_essential,
        txn_time=row.txn_time,
        user_corrected=row.user_corrected,
        version=row.version,
    )


@router.get("/transactions")
async def list_transactions(
    user_id: CurrentUser,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> TransactionPage:
    stmt = select(TransactionRow).where(TransactionRow.user_id == user_id)
    if cursor is not None:
        when, txn_id = decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                TransactionRow.txn_time < when,
                and_(TransactionRow.txn_time == when, TransactionRow.id < txn_id),
            )
        )
    stmt = stmt.order_by(TransactionRow.txn_time.desc(), TransactionRow.id.desc()).limit(limit + 1)
    async with _sessions(request)() as session:
        rows = list((await session.scalars(stmt)).all())
        items = [await _out(session, row) for row in rows[:limit]]
    next_cursor = (
        encode_cursor(rows[limit - 1].txn_time, rows[limit - 1].id) if len(rows) > limit else None
    )
    return TransactionPage(items=items, next_cursor=next_cursor)


async def _merchant_for_name(session: AsyncSession, name: str, category: str) -> Merchant:
    """Existing merchant for this name, else a new one only this user's override points to."""
    key = normalize_merchant(name)
    if key:
        existing = (
            await session.execute(
                select(Merchant)
                .join(MerchantAlias, MerchantAlias.merchant_id == Merchant.id)
                .where(MerchantAlias.alias == key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
    merchant_id = "um_" + hashlib.sha256(name.strip().lower().encode()).hexdigest()[:12]
    await session.execute(
        insert(Merchant)
        .values(id=merchant_id, name=name.strip(), default_category=category)
        .on_conflict_do_nothing()
    )
    merchant = await session.get(Merchant, merchant_id)
    assert merchant is not None
    return merchant


@router.patch("/transactions/{txn_id}")
async def correct_transaction(
    txn_id: str, body: Correction, user_id: CurrentUser, request: Request
) -> TransactionOut:
    publisher: EventPublisher = request.app.state.publisher
    async with _sessions(request).begin() as session:
        row = (
            await session.execute(
                select(TransactionRow)
                .where(TransactionRow.id == txn_id, TransactionRow.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such transaction")

        category = body.category.value if body.category else row.category
        if body.merchant_name is not None:
            merchant = await _merchant_for_name(session, body.merchant_name, category)
            row.merchant_id = merchant.id
            if body.category is None:
                category = merchant.default_category
        row.category = category
        row.is_essential = is_essential(Category(category))
        row.user_corrected = True
        row.version += 1
        row.updated_at = utcnow()

        alias = normalize_merchant(row.merchant_raw) if row.merchant_raw else ""
        if alias:
            values = {"merchant_id": row.merchant_id, "category": category, "updated_at": utcnow()}
            await session.execute(
                insert(UserMerchantOverride)
                .values(user_id=user_id, alias=alias, **values)
                .on_conflict_do_update(
                    index_elements=[UserMerchantOverride.user_id, UserMerchantOverride.alias],
                    set_=values,
                )
            )
        await session.flush()
        payload = await clean_payload(session, row)
        out = await _out(session, row)

    try:
        await publisher.publish_event(
            Topic.CLEAN_TRANSACTIONS,
            CleanTransactionEvent(
                event_id=derived_event_id("clean", payload.transaction_id, str(payload.version)),
                user_id=user_id,
                type="CLEAN_TRANSACTION",
                occurred_at=utcnow(),
                producer=PRODUCER,
                payload=payload,
            ),
        )
    except Exception:  # the row is corrected; consumers catch up on the next version
        log.exception("publish of corrected transaction failed")
    log.info("transaction corrected", extra={"transaction_id": txn_id})
    return out
