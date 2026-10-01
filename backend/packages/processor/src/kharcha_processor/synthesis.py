"""Rule synthesis (PROJECT_SPEC §10.4).

When the teacher parses a message no rule covers, collect up to 5 recent teacher-parsed
messages of the same template **from the same user** (rule 7), ask the teacher for a regex,
validate it in code, and store it as a CANDIDATE. Candidates only run in shadow until they
earn ACTIVE (rules.next_status).
"""

import hashlib
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import litellm
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import RawEventRow, TransactionRow, TransactionSource
from kharcha_common.events import (
    Channel,
    Direction,
    ParseMethod,
    RawEvent,
    RawEventType,
    TxnStatus,
)
from kharcha_common.money import paise_to_rupees
from kharcha_common.prompts import Prompt, load_prompt
from kharcha_common.settings import Settings
from kharcha_common.templates import sender_key, template_signature
from kharcha_processor import metrics
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.rule_store import insert_candidate
from kharcha_processor.rules import UnsafeRuleError, apply_rule, compile_rule, same_extraction

log = logging.getLogger(__name__)

MAX_SAMPLES = 5
MIN_SAMPLES = 2
COOLDOWN_S = 6 * 3600
_SCAN = 50


@dataclass(frozen=True, slots=True)
class Sample:
    text: str
    extraction: ExtractionResult


