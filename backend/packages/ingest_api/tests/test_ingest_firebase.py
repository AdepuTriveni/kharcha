import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from kharcha_ingest.auth import _looks_like_jwt, user_id_for_uid
from kharcha_ingest.firebase import FirebaseVerifier, InvalidTokenError, _max_age

PROJECT = "kharcha-test"
NOW = time.time()  # PyJWT checks exp against the real clock


def _key_and_cert() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "securetoken")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(datetime(2026, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2027, 1, 1, tzinfo=UTC))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.PEM).decode()


KEY, CERT = _key_and_cert()
OTHER_KEY, _ = _key_and_cert()


def token(key: rsa.RSAPrivateKey = KEY, kid: str = "k1", **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": "firebase-uid-123",
        "iat": int(NOW) - 10,
        "exp": int(NOW) + 3600,
        "auth_time": int(NOW) - 60,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class Certs:
    def __init__(self) -> None:
        self.calls = 0
        self.certs: Mapping[str, str] = {"k1": CERT}

    async def __call__(self) -> tuple[Mapping[str, str], float]:
        self.calls += 1
        return self.certs, 600.0


def _verifier(certs: Certs | None = None) -> FirebaseVerifier:
    return FirebaseVerifier(PROJECT, fetch=certs or Certs(), clock=lambda: NOW)


async def test_valid_token_gives_uid_and_certs_are_cached() -> None:
    certs = Certs()
    verifier = _verifier(certs)
    assert await verifier.verify(token()) == "firebase-uid-123"
    assert await verifier.verify(token()) == "firebase-uid-123"
    assert certs.calls == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"aud": "other-project"},
        {"iss": "https://securetoken.google.com/other-project"},
        {"exp": int(NOW) - 60},
        {"sub": ""},
        {"sub": None},
        {"auth_time": int(NOW) + 600},
    ],
)
async def test_bad_claims_are_rejected(bad: dict[str, Any]) -> None:
    with pytest.raises(InvalidTokenError):
        await _verifier().verify(token(**bad))


async def test_wrong_key_unknown_kid_and_hs256_are_rejected() -> None:
    verifier = _verifier()
    with pytest.raises(InvalidTokenError):
        await verifier.verify(token(key=OTHER_KEY))
    with pytest.raises(InvalidTokenError, match="unknown key id"):
        await verifier.verify(token(kid="k9"))
    hs = jwt.encode({"sub": "x"}, "secret-secret-secret-secret-secret", algorithm="HS256")
    with pytest.raises(InvalidTokenError, match="wrong algorithm"):
        await verifier.verify(hs)
    with pytest.raises(InvalidTokenError):
        await verifier.verify("not.a.jwt")


async def test_rotated_keys_are_refetched() -> None:
    certs = Certs()
    verifier = _verifier(certs)
    await verifier.verify(token())
    certs.certs = {"k2": CERT}
    assert await verifier.verify(token(kid="k2")) == "firebase-uid-123"
    assert certs.calls == 2


def test_helpers() -> None:
    assert _max_age("public, max-age=19045, must-revalidate") == 19045
    assert _max_age(None) == 3600
    assert _looks_like_jwt(token())
    assert not _looks_like_jwt("khk_abc.def.ghi")
    assert user_id_for_uid("a") == user_id_for_uid("a") != user_id_for_uid("b")
    assert user_id_for_uid("a").startswith("u_")
    assert timedelta(seconds=1)
