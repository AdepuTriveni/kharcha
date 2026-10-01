import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from kharcha_common.events import ParsedTransactionPayload, ParseMethod, RawEvent, RawEventPayload
from kharcha_common.templates import sender_key
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.parser import Dropped, parse_tiered
from kharcha_processor.rule_store import load_seed_rules
from kharcha_processor.rules import (
    CompiledRule,
    RuleStatus,
    UnsafeRuleError,
    apply_rule,
    check_rule,
    compile_rule,
    next_status,
    same_extraction,
)
from kharcha_processor.validation import ValidationFailure, validate_extraction

SAMPLE = Path(__file__).resolve().parents[4] / "ml" / "data" / "samples" / "parsing_sample.jsonl"
POSTED = datetime(2026, 10, 3, 8, 0, tzinfo=UTC)
AMOUNT = r"(?P<amount>[\d,]+(?:\.\d{1,2})?)"


def _samples() -> list[dict[str, Any]]:
    lines = SAMPLE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def _seeds(status: str = "ACTIVE") -> list[CompiledRule]:
    return [
        compile_rule(s.id, s.sender_key, s.regex, s.field_map, status) for s in load_seed_rules()
    ]


def _event(text: str, sender: str | None = "AX-HDFCBK") -> RawEvent:
    return RawEvent(
        user_id="u_1",
        type="RAW_SMS",
        occurred_at=POSTED,
        producer="test",
        payload=RawEventPayload(
            sender=sender, text=text, posted_at=POSTED, device_id="d", redacted=True
        ),
    )


class FakeTeacher:
    model_version = "fake#parser/v1"

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls = 0

    async def extract(
        self, *, sender: str | None, source_app: str | None, text: str
    ) -> ExtractionResult:
        self.calls += 1
        return ExtractionResult.model_validate(self.data)


def test_every_seed_rule_is_safe_and_unique() -> None:
    seeds = load_seed_rules()
    assert len(seeds) >= 12
    assert len({s.id for s in seeds}) == len(seeds)


def test_seed_rules_reproduce_gold_labels_on_sample() -> None:
    rules = _seeds()
    covered = 0
    for example in _samples():
        key = sender_key(example["sender"], example["sourceApp"])
        hits = [
            extracted
            for rule in rules
            if rule.sender_key == key and (extracted := apply_rule(rule, example["text"]))
        ]
        gold = ExtractionResult.model_validate(example["label"])
        if not gold.is_transaction:
            assert hits == [], example["eventId"]
            continue
        assert len(hits) == 1, example["eventId"]
        assert same_extraction(hits[0], gold), example["eventId"]
        assert hits[0].channel == gold.channel, example["eventId"]
        assert not isinstance(validate_extraction(hits[0], example["text"]), ValidationFailure)
        covered += 1
    assert covered == 12


@pytest.mark.parametrize(
    ("regex", "reason"),
    [
        (rf"Rs {AMOUNT} (\w+)+ x", "nested_quantifier"),
        (rf"Rs {AMOUNT} (a|b)*\1", "backreference"),
        (rf"Rs {AMOUNT} to zomato@hdfcbank", "literal_data"),
        (rf"Rs {AMOUNT} Ref 412345", "literal_data"),
        (r"Rs (?P<amt>\d+)", "no_amount_group"),
        (rf"Rs {AMOUNT} (?P<name>\w+)", "unknown_group"),
        (rf"Rs {AMOUNT} (", "does_not_compile"),
        ("x" * 700, "too_long"),
    ],
)
def test_check_rule_rejects_unsafe(regex: str, reason: str) -> None:
    with pytest.raises(UnsafeRuleError, match=reason):
        check_rule(regex, {"direction": "DEBIT"})


def test_check_rule_field_map() -> None:
    with pytest.raises(UnsafeRuleError, match="bad_direction"):
        check_rule(f"Rs {AMOUNT}", {"direction": "UP"})
    with pytest.raises(UnsafeRuleError, match="unknown_field"):
        check_rule(f"Rs {AMOUNT}", {"direction": "DEBIT", "merchant": "x"})
    # Quantifier counts and escapes are not literal data.
    check_rule(rf"Rs {AMOUNT} Ref (?P<ref>\d{{6,12}})", {"direction": "DEBIT"})


