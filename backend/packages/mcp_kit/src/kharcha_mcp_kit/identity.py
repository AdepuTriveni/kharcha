"""Who is calling a tool server (PROJECT_SPEC §17.3).

Each agent task gets a short-lived **service token** scoped to one user id and one agent
(HS256, audience ``kharcha-mcp``, at most 10 minutes). External MCP clients use a personal
token instead (§24), verified against the database by the external server. Either way the
user id comes from the token, never from tool arguments (rule 7).
"""

import contextvars
import time
from dataclasses import dataclass

import jwt

AUDIENCE = "kharcha-mcp"
MAX_TTL_S = 600


@dataclass(frozen=True, slots=True)
class Identity:
    user_id: str
    agent: str  # coach | cash_detective | refund_advocate | memory_keeper | analyst | external
    external: bool = False


CURRENT: contextvars.ContextVar[Identity | None] = contextvars.ContextVar(
    "mcp_identity", default=None
)


class InvalidServiceTokenError(ValueError):
    pass


class ServiceTokens:
    def __init__(self, secret: str, ttl_s: int = 300) -> None:
        if len(secret) < 32:
            raise ValueError("service token secret must be at least 32 characters")
        self._secret = secret
        self._ttl = min(ttl_s, MAX_TTL_S)

    def mint(self, user_id: str, agent: str, now: float | None = None) -> str:
        issued = int(now if now is not None else time.time())
        claims = {
            "sub": user_id,
            "agent": agent,
            "aud": AUDIENCE,
            "iat": issued,
            "exp": issued + self._ttl,
        }
        return jwt.encode(claims, self._secret, algorithm="HS256")

    def verify(self, token: str) -> Identity:
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=["HS256"],
                audience=AUDIENCE,
                options={"require": ["sub", "exp", "iat", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidServiceTokenError(type(exc).__name__) from exc
        if claims["exp"] - claims["iat"] > MAX_TTL_S:
            raise InvalidServiceTokenError("token lifetime too long")
        agent = claims.get("agent")
        if not isinstance(agent, str) or agent == "external":
            raise InvalidServiceTokenError("bad agent")
        return Identity(user_id=str(claims["sub"]), agent=agent)
