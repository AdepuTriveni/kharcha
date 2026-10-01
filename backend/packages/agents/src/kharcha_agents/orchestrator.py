"""Orchestrator (PROJECT_SPEC §17.1, §17.5): ``agent-tasks`` -> agent run -> ``agent-results``.

Infrastructure, not agent code: it may use the database (dedupe, daily budget, transcripts);
agents themselves only see tools. Results carry proposals; nothing is sent from here.

- Dedupe by event id (``processed_events``); tasks past their deadline are dropped.
- Daily run budget per user; once spent, only higher-priority agents run
  (Refund Advocate > Cash Detective > Coach > Memory Keeper).
- A failed run (ERROR/TIMEOUT) is retried once (the model adapter falls back across models);
  the deterministic templates from the same tools are always available.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_agents import cash_detective, coach, memory_keeper
from kharcha_common.db.models import AgentRunRow, User
from kharcha_common.events import (
    AgentName,
    AgentResultEvent,
    AgentResultPayload,
    AgentRunStatus,
    AgentTaskEvent,
    AgentTaskPayload,
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
RETRY_STATUSES = frozenset({RunStatus.ERROR, RunStatus.TIMEOUT})

Goal = Callable[[AgentTaskPayload, str], str]
Fallback = Callable[[AgentTaskPayload, ToolExecutor, RunContext], Awaitable[list[Proposal]]]


@dataclass(frozen=True, slots=True)
class AgentSpec:
    name: AgentName
    key: str  # agents.yaml key and runtime agent name
    priority: int  # higher wins when the daily budget is spent
    goal: Goal
    fallback: Fallback


REGISTRY: dict[AgentName, AgentSpec] = {
    AgentName.COACH: AgentSpec(AgentName.COACH, "coach", 1, coach.goal_text, coach.fallback),
    AgentName.CASH_DETECTIVE: AgentSpec(
        AgentName.CASH_DETECTIVE,
        "cash_detective",
        2,
        cash_detective.goal_text,
        cash_detective.fallback,
    ),
    AgentName.MEMORY_KEEPER: AgentSpec(
        AgentName.MEMORY_KEEPER,
        "memory_keeper",
        0,
        memory_keeper.goal_text,
        memory_keeper.fallback,
    ),
}
PRIORITY_WHEN_OVER_BUDGET = 2  # Cash Detective and Refund Advocate still run


@dataclass(frozen=True)
class AgentsDeps:
    sessions: async_sessionmaker[AsyncSession]
    publisher: EventPublisher
    tools: ToolExecutor
    specs: list[ToolSpec]
    configs: dict[str, AgentConfig]  # by AgentSpec.key
    model: Model | None  # None -> templates only (no LLM configured)


def run_id_for(task_id: str) -> str:
    return "run_" + derived_event_id("run", task_id).replace("-", "")


async def _runs_today(session: AsyncSession, user_id: str, now: datetime) -> int:
    start = datetime.combine(to_ist(now).date(), datetime.min.time(), tzinfo=IST)
    count = await session.scalar(
        select(func.count()).where(AgentRunRow.user_id == user_id, AgentRunRow.started_at >= start)
    )
    return int(count or 0)


def _merge(spec: AgentSpec, model: list[Proposal], templates: list[Proposal]) -> list[Proposal]:
    """Model proposals first; one templated plain proposal last so the gate can downgrade.

    The Cash Detective's buttons always come from the template (amounts from tools); a model
    question only replaces the wording.
    """
    if spec.name is AgentName.CASH_DETECTIVE:
        if not templates:
            return []
        question = next((p for p in model if p.type is ProposalType.QUESTION), None)
        base = templates[0]
        return (
            [base.model_copy(update={"text": question.text, "templated": False})]
            if question
            else [base]
        )
    out = model[:2]
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


async def _mark(deps: AgentsDeps, event_id: str) -> None:
    async with deps.sessions.begin() as session:
        await mark_processed(session, ORCHESTRATOR_CONSUMER, event_id)


async def _run(
    deps: AgentsDeps, spec: AgentSpec, task: AgentTaskPayload, ctx: RunContext, level: str
) -> RunResult:
    if deps.model is None:
        return RunResult(ctx.run_id, RunStatus.OK, [], [{"kind": "templates_only"}])
    config = deps.configs[spec.key]
    goal = spec.goal(task, level)
    result = await run_agent(config, ctx, goal, deps.model, deps.tools, deps.specs)
    if result.status in RETRY_STATUSES:
        log.info("agent run retried", extra={"run_id": ctx.run_id, "status": result.status.value})
        retry = await run_agent(config, ctx, goal, deps.model, deps.tools, deps.specs)
        retry.transcript = [*result.transcript, {"kind": "retry"}, *retry.transcript]
        return retry
    return result


async def handle_agent_task(body: bytes, deps: AgentsDeps, now: datetime | None = None) -> None:
    event = AgentTaskEvent.model_validate_json(body)
    task = event.payload
    now = now or utcnow()
    spec = REGISTRY.get(task.agent)
    async with deps.sessions() as session:
        if await is_processed(session, ORCHESTRATOR_CONSUMER, event.event_id):
            return
        user = await session.get(User, event.user_id)
        over_budget = await _runs_today(session, event.user_id, now) >= MAX_RUNS_PER_DAY
    reason = None
    if user is None or spec is None:
        reason = "unknown user or agent"
    elif utcnow() > task.deadline:  # wall clock: stale nudges are never sent
        reason = "deadline passed"
    elif over_budget and spec.priority < PRIORITY_WHEN_OVER_BUDGET:
        reason = "daily budget"
    if reason is not None or spec is None or user is None:
        log.info("task skipped", extra={"event_id": event.event_id, "reason": reason})
        await _mark(deps, event.event_id)
        return

    ctx = RunContext(run_id_for(task.task_id), event.user_id, spec.key, task.task_id, now)
    started = utcnow()
    result = await _run(deps, spec, task, ctx, user.roast_level)
    templates = await spec.fallback(task, deps.tools, ctx)
    proposals = _merge(spec, _to_event_proposals(result), templates)
    config = deps.configs.get(spec.key)

    async with deps.sessions.begin() as session:
        await session.execute(
            insert(AgentRunRow)
            .values(
                id=ctx.run_id,
                user_id=event.user_id,
                task_id=task.task_id,
                agent=spec.name.value,
                prompt_version=config.prompt_version if config else "none",
                model=config.model if config and deps.model is not None else "template",
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
                proposals=proposals[:3],
                dedupe_key=task.dedupe_key,
            ),
        ),
    )
    # After publishing: a crash in between republishes the same result id, which is harmless.
    await _mark(deps, event.event_id)
    log.info(
        "task done",
        extra={"event_id": event.event_id, "status": result.status.value, "n": len(proposals)},
    )
