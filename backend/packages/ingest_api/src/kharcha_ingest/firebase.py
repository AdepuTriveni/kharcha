"""Firebase ID token verification (PROJECT_SPEC §9, §28.2).

Same checks as ``firebase_admin.auth.verify_id_token``: RS256 signed by one of Google's
securetoken certificates, ``aud`` = project id, ``iss`` = securetoken issuer, not expired,
``auth_time`` in the past, non-empty ``sub`` (the Firebase uid). Implemented with PyJWT so
it is small and testable with a local key.
"""

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import httpx
import jwt
from cryptography.x509 import load_pem_x509_certificate

CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
)
CertFetcher = Callable[[], Awaitable[tuple[Mapping[str, str], float]]]  # (kid -> PEM, max-age s)


class InvalidTokenError(ValueError):
    pass


def _max_age(cache_control: str | None) -> float:
    for part in (cache_control or "").split(","):
        name, _, value = part.strip().partition("=")
        if name == "max-age" and value.isdigit():
            return float(value)
    return 3600.0


async def fetch_google_certs() -> tuple[Mapping[str, str], float]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(CERTS_URL)
        response.raise_for_status()
        return response.json(), _max_age(response.headers.get("cache-control"))


class FirebaseVerifier:
    def __init__(
        self,
        project_id: str,
        fetch: CertFetcher = fetch_google_certs,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.project_id = project_id
        self._fetch = fetch
        self._clock = clock
        self._certs: Mapping[str, str] = {}
        self._expires = 0.0

    async def _cert(self, kid: str) -> str:
        if kid not in self._certs or self._clock() >= self._expires:
            certs, max_age = await self._fetch()
            self._certs, self._expires = dict(certs), self._clock() + max_age
        cert = self._certs.get(kid)
        if cert is None:
            raise InvalidTokenError("unknown key id")
        return cert

    async def verify(self, token: str) -> str:
        """Return the Firebase uid, or raise :class:`InvalidTokenError`."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise InvalidTokenError("malformed token") from exc
        if header.get("alg") != "RS256" or not header.get("kid"):
            raise InvalidTokenError("wrong algorithm")
        pem = await self._cert(str(header["kid"]))
        key = load_pem_x509_certificate(pem.encode()).public_key()
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key,  # type: ignore[arg-type]
                algorithms=["RS256"],
                audience=self.project_id,
                issuer=f"https://securetoken.google.com/{self.project_id}",
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                leeway=5,
            )
        except jwt.PyJWTError as exc:
            raise InvalidTokenError(type(exc).__name__) from exc
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub or len(sub) > 128:
            raise InvalidTokenError("bad subject")
        auth_time = claims.get("auth_time")
        if isinstance(auth_time, int | float) and auth_time > self._clock() + 5:
            raise InvalidTokenError("auth_time in the future")
        return sub
