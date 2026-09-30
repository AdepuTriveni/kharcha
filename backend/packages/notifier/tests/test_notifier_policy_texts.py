from datetime import UTC, date, datetime, time

import pytest

from kharcha_common.cash import CashBalance
from kharcha_common.categories import Category
from kharcha_common.events import AgentTrigger, CashEntryType
from kharcha_notifier.coach_v0 import Facts, compose, plain_text
from kharcha_notifier.policy import (
    AlertKind,
    Counts,
    Decision,
    RoastLevel,
    decide,
    in_quiet_hours,
)
from kharcha_notifier.queries import DaySummary
from kharcha_notifier.texts import cash_logged_text, summary_text

NOON_IST = datetime(2026, 10, 3, 6, 30, tzinfo=UTC)  # 12:00 IST
LATE_IST = datetime(2026, 10, 3, 17, 30, tzinfo=UTC)  # 23:00 IST
Q_START, Q_END = time(22, 0), time(8, 0)


def _decide(kind: AlertKind, **kw: object) -> Decision:
    args: dict[str, object] = {
        "kind": kind,
        "now": NOON_IST,
        "roast_level": RoastLevel.MEDIUM,
        "quiet_start": Q_START,
        "quiet_end": Q_END,
        "counts": Counts(),
        "category": Category.FOOD_DELIVERY,
    }
    args.update(kw)
    return decide(**args).decision  # type: ignore[arg-type]


def test_quiet_hours_wrap_midnight() -> None:
    assert in_quiet_hours(LATE_IST, Q_START, Q_END)
    assert not in_quiet_hours(NOON_IST, Q_START, Q_END)
    assert in_quiet_hours(datetime(2026, 10, 3, 1, 0, tzinfo=UTC), Q_START, Q_END)  # 06:30 IST


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, Decision.SEND),
        ({"now": LATE_IST}, Decision.SUPPRESS),
        ({"roast_level": RoastLevel.OFF}, Decision.SEND_PLAIN),
        ({"category": Category.RENT}, Decision.SEND_PLAIN),
        ({"category": Category.TRANSFERS}, Decision.SEND_PLAIN),
        ({"category": None}, Decision.SEND_PLAIN),
        ({"counts": Counts(roasts_today=1)}, Decision.SEND_PLAIN),
        ({"counts": Counts(roasts_week=4)}, Decision.SEND_PLAIN),
        ({"counts": Counts(non_urgent_today=3)}, Decision.SUPPRESS),
    ],
)
def test_roast_policy(kwargs: dict[str, object], expected: Decision) -> None:
    assert _decide(AlertKind.ROAST, **kwargs) is expected


def test_confirmations_and_summaries() -> None:
    assert _decide(AlertKind.CONFIRMATION, now=LATE_IST) is Decision.SEND
    assert _decide(AlertKind.SUMMARY, counts=Counts(non_urgent_today=9)) is Decision.SEND
    assert _decide(AlertKind.SUMMARY, now=LATE_IST) is Decision.SUPPRESS


def test_summary_text() -> None:
    summary = DaySummary(
        day=date(2026, 10, 3),
        spent_paise=123400,
        payments=5,
        cash_spent_paise=15000,
        top=[("zomato@hdfcbank", 70000)],
        cash=CashBalance(100000, 15000),
    )
    assert summary_text(summary) == (
        "📊 Today, 3 Oct\nSpent ₹1,234.00 in 5 payments + ₹150.00 cash\n"
        "Top: zomato@hdfcbank ₹700.00\nCash in hand: ₹850.00"
    )


def test_cash_logged_text() -> None:
    text = cash_logged_text(CashEntryType.CASH_SPEND, 15000, "vada pav", "DINING_OUT")
    assert text == "✅ Logged cash spend ₹150.00 · vada pav (dining out)"


FACTS = Facts(AgentTrigger.FREQUENCY, Category.FOOD_DELIVERY, "zomato", 142000, count=4)


class FakeModel:
    def __init__(self, text: str) -> None:
        self.text = text

    async def write(self, prompt: object, level: object, facts: object) -> str:
        return self.text


async def test_compose_uses_grounded_model_text() -> None:
    from kharcha_common.prompts import load_prompt

    good = "Bro, 4 zomato orders this week, ₹1,420 gone. Cook once?"
    prompt = load_prompt("coach", "v0")
    assert await compose(FakeModel(good), prompt, RoastLevel.MEDIUM, FACTS) == good


async def test_compose_rejects_ungrounded_text() -> None:
    from kharcha_common.prompts import load_prompt

    bad = "₹2,000 on zomato this week!"
    text = await compose(FakeModel(bad), load_prompt("coach", "v0"), RoastLevel.MEDIUM, FACTS)
    assert text == plain_text(FACTS)
    assert "₹1,420.00" in text
    assert "4 payments" in text
