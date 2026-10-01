"""Tier 1 parse rules (PROJECT_SPEC §10.1, §10.4): pure logic, no I/O.

A rule is a regex with named groups plus a static ``field_map``. Groups:
``amount`` (required), ``merchant``, ``vpa``, ``ref``, ``acct``, ``bal``, ``days``.
``field_map`` holds ``direction`` (required), ``channel`` and ``status``.

Lifecycle: CANDIDATE (shadow only) -> ACTIVE after 5 consistent matches -> DISABLED once
``mismatch_count > 2``. Seed rules start ACTIVE.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from kharcha_common.events import Channel, Direction, TxnStatus
from kharcha_processor.amounts import parse_amount_text
from kharcha_processor.extraction import ExtractionResult

PROMOTE_AFTER = 5
DISABLE_AFTER_MISMATCHES = 2  # disabled when mismatch_count > this
MAX_REGEX_LENGTH = 600
MAX_TEXT_LENGTH = 1000
GROUPS = frozenset({"amount", "merchant", "vpa", "ref", "acct", "bal", "days"})


class RuleStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class RuleOrigin(StrEnum):
    SEED = "SEED"
    SYNTHESIZED = "SYNTHESIZED"


class UnsafeRuleError(ValueError):
    """The regex or field map is rejected (§10.4 compile guard)."""


# A group that contains a quantifier and is itself quantified, e.g. (\w+)+ or (a*b)*.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")
_BACKREFERENCE = re.compile(r"\\[1-9]|\(\?P=")
_ESCAPES_AND_COUNTS = re.compile(r"\\.|\{\d+(?:,\d*)?\}")
# Literal data must not end up in a rule shared by all users (rule 7).
_LITERAL_DATA = re.compile(r"\d{3,}|[a-z0-9]@[a-z]", re.I)


@dataclass(frozen=True, slots=True)
class CompiledRule:
    id: str
    sender_key: str
    pattern: re.Pattern[str]
    direction: Direction
    channel: Channel
    status: TxnStatus
    rule_status: RuleStatus

    @property
    def is_active(self) -> bool:
        return self.rule_status is RuleStatus.ACTIVE


def check_rule(regex: str, field_map: dict[str, Any]) -> re.Pattern[str]:
    """Compile guard. Raises :class:`UnsafeRuleError` with a short reason."""
    if len(regex) > MAX_REGEX_LENGTH:
        raise UnsafeRuleError("too_long")
    if _NESTED_QUANTIFIER.search(regex):
        raise UnsafeRuleError("nested_quantifier")
    if _BACKREFERENCE.search(regex):
        raise UnsafeRuleError("backreference")
    if _LITERAL_DATA.search(_ESCAPES_AND_COUNTS.sub("", regex)):
        raise UnsafeRuleError("literal_data")
    try:
        pattern = re.compile(regex, re.IGNORECASE)
    except re.error as exc:
        raise UnsafeRuleError("does_not_compile") from exc
    names = set(pattern.groupindex)
    if "amount" not in names:
        raise UnsafeRuleError("no_amount_group")
    if names - GROUPS:
        raise UnsafeRuleError("unknown_group")
    if field_map.get("direction") not in Direction.__members__:
        raise UnsafeRuleError("bad_direction")
    if field_map.get("channel", "UNKNOWN") not in Channel.__members__:
        raise UnsafeRuleError("bad_channel")
    if field_map.get("status", "SUCCESS") not in TxnStatus.__members__:
        raise UnsafeRuleError("bad_status")
    if set(field_map) - {"direction", "channel", "status"}:
        raise UnsafeRuleError("unknown_field")
    return pattern


def compile_rule(
    rule_id: str, sender_key: str, regex: str, field_map: dict[str, Any], status: str
) -> CompiledRule:
    return CompiledRule(
        id=rule_id,
        sender_key=sender_key,
        pattern=check_rule(regex, field_map),
        direction=Direction(field_map["direction"]),
        channel=Channel(field_map.get("channel", "UNKNOWN")),
        status=TxnStatus(field_map.get("status", "SUCCESS")),
        rule_status=RuleStatus(status),
    )


def apply_rule(rule: CompiledRule, text: str) -> ExtractionResult | None:
    """Extraction for ``text``, or None if the rule does not match."""
    if len(text) > MAX_TEXT_LENGTH:
        return None
    match = rule.pattern.search(text)
    if match is None:
        return None
    groups = {k: (v.strip() if v else None) for k, v in match.groupdict().items()}
    vpa = groups.get("vpa")
    days = groups.get("days")
    return ExtractionResult(
        is_transaction=True,
        amount=groups["amount"],
        direction=rule.direction,
        channel=rule.channel,
        status=rule.status,
        merchant_raw=groups.get("merchant") or vpa,
        counterparty_vpa=vpa,
        reference_id=groups.get("ref"),
        account_hint=groups.get("acct"),
        balance_after=groups.get("bal"),
        promised_refund_days=int(days) if days and days.isdigit() else None,
    )


def _paise(text: str | None) -> int | None:
    if text is None:
        return None
    try:
        return parse_amount_text(text)
    except ValueError:
        return None


def _norm(text: str | None) -> str | None:
    return text.strip().lower() if text and text.strip() else None


def same_extraction(a: ExtractionResult, b: ExtractionResult) -> bool:
    """Do two tiers agree on every field the rule can produce?"""
    return (
        a.is_transaction == b.is_transaction
        and _paise(a.amount) == _paise(b.amount)
        and a.direction == b.direction
        and a.status == b.status
        and _norm(a.merchant_raw) == _norm(b.merchant_raw)
        and _norm(a.reference_id) == _norm(b.reference_id)
        and (a.account_hint or "")[-4:] == (b.account_hint or "")[-4:]
        and _paise(a.balance_after) == _paise(b.balance_after)
    )


def next_status(status: RuleStatus, matches: int, mismatches: int) -> RuleStatus:
    """Status after counters change (§10.4)."""
    if status is RuleStatus.DISABLED:
        return status
    if mismatches > DISABLE_AFTER_MISMATCHES:
        return RuleStatus.DISABLED
    if status is RuleStatus.CANDIDATE and matches >= PROMOTE_AFTER and mismatches == 0:
        return RuleStatus.ACTIVE
    return status
