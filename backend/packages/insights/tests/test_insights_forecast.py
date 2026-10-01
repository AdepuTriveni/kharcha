from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from kharcha_insights.forecast.backtest import run_backtest, synthetic_cases
from kharcha_insights.forecast.model import (
    DaySample,
    ForecastInput,
    Recurring,
    forecast,
    scheduled,
    what_if,
)
from kharcha_insights.forecast.recurring import PastTxn, detect_recurring

TODAY = date(2026, 10, 1)  # Thursday


def _flat(per_day: int, days: int = 28, category: str = "FOOD_DELIVERY") -> list[DaySample]:
    start = TODAY - timedelta(days=days)
    return [
        DaySample((start + timedelta(days=i)).weekday(), {category: per_day}) for i in range(days)
    ]


def _input(
    bank: int, samples: list[DaySample], recurring: list[Recurring] | None = None
) -> ForecastInput:
    return ForecastInput(
        today=TODAY,
        bank_paise=bank,
        cash_paise=0,
        samples=samples,
        recurring=recurring or [],
        runs=300,
    )


def test_constant_spend_gives_exact_broke_day() -> None:
    result = forecast(_input(100_000, _flat(10_000)))  # ₹1,000 lasts 10 days at ₹100/day
    assert result.broke_p20 == result.broke_p50 == result.broke_p80 == TODAY + timedelta(days=10)
    assert result.prob_broke == 1.0
    assert result.daily_spend_p50_paise == 10_000


def test_cash_counts_and_no_spend_never_breaks() -> None:
    with_cash = ForecastInput(TODAY, 50_000, 50_000, _flat(10_000), runs=100)
    assert forecast(with_cash).broke_p50 == TODAY + timedelta(days=10)
    never = forecast(_input(100_000, _flat(0)))
    assert never.broke_p50 is None
    assert never.prob_broke == 0.0
    assert never.end_p50_paise == 100_000


def test_salary_and_rent_are_scheduled() -> None:
    salary = Recurring("payroll", 3_000_000, TODAY + timedelta(days=5), 30)
    rent = Recurring("rent", -1_500_000, TODAY - timedelta(days=28), 30)  # next: day 2
    flow = scheduled([salary, rent], TODAY, 45)
    assert flow[1] == -1_500_000
    assert flow[4] == 3_000_000
    assert flow[34] == 3_000_000
    result = forecast(_input(1_000_000, _flat(10_000), recurring=[salary, rent]))
    # ₹10,000 - ₹15,000 rent on day 2 -> broke on day 2.
    assert result.broke_p50 == TODAY + timedelta(days=2)


def test_what_if_cutting_a_category_gains_days() -> None:
    samples = [DaySample(s.weekday, {"FOOD_DELIVERY": 6_000, "GROCERIES": 4_000}) for s in _flat(0)]
    inp = _input(100_000, samples)
    result = what_if(inp, "FOOD_DELIVERY", 100)
    assert result.base.broke_p50 == TODAY + timedelta(days=10)
    assert result.changed.broke_p50 == TODAY + timedelta(days=25)
    assert result.days_gained == 15
    half = what_if(inp, "FOOD_DELIVERY", 50)
    assert half.days_gained is not None
    assert 0 < half.days_gained < 15


def test_weekday_bootstrap_uses_matching_days() -> None:
    # Spend only on Sundays: ₹500 each. Today is Thursday; Sundays are days 3, 10, ...
    samples = [DaySample(d % 7, {"DINING_OUT": 50_000 if d % 7 == 6 else 0}) for d in range(28)]
    result = forecast(_input(100_000, samples))
    assert result.broke_p50 == TODAY + timedelta(days=10)


@settings(max_examples=25, deadline=None)
@given(
    bank=st.integers(min_value=1, max_value=5_000_000),
    extra=st.integers(min_value=0, max_value=5_000_000),
    spends=st.lists(st.integers(min_value=0, max_value=200_000), min_size=7, max_size=30),
)
def test_more_money_never_breaks_sooner(bank: int, extra: int, spends: list[int]) -> None:
    samples = [DaySample(i % 7, {"SHOPPING": s}) for i, s in enumerate(spends)]
    a = forecast(_input(bank, samples))
    b = forecast(_input(bank + extra, samples))
    far = TODAY + timedelta(days=1000)
    assert (b.broke_p50 or far) >= (a.broke_p50 or far)
    assert what_if(_input(bank, samples), "SHOPPING", 30).days_gained in (None, *range(0, 100))


def test_detect_weekly_and_monthly_with_tolerance() -> None:
    def txn(i: int, key: str, amount: int, day: date) -> PastTxn:
        return PastTxn(f"{key}{i}", key, key, amount, day)

    gym = [
        txn(i, "gym", -50_000 - i * 1_000, TODAY - timedelta(days=7 * i + (i % 2)))
        for i in range(4)
    ]
    salary = [txn(i, "acme", 4_000_000, TODAY - timedelta(days=30 * i + 2)) for i in range(2)]
    random = [txn(i, "zomato", -30_000 * (i + 1), TODAY - timedelta(days=3 * i)) for i in range(4)]
    found, used = detect_recurring(gym + salary + random)
    by_name = {r.name: r for r in found}
    assert set(by_name) == {"gym", "acme"}
    assert by_name["gym"].interval_days == 7
    assert by_name["acme"].interval_days == 30
    assert by_name["acme"].next_date == TODAY + timedelta(days=28)
    assert by_name["acme"].amount_paise == 4_000_000
    assert used == {t.id for t in gym + salary}


def test_backtest_on_synthetic_cohort_meets_target_band() -> None:
    report = run_backtest(synthetic_cases(60, seed=3), runs=500)
    data = report.to_dict()
    assert data["cases"] == 60
    assert report.broke_cases > 10
    assert report.accuracy_3d >= 0.5
    assert report.detection_agreement >= 0.85
