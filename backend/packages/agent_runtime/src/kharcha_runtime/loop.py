"""The agent loop (PROJECT_SPEC §16.1-16.4).

build context -> model call -> for each tool call: permission check -> execute -> append
result -> repeat; stop on a final answer or any limit. ``propose_message`` is handled here:
a proposal is accepted only after the content filter and the grounding check (numbers must
come from this run's tool results). A rejected proposal gets one rewrite; then it is dropped.
Any limit, timeout, error or repeated denied tool ends the run with no proposals.
"""

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from kharcha_runtime.content_filter import check_content
from kharcha_runtime.grounding import check_text
from kharcha_runtime.types import (
    AgentConfig,
    Model,
    Proposal,
    RunContext,
    RunResult,
    RunStatus,
    ToolCall,
    ToolExecutor,
    ToolResult,
    ToolSpec,
)

log = logging.getLogger(__name__)

PROPOSE = "propose_message"
PROPOSAL_TYPES = ("ROAST", "NUDGE", "HYPE", "QUESTION", "INFO")
PROPOSE_SPEC = ToolSpec(
    name=PROPOSE,
    description=(
        "Propose ONE message for the user. It is NOT sent: the policy gate decides. Every "
        "number in `text` must appear in a tool result from this run."
    ),
    parameters={
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(PROPOSAL_TYPES)},
            "text": {"type": "string", "description": "max 2 sentences, at most 400 characters"},
            "category": {"type": "string"},
            "buttons": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
            "reason": {"type": "string", "description": "why this helps (internal)"},
        },
        "required": ["type", "text", "reason"],
    },
)
MAX_DENIED = 2


def wrap_data(value: Mapping[str, Any]) -> str:
    """Tool output as delimited data so merchant/message text cannot act as instructions."""
    return "<<<TOOL_DATA\n" + json.dumps(value, ensure_ascii=False, default=str) + "\nTOOL_DATA>>>"


