"""OpenTelemetry tracing (docs/adr/0019), OTLP-native and config-gated.

When ``otel_enabled`` is false nothing is installed and ``span()`` resolves to the
OTel API's built-in no-op tracer — so pipeline code can wrap stages in ``span()``
unconditionally at ~zero cost. When enabled, spans batch-export over OTLP/HTTP to
whatever backend the deployment points at (Jaeger, Tempo, Langfuse, …).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

if TYPE_CHECKING:
    from repo_assistant.core.config import Settings

_tracer = trace.get_tracer("repo_assistant")


def configure_tracing(settings: "Settings") -> None:
    """Install an OTLP/HTTP exporting tracer provider if tracing is enabled."""
    if not settings.otel_enabled:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    endpoint = settings.otel_exporter_endpoint.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)


def instrument_app(app: Any, settings: "Settings") -> None:
    """Auto-instrument FastAPI (request spans) and httpx (outbound LLM/embed calls)."""
    if not settings.otel_enabled:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start a current span. A no-op (cheap) when no tracer provider is installed."""
    with _tracer.start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        yield current