def test_lifecycle() -> None:
    c, a, d = RuleStatus.CANDIDATE, RuleStatus.ACTIVE, RuleStatus.DISABLED
    assert next_status(c, 4, 0) is c
    assert next_status(c, 5, 0) is a
    assert next_status(c, 9, 1) is c  # not consistent
    assert next_status(a, 100, 2) is a
    assert next_status(a, 100, 3) is d
    assert next_status(c, 0, 3) is d
    assert next_status(d, 100, 0) is d


HDFC = "Rs.349.00 debited from A/c XX1234 on 03-10-26 to VPA zomato@hdfcbank. UPI Ref 412345678901"
HDFC_LABEL = {
    "isTransaction": True,
    "amount": "349.00",
    "direction": "DEBIT",
    "channel": "UPI",
    "status": "SUCCESS",
    "merchantRaw": "zomato@hdfcbank",
    "counterpartyVpa": "zomato@hdfcbank",
    "referenceId": "412345678901",
    "accountHint": "1234",
}


def _method(parse_outcome: object) -> ParseMethod:
    assert isinstance(parse_outcome, ParsedTransactionPayload)
    return parse_outcome.parse_method


async def test_active_rule_skips_teacher() -> None:
    teacher = FakeTeacher(HDFC_LABEL)
    parse = await parse_tiered(_event(HDFC), teacher, _seeds())
    assert isinstance(parse.outcome, ParsedTransactionPayload)
    assert parse.outcome.parse_method is ParseMethod.RULE
    assert parse.outcome.rule_id == "r_hdfc_upi_debit_v1"
    assert parse.outcome.amount_paise == 34900
    assert teacher.calls == 0
    assert parse.shadow is None
    assert parse.uncovered is None


async def test_shadow_compares_rule_with_teacher() -> None:
    agree = await parse_tiered(_event(HDFC), FakeTeacher(HDFC_LABEL), _seeds(), shadow=True)
    assert agree.shadow is not None
    assert agree.shadow.agreement
    assert [o.agreed for o in agree.observations] == [True]

    wrong = {**HDFC_LABEL, "referenceId": "412345678900"}
    disagree = await parse_tiered(_event(HDFC), FakeTeacher(wrong), _seeds(), shadow=True)
    assert disagree.shadow is not None
    assert not disagree.shadow.agreement
    assert [o.agreed for o in disagree.observations] == [False]
    assert _method(disagree.outcome) is ParseMethod.RULE  # tier 1 stays authoritative


async def test_candidate_rule_is_only_observed() -> None:
    teacher = FakeTeacher(HDFC_LABEL)
    parse = await parse_tiered(_event(HDFC), teacher, _seeds("CANDIDATE"))
    assert _method(parse.outcome) is ParseMethod.TEACHER_LLM
    assert teacher.calls == 1
    assert [(o.rule_id, o.agreed) for o in parse.observations] == [("r_hdfc_upi_debit_v1", True)]
    assert parse.uncovered is None


async def test_uncovered_template_is_reported_for_synthesis() -> None:
    text = "Sent Rs.120.00 from HDFC Bank A/c **1234 to chai@ybl on 03/10/26 Ref 512345678901"
    label = {
        **HDFC_LABEL,
        "amount": "120.00",
        "merchantRaw": "chai@ybl",
        "counterpartyVpa": "chai@ybl",
        "referenceId": "512345678901",
    }
    parse = await parse_tiered(_event(text), FakeTeacher(label), _seeds())
    assert _method(parse.outcome) is ParseMethod.TEACHER_LLM
    assert parse.uncovered is not None


async def test_rule_failing_validation_counts_as_mismatch() -> None:
    bad = compile_rule(
        "r_bad", "HDFCBK", rf"Rs\.{AMOUNT} debited", {"direction": "CREDIT"}, "ACTIVE"
    )
    parse = await parse_tiered(_event(HDFC), FakeTeacher(HDFC_LABEL), [bad])
    assert [(o.rule_id, o.agreed) for o in parse.observations] == [("r_bad", False)]
    assert _method(parse.outcome) is ParseMethod.TEACHER_LLM
    assert parse.uncovered is None  # a rule exists for this template


async def test_prefilter_runs_before_rules() -> None:
    parse = await parse_tiered(_event("482913 is OTP for txn of Rs 10"), FakeTeacher({}), _seeds())
    assert isinstance(parse.outcome, Dropped)
