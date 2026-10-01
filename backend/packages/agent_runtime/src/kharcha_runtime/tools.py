"""Tool definitions shared by tool servers, plus an in-process executor (PROJECT_SPEC §17.3).

A tool is a JSON-Schema spec plus ``handler(session, ctx, args) -> dict``. The handler takes
the user id from the run context, never from the model, so every query is user-scoped (rule 7).
Until the MCP servers exist (W13) the agents service calls handlers in-process.
"""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_runtime.types import RunContext, ToolResult, ToolSpec

Handler = Callable[[AsyncSession, RunContext, Any], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class Tool:
    spec: ToolSpec
    args: type[BaseModel]
    handler: Handler


def tool(name: str, description: str, args: type[BaseModel]) -> Callable[[Handler], Tool]:
    def wrap(handler: Handler) -> Tool:
        schema = args.model_json_schema()
        schema.pop("title", None)
        return Tool(ToolSpec(name, description, schema), args, handler)

    return wrap


class InProcessExecutor:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], tools: Mapping[str, Tool]):
        self._sessions = sessions
        self._tools = dict(tools)

    @property
    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    async def call(self, ctx: RunContext, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        found = self._tools.get(name)
        if found is None:
            return ToolResult.error("UNKNOWN_TOOL", name)
        try:
            args = found.args.model_validate(dict(arguments))
        except ValidationError as exc:
            return ToolResult.error("BAD_ARGUMENTS", str(exc.errors()[0]["msg"]))
        async with self._sessions() as session:
            return ToolResult(True, await found.handler(session, ctx, args))
