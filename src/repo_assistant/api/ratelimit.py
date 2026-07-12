"""Per-key rate limiting (docs/adr/0016).

A fixed-window counter in Redis: cheap (one INCR + first-hit EXPIRE), shared across
API replicas, and good enough for abuse protection. It **fails open** — if Redis is
unreachable the request is allowed, because a rate limiter should never be the thing
that takes the whole API down. Swap in a sliding-window/token-bucket later if bursts
at window edges matter.
"""

import time
from abc import ABC, abstractmethod

from redis.asyncio import Redis
from redis.exceptions import RedisError

from repo_assistant.core.errors import RateLimitError
from repo_assistant.core.logging import get_logger

logger = get_logger(__name__)


class RateLimiter(ABC):
    @abstractmethod
    async def check(self, identity: str) -> None:
        """Raise RateLimitError if ``identity`` is over budget; otherwise return."""

    async def aclose(self) -> None:
        return None


class NoopRateLimiter(RateLimiter):
    """Disables limiting (dev, or when rate_limit_enabled is false)."""

    async def check(self, identity: str) -> None:
        return None


class RedisRateLimiter(RateLimiter):
    def __init__(self, redis_dsn: str, *, limit: int, window_seconds: int) -> None:
        self._redis: Redis = Redis.from_url(redis_dsn)
        self._limit = limit
        self._window = window_seconds

    async def check(self, identity: str) -> None:
        now = int(time.time())
        window_start = now - (now % self._window)
        key = f"ratelimit:{identity}:{window_start}"
        try:
            count = await self._redis.incr(key)
            if count == 1:
                await self._redis.expire(key, self._window)
        except (RedisError, OSError) as exc:  # fail open — never block on limiter failure
            logger.warning("rate limiter unavailable; allowing request", error=str(exc))
            return
        if count > self._limit:
            raise RateLimitError(
                f"rate limit exceeded: {self._limit} requests per {self._window}s",
                retry_after=max(self._window - (now % self._window), 1),
            )

    async def aclose(self) -> None:
        await self._redis.aclose()


class InMemoryRateLimiter(RateLimiter):
    """Process-local fixed window — for tests only (not shared across replicas)."""

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._counts: dict[tuple[str, int], int] = {}

    async def check(self, identity: str) -> None:
        now = int(time.time())
        window_start = now - (now % self._window)
        bucket = (identity, window_start)
        self._counts[bucket] = self._counts.get(bucket, 0) + 1
        if self._counts[bucket] > self._limit:
            raise RateLimitError(
                f"rate limit exceeded: {self._limit} requests per {self._window}s",
                retry_after=max(self._window - (now % self._window), 1),
            )