class _Run:
    def __init__(
        self,
        config: AgentConfig,
        ctx: RunContext,
        model: Model,
        tools: ToolExecutor,
        specs: Sequence[ToolSpec],
    ) -> None:
        self.config = config
        self.ctx = ctx
        self.model = model
        self.tools = tools
        allowed = [s for s in specs if s.name in config.allowed_tools]
        if PROPOSE in config.allowed_tools:
            allowed.append(PROPOSE_SPEC)
        self.specs = allowed
        self.transcript: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []
        self.proposals: list[Proposal] = []
        self.rewrites_used = 0
        self.calls: Counter[str] = Counter()
        self.denied = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.start = time.monotonic()

    def log(self, kind: str, **data: Any) -> None:
        self.transcript.append(
            {"t_ms": round((time.monotonic() - self.start) * 1000), "kind": kind, **data}
        )

    def _propose(self, args: Mapping[str, Any]) -> ToolResult:
        if len(self.proposals) >= self.config.limits.max_proposals:
            return ToolResult.error("TOO_MANY_PROPOSALS", "proposal limit reached")
        ptype = str(args.get("type", "")).upper()
        text = str(args.get("text", "")).strip()
        if ptype not in PROPOSAL_TYPES:
            return ToolResult.error("BAD_TYPE", f"type must be one of {', '.join(PROPOSAL_TYPES)}")
        content = check_content(text)
        grounding = check_text(text, self.results)
        if content.ok and grounding.ok:
            buttons = tuple(str(b)[:40] for b in list(args.get("buttons") or [])[:4])
            category = args.get("category")
            self.proposals.append(
                Proposal(
                    type=ptype,
                    text=text,
                    category=str(category) if category else None,
                    buttons=buttons,
                    reason=str(args.get("reason", ""))[:300],
                )
            )
            self.log("proposal", accepted=True, type=ptype)
            return ToolResult(True, {"accepted": True, "proposals": len(self.proposals)})
        reason = content.reason if not content.ok else "UNGROUNDED"
        self.log("proposal", accepted=False, reason=reason, unmatched=list(grounding.unmatched))
        if self.rewrites_used >= 1:
            return ToolResult.error(
                "DROPPED", "proposal rejected again and dropped; stop proposing"
            )
        self.rewrites_used += 1
        if reason == "UNGROUNDED":
            numbers = ", ".join(grounding.unmatched)
            return ToolResult.error(
                "UNGROUNDED",
                f"numbers not found in tool results: {numbers}. Rewrite using only tool numbers.",
            )
        return ToolResult.error("CONTENT", f"blocked ({reason}); talk only about spending")

    async def _tool(self, call: ToolCall) -> ToolResult | RunStatus:
        if call.name not in self.config.allowed_tools:
            self.denied += 1
            self.log("denied", tool=call.name)
            if self.denied >= MAX_DENIED:
                return RunStatus.DENIED_TOOL
            return ToolResult.error("DENIED_TOOL", f"{call.name} is not allowed for this agent")
        self.calls[call.name] += 1
        if self.calls[call.name] > self.config.limits.max_calls_per_tool:
            return RunStatus.BUDGET_EXCEEDED
        if call.name == PROPOSE:
            return self._propose(call.arguments)
        try:
            result = await self.tools.call(self.ctx, call.name, call.arguments)
        except Exception as exc:  # tool bugs become data for the model, never a crash
            log.warning("tool failed", extra={"tool": call.name, "run_id": self.ctx.run_id})
            result = ToolResult.error("TOOL_FAILED", type(exc).__name__)
        if result.ok:
            self.results.append(dict(result.data))
        return result

    async def run(self, messages: list[dict[str, Any]]) -> RunStatus:
        limits = self.config.limits
        for iteration in range(limits.max_iterations):
            try:
                response = await self.model.complete(messages, self.specs)
            except Exception as exc:
                self.log("model_error", error=type(exc).__name__)
                return RunStatus.ERROR
            self.input_tokens += response.input_tokens
            self.output_tokens += response.output_tokens
            self.log(
                "model",
                iteration=iteration,
                content=response.content,
                tool_calls=[
                    {"id": c.id, "name": c.name, "arguments": dict(c.arguments)}
                    for c in response.tool_calls
                ],
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                model=response.model,
            )
            if self.input_tokens > limits.max_input_tokens:
                return RunStatus.BUDGET_EXCEEDED
            if not response.tool_calls:
                self.final_text = response.content
                return RunStatus.OK
            messages.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {
                                "name": c.name,
                                "arguments": json.dumps(dict(c.arguments)),
                            },
                        }
                        for c in response.tool_calls
                    ],
                }
            )
            for call in response.tool_calls:
                outcome = await self._tool(call)
                if isinstance(outcome, RunStatus):
                    return outcome
                self.log("tool", id=call.id, name=call.name, ok=outcome.ok, data=dict(outcome.data))
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": wrap_data(outcome.data)}
                )
        return RunStatus.BUDGET_EXCEEDED

    final_text: str | None = None


async def run_agent(
    config: AgentConfig,
    ctx: RunContext,
    goal: str,
    model: Model,
    tools: ToolExecutor,
    specs: Sequence[ToolSpec],
) -> RunResult:
    """Run one task. Never raises for model/tool problems; the status says what happened."""
    state = _Run(config, ctx, model, tools, specs)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": goal},
    ]
    state.log("start", agent=config.name, prompt=config.prompt_version, model=config.model)
    try:
        async with asyncio.timeout(config.limits.max_wall_s):
            status = await state.run(messages)
    except TimeoutError:
        status = RunStatus.TIMEOUT
    state.log("end", status=status.value, proposals=len(state.proposals))
    return RunResult(
        run_id=ctx.run_id,
        status=status,
        proposals=state.proposals if status is RunStatus.OK else [],
        transcript=state.transcript,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        final_text=state.final_text,
        tool_results=state.results,
    )
