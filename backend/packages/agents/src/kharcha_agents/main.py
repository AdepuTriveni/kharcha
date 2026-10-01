"""Agents service (``kharcha-agents``): orchestrator + agent workers on ``agent-tasks``."""

import asyncio

from faststream import AckPolicy, FastStream
from faststream.confluent import KafkaBroker
from faststream.confluent.annotations import KafkaMessage
from prometheus_client import start_http_server
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_agents.orchestrator import ORCHESTRATOR_CONSUMER, AgentsDeps, handle_agent_task
from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.kafka import BrokerPublisher, make_broker, run_with_retry_and_dlt
from kharcha_common.logging import configure_logging
from kharcha_common.settings import Settings, get_settings
from kharcha_common.topics import Topic
from kharcha_mcp_finance.tools import TOOLS as FINANCE_TOOLS
from kharcha_mcp_kit.client import McpToolExecutor
from kharcha_mcp_kit.identity import ServiceTokens
from kharcha_mcp_memory.server import configure_embedder
from kharcha_mcp_memory.tools import TOOLS as MEMORY_TOOLS
from kharcha_mcp_notify.tools import TOOLS as NOTIFY_TOOLS
from kharcha_mcp_refund.tools import TOOLS as REFUND_TOOLS
from kharcha_runtime.config import load_agent_config
from kharcha_runtime.models import LiteLLMModel
from kharcha_runtime.tools import InProcessExecutor
from kharcha_runtime.types import ToolExecutor

ALL_TOOLS = FINANCE_TOOLS | NOTIFY_TOOLS | MEMORY_TOOLS | REFUND_TOOLS


def build_executor(settings: Settings, sessions: async_sessionmaker[AsyncSession]) -> ToolExecutor:
    """MCP clients when the tool servers are configured (W13), else the same handlers in-process."""
    servers = [
        (FINANCE_TOOLS, settings.mcp_finance_url),
        (NOTIFY_TOOLS, settings.mcp_notify_url),
        (MEMORY_TOOLS, settings.mcp_memory_url),
        (REFUND_TOOLS, settings.mcp_refund_url),
    ]
    if settings.service_token_secret and all(url for _, url in servers):
        routes = {name: str(url) for tools, url in servers for name in tools}
        return McpToolExecutor(routes, ServiceTokens(settings.service_token_secret))
    configure_embedder(settings)
    return InProcessExecutor(sessions, ALL_TOOLS)


def build_deps(settings: Settings, publisher: BrokerPublisher) -> AgentsDeps:
    sessions = make_sessionmaker(make_engine(settings))
    executor = build_executor(settings, sessions)
    config, models = load_agent_config("coach", settings.llm_model)
    model = (
        LiteLLMModel(models, settings.ollama_api_base, settings.llm_timeout_s)
        if settings.coach_llm_enabled
        else None
    )
    specs = [t.spec for t in ALL_TOOLS.values()]
    return AgentsDeps(sessions, publisher, executor, specs, config, model)


def build_broker(settings: Settings) -> KafkaBroker:
    broker = make_broker(settings, client_id="kharcha-agents")
    publisher = BrokerPublisher(broker)
    deps = build_deps(settings, publisher)

    @broker.subscriber(
        Topic.AGENT_TASKS,
        group_id=ORCHESTRATOR_CONSUMER,
        auto_offset_reset="earliest",
        ack_policy=AckPolicy.NACK_ON_ERROR,
    )
    async def on_task(message: KafkaMessage) -> None:
        key = message.raw_message.key()  # type: ignore[union-attr]
        await run_with_retry_and_dlt(
            lambda body: handle_agent_task(body, deps),
            body=message.body,
            key=key if isinstance(key, bytes) else None,
            topic=Topic.AGENT_TASKS,
            consumer=ORCHESTRATOR_CONSUMER,
            publisher=publisher,
        )

    return broker


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    start_http_server(settings.metrics_port + 3)
    asyncio.run(FastStream(build_broker(settings)).run())
