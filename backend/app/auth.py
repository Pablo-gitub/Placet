"""
Authentication for the two kinds of callers:

- Agents authenticate with a Bearer token; their identity is the agent whose
  token matches, never a field of the request body.
- The human (PWA) authenticates through Tailscale identity: `tailscale serve`
  adds the `Tailscale-User-Login` header to proxied requests. That header is
  trusted ONLY on the human listener (a Unix socket that only `tailscale serve`
  should proxy to); on any other listener anyone could forge it.
"""
import hmac
import logging
import os
from dataclasses import dataclass
from typing import Iterable, Literal, Optional, Set

from fastapi import HTTPException, Request, status

from backend.app.config import PlacetConfig

logger = logging.getLogger(__name__)

TAILSCALE_LOGIN_HEADER = "tailscale-user-login"
LISTENER_SCOPE_KEY = "placet.listener"
HUMAN_LISTENER = "human"


@dataclass(frozen=True)
class Principal:
    kind: Literal["agent", "human", "bridge"]
    id: str

    @property
    def is_human(self) -> bool:
        return self.kind == "human"

    @property
    def actor(self) -> str:
        """How this principal appears in the audit log."""
        return "bridge" if self.kind == "bridge" else f"{self.kind}:{self.id}"


class HumanListener:
    """
    ASGI wrapper marking requests as arriving on the human listener, where the
    Tailscale identity header can be trusted.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope)
            scope[LISTENER_SCOPE_KEY] = HUMAN_LISTENER
        await self.app(scope, receive, send)


def parse_allowed_users(raw: Optional[str]) -> Set[str]:
    return {u.strip().lower() for u in (raw or "").split(",") if u.strip()}


def load_allowed_users() -> Set[str]:
    users = parse_allowed_users(os.environ.get("PLACET_ALLOWED_USERS"))
    if not users:
        logger.warning(
            "PLACET_ALLOWED_USERS is empty: nobody can use the PWA until it is set."
        )
    return users


class Authenticator:
    def __init__(
        self,
        config: PlacetConfig,
        allowed_users: Iterable[str],
        bridge_token: str = "",
    ):
        self.config = config
        self.allowed_users = {u.lower() for u in allowed_users}
        self.bridge_token = bridge_token
        for agent_id, spec in config.agents.items():
            if not spec.token:
                logger.warning(
                    f"Agent '{agent_id}' has no token configured: it cannot authenticate."
                )

    def _agent_for_token(self, token: str) -> Optional[str]:
        match = None
        # Compare against every token in constant time; do not stop early.
        for agent_id, spec in self.config.agents.items():
            if spec.token and hmac.compare_digest(spec.token.encode(), token.encode()):
                match = agent_id
        return match

    def identify(self, request: Request) -> Principal:
        auth_header = request.headers.get("authorization", "")
        if auth_header:
            scheme, _, token = auth_header.partition(" ")
            token = token.strip()
            is_bearer = scheme.lower() == "bearer" and bool(token)
            if is_bearer and self.bridge_token and hmac.compare_digest(
                self.bridge_token.encode(), token.encode()
            ):
                return Principal(kind="bridge", id="bridge")
            agent_id = self._agent_for_token(token) if is_bearer else None
            if agent_id is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid agent token.",
                )
            return Principal(kind="agent", id=agent_id)

        if request.scope.get(LISTENER_SCOPE_KEY) == HUMAN_LISTENER:
            login = request.headers.get(TAILSCALE_LOGIN_HEADER, "").strip().lower()
            if login and login in self.allowed_users:
                return Principal(kind="human", id=login)
            if login:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Tailscale user {login} is not allowed.",
                )

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
        )


def require_human(principal: Principal) -> None:
    if not principal.is_human:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the human supervisor can perform this action.",
        )


def require_agent(principal: Principal) -> None:
    if principal.kind != "agent":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only an authenticated agent can perform this action.",
        )


def require_bridge(principal: Principal) -> None:
    if principal.kind != "bridge":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the local bridge can perform this action.",
        )
