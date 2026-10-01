"""Tools for the user's own MCP clients (PROJECT_SPEC §24). Read-only; no SQL tools.

They reuse the internal handlers; accounts are never present in outputs (masked by design).
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.categories import Category
from kharcha_mcp_finance import tools as finance
from kharcha_mcp_refund.tools import list_refund_cases
from kharcha_runtime.tools import Tool, tool
from kharcha_runtime.types import RunContext


class SummaryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    week: Literal["this", "last"] = "this"


class ExternalListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=30, ge=1, le=90)
    category: Category | None = None
    limit: int = Field(default=50, ge=1, le=100)


@tool("get_spending_summary", "Your spending for this or last week (Mon-Sun, IST).", SummaryArgs)
async def get_spending_summary(
    session: AsyncSession, ctx: RunContext, args: SummaryArgs
) -> dict[str, Any]:
    return await finance.get_weekly_summary.handler(session, ctx, finance.WeekArgs(week=args.week))


@tool(
    "list_transactions",
    "Your recent payments, newest first (max 100, no account numbers).",
    ExternalListArgs,
)
async def list_transactions(
    session: AsyncSession, ctx: RunContext, args: ExternalListArgs
) -> dict[str, Any]:
    inner = finance.ListArgs.model_construct(
        days=args.days, merchant=None, category=args.category, limit=args.limit
    )
    return await finance.list_transactions.handler(session, ctx, inner)


@tool(
    "get_broke_date_forecast",
    "When your money runs out at this pace (p20/p50/p80, IST).",
    finance.NoArgs,
)
async def get_broke_date_forecast(
    session: AsyncSession, ctx: RunContext, args: finance.NoArgs
) -> dict[str, Any]:
    return await finance.get_forecast.handler(session, ctx, args)


TOOLS: dict[str, Tool] = {
    t.spec.name: t
    for t in (
        get_spending_summary,
        list_transactions,
        finance.get_cash_balance,
        get_broke_date_forecast,
        list_refund_cases,
    )
}
