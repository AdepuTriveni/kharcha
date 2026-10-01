"""Cash Detective (PROJECT_SPEC §17.4): one smart question about cash that went unlogged.

The question and every breakdown come from tools (withdrawal facts + the user's cash habits),
so the amounts are exact and sum to the unaccounted cash. Buttons: up to 3 plausible
breakdowns, [Other], [Don't remember]. A tap becomes cash entries (notifier).
"""

import json
from collections.abc import Mapping
from typing import Any

from kharcha_common.events import (
    AgentTaskPayload,
    ButtonAction,
    CashSplit,
    Proposal,
    ProposalType,
)
from kharcha_common.money import format_inr
from kharcha_common.prompts import load_prompt
from kharcha_runtime.types import RunContext, ToolExecutor

DEFAULT_GUESSES = ("DINING_OUT", "TRANSPORT", "SHOPPING")
LABELS = {
    "DINING_OUT": "food & chai",
    "TRANSPORT": "autos",
    "GROCERIES": "groceries",
    "SHOPPING": "shopping",
    "ENTERTAINMENT": "outings",
    "CASH_UNCATEGORIZED": "misc",
}


def goal_text(task: AgentTaskPayload, roast_level: str) -> str:
    refs = {k: v for k, v in task.context_refs.items() if v is not None}
    return (
        load_prompt("cash_detective", "v1")
        .section("user")
        .replace("{goal}", task.goal)
        .replace("{refs}", json.dumps(refs, ensure_ascii=False))
    )


def _round10(paise: int) -> int:
    return max(1_000, (paise // 1_000) * 1_000)  # whole ₹10 steps


def split(total: int, weights: list[tuple[str, int]]) -> list[CashSplit]:
    """Split ``total`` paise by weights into ₹10 steps; the last part takes the remainder."""
    if total <= 0 or not weights:
        return []
    weight_sum = sum(w for _, w in weights) or 1
    parts: list[CashSplit] = []
    left = total
    for i, (category, weight) in enumerate(weights):
        if i == len(weights) - 1:
            amount = left
        else:
            amount = min(
                left - 1_000 * (len(weights) - i - 1), _round10(total * weight // weight_sum)
            )
        if amount <= 0:
            continue
        parts.append(CashSplit(category=category, amount_paise=amount))
        left -= amount
    return parts


def _label(parts: list[CashSplit]) -> str:
    text = " + ".join(
        f"{LABELS.get(p.category, p.category.lower())} {format_inr(p.amount_paise)}" for p in parts
    )
    return text.replace(".00", "")[:40]


def breakdowns(unaccounted: int, habits: list[Mapping[str, Any]]) -> list[list[CashSplit]]:
    cats = [
        (str(h["category"]), int(h["totalPaise"]))
        for h in habits
        if int(h.get("totalPaise", 0)) > 0
    ]
    if not cats:
        cats = [(c, 1) for c in DEFAULT_GUESSES]
    options = [split(unaccounted, cats[:1])]
    if len(cats) >= 2:
        options.append(split(unaccounted, cats[:2]))
    if len(cats) >= 3:
        options.append(split(unaccounted, cats[:3]))
    else:
        fallback = next((c for c in DEFAULT_GUESSES if c not in {x for x, _ in cats}), None)
        if fallback:
            options.append(split(unaccounted, [(fallback, 1)]))
    seen: set[str] = set()
    unique = []
    for option in options:
        key = _label(option)
        if option and key not in seen:
            seen.add(key)
            unique.append(option)
    return unique[:3]


async def fallback(task: AgentTaskPayload, tools: ToolExecutor, ctx: RunContext) -> list[Proposal]:
    ledger_id = task.context_refs.get("cashLedgerId")
    if not ledger_id:
        return []
    w = await tools.call(ctx, "get_withdrawal", {"cashLedgerId": ledger_id})
    if not w.ok or int(w.data.get("unaccountedPaise", 0)) < 5_000:
        return []  # less than ₹50 unaccounted: not worth asking
    unaccounted = int(w.data["unaccountedPaise"])
    habits = await tools.call(ctx, "get_cash_habits", {"days": 60})
    options = breakdowns(unaccounted, list(habits.data.get("habits", [])) if habits.ok else [])
    actions = [ButtonAction(label=_label(o), kind="CASH_ENTRIES", cash_entries=o) for o in options]
    actions.append(ButtonAction(label="Other", kind="OTHER"))
    actions.append(ButtonAction(label="Don't remember", kind="DONT_REMEMBER"))
    text = (
        f"{w.data['unaccountedText']} of your {w.data['withdrawnText']} ATM cash isn't logged yet. "
        "Where did it go?"
    )
    return [
        Proposal(
            type=ProposalType.QUESTION,
            text=text,
            category="CASH_UNCATEGORIZED",
            buttons=[a.label for a in actions],
            reason="missing cash",
            templated=True,
            button_actions=actions,
            refs={"cashLedgerId": str(ledger_id), "unaccountedPaise": str(unaccounted)},
        )
    ]
