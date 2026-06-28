"""Tests for KazmaTracer — tracing backend and all trace methods."""

from __future__ import annotations

import logging
from unittest.mock import patch

from kazma_core.tracing import KazmaTracer, TracingBackend, create_tracer


class TestTracingBackend:
    """Test the TracingBackend enum."""

    def test_all_backends(self):
        assert TracingBackend.LANGFUSE.value == "langfuse"
        assert TracingBackend.OPENTELEMETRY.value == "opentelemetry"
        assert TracingBackend.CONSOLE.value == "console"


class TestKazmaTracerInit:
    """Test tracer initialization."""

    def test_console_backend_default(self):
        tracer = KazmaTracer(backend="console")
        assert tracer.backend == TracingBackend.CONSOLE
        assert tracer._client is None

    def test_explicit_backend(self):
        tracer = KazmaTracer(backend="console")
        assert tracer.backend == TracingBackend.CONSOLE

    def test_env_var_backend(self, monkeypatch):
        monkeypatch.setenv("KAZMA_TRACING_BACKEND", "console")
        tracer = KazmaTracer()
        assert tracer.backend == TracingBackend.CONSOLE

    @patch("kazma_core.tracing.KazmaTracer._init_langfuse")
    def test_langfuse_backend(self, mock_init):
        tracer = KazmaTracer(backend="langfuse")
        assert tracer.backend == TracingBackend.LANGFUSE
        mock_init.assert_called_once()

    @patch("kazma_core.tracing.KazmaTracer._init_opentelemetry")
    def test_opentelemetry_backend(self, mock_init):
        tracer = KazmaTracer(backend="opentelemetry")
        assert tracer.backend == TracingBackend.OPENTELEMETRY
        mock_init.assert_called_once()


class TestTraceLLMCall:
    """Test LLM call tracing."""

    def test_console_trace(self, caplog):
        tracer = KazmaTracer(backend="console")
        with caplog.at_level(logging.INFO):
            tracer.trace_llm_call(
                model="gpt-4",
                prompt="What is 2+2?",
                response="4",
                tokens=100,
                cost=0.003,
                duration_ms=500,
            )
        assert "LLM call: model=gpt-4" in caplog.text
        assert "tokens=100" in caplog.text
        assert "$0.0030" in caplog.text

    def test_truncation(self, caplog):
        tracer = KazmaTracer(backend="console")
        long_prompt = "x" * 1000
        long_response = "y" * 1000
        with caplog.at_level(logging.INFO):
            tracer.trace_llm_call(
                model="gpt-4",
                prompt=long_prompt,
                response=long_response,
                tokens=500,
                cost=0.01,
            )
        # Should not crash on long inputs
        assert "LLM call" in caplog.text


class TestTraceToolExecution:
    """Test tool execution tracing."""

    def test_console_trace_success(self, caplog):
        tracer = KazmaTracer(backend="console")
        with caplog.at_level(logging.INFO):
            tracer.trace_tool_execution(
                tool_name="web_search",
                input_data={"query": "kazma ai"},
                output_data={"results": 5},
                duration_ms=250,
                success=True,
            )
        assert "Tool web_search [OK]" in caplog.text

    def test_console_trace_failure(self, caplog):
        tracer = KazmaTracer(backend="console")
        with caplog.at_level(logging.INFO):
            tracer.trace_tool_execution(
                tool_name="file_read",
                input_data={"path": "/nonexistent"},
                output_data={"error": "not found"},
                duration_ms=10,
                success=False,
            )
        assert "Tool file_read [FAIL]" in caplog.text


class TestTraceStateTransition:
    """Test state transition tracing."""

    def test_console_trace(self, caplog):
        tracer = KazmaTracer(backend="console")
        with caplog.at_level(logging.INFO):
            tracer.trace_state_transition(
                from_state="idle",
                to_state="thinking",
                checkpoint_id="cp-abc-123",
            )
        assert "idle → thinking" in caplog.text
        assert "cp-abc-123" in caplog.text


class TestTraceCompaction:
    """Test compaction tracing."""

    def test_console_trace(self, caplog):
        tracer = KazmaTracer(backend="console")
        with caplog.at_level(logging.INFO):
            tracer.trace_compaction(
                tokens_before=10000,
                tokens_after=2000,
                summary="Compacted tool results and old messages",
            )
        assert "10000 → 2000 tokens" in caplog.text
        assert "80% reduction" in caplog.text

    def test_zero_tokens_before(self, caplog):
        tracer = KazmaTracer(backend="console")
        with caplog.at_level(logging.INFO):
            tracer.trace_compaction(
                tokens_before=0,
                tokens_after=0,
                summary="Nothing to compact",
            )
        assert "0 → 0 tokens" in caplog.text


class TestTracerLifecycle:
    """Test flush and shutdown."""

    def test_flush_console(self):
        tracer = KazmaTracer(backend="console")
        tracer.flush()  # Should not raise

    def test_shutdown_console(self):
        tracer = KazmaTracer(backend="console")
        tracer.shutdown()  # Should not raise


class TestCreateTracer:
    """Test the factory function."""

    def test_create_default(self):
        tracer = create_tracer()
        assert isinstance(tracer, KazmaTracer)

    def test_create_console(self):
        tracer = create_tracer(backend="console")
        assert tracer.backend == TracingBackend.CONSOLE
