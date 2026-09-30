"""Ingest API metrics (PROJECT_SPEC §29)."""

from prometheus_client import Counter

EVENTS_RECEIVED = Counter(
    "kharcha_events_received_total", "Uploaded events by result status", ["status"]
)
