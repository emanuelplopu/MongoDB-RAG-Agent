"""Server-side login enforcement for tenants that must never serve guests.

Most routers resolve the caller with ``get_current_user`` and tolerate an
anonymous request (search and chat, for example, work without a token so the
RecallHub tenant can offer guest access). A Quellex install that is reachable
over the office network must not behave like that: every request outside a
small public allowlist has to carry a valid session (JWT) or API key.

``RequireAuthMiddleware`` implements that rule once, in front of every router,
so no endpoint can be forgotten. It is enabled through the ``REQUIRE_AUTH``
setting (``backend/core/config.py``) and advertised to the frontend through
``GET /api/v1/tenant/config`` so the UI applies the same rule.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Iterable, Optional

from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Type of the callable that turns a request into a user (or None).
UserResolver = Callable[[Request], Awaitable[Optional[object]]]

#: Paths that must work without a session: health probes for Docker and the
#: tray app, tenant branding for the login screen, and the login/register
#: endpoints themselves (registration is still subject to REGISTRATION_MODE).
#: The API docs (/docs, /redoc, /openapi.json) are deliberately not listed:
#: with REQUIRE_AUTH on, the API surface is not shown to anonymous callers.
DEFAULT_PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/",
        "/health",
        "/api/v1/system/health",
        "/api/v1/tenant/config",
        "/api/v1/auth/login",
        "/api/v1/auth/register",
    }
)

#: Path prefixes that stay public. Empty by default; kept as a hook for
#: deployments that must expose e.g. a status page.
DEFAULT_PUBLIC_PREFIXES: tuple[str, ...] = ()

#: Server-Sent-Events endpoints the browser opens with ``EventSource``, which
#: cannot send an Authorization header. The frontend appends ``?token=<jwt>``
#: there (see IngestionManagementPage), so the token is accepted from the query
#: string for these suffixes only.
DEFAULT_QUERY_TOKEN_SUFFIXES: tuple[str, ...] = ("/stream",)

QUERY_TOKEN_PARAM = "token"


def extract_bearer_credentials(
    request: Request,
    query_token_suffixes: Iterable[str] = DEFAULT_QUERY_TOKEN_SUFFIXES,
) -> Optional[HTTPAuthorizationCredentials]:
    """Build the credentials object ``get_current_user`` expects.

    Args:
        request: Incoming request.
        query_token_suffixes: Path suffixes for which a ``token`` query
            parameter is accepted as a bearer token (SSE endpoints).

    Returns:
        Credentials for a ``Bearer`` Authorization header or SSE query token,
        ``None`` when the request carries neither.
    """
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token:
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    path = request.url.path
    if any(path.endswith(suffix) for suffix in query_token_suffixes):
        query_token = request.query_params.get(QUERY_TOKEN_PARAM, "").strip()
        if query_token:
            return HTTPAuthorizationCredentials(scheme="Bearer", credentials=query_token)
    return None


class RequireAuthMiddleware(BaseHTTPMiddleware):
    """Reject every non-public request that has no valid user.

    The middleware does not know how users are stored; ``resolve_user`` is the
    application's own resolver (``get_current_user`` from the auth router) so
    JWT validation, API-key lookup and the ``is_active`` check stay in one place.
    """

    def __init__(
        self,
        app,
        resolve_user: UserResolver,
        enabled: bool = True,
        public_paths: Iterable[str] = DEFAULT_PUBLIC_PATHS,
        public_prefixes: Iterable[str] = DEFAULT_PUBLIC_PREFIXES,
    ) -> None:
        super().__init__(app)
        self.enabled = enabled
        self.resolve_user = resolve_user
        self.public_paths = frozenset(public_paths)
        self.public_prefixes = tuple(public_prefixes)

    def is_public(self, path: str) -> bool:
        """Return True when ``path`` is served without a session."""
        normalized = path.rstrip("/") or "/"
        if normalized in self.public_paths:
            return True
        return any(
            normalized == prefix or normalized.startswith(prefix + "/")
            for prefix in self.public_prefixes
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self.enabled or request.method == "OPTIONS" or self.is_public(request.url.path):
            return await call_next(request)

        try:
            user = await self.resolve_user(request)
        except Exception as exc:  # resolver hit the database or decoder
            logger.error("Authentication check failed for %s %s: %s", request.method, request.url.path, exc)
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Authentication service unavailable",
                    "error": "authentication_unavailable",
                },
            )

        if user is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated", "error": "authentication_required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        request.state.user = user
        return await call_next(request)
