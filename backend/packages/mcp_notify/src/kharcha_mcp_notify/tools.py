"""mcp-notify read tools (PROJECT_SPEC §17.3). ``propose_message`` lives in the runtime: it only
records a proposal, and nothing is sent until the notifier's policy gate approves it.
"""

from collections import Counter
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import AlertFeedback, AlertSent
from kharcha_common.time import to_ist
from kharcha_runtime.tools import Tool, tool
from kharcha_runtime.types import RunContext


class DaysArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int = Field(default=7, ge=1, le=60)


@tool("get_recent_alerts", "Messages already sent to the user recently (avoid repeats).", DaysArgs)
async def get_recent_alerts(
    session: AsyncSession, ctx: RunContext, args: DaysArgs
) -> dict[str, Any]:
    user_id, now = ctx.user_id, ctx.clock()
    rows = (
        await session.execute(
            select(AlertSent.alert_type, AlertSent.text, AlertSent.sent_at)
            .where(
                AlertSent.user_id == user_id,
                AlertSent.status == "SENT",
                AlertSent.sent_at >= now - timedelta(days=args.days),
            )
            .order_by(AlertSent.sent_at.desc())
            .limit(10)
        )
    ).all()
    return {
        "alerts": [
            {"type": t, "text": text, "date": to_ist(at).date().isoformat() if at else None}
            for t, text, at in rows
        ]
    }


@tool("get_feedback_stats", "How the user reacted to recent messages (FAIR, FUNNY, ...).", DaysArgs)
async def get_feedback_stats(
    session: AsyncSession, ctx: RunContext, args: DaysArgs
) -> dict[str, Any]:
    user_id, now = ctx.user_id, ctx.clock()
    rows = (
        await session.execute(
            select(AlertFeedback.reaction)
            .join(AlertSent, AlertSent.id == AlertFeedback.alert_id)
            .where(
                AlertSent.user_id == user_id,
                AlertFeedback.created_at >= now - timedelta(days=args.days),
            )
        )
    ).scalars()
    return {"reactions": dict(Counter(rows)), "days": args.days}


TOOLS: dict[str, Tool] = {t.spec.name: t for t in (get_recent_alerts, get_feedback_stats)}
