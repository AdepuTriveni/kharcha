"""Build an MCP server (official SDK, streamable HTTP) from Kharcha tools (PROJECT_SPEC §17.3).

Every call is checked twice on the server: the bearer token must be valid (middleware), and
the token's agent must be allowed to use the tool (permission table). The handler then runs
with the token's user id, so a tool can only ever read that user's data.
"""

import inspect
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import ASGIApp, Receive, Scope, Send

from kharcha_mcp_kit.identity import CURRENT, Identity
from kharcha_mcp_kit.permissions import allowed
from kharcha_runtime.tools import Tool
from kharcha_runtime.types import RunContext

log = logging.getLogger(__name__)
Verifier = Callable[[str], Awaitable[Identity | None]]


def error(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message}


def _wrapper(
    name: str, tool: Tool, sessions: async_sessionmaker[AsyncSession]
) -> Callable[..., Any]:
    async def call(**kwargs: Any) -> dict[str, Any]:
        identity = CURRENT.get()
        if identity is None:
            return error("UNAUTHENTICATED", "missing identity")
        if not allowed(identity.agent, name):
            log.warning("tool denied", extra={"tool": name, "agent": identity.agent})
            return error("DENIED_TOOL", f"{name} is not allowed for {identity.agent}")
        try:
            args = tool.args.model_validate({k: v for k, v in kwargs.items() if v is not None})
        except ValidationError as exc:
            return error("BAD_ARGUMENTS", str(exc.errors()[0]["msg"]))
        ctx = RunContext(run_id="mcp", user_id=identity.user_id, agent=identity.agent)
        async with sessions() as session:
            return await tool.handler(session, ctx, args)

    # The SDK derives the input schema from the signature: mirror the pydantic args model.
    params = []
    annotations: dict[str, Any] = {}
    for field_name, info in tool.args.model_fields.items():
        key = info.alias or field_name
        annotation = info.annotation if info.is_required() else info.annotation | None  # type: ignore[operator]
        default = inspect.Parameter.empty if info.is_required() else None
        params.append(
            inspect.Parameter(
                key, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=annotation
            )
        )
        annotations[key] = annotation
    call.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]
    call.__annotations__ = {**annotations, "return": dict[str, Any]}
    call.__name__ = name
    return call


def build_server(
    name: str, sessions: async_sessionmaker[AsyncSession], tools: Mapping[str, Tool]
) -> MCPServer:
    server = MCPServer(name, instructions="Kharcha tools. Amounts in *Paise fields are paise.")
    for tool_name, tool in tools.items():
        server.add_tool(
            _wrapper(tool_name, tool, sessions), name=tool_name, description=tool.spec.description
        )
    return server


class BearerAuth:
    """ASGI middleware: Bearer token -> :data:`CURRENT` identity, else 401."""

    def __init__(self, app: ASGIApp, verify: Verifier) -> None:
        self.app = app
        self.verify = verify

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        header = dict(scope.get("headers", [])).get(b"authorization", b"").decode()
        token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        identity = await self.verify(token) if token else None
        if identity is None:
            body = json.dumps({"error": "unauthorized"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        reset = CURRENT.set(identity)
        try:
            await self.app(scope, receive, send)
        finally:
            CURRENT.reset(reset)


def http_app(server: MCPServer, verify: Verifier, allowed_hosts: list[str]) -> tuple[ASGIApp, Any]:
    """(authenticated ASGI app, the inner Starlette app whose lifespan must run)."""
    inner = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts),
    )
    return BearerAuth(inner, verify), inner
