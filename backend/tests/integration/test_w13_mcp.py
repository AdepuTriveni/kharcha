"""W13 on real Postgres: MCP servers (official SDK) with scoped service tokens.

Proves the §34 "done" condition: a denied tool is blocked by the runtime *and* by the server,
and a token for one user never reads another user's data.
"""

import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import fakeredis
import httpx
import httpx2
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.applications import Starlette
from starlette.routing import Mount

from kharcha_common.events import EventEnvelope
from kharcha_common.ids import uuid7
from kharcha_common.settings import Settings
from kharcha_ingest.auth import hash_api_key
from kharcha_ingest.main import create_app
from kharcha_mcp_finance.external import TOOLS as EXTERNAL_TOOLS
from kharcha_mcp_finance.tools import TOOLS as FINANCE_TOOLS
from kharcha_mcp_kit.client import McpToolExecutor
from kharcha_mcp_kit.identity import Identity, ServiceTokens
from kharcha_mcp_kit.serve import service_verifier
from kharcha_mcp_kit.server import Verifier, build_server, http_app
from kharcha_mcp_memory.tools import TOOLS as MEMORY_TOOLS
from kharcha_mcp_notify.tools import TOOLS as NOTIFY_TOOLS
from kharcha_runtime.loop import PROPOSE, run_agent
from kharcha_runtime.tools import Tool
from kharcha_runtime.types import (
    AgentConfig,
    ModelResponse,
    RunContext,
    RunStatus,
    ToolCall,
    ToolSpec,
)
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

A, B = "u_mcp_a", "u_mcp_b"
SECRET = "test-secret-for-service-tokens-0123456789"
BASE = "http://localhost:8000"
TOKENS = ServiceTokens(SECRET)


class NullPublisher:
    async def publish_event(self, topic: str, event: EventEnvelope[Any]) -> None:
        return None

    async def publish_raw(
        self, topic: str, body: bytes, key: bytes | None, headers: Mapping[str, str]
    ) -> None:
        return None


@dataclass
class Env:
    executor: McpToolExecutor
    routes: dict[str, str]
    app: Starlette
    sessions: async_sessionmaker[AsyncSession]
    migrated: PgUrls
    inners: list[Any]

    @asynccontextmanager
    async def serve(self) -> AsyncIterator[None]:
        """Run the servers' lifespans inside the test task (anyio cancel scopes)."""
        async with AsyncExitStack() as stack:
            for inner in self.inners:
                await stack.enter_async_context(inner.router.lifespan_context(inner))
            yield


def _mount(
    sessions: async_sessionmaker[AsyncSession],
    name: str,
    tools: Mapping[str, Tool],
    verify: Verifier,
) -> tuple[Mount, Any]:
    outer, inner = http_app(build_server(name, sessions, tools), verify, ["localhost:*"])
    return Mount(f"/{name}", app=outer), inner


@pytest.fixture
async def env(migrated_db: PgUrls) -> AsyncIterator[Env]:
    engine = create_async_engine(migrated_db.async_url)
    async with engine.begin() as conn:
        for table in (
            "mcp_tokens",
            "memories",
            "forecasts",
            "agent_runs",
            "alerts_sent",
            "cash_ledger",
            "transaction_sources",
            "transactions",
            "processed_events",
            "raw_events",
            "budgets",
            "user_merchant_overrides",
            "users",
        ):
            await conn.execute(sa.text(f"DELETE FROM {table}"))
        await conn.execute(sa.text("INSERT INTO users (id) VALUES (:a), (:b)"), {"a": A, "b": B})
        for user, amount in ((A, 35_500), (B, 99_900)):
            await conn.execute(
                sa.text(
                    "INSERT INTO transactions (id, user_id, amount_paise, direction, kind, status,"
                    " channel, merchant_raw, category, is_essential, txn_time, parse_method) "
                    "VALUES (:id, :u, :amt, 'DEBIT', 'SPEND', 'SUCCESS', 'UPI', 'Zomato', "
                    "'FOOD_DELIVERY', false, now() - interval '1 hour', 'RULE')"
                ),
                {"id": "t_" + uuid7().hex, "u": user, "amt": amount},
            )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    verify = service_verifier(TOKENS)
    mounts, inners = [], []
    for name, tools, verifier in (
        ("finance", FINANCE_TOOLS, verify),
        ("notify", NOTIFY_TOOLS, verify),
        ("memory", MEMORY_TOOLS, verify),
        ("external", EXTERNAL_TOOLS, token_verifier_for(sessions)),
    ):
        mount, inner = _mount(sessions, name, tools, verifier)
        mounts.append(mount)
        inners.append(inner)
    app = Starlette(routes=mounts)

    def http(headers: dict[str, str]) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=headers
        )

    routes = dict.fromkeys(FINANCE_TOOLS, f"{BASE}/finance/mcp")
    routes |= dict.fromkeys(NOTIFY_TOOLS, f"{BASE}/notify/mcp")
    routes |= dict.fromkeys(MEMORY_TOOLS, f"{BASE}/memory/mcp")
    executor = McpToolExecutor(routes, TOKENS, http=http)
    yield Env(executor, routes, app, sessions, migrated_db, inners)
    await engine.dispose()


def token_verifier_for(sessions: async_sessionmaker[AsyncSession]) -> Verifier:
    from kharcha_common import mcp_tokens
    from kharcha_common.time import utcnow

    async def verify(token: str) -> Identity | None:
        async with sessions.begin() as session:
            user_id = await mcp_tokens.verify(session, token, utcnow())
        return Identity(user_id, "external", external=True) if user_id else None

    return verify


