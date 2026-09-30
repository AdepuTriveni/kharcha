import httpx

from kharcha_common.settings import Settings
from kharcha_ingest.main import create_app


async def test_healthz() -> None:
    app = create_app(Settings(env="test"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "env": "test"}
