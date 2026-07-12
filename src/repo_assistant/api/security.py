"""API-key generation and hashing (docs/adr/0016).

Keys are 256-bit random tokens, so a fast SHA-256 is the right hash — key-stretching
(bcrypt/argon2) exists to slow brute force of *low-entropy* passwords and buys
nothing against a value with this much entropy, while costing latency on every
request. Only the hash is stored; the plaintext is shown once at creation.
"""

import hashlib
import secrets
from dataclasses import dataclass

_KEY_PREFIX = "ra_"
_PREFIX_DISPLAY_LEN = len(_KEY_PREFIX) + 6  # "ra_" + 6 chars, enough to disambiguate


@dataclass(frozen=True, slots=True)
class GeneratedKey:
    plaintext: str  # shown to the user exactly once
    prefix: str  # non-secret display label
    key_hash: str  # what we persist


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key() -> GeneratedKey:
    """Mint a new key: ``ra_<43 url-safe chars>`` (32 random bytes)."""
    token = _KEY_PREFIX + secrets.token_urlsafe(32)
    return GeneratedKey(
        plaintext=token, prefix=token[:_PREFIX_DISPLAY_LEN], key_hash=hash_key(token)
    )
