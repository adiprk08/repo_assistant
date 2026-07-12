"""arq worker configuration. Run with ``ra worker`` (or ``arq repo_assistant.workers.settings.WorkerSettings``)."""

from typing import Any

from arq.connections import RedisSettings
from arq.worker import func

from repo_assistant.cli.runtime import build_runtime
from repo_assistant.core.config import get_settings
from repo_assistant.core.logging import configure_logging
from repo_assistant.workers.ingestion import run_ingestion


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(settings)
    ctx["runtime"] = build_runtime(settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["runtime"].aclose()


class WorkerSettings:
    # max_tries=1: a failed ingestion is marked failed and left for an explicit
    # re-POST rather than auto-retried (provider misconfigurations would otherwise
    # burn embedding/LLM spend x5). Checkpointed auto-retry is ADR-0014 follow-up.
    functions = [func(run_ingestion, name="run_ingestion", max_tries=1, timeout=3600)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_dsn)
