"""Agents service (``kharcha-agents``): orchestrator + agent workers on ``agent-tasks``."""

import asyncio

from faststream import AckPolicy, FastStream
from faststream.confluent import KafkaBroker
from faststream.confluent.annotations import KafkaMessage
from prometheus_client import start_http_server

from kharcha_agents.orchestrator import ORCHESTRATOR_CONSUMER, AgentsDeps, handle_agent_task
from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.kafka import BrokerPublisher, make_broker, run_with_retry_and_dlt
from kharcha_common.logging import configure_logging
from kharcha_common.settings import Settings, get_settings
from kharcha_common.topics import Topic
from kharcha_mcp_finance.tools import TOOLS as FINANCE_TOOLS
from kharcha_mcp_notify.tools import TOOLS as NOTIFY_TOOLS
from kharcha_runtime.config import load_agent_config
from kharcha_runtime.models import LiteLLMModel
from kharcha_runtime.tools import InProcessExecutor


def build_deps(settings: Settings, publisher: BrokerPublisher) -> AgentsDeps:
    sessions = make_sessionmaker(make_engine(settings))
    executor = InProcessExecutor(sessions, FINANCE_TOOLS | NOTIFY_TOOLS)
    config, models = load_agent_config("coach", settings.llm_model)
    model = (
        LiteLLMModel(models, settings.ollama_api_base, settings.llm_timeout_s)
        if settings.coach_llm_enabled
        else None
    )
    return AgentsDeps(sessions, publisher, executor, executor.specs, config, model)


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
