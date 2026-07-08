# Kazma — Post-Audit Remediation Plan

**Status**: Phase 0-5 Complete (Critical fixes, tests, quality improvements)
**Next**: Phase 6+ — CI stabilization, integration, observability, tech debt

---

## 📋 Phase Overview

| Phase | Focus | Timeline | Status |
|-------|-------|----------|--------|
| **0-5** | Critical fixes, tests, quality | **DONE** | ✅ Complete |
| **6** | CI Stabilization | 1-2 days | 🔄 Ready to start |
| **7** | Integration Testing | 3-5 days | 📅 Planned |
| **8** | Observability (OpenTelemetry) | 2-3 days | 📅 Planned |
| **9** | Tech Debt & Hardening | 2-3 weeks | 📅 Planned |

---

## 🔴 Phase 6: CI Stabilization (1-2 Days)

### 6.1 Fix Gateway Test Failures
**Files**: `kazma-gateway/tests/test_gateway_ux.py`
**Issue**: 2 failing tests in `TestSlashCommands`:
- `test_reset_command_clears_state` — `assert None is not None`
- `test_model_command` — `assert None is not None`

**Action**:
```bash
# Debug first
cd G:/GitHubRepos/kazma && python -m pytest kazma-gateway/tests/test_gateway_ux.py::TestSlashCommands -v -s
```

**Likely fixes**:
- Update mock return values in test fixtures
- Check `KazmaAgent` slash command handler return values
- Ensure `reset_command` and `model_command` return proper response dicts

### 6.2 Add UI Test Infrastructure
**New Files**:
```
kazma-ui/tests/conftest.py          # Shared fixtures
kazma-ui/tests/unit/__init__.py
kazma-ui/tests/integration/__init__.py
```

**conftest.py content**:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.config = MagicMock()
    agent.config.raw = {}
    agent.llm = AsyncMock()
    agent.tools = AsyncMock()
    agent.tools.get_tool_definitions = MagicMock(return_value=[])
    agent.tools.execute = AsyncMock(return_value={"content": "ok"})
    agent.cost_breaker = MagicMock()
    agent.cost_breaker.should_halt = MagicMock(return_value=False)
    agent.system_prompt = "Test prompt"
    return agent

@pytest.fixture
def mock_config_store():
    from unittest.mock import MagicMock
    store = MagicMock()
    store.get = MagicMock(return_value=None)
    store.set = MagicMock()
    return store
```

### 6.3 CI Pipeline Verification
**GitHub Actions**: `.github/workflows/ci.yml`
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e .[test]
      - run: python -m pytest kazma-core/tests kazma-gateway/tests -v
      - run: python scripts/check_docs_sync.py
```

---

## 🟡 Phase 7: Integration Testing (3-5 Days)

### 7.1 Multi-Platform Flow Tests
**New File**: `kazma-gateway/tests/integration/test_multi_platform.py`

```python
"""End-to-end tests across Telegram, Discord, Slack adapters."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

class TestMultiPlatformDispatch:
    """Test swarm dispatch works identically across platforms."""
    
    @pytest.fixture
    def mock_gateway_manager(self):
        manager = MagicMock()
        manager.send = AsyncMock()
        return manager
    
    @pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
    async def test_swarm_dispatch_routes_correctly(self, platform, mock_gateway_manager):
        from kazma_gateway.agent_handler.swarm_dispatch import _dispatch_swarm_from_chat
        from kazma_gateway.gateway import IncomingMessage
        
        msg = IncomingMessage(
            platform=platform,
            sender_id=f"{platform}:12345",
            text="swarm test task",
            context_metadata={"chat_id": "-100123", "username": "testuser"}
        )
        
        with patch("kazma_core.swarm.get_swarm_engine") as mock_engine:
            mock_engine.return_value.dispatch = AsyncMock(
                return_value=MagicMock(aggregated_output="Done")
            )
            await _dispatch_swarm_from_chat(mock_gateway_manager, mock_engine.return_value, msg, "thread-1")
            
            mock_gateway_manager.send.assert_called_once()
            call_args = mock_gateway_manager.send.call_args[0][0]
            assert call_args.target_id.startswith(f"gw-{platform}-")
```

