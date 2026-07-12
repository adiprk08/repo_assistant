"""Symmetric encryption for secrets at rest (docs/adr/0020).

GitHub installation tokens are cached in Postgres encrypted with Fernet
(AES-128-CBC + HMAC, authenticated), so a database dump alone never leaks a usable
token. The key comes from ``token_encryption_key`` (generate one with
``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``).
Plaintext tokens live only in memory; nothing here logs values.
"""

from cryptography.fernet import Fernet

from repo_assistant.core.errors import ValidationError


class TokenCipher:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("utf-8") if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise ValidationError(
                "token_encryption_key is not a valid Fernet key (32 url-safe base64 bytes)."
            ) from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")


def cipher_from_settings(key: str | None) -> TokenCipher:
    if not key:
        raise ValidationError(
            "Private repositories require token_encryption_key to be set (Fernet key)."
        )
    return TokenCipher(key)
