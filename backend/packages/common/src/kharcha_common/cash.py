"""Cash wallet rules (PROJECT_SPEC §13)."""

import uuid
from dataclasses import dataclass

from kharcha_common.events import CashEntryType
from kharcha_common.ids import NAMESPACE_EVENTS
from kharcha_common.money import format_inr

INFLOW = frozenset({CashEntryType.ATM_WITHDRAWAL, CashEntryType.CASH_RECEIVED})
OUTFLOW = frozenset({CashEntryType.CASH_SPEND, CashEntryType.UNACCOUNTED})


@dataclass(frozen=True, slots=True)
class CashBalance:
    inflow_paise: int
    outflow_paise: int

    @property
    def balance_paise(self) -> int:
        return self.inflow_paise - self.outflow_paise

    def describe(self) -> str:
        """Never show a negative balance (§13)."""
        if self.balance_paise >= 0:
            return f"Cash in hand: {format_inr(self.balance_paise)}"
        return f"You logged {format_inr(-self.balance_paise)} more than tracked cash"


def ledger_id_for(cash_event_id: str) -> str:
    """Ledger row id derived from the cash event id (the notifier's Undo button uses it)."""
    return "c_" + uuid.uuid5(NAMESPACE_EVENTS, f"cash/{cash_event_id}").hex


def balance_from_totals(totals: dict[str, int]) -> CashBalance:
    inflow = sum(v for k, v in totals.items() if k in INFLOW)
    outflow = sum(v for k, v in totals.items() if k in OUTFLOW)
    return CashBalance(inflow_paise=inflow, outflow_paise=outflow)