### 7.2 HITL End-to-End Tests
**New File**: `kazma-core/tests/integration/test_hitl_e2e.py`

```python
"""Full HITL flow tests: interrupt → approve → resume."""

import pytest
from unittest.mock import AsyncMock, MagicMock

class TestHITLE2E:
    async def test_graph_interrupt_approve_resume(self):
        """Graph path: tool call → interrupt → approve → resume."""
        from kazma_core.agent.graph_builder import build_supervisor_graph
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        
        async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
            graph = build_supervisor_graph(
                llm=MagicMock(),
                system_prompt="Test",
                tool_definitions=[{"name": "file_write", ...}],
                tool_executor=AsyncMock(),
                hitl_config={"enabled": True, "require_approval_for": ["file_write"]},
                checkpointer=checkpointer,
            )
            
            config = {"configurable": {"thread_id": "test-1"}}
            # 1. Invoke with danger tool → should interrupt
            # 2. Resume with approval → should execute
            # 3. Verify tool was called
```

### 7.3 Swarm Pattern Integration Tests
**New File**: `kazma-core/tests/integration/test_swarm_patterns.py`

```python
"""Test all 5 swarm patterns with real workers."""

class TestSwarmPatterns:
    @pytest.mark.parametrize("pattern", ["dispatch", "pipeline", "consult", "fan_out", "broadcast"])
    async def test_pattern_execution(self, pattern):
        from kazma_core.swarm.engine import SwarmEngine, SwarmConfig, WorkerConfig
        from kazma_core.swarm.task import SwarmTask, TaskType
        
        engine = SwarmEngine(config=SwarmConfig(enabled=True, workers=[
            WorkerConfig(name="worker1", type="in_process", role="coder", model="gpt-4o-mini"),
        ]))
        
        task = SwarmTask(
            type=TaskType(pattern.upper()),
            payload="Test task",
            workers=["worker1"] if pattern != "broadcast" else [],
        )
        
        result = await engine.dispatch(task)
        assert result is not None
        assert len(result.worker_results) > 0
```

---

## 🟢 Phase 8: Observability — OpenTelemetry (2-3 Days)

### 8.1 Tracing Module
**New File**: `kazma-core/kazma_core/tracing.py`

```python
"""OpenTelemetry tracing setup for Kazma."""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
import os

def setup_tracing(service_name: str = "kazma") -> trace.Tracer:
    """Initialize OpenTelemetry tracing."""
    
    # Only enable if endpoint configured
    otlp_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not otlp_endpoint:
        return trace.get_tracer(service_name)  # No-op tracer
    
    provider = TracerProvider()
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    
    # Auto-instrument
    FastAPIInstrumentor.instrument()
    HTTPXClientInstrumentor.instrument()
    
    return trace.get_tracer(service_name)

# Convenience
def get_tracer(name: str = "kazma") -> trace.Tracer:
    return trace.get_tracer(name)
```

### 8.2 Instrument Critical Paths

**Swarm Engine** (`engine.py`):
```python
from kazma_core.tracing import get_tracer
tracer = get_tracer("kazma.swarm")

async def dispatch(self, task: SwarmTask) -> SwarmTaskResult:
    with tracer.start_as_current_span(f"swarm.dispatch.{task.pattern.value}") as span:
        span.set_attribute("task.id", task.id)
        span.set_attribute("task.pattern", task.pattern.value)
        span.set_attribute("workers.count", len(task.workers))
        # ... existing code
```

**Gateway** (`gateway.py`):
```python
from kazma_core.tracing import get_tracer
tracer = get_tracer("kazma.gateway")

async def _consume_loop(self):
    while not self._shutdown.is_set():
        with tracer.start_as_current_span("gateway.consume") as span:
            span.set_attribute("queue.depth", self.queue.qsize())
            # ... existing code
```

