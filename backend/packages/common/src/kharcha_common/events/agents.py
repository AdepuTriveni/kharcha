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


class ProposalType(StrEnum):
    ROAST = "ROAST"
    NUDGE = "NUDGE"
    HYPE = "HYPE"
    QUESTION = "QUESTION"
    INFO = "INFO"


class AgentRunStatus(StrEnum):
    OK = "OK"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    DENIED_TOOL = "DENIED_TOOL"


class Proposal(CamelModel):
    """A message an agent wants sent. Only the notifier's policy gate can send it (rule 5)."""

    type: ProposalType
    text: str = Field(max_length=400)
    category: str | None = None
    buttons: list[str] = Field(default_factory=list, max_length=4)
    reason: str = ""
    grounding_numbers: list[str] = Field(default_factory=list)
    # True when written by a fixed template instead of a model (fallback path).
    templated: bool = False


class AgentResultPayload(CamelModel):
    task_id: str
    agent: AgentName
    run_id: str
    status: AgentRunStatus
    trigger: AgentTrigger
    proposals: list[Proposal] = Field(default_factory=list, max_length=3)
    memory_writes: list[dict[str, str]] = Field(default_factory=list)
    drafts: list[dict[str, str]] = Field(default_factory=list)
    dedupe_key: str | None = None
