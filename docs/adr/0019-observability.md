# ADR-0019: Observability — OTLP-native traces + Prometheus metrics

**Status:** Accepted (2026-07-12)

## Context

Phase 5 needs the system to be observable in production: where time and money go
across the ingest→retrieve→answer path, cache effectiveness, error rates, and
leading quality signals (citation-verification failures). ARCHITECTURE §11 named
three pillars — structlog logging (already in place), OpenTelemetry traces,
Prometheus metrics — and Langfuse for LLM-call traces / cost.

The tension: Langfuse (and Jaeger, Tempo, Honeycomb, …) are all trace backends,
and adding a *vendor SDK* per backend couples pipeline code to a product choice.

## Decision

- **OTLP-native tracing, no vendor SDK.** Instrument with the vendor-neutral
  OpenTelemetry SDK and export spans over **OTLP/HTTP** to whatever the deployment
  points at. Langfuse ingests OTLP natively, so the "Langfuse for LLM traces" goal
  is met by exporting LLM-call spans (with model, token, and cost attributes) over
  OTLP — Jaeger/Tempo/Honeycomb work the same way. Zero code change to switch
  backends; no `langfuse` dependency.

- **Tracing is config-gated and no-op by default.** `otel_enabled=false` installs
  no exporter and the `span()` helper is a cheap no-op, so the app runs with zero
  tracing overhead and no backend required. When enabled, a `TracerProvider` with
  a batch OTLP/HTTP exporter is installed, and FastAPI + httpx are auto-instrumented
  (request spans + outbound-call spans, including the Anthropic/Voyage HTTP calls).
  Manual spans wrap the pipeline stages (clone→…→answer) and the LLM calls.

- **Prometheus metrics on `/metrics`.** A process-wide registry with:
  - HTTP: request count by method/route/status + latency histogram.
  - Ingestion: per-stage duration histogram.
  - Retrieval: latency histogram.
  - Embedding cache: hit/miss counters (the RISKS #1 cost defense, now measured).
  - LLM: token spend counter by model × kind (input/output/cache-read/cache-write)
    and call-latency histogram — this is the "cost per request/repo" telemetry.
  - Quality: citation-verification drop counter.
  `/metrics` is unauthenticated (standard for a scrape endpoint on an internal
  port) and returns the text exposition format. Metric emission goes through thin
  helpers that no-op when `metrics_enabled=false`, so library code stays clean and
  test/CLI runs don't touch the global registry unless asked.

- **Where instrumentation lives.** Metrics/tracing helpers live in `core/`
  (`core/metrics.py`, `core/tracing.py`) next to logging; pipeline code calls the
  helpers, keeping the thin-shell rule. The Anthropic adapter is the one place that
  knows model + token usage, so LLM metrics/spans are emitted there.

## Alternatives considered

- **Langfuse SDK for LLM traces.** Richer product-specific UI, but couples code to
  Langfuse and duplicates the tracing path. OTLP export to Langfuse gives ~the same
  data with no lock-in; adopt the SDK later only if a Langfuse-only feature is
  needed.
- **`prometheus-fastapi-instrumentator` / OTel auto-metrics.** Convenient, but a
  hand-rolled middleware + registry is a few lines, avoids a dependency, and lets us
  name domain metrics (cache, tokens, citation drops) precisely.
- **Metrics-only (skip tracing).** Metrics answer "how much / how often"; traces
  answer "where in this one slow request." Both matter for a RAG pipeline with many
  stages; tracing being no-op-by-default keeps the cost of including it ~zero.
- **OTLP/gRPC exporter.** Fewer bytes, but pulls in grpc; OTLP/HTTP avoids that and
  is universally accepted by collectors.

## Consequences

- The system is observable against any OTLP backend + any Prometheus scraper, with
  no vendor lock-in and no overhead when disabled.
- LLM cost/latency and cache hit rates are first-class metrics, closing the loop on
  the RISKS #1 cost defense and the Phase-2 cost ceilings.
- New dependencies: `prometheus-client`, `opentelemetry-sdk`, the OTLP/HTTP
  exporter, and FastAPI/httpx instrumentation. `langfuse` is intentionally **not** a
  dependency.
- Deferred: router-disagreement quality metric (needs a reference decision to
  compare against), Grafana dashboards / alert rules (deployment artifacts), and
  metrics for the arq worker process (this pass instruments the API + library).
