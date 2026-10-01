"""Parser eval suite (PROJECT_SPEC §15.5, §27.2) -- ``kharcha-eval parsing``.

Runs the server parser (pre-filter -> extractor -> §10.2 validation) over labeled examples and
scores it with the shared ``kharcha_ml.eval.metrics``. Labels are only read here, never
trained on (rule 8).
"""

import statistics
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from kharcha_common.events import ParsedTransactionPayload, RawEvent, RawEventPayload
from kharcha_common.money import paise_to_rupees
from kharcha_ml.dataset.labels import Extraction, LabeledExample
from kharcha_ml.eval.metrics import ParsingReport, evaluate, to_markdown
from kharcha_processor.parser import Dropped, Failed, NotTransaction, parse_raw_event
from kharcha_processor.teacher import Extractor
from kharcha_processor.validation import ValidationFailure

# Labeled examples carry no timestamp; the parser only copies it into txn_time.
_POSTED = datetime(2026, 1, 1, tzinfo=UTC)


def to_raw_event(example: LabeledExample) -> RawEvent:
    return RawEvent(
        user_id="eval",
        type="RAW_SMS" if example.sender else "RAW_NOTIFICATION",
        occurred_at=_POSTED,
        producer="kharcha-eval",
        payload=RawEventPayload(
            source_app=example.source_app,
            sender=example.sender,
            text=example.text,
            posted_at=_POSTED,
            device_id="eval",
            redacted=True,
        ),
    )


def to_extraction(payload: ParsedTransactionPayload) -> Extraction:
    balance = payload.balance_after_paise
    return Extraction(
        is_transaction=True,
        amount=str(paise_to_rupees(payload.amount_paise)),
        direction=payload.direction.value,
        channel=payload.channel.value,
        status=payload.status.value,
        merchant_raw=payload.merchant_raw,
        counterparty_vpa=payload.counterparty_vpa,
        reference_id=payload.reference_id,
        account_hint=payload.account_hint,
        balance_after=None if balance is None else str(paise_to_rupees(balance)),
        promised_refund_days=payload.promised_refund_days,
    )


def _outcome_name(outcome: object) -> str:
    match outcome:
        case Dropped(reason=reason):
            return f"DROPPED_{reason.value}"
        case NotTransaction():
            return "NOT_TRANSACTION"
        case Failed(reason=reason):
            return f"FAILED_{reason.value}"
        case _:
            return "TRANSACTION"


@dataclass
class ParsingEval:
    subject: str
    report: ParsingReport
    outcomes: Counter[str] = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)
    model_calls: int = 0

    def json_validity(self) -> float:
        bad = self.outcomes[f"FAILED_{ValidationFailure.BAD_MODEL_OUTPUT.value}"]
        return 1 - bad / self.model_calls if self.model_calls else 1.0

    def latency(self) -> dict[str, float]:
        if not self.latencies_ms:
            return {"p50": 0.0, "p95": 0.0}
        ordered = sorted(self.latencies_ms)
        p95 = ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]
        return {"p50": round(statistics.median(ordered), 1), "p95": round(p95, 1)}

    def metrics(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            **self.report.to_dict(),
            "jsonValidity": round(self.json_validity(), 4),
            "outcomes": dict(sorted(self.outcomes.items())),
            "latencyMs": self.latency(),
        }

    def markdown(self, title: str) -> str:
        lines = [
            to_markdown(title, self.report).rstrip("\n"),
            "",
            f"Subject: `{self.subject}`",
            "",
            f"JSON validity: {self.json_validity():.3f} over {self.model_calls} model calls; "
            f"latency p50 {self.latency()['p50']} ms, p95 {self.latency()['p95']} ms",
            "",
            "| Parser outcome | n |",
            "|---|---|",
        ]
        lines += [f"| {name} | {n} |" for name, n in sorted(self.outcomes.items())]
        return "\n".join(lines) + "\n"


class _CountingExtractor:
    """Wraps an extractor to count model calls and time them."""

    def __init__(self, inner: Extractor, result: ParsingEval) -> None:
        self._inner = inner
        self._result = result
        self.model_version = inner.model_version

    async def extract(self, *, sender: str | None, source_app: str | None, text: str) -> Any:
        self._result.model_calls += 1
        start = time.perf_counter()
        try:
            return await self._inner.extract(sender=sender, source_app=source_app, text=text)
        finally:
            self._result.latencies_ms.append((time.perf_counter() - start) * 1000)


async def run_parsing(examples: Sequence[LabeledExample], extractor: Extractor) -> ParsingEval:
    result = ParsingEval(subject=extractor.model_version, report=ParsingReport())
    counting = _CountingExtractor(extractor, result)
    pairs: list[tuple[str | None, Extraction, Extraction | None]] = []
    for example in examples:
        outcome = await parse_raw_event(to_raw_event(example), counting)
        result.outcomes[_outcome_name(outcome)] += 1
        predicted = (
            to_extraction(outcome) if isinstance(outcome, ParsedTransactionPayload) else None
        )
        pairs.append((example.sender or example.source_app, example.label, predicted))
    result.report = evaluate(pairs)
    return result
