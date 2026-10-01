"""parse_rules persistence: seeds, lookup by sender, counters and status changes (§10.4)."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import ParseRule
from kharcha_processor import metrics
from kharcha_processor.rules import (
    CompiledRule,
    RuleOrigin,
    RuleStatus,
    UnsafeRuleError,
    check_rule,
    compile_rule,
    next_status,
)

log = logging.getLogger(__name__)

SEED_RULES_FILE = Path(__file__).resolve().parents[4] / "seeds" / "seed-rules.yaml"


@dataclass(frozen=True, slots=True)
class SeedRule:
    id: str
    sender_key: str
    regex: str
    field_map: dict[str, Any]


def load_seed_rules(path: Path = SEED_RULES_FILE) -> list[SeedRule]:
    data: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text(encoding="utf-8"))
    rules = []
    for rule_id, spec in data.items():
        field_map: dict[str, Any] = {
            k: spec[k] for k in ("direction", "channel", "status") if k in spec
        }
        check_rule(spec["regex"], field_map)
        rules.append(SeedRule(str(rule_id), str(spec["sender"]), str(spec["regex"]), field_map))
    return rules


async def seed_rules(session: AsyncSession, path: Path = SEED_RULES_FILE) -> int:
    """Insert seed rules as ACTIVE. Existing rows keep their counters and status."""
    seeds = load_seed_rules(path)
    for seed in seeds:
        await session.execute(
            insert(ParseRule)
            .values(
                id=seed.id,
                sender_pattern=seed.sender_key,
                regex=seed.regex,
                field_map=seed.field_map,
                status=RuleStatus.ACTIVE.value,
                origin=RuleOrigin.SEED.value,
            )
            .on_conflict_do_nothing()
        )
    return len(seeds)


async def rules_for(session: AsyncSession, key: str | None) -> list[CompiledRule]:
    """ACTIVE and CANDIDATE rules for a sender key, active first, oldest first."""
    if key is None:
        return []
    rows = await session.scalars(
        select(ParseRule)
        .where(
            ParseRule.sender_pattern == key,
            ParseRule.status.in_([RuleStatus.ACTIVE.value, RuleStatus.CANDIDATE.value]),
        )
        .order_by(ParseRule.status, ParseRule.created_at, ParseRule.id)
    )
    out = []
    for row in rows:
        try:
            out.append(compile_rule(row.id, key, row.regex, row.field_map, row.status))
        except UnsafeRuleError as exc:
            log.warning("skipping unsafe rule", extra={"rule_id": row.id, "reason": str(exc)})
    return out


async def insert_candidate(
    session: AsyncSession, rule_id: str, key: str, regex: str, field_map: dict[str, Any]
) -> bool:
    result = await session.execute(
        insert(ParseRule)
        .values(
            id=rule_id,
            sender_pattern=key,
            regex=regex,
            field_map=field_map,
            status=RuleStatus.CANDIDATE.value,
            origin=RuleOrigin.SYNTHESIZED.value,
        )
        .on_conflict_do_nothing()
        .returning(ParseRule.id)
    )
    return result.scalar_one_or_none() is not None


async def record_observation(session: AsyncSession, rule_id: str, *, agreed: bool) -> RuleStatus:
    """Bump a counter and apply promotion / auto-disable. Call inside the idempotent tx."""
    column = ParseRule.match_count if agreed else ParseRule.mismatch_count
    row = (
        await session.execute(
            update(ParseRule)
            .where(ParseRule.id == rule_id)
            .values({column: column + 1})
            .returning(ParseRule.status, ParseRule.match_count, ParseRule.mismatch_count)
        )
    ).one_or_none()
    if row is None:
        return RuleStatus.DISABLED
    status, matches, mismatches = RuleStatus(row[0]), row[1], row[2]
    metrics.RULE_OBSERVATIONS.labels("agree" if agreed else "mismatch").inc()
    new_status = next_status(status, matches, mismatches)
    if new_status is not status:
        await session.execute(
            update(ParseRule).where(ParseRule.id == rule_id).values(status=new_status.value)
        )
        metrics.RULE_STATUS_CHANGES.labels(new_status.value).inc()
        log.info("rule status changed", extra={"rule_id": rule_id, "status": new_status.value})
    return new_status