### 8.3 Metrics Export
**New File**: `kazma-ui/kazma_ui/metrics.py` (already exists — verify Prometheus)

```python
# Add to health.py
from prometheus_client import Counter, Histogram, Gauge

SWARM_DISPATCH_TOTAL = Counter("kazma_swarm_dispatch_total", "Total dispatches", ["pattern", "status"])
SWARM_DISPATCH_DURATION = Histogram("kazma_swarm_dispatch_duration_seconds", "Dispatch latency")
GATEWAY_QUEUE_DEPTH = Gauge("kazma_gateway_queue_depth", "Message queue depth")
```

---

## 🔵 Phase 9: Tech Debt & Hardening (2-3 Weeks)

### 9.1 Circuit Breaker Unit Tests
**New File**: `kazma-core/tests/unit/test_circuit_breaker.py`

```python
import pytest
from kazma_core.swarm.reliability import CircuitBreaker, CircuitState, CircuitBreakerOpenError

class TestCircuitBreaker:
    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.1)
        for _ in range(3):
            with pytest.raises(Exception):
                await cb.call(lambda: (_ for _ in ()).throw(Exception("fail")))
        assert cb.state == CircuitState.OPEN
    
    def test_half_open_probe_single(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.1)
        await cb.call(lambda: (_ for _ in ()).throw(Exception("fail")))
        await cb.call(lambda: (_ for _ in ()).throw(Exception("fail")))
        assert cb.state == CircuitState.OPEN
        
        # Wait for recovery
        import asyncio
        await asyncio.sleep(0.15)
        
        # First call enters half-open
        await cb.call(lambda: "success")
        assert cb.state == CircuitState.CLOSED
    
    def test_probe_in_flight_flag(self):
        """AGENTS.md §5: _probe_in_flight prevents concurrent probes."""
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.1)
        await cb.call(lambda: (_ for _ in ()).throw(Exception("fail")))
        
        import asyncio
        await asyncio.sleep(0.15)
        
        # Concurrent calls in half-open should be rejected
        async def probe():
            try:
                await cb.call(lambda: "success")
            except CircuitBreakerOpenError:
                return "rejected"
            return "ok"
        
        results = await asyncio.gather(probe(), probe(), probe())
        # Only one should succeed, others rejected
        assert results.count("ok") == 1
        assert results.count("rejected") == 2
```

### 9.2 Adapter Extraction
**Current**: `swarm_dispatch.py` has `_maybe_send_to_output_target()` with Telegram logic inline.

**Target Structure**:
```
kazma-gateway/kazma_gateway/adapters/
├── __init__.py
├── output/
│   ├── __init__.py
│   ├── base.py          # OutputAdapter protocol
│   ├── telegram.py      # TelegramOutputAdapter
│   ├── discord.py       # DiscordOutputAdapter
│   └── slack.py         # SlackOutputAdapter
└── output_factory.py    # get_output_adapter(config)
```

**Protocol**:
```python
# base.py
from typing import Protocol, Any

class OutputAdapter(Protocol):
    async def send(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> bool: ...
    async def __aenter__(self) -> "OutputAdapter": ...
    async def __aexit__(self, *args) -> None: ...
```

### 9.3 SSE Replacement for WebSocket (Full HITL)
**New File**: `kazma-ui/kazma_ui/sse_chat_v2.py`

```python
"""Enhanced SSE chat with full feature parity to deprecated WebSocket."""

async def chat_sse_stream_v2(request: Request, agent: KazmaAgent):
    """SSE endpoint with all WebSocket features + HITL."""
    # - Session management (shared with WebSocket)
    # - Model override via query param
    # - Tool call visualization
    # - Full HITL interrupt() support
    # - Cost tracking
    # - Context compaction
```

### 9.4 Config Migration UI
**New File**: `kazma-ui/kazma_ui/migrations_ui.py`

