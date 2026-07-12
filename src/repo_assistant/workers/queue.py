"""Client side of the job queue: enqueue ingestion jobs onto arq/Redis.

The API service holds one of these; the pool is created lazily so the API can
boot (and serve search/chat) even when Redis is down — only enqueueing fails.
"""

import uuid

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from repo_assistant.core.errors import ProviderError

INGESTION_TASK = "run_ingestion"


class IngestionQueue:
    def __init__(self, redis_dsn: str) -> None:
        self._redis_settings = RedisSettings.from_dsn(redis_dsn)
        self._pool: ArqRedis | None = None

    async def enqueue(self, job_id: uuid.UUID) -> None:
        """Enqueue the arq task for ``job_id`` (the Postgres jobs row is the source of truth)."""
        try:
            if self._pool is None:
                self._pool = await create_pool(self._redis_settings)
            await self._pool.enqueue_job(INGESTION_TASK, str(job_id))
        except OSError as exc:
            raise ProviderError(f"Job queue (Redis) unavailable: {exc}") from exc

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
