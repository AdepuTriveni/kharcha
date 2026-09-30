"""Kafka topics (PROJECT_SPEC §7.2). Key for every topic is ``user_id``.

``infra/kafka/create-topics.sh`` must match this table; a contract test checks it.
"""

from dataclasses import dataclass
from enum import StrEnum

DLT_SUFFIX = ".DLT"
DLT_RETENTION_DAYS = 30


class Topic(StrEnum):
    RAW_EVENTS = "raw-events"
    PARSED_TRANSACTIONS = "parsed-transactions"
    CLEAN_TRANSACTIONS = "clean-transactions"
    CASH_EVENTS = "cash-events"
    AGENT_TASKS = "agent-tasks"
    AGENT_RESULTS = "agent-results"
    USER_FEEDBACK = "user-feedback"
    NUDGE_OUTCOMES = "nudge-outcomes"
    MODEL_SHADOW = "model-shadow"


@dataclass(frozen=True, slots=True)
class TopicSpec:
    partitions_local: int
    partitions_prod: int
    retention_days: int


TOPIC_SPECS: dict[Topic, TopicSpec] = {
    Topic.RAW_EVENTS: TopicSpec(3, 12, 30),
    Topic.PARSED_TRANSACTIONS: TopicSpec(3, 12, 7),
    Topic.CLEAN_TRANSACTIONS: TopicSpec(3, 12, 7),
    Topic.CASH_EVENTS: TopicSpec(3, 12, 7),
    Topic.AGENT_TASKS: TopicSpec(3, 12, 3),
    Topic.AGENT_RESULTS: TopicSpec(3, 12, 7),
    Topic.USER_FEEDBACK: TopicSpec(3, 6, 30),
    Topic.NUDGE_OUTCOMES: TopicSpec(3, 6, 30),
    Topic.MODEL_SHADOW: TopicSpec(3, 3, 7),
}


def dlt(topic: Topic) -> str:
    """Dead-letter topic name for ``topic``."""
    return f"{topic.value}{DLT_SUFFIX}"
