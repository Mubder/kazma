"""OpenTelemetry GenAI spans (audit R-3).

The point of the conventions is that somebody else's dashboard understands
Kazma without being taught about Kazma. That only works if the attribute names
are exactly right, so most of this file checks spelling against the spec.

The other half is the part that would be embarrassing to get wrong: these spans
must never carry prompts, completions, tool arguments or tool results. The
conventions make message capture opt-in precisely because it is a
data-exfiltration surface, and a product whose pitch is "your agent, your box"
should not stream conversations to a collector by default.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry")

from kazma_core.observability.genai_otel import (  # noqa: E402
    genai_chat_span,
    genai_tool_span,
    record_chat_response,
)
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)


@pytest.fixture
def spans(monkeypatch):
    # A developer with OTEL_EXPORTER_OTLP_ENDPOINT exported would otherwise get
    # a real exporter installed by _ensure_setup during these tests.
    for var in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    ):
        monkeypatch.delenv(var, raising=False)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # set_tracer_provider is one-shot per process; override the internal so
    # each test gets a clean exporter regardless of ordering.
    trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]
    yield exporter
    exporter.clear()


class _Resp:
    model = "deepseek-flash"
    id = "resp-1"
    finish_reason = "stop"
    usage = {"prompt_tokens": 100, "completion_tokens": 20}
    tool_calls = [1, 2]
    content = "THE ACTUAL MODEL REPLY, WHICH MUST NOT APPEAR"


# ── the spec's spelling ─────────────────────────────────────────────────────


def test_a_chat_span_matches_the_convention(spans):
    with genai_chat_span(
        system="deepseek", model="deepseek-flash", max_tokens=512, temperature=0.0
    ) as s:
        record_chat_response(s, _Resp())

    (span,) = spans.get_finished_spans()
    assert span.name == "chat deepseek-flash", (
        "span name is '{operation} {model}' -- this is what groups them in a UI "
        "that has never heard of Kazma"
    )
    assert span.kind is trace.SpanKind.CLIENT
    a = dict(span.attributes)
    assert a["gen_ai.system"] == "deepseek"
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.request.model"] == "deepseek-flash"
    assert a["gen_ai.request.max_tokens"] == 512
    assert a["gen_ai.response.model"] == "deepseek-flash"
    assert a["gen_ai.response.id"] == "resp-1"
    assert tuple(a["gen_ai.response.finish_reasons"]) == ("stop",)
    assert a["gen_ai.usage.input_tokens"] == 100
    assert a["gen_ai.usage.output_tokens"] == 20


def test_a_tool_span_matches_the_convention(spans):
    with genai_tool_span("shell_exec", call_id="call-7"):
        pass

    (span,) = spans.get_finished_spans()
    assert span.name == "execute_tool shell_exec"
    assert span.kind is trace.SpanKind.INTERNAL
    a = dict(span.attributes)
    assert a["gen_ai.operation.name"] == "execute_tool"
    assert a["gen_ai.tool.name"] == "shell_exec"
    assert a["gen_ai.tool.call.id"] == "call-7"


def test_kazma_specific_attributes_are_namespaced(spans):
    """Anything outside the spec must be under `kazma.` so it cannot collide
    with a future convention attribute."""
    with genai_chat_span(system="groq", model="m") as s:
        record_chat_response(s, _Resp())

    (span,) = spans.get_finished_spans()
    for key in span.attributes:
        assert key.startswith(("gen_ai.", "kazma.", "error.")), key


# ── the invariant that matters most ─────────────────────────────────────────


def test_no_span_ever_carries_content(spans):
    """Prompts, completions, arguments and results stay out.

    If this fails, Kazma is exporting conversations to whatever collector the
    host has configured -- which is a data-exfiltration bug wearing an
    observability hat, and far worse than having no telemetry.
    """
    secret = "THE ACTUAL MODEL REPLY, WHICH MUST NOT APPEAR"
    with genai_chat_span(system="deepseek", model="m") as s:
        record_chat_response(s, _Resp())
    with genai_tool_span("file_write") as s:
        pass

    for span in spans.get_finished_spans():
        blob = repr(dict(span.attributes))
        assert secret not in blob
        for banned in ("gen_ai.input.messages", "gen_ai.output.messages",
                       "gen_ai.prompt", "gen_ai.completion"):
            assert banned not in span.attributes, f"{banned} would carry content"


def test_there_is_no_flag_that_turns_content_on():
    """Adding one is a decision with a privacy argument attached, not a config
    default. If someone adds it, this test should be deleted deliberately."""
    import inspect

    from kazma_core.observability import genai_otel

    src = inspect.getsource(genai_otel)
    assert "gen_ai.input.messages" not in src.replace(
        "``gen_ai.input.messages``", ""
    ).replace("gen_ai.input.messages` / `gen_ai.output.messages`", "")


# ── failure behaviour ───────────────────────────────────────────────────────


def test_an_error_is_recorded_and_re_raised(spans):
    with pytest.raises(ValueError):
        with genai_chat_span(system="groq", model="compound-mini"):
            raise ValueError("boom")

    (span,) = spans.get_finished_spans()
    assert span.attributes["error.type"] == "ValueError"
    assert span.status.status_code is trace.StatusCode.ERROR


def test_a_tool_error_is_recorded_and_re_raised(spans):
    with pytest.raises(RuntimeError):
        with genai_tool_span("shell_exec"):
            raise RuntimeError("nope")

    (span,) = spans.get_finished_spans()
    assert span.attributes["error.type"] == "RuntimeError"


def test_a_broken_tracer_does_not_break_the_caller(monkeypatch):
    """Telemetry that can fail a request is worse than no telemetry."""
    import kazma_core.observability.genai_otel as mod

    def _boom(*a, **kw):
        raise RuntimeError("tracer exploded")

    monkeypatch.setattr(trace, "get_tracer", _boom)
    ran = False
    with mod.genai_chat_span(system="x", model="y") as span:
        ran = True
        span.set_attribute("anything", 1)  # the no-op must absorb this
    assert ran, "the body must still execute when tracing is broken"


def test_record_chat_response_tolerates_a_junk_response(spans):
    """Providers return what they return; a missing field costs an attribute,
    not a turn."""
    with genai_chat_span(system="x", model="y") as s:
        record_chat_response(s, object())
        record_chat_response(s, None)
        record_chat_response(None, _Resp())


# ── provider mapping ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://api.deepseek.com/v1", "deepseek"),
        ("https://api.groq.com/openai/v1", "groq"),
        ("https://api.anthropic.com", "anthropic"),
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("http://localhost:11434/v1", "ollama"),
        ("https://api.openai.com/v1", "openai"),
        ("https://gateway.example.internal/v1", "openai"),
        ("", "openai"),
        (None, "openai"),
    ],
)
def test_gen_ai_system_is_the_provider_not_the_hostname(url, expected):
    """`gen_ai.system` identifies the provider. An unknown gateway falls back
    to `openai` because that is the wire protocol it speaks, which is what the
    attribute actually describes."""
    from kazma_core.llm_provider import _otel_system_for

    assert _otel_system_for(url) == expected


# ── it is wired into the chokepoints ────────────────────────────────────────


def test_chat_is_wrapped_not_just_instrumentable():
    import inspect

    from kazma_core.llm_provider import LLMProvider

    src = inspect.getsource(LLMProvider.chat)
    assert "genai_chat_span" in src
    assert "_chat_inner" in src, "the span must wrap the whole call, retries included"


def test_tool_execute_is_wrapped():
    import inspect

    from kazma_core.agent.tool_registry import LocalToolRegistry

    src = inspect.getsource(LocalToolRegistry.execute)
    assert "genai_tool_span" in src
    assert "_execute_inner" in src


# -- the no-OpenTelemetry path ----------------------------------------------


@pytest.fixture
def no_otel(monkeypatch):
    """Make `import opentelemetry` fail, as it does on a default install.

    `opentelemetry` is not a Kazma dependency, so this branch is the one most
    users actually run -- and it is the branch no other test in this file
    touches, because the whole module is behind an importorskip.
    """
    import builtins

    real_import = builtins.__import__

    def _fail(name, *args, **kwargs):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            raise ImportError("no opentelemetry here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fail)


def test_without_opentelemetry_the_body_still_runs(no_otel):
    import kazma_core.observability.genai_otel as mod

    ran = []
    with mod.genai_chat_span(system="deepseek", model="m") as s:
        s.set_attribute("k", 1)  # the no-op absorbs it
        mod.record_chat_response(s, _Resp())
        ran.append("chat")
    with mod.genai_tool_span("shell_exec") as s:
        s.set_attribute("k", 1)
        ran.append("tool")
    assert ran == ["chat", "tool"]


@pytest.mark.parametrize("exc", [ValueError, RuntimeError, KeyboardInterrupt])
def test_without_opentelemetry_errors_still_reach_the_caller(no_otel, exc):
    """The counterpart to the two tests above, for the branch most installs
    take. An earlier draft of this module wrapped the span in a broad
    `except Exception` that caught the caller's own re-raised error -- every
    LLM and tool failure would have been reported to the caller as success.
    The tests caught it; these pin the other branch so it cannot come back.

    `KeyboardInterrupt` is in here on purpose: it is not an `Exception`, and a
    Ctrl-C during a long call must not be converted into a silent success.
    """
    import kazma_core.observability.genai_otel as mod

    with pytest.raises(exc):
        with mod.genai_chat_span(system="x", model="y"):
            raise exc("boom")

    with pytest.raises(exc):
        with mod.genai_tool_span("shell_exec"):
            raise exc("boom")


@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit])
def test_a_base_exception_is_not_swallowed_with_otel_present(spans, exc):
    """Same invariant on the instrumented path. `BaseException` is the case a
    bare `except Exception:` silently gets right and a bare `except:` gets
    catastrophically wrong."""
    with pytest.raises(exc):
        with genai_chat_span(system="x", model="y"):
            raise exc()
    with pytest.raises(exc):
        with genai_tool_span("t"):
            raise exc()


# -- the wiring that makes spans actually leave the process ------------------
#
# Installing opentelemetry-sdk and setting OTEL_EXPORTER_OTLP_ENDPOINT is NOT
# enough on its own: the global provider stays `ProxyTracerProvider` and every
# span is a NonRecordingSpan. The docs said otherwise until this was checked,
# which would have sent operators to an empty dashboard.


@pytest.fixture
def fresh_setup(monkeypatch):
    """Reset the once-flag and hand back the module."""
    import kazma_core.observability.genai_otel as mod

    monkeypatch.setattr(mod, "_setup_done", False)
    for var in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_SERVICE_NAME",
    ):
        monkeypatch.delenv(var, raising=False)
    return mod


def test_no_endpoint_means_no_provider_is_installed(fresh_setup):
    """Kazma must not start exporting because a package happens to be present.
    Opting in is setting the endpoint."""
    assert fresh_setup.setup_genai_tracing() is False


def test_a_host_provider_is_never_replaced(fresh_setup, monkeypatch):
    """Kazma is a library here. An application that configured its own tracing
    owns the global provider, and stomping it would silently redirect that
    application's telemetry."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    provider = TracerProvider()
    trace._TRACER_PROVIDER = provider  # type: ignore[attr-defined]

    assert fresh_setup.setup_genai_tracing() is False
    assert trace.get_tracer_provider() is provider


