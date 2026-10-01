"""Run a tool server (``kharcha-mcp-finance`` etc.) on streamable HTTP (PROJECT_SPEC §17.3)."""

from collections.abc import Mapping

import uvicorn
from starlette.types import Receive, Scope, Send

from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.logging import configure_logging
from kharcha_common.settings import Settings, get_settings
from kharcha_mcp_kit.identity import Identity, InvalidServiceTokenError, ServiceTokens
from kharcha_mcp_kit.server import Verifier, build_server, http_app
from kharcha_runtime.tools import Tool


def service_verifier(tokens: ServiceTokens) -> Verifier:
    async def verify(token: str) -> Identity | None:
        try:
            return tokens.verify(token)
        except InvalidServiceTokenError:
            return None

    return verify


class _WithLifespan:
    """The SDK's session manager runs in the inner app's lifespan; forward it."""

    def __init__(self, outer: object, inner: object) -> None:
        self.outer, self.inner = outer, inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        target = self.inner if scope["type"] == "lifespan" else self.outer
        await target(scope, receive, send)  # type: ignore[operator]


def make_app(
    name: str, tools: Mapping[str, Tool], settings: Settings, verify: Verifier | None = None
) -> _WithLifespan:
    if verify is None:
        if not settings.service_token_secret:
            raise SystemExit("KHARCHA_SERVICE_TOKEN_SECRET is not set")
        verify = service_verifier(ServiceTokens(settings.service_token_secret))
    sessions = make_sessionmaker(make_engine(settings))
    outer, inner = http_app(build_server(name, sessions, tools), verify, settings.mcp_allowed_hosts)
    return _WithLifespan(outer, inner)


def serve(name: str, tools: Mapping[str, Tool], port: int) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(make_app(name, tools, settings), host="0.0.0.0", port=port, log_level="warning")  # noqa: S104
