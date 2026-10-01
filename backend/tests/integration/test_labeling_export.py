"""``kharcha-admin export-labeling`` against real Postgres (§15.2, rule 9: consent only)."""

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from kharcha_common.ids import uuid7
from kharcha_ingest import labeling
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
TXN_TEXT = "Rs.349.00 debited from A/c 1234567890123456 to zomato@hdfcbank. UPI Ref 412345678901"


@pytest.fixture
async def sessions(migrated_db: PgUrls) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_db.async_url)
    async with engine.begin() as conn:
        for table in (
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
        await conn.execute(
            sa.text("INSERT INTO users (id, ml_consent) VALUES ('u_yes', true), ('u_no', false)")
        )
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _raw(session: AsyncSession, user: str, text: str, raw_type: str = "RAW_SMS") -> str:
    raw_id = str(uuid7())
    await session.execute(
        sa.text(
            "INSERT INTO raw_events (event_id, user_id, type, sender, text, posted_at) "
            "VALUES (:id, :u, :t, 'AX-HDFCBK', :x, :p)"
        ),
        {"id": raw_id, "u": user, "t": raw_type, "x": text, "p": T0},
    )
    return raw_id


async def test_exports_only_consenting_users_with_proposed_labels(
    sessions: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    async with sessions.begin() as session:
        txn_raw = await _raw(session, "u_yes", TXN_TEXT)
        otp_raw = await _raw(session, "u_yes", "482913 is OTP for txn of Rs 10. Do not share")
        await _raw(session, "u_yes", "150 vada pav", raw_type="MANUAL_TEXT")
        await _raw(session, "u_no", TXN_TEXT)
        await session.execute(
            sa.text(
                "INSERT INTO transactions (id, user_id, amount_paise, direction, kind, status, "
                "channel, merchant_raw, category, is_essential, reference_id, account_hint, "
                "txn_time, parse_method) VALUES ('t_1', 'u_yes', 34900, 'DEBIT', 'SPEND', "
                "'SUCCESS', 'UPI', 'zomato@hdfcbank', 'FOOD_DELIVERY', false, '412345678901', "
                "'3456', :p, 'TEACHER_LLM')"
            ),
            {"p": T0},
        )
        await session.execute(
            sa.text("INSERT INTO transaction_sources VALUES ('t_1', :r)"), {"r": txn_raw}
        )

    async with sessions() as session:
        examples = await labeling.collect(session)
    out = tmp_path / "events.jsonl"
    assert labeling.write(out, examples) == 2

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    by_id = {line["eventId"]: line for line in lines}
    assert set(by_id) == {txn_raw, otp_raw}

    txn = by_id[txn_raw]
    assert "1234567890123456" not in txn["text"]
    assert "XXXXXXXXXXXX3456" in txn["text"]
    assert txn["label"] == {
        "isTransaction": True,
        "amount": "349.00",
        "direction": "DEBIT",
        "channel": "UPI",
        "status": "SUCCESS",
        "merchantRaw": "zomato@hdfcbank",
        "referenceId": "412345678901",
        "accountHint": "3456",
    }
    assert txn["source"] == "REAL"
    assert txn["reviewed"] is False
    assert "userId" not in txn
    assert by_id[otp_raw]["label"] == {"isTransaction": False}
