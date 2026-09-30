"""Tiered parser (PROJECT_SPEC §10). W2: pre-filter -> teacher LLM -> validation.

Rules (tier 1) arrive in W5 and the own model (tiers 2-3) in Phase 4.
"""

from dataclasses import dataclass

from kharcha_common.events import ParsedTransactionPayload, ParseMethod, RawEvent
from kharcha_processor import metrics
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.prefilter import DropReason, prefilter
from kharcha_processor.teacher import BadModelOutputError, Extractor
from kharcha_processor.validation import Validated, ValidationFailure, validate_extraction

TEACHER_CONFIDENCE = 0.9


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


def _payload(
    event: RawEvent, result: ExtractionResult, valid: Validated, method: ParseMethod, version: str
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
        confidence=TEACHER_CONFIDENCE,
        promised_refund_days=result.promised_refund_days,
    )


async def parse_raw_event(event: RawEvent, teacher: Extractor) -> ParseOutcome:
    text = event.payload.text
    if (reason := prefilter(text)) is not None:
        metrics.PREFILTER_DROPS.labels(reason.value).inc()
        return Dropped(reason)
    assert text is not None

    try:
        result = await teacher.extract(
            sender=event.payload.sender, source_app=event.payload.source_app, text=text
        )
    except BadModelOutputError:
        metrics.PARSE_FAILURES.labels(ValidationFailure.BAD_MODEL_OUTPUT.value).inc()
        return Failed(ValidationFailure.BAD_MODEL_OUTPUT)
    checked = validate_extraction(result, text)
    if checked is ValidationFailure.NOT_TRANSACTION:
        metrics.NOT_TRANSACTION.inc()
        return NotTransaction(ParseMethod.TEACHER_LLM)
    if isinstance(checked, ValidationFailure):
        metrics.PARSE_FAILURES.labels(checked.value).inc()
        return Failed(checked)

    metrics.PARSE_METHOD.labels(ParseMethod.TEACHER_LLM.value).inc()
    return _payload(event, result, checked, ParseMethod.TEACHER_LLM, teacher.model_version)
