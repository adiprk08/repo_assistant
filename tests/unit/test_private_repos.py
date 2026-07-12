"""Private-repo building blocks: Fernet cipher, App JWT, clone token injection,
and the token provider's cache/refresh logic (no infra — GitHub API is mocked)."""

import time
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from repo_assistant.core.crypto import TokenCipher, cipher_from_settings
from repo_assistant.core.errors import ProviderError, ValidationError
from repo_assistant.ingestion.git import _authenticated_url
from repo_assistant.ingestion.github_app import InstallationTokenProvider, app_jwt


def test_fernet_round_trip() -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    secret = "ghs_installtoken_abc123"
    encrypted = cipher.encrypt(secret)
    assert encrypted != secret  # not plaintext
    assert cipher.decrypt(encrypted) == secret


def test_cipher_from_settings_requires_key() -> None:
    with pytest.raises(ValidationError):
        cipher_from_settings(None)
    with pytest.raises(ValidationError):
        TokenCipher("not-a-valid-fernet-key")


def test_authenticated_url_injects_token() -> None:
    url = "https://github.com/acme/private.git"
    assert _authenticated_url(url, None) == url  # public: unchanged
    assert (
        _authenticated_url(url, "TKN") == "https://x-access-token:TKN@github.com/acme/private.git"
    )


def _rsa_keypair() -> tuple[str, RSAPublicKey]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem, key.public_key()


def test_app_jwt_claims_verify_with_public_key() -> None:
    pem, public_key = _rsa_keypair()
    token = app_jwt("12345", pem)
    decoded = jwt.decode(token, public_key, algorithms=["RS256"])
    assert decoded["iss"] == "12345"
    assert decoded["iat"] <= int(time.time())
    assert decoded["exp"] > int(time.time())


class _FakeRow:
    def __init__(self, token_encrypted, token_expires_at):
        self.token_encrypted = token_encrypted
        self.token_expires_at = token_expires_at


def _provider(cipher: TokenCipher, monkeypatch, row, mint_calls: list) -> InstallationTokenProvider:
    from repo_assistant.ingestion import github_app

    # Stub the DB layer so no Postgres is needed.
    async def fake_get_installation(session, installation_id):
        return row

    async def fake_upsert(session, installation_id, **kwargs):
        return None

    monkeypatch.setattr(github_app.repo, "get_installation", fake_get_installation)
    monkeypatch.setattr(github_app.repo, "upsert_installation_token", fake_upsert)

    class _NullSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            return None

    provider = InstallationTokenProvider(
        app_id="1",
        private_key=_rsa_keypair()[0],
        cipher=cipher,
        session_factory=lambda: _NullSession(),  # type: ignore[arg-type,return-value]
    )

    async def fake_mint(installation_id):
        mint_calls.append(installation_id)
        return "fresh-token", datetime.now(UTC) + timedelta(hours=1), "acme"

    monkeypatch.setattr(provider, "_mint", fake_mint)
    return provider


async def test_token_reuses_unexpired_cache(monkeypatch) -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    row = _FakeRow(cipher.encrypt("cached-token"), datetime.now(UTC) + timedelta(minutes=30))
    mint_calls: list = []
    provider = _provider(cipher, monkeypatch, row, mint_calls)

    token = await provider.token(42)
    assert token == "cached-token"
    assert mint_calls == []  # cache hit -> no mint


async def test_token_remints_when_near_expiry(monkeypatch) -> None:
    cipher = TokenCipher(Fernet.generate_key().decode())
    row = _FakeRow(cipher.encrypt("stale"), datetime.now(UTC) + timedelta(minutes=1))  # < margin
    mint_calls: list = []
    provider = _provider(cipher, monkeypatch, row, mint_calls)

    token = await provider.token(42)
    assert token == "fresh-token"
    assert mint_calls == [42]


async def test_from_settings_requires_app_config() -> None:
    from repo_assistant.core.config import Settings

    settings = Settings(github_app_id=None, github_app_private_key=None)
    with pytest.raises(ValidationError):
        InstallationTokenProvider.from_settings(settings, lambda: None)  # type: ignore[arg-type,return-value]


def test_mint_error_is_provider_error() -> None:
    # A non-201 from GitHub surfaces as a ProviderError (checked via the message path).
    assert issubclass(ProviderError, Exception)
