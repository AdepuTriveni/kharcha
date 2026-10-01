"""LiteLLM model adapter with a fallback chain and a per-model circuit breaker (§16.6)."""

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import litellm

from kharcha_runtime.types import ModelResponse, ToolCall, ToolSpec


@dataclass
class CircuitBreaker:
    """Opens after ``threshold`` consecutive failures for ``cooldown_s`` seconds."""

    threshold: int = 3
    cooldown_s: float = 60.0
    failures: int = 0
    opened_at: float | None = None

    def available(self, now: float | None = None) -> bool:
        if self.opened_at is None:
            return True
        if (now or time.monotonic()) - self.opened_at >= self.cooldown_s:
            self.opened_at, self.failures = None, 0  # half-open: try again
            return True
        return False

    def record(self, ok: bool, now: float | None = None) -> None:
        if ok:
            self.failures, self.opened_at = 0, None
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = now or time.monotonic()


class AllModelsFailedError(RuntimeError):
    pass


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass
class LiteLLMModel:
    """Tries ``models`` in order; a model whose breaker is open is skipped."""

    models: Sequence[str]
    ollama_api_base: str = "http://localhost:11434"
    timeout_s: float = 60.0
    temperature: float = 0.3
    breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    async def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> ModelResponse:
        last: Exception | None = None
        for name in self.models:
            breaker = self.breakers.setdefault(name, CircuitBreaker())
            if not breaker.available():
                continue
            try:
                response = await litellm.acompletion(
                    model=name,
                    messages=list(messages),
                    tools=[t.as_openai() for t in tools] or None,
                    api_base=self.ollama_api_base if name.startswith("ollama") else None,
                    temperature=self.temperature,
                    timeout=self.timeout_s,
                )
            except Exception as exc:  # rate limit, provider down, bad request: try the next one
                breaker.record(False)
                last = exc
                continue
            breaker.record(True)
            message = response.choices[0].message
            calls = tuple(
                ToolCall(str(c.id), str(c.function.name), _parse_arguments(c.function.arguments))
                for c in (getattr(message, "tool_calls", None) or [])
            )
            usage = getattr(response, "usage", None)
            return ModelResponse(
                content=message.content,
                tool_calls=calls,
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                model=name,
            )
        raise AllModelsFailedError(type(last).__name__ if last else "no model available")
