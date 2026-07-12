"""Prometheus metrics (docs/adr/0019).

A single module-level registry with the domain metrics we care about, plus thin
emit helpers that no-op when metrics are disabled — so library and pipeline code
call them unconditionally without importing prometheus or touching a global on
every CLI/test run. Call ``enable_metrics()`` once at API startup.
"""

import time
from collections.abc import Iterator
from contextlib import contextmanager

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest

from repo_assistant.core.tracing import span

_enabled = False

# Buckets tuned for a RAG service: sub-100ms retrieval up to multi-second LLM calls.
_LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
_STAGE_BUCKETS = (0.1, 0.5, 1.0, 5.0, 15.0, 30.0, 60.0, 300.0)

http_requests = Counter(
    "ra_http_requests_total", "HTTP requests handled.", ["method", "route", "status"]
)
http_latency = Histogram(
    "ra_http_request_seconds",
    "HTTP request latency.",
    ["method", "route"],
    buckets=_LATENCY_BUCKETS,
)
ingestion_stage_seconds = Histogram(
    "ra_ingestion_stage_seconds", "Ingestion stage duration.", ["stage"], buckets=_STAGE_BUCKETS
)
retrieval_seconds = Histogram(
    "ra_retrieval_seconds", "Retrieval latency.", ["path"], buckets=_LATENCY_BUCKETS
)
embedding_cache = Counter(
    "ra_embedding_cache_total",
    "Embedding cache lookups.",
    ["result"],  # hit | miss
)
llm_tokens = Counter(
    "ra_llm_tokens_total",
    "LLM tokens.",
    ["model", "kind"],  # input|output|cache_read|cache_write
)
llm_call_seconds = Histogram(
    "ra_llm_call_seconds", "LLM call latency.", ["model"], buckets=_LATENCY_BUCKETS
)
citation_drops = Counter("ra_citation_drops_total", "Citations dropped by post-hoc verification.")


def enable_metrics() -> None:
    global _enabled
    _enabled = True


def is_enabled() -> bool:
    return _enabled


def observe_http(method: str, route: str, status: int, seconds: float) -> None:
    if not _enabled:
        return
    http_requests.labels(method=method, route=route, status=str(status)).inc()
    http_latency.labels(method=method, route=route).observe(seconds)


def observe_stage(stage: str, seconds: float) -> None:
    if _enabled:
        ingestion_stage_seconds.labels(stage=stage).observe(seconds)


@contextmanager
def timed_stage(stage: str) -> Iterator[None]:
    """Time an ingestion stage into the histogram and open a trace span for it."""
    with span(f"ingest.{stage}", **{"ingest.stage": stage}):
        start = time.perf_counter()
        try:
            yield
        finally:
            observe_stage(stage, time.perf_counter() - start)


def observe_retrieval(path: str, seconds: float) -> None:
    if _enabled:
        retrieval_seconds.labels(path=path).observe(seconds)


def observe_cache(hits: int, misses: int) -> None:
    if not _enabled:
        return
    if hits:
        embedding_cache.labels(result="hit").inc(hits)
    if misses:
        embedding_cache.labels(result="miss").inc(misses)


def observe_llm(model: str, usage: object, seconds: float) -> None:
    """Record token spend (by kind) and call latency for one LLM response."""
    if not _enabled:
        return
    llm_call_seconds.labels(model=model).observe(seconds)
    for kind in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
        value = getattr(usage, kind, 0) or 0
        if value:
            llm_tokens.labels(model=model, kind=kind.removesuffix("_tokens")).inc(value)


def observe_citation_drops(dropped: int) -> None:
    if _enabled and dropped:
        citation_drops.inc(dropped)


def render_latest() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics exposition endpoint."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