class ProposedRule(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")

    regex: str = Field(max_length=2000)
    field_map: dict[str, str]


class RuleWriter(Protocol):
    async def write_rule(self, key: str, samples: Sequence[Sample]) -> ProposedRule: ...


def _fields(extraction: ExtractionResult) -> str:
    data = extraction.model_dump(
        by_alias=True,
        include={"amount", "merchant_raw", "counterparty_vpa", "reference_id", "account_hint"}
        | {"balance_after", "promised_refund_days"},
        exclude_none=True,
    )
    return json.dumps(data, ensure_ascii=False)


class TeacherRuleWriter:
    def __init__(self, settings: Settings, prompt: Prompt | None = None) -> None:
        self._settings = settings
        self._prompt = prompt or load_prompt("rules")

    async def write_rule(self, key: str, samples: Sequence[Sample]) -> ProposedRule:
        examples = "\n".join(
            f"<<<{s.text.replace('>>>', '> > >')}>>>\n{_fields(s.extraction)}" for s in samples
        )
        user = self._prompt.section("user").replace("{sender}", key).replace("{examples}", examples)
        is_ollama = self._settings.llm_model.startswith("ollama")
        response = await litellm.acompletion(
            model=self._settings.llm_model,
            messages=[
                {"role": "system", "content": self._prompt.section("system")},
                {"role": "user", "content": user},
            ],
            api_base=self._settings.ollama_api_base if is_ollama else None,
            temperature=0,
            response_format={"type": "json_object"},
            timeout=self._settings.llm_timeout_s,
        )
        return ProposedRule.model_validate_json(response.choices[0].message.content or "{}")


def rule_id_for(key: str, regex: str) -> str:
    return "r_syn_" + hashlib.sha256(f"{key}\n{regex}".encode()).hexdigest()[:12]


def validate_proposal(
    key: str, proposal: ProposedRule, samples: Sequence[Sample]
) -> tuple[str, str | None]:
    """(rule id, None) if the rule reproduces every sample, else (rule id, reason)."""
    rule_id = rule_id_for(key, proposal.regex)
    try:
        rule = compile_rule(rule_id, key, proposal.regex, dict(proposal.field_map), "CANDIDATE")
    except UnsafeRuleError as exc:
        return rule_id, str(exc)
    for sample in samples:
        extracted = apply_rule(rule, sample.text)
        if extracted is None:
            return rule_id, "no_match"
        if not same_extraction(extracted, sample.extraction):
            return rule_id, "fields_differ"
        if sample.extraction.channel is not Channel.UNKNOWN and (
            extracted.channel is not sample.extraction.channel
        ):
            return rule_id, "channel_differs"
    return rule_id, None


def _amount(paise: int | None) -> str | None:
    return None if paise is None else str(paise_to_rupees(paise))


def _from_row(txn: TransactionRow) -> ExtractionResult:
    return ExtractionResult(
        is_transaction=True,
        amount=_amount(txn.amount_paise),
        direction=Direction(txn.direction),
        channel=Channel(txn.channel),
        status=TxnStatus(txn.status),
        merchant_raw=txn.merchant_raw,
        reference_id=txn.reference_id,
        account_hint=txn.account_hint,
        balance_after=_amount(txn.balance_after_paise),
    )


async def recent_samples(
    session: AsyncSession, user_id: str, key: str, template: str, exclude: str, limit: int
) -> list[Sample]:
    """Teacher-parsed messages of one template, newest first, for one user only."""
    stmt = (
        select(RawEventRow, TransactionRow)
        .join(TransactionSource, TransactionSource.raw_event_id == RawEventRow.event_id)
        .join(TransactionRow, TransactionRow.id == TransactionSource.transaction_id)
        .where(
            RawEventRow.user_id == user_id,
            TransactionRow.user_id == user_id,
            RawEventRow.type.in_([RawEventType.RAW_NOTIFICATION, RawEventType.RAW_SMS]),
            RawEventRow.text.is_not(None),
            TransactionRow.parse_method == ParseMethod.TEACHER_LLM.value,
            or_(
                func.upper(RawEventRow.sender) == key,
                func.upper(RawEventRow.sender).like(f"%-{key}"),
                RawEventRow.source_app == key,
            ),
        )
        .order_by(RawEventRow.posted_at.desc())
        .limit(_SCAN)
    )
    out: list[Sample] = []
    for raw, txn in (await session.execute(stmt)).all():
        if str(raw.event_id) == exclude or raw.text is None:
            continue
        if template_signature(raw.text, raw.sender) != template:
            continue
        out.append(Sample(raw.text, _from_row(txn)))
        if len(out) >= limit:
            break
    return out


class Cooldown:
    """At most one synthesis attempt per template per process every ``seconds``."""

    def __init__(self, seconds: float = COOLDOWN_S) -> None:
        self._seconds = seconds
        self._last: dict[str, float] = {}

    def ready(self, template: str) -> bool:
        now = time.monotonic()
        last = self._last.get(template)
        if last is not None and now - last < self._seconds:
            return False
        self._last[template] = now
        return True


async def maybe_synthesize(
    session: AsyncSession,
    writer: RuleWriter,
    cooldown: Cooldown,
    event: RawEvent,
    extraction: ExtractionResult,
) -> str | None:
    """Try to add a CANDIDATE rule for ``event``'s template. Returns the new rule id."""
    text = event.payload.text
    key = sender_key(event.payload.sender, event.payload.source_app)
    if text is None or key is None:
        return None
    template = template_signature(text, event.payload.sender)
    samples = [Sample(text, extraction)] + await recent_samples(
        session, event.user_id, key, template, event.event_id, MAX_SAMPLES - 1
    )
    if len(samples) < MIN_SAMPLES:
        metrics.RULES_SYNTHESIZED.labels("too_few_samples").inc()
        return None
    if not cooldown.ready(template):  # only real writer calls are rate-limited
        return None
    try:
        proposal = await writer.write_rule(key, samples)
    except Exception as exc:  # best effort: a bad or unreachable writer never fails the event
        metrics.RULES_SYNTHESIZED.labels("writer_error").inc()
        log.warning(
            "rule writer failed", extra={"event_id": event.event_id, "error": type(exc).__name__}
        )
        return None
    rule_id, reason = validate_proposal(key, proposal, samples)
    if reason is not None:
        metrics.RULES_SYNTHESIZED.labels(f"rejected_{reason}").inc()
        log.info("rule rejected", extra={"event_id": event.event_id, "reason": reason})
        return None
    created = await insert_candidate(
        session, rule_id, key, proposal.regex, dict(proposal.field_map)
    )
    metrics.RULES_SYNTHESIZED.labels("candidate" if created else "duplicate").inc()
    log.info("rule candidate", extra={"event_id": event.event_id, "rule_id": rule_id})
    return rule_id if created else None