def test_setup_runs_once_even_if_it_fails(fresh_setup, monkeypatch):
    """A failing exporter must not be retried on every single span."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    calls = []
    monkeypatch.setattr(
        fresh_setup, "_provider_already_installed", lambda: (calls.append(1), True)[1]
    )
    fresh_setup.setup_genai_tracing()
    fresh_setup.setup_genai_tracing()
    fresh_setup.setup_genai_tracing()
    assert len(calls) == 1


def test_spans_reach_a_real_collector(fresh_setup, monkeypatch):
    """End to end, against a real HTTP listener rather than a mock.

    A mock cannot answer the question this asks -- whether an operator who
    follows the documentation gets bytes on the wire. This is the test that
    would have caught the empty-dashboard bug.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    received: list[bytes] = []

    class _H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length", 0))
            received.append(self.rfile.read(n))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):  # silence the default stderr logging
            pass

    srv = HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # This test installs a REAL exporter globally. Left behind, it would point
    # every later test in the session at a dead port -- the neighbour-breaking
    # fixture leak, one more time.
    previous = getattr(trace, "_TRACER_PROVIDER", None)
    installed = None
    try:
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", f"http://127.0.0.1:{srv.server_address[1]}"
        )
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
        trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
        assert fresh_setup.setup_genai_tracing() is True, "no provider was installed"

        with fresh_setup.genai_chat_span(
            system="deepseek", model="deepseek-flash", max_tokens=512
        ) as s:
            fresh_setup.record_chat_response(s, _Resp())
        with fresh_setup.genai_tool_span("shell_exec", call_id="call-7"):
            pass

        installed = trace.get_tracer_provider()
        installed.force_flush(5000)
    finally:
        try:
            if installed is not None:
                installed.shutdown()
        finally:
            trace._TRACER_PROVIDER = previous  # type: ignore[attr-defined]
            srv.shutdown()
            srv.server_close()

    blob = b"".join(received)
    assert blob, "nothing reached the collector"
    assert b"deepseek-flash" in blob
    assert b"gen_ai.usage.input_tokens" in blob
    assert b"execute_tool shell_exec" in blob
    assert _Resp.content.encode() not in blob, (
        "the model's reply went to the collector -- this is the exfiltration "
        "bug the whole no-content rule exists to prevent"
    )


