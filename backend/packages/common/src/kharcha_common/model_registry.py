"""Model registry over ``model_versions`` (PROJECT_SPEC §15.5-15.7).

CANDIDATE -> SHADOW -> ACTIVE (the previous ACTIVE becomes RETIRED). ACTIVE requires the
promotion gate in the eval report to have passed. Rollback = promote the previous version.
"""

from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kharcha_common.db.models import ModelVersion


class ModelStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class PromotionError(ValueError):
    pass


async def register(
    session: AsyncSession,
    manifest: dict[str, Any],
    artifact_uri: str,
    eval_report: dict[str, Any],
    kind: str = "PARSER",
) -> str:
    model_id = f"{kind.lower()}-{manifest['version']}-{str(manifest['quant']).lower()}"
    await session.execute(
        insert(ModelVersion)
        .values(
            id=model_id,
            kind=kind,
            base_model=manifest.get("baseModel"),
            artifact_uri=artifact_uri,
            sha256=manifest["sha256"],
            eval_report={"manifest": manifest, **eval_report},
            status=ModelStatus.CANDIDATE.value,
        )
        .on_conflict_do_nothing()
    )
    return model_id


async def promote(session: AsyncSession, model_id: str, to: ModelStatus) -> None:
    row = await session.get(ModelVersion, model_id)
    if row is None:
        raise PromotionError(f"unknown model {model_id}")
    if to is ModelStatus.ACTIVE:
        if not row.eval_report.get("gate", {}).get("passed"):
            raise PromotionError("promotion gate not passed in the eval report (§15.5)")
        await session.execute(
            update(ModelVersion)
            .where(ModelVersion.kind == row.kind, ModelVersion.status == ModelStatus.ACTIVE.value)
            .values(status=ModelStatus.RETIRED.value)
        )
    row.status = to.value
    await session.flush()


async def active(session: AsyncSession, kind: str = "PARSER") -> ModelVersion | None:
    return (
        await session.execute(
            select(ModelVersion)
            .where(ModelVersion.kind == kind, ModelVersion.status == ModelStatus.ACTIVE.value)
            .order_by(ModelVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
