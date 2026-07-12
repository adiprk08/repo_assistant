"""GitHub App authentication for private repositories (docs/adr/0020).

Signs a short-lived RS256 JWT with the app's private key, exchanges it for an
installation access token, and caches that token (Fernet-encrypted) in Postgres
until it nears expiry. Nothing here logs token values.
"""

import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from repo_assistant.core.config import Settings
from repo_assistant.core.crypto import TokenCipher, cipher_from_settings
from repo_assistant.core.errors import ProviderError, ValidationError
from repo_assistant.core.logging import get_logger
from repo_assistant.storage import repositories as repo

logger = get_logger(__name__)

_GITHUB_API = "https://api.github.com"
# Re-mint when the cached token is within this margin of expiry.
_REFRESH_MARGIN = timedelta(minutes=5)


def _resolve_private_key(value: str) -> str:
    """Accept either PEM contents or a path to a .pem file."""
    if "BEGIN" in value:
        return value
    try:
        with open(value, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        raise ValidationError(
            "github_app_private_key is neither PEM contents nor a readable path."
        ) from exc


def app_jwt(app_id: str, private_key: str) -> str:
    """A short-lived (≈9 min) RS256 JWT identifying the App to GitHub."""
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 540, "iss": str(app_id)}  # -60s for clock skew
    return jwt.encode(payload, _resolve_private_key(private_key), algorithm="RS256")


def _parse_expiry(value: str) -> datetime:
    # GitHub returns e.g. "2026-07-12T20:00:00Z".
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class InstallationTokenProvider:
    """Mints and caches installation access tokens for a configured GitHub App."""

    def __init__(
        self,
        *,
        app_id: str,
        private_key: str,
        cipher: TokenCipher,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._app_id = app_id
        self._private_key = private_key
        self._cipher = cipher
        self._session_factory = session_factory

    @classmethod
    def from_settings(
        cls, settings: Settings, session_factory: async_sessionmaker[AsyncSession]
    ) -> "InstallationTokenProvider":
        if not settings.github_app_id or not settings.github_app_private_key:
            raise ValidationError(
                "Private repositories require github_app_id and github_app_private_key."
            )
        return cls(
            app_id=settings.github_app_id,
            private_key=settings.github_app_private_key,
            cipher=cipher_from_settings(settings.token_encryption_key),
            session_factory=session_factory,
        )

    async def token(self, installation_id: int) -> str:
        """Return a valid installation token, reusing the cached one until it nears expiry."""
        async with self._session_factory() as session:
            row = await repo.get_installation(session, installation_id)
            if row and row.token_encrypted and row.token_expires_at:
                expires = row.token_expires_at
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=UTC)
                if expires - _REFRESH_MARGIN > datetime.now(UTC):
                    return self._cipher.decrypt(row.token_encrypted)

        token, expires_at, account = await self._mint(installation_id)
        async with self._session_factory() as session:
            await repo.upsert_installation_token(
                session,
                installation_id,
                account_login=account,
                token_encrypted=self._cipher.encrypt(token),
                token_expires_at=expires_at.replace(tzinfo=None),
            )
            await session.commit()
        logger.info("minted installation token", installation_id=installation_id)
        return token

    async def _mint(self, installation_id: int) -> tuple[str, datetime, str | None]:
        headers = {
            "Authorization": f"Bearer {app_jwt(self._app_id, self._private_key)}",
            "Accept": "application/vnd.github+json",
        }
        url = f"{_GITHUB_API}/app/installations/{installation_id}/access_tokens"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"GitHub token exchange failed: {exc}") from exc
        if response.status_code != 201:
            raise ProviderError(
                f"GitHub token exchange returned {response.status_code} for installation "
                f"{installation_id}"
            )
        data = response.json()
        account = (data.get("account") or {}).get("login")
        return data["token"], _parse_expiry(data["expires_at"]), account
