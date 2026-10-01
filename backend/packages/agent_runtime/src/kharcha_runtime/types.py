"""Runtime data types and the two protocols the loop depends on (model, tools)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol


class RunStatus(StrEnum):
    OK = "OK"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    DENIED_TOOL = "DENIED_TOOL"


@dataclass(frozen=True, slots=True)
class Limits:
    """§16.2 defaults."""

    max_iterations: int = 8
    max_input_tokens: int = 12_000
    max_wall_s: float = 60.0
    max_proposals: int = 3
    max_calls_per_tool: int = 4


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: Mapping[str, Any]  # JSON Schema object

    def as_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: str | None
    tool_calls: Sequence[ToolCall] = ()
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Always data. Errors are results too (``ok=False``), never exceptions in the model."""

    ok: bool
    data: Mapping[str, Any]

    @staticmethod
    def error(code: str, message: str) -> "ToolResult":
        return ToolResult(False, {"error": code, "message": message})


@dataclass(frozen=True, slots=True)
class Proposal:
    """A message an agent wants to send. Only the notifier's policy gate can send it."""

    type: str  # ROAST | NUDGE | HYPE | QUESTION | INFO
    text: str
    category: str | None = None
    buttons: tuple[str, ...] = ()
    reason: str = ""
    grounding_numbers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunContext:
    """Who the run is for. Tool executors scope every call to ``user_id`` (rule 7)."""

    run_id: str
    user_id: str
    agent: str
    task_id: str | None = None
    now: datetime | None = None  # clock for tools; None = wall clock

    def clock(self) -> datetime:
        return self.now or datetime.now(UTC)


class Model(Protocol):
    async def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> ModelResponse: ...


class ToolExecutor(Protocol):
    async def call(
        self, ctx: RunContext, name: str, arguments: Mapping[str, Any]
    ) -> ToolResult: ...


@dataclass(frozen=True, slots=True)
class AgentConfig:
    name: str
    system_prompt: str
    prompt_version: str
    model: str
    allowed_tools: frozenset[str]
    limits: Limits = field(default_factory=Limits)


@dataclass
class RunResult:
    run_id: str
    status: RunStatus
    proposals: list[Proposal]
    transcript: list[dict[str, Any]]
    input_tokens: int = 0
    output_tokens: int = 0
    final_text: str | None = None
    tool_results: list[dict[str, Any]] = field(default_factory=list)
