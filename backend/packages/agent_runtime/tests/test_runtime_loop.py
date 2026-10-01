import asyncio
from collections.abc import Mapping, Sequence
from typing import Any

from kharcha_runtime.content_filter import check_content
from kharcha_runtime.grounding import allowed_from_results, check_text
from kharcha_runtime.loop import PROPOSE, run_agent
from kharcha_runtime.models import CircuitBreaker
from kharcha_runtime.replay import ReplayModel, ReplayToolExecutor
from kharcha_runtime.types import (
    AgentConfig,
    Limits,
    ModelResponse,
    RunContext,
    RunStatus,
    ToolCall,
    ToolResult,
    ToolSpec,
)

CTX = RunContext(run_id="r_1", user_id="u_1", agent="coach")
SUMMARY = {
    "weekSpentPaise": 142_000,
    "topMerchant": "Zomato",
    "payments": 7,
    "weekStart": "2026-09-28",
}
FOOD = "₹1,420 on food this week, 7 orders. Cook twice and save."
SPECS = [
    ToolSpec("get_weekly_summary", "week totals", {"type": "object", "properties": {}}),
    ToolSpec("run_analyst_sql", "sql", {"type": "object", "properties": {}}),
]


def _config(**limits: Any) -> AgentConfig:
    return AgentConfig(
        name="coach",
        system_prompt="You are Coach.",
        prompt_version="coach/v1",
        model="scripted",
        allowed_tools=frozenset({"get_weekly_summary", PROPOSE}),
        limits=Limits(**limits),
    )


def call(name: str, i: int = 0, **args: Any) -> ToolCall:
    return ToolCall(f"c{i}", name, args)


def propose(text: str, i: int = 9, ptype: str = "ROAST") -> ToolCall:
    return call(PROPOSE, i, type=ptype, text=text, reason="weekly review", category="FOOD_DELIVERY")


class Scripted:
    def __init__(self, *turns: ModelResponse, delay: float = 0.0) -> None:
        self.turns = list(turns)
        self.delay = delay
        self.seen: list[list[Mapping[str, Any]]] = []

    async def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> ModelResponse:
        self.seen.append(list(messages))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.turns.pop(0)


