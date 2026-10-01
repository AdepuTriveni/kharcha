"""W9 on real Postgres: Firebase sign-in creates the user, settings/consent, DELETE /v1/me."""

import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

import fakeredis
import httpx
import jwt
import pytest
import sqlalchemy as sa
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from sqlalchemy.ext.asyncio import create_async_engine

from kharcha_common.events import EventEnvelope
from kharcha_common.linking import create_link_code
from kharcha_common.settings import Settings
from kharcha_ingest.auth import user_id_for_uid
from kharcha_ingest.firebase import FirebaseVerifier
from kharcha_ingest.main import create_app
from tests.integration.conftest import PgUrls

pytestmark = pytest.mark.integration

PROJECT = "kharcha-it"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_NAME = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "it")])
CERT = (
    x509.CertificateBuilder()
    .subject_name(_NAME)
    .issuer_name(_NAME)
    .public_key(KEY.public_key())
    .serial_number(7)
    .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
    .not_valid_after(datetime(2028, 1, 1, tzinfo=UTC))
    .sign(KEY, hashes.SHA256())
    .public_bytes(serialization.Encoding.PEM)
    .decode()
)


def id_token(uid: str) -> str:
    now = int(time.time())
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": uid,
        "iat": now,
        "exp": now + 3600,
        "auth_time": now - 5,
    }
    return jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": "it"})


async def certs() -> tuple[Mapping[str, str], float]:
    return {"it": CERT}, 3600.0


class NullPublisher:
    async def publish_event(self, topic: str, event: EventEnvelope[Any]) -> None:
        return None

    async def publish_raw(
        self, topic: str, body: bytes, key: bytes | None, headers: Mapping[str, str]
    ) -> None:
        return None


@pytest.fixture
async def client(migrated_db: PgUrls) -> AsyncIterator[tuple[httpx.AsyncClient, Any, Any]]:
    engine = create_async_engine(migrated_db.async_url)
    async with engine.begin() as conn:
        for table in (
            "forecasts",
            "agent_runs",
            "alerts_sent",
            "cash_ledger",
            "transaction_sources",
            "transactions",
            "processed_events",
            "raw_events",
            "budgets",
            "user_merchant_overrides",
            "users",
        ):
            await conn.execute(sa.text(f"DELETE FROM {table}"))
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    app = create_app(
        Settings(env="test", database_url=migrated_db.async_url),
        publisher=NullPublisher(),
        redis=redis,
        firebase=FirebaseVerifier(PROJECT, fetch=certs),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        yield c, engine, redis
    await engine.dispose()


async def _count(engine: Any, table: str, user: str) -> int:
    column = "id" if table == "users" else "user_id"
    async with engine.connect() as conn:
        result = await conn.execute(
            sa.text(f"SELECT count(*) FROM {table} WHERE {column} = :u"), {"u": user}
        )
        return int(result.scalar_one())


async def test_sign_in_settings_consent_and_delete(
    client: tuple[httpx.AsyncClient, Any, Any],
) -> None:
    http, engine, redis = client
    auth = {"Authorization": f"Bearer {id_token('fb-uid-1')}"}
    user = user_id_for_uid("fb-uid-1")

    first = await http.get("/v1/settings", headers=auth)
    assert first.status_code == 200
    body = first.json()
    assert body["userId"] == user
    assert body["mlConsent"] is False  # opt-in only
    assert body["experimentOptIn"] is False
    assert body["roastLevel"] == "MEDIUM"

    changed = await http.put(
        "/v1/settings",
        json={
            "roastLevel": "SAVAGE",
            "quietStart": "23:00:00",
            "quietEnd": "07:30:00",
            "mlConsent": True,
            "experimentOptIn": True,
            "budgets": [{"category": "FOOD_DELIVERY", "monthlyLimitPaise": 300000}],
        },
        headers=auth,
    )
    assert changed.status_code == 200
    data = changed.json()
    assert (data["roastLevel"], data["quietStart"], data["mlConsent"]) == (
        "SAVAGE",
        "23:00:00",
        True,
    )
    assert data["budgets"] == [{"category": "FOOD_DELIVERY", "monthlyLimitPaise": 300000}]
    bad = await http.put("/v1/settings", json={"roastLevel": "NUCLEAR"}, headers=auth)
    assert bad.status_code == 422

    # Some data to delete.
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO transactions (id, user_id, amount_paise, direction, kind, status, "
                "channel, category, is_essential, txn_time, parse_method) VALUES ('t_w9', :u, "
                "100, 'DEBIT', 'SPEND', 'SUCCESS', 'UPI', 'OTHER', false, now(), 'RULE')"
            ),
            {"u": user},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO alerts_sent (id, user_id, alert_type, text, status) "
                "VALUES ('a_w9', :u, 'NUDGE', 'hi', 'SENT')"
            ),
            {"u": user},
        )
        await conn.execute(
            sa.text("INSERT INTO alert_feedback (alert_id, reaction) VALUES ('a_w9', 'FUNNY')")
        )
    code = await create_link_code(redis, user)

    assert (await http.delete("/v1/me", headers=auth)).status_code == 204
    for table in ("users", "transactions", "alerts_sent", "budgets"):
        assert await _count(engine, table, user) == 0, table
    assert await redis.get(f"tglink:{code}") is None

    bad_token = {"Authorization": "Bearer " + id_token("x")[:-4] + "abcd"}
    assert (await http.get("/v1/settings", headers=bad_token)).status_code == 401
