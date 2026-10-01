"""Tiered parser (PROJECT_SPEC §10): pre-filter -> rules -> device model -> own server model
-> teacher LLM, with §10.2 validation on every tier's output.

``parse_tiered`` also reports what the rules did (counters, promotion, auto-disable) and an
optional shadow comparison (§10.5) for the ``model-shadow`` topic.
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
from kharcha_common.money import paise_to_rupees
from kharcha_processor import metrics
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.prefilter import DropReason, prefilter
from kharcha_processor.rules import CompiledRule, apply_rule, same_extraction
from kharcha_processor.teacher import BadModelOutputError, Extractor
from kharcha_processor.validation import Validated, ValidationFailure, validate_extraction

TEACHER_CONFIDENCE = 0.9
RULE_CONFIDENCE = 0.99
DEVICE_CONFIDENCE = 0.95
SERVER_MODEL_CONFIDENCE = 0.95


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
    event: RawEvent,
    text: str,
    teacher: Extractor,
    method: ParseMethod = ParseMethod.TEACHER_LLM,
    confidence: float = TEACHER_CONFIDENCE,
) -> tuple[ParseOutcome, ExtractionResult | None]:
    """One model tier (own model or teacher): extract, then section 10.2 validation."""
    try:
        result = await teacher.extract(
            sender=event.payload.sender, source_app=event.payload.source_app, text=text
        )
    except BadModelOutputError:
        return Failed(ValidationFailure.BAD_MODEL_OUTPUT), None
    checked = validate_extraction(result, text)
    if checked is ValidationFailure.NOT_TRANSACTION:
        return NotTransaction(method), result
    if isinstance(checked, ValidationFailure):
        return Failed(checked), result
    payload = _payload(
        event, result, checked, method, version=teacher.model_version, confidence=confidence
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
    model: Extractor | None = None,
    model_shadow: bool = False,
) -> TieredParse:
    """Parse one bank message.

    ``shadow`` (a sampled event) also runs the teacher behind a matching rule, and the own
    model behind the teacher while the model is in SHADOW (``model_shadow``). An ACTIVE own
    model (tier 3) answers before the teacher; if its output fails validation the teacher runs.
    """
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

    if rule_hit is not None:
        teacher_outcome, teacher_result = await _teacher_tier(event, text, teacher)
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

    # No rule: tier 2 (device result, re-validated) -> tier 3 (own model) -> tier 4 (teacher).
    outcome: ParseOutcome | None = None
    result: ExtractionResult | None = None
    device = _device_tier(event, text)
    if device is not None:
        outcome, result = device
    elif model is not None and not model_shadow:
        tried, tried_result = await _teacher_tier(
            event, text, model, ParseMethod.SERVER_MODEL, SERVER_MODEL_CONFIDENCE
        )
        if not isinstance(tried, Failed):
            outcome, result = tried, tried_result
    shadow_payload: ModelShadowPayload | None = None
    if outcome is None:
        outcome, result = await _teacher_tier(event, text, teacher)
        if model is not None and model_shadow and shadow:
            model_outcome, model_result = await _teacher_tier(
                event, text, model, ParseMethod.SERVER_MODEL, SERVER_MODEL_CONFIDENCE
            )
            agreed = _agree(result, model_result)
            metrics.SHADOW_COMPARISONS.labels(str(agreed).lower()).inc()
            shadow_payload = ModelShadowPayload(
                raw_event_id=event.event_id,
                tier_results=[
                    _tier_result(ParseMethod.TEACHER_LLM, teacher.model_version, outcome),
                    _tier_result(ParseMethod.SERVER_MODEL, model.model_version, model_outcome),
                ],
                agreement=agreed,
            )

    parse = TieredParse(outcome, observations, shadow=shadow_payload)
    if isinstance(outcome, ParsedTransactionPayload):
        assert result is not None
        for rule in (r for r in rules if not r.is_active):
            extracted = apply_rule(rule, text)
            if extracted is None:
                continue
            matched_any = True
            valid = not isinstance(validate_extraction(extracted, text), ValidationFailure)
            agreed = valid and same_extraction(extracted, result)
            observations.append(RuleObservation(rule.id, agreed=agreed))
        if not matched_any:
            parse.uncovered = result
    _count(parse.outcome)
    return parse


def _agree(a: ExtractionResult | None, b: ExtractionResult | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if not a.is_transaction or not b.is_transaction:
        return a.is_transaction == b.is_transaction
    return same_extraction(a, b)


def _device_tier(
    event: RawEvent, text: str
) -> tuple[ParsedTransactionPayload, ExtractionResult] | None:
    """Tier 2: the phone's own-model result, accepted only if it passes server validation."""
    device = event.payload.device_parse
    if device is None:
        return None
    r = device.result
    extracted = ExtractionResult(
        is_transaction=True,
        amount=str(paise_to_rupees(r.amount_paise)),
        direction=r.direction,
        channel=r.channel,
        status=r.status,
        merchant_raw=r.merchant_raw,
        reference_id=r.reference_id,
    )
    checked = validate_extraction(extracted, text)
    if isinstance(checked, ValidationFailure):
        metrics.PARSE_FAILURES.labels(f"DEVICE_{checked.value}").inc()
        return None
    payload = _payload(
        event,
        extracted,
        checked,
        ParseMethod.DEVICE_MODEL,
        version=device.model_version,
        confidence=DEVICE_CONFIDENCE,
    )
    return payload, extracted


async def parse_raw_event(
    event: RawEvent, teacher: Extractor, rules: Sequence[CompiledRule] = ()
) -> ParseOutcome:
    return (await parse_tiered(event, teacher, rules)).outcome
