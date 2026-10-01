"""Coach v1 (PROJECT_SPEC §17.2): goal text for the runtime and deterministic fallbacks.

The fallback runs when no model is configured or the run fails. It reads the same tools as the
model (never the database), so its numbers are grounded by construction.
"""

import json
from collections.abc import Mapping
from typing import Any

from kharcha_common.events import AgentTaskPayload, AgentTrigger, Proposal, ProposalType
from kharcha_common.money import format_inr
from kharcha_common.prompts import load_prompt
from kharcha_runtime.types import RunContext, ToolExecutor

ROAST_LEVELS = ("OFF", "MILD", "MEDIUM", "SAVAGE")


def goal_text(task: AgentTaskPayload, roast_level: str) -> str:
    prompt = load_prompt("coach", "v1")
    refs = {k: v for k, v in task.context_refs.items() if v is not None}
    return (
        prompt.section("user")
        .replace("{goal}", task.goal)
        .replace("{trigger}", task.trigger.value)
        .replace("{level}", roast_level if roast_level in ROAST_LEVELS else "MEDIUM")
        .replace("{refs}", json.dumps(refs, ensure_ascii=False))
    )


def _day(iso: str) -> str:
    _, month, day = iso.split("-")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{int(day)} {months[int(month) - 1]}"


def _nudge(text: str, reason: str, category: str | None = None) -> Proposal:
    return Proposal(
        type=ProposalType.NUDGE, text=text, category=category, reason=reason, templated=True
    )


async def _data(tools: ToolExecutor, ctx: RunContext, name: str, **args: Any) -> Mapping[str, Any]:
    result = await tools.call(ctx, name, args)
    return result.data if result.ok else {}


async def fallback(task: AgentTaskPayload, tools: ToolExecutor, ctx: RunContext) -> list[Proposal]:
    refs = task.context_refs
    if task.trigger is AgentTrigger.FREQUENCY and refs.get("merchant"):
        merchant = str(refs["merchant"])
        items = await _data(tools, ctx, "list_transactions", days=7, merchant=merchant, limit=20)
        spends = [i for i in items.get("items", []) if i.get("direction") == "DEBIT"]
        if not spends:
            return []
        total = sum(int(i["amountPaise"]) for i in spends)
        return [
            _nudge(
                f"{merchant}: {len(spends)} payments in the last 7 days, {format_inr(total)} "
                "in total. Skipping the next one keeps that money with you.",
                "frequency trigger",
                str(refs.get("category") or "") or None,
            )
        ]
    if task.trigger is AgentTrigger.BUDGET and refs.get("category"):
        budgets = (await _data(tools, ctx, "get_budgets")).get("budgets", [])
        match = next((b for b in budgets if b["category"] == refs["category"]), None)
        if match is None:
            return []
        label = str(match["category"]).replace("_", " ").lower()
        return [
            _nudge(
                f"{label}: {format_inr(match['spentThisMonthPaise'])} spent this month, "
                f"{match['percentUsed']}% of your {format_inr(match['monthlyLimitPaise'])} "
                "budget. Slow down for the rest of the month.",
                "budget trigger",
                str(match["category"]),
            )
        ]
    if task.trigger is AgentTrigger.BROKE_DATE_MOVED:
        f = await _data(tools, ctx, "get_forecast")
        if f.get("status") != "OK" or not f.get("brokeP50"):
            return []
        return [
            _nudge(
                f"Heads up: at this pace your money runs out around {_day(f['brokeP50'])} "
                f"(about {f['daysLeftP50']} days). You have {format_inr(f['moneyNowPaise'])} "
                "now, cash included.",
                "broke date moved earlier",
            )
        ]
    if task.trigger is AgentTrigger.WEEKLY_REVIEW:
        s = await _data(tools, ctx, "get_weekly_summary", week="this")
        if not s or not s.get("payments"):
            return []
        text = f"This week so far: {format_inr(s['spentPaise'])} over {s['payments']} payments."
        if s.get("topMerchants"):
            top = s["topMerchants"][0]
            text += f" Top: {top['merchant']} ({format_inr(top['spentPaise'])})."
        return [_nudge(text, "weekly review")]
    return []