class Tools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def call(self, ctx: RunContext, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        self.calls.append((ctx.user_id, name))
        if name == "get_weekly_summary":
            return ToolResult(True, SUMMARY)
        raise RuntimeError("boom")


def turn(*calls: ToolCall, content: str | None = None, tokens: int = 100) -> ModelResponse:
    return ModelResponse(content=content, tool_calls=calls, input_tokens=tokens)


async def test_grounded_proposal_is_accepted() -> None:
    model = Scripted(
        turn(call("get_weekly_summary")),
        turn(propose(FOOD)),
        turn(content="done"),
    )
    tools = Tools()
    result = await run_agent(_config(), CTX, "Weekly review", model, tools, SPECS)
    assert result.status is RunStatus.OK
    assert [p.text for p in result.proposals] == [
        "₹1,420 on food this week, 7 orders. Cook twice and save."
    ]
    assert tools.calls == [("u_1", "get_weekly_summary")]
    # Tool output reaches the model as delimited data.
    tool_message = model.seen[1][-1]
    assert tool_message["role"] == "tool"
    assert str(tool_message["content"]).startswith("<<<TOOL_DATA")
    kinds = [e["kind"] for e in result.transcript]
    assert kinds[0] == "start"
    assert kinds[-1] == "end"
    assert "proposal" in kinds


async def test_ungrounded_proposal_gets_one_rewrite_then_drops() -> None:
    model = Scripted(
        turn(call("get_weekly_summary")),
        turn(propose("You spent ₹2,000 this week")),  # not in tool results
        turn(propose("You spent ₹2,500 this week", i=10)),  # still wrong -> dropped
        turn(content="ok"),
    )
    result = await run_agent(_config(), CTX, "Weekly review", model, Tools(), SPECS)
    assert result.status is RunStatus.OK
    assert result.proposals == []
    rejection = model.seen[2][-1]["content"]
    assert "UNGROUNDED" in str(rejection)
    assert "2,000" in str(rejection)


async def test_rewrite_after_rejection_is_accepted() -> None:
    model = Scripted(
        turn(call("get_weekly_summary")),
        turn(propose("You spent ₹2,000 this week")),
        turn(propose("You spent ₹1,420 this week", i=10)),
        turn(content="ok"),
    )
    result = await run_agent(_config(), CTX, "g", model, Tools(), SPECS)
    assert [p.text for p in result.proposals] == ["You spent ₹1,420 this week"]


async def test_content_filter_blocks_personal_remarks() -> None:
    model = Scripted(
        turn(call("get_weekly_summary")),
        turn(propose("7 orders? No wonder you look fat")),
        turn(content="ok"),
    )
    result = await run_agent(_config(), CTX, "g", model, Tools(), SPECS)
    assert result.proposals == []
    assert "CONTENT" in str(model.seen[2][-1]["content"])


async def test_denied_tool_is_an_error_then_ends_run() -> None:
    model = Scripted(
        turn(call("run_analyst_sql")),
        turn(call("run_analyst_sql", i=1)),
    )
    tools = Tools()
    result = await run_agent(_config(), CTX, "g", model, tools, SPECS)
    assert result.status is RunStatus.DENIED_TOOL
    assert tools.calls == []  # never executed
    assert "DENIED_TOOL" in str(model.seen[1][-1]["content"])


async def test_tool_exception_becomes_structured_error() -> None:
    config = AgentConfig("coach", "s", "coach/v1", "m", frozenset({"run_analyst_sql"}))
    model = Scripted(turn(call("run_analyst_sql")), turn(content="sorry"))
    result = await run_agent(config, CTX, "g", model, Tools(), SPECS)
    assert result.status is RunStatus.OK
    assert "TOOL_FAILED" in str(model.seen[1][-1]["content"])


async def test_limits_end_run_without_proposals() -> None:
    loop_forever = [turn(call("get_weekly_summary", i)) for i in range(20)]
    result = await run_agent(
        _config(max_iterations=3), CTX, "g", Scripted(*loop_forever), Tools(), SPECS
    )
    assert result.status is RunStatus.BUDGET_EXCEEDED

    per_tool = await run_agent(
        _config(max_calls_per_tool=2), CTX, "g", Scripted(*loop_forever), Tools(), SPECS
    )
    assert per_tool.status is RunStatus.BUDGET_EXCEEDED

    tokens = Scripted(
        turn(call("get_weekly_summary")),
        turn(propose("₹1,420 on food"), tokens=20_000),
    )
    over = await run_agent(_config(), CTX, "g", tokens, Tools(), SPECS)
    assert over.status is RunStatus.BUDGET_EXCEEDED
    assert over.proposals == []

    slow = Scripted(turn(content="late"), delay=0.5)
    timed = await run_agent(_config(max_wall_s=0.05), CTX, "g", slow, Tools(), SPECS)
    assert timed.status is RunStatus.TIMEOUT

    many = Scripted(
        turn(call("get_weekly_summary")),
        *(turn(propose("₹1,420 on food", i=i)) for i in range(5)),
        turn(content="done"),
    )
    capped = await run_agent(
        _config(max_proposals=2, max_calls_per_tool=9), CTX, "g", many, Tools(), SPECS
    )
    assert len(capped.proposals) == 2


async def test_model_error_is_error_status() -> None:
    class Broken:
        async def complete(
            self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
        ) -> ModelResponse:
            raise ConnectionError("down")

    result = await run_agent(_config(), CTX, "g", Broken(), Tools(), SPECS)
    assert result.status is RunStatus.ERROR


async def test_replay_reproduces_run() -> None:
    original = await run_agent(
        _config(),
        CTX,
        "Weekly review",
        Scripted(
            turn(call("get_weekly_summary")),
            turn(propose("₹1,420 on food this week")),
            turn(content="done"),
        ),
        Tools(),
        SPECS,
    )
    replayed = await run_agent(
        _config(),
        CTX,
        "Weekly review",
        ReplayModel(original.transcript),
        ReplayToolExecutor(original.transcript),
        SPECS,
    )
    assert replayed.status is RunStatus.OK
    assert replayed.proposals == original.proposals


def test_grounding_allows_tool_numbers_only() -> None:
    results = [{"spentPaise": 142_050, "count": 7, "date": "2026-10-18", "note": "limit ₹5,000"}]
    allowed = allowed_from_results(results)
    assert check_text("₹1,420.50 over 7 payments, broke by 18 Oct; limit ₹5,000", results).ok
    assert check_text("about ₹1,420", results).ok  # rounded to ₹1
    assert not check_text("₹1,500 spent", results).ok
    assert len(allowed) > 3


def test_content_filter() -> None:
    assert check_content("Zomato 7 times this week? Your wallet is crying").ok
    assert check_content("Bas kar bhai, mota ho jayega").reason == "BODY"
    assert check_content("x" * 401).reason == "TOO_LONG"
    assert check_content("  ").reason == "EMPTY"


def test_circuit_breaker() -> None:
    breaker = CircuitBreaker(threshold=2, cooldown_s=10)
    breaker.record(False, now=0)
    assert breaker.available(now=1)
    breaker.record(False, now=1)
    assert not breaker.available(now=5)
    assert breaker.available(now=12)  # half-open after cooldown
