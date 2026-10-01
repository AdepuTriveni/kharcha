from pathlib import Path

import pytest

from kharcha_ml.dataset.build_cli import build
from kharcha_ml.dataset.labels import Extraction, LabeledExample, read_jsonl
from kharcha_ml.dataset.splits import (
    drop_near_duplicates,
    leaking_templates,
    make_splits,
    minhash,
    similarity,
    template_of,
)
from kharcha_ml.dataset.synthetic import (
    HELD_OUT_SENDERS,
    TEMPLATES,
    amount_text,
    generate,
    indian_grouping,
)
from kharcha_ml.distill.teacher import agrees, decode, distill, load_sections

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "samples" / "parsing_sample.jsonl"


def _real(i: int, text: str, sender: str = "AX-HDFCBK", reviewed: bool = False) -> LabeledExample:
    return LabeledExample(
        event_id=f"real-{i}",
        sender=sender,
        text=text,
        source="REAL",
        label=Extraction(is_transaction=True, amount="10", direction="DEBIT", status="SUCCESS"),
        reviewed=reviewed,
    )


def test_indian_amount_formats() -> None:
    assert indian_grouping(100_000) == "1,00,000"
    assert indian_grouping(12_345_678) == "1,23,45,678"
    assert indian_grouping(999) == "999"
    import random

    rng = random.Random(3)
    seen = {amount_text(124_950, rng) for _ in range(200)}
    assert {"1,249.50", "1249.50"} <= seen


def test_synthetic_is_deterministic_and_labels_are_exact() -> None:
    a = list(generate(800, seed=11))
    assert a == list(generate(800, seed=11))
    assert a != list(generate(800, seed=12))
    positives = [e for e in a if e.label.is_transaction]
    negatives = [e for e in a if not e.label.is_transaction]
    assert 0.1 < len(negatives) / len(a) < 0.3
    assert {e.template for e in positives} == {f"syn:{t.id}" for t in TEMPLATES}
    for e in positives:
        label = e.label
        assert label.amount is not None
        assert label.amount in e.text, e.event_id
        for value in (label.reference_id, label.account_hint, label.counterparty_vpa):
            assert value is None or value in e.text, e.event_id
        if label.status == "FAILED" and label.promised_refund_days is not None:
            assert f"{label.promised_refund_days} working days" in e.text
        assert e.reviewed
        assert e.source == "SYNTHETIC"
    assert all(e.source == "HARD_NEGATIVE" and e.reviewed for e in negatives)


def test_no_template_appears_in_more_than_one_split() -> None:
    """§15.3 leakage test: a template lands in exactly one split."""
    examples = [*generate(3000, seed=5), *read_jsonl(SAMPLE)]
    examples += [_real(i, f"Rs.{i}0 paid to shop{i % 7}@ybl Ref {i}") for i in range(60)]
    splits = make_splits(examples)
    assert leaking_templates(splits) == {}
    assert all(len(v) > 0 for v in splits.by_name().values())
    # gold_test is human-verified only and holds every held-out bank example.
    assert all(e.reviewed for e in splits.gold_test)
    held = [e for e in examples if (e.sender or "").split("-")[-1] in HELD_OUT_SENDERS]
    gold_ids = {e.event_id for e in splits.gold_test}
    assert held
    assert all(e.event_id in gold_ids for e in held)
    train_val = splits.train + splits.val
    assert not any((e.sender or "").split("-")[-1] in HELD_OUT_SENDERS for e in train_val)
    # Unreviewed real messages never reach gold.
    assert not any(e.event_id.startswith("real-") for e in splits.gold_test)


def test_splits_are_stable_for_a_salt() -> None:
    examples = list(generate(500, seed=2))
    a, b = make_splits(examples), make_splits(examples)
    assert [e.event_id for e in a.gold_test] == [e.event_id for e in b.gold_test]


def test_near_duplicates_of_gold_are_removed_from_train() -> None:
    gold = _real(1, "Rs.349 debited from A/c XX1234 to zomato@hdfcbank UPI Ref 412345678901")
    dup = _real(2, "Rs.350 debited from A/c XX1234 to zomato@hdfcbank UPI Ref 412345678999")
    other = _real(3, "Your Netflix subscription of Rs 649 was renewed using HDFC card")
    assert similarity(minhash(gold.text), minhash(dup.text)) >= 0.8
    kept, removed = drop_near_duplicates([gold], [dup, other])
    assert removed == 1
    assert kept == [other]


def test_template_of_falls_back_to_signature() -> None:
    e = _real(1, "Rs.10 paid to x@ybl Ref 1")
    assert template_of(e).startswith("HDFCBK|")


def test_decode_teacher_output() -> None:
    body = '{"isTransaction": true, "amount": 349.00, "referenceId": 412345678901, "extra": 1}'
    raw = f"```json\n{body}\n```"
    label = decode(raw)
    assert label is not None
    assert label.amount == "349.00"
    assert label.reference_id == "412345678901"
    assert decode("not json") is None
    assert decode("[1, 2]") is None
    assert decode('{"isTransaction": true, "direction": "SIDEWAYS"}') is None


def test_prompt_sections_come_from_backend_registry() -> None:
    sections = load_sections()
    assert "Extract ONE financial transaction" in sections["system"]
    assert "{text}" in sections["user"]


class FakeTeacher:
    name = "fake"

    def __init__(self, answers: dict[str, Extraction | None]) -> None:
        self.answers = answers

    async def label(self, example: LabeledExample) -> Extraction | None:
        return self.answers[example.event_id]


async def test_distill_builds_review_queue() -> None:
    same = _real(1, "a")
    different = _real(2, "b")
    broken = _real(3, "c")
    human = _real(4, "d", reviewed=True)
    teacher = FakeTeacher(
        {
            "real-1": same.label,
            "real-2": same.label.model_copy(update={"amount": "11"}),
            "real-3": None,
        }
    )
    result = await distill([same, different, broken, human], teacher, sample_rate=0.0)
    assert (result.disagreements, result.failures) == (1, 1)
    assert {e.event_id for e in result.review_queue} == {"real-2", "real-3"}
    by_id = {e.event_id: e for e in result.labeled}
    assert by_id["real-2"].label.amount == "11"  # teacher label kept, flagged for review
    assert by_id["real-4"] is human
    assert agrees(same.label, same.label)
    assert not agrees(same.label, Extraction(is_transaction=False))


def test_build_writes_splits_and_card(tmp_path: Path) -> None:
    from kharcha_ml.dataset.labels import write_jsonl

    synthetic = tmp_path / "syn.jsonl"
    write_jsonl(synthetic, generate(600, seed=9))
    reviewed = tmp_path / "labels.jsonl"
    write_jsonl(reviewed, read_jsonl(SAMPLE))
    card = tmp_path / "DATASET_CARD.md"
    splits = build([synthetic], reviewed, tmp_path / "processed", card=card)
    for name in ("train", "val", "gold_test"):
        assert (tmp_path / "processed" / f"{name}.jsonl").exists()
    text = card.read_text(encoding="utf-8")
    assert "never used for training" in text
    assert "| gold_test |" in text
    assert sum(len(v) for v in splits.by_name().values()) <= 618


@pytest.mark.parametrize("seed", [0, 1])
def test_every_template_renders(seed: int) -> None:
    import random

    from kharcha_ml.dataset.synthetic import render

    rng = random.Random(seed)
    for template in TEMPLATES:
        example = render(template, rng, 0)
        assert example.label.direction == template.direction
        assert example.label.channel == template.channel
