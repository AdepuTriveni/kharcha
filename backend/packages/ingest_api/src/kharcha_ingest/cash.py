"""Cash wallet API (PROJECT_SPEC §9, §13).

``POST /v1/cash`` publishes a ``cash-events`` message whose id is the client's ``entryId``,
so retries are idempotent. ``DELETE /v1/cash/{entryId}`` is the app's Undo; it accepts the
``entryId`` of a ``POST /v1/cash`` or the ``eventId`` of an uploaded MANUAL_TEXT/WIDGET_TAP.
"""

import logging
import uuid
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import AwareDatetime, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.cash import ledger_id_for
from kharcha_common.categories import Category
from kharcha_common.events import CamelModel, CashEntryType, CashEvent, CashEventPayload
from kharcha_common.kafka import EventPublisher
from kharcha_common.money import PositivePaise
from kharcha_common.time import utcnow
from kharcha_common.topics import Topic
from kharcha_common.wallet import cash_balance, delete_cash_entry, ledger_ids_for_entry
from kharcha_ingest.auth import CurrentUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")
PRODUCER = "ingest-api"
MAX_AGE = timedelta(days=30)
MAX_SKEW = timedelta(minutes=5)

# UNACCOUNTED is only written by the Cash Detective (§17.4).
UserEntryType = Literal["CASH_SPEND", "CASH_RECEIVED", "ATM_WITHDRAWAL"]


class CashEntryRequest(CamelModel):
    entry_id: str = Field(description="client-generated UUID (v4 or v7)")
    entry_type: UserEntryType
    amount_paise: PositivePaise = Field(le=10_000_000)  # ₹1,00,000 per entry
    category: Category | None = None
    note: str | None = Field(default=None, max_length=100)
    occurred_at: AwareDatetime

    @field_validator("entry_id", mode="before")
    @classmethod
    def _uuid_text(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("entryId must be a UUID string")
        return str(uuid.UUID(value))


class CashEntryAccepted(CamelModel):
    entry_id: str
    ledger_id: str


class CashBalanceOut(CamelModel):
    balance_paise: int = Field(description="may be negative; show `text` to the user")
    inflow_paise: int
    outflow_paise: int
    text: str


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    return sessions


@router.post("/cash", status_code=status.HTTP_202_ACCEPTED)
async def add_cash(
    body: CashEntryRequest, user_id: CurrentUser, request: Request
) -> CashEntryAccepted:
    now = utcnow()
    if not now - MAX_AGE <= body.occurred_at <= now + MAX_SKEW:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "occurredAt out of range")
    entry_id = body.entry_id
    publisher: EventPublisher = request.app.state.publisher
    event = CashEvent(
        event_id=entry_id,
        user_id=user_id,
        type="CASH_EVENT",
        occurred_at=body.occurred_at,
        producer=PRODUCER,
        payload=CashEventPayload(
            entry_type=CashEntryType(body.entry_type),
            amount_paise=body.amount_paise,
            category=body.category.value if body.category else None,
            note=body.note,
        ),
    )
    try:
        await publisher.publish_event(Topic.CASH_EVENTS, event)
    except Exception as exc:
        log.exception("publish to cash-events failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "retry later") from exc
    return CashEntryAccepted(entry_id=entry_id, ledger_id=ledger_id_for(entry_id))


@router.delete("/cash/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def undo_cash(entry_id: str, user_id: CurrentUser, request: Request) -> Response:
    async with _sessions(request).begin() as session:
        for ledger_id in [entry_id, *ledger_ids_for_entry(entry_id)]:
            if await delete_cash_entry(session, user_id, ledger_id):
                return Response(status_code=status.HTTP_204_NO_CONTENT)
    # Not written yet (still in Kafka) or not this user's: the app retries a few times.
    raise HTTPException(status.HTTP_404_NOT_FOUND, "no such cash entry")


@router.get("/cash/balance")
async def get_cash_balance(user_id: CurrentUser, request: Request) -> CashBalanceOut:
    async with _sessions(request)() as session:
        balance = await cash_balance(session, user_id)
    return CashBalanceOut(
        balance_paise=balance.balance_paise,
        inflow_paise=balance.inflow_paise,
        outflow_paise=balance.outflow_paise,
        text=balance.describe(),
    )
