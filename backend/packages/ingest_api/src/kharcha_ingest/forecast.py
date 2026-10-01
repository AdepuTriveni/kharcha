"""``GET /v1/forecast`` (PROJECT_SPEC §9, §14): broke-date range plus an optional what-if.

GET /v1/forecast                        -> p20/p50/p80, money now, chance of going broke
GET /v1/forecast?skip=FOOD_DELIVERY     -> also days gained by cutting that category
GET /v1/forecast?balancePaise=1250000   -> answer the one-time "what's your balance?" ask
"""

from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Query, Request
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.categories import Category
from kharcha_common.events import CamelModel
from kharcha_common.time import utcnow
from kharcha_ingest.auth import CurrentUser
from kharcha_insights.forecast.inputs import build_input
from kharcha_insights.forecast.model import forecast, what_if
from kharcha_insights.forecast.service import compute_and_store

router = APIRouter(prefix="/v1")


class CategorySpend(CamelModel):
    category: str
    daily_avg_paise: int


class WhatIfOut(CamelModel):
    category: str
    reduction_pct: int
    broke_p50: date | None
    days_gained: int | None


class ForecastOut(CamelModel):
    status: str = Field(description="OK or NEEDS_BALANCE")
    money_now_paise: int | None = None
    bank_paise: int | None = None
    cash_paise: int | None = None
    balance_fresh: bool | None = None
    broke_p20: date | None = None
    broke_p50: date | None = None
    broke_p80: date | None = None
    days_left_p50: int | None = None
    prob_broke: float | None = None
    horizon_days: int | None = None
    daily_spend_p50_paise: int | None = None
    top_categories: list[CategorySpend] = Field(default_factory=list)
    what_if: WhatIfOut | None = None


@router.get("/forecast")
async def get_forecast(
    user_id: CurrentUser,
    request: Request,
    skip: Category | None = None,
    reduction_pct: int = Query(default=100, ge=0, le=100, alias="reductionPct"),
    balance_paise: int | None = Query(default=None, ge=0, alias="balancePaise"),
) -> ForecastOut:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    now = utcnow()
    async with sessions.begin() as session:
        if balance_paise is None and skip is None:
            stored = await compute_and_store(session, user_id, now)
            built = stored.built if stored else await build_input(session, user_id, now)
        else:
            built = await build_input(session, user_id, now, bank_override_paise=balance_paise)
    if built.input is None or built.balance is None:
        return ForecastOut(status="NEEDS_BALANCE")

    inp = built.input
    result = forecast(inp)
    totals: dict[str, int] = defaultdict(int)
    for sample in inp.samples:
        for category, amount in sample.by_category.items():
            totals[category] += amount
    days = max(1, len(inp.samples))
    top = sorted(totals.items(), key=lambda kv: -kv[1])[:5]

    change = None
    if skip is not None:
        w = what_if(inp, skip.value, reduction_pct)
        change = WhatIfOut(
            category=skip.value,
            reduction_pct=reduction_pct,
            broke_p50=w.changed.broke_p50,
            days_gained=w.days_gained,
        )
    return ForecastOut(
        status="OK",
        money_now_paise=result.start_paise,
        bank_paise=inp.bank_paise,
        cash_paise=inp.cash_paise,
        balance_fresh=built.balance.fresh,
        broke_p20=result.broke_p20,
        broke_p50=result.broke_p50,
        broke_p80=result.broke_p80,
        days_left_p50=result.days_left_p50(inp.today),
        prob_broke=result.prob_broke,
        horizon_days=result.horizon_days,
        daily_spend_p50_paise=result.daily_spend_p50_paise,
        top_categories=[CategorySpend(category=c, daily_avg_paise=t // days) for c, t in top],
        what_if=change,
    )