```python
"""Admin UI for viewing/running database migrations."""

@router.get("/admin/migrations")
async def list_migrations():
    from kazma_core.migrations import get_runner
    runner = get_runner("kazma-data/settings.db", "config")
    return runner.status()

@router.post("/admin/migrations/run")
async def run_migrations(target_version: int | None = None):
    from kazma_core.migrations import run_startup_migrations
    applied = run_startup_migrations({
        "config": "kazma-data/settings.db",
        "task": "kazma-data/swarm_tasks.db",
        "session": "kazma-data/sessions.db",
    })
    return {"applied": {k: [m.name for m in v] for k, v in applied.items()}}
```

### 9.5 Chaos Testing Framework
**New File**: `tests/chaos/test_chaos.py`

```python
"""Chaos engineering: inject failures at critical paths."""

import pytest
from unittest.mock import patch, AsyncMock

class TestChaos:
    @pytest.mark.chaos
    async def test_swarm_dispatch_timeout_handling(self):
        """Simulate engine.dispatch() hanging."""
        with patch("kazma_core.swarm.engine.SwarmEngine.dispatch", 
                   new_callable=AsyncMock) as mock:
            mock.side_effect = asyncio.TimeoutError()
            # Verify graceful handling in swarm_dispatch.py
    
    @pytest.mark.chaos
    async def test_config_store_sqlite_corruption(self):
        """Simulate SQLite disk full / corruption."""
        with patch("sqlite3.connect", side_effect=sqlite3.DatabaseError("disk full")):
            # Verify in-memory fallback activates
    
    @pytest.mark.chaos
    async def test_hitl_approval_timeout(self):
        """Simulate operator never responding to approval."""
        # Verify fail-closed after timeout
```

### 9.6 Architecture Docs Sync
**Update**: `architecture.md`

| Section | Additions |
|---------|-----------|
| **Configuration** | `config_schema.py` validation, `migrations.py` framework |
| **Error Handling** | `exceptions.py` hierarchy, `sanitize_error()` |
| **Observability** | OpenTelemetry tracing, Prometheus metrics |
| **Testing** | HITL gate verification, Sprint 14 regression, integration tests |
| **WebSocket** | Deprecated → SSE with 410 Gone |

---

## 📅 Detailed Timeline

| Week | Phase | Deliverables |
|------|-------|--------------|
| **Week 1** | Phase 6 | ✅ CI green, UI test infra |
| **Week 2** | Phase 7 | ✅ Multi-platform + HITL E2E tests |
| **Week 3** | Phase 8 | ✅ OpenTelemetry tracing + metrics |
| **Week 4** | Phase 9.1-9.2 | ✅ Circuit breaker tests + Adapter extraction |
| **Week 5** | Phase 9.3-9.4 | ✅ SSE replacement + Migration UI |
| **Week 6** | Phase 9.5-9.6 | ✅ Chaos tests + Docs sync |

---

## 🎯 Success Criteria

| Metric | Target |
|--------|--------|
| **CI Pass Rate** | 100% (all tests + docs sync) |
| **Test Coverage** | ≥80% core modules |
| **HITL Gate Verification** | Runs in CI on every PR |
| **OpenTelemetry** | Traces traces exported to collector (when OTEL_ENDPOINT set) |
| **Circuit Breaker** | `_probe_in_flight` tested under concurrency |
| **Zero Critical Bugs** | No silent failures, no leaked internals |

---

## 📝 Quick Start Commands

```bash
# Phase 6: Fix CI
cd G:/GitHubRepos/kazma
python -m pytest kazma-gateway/tests/test_gateway_ux.py::TestSlashCommands -v

# Phase 7: Run new integration tests
python -m pytest kazma-gateway/tests/integration/ kazma-core/tests/integration/ -v

# Phase 8: Test tracing (set OTEL endpoint)
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
python -m pytest kazma-core/tests/ -v

# Phase 9: Run chaos tests (manual)
python -m pytest tests/chaos/ -v -m chaos

# Docs sync
python scripts/check_docs_sync.py
```

---

This plan is executable as-is. Each phase has specific files, code snippets, and verification steps. Shall I start with **Phase 6.1** (fixing the 2 gateway test failures)?