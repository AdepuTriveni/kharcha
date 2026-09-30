"""FastAPI app for phone uploads (PROJECT_SPEC §9)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from prometheus_client import make_asgi_app
from pydantic import BaseModel
from redis.asyncio import Redis

from kharcha_common.db import make_engine, make_sessionmaker
from kharcha_common.kafka import BrokerPublisher, EventPublisher, make_broker
from kharcha_common.logging import configure_logging
from kharcha_common.settings import Settings, get_settings
from kharcha_ingest import events, telegram


class Health(BaseModel):
    status: str
    env: str


def create_app(
    settings: Settings | None = None,
    publisher: EventPublisher | None = None,
    redis: Redis | None = None,
) -> FastAPI:
    """Build the app. Pass ``publisher``/``redis`` in tests to avoid real connections."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings)
        app.state.sessions = make_sessionmaker(engine)
        app.state.redis = redis or Redis.from_url(settings.redis_url, decode_responses=True)
        broker = None
        if publisher is None:
            broker = make_broker(settings, client_id="kharcha-ingest")
            await broker.connect()
            app.state.publisher = BrokerPublisher(broker)
        else:
            app.state.publisher = publisher
        try:
            yield
        finally:
            if broker is not None:
                await broker.stop()
            if redis is None:
                await app.state.redis.aclose()
            await engine.dispose()

    app = FastAPI(title="Kharcha ingest-api", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(events.router)
    app.include_router(telegram.router)
    app.mount("/metrics", make_asgi_app())

    @app.get("/healthz")
    async def healthz() -> Health:
        return Health(status="ok", env=settings.env)

    return app


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",  # noqa: S104 - container entry point
        port=8000,
        log_level=settings.log_level.lower(),
    )
