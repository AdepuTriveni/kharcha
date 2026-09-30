"""Deterministic triggers (PROJECT_SPEC §22.1): clean transactions -> Coach ``agent-tasks``.

W3: merchant frequency (3+ payments to one merchant in 7 days) and budget thresholds
(80% / 100% of a category's monthly budget). Broke-date and weekly triggers arrive in W7-W8.
Task ids are derived from the dedupe key, so re-processing publishes the same task.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import Budget, TransactionRow
from kharcha_common.events import (
    AgentName,
    AgentTaskPayload,
    AgentTrigger,
    CleanTransactionPayload,
    Priority,
    TxnKind,
    TxnStatus,
)
from kharcha_common.kafka import derived_event_id
from kharcha_common.time import to_ist

FREQUENCY_THRESHOLD = 3
FREQUENCY_WINDOW = timedelta(days=7)
BUDGET_THRESHOLDS = (80, 100)
TASK_DEADLINE = timedelta(hours=2)

# Person VPAs are hashed on the phone (p<hash>@handle): transfers to people are never nudged.
_PERSON_VPA = re.compile(r"^p[0-9a-f]{8}@")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    trigger: AgentTrigger
    goal: str
    refs: dict[str, str | None]
    dedupe_key: str
    priority: Priority


def to_payload(spec: TaskSpec, user_id: str, now: datetime) -> AgentTaskPayload:
    return AgentTaskPayload(
        task_id="at_" + derived_event_id("task", user_id, spec.dedupe_key).replace("-", ""),
        agent=AgentName.COACH,
        trigger=spec.trigger,
        goal=spec.goal,
        context_refs=spec.refs,
        deadline=now + TASK_DEADLINE,
        priority=spec.priority,
        dedupe_key=spec.dedupe_key,
    )


async def frequency_task(
    session: AsyncSession, user_id: str, txn: TransactionRow
) -> TaskSpec | None:
    merchant = txn.merchant_raw
    if not merchant or _PERSON_VPA.match(merchant):
        return None
    count = (
        await session.execute(
            select(func.count()).where(
                TransactionRow.user_id == user_id,
                TransactionRow.merchant_raw == merchant,
                TransactionRow.kind == TxnKind.SPEND.value,
                TransactionRow.status == TxnStatus.SUCCESS.value,
                TransactionRow.txn_time > txn.txn_time - FREQUENCY_WINDOW,
                TransactionRow.txn_time <= txn.txn_time,
            )
        )
    ).scalar_one()
    if count < FREQUENCY_THRESHOLD:
        return None
    year, week, _ = to_ist(txn.txn_time).isocalendar()
    return TaskSpec(
        trigger=AgentTrigger.FREQUENCY,
        goal=f"Frequent spending at {merchant}",
        refs={"merchant": merchant, "category": txn.category},
        dedupe_key=f"freq:{merchant}:{year}-W{week:02d}",
        priority=Priority.NORMAL,
    )


async def budget_task(session: AsyncSession, user_id: str, txn: TransactionRow) -> TaskSpec | None:
    limit = (
        await session.execute(
            select(Budget.monthly_limit_paise).where(
                Budget.user_id == user_id, Budget.category == txn.category
            )
        )
    ).scalar_one_or_none()
    if not limit:
        return None
    local = to_ist(txn.txn_time)
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent_after = int(
        (
            await session.execute(
                select(func.coalesce(func.sum(TransactionRow.amount_paise), 0)).where(
                    TransactionRow.user_id == user_id,
                    TransactionRow.category == txn.category,
                    TransactionRow.kind == TxnKind.SPEND.value,
                    TransactionRow.status == TxnStatus.SUCCESS.value,
                    TransactionRow.txn_time >= month_start,
                    TransactionRow.txn_time <= txn.txn_time,
                )
            )
        ).scalar_one()
    )
    spent_before = spent_after - txn.amount_paise
    crossed = [t for t in BUDGET_THRESHOLDS if spent_before * 100 < t * limit <= spent_after * 100]
    if not crossed:
        return None
    threshold = max(crossed)
    return TaskSpec(
        trigger=AgentTrigger.BUDGET,
        goal=f"{txn.category} budget {threshold}% used",
        refs={"category": txn.category, "threshold": str(threshold)},
        dedupe_key=f"budget:{txn.category}:{local:%Y-%m}:{threshold}",
        priority=Priority.HIGH,
    )


async def tasks_for(
    session: AsyncSession, user_id: str, clean: CleanTransactionPayload
) -> list[TaskSpec]:
    if clean.kind is not TxnKind.SPEND or clean.status is not TxnStatus.SUCCESS:
        return []
    txn = (
        await session.execute(
            select(TransactionRow).where(
                TransactionRow.id == clean.transaction_id, TransactionRow.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if txn is None:
        return []
    specs = [await budget_task(session, user_id, txn), await frequency_task(session, user_id, txn)]
    return [s for s in specs if s is not None]
