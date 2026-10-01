"""Memory Keeper (PROJECT_SPEC §17.2, §20): turns user messages and feedback into memories.

Writes go through mcp-memory only. Never proposes messages (no ``propose_message``).
Fallback (no model): the commitment rules, so "limit Zomato to 2 a week" is always remembered.
"""

import json

from kharcha_common.commitments import detect
from kharcha_common.events import AgentTaskPayload, AgentTrigger, Proposal
from kharcha_common.prompts import load_prompt
from kharcha_common.time import to_ist
from kharcha_runtime.types import RunContext, ToolExecutor


def goal_text(task: AgentTaskPayload, roast_level: str) -> str:
    refs = {k: v for k, v in task.context_refs.items() if v is not None}
    return (
        load_prompt("memory_keeper", "v1")
        .section("user")
        .replace("{trigger}", task.trigger.value)
        .replace("{refs}", json.dumps(refs, ensure_ascii=False))
    )


async def fallback(task: AgentTaskPayload, tools: ToolExecutor, ctx: RunContext) -> list[Proposal]:
    text = task.context_refs.get("text")
    if task.trigger is AgentTrigger.USER_MESSAGE and text:
        commitment = detect(text, to_ist(ctx.clock()).date())
        if commitment is not None:
            args: dict[str, object] = {
                "kind": "COMMITMENT",
                "content": commitment.text[:300],
                "confidence": 0.9,
            }
            if (due := commitment.due_at()) is not None:
                args["dueAt"] = due.isoformat()
            await tools.call(ctx, "write_memory", args)
    reaction = task.context_refs.get("reaction")
    if (
        task.trigger is AgentTrigger.FEEDBACK
        and reaction == "NOT_FAIR"
        and task.context_refs.get("category")
    ):
        category = str(task.context_refs["category"]).replace("_", " ").lower()
        await tools.call(
            ctx,
            "write_memory",
            {
                "kind": "PREFERENCE",
                "content": f"Finds roasts about {category} not fair",
                "confidence": 0.8,
            },
        )
    return []
