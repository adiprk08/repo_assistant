"""FastAPI dependencies.

The service holds one composition ``Runtime`` (providers, vector index, session
factory) and one ``IngestionQueue`` on ``app.state``, created at startup and
closed at shutdown. Dependencies just hand those to routers, so the routers stay
thin over the library (CLAUDE.md).
"""

from typing import Annotated

from fastapi import Depends, Request

from repo_assistant.cli.runtime import Runtime
from repo_assistant.workers.queue import IngestionQueue


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def get_queue(request: Request) -> IngestionQueue:
    return request.app.state.queue


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]
QueueDep = Annotated[IngestionQueue, Depends(get_queue)]
