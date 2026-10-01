"""Dedup + merchants against real Postgres (PROJECT_SPEC §11): merges, splits, special cases."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kharcha_common.events import (
    Channel,
    Direction,
    ParsedTransactionPayload,
    ParseMethod,
    TxnStatus,
)
from kharcha_common.ids import uuid7
from kharcha_common.merchants import seed_merchants
from kharcha_common.transactions import clean_payload
from kharcha_processor.dedup import apply_parsed
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

USER = "u_dedup"
T0 = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)


@pytest.fixture
async def sessions(migrated_db: PgUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_db.async_url)
    async with engine.begin() as conn:
        for table in (
            "agent_runs",
            "alerts_sent",
            "cash_ledger",
            "transaction_sources",
            "transactions",
            "processed_events",
            "raw_events",
            "budgets",
            "users",
        ):
            await conn.execute(sa.text(f"DELETE FROM {table}"))
        await conn.execute(sa.text("INSERT INTO users (id) VALUES (:u)"), {"u": USER})
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker.begin() as session:
        await seed_merchants(session)
    yield maker
    await engine.dispose()


async def _ingest(
    sessions: async_sessionmaker[AsyncSession],
    *,
    raw_type: str = "RAW_NOTIFICATION",
    app: str | None = "com.phonepe.app",
    at: datetime = T0,
    amount: int = 34900,
    direction: Direction = Direction.DEBIT,
    merchant: str | None = "zomato@hdfcbank",
    ref: str | None = None,
    acct: str | None = "1234",
    status: TxnStatus = TxnStatus.SUCCESS,
) -> list[tuple[str, str, int, str, str]]:
    raw_id = str(uuid7())
    async with sessions.begin() as session:
        await session.execute(
            sa.text(
                "INSERT INTO raw_events (event_id, user_id, type, source_app, posted_at) "
                "VALUES (:id, :u, :t, :a, :p)"
            ),
            {"id": raw_id, "u": USER, "t": raw_type, "a": app, "p": at},
        )
        rows = await apply_parsed(
            session,
            USER,
            ParsedTransactionPayload(
                raw_event_id=raw_id,
                amount_paise=amount,
                direction=direction,
                channel=Channel.UPI,
                status=status,
                merchant_raw=merchant,
                reference_id=ref,
                account_hint=acct,
                txn_time=at,
                parse_method=ParseMethod.TEACHER_LLM,
                confidence=0.9,
            ),
        )
        payloads = [await clean_payload(session, r) for r in rows]
    return [
        (p.transaction_id, p.kind.value, p.version, p.category, p.merchant_name or "")
        for p in payloads
    ]


async def _count(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as session:
        return int(
            (await session.execute(sa.text("SELECT count(*) FROM transactions"))).scalar_one()
        )


async def test_merchant_and_category_from_seeds(sessions: async_sessionmaker[AsyncSession]) -> None:
    [(_, kind, version, category, name)] = await _ingest(sessions)
    assert (kind, version, category, name) == ("SPEND", 1, "FOOD_DELIVERY", "Zomato")
    [(_, _, _, category, name)] = await _ingest(
        sessions, merchant="SWIGGY INSTAMART ORDER", at=T0 + timedelta(hours=1), amount=999
    )
    assert (category, name) == ("QUICK_COMMERCE_SNACKS", "Swiggy Instamart")
    [(_, _, _, category, name)] = await _ingest(
        sessions, merchant="p3fa2c1d0@okaxis", at=T0 + timedelta(hours=2), amount=500
    )
    assert (category, name) == ("TRANSFERS", "")


async def test_notification_and_sms_of_one_payment_merge(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    [(first_id, _, _, _, _)] = await _ingest(sessions)
    [(merged_id, _, version, _, _)] = await _ingest(
        sessions,
        raw_type="RAW_SMS",
        app=None,
        at=T0 + timedelta(minutes=1),
        merchant="ZOMATO",
        ref="412345678901",
    )
    assert merged_id == first_id
    assert version == 2
    assert await _count(sessions) == 1
    async with sessions() as session:
        ref: str = (
            await session.execute(sa.text("SELECT reference_id FROM transactions"))
        ).scalar_one()
    assert ref == "412345678901"  # richest fields kept


async def test_two_payments_from_same_app_stay_separate(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _ingest(sessions)
    await _ingest(sessions, at=T0 + timedelta(minutes=2))
    assert await _count(sessions) == 2


async def test_same_reference_merges_even_hours_apart(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _ingest(sessions, ref="499999999999", status=TxnStatus.PENDING)
    [(_, _, version, _, _)] = await _ingest(
        sessions, raw_type="RAW_SMS", app=None, at=T0 + timedelta(hours=20), ref="499999999999"
    )
    assert version == 2
    async with sessions() as session:
        status: str = (
            await session.execute(sa.text("SELECT status FROM transactions"))
        ).scalar_one()
    assert status == "SUCCESS"  # pending upgraded


async def test_far_apart_without_reference_stay_separate(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _ingest(sessions)
    await _ingest(sessions, raw_type="RAW_SMS", app=None, at=T0 + timedelta(minutes=20))
    assert await _count(sessions) == 2


async def test_self_transfer_marks_both_sides(sessions: async_sessionmaker[AsyncSession]) -> None:
    [(debit_id, _, _, _, _)] = await _ingest(sessions, amount=500000, merchant=None, acct="1234")
    rows = await _ingest(
        sessions,
        amount=500000,
        direction=Direction.CREDIT,
        merchant=None,
        acct="9876",
        app="com.snapwork.hdfc",
        at=T0 + timedelta(minutes=4),
    )
    kinds = {txn_id: (kind, version, category) for txn_id, kind, version, category, _ in rows}
    assert kinds[debit_id] == ("SELF_TRANSFER", 2, "TRANSFERS")
    assert {v[0] for v in kinds.values()} == {"SELF_TRANSFER"}


async def test_reversal_and_refund(sessions: async_sessionmaker[AsyncSession]) -> None:
    await _ingest(sessions, ref="412300000001", status=TxnStatus.FAILED)
    [(_, kind, _, _, _)] = await _ingest(
        sessions, direction=Direction.CREDIT, ref="412300000001", at=T0 + timedelta(days=2)
    )
    assert kind == "REVERSAL"
    await _ingest(sessions, amount=120000, merchant="AMAZON", at=T0 + timedelta(hours=1))
    [(_, kind, _, _, _)] = await _ingest(
        sessions,
        amount=120000,
        direction=Direction.CREDIT,
        merchant="Amazon Pay India",
        at=T0 + timedelta(days=5),
    )
    assert kind == "REFUND"


async def test_reprocessing_same_raw_event_is_idempotent(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    raw_id = str(uuid7())
    async with sessions.begin() as session:
        await session.execute(
            sa.text(
                "INSERT INTO raw_events (event_id, user_id, type, source_app, posted_at) "
                "VALUES (:id, :u, 'RAW_NOTIFICATION', 'com.phonepe.app', :p)"
            ),
            {"id": raw_id, "u": USER, "p": T0},
        )
    parsed = ParsedTransactionPayload(
        raw_event_id=raw_id,
        amount_paise=1000,
        direction=Direction.DEBIT,
        channel=Channel.UPI,
        status=TxnStatus.SUCCESS,
        txn_time=T0,
        parse_method=ParseMethod.TEACHER_LLM,
        confidence=0.9,
    )
    for _ in range(2):
        async with sessions.begin() as session:
            [row] = await apply_parsed(session, USER, parsed)
            assert row.version == 1
    assert await _count(sessions) == 1
