"""Deterministic replay of a recorded run (PROJECT_SPEC §16.5).

``ReplayModel`` answers with the recorded model turns; ``ReplayToolExecutor`` with the
recorded tool results, in order. Regression mode: a *new* model against recorded tool
results (``ReplayToolExecutor`` alone), then compare proposals.
"""

from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import Any

from kharcha_runtime.types import ModelResponse, RunContext, ToolCall, ToolResult, ToolSpec


class ReplayExhaustedError(RuntimeError):
    pass


class ReplayModel:
    def __init__(self, transcript: Sequence[Mapping[str, Any]]) -> None:
        self._turns = deque(e for e in transcript if e.get("kind") == "model")

    async def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> ModelResponse:
        if not self._turns:
            raise ReplayExhaustedError("no more recorded model turns")
        turn = self._turns.popleft()
        return ModelResponse(
            content=turn.get("content"),
            tool_calls=tuple(
                ToolCall(c["id"], c["name"], c.get("arguments", {}))
                for c in turn.get("tool_calls", [])
            ),
            input_tokens=int(turn.get("input_tokens", 0)),
            output_tokens=int(turn.get("output_tokens", 0)),
            model=str(turn.get("model", "replay")),
        )


class ReplayToolExecutor:
    def __init__(self, transcript: Sequence[Mapping[str, Any]]) -> None:
        self._results: dict[str, deque[Mapping[str, Any]]] = defaultdict(deque)
        for entry in transcript:
            if entry.get("kind") == "tool":
                self._results[str(entry["name"])].append(entry)

    async def call(self, ctx: RunContext, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        queue = self._results.get(name)
        if not queue:
            return ToolResult.error("NOT_RECORDED", f"no recorded result for {name}")
        entry = queue.popleft()
        return ToolResult(bool(entry.get("ok", True)), dict(entry.get("data", {})))
