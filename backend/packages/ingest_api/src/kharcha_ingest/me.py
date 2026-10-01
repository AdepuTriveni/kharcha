"""User settings, consent and deletion (PROJECT_SPEC §9, §28.4-28.5).

``GET/PUT /v1/settings``: roast level, quiet hours, budgets, experiment opt-in and ML consent
(both explicit opt-ins, off by default). ``DELETE /v1/me``: removes the user's rows, unlinks
Telegram and drops pending link codes. Kafka data expires by topic retention (§7.2).
"""

import logging
from datetime import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import Field
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.categories import Category
from kharcha_common.db.models import Budget, User
from kharcha_common.events import CamelModel
from kharcha_ingest.auth import CurrentUser

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1")

RoastLevelName = Literal["OFF", "MILD", "MEDIUM", "SAVAGE"]
OPTED_IN = "OPTED_IN"  # experiment_group until the bandit assigns an arm (W19)


class BudgetItem(CamelModel):
    category: Category
    monthly_limit_paise: int = Field(gt=0, le=100_000_000)


class SettingsOut(CamelModel):
    user_id: str
    display_name: str | None
    roast_level: RoastLevelName
    quiet_start: time
    quiet_end: time
    ml_consent: bool
    experiment_opt_in: bool
    telegram_linked: bool
    budgets: list[BudgetItem]


class SettingsUpdate(CamelModel):
    """Only fields that are present change. ``budgets`` replaces the whole list."""

    display_name: str | None = Field(default=None, max_length=60)
    roast_level: RoastLevelName | None = None
    quiet_start: time | None = None
    quiet_end: time | None = None
    ml_consent: bool | None = None
    experiment_opt_in: bool | None = None
    budgets: list[BudgetItem] | None = Field(default=None, max_length=16)


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    return sessions


async def _load(session: AsyncSession, user_id: str) -> SettingsOut:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such user")
    budgets = (
        await session.execute(
            select(Budget.category, Budget.monthly_limit_paise)
            .where(Budget.user_id == user_id)
            .order_by(Budget.category)
        )
    ).all()
    return SettingsOut(
        user_id=user.id,
        display_name=user.display_name,
        roast_level=user.roast_level,  # type: ignore[arg-type]
        quiet_start=user.quiet_start,
        quiet_end=user.quiet_end,
        ml_consent=user.ml_consent,
        experiment_opt_in=user.experiment_group is not None,
        telegram_linked=user.telegram_chat_id is not None,
        budgets=[BudgetItem(category=Category(c), monthly_limit_paise=int(v)) for c, v in budgets],
    )


@router.get("/settings")
async def get_settings_(user_id: CurrentUser, request: Request) -> SettingsOut:
    async with _sessions(request)() as session:
        return await _load(session, user_id)


@router.put("/settings")
async def put_settings(body: SettingsUpdate, user_id: CurrentUser, request: Request) -> SettingsOut:
    changes: dict[str, object] = {}
    for name in ("display_name", "roast_level", "quiet_start", "quiet_end", "ml_consent"):
        value = getattr(body, name)
        if value is not None:
            changes[name] = value
    if body.experiment_opt_in is not None:
        changes["experiment_group"] = OPTED_IN if body.experiment_opt_in else None
    async with _sessions(request).begin() as session:
        if changes:
            await session.execute(update(User).where(User.id == user_id).values(**changes))
        if body.budgets is not None:
            await session.execute(delete(Budget).where(Budget.user_id == user_id))
            for item in body.budgets:
                session.add(
                    Budget(
                        user_id=user_id,
                        category=item.category.value,
                        monthly_limit_paise=item.monthly_limit_paise,
                    )
                )
        await session.flush()
        out = await _load(session, user_id)
    if "ml_consent" in changes:
        log.info("ml consent changed", extra={"consent": bool(changes["ml_consent"])})
    return out


# Children before parents. Every statement is scoped by the authenticated user id.
_DELETE_STATEMENTS = (
    "DELETE FROM alert_feedback WHERE alert_id IN (SELECT id FROM alerts_sent WHERE user_id = :u)",
    "DELETE FROM alerts_sent WHERE user_id = :u",
    "DELETE FROM nudge_decisions WHERE user_id = :u",
    "DELETE FROM risk_scores WHERE user_id = :u",
    "DELETE FROM memories WHERE user_id = :u",
    "DELETE FROM agent_runs WHERE user_id = :u",
    "DELETE FROM forecasts WHERE user_id = :u",
    "DELETE FROM refund_cases WHERE user_id = :u",
    "DELETE FROM cash_ledger WHERE user_id = :u",
    "DELETE FROM transaction_sources WHERE transaction_id IN "
    "(SELECT id FROM transactions WHERE user_id = :u)",
    "DELETE FROM transactions WHERE user_id = :u",
    "DELETE FROM user_merchant_overrides WHERE user_id = :u",
    "DELETE FROM mcp_tokens WHERE user_id = :u",
    "DELETE FROM budgets WHERE user_id = :u",
    "DELETE FROM devices WHERE user_id = :u",
    "DELETE FROM raw_events WHERE user_id = :u",
    "DELETE FROM users WHERE id = :u",
)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(user_id: CurrentUser, request: Request) -> Response:
    async with _sessions(request).begin() as session:
        for statement in _DELETE_STATEMENTS:
            await session.execute(text(statement), {"u": user_id})
    redis = request.app.state.redis
    async for key in redis.scan_iter(match="tglink:*"):
        if await redis.get(key) == user_id:
            await redis.delete(key)
    log.info("user deleted all data")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
