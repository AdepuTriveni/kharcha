"""Cash Detective tools (PROJECT_SPEC §17.4): one withdrawal, and the user's cash habits."""

import statistics
from collections import defaultdict
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import CashLedgerRow
from kharcha_common.events import CashEntryType
from kharcha_common.money import format_inr
from kharcha_runtime.tools import Tool, tool
from kharcha_runtime.types import RunContext

HABIT_DAYS = 60


class WithdrawalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    cash_ledger_id: str = Field(alias="cashLedgerId")


class HabitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=HABIT_DAYS, ge=7, le=120)


async def logged_since(session: AsyncSession, user_id: str, since: Any) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(CashLedgerRow.amount_paise), 0)).where(
            CashLedgerRow.user_id == user_id,
            CashLedgerRow.entry_type.in_(
                [CashEntryType.CASH_SPEND.value, CashEntryType.UNACCOUNTED.value]
            ),
            CashLedgerRow.occurred_at >= since,
        )
    )
    return int(total or 0)


@tool("get_withdrawal", "One ATM withdrawal and how much cash was logged since.", WithdrawalArgs)
async def get_withdrawal(
    session: AsyncSession, ctx: RunContext, args: WithdrawalArgs
) -> dict[str, Any]:
    row = await session.get(CashLedgerRow, args.cash_ledger_id)
    if row is None or row.user_id != ctx.user_id:
        return {"error": "NOT_FOUND", "message": "no such withdrawal"}
    logged = await logged_since(session, ctx.user_id, row.occurred_at)
    missing = max(0, row.amount_paise - logged)
    return {
        "cashLedgerId": row.id,
        "withdrawnPaise": row.amount_paise,
        "withdrawnText": format_inr(row.amount_paise),
        "withdrawnAt": row.occurred_at.isoformat(),
        "loggedSincePaise": logged,
        "unaccountedPaise": missing,
        "unaccountedText": format_inr(missing),
    }


@tool("get_cash_habits", "Typical cash spends per category over recent weeks.", HabitArgs)
async def get_cash_habits(
    session: AsyncSession, ctx: RunContext, args: HabitArgs
) -> dict[str, Any]:
    since = ctx.clock() - timedelta(days=args.days)
    rows = (
        await session.execute(
            select(CashLedgerRow.category, CashLedgerRow.note, CashLedgerRow.amount_paise).where(
                CashLedgerRow.user_id == ctx.user_id,
                CashLedgerRow.entry_type == CashEntryType.CASH_SPEND.value,
                CashLedgerRow.occurred_at >= since,
            )
        )
    ).all()
    by_category: dict[str, list[int]] = defaultdict(list)
    notes: dict[str, list[str]] = defaultdict(list)
    for category, note, amount in rows:
        key = category or "CASH_UNCATEGORIZED"
        by_category[key].append(int(amount))
        if note:
            notes[key].append(str(note))
    habits = [
        {
            "category": category,
            "count": len(amounts),
            "typicalPaise": int(statistics.median(amounts)),
            "totalPaise": sum(amounts),
            "examples": sorted(set(notes[category]))[:3],
        }
        for category, amounts in sorted(by_category.items(), key=lambda kv: -len(kv[1]))
    ]
    return {"days": args.days, "habits": habits[:6]}


TOOLS: dict[str, Tool] = {t.spec.name: t for t in (get_withdrawal, get_cash_habits)}
