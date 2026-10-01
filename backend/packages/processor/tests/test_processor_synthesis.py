from kharcha_common.events import Channel, Direction, TxnStatus
from kharcha_processor.extraction import ExtractionResult
from kharcha_processor.handlers import in_shadow_sample
from kharcha_processor.synthesis import (
    Cooldown,
    ProposedRule,
    Sample,
    rule_id_for,
    validate_proposal,
)

AMOUNT = r"(?P<amount>[\d,]+(?:\.\d{1,2})?)"
GOOD = (
    rf"Sent Rs\.?{AMOUNT} from HDFC Bank A/c \*\*(?P<acct>\d{{4}}) to (?P<vpa>[\w.\-]+@\w+)"
    r" on [\d/]+ Ref (?P<ref>\d{6,})"
)
FIELD_MAP = {"direction": "DEBIT", "channel": "UPI", "status": "SUCCESS"}


def _sample(amount: str, vpa: str, ref: str) -> Sample:
    text = f"Sent Rs.{amount} from HDFC Bank A/c **1234 to {vpa} on 03/10/26 Ref {ref}"
    return Sample(
        text,
        ExtractionResult(
            is_transaction=True,
            amount=amount,
            direction=Direction.DEBIT,
            channel=Channel.UPI,
            status=TxnStatus.SUCCESS,
            merchant_raw=vpa,
            counterparty_vpa=vpa,
            reference_id=ref,
            account_hint="1234",
        ),
    )


SAMPLES = [_sample("120.00", "chai@ybl", "512345678901"), _sample("1,050", "ola@axl", "51234567")]


def test_valid_proposal_is_accepted_with_stable_id() -> None:
    rule_id, reason = validate_proposal(
        "HDFCBK", ProposedRule(regex=GOOD, field_map=FIELD_MAP), SAMPLES
    )
    assert reason is None
    assert rule_id == rule_id_for("HDFCBK", GOOD)
    assert rule_id.startswith("r_syn_")


def test_proposals_that_do_not_reproduce_samples_are_rejected() -> None:
    def check(regex: str, field_map: dict[str, str] = FIELD_MAP) -> str | None:
        proposal = ProposedRule(regex=regex, field_map=field_map)
        return validate_proposal("HDFCBK", proposal, SAMPLES)[1]

    assert check(GOOD.replace("Sent", "Paid")) == "no_match"
    assert check(GOOD.replace(r"(?P<ref>\d{6,})", r"(?P<ref>\d{4})")) == "fields_differ"
    assert check(GOOD, {**FIELD_MAP, "channel": "CARD"}) == "channel_differs"
    assert check(GOOD, {**FIELD_MAP, "direction": "CREDIT"}) == "fields_differ"
    assert check(GOOD.replace(r"(?P<vpa>[\w.\-]+@\w+)", "chai@ybl")) == "literal_data"


def test_cooldown_blocks_repeat_attempts() -> None:
    cooldown = Cooldown(seconds=3600)
    assert cooldown.ready("t1")
    assert not cooldown.ready("t1")
    assert cooldown.ready("t2")
    assert Cooldown(seconds=0).ready("t1")


def test_shadow_sample_is_deterministic_and_near_rate() -> None:
    ids = [f"0192f0c0-0000-7000-8000-{i:012d}" for i in range(2000)]
    picked = [i for i in ids if in_shadow_sample(i, 0.1)]
    assert 150 < len(picked) < 250
    assert picked == [i for i in ids if in_shadow_sample(i, 0.1)]
    assert not any(in_shadow_sample(i, 0.0) for i in ids)
    assert all(in_shadow_sample(i, 1.0) for i in ids)
