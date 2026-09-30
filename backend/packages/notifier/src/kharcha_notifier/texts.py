"""Deterministic message templates. Numbers are formatted here, never by a model."""

from kharcha_common.events import CashEntryType
from kharcha_common.money import format_inr
from kharcha_notifier.queries import DaySummary

HELP = (
    "Log cash by typing it: '150 vada pav', 'chai 20', 'got 500 from mom', '1.2k shoes'.\n"
    "Commands: /summary /cash /undo /budget <category> <amount> /level mild|medium|savage|off"
)
LINK_FIRST = (
    "This chat is not linked yet. Get a code from the Kharcha app (Settings → Telegram) or "
    "`kharcha-admin link-code --user <id>`, then send /start <code>."
)


def summary_text(s: DaySummary) -> str:
    lines = [f"📊 Today, {s.day.day} {s.day.strftime('%b')}"]
    if s.payments == 0 and s.cash_spent_paise == 0:
        lines.append("No spends recorded today. 🙌")
    else:
        lines.append(
            f"Spent {format_inr(s.spent_paise)} in {s.payments} payment"
            f"{'' if s.payments == 1 else 's'}"
            + (f" + {format_inr(s.cash_spent_paise)} cash" if s.cash_spent_paise else "")
        )
        if s.top:
            lines.append("Top: " + ", ".join(f"{name} {format_inr(amt)}" for name, amt in s.top))
    lines.append(s.cash.describe())
    return "\n".join(lines)


_ENTRY_VERB = {
    CashEntryType.CASH_SPEND: "Logged cash spend",
    CashEntryType.CASH_RECEIVED: "Logged cash received",
    CashEntryType.ATM_WITHDRAWAL: "Logged ATM withdrawal",
    CashEntryType.UNACCOUNTED: "Logged unaccounted cash",
}


def cash_logged_text(
    entry_type: CashEntryType, amount_paise: int, note: str | None, category: str | None
) -> str:
    text = f"✅ {_ENTRY_VERB[entry_type]} {format_inr(amount_paise)}"
    if note:
        text += f" · {note}"
    if category and category != "CASH_UNCATEGORIZED":
        text += f" ({category.replace('_', ' ').lower()})"
    return text
