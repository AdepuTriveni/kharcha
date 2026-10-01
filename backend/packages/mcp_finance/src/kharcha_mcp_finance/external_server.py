"""``kharcha-mcp-external``: read-only tools for the user's own MCP clients (PROJECT_SPEC §24).

HTTP (port 8105) with a personal token from ``POST /v1/mcp-tokens`` as Bearer, or stdio for a
desktop client: ``kharcha-mcp-external --stdio --token kmcp_...``.
"""

import argparse

import anyio

from kharcha_common import mcp_tokens
from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.settings import get_settings
from kharcha_common.time import utcnow
from kharcha_mcp_finance.external import TOOLS
from kharcha_mcp_kit.identity import CURRENT, Identity
from kharcha_mcp_kit.serve import make_app
from kharcha_mcp_kit.server import Verifier, build_server

PORT = 8105


def token_verifier() -> Verifier:
    sessions = make_sessionmaker(make_engine(get_settings()))

    async def verify(token: str) -> Identity | None:
        async with sessions.begin() as session:
            user_id = await mcp_tokens.verify(session, token, utcnow())
        return Identity(user_id, "external", external=True) if user_id else None

    return verify


async def _stdio(token: str) -> None:
    identity = await token_verifier()(token)
    if identity is None:
        raise SystemExit("invalid or expired token")
    CURRENT.set(identity)
    sessions = make_sessionmaker(make_engine(get_settings()))
    await build_server("kharcha", sessions, TOOLS).run_stdio_async()


def run() -> None:
    parser = argparse.ArgumentParser(prog="kharcha-mcp-external")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--token", default=None)
    args = parser.parse_args()
    if args.stdio:
        if not args.token:
            raise SystemExit("--token is required with --stdio")
        anyio.run(_stdio, args.token)
        return
    import uvicorn

    settings = get_settings()
    app = make_app("kharcha", TOOLS, settings, verify=token_verifier())
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")  # noqa: S104
