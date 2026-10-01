"""Broke-date Monte Carlo (PROJECT_SPEC §14): pure ``ForecastInput -> ForecastResult``.

Each run walks ``horizon`` days from today: money left = bank + cash, minus a discretionary
day bootstrapped from past days with the same weekday, plus scheduled recurring payments and
income. The broke day is the first day the total is <= 0. p20/p50/p80 of that day form the
range (p20 is the pessimistic end). Money stays int64 paise; totals reported as Python int.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import numpy.typing as npt

DEFAULT_RUNS = 2_000
DEFAULT_HORIZON = 45


@dataclass(frozen=True, slots=True)
class DaySample:
    """One past day of discretionary spend: total and the part per category (paise)."""

    weekday: int  # 0 = Monday
    by_category: Mapping[str, int]

    @property
    def total(self) -> int:
        return sum(self.by_category.values())


@dataclass(frozen=True, slots=True)
class Recurring:
    """A scheduled payment (negative) or income (positive) repeating every ``interval_days``."""

    name: str
    amount_paise: int  # signed: income > 0, payment < 0
    next_date: date
    interval_days: int
    category: str | None = None


@dataclass(frozen=True, slots=True)
class ForecastInput:
    today: date  # IST calendar day
    bank_paise: int
    cash_paise: int
    samples: Sequence[DaySample]
    recurring: Sequence[Recurring] = ()
    horizon_days: int = DEFAULT_HORIZON
    runs: int = DEFAULT_RUNS
    seed: int = 0


@dataclass(frozen=True, slots=True)
class ForecastResult:
    start_paise: int
    horizon_days: int
    runs: int
    broke_p20: date | None
    broke_p50: date | None
    broke_p80: date | None
    prob_broke: float  # share of runs that hit zero within the horizon
    end_p50_paise: int  # median money left at the end of the horizon
    daily_spend_p50_paise: int
    inputs: dict[str, object] = field(default_factory=dict)

    def days_left_p50(self, today: date) -> int | None:
        return None if self.broke_p50 is None else (self.broke_p50 - today).days


def _weekday_pools(
    samples: Sequence[DaySample], cut: Mapping[str, int]
) -> list[npt.NDArray[np.int64]]:
    """Per weekday, the past daily totals with ``cut`` percent removed from some categories."""
    pools: list[list[int]] = [[] for _ in range(7)]
    for s in samples:
        total = 0
        for category, amount in s.by_category.items():
            pct = cut.get(category, 0)
            total += amount - (amount * pct) // 100
        pools[s.weekday].append(total)
    every = [v for pool in pools for v in pool] or [0]
    return [np.array(pool or every, dtype=np.int64) for pool in pools]


def scheduled(recurring: Sequence[Recurring], today: date, horizon: int) -> npt.NDArray[np.int64]:
    """Net scheduled flow per day (index 0 = tomorrow)."""
    flow = np.zeros(horizon, dtype=np.int64)
    end = today + timedelta(days=horizon)
    for item in recurring:
        when = item.next_date
        while when <= today:
            when += timedelta(days=item.interval_days)
        while when <= end:
            flow[(when - today).days - 1] += item.amount_paise
            when += timedelta(days=item.interval_days)
    return flow


def forecast(inp: ForecastInput, cut: Mapping[str, int] | None = None) -> ForecastResult:
    """Run the simulation. ``cut`` maps category -> percent of its spend removed (what-if)."""
    cut = cut or {}
    horizon, runs = inp.horizon_days, inp.runs
    rng = np.random.default_rng(inp.seed)
    pools = _weekday_pools(inp.samples, cut)

    spend = np.empty((runs, horizon), dtype=np.int64)
    for day in range(horizon):
        pool = pools[(inp.today + timedelta(days=day + 1)).weekday()]
        spend[:, day] = pool[rng.integers(0, len(pool), size=runs)]

    start = inp.bank_paise + inp.cash_paise
    flow = scheduled(inp.recurring, inp.today, horizon)
    balance = start + np.cumsum(flow[np.newaxis, :] - spend, axis=1, dtype=np.int64)

    hit = balance <= 0
    broke_any = hit.any(axis=1)
    # Day index of the first hit; runs that never hit count as "after the horizon".
    first = np.where(broke_any, hit.argmax(axis=1) + 1, horizon + 1)

    def day_at(q: float) -> date | None:
        idx = int(np.ceil(np.quantile(first, q, method="lower")))
        return None if idx > horizon else inp.today + timedelta(days=idx)

    return ForecastResult(
        start_paise=int(start),
        horizon_days=horizon,
        runs=runs,
        broke_p20=day_at(0.2),
        broke_p50=day_at(0.5),
        broke_p80=day_at(0.8),
        prob_broke=round(float(broke_any.mean()), 4),
        end_p50_paise=int(np.median(balance[:, -1]).round()),
        daily_spend_p50_paise=int(np.median(spend).round()),
        inputs={
            "today": inp.today.isoformat(),
            "bankPaise": inp.bank_paise,
            "cashPaise": inp.cash_paise,
            "sampleDays": len(inp.samples),
            "recurring": [
                {
                    "name": r.name,
                    "amountPaise": r.amount_paise,
                    "nextDate": r.next_date.isoformat(),
                    "intervalDays": r.interval_days,
                }
                for r in inp.recurring
            ],
            "cut": dict(cut),
            "runs": runs,
            "horizonDays": horizon,
            "seed": inp.seed,
        },
    )


@dataclass(frozen=True, slots=True)
class WhatIf:
    category: str
    reduction_pct: int
    base: ForecastResult
    changed: ForecastResult

    @property
    def days_gained(self) -> int | None:
        """p50 days gained; None if neither run goes broke inside the horizon."""
        b, c = self.base.broke_p50, self.changed.broke_p50
        if b is None and c is None:
            return None
        horizon_end = date.fromisoformat(str(self.base.inputs["today"])) + timedelta(
            days=self.base.horizon_days + 1
        )
        return ((c or horizon_end) - (b or horizon_end)).days


def what_if(inp: ForecastInput, category: str, reduction_pct: int = 100) -> WhatIf:
    if not 0 <= reduction_pct <= 100:
        raise ValueError("reduction_pct must be 0..100")
    # Same seed: both runs draw the same days, so the difference is the cut alone.
    return WhatIf(category, reduction_pct, forecast(inp), forecast(inp, {category: reduction_pct}))
