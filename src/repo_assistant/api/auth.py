"""Bearer API-key authentication + per-key rate limiting (docs/adr/0016).

``secured`` is the single dependency the protected routers depend on: it validates
the ``Authorization: Bearer <key>`` credential against the hashed keys in Postgres,
then charges the caller's rate-limit budget. Both are gated by settings, so a dev
instance can run open. ``/health`` is intentionally left unauthenticated.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from repo_assistant.api.security import hash_key
from repo_assistant.core.errors import AuthenticationError
from repo_assistant.storage import repositories as repo
from repo_assistant.storage.models import ApiKey

# auto_error=False: we raise our own AuthenticationError (-> 401 + WWW-Authenticate)
# so the response shape matches the rest of the API's error envelope.
_bearer = HTTPBearer(auto_error=False, description="API key as a bearer token.")


async def authenticate(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> ApiKey | None:
    settings = request.app.state.settings
    if not settings.require_api_key:
        return None
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Missing API key. Send 'Authorization: Bearer <key>'.")

    runtime = request.app.state.runtime
    key_hash = hash_key(credentials.credentials)
    async with runtime.session_factory() as session:
        api_key = await repo.get_api_key_by_hash(session, key_hash)
        if api_key is None or api_key.revoked_at is not None:
            raise AuthenticationError("Invalid or revoked API key.")
        await repo.touch_api_key(session, api_key.id)
        await session.commit()
    return api_key


async def secured(
    request: Request, api_key: Annotated[ApiKey | None, Depends(authenticate)]
) -> ApiKey | None:
    """Authenticate, then charge the rate-limit budget for the authenticated key."""
    identity = str(api_key.id) if api_key is not None else "anonymous"
    await request.app.state.rate_limiter.check(identity)
    return api_key