# -- the page must stay true -------------------------------------------------


@pytest.fixture(scope="module")
def otel_doc() -> str:
    from pathlib import Path

    doc = Path(__file__).resolve().parent.parent / "docs" / "docs" / "ops" / "opentelemetry.md"
    assert doc.exists(), "the OpenTelemetry page is gone"
    return doc.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "var",
    [
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_SERVICE_NAME",
    ],
)
def test_every_documented_variable_is_one_the_code_reads(var, otel_doc):
    """A page naming a variable the code ignores is worse than no page: the
    operator sets it, sees nothing, and concludes the feature is broken."""
    import inspect

    from kazma_core.observability import genai_otel

    assert var in otel_doc, f"the page stopped documenting {var}"
    assert var in inspect.getsource(genai_otel), (
        f"the page tells operators to set {var} and nothing reads it"
    )


@pytest.mark.parametrize(
    "attr",
    [
        "gen_ai.system",
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.tool.name",
        "kazma.llm.duration_ms",
    ],
)
def test_every_attribute_the_page_advertises_is_emitted(attr, otel_doc):
    import inspect

    from kazma_core.observability import genai_otel

    assert attr in otel_doc
    assert attr in inspect.getsource(genai_otel) or attr in genai_otel._A.values()


def test_the_page_is_in_the_sidebar():
    """Docusaurus builds an unregistered page as an orphan: it exists at a URL
    and nothing links to it, so nobody finds it."""
    from pathlib import Path

    sidebars = Path(__file__).resolve().parent.parent / "docs" / "sidebars.js"
    assert "ops/opentelemetry" in sidebars.read_text(encoding="utf-8")


def test_the_page_states_the_no_content_rule(otel_doc):
    """This is the claim an enterprise reviewer cares about most."""
    low = otel_doc.lower()
    assert "no prompts" in low
    assert "no flag that turns them on" in low
