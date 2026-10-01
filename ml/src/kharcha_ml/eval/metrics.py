"""Parsing metrics shared by backend and model evals (PROJECT_SPEC §15.5).

isTransaction precision/recall/F1, exact match per field, all-fields exact match, per-sender
breakdown. Amounts are compared as paise (``"349"`` == ``"349.00"``), text fields
case-insensitively with surrounding spaces removed.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from kharcha_ml.dataset.labels import Extraction

FIELDS = ("amount", "direction", "status", "merchant_raw", "reference_id")


def _amount_key(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.lower().replace("rs.", "").replace("rs", "").replace("inr", "")
    cleaned = cleaned.replace("₹", "").replace(",", "").strip()
    try:
        return int(Decimal(cleaned) * 100)
    except InvalidOperation:
        return None


def field_equal(name: str, expected: Extraction, predicted: Extraction) -> bool:
    a, b = getattr(expected, name), getattr(predicted, name)
    if name == "amount":
        return _amount_key(a) == _amount_key(b)
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return bool(a == b)


@dataclass
class ParsingReport:
    n: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    field_correct: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FIELDS, 0))
    all_fields_correct: int = 0
    by_sender: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))

    @property
    def positives(self) -> int:
        return self.tp

    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        return 2 * p * r / (p + r) if p + r else 0.0

    def to_dict(self) -> dict[str, object]:
        pos = max(self.tp, 1)
        return {
            "n": self.n,
            "isTransaction": {
                "precision": round(self.precision(), 4),
                "recall": round(self.recall(), 4),
                "f1": round(self.f1(), 4),
            },
            "fieldExactMatch": {k: round(v / pos, 4) for k, v in self.field_correct.items()},
            # All-fields EM over every example: negatives count as correct when rejected.
            "allFieldsExactMatch": round((self.all_fields_correct + self.tn) / max(self.n, 1), 4),
            "bySender": {
                s: {"n": n, "allFieldsExactMatch": round(c / n, 4)}
                for s, (c, n) in sorted(self.by_sender.items())
            },
        }


def evaluate(
    pairs: Sequence[tuple[str | None, Extraction, Extraction | None]],
) -> ParsingReport:
    """``pairs`` = (sender, expected, predicted or None when the parser produced nothing)."""
    report = ParsingReport()
    for sender, expected, predicted in pairs:
        report.n += 1
        got_txn = predicted is not None and predicted.is_transaction
        correct = False
        if expected.is_transaction and got_txn:
            assert predicted is not None
            report.tp += 1
            matches = {f: field_equal(f, expected, predicted) for f in FIELDS}
            for name, ok in matches.items():
                report.field_correct[name] += ok
            correct = all(matches.values())
            report.all_fields_correct += correct
        elif expected.is_transaction:
            report.fn += 1
        elif got_txn:
            report.fp += 1
        else:
            report.tn += 1
            correct = True
        bucket = report.by_sender[sender or "unknown"]
        bucket[0] += correct
        bucket[1] += 1
    return report


def to_markdown(title: str, report: ParsingReport) -> str:
    d = report.to_dict()
    is_txn = d["isTransaction"]
    fields = d["fieldExactMatch"]
    assert isinstance(is_txn, dict) and isinstance(fields, dict)
    lines = [
        f"# {title}",
        "",
        f"Examples: {report.n}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| isTransaction F1 | {is_txn['f1']:.3f} |",
        f"| isTransaction precision | {is_txn['precision']:.3f} |",
        f"| isTransaction recall | {is_txn['recall']:.3f} |",
        f"| All-fields exact match | {d['allFieldsExactMatch']:.3f} |",
    ]
    lines += [f"| EM {name} | {value:.3f} |" for name, value in fields.items()]
    by_sender = d["bySender"]
    assert isinstance(by_sender, dict)
    lines += ["", "| Sender | n | All-fields EM |", "|---|---|---|"]
    lines += [f"| {s} | {v['n']} | {v['allFieldsExactMatch']:.3f} |" for s, v in by_sender.items()]
    return "\n".join(lines) + "\n"
