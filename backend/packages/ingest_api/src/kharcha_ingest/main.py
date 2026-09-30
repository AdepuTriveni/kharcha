"""FastAPI app for phone uploads (PROJECT_SPEC §9). Phase 0: health endpoint only."""

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

from kharcha_common.settings import Settings, get_settings


class Health(BaseModel):
    status: str
    env: str


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Kharcha ingest-api", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> Health:
        return Health(status="ok", env=settings.env)

    return app


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - container entry point
        port=8000,
        log_level=settings.log_level.lower(),
    )
