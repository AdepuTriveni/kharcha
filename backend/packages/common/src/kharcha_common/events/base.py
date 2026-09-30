"""Event envelope (PROJECT_SPEC §7.1). JSON on the wire uses camelCase."""

import uuid
from typing import Any, ClassVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from kharcha_common.ids import uuid7
from kharcha_common.time import utcnow

SCHEMA_VERSION = 1


class CamelModel(BaseModel):
    """Base for every wire model: camelCase JSON, strict about unknown fields."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class EventEnvelope[PayloadT: BaseModel](CamelModel):
    """Envelope shared by all topics. ``eventId`` is a UUIDv7 (phone-generated for raw events)."""

    SCHEMA_VERSION: ClassVar[int] = SCHEMA_VERSION

    event_id: str = Field(default_factory=lambda: str(uuid7()))
    user_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    schema_version: int = SCHEMA_VERSION
    occurred_at: AwareDatetime
    produced_at: AwareDatetime = Field(default_factory=utcnow)
    producer: str = Field(min_length=1)
    causation_id: str | None = None
    payload: PayloadT

    @field_validator("event_id")
    @classmethod
    def _event_id_is_uuid(cls, value: str) -> str:
        return str(uuid.UUID(value))

    def kafka_key(self) -> bytes:
        """Kafka key is always the user id (per-user ordering)."""
        return self.user_id.encode()

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)
