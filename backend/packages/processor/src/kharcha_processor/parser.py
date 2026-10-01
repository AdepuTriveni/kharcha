"""Tiered parser (PROJECT_SPEC §10): pre-filter -> rules -> teacher LLM, validation everywhere.

The own model (tiers 2-3) arrives in Phase 4. ``parse_tiered`` also reports what the rules
did (for counters, promotion and auto-disable) and an optional shadow comparison (§10.5).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from kharcha_common.events import (
    ModelShadowPayload,
    ParsedTransactionPayload,
    ParseMethod,
    RawEvent,
    TierOutcome,
    TierResult,
)
from kharcha_processor import metrics
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.prefilter import DropReason, prefilter
from kharcha_processor.rules import CompiledRule, apply_rule, same_extraction
from kharcha_processor.teacher import BadModelOutputError, Extractor
from kharcha_processor.validation import Validated, ValidationFailure, validate_extraction

TEACHER_CONFIDENCE = 0.9
RULE_CONFIDENCE = 0.99


@dataclass(frozen=True, slots=True)
class Dropped:
    reason: DropReason


@dataclass(frozen=True, slots=True)
class NotTransaction:
    method: ParseMethod


@dataclass(frozen=True, slots=True)
class Failed:
    reason: ValidationFailure


ParseOutcome = ParsedTransactionPayload | Dropped | NotTransaction | Failed


@dataclass(frozen=True, slots=True)
class RuleObservation:
    rule_id: str
    agreed: bool


@dataclass
class TieredParse:
    outcome: ParseOutcome
    observations: list[RuleObservation] = field(default_factory=list)
    shadow: ModelShadowPayload | None = None
    # Set when the teacher parsed a transaction that no rule covers: a synthesis opportunity.
    uncovered: ExtractionResult | None = None


def _payload(
    event: RawEvent,
    result: ExtractionResult,
    valid: Validated,
    method: ParseMethod,
    *,
    version: str | None = None,
    rule_id: str | None = None,
    confidence: float = TEACHER_CONFIDENCE,
) -> ParsedTransactionPayload:
    assert result.direction is not None  # guaranteed by validation
    return ParsedTransactionPayload(
        raw_event_id=event.event_id,
        amount_paise=valid.amount_paise,
        direction=result.direction,
        channel=result.channel,
        status=result.status,
        merchant_raw=result.merchant_raw,
        counterparty_vpa=result.counterparty_vpa,
        reference_id=result.reference_id,
        account_hint=result.account_hint[-4:] if result.account_hint else None,
        balance_after_paise=valid.balance_after_paise,
        txn_time=event.payload.posted_at,
        parse_method=method,
        model_version=version,
        rule_id=rule_id,
        confidence=confidence,
        promised_refund_days=result.promised_refund_days,
    )


def _tier_result(method: ParseMethod, version: str | None, outcome: ParseOutcome) -> TierResult:
    if isinstance(outcome, ParsedTransactionPayload):
        return TierResult(
            parse_method=method,
            version=version,
            outcome=TierOutcome.TRANSACTION,
            amount_paise=outcome.amount_paise,
            direction=outcome.direction,
            status=outcome.status,
            merchant_raw=outcome.merchant_raw,
            reference_id=outcome.reference_id,
        )
    kind = TierOutcome.FAILED if isinstance(outcome, Failed) else TierOutcome.NOT_TRANSACTION
    return TierResult(parse_method=method, version=version, outcome=kind)


async def _teacher_tier(
    event: RawEvent, text: str, teacher: Extractor
) -> tuple[ParseOutcome, ExtractionResult | None]:
    try:
        result = await teacher.extract(
            sender=event.payload.sender, source_app=event.payload.source_app, text=text
        )
    except BadModelOutputError:
        return Failed(ValidationFailure.BAD_MODEL_OUTPUT), None
    checked = validate_extraction(result, text)
    if checked is ValidationFailure.NOT_TRANSACTION:
        return NotTransaction(ParseMethod.TEACHER_LLM), result
    if isinstance(checked, ValidationFailure):
        return Failed(checked), result
    payload = _payload(
        event, result, checked, ParseMethod.TEACHER_LLM, version=teacher.model_version
    )
    return payload, result


def _count(outcome: ParseOutcome) -> None:
    match outcome:
        case ParsedTransactionPayload(parse_method=method):
            metrics.PARSE_METHOD.labels(method.value).inc()
        case NotTransaction():
            metrics.NOT_TRANSACTION.inc()
        case Failed(reason=reason):
            metrics.PARSE_FAILURES.labels(reason.value).inc()
        case Dropped(reason=reason):
            metrics.PREFILTER_DROPS.labels(reason.value).inc()


async def parse_tiered(
    event: RawEvent,
    teacher: Extractor,
    rules: Sequence[CompiledRule] = (),
    *,
    shadow: bool = False,
) -> TieredParse:
    """Parse one bank message. ``shadow`` also runs the teacher behind a matching rule."""
    text = event.payload.text
    if (reason := prefilter(text)) is not None:
        parse = TieredParse(Dropped(reason))
        _count(parse.outcome)
        return parse
    assert text is not None

    observations: list[RuleObservation] = []
    matched_any = False
    rule_hit: tuple[CompiledRule, ExtractionResult, ParsedTransactionPayload] | None = None
    for rule in (r for r in rules if r.is_active):
        extracted = apply_rule(rule, text)
        if extracted is None:
            continue
        matched_any = True
        checked = validate_extraction(extracted, text)
        if isinstance(checked, ValidationFailure):
            observations.append(RuleObservation(rule.id, agreed=False))
            continue
        payload = _payload(
            event, extracted, checked, ParseMethod.RULE, rule_id=rule.id, confidence=RULE_CONFIDENCE
        )
        rule_hit = (rule, extracted, payload)
        break

    if rule_hit is not None and not shadow:
        parse = TieredParse(rule_hit[2], observations)
        _count(parse.outcome)
        return parse

    teacher_outcome, teacher_result = await _teacher_tier(event, text, teacher)

    if rule_hit is not None:
        rule, extracted, payload = rule_hit
        agreed = teacher_result is not None and same_extraction(extracted, teacher_result)
        observations.append(RuleObservation(rule.id, agreed=agreed))
        metrics.SHADOW_COMPARISONS.labels(str(agreed).lower()).inc()
        parse = TieredParse(
            payload,
            observations,
            shadow=ModelShadowPayload(
                raw_event_id=event.event_id,
                tier_results=[
                    _tier_result(ParseMethod.RULE, rule.id, payload),
                    _tier_result(ParseMethod.TEACHER_LLM, teacher.model_version, teacher_outcome),
                ],
                agreement=agreed,
            ),
        )
        _count(parse.outcome)
        return parse

    parse = TieredParse(teacher_outcome, observations)
    if isinstance(teacher_outcome, ParsedTransactionPayload):
        assert teacher_result is not None
        for rule in (r for r in rules if not r.is_active):
            extracted = apply_rule(rule, text)
            if extracted is None:
                continue
            matched_any = True
            valid = not isinstance(validate_extraction(extracted, text), ValidationFailure)
            agreed = valid and same_extraction(extracted, teacher_result)
            observations.append(RuleObservation(rule.id, agreed=agreed))
        if not matched_any:
            parse.uncovered = teacher_result
    _count(parse.outcome)
    return parse


async def parse_raw_event(
    event: RawEvent, teacher: Extractor, rules: Sequence[CompiledRule] = ()
) -> ParseOutcome:
    return (await parse_tiered(event, teacher, rules)).outcome
