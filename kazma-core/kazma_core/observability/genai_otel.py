"""OpenTelemetry GenAI spans — Kazma in somebody else's observability stack.

The GenAI semantic conventions are how agent traces are expressed
industry-wide. Emitting them means an enterprise already running OTel sees
Kazma's LLM calls and tool executions in the dashboards they have, with no
integration work and no Kazma-specific collector.

Three properties this has to keep, in priority order:

**It is optional.** ``opentelemetry`` is not a Kazma dependency. Without it
every function here is a no-op context manager. The existing stdlib tracer in
``swarm/tracing.py`` remains the dependency-free path and is untouched.

**It never breaks a turn.** Telemetry that can fail a request is worse than no
telemetry. Every entry point swallows its own errors; a broken exporter costs
you a span, not an answer.

**It records no content.** No prompts, no completions, no tool arguments, no
tool results. The conventions make message capture opt-in precisely because it
is a data-exfiltration surface, and a product whose pitch is "your agent, your
box" should not stream conversations to a collector by default. What ships is
metadata: model, token counts, finish reasons, latency, tool names, errors.
``gen_ai.input.messages`` / ``gen_ai.output.messages`` are deliberately absent
and there is no flag to turn them on — adding one is a decision with a privacy
argument attached, not a config default.

Attribute names come from ``opentelemetry.semconv`` when that package is
present, and fall back to the literal strings otherwise, so a collector sees
the right keys either way.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "genai_available",
    "setup_genai_tracing",
    "genai_chat_span",
    "genai_tool_span",
    "record_chat_response",
]

#: Instrumentation scope name. One per library, per the OTel spec.
_SCOPE = "kazma.genai"

# Literal fallbacks. The semconv package moves these between releases and the
# incubating module is not guaranteed to exist; the wire format is stable, so
# the strings are the contract and the constants are a convenience.
_A = {
    "system": "gen_ai.system",
    "operation": "gen_ai.operation.name",
    "req_model": "gen_ai.request.model",
    "req_max_tokens": "gen_ai.request.max_tokens",
    "req_temperature": "gen_ai.request.temperature",
    "res_model": "gen_ai.response.model",
    "res_id": "gen_ai.response.id",
    "res_finish": "gen_ai.response.finish_reasons",
    "in_tokens": "gen_ai.usage.input_tokens",
    "out_tokens": "gen_ai.usage.output_tokens",
    "tool_name": "gen_ai.tool.name",
    "tool_call_id": "gen_ai.tool.call.id",
    "tool_type": "gen_ai.tool.type",
    "error_type": "error.type",
}

try:  # pragma: no cover - exercised by whichever branch the host has
    from opentelemetry.semconv._incubating.attributes import (  # type: ignore
        gen_ai_attributes as _g,
    )

    _A.update({
        "system": _g.GEN_AI_SYSTEM,
        "operation": _g.GEN_AI_OPERATION_NAME,
        "req_model": _g.GEN_AI_REQUEST_MODEL,
        "req_max_tokens": _g.GEN_AI_REQUEST_MAX_TOKENS,
        "req_temperature": _g.GEN_AI_REQUEST_TEMPERATURE,
        "res_model": _g.GEN_AI_RESPONSE_MODEL,
        "res_id": _g.GEN_AI_RESPONSE_ID,
        "res_finish": _g.GEN_AI_RESPONSE_FINISH_REASONS,
        "in_tokens": _g.GEN_AI_USAGE_INPUT_TOKENS,
        "out_tokens": _g.GEN_AI_USAGE_OUTPUT_TOKENS,
        "tool_name": _g.GEN_AI_TOOL_NAME,
        "tool_call_id": _g.GEN_AI_TOOL_CALL_ID,
        "tool_type": _g.GEN_AI_TOOL_TYPE,
    })
except Exception:  # noqa: BLE001 - semconv is optional; literals already set
    pass


class _NoopSpan:
    """Stand-in when OpenTelemetry is absent. Absorbs everything."""

    def set_attribute(self, key: str, value: Any) -> None:  # noqa: D401
        return None

    def set_attributes(self, attrs: dict[str, Any]) -> None:
        return None

    def set_status(self, *args: Any, **kwargs: Any) -> None:
        return None

    def record_exception(self, *args: Any, **kwargs: Any) -> None:
        return None

    def is_recording(self) -> bool:
        return False


def genai_available() -> bool:
    """True when OpenTelemetry is importable and spans will be real."""
    try:
        from opentelemetry import trace  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


# ── making the spans actually leave the process ─────────────────────────────
#
# `trace.get_tracer()` returns a NonRecordingSpan until somebody installs a
# TracerProvider. Installing the SDK and setting OTEL_EXPORTER_OTLP_ENDPOINT is
# NOT enough on its own -- verified, not assumed: with both in place the global
# provider is still `ProxyTracerProvider` and `is_recording()` is False. The
# usual answer is to launch under `opentelemetry-instrument`, which means yet
# another package and a changed start command.
#
# So Kazma wires it up itself, under two rules that keep it from being rude:
# it does nothing unless an endpoint is explicitly configured, and it never
# replaces a provider somebody else installed.

_setup_done = False


def _provider_already_installed() -> bool:
    """True when a host application has set its own TracerProvider.

    The API's placeholder is `ProxyTracerProvider`; anything else means the
    embedding application configured tracing and Kazma must not touch it.
    """
    try:
        from opentelemetry import trace  # type: ignore

        return type(trace.get_tracer_provider()).__name__ != "ProxyTracerProvider"
    except Exception:
        return False


def setup_genai_tracing() -> bool:
    """Install an OTLP exporter if the operator asked for one. Idempotent.

    Returns True when this call installed a provider. Driven entirely by the
    standard `OTEL_*` environment variables so it behaves the way an operator
    who knows OpenTelemetry already expects; there is no Kazma-specific dialect
    to learn.
    """
    global _setup_done
    if _setup_done:
        return False
    _setup_done = True  # set first: a failure must not retry on every span

    import os

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
    )
    if not endpoint:
        return False
    if _provider_already_installed():
        logger.debug("[genai_otel] a TracerProvider is already installed; leaving it")
        return False

    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore
    except Exception:
        logger.debug("[genai_otel] opentelemetry-sdk not installed", exc_info=True)
        return False

    exporter = None
    # Protocol selection follows the spec's own env var. gRPC is the default
    # because that is what the OTLP spec defaults to.
    protocol = (
        os.environ.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")
        or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "grpc"
    ).lower()
    try:
        if protocol.startswith("http"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
                OTLPSpanExporter,
            )
        else:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore
                OTLPSpanExporter,
            )
        exporter = OTLPSpanExporter()
    except Exception:
        logger.warning(
            "[genai_otel] OTEL_EXPORTER_OTLP_ENDPOINT is set but no OTLP exporter "
            "could be built; install opentelemetry-exporter-otlp. Spans will not "
            "be exported.",
            exc_info=True,
        )
        return False

    try:
        resource = Resource.create({
            "service.name": os.environ.get("OTEL_SERVICE_NAME") or "kazma",
        })
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    except Exception:
        logger.debug("[genai_otel] provider install failed", exc_info=True)
        return False

    logger.info("[genai_otel] exporting GenAI spans to %s via %s", endpoint, protocol)
    return True


def _ensure_setup() -> None:
    """Called before the first span. Cheap after the first time, never raises."""
    if _setup_done:
        return
    try:
        setup_genai_tracing()
    except Exception:
        logger.debug("[genai_otel] tracing setup failed", exc_info=True)


def _clean(attrs: dict[str, Any]) -> dict[str, Any]:
    """Drop Nones and coerce to types the OTLP wire accepts."""
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (str, bool, int, float)):
            out[k] = v
        elif isinstance(v, (list, tuple)) and all(
            isinstance(x, (str, bool, int, float)) for x in v
        ):
            out[k] = list(v)
        else:
            out[k] = str(v)
    return out


@contextmanager
def genai_chat_span(
    *,
    system: str,
    model: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    operation: str = "chat",
) -> Iterator[Any]:
    """A CLIENT span for one logical LLM call, retries included.

    Span name follows the convention ``{operation} {model}`` — "chat
    deepseek-flash" — which is what makes these group correctly in a UI that
    has never heard of Kazma.

    The span covers the whole call including provider-side retries, because a
    caller waiting 40 seconds across three attempts waited 40 seconds; a span
    per attempt would report three fast calls and hide the latency that was
    actually experienced.
    """
    try:
        from opentelemetry import trace  # type: ignore
    except Exception:
        yield _NoopSpan()
        return

    _ensure_setup()
    attrs = _clean({
        _A["system"]: system,
        _A["operation"]: operation,
        _A["req_model"]: model,
        _A["req_max_tokens"]: max_tokens,
        _A["req_temperature"]: temperature,
    })
    try:
        tracer = trace.get_tracer(_SCOPE)
        ctx = tracer.start_as_current_span(
            f"{operation} {model}".strip(),
            kind=trace.SpanKind.CLIENT,
            attributes=attrs,
        )
    except Exception:
        logger.debug("[genai_otel] chat span setup failed", exc_info=True)
        yield _NoopSpan()
        return

    started = time.monotonic()
    # NO broad try/except around this `with`. An earlier draft had one, meaning
    # to catch failures in the span machinery -- and it caught the caller's
    # re-raised exception instead, silently swallowing every LLM error in the
    # system. Telemetry that hides errors is far worse than telemetry that
    # fails. Setup is already guarded above; only the annotation calls need
    # guarding, and they have their own.
    with ctx as span:
        try:
            yield span
        except Exception as exc:
            try:
                span.set_attribute(_A["error_type"], type(exc).__name__)
                span.record_exception(exc)
                from opentelemetry.trace import Status, StatusCode  # type: ignore

                span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            except Exception:
                logger.debug("[genai_otel] error annotation failed", exc_info=True)
            raise
        finally:
            try:
                span.set_attribute(
                    "kazma.llm.duration_ms",
                    round((time.monotonic() - started) * 1000, 2),
                )
            except Exception:
                pass


def record_chat_response(span: Any, response: Any) -> None:
    """Attach response metadata to a chat span. Content is never read.

    Tolerant by design: `response` is whatever the provider returned, and a
    missing field costs an attribute rather than a turn.
    """
    if span is None:
        return
    try:
        usage = getattr(response, "usage", None) or {}
        if not isinstance(usage, dict):
            usage = {}
        finish = getattr(response, "finish_reason", None)
        attrs = _clean({
            _A["res_model"]: getattr(response, "model", None),
            _A["res_id"]: getattr(response, "id", None),
            _A["res_finish"]: [finish] if finish else None,
            _A["in_tokens"]: usage.get("prompt_tokens") or usage.get("input_tokens"),
            _A["out_tokens"]: usage.get("completion_tokens") or usage.get("output_tokens"),
            # Kazma-specific, namespaced so it cannot collide with the spec.
            "kazma.llm.tool_calls": len(getattr(response, "tool_calls", None) or []),
        })
        for k, v in attrs.items():
            span.set_attribute(k, v)
    except Exception:
        logger.debug("[genai_otel] response annotation failed", exc_info=True)


@contextmanager
def genai_tool_span(
    tool_name: str, *, call_id: str | None = None, tool_type: str = "function"
) -> Iterator[Any]:
    """An INTERNAL span for one tool execution.

    Named ``execute_tool {name}`` per the conventions. Arguments and results
    are deliberately not recorded: a tool result is untrusted third-party text
    and a tool argument routinely contains a path, a query or a message body.
    """
    try:
        from opentelemetry import trace  # type: ignore
    except Exception:
        yield _NoopSpan()
        return

    _ensure_setup()
    attrs = _clean({
        _A["operation"]: "execute_tool",
        _A["tool_name"]: tool_name,
        _A["tool_call_id"]: call_id,
        _A["tool_type"]: tool_type,
    })
    try:
        tracer = trace.get_tracer(_SCOPE)
        ctx = tracer.start_as_current_span(
            f"execute_tool {tool_name}",
            kind=trace.SpanKind.INTERNAL,
            attributes=attrs,
        )
    except Exception:
        logger.debug("[genai_otel] tool span setup failed", exc_info=True)
        yield _NoopSpan()
        return

    # Same reasoning as the chat span: no outer swallow, or a failing tool
    # would report success to the caller.
    with ctx as span:
        try:
            yield span
        except Exception as exc:
            try:
                span.set_attribute(_A["error_type"], type(exc).__name__)
                span.record_exception(exc)
                from opentelemetry.trace import Status, StatusCode  # type: ignore

                span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            except Exception:
                logger.debug("[genai_otel] tool error annotation failed", exc_info=True)
            raise
