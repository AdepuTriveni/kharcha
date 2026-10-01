"""``GET /v1/models/parser/latest`` (PROJECT_SPEC §9, §15.6): on-device model manifest.

The phone downloads over Wi-Fi only, checks ``minRamMb`` and verifies ``sha256`` first.
"""

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kharcha_common.events import CamelModel
from kharcha_common.model_registry import active
from kharcha_ingest.auth import CurrentUser

router = APIRouter(prefix="/v1")


class ModelManifest(CamelModel):
    id: str
    version: str
    url: str
    sha256: str
    size_bytes: int
    min_ram_mb: int
    prompt_version: str


@router.get("/models/parser/latest")
async def latest_parser(user_id: CurrentUser, request: Request) -> ModelManifest:
    sessions: async_sessionmaker[AsyncSession] = request.app.state.sessions
    async with sessions() as session:
        row = await active(session)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active parser model")
    manifest = row.eval_report.get("manifest", {})
    return ModelManifest(
        id=row.id,
        version=str(manifest.get("version", row.id)),
        url=row.artifact_uri,
        sha256=row.sha256,
        size_bytes=int(manifest.get("sizeBytes", 0)),
        min_ram_mb=int(manifest.get("minRamMb", 4000)),
        prompt_version=str(manifest.get("promptVersion", "parser/v1")),
    )
