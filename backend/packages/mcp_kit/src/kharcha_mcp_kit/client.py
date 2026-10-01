"""``ToolExecutor`` over MCP (PROJECT_SPEC §17.3): what agents use instead of in-process calls.

Each call mints a service token for the run's (user id, agent), so the server - not the
model - decides whose data is read and whether the tool is allowed.
"""

import json
from collections.abc import Callable, Mapping
from typing import Any

import httpx2
from mcp.client.client import Client
from mcp.client.streamable_http import streamable_http_client

from kharcha_mcp_kit.identity import ServiceTokens
from kharcha_runtime.types import RunContext, ToolResult

HttpFactory = Callable[[dict[str, str]], httpx2.AsyncClient]


def _default_http(headers: dict[str, str]) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(headers=headers, timeout=30)


def _decode(result: Any) -> dict[str, Any]:
    if result.structured_content:
        data = result.structured_content
        return dict(data.get("result", data)) if isinstance(data, dict) else {"result": data}
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            try:
                value = json.loads(text)
            except ValueError:
                return {"error": "BAD_RESPONSE", "message": text[:200]}
            return value if isinstance(value, dict) else {"result": value}
    return {}


class McpToolExecutor:
    def __init__(
        self,
        routes: Mapping[str, str],
        tokens: ServiceTokens,
        http: HttpFactory = _default_http,
    ) -> None:
        """``routes``: tool name -> server URL (``http://host:port/mcp``)."""
        self._routes = dict(routes)
        self._tokens = tokens
        self._http = http

    async def call(self, ctx: RunContext, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        url = self._routes.get(name)
        if url is None:
            return ToolResult.error("UNKNOWN_TOOL", name)
        token = self._tokens.mint(ctx.user_id, ctx.agent)
        try:
            async with (
                self._http({"Authorization": f"Bearer {token}"}) as http,
                Client(streamable_http_client(url, http_client=http)) as client,
            ):
                result = await client.call_tool(name, dict(arguments))
        except Exception as exc:  # transport or protocol failure: data for the model
            return ToolResult.error("TOOL_UNAVAILABLE", type(exc).__name__)
        data = _decode(result)
        if result.is_error or "error" in data:
            return ToolResult(False, data or {"error": "TOOL_FAILED"})
        return ToolResult(True, data)
