"""Compute, store and compare forecasts (PROJECT_SPEC §14, §22.1 BROKE_DATE_MOVED)."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import ForecastRow
from kharcha_common.ids import uuid7
from kharcha_insights.forecast.inputs import BuiltInput, build_input
from kharcha_insights.forecast.model import ForecastResult, forecast

RECOMPUTE_AFTER = timedelta(hours=1)
MOVED_EARLIER_DAYS = 3


@dataclass(frozen=True, slots=True)
class Stored:
    id: str
    result: ForecastResult
    built: BuiltInput


async def latest(session: AsyncSession, user_id: str) -> ForecastRow | None:
    return (
        await session.execute(
            select(ForecastRow)
            .where(ForecastRow.user_id == user_id)
            .order_by(ForecastRow.computed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def compute_and_store(session: AsyncSession, user_id: str, now: datetime) -> Stored | None:
    built = await build_input(session, user_id, now)
    if built.input is None:
        return None
    result = forecast(built.input)
    row_id = "f_" + uuid7().hex
    session.add(
        ForecastRow(
            id=row_id,
            user_id=user_id,
            computed_at=now,
            balance_now_paise=result.start_paise,
            broke_p20=result.broke_p20,
            broke_p50=result.broke_p50,
            broke_p80=result.broke_p80,
            horizon_days=result.horizon_days,
            inputs=result.inputs | {"probBroke": result.prob_broke},
        )
    )
    await session.flush()
    return Stored(row_id, result, built)


def moved_earlier(previous: date | None, new: date | None, horizon_end: date) -> int:
    """Days the p50 broke date moved earlier (0 if it did not)."""
    if new is None:
        return 0
    return max(0, ((previous or horizon_end) - new).days)


@dataclass(frozen=True, slots=True)
class Movement:
    previous_p50: date | None
    new_p50: date
    days_earlier: int
    stored: Stored


async def refresh_if_due(session: AsyncSession, user_id: str, now: datetime) -> Movement | None:
    """Recompute at most hourly; report when the p50 broke date moved >= 3 days earlier."""
    before = await latest(session, user_id)
    if before is not None and now - before.computed_at < RECOMPUTE_AFTER:
        return None
    stored = await compute_and_store(session, user_id, now)
    if stored is None or before is None or stored.result.broke_p50 is None:
        return None
    horizon_end = before.computed_at.date() + timedelta(days=before.horizon_days + 1)
    days = moved_earlier(before.broke_p50, stored.result.broke_p50, horizon_end)
    if days < MOVED_EARLIER_DAYS:
        return None
    return Movement(before.broke_p50, stored.result.broke_p50, days, stored)
