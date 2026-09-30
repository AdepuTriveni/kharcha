"""Policy gate v0 (PROJECT_SPEC §22.2): the final authority on what reaches a user.

W3 implements caps, quiet hours, dedupe (via ``alerts_sent``) and roastability. The content
filter, NOT_FAIR de-escalation, grounding for agent output and PLAIN experiment rewriting
arrive with the agent runtime (W8).
"""

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum

from kharcha_common.categories import ROASTABLE, Category, Roastable
from kharcha_common.time import to_ist

MAX_ROASTS_PER_DAY = 1
MAX_ROASTS_PER_WEEK = 4
MAX_NON_URGENT_PER_DAY = 3


class AlertKind(StrEnum):
    ROAST = "ROAST"  # humorous nudge about spending
    NUDGE = "NUDGE"  # plain, non-urgent nudge
    SUMMARY = "SUMMARY"  # scheduled digest the user asked for
    CONFIRMATION = "CONFIRMATION"  # direct answer to the user's own action


class RoastLevel(StrEnum):
    OFF = "OFF"
    MILD = "MILD"
    MEDIUM = "MEDIUM"
    SAVAGE = "SAVAGE"


class Decision(StrEnum):
    SEND = "SEND"
    SEND_PLAIN = "SEND_PLAIN"
    SUPPRESS = "SUPPRESS"


@dataclass(frozen=True, slots=True)
class Counts:
    roasts_today: int = 0
    roasts_week: int = 0
    non_urgent_today: int = 0


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: Decision
    reason: str | None = None


ROAST_OK = frozenset({Roastable.YES, Roastable.GENTLY})


def in_quiet_hours(now: datetime, start: time, end: time) -> bool:
    local = to_ist(now).time()
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def decide(
    *,
    kind: AlertKind,
    now: datetime,
    roast_level: RoastLevel,
    quiet_start: time,
    quiet_end: time,
    counts: Counts,
    category: Category | None = None,
) -> Verdict:
    if kind is AlertKind.CONFIRMATION:
        return Verdict(Decision.SEND)
    if in_quiet_hours(now, quiet_start, quiet_end):
        return Verdict(Decision.SUPPRESS, "QUIET_HOURS")
    if kind is AlertKind.SUMMARY:
        return Verdict(Decision.SEND)
    if counts.non_urgent_today >= MAX_NON_URGENT_PER_DAY:
        return Verdict(Decision.SUPPRESS, "DAILY_CAP")
    if kind is AlertKind.NUDGE:
        return Verdict(Decision.SEND)

    # Roasts: downgrade to a plain nudge whenever roasting is not allowed.
    if roast_level is RoastLevel.OFF:
        return Verdict(Decision.SEND_PLAIN, "ROAST_OFF")
    if category is None or ROASTABLE.get(category) not in ROAST_OK:
        return Verdict(Decision.SEND_PLAIN, "NOT_ROASTABLE")
    if counts.roasts_today >= MAX_ROASTS_PER_DAY or counts.roasts_week >= MAX_ROASTS_PER_WEEK:
        return Verdict(Decision.SEND_PLAIN, "ROAST_CAP")
    return Verdict(Decision.SEND)
