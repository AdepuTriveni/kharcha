"""Forecast backtest (PROJECT_SPEC §14, §27.2): ±3-day accuracy and MAE in days.

A case is a history the forecaster sees plus the future that actually happened. Until enough
real histories exist, ``synthetic_cases`` draws personas (salary, rent, subscriptions, noisy
daily spend with weekend bumps) whose real future comes from the same process.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from kharcha_insights.forecast.model import DaySample, ForecastInput, forecast
from kharcha_insights.forecast.recurring import PastTxn, detect_recurring

HISTORY_DAYS = 60
TOLERANCE_DAYS = 3


@dataclass(frozen=True, slots=True)
class Case:
    name: str
    today: date
    start_paise: int
    history: Sequence[PastTxn]  # signed amounts, includes recurring items
    future_net: Sequence[int]  # actual net flow per future day (index 0 = tomorrow)

    def actual_broke(self) -> date | None:
        money = self.start_paise
        for i, net in enumerate(self.future_net):
            money += net
            if money <= 0:
                return self.today + timedelta(days=i + 1)
        return None


@dataclass(frozen=True, slots=True)
class BacktestReport:
    cases: int
    broke_cases: int
    within_3d: int
    mae_days: float
    detection_agreement: float  # forecast and reality agree on "broke within horizon or not"

    @property
    def accuracy_3d(self) -> float:
        return self.within_3d / self.broke_cases if self.broke_cases else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "cases": self.cases,
            "brokeCases": self.broke_cases,
            "accuracy3d": round(self.accuracy_3d, 4),
            "maeDays": round(self.mae_days, 2),
            "detectionAgreement": round(self.detection_agreement, 4),
        }


def to_input(case: Case, runs: int, horizon: int, seed: int) -> ForecastInput:
    recurring, explained = detect_recurring(case.history)
    by_day: dict[date, dict[str, int]] = {}
    for t in case.history:
        if t.amount_paise < 0 and t.id not in explained:
            day = by_day.setdefault(t.day, {})
            key = t.category or "OTHER"
            day[key] = day.get(key, 0) - t.amount_paise
    start = case.today - timedelta(days=HISTORY_DAYS)
    samples = [
        DaySample(d.weekday(), by_day.get(d, {}))
        for d in (start + timedelta(days=i) for i in range(HISTORY_DAYS))
    ]
    return ForecastInput(
        today=case.today,
        bank_paise=case.start_paise,
        cash_paise=0,
        samples=samples,
        recurring=recurring,
        horizon_days=horizon,
        runs=runs,
        seed=seed,
    )


def run_backtest(
    cases: Sequence[Case], *, runs: int = 2_000, horizon: int = 45, seed: int = 0
) -> BacktestReport:
    broke = within = agree = 0
    errors: list[int] = []
    for case in cases:
        result = forecast(to_input(case, runs, horizon, seed))
        actual = case.actual_broke()
        predicted = result.broke_p50
        agree += (actual is None) == (predicted is None)
        if actual is None:
            continue
        broke += 1
        end = case.today + timedelta(days=horizon + 1)
        error = abs(((predicted or end) - actual).days)
        errors.append(error)
        within += error <= TOLERANCE_DAYS
    return BacktestReport(
        cases=len(cases),
        broke_cases=broke,
        within_3d=within,
        mae_days=float(np.mean(errors)) if errors else 0.0,
        detection_agreement=agree / len(cases) if cases else 0.0,
    )


def _persona(rng: np.random.Generator, index: int, today: date, horizon: int) -> Case:
    salary = int(rng.integers(25_000, 90_000)) * 100
    rent = int(salary * rng.uniform(0.2, 0.4)) // 100 * 100
    subscription = int(rng.choice([19_900, 49_900, 64_900]))
    weekday_mean = int(salary * rng.uniform(0.012, 0.03))
    salary_day = int(rng.integers(1, 6))
    start = int(salary * rng.uniform(0.1, 0.9))

    def day_spend(day: date) -> int:
        bump = 1.6 if day.weekday() >= 5 else 1.0
        spend = rng.gamma(2.0, weekday_mean * bump / 2.0)
        return int(spend) // 100 * 100

    def scheduled(day: date) -> list[tuple[str, int, str]]:
        items = []
        if day.day == salary_day:
            items.append(("ACME PAYROLL", salary, "INCOME"))
        if day.day == salary_day + 2:
            items.append(("rent", -rent, "RENT"))
        if day.day == 15:
            items.append(("netflix", -subscription, "SUBSCRIPTIONS"))
        return items

    history: list[PastTxn] = []
    for i in range(HISTORY_DAYS, 0, -1):
        day = today - timedelta(days=i)
        for j, (name, amount, category) in enumerate(scheduled(day)):
            history.append(PastTxn(f"{index}-{i}-s{j}", name, name, amount, day, category))
        if (spend := day_spend(day)) > 0:
            history.append(
                PastTxn(f"{index}-{i}-d", f"shop{i % 9}", "shop", -spend, day, "SHOPPING")
            )

    future = []
    for i in range(1, horizon + 1):
        day = today + timedelta(days=i)
        future.append(sum(a for _, a, _ in scheduled(day)) - day_spend(day))
    return Case(f"persona-{index}", today, start, history, future)


def synthetic_cases(n: int = 200, seed: int = 7, horizon: int = 45) -> list[Case]:
    rng = np.random.default_rng(seed)
    today = date(2026, 10, 1)
    return [
        _persona(rng, i, today + timedelta(days=int(rng.integers(0, 28))), horizon)
        for i in range(n)
    ]
