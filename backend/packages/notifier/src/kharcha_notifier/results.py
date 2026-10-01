"""``agent-results`` -> policy gate -> Telegram (PROJECT_SPEC §17.1, §22.2; rule 5).

The gate is the final authority. At most one message per result: the first proposal the gate
allows. A ROAST needs a plain "SEND"; when roasting is not allowed the templated plain nudge
(always appended by the orchestrator) is sent instead. ``alerts_sent.dedupe_key`` makes a
redelivered result a no-op.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from kharcha_common.categories import Category
from kharcha_common.db.models import AlertSent, User
from kharcha_common.events import AgentResultEvent, Proposal, ProposalType
from kharcha_common.idempotency import is_processed, mark_processed
from kharcha_common.time import utcnow
from kharcha_notifier import queries
from kharcha_notifier.bot import BotDeps
from kharcha_notifier.policy import AlertKind, Decision, RoastLevel, decide
from kharcha_notifier.telegram import Button

log = logging.getLogger(__name__)
RESULTS_CONSUMER = "notifier.results"


def _category(raw: str | None) -> Category | None:
    return Category(raw) if raw is not None and raw in Category.__members__ else None


def choose(
    proposals: list[Proposal], allow: dict[ProposalType, Decision]
) -> tuple[Proposal, AlertKind] | None:
    """First proposal the gate allows, given each type's verdict."""
    for proposal in proposals:
        verdict = allow.get(proposal.type, Decision.SUPPRESS)
        if proposal.type is ProposalType.ROAST:
            if verdict is Decision.SEND:
                return proposal, AlertKind.ROAST
            continue
        if verdict in (Decision.SEND, Decision.SEND_PLAIN):
            return proposal, AlertKind.NUDGE
    return None


async def handle_agent_result(body: bytes, deps: BotDeps, now: datetime | None = None) -> None:
    event = AgentResultEvent.model_validate_json(body)
    result = event.payload
    now = now or utcnow()
    async with deps.sessions() as session:
        if await is_processed(session, RESULTS_CONSUMER, event.event_id):
            return
        user = (
            await session.execute(select(User).where(User.id == event.user_id))
        ).scalar_one_or_none()
        counts = await queries.alert_counts(session, event.user_id, now)
    if user is None or user.telegram_chat_id is None or not result.proposals:
        async with deps.sessions.begin() as session:
            await mark_processed(session, RESULTS_CONSUMER, event.event_id)
        return

    level = RoastLevel(user.roast_level)
    allow: dict[ProposalType, Decision] = {}
    for proposal in result.proposals:
        kind = AlertKind.ROAST if proposal.type is ProposalType.ROAST else AlertKind.NUDGE
        allow.setdefault(
            proposal.type,
            decide(
                kind=kind,
                now=now,
                roast_level=level,
                quiet_start=user.quiet_start,
                quiet_end=user.quiet_end,
                counts=counts,
                category=_category(proposal.category),
            ).decision,
        )
    picked = choose(result.proposals, allow)

    alert_id = "a_" + uuid.uuid4().hex
    status = "QUEUED" if picked else "SUPPRESSED"
    async with deps.sessions.begin() as session:
        inserted = (
            await session.execute(
                insert(AlertSent)
                .values(
                    id=alert_id,
                    user_id=user.id,
                    alert_type=(picked[1] if picked else AlertKind.NUDGE).value,
                    text=picked[0].text if picked else "",
                    dedupe_key=result.dedupe_key,
                    agent_run_id=result.run_id,
                    status=status,
                )
                .on_conflict_do_nothing()
                .returning(AlertSent.id)
            )
        ).first()
        await mark_processed(session, RESULTS_CONSUMER, event.event_id)
    if inserted is None or picked is None:
        return  # duplicate alert, or the gate said no

    proposal = picked[0]
    buttons = (
        [[Button(label, f"ans:{alert_id}:{i}") for i, label in enumerate(proposal.buttons)]]
        if proposal.buttons
        else None
    )
    try:
        await deps.messenger.send_message(user.telegram_chat_id, proposal.text, buttons=buttons)
        final = {"status": "SENT", "sent_at": utcnow()}
    except Exception:
        log.exception("telegram send failed")
        final = {"status": "FAILED"}
    async with deps.sessions.begin() as session:
        await session.execute(update(AlertSent).where(AlertSent.id == alert_id).values(**final))
