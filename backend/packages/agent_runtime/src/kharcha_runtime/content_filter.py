"""Content filter for proposals (PROJECT_SPEC §16.4, §22.2-22.3).

Roasts are about spending only: never body, appearance, health, family, caste, religion,
gender, or profanity. Matching is by word, English + common Hinglish.
"""

import re
from dataclasses import dataclass

_BLOCKED = {
    "BODY": ["fat", "moti", "mota", "ugly", "skinny", "weight", "belly", "face"],
    "HEALTH": ["diabetes", "diabetic", "sick", "disease", "depressed", "mental"],
    "FAMILY": [
        "your mom",
        "your mother",
        "your father",
        "your wife",
        "your husband",
        "maa",
        "baap",
    ],
    "IDENTITY": [
        "caste",
        "religion",
        "hindu",
        "muslim",
        "christian",
        "sikh",
        "dalit",
        "gay",
        "girly",
    ],
    "PROFANITY": ["fuck", "shit", "bitch", "bastard", "chutiya", "bc", "mc", "saala", "kamina"],
}
_PATTERNS = {
    reason: re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I)
    for reason, words in _BLOCKED.items()
}
MAX_CHARS = 400


@dataclass(frozen=True, slots=True)
class FilterResult:
    ok: bool
    reason: str | None = None


def check_content(text: str) -> FilterResult:
    if not text.strip():
        return FilterResult(False, "EMPTY")
    if len(text) > MAX_CHARS:
        return FilterResult(False, "TOO_LONG")
    for reason, pattern in _PATTERNS.items():
        if pattern.search(text):
            return FilterResult(False, reason)
    return FilterResult(True)
