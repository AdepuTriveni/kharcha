"""Run an agent task the way production does: orchestrator -> agent-results -> policy gate."""

from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_agents.orchestrator import AgentsDeps, handle_agent_task
from kharcha_common.kafka import EventPublisher
from kharcha_common.topics import Topic
from kharcha_mcp_finance.tools import TOOLS as FINANCE_TOOLS
from kharcha_mcp_notify.tools import TOOLS as NOTIFY_TOOLS
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
    executor = InProcessExecutor(sessions, FINANCE_TOOLS | NOTIFY_TOOLS)
    config, _ = load_agent_config("coach", "test-model")
    return AgentsDeps(sessions, publisher, executor, executor.specs, config, model)


async def deliver(
    task_body: bytes, bot: BotDeps, publisher: Queues, now: datetime, model: Model | None = None
) -> None:
    await handle_agent_task(task_body, agents_deps(bot.sessions, publisher, model), now=now)
    for body in publisher.queues.pop(Topic.AGENT_RESULTS.value, []):
        await handle_agent_result(body, bot, now=now)
