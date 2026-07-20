"""Authentication + per-user rate limiting (docs/adr/0016, docs/adr/0023).

Identity resolves from **either** a browser session cookie (set by the GitHub
OAuth flow) **or** an ``Authorization: Bearer <key>`` API key — both map to a
``User``. ``current_user`` is what routers inject to scope data to the caller;
``secured`` is the router-level guard that authenticates and then charges the
caller's rate-limit budget. ``/health`` stays unauthenticated.

With ``require_auth`` off (dev), everything runs as the singleton ``local`` user,
so the app is fully usable without logging in and data still has a real owner.
"""

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from repo_assistant.api.security import hash_key
from repo_assistant.core.config import Settings
from repo_assistant.core.errors import AuthenticationError
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.models import User

SESSION_COOKIE = "ra_session"
STATE_COOKIE = "ra_oauth_state"


def new_session_token() -> tuple[str, str]:
    """A fresh opaque session token and its SHA-256 (only the hash is stored)."""
    token = secrets.token_urlsafe(32)
    return token, hash_key(token)


def session_expiry(settings: Settings) -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) + timedelta(days=settings.session_ttl_days)


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# auto_error=False: we raise our own AuthenticationError (-> 401 + WWW-Authenticate)
# so the response shape matches the rest of the API's error envelope.
_bearer = HTTPBearer(auto_error=False, description="API key as a bearer token.")


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the calling user from a session cookie or an API key.

    Precedence: cookie first (the browser's own session), then bearer key (CLI /
    programmatic). Raises AuthenticationError when auth is required and neither
    presents a valid identity.
    """
    settings = request.app.state.settings
    runtime = request.app.state.runtime

    if not settings.require_auth:
        async with runtime.session_factory() as session:
            user = await repo.get_or_create_local_user(session)
            await session.commit()
            return user

    cookie = request.cookies.get(SESSION_COOKIE)
    async with runtime.session_factory() as session:
        if cookie:
            user = await repo.get_user_for_session_token(session, hash_key(cookie))
            if user is not None:
                return user
        if credentials is not None and credentials.credentials:
            user = await repo.user_for_api_key_hash(session, hash_key(credentials.credentials))
            if user is not None:
                await repo.touch_api_key_by_hash(session, hash_key(credentials.credentials))
                await session.commit()
                return user

    raise AuthenticationError("Sign in or send a valid 'Authorization: Bearer <key>'.")


CurrentUser = Annotated[User, Depends(current_user)]


async def secured(request: Request, user: CurrentUser) -> User:
    """Authenticate, then charge the rate-limit budget for the authenticated user."""
    await request.app.state.rate_limiter.check(str(user.id))
    return user
