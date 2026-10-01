"""Run an agent task the way production does: orchestrator -> agent-results -> policy gate."""

from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_agents.main import ALL_TOOLS
from kharcha_agents.orchestrator import AgentsDeps, handle_agent_task
from kharcha_common.kafka import EventPublisher
from kharcha_common.topics import Topic
from kharcha_notifier.bot import BotDeps
from kharcha_notifier.results import handle_agent_result
from kharcha_runtime.config import load_agent_config
from kharcha_runtime.tools import InProcessExecutor
from kharcha_runtime.types import Model


class Queues(EventPublisher, Protocol):
    queues: dict[str, list[bytes]]


def agents_deps(
    sessions: async_sessionmaker[AsyncSession], publisher: Queues, model: Model | None = None
) -> AgentsDeps:
    executor = InProcessExecutor(sessions, ALL_TOOLS)
    configs = {
        key: load_agent_config(key, "test-model")[0]
        for key in ("coach", "cash_detective", "memory_keeper")
    }
    return AgentsDeps(sessions, publisher, executor, executor.specs, configs, model)


async def deliver(
    task_body: bytes, bot: BotDeps, publisher: Queues, now: datetime, model: Model | None = None
) -> None:
    await handle_agent_task(task_body, agents_deps(bot.sessions, publisher, model), now=now)
    for body in publisher.queues.pop(Topic.AGENT_RESULTS.value, []):
        await handle_agent_result(body, bot, now=now)
