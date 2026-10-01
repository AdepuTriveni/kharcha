from datetime import UTC, datetime
from typing import Any

from kharcha_common.events import (
    Channel,
    DeviceParse,
    DeviceParseResult,
    Direction,
    ParsedTransactionPayload,
    ParseMethod,
    RawEvent,
    RawEventPayload,
    TxnStatus,
)
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.parser import parse_tiered
from kharcha_processor.teacher import BadModelOutputError

POSTED = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
TEXT = "Sent Rs.120.00 from HDFC Bank A/c **1234 to chai@ybl on 03/10/26 Ref 512345678901"
LABEL = {
    "isTransaction": True,
    "amount": "120.00",
    "direction": "DEBIT",
    "channel": "UPI",
    "status": "SUCCESS",
    "merchantRaw": "chai@ybl",
    "referenceId": "512345678901",
    "accountHint": "1234",
}


class Fake:
    def __init__(self, name: str, data: dict[str, Any] | Exception) -> None:
        self.model_version = name
        self.data = data
        self.calls = 0

    async def extract(
        self, *, sender: str | None, source_app: str | None, text: str
    ) -> ExtractionResult:
        self.calls += 1
        if isinstance(self.data, Exception):
            raise self.data
        return ExtractionResult.model_validate(self.data)


def _event(device: DeviceParse | None = None) -> RawEvent:
    return RawEvent(
        user_id="u_1",
        type="RAW_SMS",
        occurred_at=POSTED,
        producer="test",
        payload=RawEventPayload(
            sender="AX-HDFCBK",
            text=TEXT,
            posted_at=POSTED,
            device_id="d",
            redacted=True,
            device_parse=device,
        ),
    )


def _device(amount_paise: int) -> DeviceParse:
    return DeviceParse(
        model_version="kharcha-parser-v1-q4",
        result=DeviceParseResult(
            amount_paise=amount_paise,
            direction=Direction.DEBIT,
            channel=Channel.UPI,
            status=TxnStatus.SUCCESS,
            merchant_raw="chai@ybl",
            reference_id="512345678901",
        ),
        latency_ms=820,
    )


def _method(parse: object) -> ParseMethod:
    assert isinstance(parse, ParsedTransactionPayload)
    return parse.parse_method


async def test_valid_device_result_is_used_without_any_model_call() -> None:
    teacher = Fake("teacher", LABEL)
    parse = await parse_tiered(_event(_device(12_000)), teacher)
    assert _method(parse.outcome) is ParseMethod.DEVICE_MODEL
    assert isinstance(parse.outcome, ParsedTransactionPayload)
    assert parse.outcome.model_version == "kharcha-parser-v1-q4"
    assert teacher.calls == 0
    assert parse.uncovered is not None  # still a rule-synthesis opportunity


async def test_hallucinated_device_amount_falls_through_to_teacher() -> None:
    teacher = Fake("teacher", LABEL)
    parse = await parse_tiered(_event(_device(99_900)), teacher)  # ₹999 is not in the text
    assert _method(parse.outcome) is ParseMethod.TEACHER_LLM
    assert teacher.calls == 1


async def test_active_server_model_answers_before_teacher() -> None:
    teacher, model = Fake("teacher", LABEL), Fake("ollama/kharcha-parser:v1#parser/v1", LABEL)
    parse = await parse_tiered(_event(), teacher, model=model, model_shadow=False)
    assert _method(parse.outcome) is ParseMethod.SERVER_MODEL
    assert (model.calls, teacher.calls) == (1, 0)


async def test_failing_server_model_falls_back_to_teacher() -> None:
    teacher = Fake("teacher", LABEL)
    for broken in (BadModelOutputError("x"), {**LABEL, "amount": "999"}):
        model = Fake("own", broken)
        parse = await parse_tiered(_event(), teacher, model=model, model_shadow=False)
        assert _method(parse.outcome) is ParseMethod.TEACHER_LLM


async def test_shadow_model_runs_behind_teacher_on_sampled_events() -> None:
    teacher, model = Fake("teacher", LABEL), Fake("own", {**LABEL, "referenceId": "512345678900"})
    unsampled = await parse_tiered(_event(), teacher, model=model, model_shadow=True, shadow=False)
    assert model.calls == 0
    assert unsampled.shadow is None

    sampled = await parse_tiered(_event(), teacher, model=model, model_shadow=True, shadow=True)
    assert _method(sampled.outcome) is ParseMethod.TEACHER_LLM  # teacher stays authoritative
    assert sampled.shadow is not None
    assert not sampled.shadow.agreement
    methods = [t.parse_method for t in sampled.shadow.tier_results]
    assert methods == [ParseMethod.TEACHER_LLM, ParseMethod.SERVER_MODEL]

    agreeing = Fake("own", LABEL)
    same = await parse_tiered(_event(), teacher, model=agreeing, model_shadow=True, shadow=True)
    assert same.shadow is not None
    assert same.shadow.agreement
