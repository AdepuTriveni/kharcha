"""Orchestrator (PROJECT_SPEC §17.1): ``agent-tasks`` -> agent run -> ``agent-results``.

Infrastructure, not agent code: it may use the database (dedupe, daily budget, transcripts);
agents themselves only see tools. Results carry proposals; nothing is sent from here.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_agents import coach
from kharcha_common.db.models import AgentRunRow, User
from kharcha_common.events import (
    AgentName,
    AgentResultEvent,
    AgentResultPayload,
    AgentRunStatus,
    AgentTaskEvent,
    Proposal,
    ProposalType,
)
from kharcha_common.idempotency import is_processed, mark_processed
from kharcha_common.kafka import EventPublisher, derived_event_id
from kharcha_common.time import IST, to_ist, utcnow
from kharcha_common.topics import Topic
from kharcha_runtime.loop import run_agent
from kharcha_runtime.types import (
    AgentConfig,
    Model,
    RunContext,
    RunResult,
    RunStatus,
    ToolExecutor,
    ToolSpec,
)

log = logging.getLogger(__name__)

ORCHESTRATOR_CONSUMER = "agents.orchestrator"
RESULT_TYPE = "AGENT_RESULT"
MAX_RUNS_PER_DAY = 12  # per user, all agents


@dataclass(frozen=True)
class AgentsDeps:
    sessions: async_sessionmaker[AsyncSession]
    publisher: EventPublisher
    tools: ToolExecutor
    specs: list[ToolSpec]
    coach_config: AgentConfig
    model: Model | None  # None -> templates only (no LLM configured)


def run_id_for(task_id: str) -> str:
    return "run_" + derived_event_id("run", task_id).replace("-", "")


async def _runs_today(session: AsyncSession, user_id: str, now: datetime) -> int:
    start = datetime.combine(to_ist(now).date(), datetime.min.time(), tzinfo=IST)
    count = await session.scalar(
        select(func.count()).where(AgentRunRow.user_id == user_id, AgentRunRow.started_at >= start)
    )
    return int(count or 0)


def _merge(model_proposals: list[Proposal], templates: list[Proposal]) -> list[Proposal]:
    """Model proposals first; one templated plain nudge last so the gate can always downgrade."""
    out = model_proposals[:2]
    plain = [t for t in templates if t.type is not ProposalType.ROAST]
    if plain and len(out) < 3:
        out.append(plain[0])
    return out


def _to_event_proposals(result: RunResult) -> list[Proposal]:
    return [
        Proposal(
            type=ProposalType(p.type),
            text=p.text,
            category=p.category,
            buttons=list(p.buttons),
            reason=p.reason,
            grounding_numbers=list(p.grounding_numbers),
        )
        for p in result.proposals
    ]


async def handle_agent_task(body: bytes, deps: AgentsDeps, now: datetime | None = None) -> None:
    event = AgentTaskEvent.model_validate_json(body)
    task = event.payload
    now = now or utcnow()
    async with deps.sessions() as session:
        if await is_processed(session, ORCHESTRATOR_CONSUMER, event.event_id):
            return
        user = await session.get(User, event.user_id)
        over_budget = await _runs_today(session, event.user_id, now) >= MAX_RUNS_PER_DAY
    if user is None or task.agent is not AgentName.COACH or over_budget:
        log.info(
            "task skipped",
            extra={"event_id": event.event_id, "reason": "budget" if over_budget else "agent"},
        )
        async with deps.sessions.begin() as session:
            await mark_processed(session, ORCHESTRATOR_CONSUMER, event.event_id)
        return

    ctx = RunContext(run_id_for(task.task_id), event.user_id, "coach", task.task_id, now)
    templates = await coach.fallback(task, deps.tools, ctx)
    started = utcnow()
    if deps.model is not None:
        result = await run_agent(
            deps.coach_config,
            ctx,
            coach.goal_text(task, user.roast_level),
            deps.model,
            deps.tools,
            deps.specs,
        )
    else:
        result = RunResult(ctx.run_id, RunStatus.OK, [], [{"kind": "templates_only"}])
    proposals = _merge(_to_event_proposals(result), templates)

    async with deps.sessions.begin() as session:
        await session.execute(
            insert(AgentRunRow)
            .values(
                id=ctx.run_id,
                user_id=event.user_id,
                task_id=task.task_id,
                agent="COACH",
                prompt_version=deps.coach_config.prompt_version,
                model=deps.coach_config.model if deps.model is not None else "template",
                started_at=started,
                finished_at=utcnow(),
                status=result.status.value,
                transcript=result.transcript,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
            .on_conflict_do_nothing()
        )

    await deps.publisher.publish_event(
        Topic.AGENT_RESULTS,
        AgentResultEvent(
            event_id=derived_event_id("agent-result", task.task_id),
            user_id=event.user_id,
            type=RESULT_TYPE,
            occurred_at=now,
            producer="agents",
            causation_id=event.event_id,
            payload=AgentResultPayload(
                task_id=task.task_id,
                agent=task.agent,
                run_id=ctx.run_id,
                status=AgentRunStatus(result.status.value),
                trigger=task.trigger,
                proposals=proposals,
                dedupe_key=task.dedupe_key,
            ),
        ),
    )
    # After publishing: a crash in between republishes the same result id, which is harmless.
    async with deps.sessions.begin() as session:
        await mark_processed(session, ORCHESTRATOR_CONSUMER, event.event_id)
    log.info(
        "task done",
        extra={"event_id": event.event_id, "status": result.status.value, "n": len(proposals)},
    )
