from collections.abc import Iterator
from pathlib import Path

from kharcha_ml.dataset.label_cli import review
from kharcha_ml.dataset.labels import Extraction, read_jsonl, template_signature
from kharcha_ml.eval.metrics import evaluate, field_equal, to_markdown

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "samples" / "parsing_sample.jsonl"


def test_sample_set_is_valid() -> None:
    examples = list(read_jsonl(SAMPLE))
    assert len(examples) >= 18
    assert any(not e.label.is_transaction for e in examples)
    assert all(e.source in {"SYNTHETIC", "HARD_NEGATIVE"} for e in examples)  # never real data


def test_template_signature_groups_same_template() -> None:
    a = template_signature("Rs.349.00 debited from A/c XX1234 to VPA zomato@hdfcbank", "AX-HDFCBK")
    b = template_signature("Rs.12.50 debited from A/c XX9876 to VPA ravi@okaxis", "AX-HDFCBK")
    c = template_signature("Rs.12.50 credited to A/c XX9876", "AX-HDFCBK")
    assert a == b != c
    assert a.startswith("HDFCBK|")


def test_field_equal_amount_formats() -> None:
    e = Extraction(is_transaction=True, amount="1,249.50")
    assert field_equal("amount", e, Extraction(is_transaction=True, amount="1249.5"))
    assert field_equal("amount", e, Extraction(is_transaction=True, amount="Rs.1,249.50"))
    assert not field_equal("amount", e, Extraction(is_transaction=True, amount="1249"))


def test_evaluate_counts() -> None:
    txn = Extraction(
        is_transaction=True,
        amount="10",
        direction="DEBIT",
        status="SUCCESS",
        merchant_raw="Zomato",
        reference_id="1",
    )
    neg = Extraction(is_transaction=False)
    report = evaluate(
        [
            ("A", txn, txn),  # all fields right
            ("A", txn, txn.model_copy(update={"amount": "11"})),  # amount wrong
            ("B", txn, None),  # missed
            ("B", neg, neg),  # correct rejection
            ("B", neg, txn),  # false positive
        ]
    )
    d = report.to_dict()
    assert (report.tp, report.fn, report.fp, report.tn) == (2, 1, 1, 1)
    assert d["allFieldsExactMatch"] == 0.4  # (1 txn + 1 negative) / 5
    assert d["fieldExactMatch"]["amount"] == 0.5  # type: ignore[index]
    assert "| A | 2 | 0.500 |" in to_markdown("t", report)


def _answers(*values: str) -> Iterator[str]:
    yield from values


def test_review_accept_edit_reject_and_resume(tmp_path: Path) -> None:
    out = tmp_path / "labels.jsonl"
    answers = _answers("a", "e", "", "", "", "", "", "", "", "", "-", "", "n", "q")
    written = review(SAMPLE, out, ask=lambda _: next(answers), say=lambda _: None)
    assert written == 3
    labels = list(read_jsonl(out))
    assert labels[0].label == next(read_jsonl(SAMPLE)).label
    assert labels[1].label.balance_after is None  # cleared with '-'
    assert labels[2].label.is_transaction is False
    assert all(lbl.reviewed and lbl.template for lbl in labels)

    resumed = _answers("q")
    assert review(SAMPLE, out, ask=lambda _: next(resumed), say=lambda _: None) == 0
    assert len(list(read_jsonl(out))) == 3