def ctx(user: str, agent: str = "coach") -> RunContext:
    return RunContext(run_id="r", user_id=user, agent=agent)


async def test_tools_are_scoped_to_the_token_user(env: Env) -> None:
    async with env.serve():
        a = await env.executor.call(ctx(A), "list_transactions", {"days": 7})
        b = await env.executor.call(ctx(B), "list_transactions", {"days": 7})
        assert a.ok
        assert b.ok
        assert [i["amountPaise"] for i in a.data["items"]] == [35_500]
        assert [i["amountPaise"] for i in b.data["items"]] == [99_900]
        weekly = await env.executor.call(ctx(A), "get_weekly_summary", {"week": "this"})
        assert weekly.data["spentPaise"] in (0, 35_500)  # 0 only when "1 hour ago" was last week


async def test_server_denies_tools_outside_the_permission_table(env: Env) -> None:
    async with env.serve():
        # The runtime would never send this; call the server directly to prove the second layer.
        denied = await env.executor.call(
            ctx(A, "coach"), "write_memory", {"kind": "FACT", "content": "x y z"}
        )
        assert not denied.ok
        assert denied.data["error"] == "DENIED_TOOL"
        keeper = await env.executor.call(
            ctx(A, "memory_keeper"),
            "write_memory",
            {"kind": "COMMITMENT", "content": "Limit Zomato to 2 orders a week until 31 Oct"},
        )
        assert keeper.ok
        assert keeper.data["action"] == "inserted"
        found = await env.executor.call(ctx(A), "search_memories", {"query": "zomato orders limit"})
        assert [m["kind"] for m in found.data["memories"]] == ["COMMITMENT"]
        other = await env.executor.call(ctx(B), "search_memories", {"query": "zomato orders limit"})
        assert other.data["memories"] == []


async def test_bad_tokens_are_rejected(env: Env) -> None:
    async with env.serve():

        def http(headers: dict[str, str]) -> httpx2.AsyncClient:
            return httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=env.app),
                base_url=BASE,
                headers={"Authorization": "Bearer not-a-token"},
            )

        forged = McpToolExecutor(env.routes, TOKENS, http=http)
        result = await forged.call(ctx(A), "list_transactions", {})
        assert not result.ok
        other_secret = McpToolExecutor(env.routes, ServiceTokens("x" * 40), http=env.executor._http)
        assert not (await other_secret.call(ctx(A), "list_transactions", {})).ok


class Scripted:
    def __init__(self, *turns: ModelResponse) -> None:
        self.turns = list(turns)

    async def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[ToolSpec]
    ) -> ModelResponse:
        return self.turns.pop(0)


async def test_agent_runs_through_mcp_and_runtime_also_blocks(env: Env) -> None:
    async with env.serve():
        config = AgentConfig(
            "coach", "s", "coach/v1", "m", frozenset({"list_transactions", PROPOSE})
        )
        model = Scripted(
            ModelResponse(
                None, (ToolCall("c0", "write_memory", {"kind": "FACT", "content": "abc"}),)
            ),
            ModelResponse(None, (ToolCall("c1", "list_transactions", {"days": 7}),)),
            ModelResponse(
                None,
                (
                    ToolCall(
                        "p",
                        PROPOSE,
                        {"type": "NUDGE", "text": "₹355 on Zomato today.", "reason": "r"},
                    ),
                ),
            ),
            ModelResponse("done"),
        )
        specs = [t.spec for t in (FINANCE_TOOLS | MEMORY_TOOLS).values()]
        result = await run_agent(config, ctx(A), "g", model, env.executor, specs)
        assert result.status is RunStatus.OK
        assert [p.text for p in result.proposals] == ["₹355 on Zomato today."]
        assert [e["kind"] for e in result.transcript].count("denied") == 1  # runtime layer


async def test_external_personal_tokens(env: Env) -> None:
    async with env.serve():
        api = create_app(
            Settings(
                env="test", database_url=env.migrated.async_url, api_keys={hash_api_key("k_a"): A}
            ),
            publisher=NullPublisher(),
            redis=fakeredis.FakeAsyncRedis(),
        )
        async with (
            api.router.lifespan_context(api),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=api), base_url="http://t"
            ) as client,
        ):
            auth = {"Authorization": "Bearer k_a"}
            issued = (
                await client.post("/v1/mcp-tokens", json={"label": "Claude"}, headers=auth)
            ).json()
            token = issued["token"]
            assert token.startswith("kmcp_")

            def http(headers: dict[str, str]) -> httpx2.AsyncClient:
                return httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=env.app),
                    base_url=BASE,
                    headers={"Authorization": f"Bearer {token}"},
                )

            external = McpToolExecutor(
                dict.fromkeys(EXTERNAL_TOOLS, f"{BASE}/external/mcp")
                | {"run_analyst_sql": f"{BASE}/external/mcp"},
                TOKENS,
                http=http,
            )
            txns = await external.call(ctx("ignored"), "list_transactions", {"limit": 100})
            assert txns.ok
            assert [i["amountPaise"] for i in txns.data["items"]] == [35_500]  # user from the token
            assert "accountHint" not in json.dumps(txns.data)
            assert not (await external.call(ctx(A), "run_analyst_sql", {})).ok  # not exposed

            listed = (await client.get("/v1/mcp-tokens", headers=auth)).json()
            assert [t["label"] for t in listed] == ["Claude"]
            assert (
                await client.delete(f"/v1/mcp-tokens/{issued['id']}", headers=auth)
            ).status_code == 204
            assert not (await external.call(ctx(A), "get_cash_balance", {})).ok  # revoked
        assert datetime.now(UTC)
