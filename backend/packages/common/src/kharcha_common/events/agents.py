"""Agent task payloads (PROJECT_SPEC §7.3). Results/feedback payloads arrive with W8."""

from enum import StrEnum

from pydantic import AwareDatetime, Field

from kharcha_common.events.base import CamelModel


class AgentName(StrEnum):
    COACH = "COACH"
    CASH_DETECTIVE = "CASH_DETECTIVE"
    REFUND_ADVOCATE = "REFUND_ADVOCATE"
    MEMORY_KEEPER = "MEMORY_KEEPER"


class AgentTrigger(StrEnum):
    WEEKLY_REVIEW = "WEEKLY_REVIEW"
    RISK_MOMENT = "RISK_MOMENT"
    MISSING_CASH = "MISSING_CASH"
    REFUND_OVERDUE = "REFUND_OVERDUE"
    FEEDBACK = "FEEDBACK"
    USER_MESSAGE = "USER_MESSAGE"
    # Deterministic insights triggers from §22.1 (added to the §7.3 list).
    BUDGET = "BUDGET"
    FREQUENCY = "FREQUENCY"
    BROKE_DATE_MOVED = "BROKE_DATE_MOVED"


class Priority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


class AgentTaskPayload(CamelModel):
    task_id: str
    agent: AgentName
    trigger: AgentTrigger
    goal: str
    # Ids and small keys the agent needs to look things up (e.g. week, category, merchant).
    context_refs: dict[str, str | None] = Field(default_factory=dict)
    bandit_decision: str | None = None
    deadline: AwareDatetime
    priority: Priority = Priority.NORMAL
    # Dedupe key for the resulting alert (alerts_sent.dedupe_key).
    dedupe_key: str | None = None
