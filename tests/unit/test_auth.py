"""API-key generation/hashing and the rate-limiter classes (no infra)."""

import pytest

from repo_assistant.api.ratelimit import InMemoryRateLimiter, NoopRateLimiter
from repo_assistant.api.security import generate_api_key, hash_key
from repo_assistant.core.errors import RateLimitError


def test_generated_key_shape_and_hash() -> None:
    g = generate_api_key()
    assert g.plaintext.startswith("ra_")
    assert g.prefix == g.plaintext[:9]  # "ra_" + 6
    assert g.key_hash == hash_key(g.plaintext)
    assert len(g.key_hash) == 64  # sha256 hex
    assert g.plaintext not in g.key_hash  # plaintext is not recoverable from the hash


def test_keys_are_unique() -> None:
    assert generate_api_key().plaintext != generate_api_key().plaintext


def test_hash_is_stable_and_distinct() -> None:
    assert hash_key("ra_abc") == hash_key("ra_abc")
    assert hash_key("ra_abc") != hash_key("ra_abd")


async def test_noop_limiter_never_raises() -> None:
    limiter = NoopRateLimiter()
    for _ in range(1000):
        await limiter.check("someone")


async def test_in_memory_limiter_enforces_budget() -> None:
    limiter = InMemoryRateLimiter(limit=3, window_seconds=60)
    for _ in range(3):
        await limiter.check("key-1")  # first 3 allowed
    with pytest.raises(RateLimitError) as exc:
        await limiter.check("key-1")  # 4th over budget
    assert exc.value.retry_after >= 1


async def test_in_memory_limiter_is_per_identity() -> None:
    limiter = InMemoryRateLimiter(limit=1, window_seconds=60)
    await limiter.check("key-a")
    await limiter.check("key-b")  # different identity, own budget
    with pytest.raises(RateLimitError):
        await limiter.check("key-a")
