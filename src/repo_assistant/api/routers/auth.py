"""GitHub OAuth login, logout, and the current-user endpoint (docs/adr/0023).

The browser hits these through the same-origin proxy (``/api/auth/*`` on the web
origin), so the session cookie set here is first-party. Login uses the OAuth
``state`` double-submit (a random value mirrored in a short-lived cookie and the
GitHub redirect) to defeat login CSRF; the resulting server-side session is an
opaque token whose SHA-256 is all that's stored.
"""

from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from repo_assistant.api.auth import (
    STATE_COOKIE,
    CurrentUser,
    clear_session_cookie,
    new_session_token,
    session_expiry,
    set_session_cookie,
)
from repo_assistant.api.schemas import UserOut
from repo_assistant.api.security import hash_key
from repo_assistant.core.logging import get_logger
from repo_assistant.storage import repositories as repo

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)

_GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
_GITHUB_USER = "https://api.github.com/user"


def _github_client() -> httpx.AsyncClient:
    """The HTTP client used for the OAuth token/user exchange. A named seam so
    tests can substitute a fake without patching httpx globally."""
    return httpx.AsyncClient(timeout=10.0)


def _callback_url(web_base_url: str) -> str:
    return f"{web_base_url}/api/auth/github/callback"


@router.get("/github/login")
async def github_login(request: Request) -> RedirectResponse:
    settings = request.app.state.settings
    if not settings.github_oauth_client_id or not settings.github_oauth_client_secret:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured.")

    _, state = new_session_token()  # opaque, single-use; mirrored in a cookie
    params = {
        "client_id": settings.github_oauth_client_id,
        "redirect_uri": _callback_url(settings.web_base_url),
        "scope": "read:user",
        "state": state,
        "allow_signup": "false",
    }
    response = RedirectResponse(f"{_GITHUB_AUTHORIZE}?{urlencode(params)}")
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return response


@router.get("/github/callback")
async def github_callback(
    request: Request, code: str | None = None, state: str | None = None
) -> RedirectResponse:
    settings = request.app.state.settings
    runtime = request.app.state.runtime

    cookie_state = request.cookies.get(STATE_COOKIE)
    if not code or not state or not cookie_state or state != cookie_state:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")

    async with _github_client() as client:
        try:
            token_resp = await client.post(
                _GITHUB_TOKEN,
                headers={"Accept": "application/json"},
                data={
                    "client_id": settings.github_oauth_client_id,
                    "client_secret": settings.github_oauth_client_secret,
                    "code": code,
                    "redirect_uri": _callback_url(settings.web_base_url),
                },
            )
            access_token = token_resp.json().get("access_token")
            if not access_token:
                raise HTTPException(
                    status_code=502, detail="GitHub did not return an access token."
                )
            user_resp = await client.get(
                _GITHUB_USER,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            user_resp.raise_for_status()
            gh = user_resp.json()
        except httpx.HTTPError as exc:
            logger.warning("github oauth exchange failed", error=str(exc))
            raise HTTPException(status_code=502, detail="GitHub sign-in failed.") from exc

    token, token_hash = new_session_token()
    async with runtime.session_factory() as session:
        user = await repo.upsert_github_user(
            session,
            github_id=int(gh["id"]),
            login=gh["login"],
            name=gh.get("name"),
            avatar_url=gh.get("avatar_url"),
        )
        await repo.create_web_session(
            session, user_id=user.id, token_hash=token_hash, expires_at=session_expiry(settings)
        )
        await session.commit()
    logger.info("user signed in", login=gh["login"])

    response = RedirectResponse(settings.web_base_url)
    set_session_cookie(response, token, settings)
    response.delete_cookie(STATE_COOKIE, path="/")
    return response


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    runtime = request.app.state.runtime
    token = request.cookies.get("ra_session")
    if token:
        async with runtime.session_factory() as session:
            await repo.delete_web_session(session, hash_key(token))
            await session.commit()
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    """The signed-in user (or 401 if unauthenticated)."""
    return UserOut.model_validate(user)
