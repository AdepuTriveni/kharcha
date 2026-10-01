"""Temporary Coach for W3: ``agent-tasks`` -> facts -> short text -> policy gate -> Telegram.

Replaced by the agent runtime + Coach agent + ``agent-results`` in W8. It already follows the
rules that matter: numbers come from SQL facts (never from the model), the model text must pass
the grounding check (§27.4) or a deterministic template is used, and the policy gate decides.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Protocol

import litellm
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.categories import Category
from kharcha_common.db.models import AlertSent, Budget, ForecastRow, TransactionRow, User
from kharcha_common.events import AgentName, AgentTaskEvent, AgentTrigger, TxnKind, TxnStatus
from kharcha_common.grounding import allowed_values, check_grounding
from kharcha_common.money import format_inr
from kharcha_common.prompts import Prompt, load_prompt
from kharcha_common.settings import Settings
from kharcha_common.time import to_ist, utcnow
from kharcha_notifier import queries
from kharcha_notifier.bot import BotDeps
from kharcha_notifier.policy import AlertKind, Decision, RoastLevel, decide

log = logging.getLogger(__name__)
COACH_CONSUMER = "notifier.coach-v0"


@dataclass(frozen=True, slots=True)
class Facts:
    """Everything the message may say. Money in paise, formatted by :func:`format_inr`."""

    kind: AgentTrigger
    category: Category | None
    subject: str  # merchant or category label
    amount_paise: int  # 7-day total (FREQUENCY) or month-to-date (BUDGET)
    count: int = 0
    limit_paise: int | None = None
    percent: int | None = None

    def as_prompt(self) -> str:
        data: dict[str, object] = {"subject": self.subject, "amount": format_inr(self.amount_paise)}
        if self.kind is AgentTrigger.FREQUENCY:
            data |= {"payments_last_7_days": self.count}
        if self.limit_paise is not None:
            data |= {"monthly_budget": format_inr(self.limit_paise), "percent_used": self.percent}
        return json.dumps(data, ensure_ascii=False)

    def allowed(self) -> set[Decimal]:
        paise = [self.amount_paise] + ([self.limit_paise] if self.limit_paise else [])
        counts = [self.count] + ([self.percent] if self.percent is not None else [])
        return allowed_values(paise=paise, counts=counts)


class TextModel(Protocol):
    async def write(self, prompt: Prompt, level: RoastLevel, facts: Facts) -> str: ...


class LiteLLMTextModel:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def write(self, prompt: Prompt, level: RoastLevel, facts: Facts) -> str:
        is_ollama = self._settings.llm_model.startswith("ollama")
        user = prompt.section("user").replace("{level}", level.value)
        user = user.replace("{facts}", facts.as_prompt())
        response = await litellm.acompletion(
            model=self._settings.llm_model,
            messages=[
                {"role": "system", "content": prompt.section("system")},
                {"role": "user", "content": user},
            ],
            api_base=self._settings.ollama_api_base if is_ollama else None,
            temperature=float(prompt.meta.get("temperature", "0.7")),
            max_tokens=120,
            timeout=self._settings.llm_timeout_s,
        )
        return str(response.choices[0].message.content or "").strip()


def plain_text(facts: Facts) -> str:
    amount = format_inr(facts.amount_paise)
    if facts.kind is AgentTrigger.BROKE_DATE_MOVED:
        return (
            f"Heads up: at this pace your money runs out around {facts.subject} "
            f"(about {facts.count} days). You have {amount} now, cash included."
        )
    if facts.kind is AgentTrigger.FREQUENCY:
        return (
            f"{facts.subject}: {facts.count} payments in the last 7 days, {amount} in total. "
            "Skipping the next one keeps that money with you."
        )
    assert facts.limit_paise is not None
    return (
        f"{facts.subject}: {amount} spent this month, {facts.percent}% of your "
        f"{format_inr(facts.limit_paise)} budget. Slow down for the rest of the month."
    )


async def _facts(session: AsyncSession, event: AgentTaskEvent) -> Facts | None:
    task = event.payload
    refs = task.context_refs
    now = event.occurred_at
    if task.trigger is AgentTrigger.FREQUENCY and refs.get("merchant"):
        merchant = str(refs["merchant"])
        total, count = (
            await session.execute(
                select(func.coalesce(func.sum(TransactionRow.amount_paise), 0), func.count()).where(
                    TransactionRow.user_id == event.user_id,
                    TransactionRow.merchant_raw == merchant,
                    TransactionRow.kind == TxnKind.SPEND.value,
                    TransactionRow.status == TxnStatus.SUCCESS.value,
                    TransactionRow.txn_time >= now - timedelta(days=7),
                    TransactionRow.txn_time <= now,
                )
            )
        ).one()
        raw_category = refs.get("category")
        category = Category(raw_category) if raw_category else None
        return Facts(AgentTrigger.FREQUENCY, category, merchant, int(total), count=int(count))
    if task.trigger is AgentTrigger.BROKE_DATE_MOVED and refs.get("forecastId"):
        row = await session.get(ForecastRow, str(refs["forecastId"]))
        if row is None or row.user_id != event.user_id or row.broke_p50 is None:
            return None
        days = (row.broke_p50 - to_ist(now).date()).days
        if days < 0:
            return None
        return Facts(
            AgentTrigger.BROKE_DATE_MOVED,
            None,
            f"{row.broke_p50.day} {row.broke_p50:%b}",
            row.balance_now_paise,
            count=days,
        )
    if task.trigger is AgentTrigger.BUDGET and refs.get("category"):
        category = Category(str(refs["category"]))
        limit = (
            await session.execute(
                select(Budget.monthly_limit_paise).where(
                    Budget.user_id == event.user_id, Budget.category == category.value
                )
            )
        ).scalar_one_or_none()
        if not limit:
            return None
        month_start = to_ist(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        spent = (
            await session.execute(
                select(func.coalesce(func.sum(TransactionRow.amount_paise), 0)).where(
                    TransactionRow.user_id == event.user_id,
                    TransactionRow.category == category.value,
                    TransactionRow.kind == TxnKind.SPEND.value,
                    TransactionRow.status == TxnStatus.SUCCESS.value,
                    TransactionRow.txn_time >= month_start,
                )
            )
        ).scalar_one()
        percent = int(spent) * 100 // int(limit)
        label = category.value.replace("_", " ").lower()
        return Facts(
            AgentTrigger.BUDGET,
            category,
            label,
            int(spent),
            limit_paise=int(limit),
            percent=percent,
        )
    return None


async def compose(model: TextModel | None, prompt: Prompt, level: RoastLevel, facts: Facts) -> str:
    """Model text if it passes grounding, else the deterministic template."""
    if model is None or level is RoastLevel.OFF:
        return plain_text(facts)
    try:
        text = await model.write(prompt, level, facts)
    except Exception:
        log.warning("coach model failed; using template", exc_info=False)
        return plain_text(facts)
    grounding = check_grounding(text, facts.allowed())
    if not text or not grounding.ok or len(text) > 400:
        log.info("coach text rejected by grounding", extra={"reason": "grounding"})
        return plain_text(facts)
    return text


async def handle_agent_task(
    body: bytes, deps: BotDeps, model: TextModel | None, now: datetime | None = None
) -> None:
    event = AgentTaskEvent.model_validate_json(body)
    task = event.payload
    if task.agent is not AgentName.COACH:
        return
    now = now or utcnow()
    async with deps.sessions() as session:
        user = (await session.execute(select(User).where(User.id == event.user_id))).scalar_one()
        facts = await _facts(session, event)
        counts = await queries.alert_counts(session, user.id, now)
    if facts is None or user.telegram_chat_id is None:
        return

    level = RoastLevel(user.roast_level)
    is_broke = facts.kind is AgentTrigger.BROKE_DATE_MOVED
    verdict = decide(
        kind=AlertKind.NUDGE if is_broke else AlertKind.ROAST,
        now=now,
        roast_level=level,
        quiet_start=user.quiet_start,
        quiet_end=user.quiet_end,
        counts=counts,
        category=facts.category,
    )
    alert_id = "a_" + uuid.uuid4().hex
    roast = verdict.decision is Decision.SEND and not is_broke
    kind = AlertKind.ROAST if roast else AlertKind.NUDGE
    text = ""
    if is_broke and verdict.decision is not Decision.SUPPRESS:
        text = plain_text(facts)  # dates and money are never left to the model
    elif verdict.decision is Decision.SEND:
        text = await compose(model, load_prompt("coach", "v0"), level, facts)
    elif verdict.decision is Decision.SEND_PLAIN:
        text = plain_text(facts)

    status = "QUEUED" if verdict.decision is not Decision.SUPPRESS else "SUPPRESSED"
    async with deps.sessions.begin() as session:
        inserted = (
            await session.execute(
                insert(AlertSent)
                .values(
                    id=alert_id,
                    user_id=user.id,
                    alert_type=kind.value,
                    text=text,
                    dedupe_key=task.dedupe_key,
                    status=status,
                )
                .on_conflict_do_nothing()
                .returning(AlertSent.id)
            )
        ).first()
    if inserted is None or status == "SUPPRESSED":
        return  # duplicate task, or the policy gate said no

    try:
        await deps.messenger.send_message(user.telegram_chat_id, text)
        final = {"status": "SENT", "sent_at": utcnow()}
    except Exception:
        log.exception("telegram send failed")
        final = {"status": "FAILED"}
    async with deps.sessions.begin() as session:
        await session.execute(update(AlertSent).where(AlertSent.id == alert_id).values(**final))
