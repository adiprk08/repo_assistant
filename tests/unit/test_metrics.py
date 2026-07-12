"""Prometheus metric helpers and the no-op tracing span (no infra)."""

import pytest
from prometheus_client import REGISTRY

from repo_assistant.core import metrics
from repo_assistant.core.interfaces import Usage
from repo_assistant.core.tracing import span


def _val(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


@pytest.fixture
def enabled():
    prev = metrics._enabled
    metrics.enable_metrics()
    try:
        yield
    finally:
        metrics._enabled = prev


def test_observe_cache_increments(enabled) -> None:
    before = _val("ra_embedding_cache_total", {"result": "hit"})
    metrics.observe_cache(hits=3, misses=1)
    assert _val("ra_embedding_cache_total", {"result": "hit"}) == before + 3


def test_observe_llm_splits_token_kinds(enabled) -> None:
    usage = Usage(input_tokens=10, output_tokens=5, cache_read_tokens=2, cache_write_tokens=0)
    before_in = _val("ra_llm_tokens_total", {"model": "m1", "kind": "input"})
    before_read = _val("ra_llm_tokens_total", {"model": "m1", "kind": "cache_read"})
    metrics.observe_llm("m1", usage, 0.42)
    assert _val("ra_llm_tokens_total", {"model": "m1", "kind": "input"}) == before_in + 10
    assert _val("ra_llm_tokens_total", {"model": "m1", "kind": "cache_read"}) == before_read + 2
    # cache_write was 0 -> not emitted (no zero-value series churn)


def test_observe_citation_drops(enabled) -> None:
    before = _val("ra_citation_drops_total")
    metrics.observe_citation_drops(4)
    assert _val("ra_citation_drops_total") == before + 4


def test_helpers_are_noop_when_disabled() -> None:
    prev = metrics._enabled
    metrics._enabled = False
    try:
        before = _val("ra_citation_drops_total")
        metrics.observe_citation_drops(99)
        metrics.observe_cache(hits=99, misses=99)
        metrics.observe_retrieval("hybrid", 1.0)
        assert _val("ra_citation_drops_total") == before  # unchanged
    finally:
        metrics._enabled = prev


def test_timed_stage_records_duration(enabled) -> None:
    before = _val("ra_ingestion_stage_seconds_count", {"stage": "scanning"})
    with metrics.timed_stage("scanning"):
        pass
    assert _val("ra_ingestion_stage_seconds_count", {"stage": "scanning"}) == before + 1


def test_span_is_noop_without_provider() -> None:
    # No tracer provider installed -> span is a cheap no-op that still supports the API.
    with span("unit.test", attr="value") as current:
        current.set_attribute("k", "v")


def test_render_latest_returns_exposition() -> None:
    body, content_type = metrics.render_latest()
    assert isinstance(body, bytes)
    assert "text/plain" in content_type
