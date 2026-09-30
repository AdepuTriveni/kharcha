"""JSON logging. Never log message text or full account numbers (CLAUDE.md rule 4).

Pass identifiers through ``extra``: ``log.info("parsed", extra={"event_id": ..., "user": ...})``.
User ids are hashed before they are written.
"""

import hashlib
import json
import logging
import sys
from typing import Any

_EXTRA_FIELDS = ("event_id", "consumer", "topic", "reason", "method", "status", "trace_id")


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:12]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            if (value := getattr(record, field, None)) is not None:
                entry[field] = value
        if (user := getattr(record, "user", None)) is not None:
            entry["user"] = hash_user_id(str(user))
        if record.exc_info:
            entry["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
