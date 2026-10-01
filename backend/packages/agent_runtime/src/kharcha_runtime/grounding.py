"""Grounding for agent text (PROJECT_SPEC §16.4, §27.4).

Allowed numbers come only from this run's tool results: ``*Paise`` fields as rupees, other
integers as counts, ISO dates as day/month/year, and numbers written inside strings.
"""

import contextlib
import re
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from kharcha_common.grounding import (
    GroundingResult,
    allowed_values,
    check_grounding,
    extract_numbers,
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _walk(
    value: Any, key: str, paise: list[int], counts: list[int], dates: list[date], texts: list[str]
) -> None:
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, Mapping):
        for k, v in value.items():
            _walk(v, str(k), paise, counts, dates, texts)
    elif isinstance(value, list | tuple):
        for v in value:
            _walk(v, key, paise, counts, dates, texts)
    elif isinstance(value, int):
        (paise if key.endswith(("Paise", "_paise")) else counts).append(value)
    elif isinstance(value, float):
        counts.append(round(value))
        if 0 <= value <= 1:
            counts.append(round(value * 100))  # probabilities may be said as percent
    elif isinstance(value, str):
        if _ISO_DATE.match(value):
            with contextlib.suppress(ValueError):
                dates.append(date.fromisoformat(value[:10]))
        texts.append(value)


def allowed_from_results(results: Iterable[Mapping[str, Any]]) -> set[Decimal]:
    paise: list[int] = []
    counts: list[int] = []
    dates: list[date] = []
    texts: list[str] = []
    for result in results:
        _walk(result, "", paise, counts, dates, texts)
    allowed = allowed_values(paise=paise, counts=counts, dates=dates)
    for text in texts:
        allowed.update(value for _, value in extract_numbers(text))
    return allowed


def check_text(text: str, results: Iterable[Mapping[str, Any]]) -> GroundingResult:
    return check_grounding(text, allowed_from_results(results))
