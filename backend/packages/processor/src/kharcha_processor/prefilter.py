"""Tier 0 pre-filter (PROJECT_SPEC §10.1): drop messages that are not transactions."""

import re
from enum import StrEnum

from kharcha_processor.amounts import all_number_amounts


class DropReason(StrEnum):
    EMPTY = "EMPTY"
    OTP = "OTP"
    PROMO = "PROMO"
    BALANCE_ONLY = "BALANCE_ONLY"
    COLLECT_REQUEST = "COLLECT_REQUEST"
    NO_AMOUNT = "NO_AMOUNT"


_OTP = re.compile(r"\b(otp|one[\s-]?time[\s-]?password|verification code|security code)\b", re.I)
_PROMO = re.compile(
    r"(cashback (?:of )?up ?to|get up ?to|\bupto\b.*\boff\b|pre-?approved|apply now|"
    r"limited period|\bwin\b|\bexclusive offer|\bcoupon\b|click here|t&c apply)",
    re.I,
)
_COLLECT = re.compile(
    r"(has requested (?:money|rs|inr|₹)|collect request|requested a payment|approve the request)",
    re.I,
)
_BALANCE = re.compile(r"\b(avl\.? ?bal|available balance|a/c balance|account balance|bal:)", re.I)
_MOVEMENT = re.compile(
    r"\b(debited|credited|paid|sent|received|spent|withdrawn|withdrawal|purchase|transferred|"
    r"deposited|refund(?:ed)?|reversed|failed|declined|txn|transaction)\b",
    re.I,
)


def prefilter(text: str | None) -> DropReason | None:
    """Return a drop reason, or None if the message may be a transaction."""
    if text is None or not text.strip():
        return DropReason.EMPTY
    if _OTP.search(text):
        return DropReason.OTP
    if _COLLECT.search(text):
        return DropReason.COLLECT_REQUEST
    has_movement = _MOVEMENT.search(text) is not None
    if _PROMO.search(text) and not has_movement:
        return DropReason.PROMO
    if _BALANCE.search(text) and not has_movement:
        return DropReason.BALANCE_ONLY
    if not all_number_amounts(text):
        return DropReason.NO_AMOUNT
    return None
