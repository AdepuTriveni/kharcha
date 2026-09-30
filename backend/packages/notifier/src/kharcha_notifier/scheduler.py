"""Daily summary at ``daily_summary_time`` IST for every linked user (PROJECT_SPEC §34 W3).

Exactly once per user per day: ``alerts_sent`` row with dedupe key ``daily:<date>`` is inserted
before sending, so restarts and several notifier replicas cannot double-send.
"""

import asyncio
import logging
import uuid
from datetime import datetime, time

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from kharcha_common.db.models import AlertSent, User
from kharcha_common.time import to_ist, utcnow
from kharcha_notifier import queries
from kharcha_notifier.bot import BotDeps
from kharcha_notifier.policy import AlertKind, Counts, Decision, RoastLevel, decide
from kharcha_notifier.texts import summary_text

log = logging.getLogger(__name__)


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


async def send_due_summaries(deps: BotDeps, at: time, now: datetime | None = None) -> int:
    """Send today's summary to users who have not had it yet. Returns how many were sent."""
    now = now or utcnow()
    local = to_ist(now)
    if local.time() < at:
        return 0
    day = local.date()
    async with deps.sessions() as session:
        users = (
            (await session.execute(select(User).where(User.telegram_chat_id.is_not(None))))
            .scalars()
            .all()
        )
    sent = 0
    for user in users:
        verdict = decide(
            kind=AlertKind.SUMMARY,
            now=now,
            roast_level=RoastLevel(user.roast_level),
            quiet_start=user.quiet_start,
            quiet_end=user.quiet_end,
            counts=Counts(),
        )
        if verdict.decision is Decision.SUPPRESS:
            continue
        async with deps.sessions.begin() as session:
            summary = await queries.day_summary(session, user.id, day)
            text = summary_text(summary)
            alert_id = "a_" + uuid.uuid4().hex
            claimed = (
                await session.execute(
                    insert(AlertSent)
                    .values(
                        id=alert_id,
                        user_id=user.id,
                        alert_type=AlertKind.SUMMARY.value,
                        text=text,
                        dedupe_key=f"daily:{day.isoformat()}",
                        status="QUEUED",
                    )
                    .on_conflict_do_nothing()
                    .returning(AlertSent.id)
                )
            ).first()
        if claimed is None or user.telegram_chat_id is None:
            continue
        try:
            await deps.messenger.send_message(user.telegram_chat_id, text)
            values: dict[str, object] = {"status": "SENT", "sent_at": utcnow()}
            sent += 1
        except Exception:
            log.exception("daily summary send failed", extra={"user": user.id})
            values = {"status": "FAILED"}
        async with deps.sessions.begin() as session:
            await session.execute(
                update(AlertSent).where(AlertSent.id == alert_id).values(**values)
            )
    return sent


async def run_daily_summaries(deps: BotDeps, at: time, interval_s: float = 60.0) -> None:
    while True:
        try:
            await send_due_summaries(deps, at)
        except Exception:
            log.exception("daily summary loop error")
        await asyncio.sleep(interval_s)
