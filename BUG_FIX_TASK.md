# 🔧 Comprehensive Bug Fix Task for Kazma Repository

## Executive Summary

The Kazma repository has **excellent test coverage** (50+ test files with comprehensive test cases) and a well-architected design, but **critical implementation gaps** are preventing the codebase from being functional. This task outlines all remaining bugs and provides detailed instructions for fixing them.

**Current State:** Tests exist → Implementation missing → Imports fail → Code doesn't run

**Target State:** All modules implemented → All imports work → Code runs successfully → All tests pass

---

## 🔴 CRITICAL ISSUES (MUST FIX FIRST)

### Issue #1: Truncated `pyproject.toml` — Build Configuration Broken

**Location:** `pyproject.toml`, line 55

**Current Code:**
```toml
[tool.hatch.build.targets.wheel]
packages = ["kazma-core/kazma_core", "kazma-memory/kazma_memory", "kazma-skills/kazma_skills", "kazma-connectors/kazma_connectors", "kazma-providers/kazma_providers", "kazma-ui/kazma_ui", "kazma-c[...]
```

**Problem:**
- The packages list is truncated with `"kazma-c[...]"` 
- This causes the build system to fail when trying to package the project
- The package cannot be installed via `pip install -e .`
- CI/CD workflows will fail

**Solution:**
Replace the entire line with the complete packages list:

```toml
[tool.hatch.build.targets.wheel]
packages = [
    "kazma-core/kazma_core",
    "kazma-memory/kazma_memory",
    "kazma-skills/kazma_skills",
    "kazma-connectors/kazma_connectors",
    "kazma-providers/kazma_providers",
    "kazma-ui/kazma_ui",
    "kazma-cli/kazma_cli",
    "kazma-tui/kazma_tui"
]
```

**Impact:** Fixes package building, enables installation, unblocks CI/CD

---

### Issue #2: Missing Core Implementation — `kazma-core` Modules Empty

**Location:** `kazma-core/kazma_core/` directory

**Problem:**
The following **critical modules are imported but do not exist:**

1. **`kazma_core/state.py`** ❌ MISSING
   - Needed by: `agent.py` (line 27)
   - Imports: `from kazma_core.state import AgentState, initial_state`
   - Defines: The AgentState TypedDict used throughout the agent

2. **`kazma_core/llm_provider.py`** ❌ MISSING
   - Needed by: `agent.py` (line 29)
   - Imports: `from kazma_core.llm_provider import LLMProvider, LLMConfig, LLMResponse`
   - Purpose: Abstracts LLM interactions (OpenAI, Anthropic, local models)

3. **`kazma_core/tool_registry.py`** ❌ MISSING
   - Needed by: `agent.py` (line 30)
   - Imports: `from kazma_core.tool_registry import ToolRegistry`
   - Purpose: Manages MCP tool discovery and execution

4. **`kazma_core/cost_breaker.py`** ❌ MISSING
   - Needed by: `agent.py` (line 31)
   - Imports: `from kazma_core.cost_breaker import CostCircuitBreaker, create_cost_breaker`
   - Purpose: Prevents runaway costs with configurable threshold ($0.50 default)

5. **`kazma_core/tracing.py`** ❌ MISSING
   - Needed by: `agent.py` (line 32)
   - Imports: `from kazma_core.tracing import KazmaTracer, create_tracer`
   - Purpose: Integrates Langfuse and OpenTelemetry for observability

6. **`kazma_core/authority.py`** ❌ MISSING
   - Needed by: `agent.py` (line 28)
   - Imports: `from kazma_core.authority import ContextAuthority, create_authority`
   - Purpose: Enforces 80% context window compaction to prevent exhaustion

**Current Error:**
When running the agent, Python will immediately raise:
```
ImportError: cannot import name 'AgentState' from 'kazma_core.state'
```

**Solution:**
Create each missing module with the required classes and functions. Use the test files as the specification for what each module should implement.

#### **Module #1: `kazma-core/kazma_core/state.py`**

**Test Reference:** `tests/test_*` (see all tests that import AgentState)

**Required Exports:**
```python
from typing import TypedDict, Any, Optional
from datetime import datetime

class AgentState(TypedDict, total=False):
    """Agent state container for the ReAct loop."""
    messages: list[dict[str, Any]]
    tool_results: dict[str, Any]
    context_tokens: int
    created_at: str
    last_cp_id: str
    provenance: dict[str, Any]
    _should_continue: bool

def initial_state() -> AgentState:
    """Return a fresh AgentState for a new session."""
    return {
        "messages": [],
        "tool_results": {},
        "context_tokens": 0,
        "created_at": datetime.utcnow().isoformat(),
        "last_cp_id": "",
        "provenance": {},
        "_should_continue": True,
    }
```

**Test Coverage:** Reference `tests/test_agent_discovery.py`, `tests/test_integration.py`

---

#### **Module #2: `kazma-core/kazma_core/llm_provider.py`**

**Test Reference:** `tests/test_llm_provider.py` (lines 1-100+)

**Required Exports:**
```python
from dataclasses import dataclass
from typing import Optional, Any
import httpx

@dataclass
class LLMConfig:
    """Configuration for LLM provider."""
    model: str
    base_url: str
    api_key: str
    temperature: float = 0.7
    max_tokens: int = 4096

    @classmethod
    def from_dict(cls, data: dict) -> "LLMConfig":
        """Create from dictionary (e.g., from kazma.yaml)."""
        return cls(
            model=data.get("model", "gpt-4o-mini"),
            base_url=data.get("base_url", "https://api.openai.com/v1"),
            api_key=data.get("api_key", ""),
            temperature=data.get("temperature", 0.7),
            max_tokens=data.get("max_tokens", 4096),
        )

@dataclass
class ToolCall:
    """Represents a tool call from the LLM."""
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass
class LLMResponse:
    """Response from an LLM call."""
    content: str
    model: str
    usage: dict[str, int]  # {"prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z}
    cost_usd: float
    tool_calls: list[ToolCall] = None

class LLMProvider:
    """Manages LLM interactions via OpenAI-compatible API."""
    
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={"Authorization": f"Bearer {config.api_key}"}
        )
    
    async def chat(
        self,
        messages: list[dict[str, str]],
        tools: Optional[list[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """Call the LLM with optional tools."""
        # Implement OpenAI-compatible API call
        # Return LLMResponse with content, usage, cost_usd, tool_calls
        pass
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
```

**Test Coverage:** `tests/test_llm_provider.py`

---

#### **Module #3: `kazma-core/kazma_core/tool_registry.py`**

**Test Reference:** `tests/test_tool_registry.py`

**Required Exports:**
```python
from typing import Any, Callable, Optional
from dataclasses import dataclass

@dataclass
class Tool:
    """Represents a single tool/function."""
    name: str
    description: str
    parameters: dict[str, Any]
    function: Callable

class ToolRegistry:
    """Manages tool registration and execution."""
    
    def __init__(self):
        self._tools: dict[str, Tool] = {}
        self._mcp_servers: dict[str, Any] = {}
    
    async def connect_server(self, server_config: dict) -> int:
        """Connect to an MCP server and register its tools."""
        # Connect to server, discover tools
        # Return count of tools registered
        pass
    
    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict,
        function: Callable
    ) -> None:
        """Register a new tool."""
        pass
    
    def get_tool_definitions(self) -> list[dict]:
        """Get tool definitions for the LLM (OpenAI format)."""
        pass
    
    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Execute a tool by name with given arguments."""
        pass
    
    async def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        pass
```

**Test Coverage:** `tests/test_tool_registry.py`

---

#### **Module #4: `kazma-core/kazma_core/cost_breaker.py`**

**Test Reference:** `tests/test_cost_breaker.py`

**Required Exports:**
```python
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

@dataclass
class CostCircuitBreaker:
    """Prevents runaway costs by enforcing a budget threshold."""
    
    threshold_usd: float = 0.50
    silence_window_seconds: int = 300  # 5 minutes
    
    def __post_init__(self):
        self._total_cost = 0.0
        self._last_halt_time: Optional[datetime] = None
        self._interaction_count = 0
    
    def record_user_interaction(self) -> None:
        """Record that a user interaction occurred."""
        self._interaction_count += 1
    
    def record_cost(self, cost_usd: float) -> None:
        """Record cost from an LLM call."""
        self._total_cost += cost_usd
        logger.debug(f"Cost recorded: ${cost_usd:.4f}, total: ${self._total_cost:.4f}")
    
    def should_halt(self) -> bool:
        """Check if the circuit breaker should halt execution."""
        if self._total_cost >= self.threshold_usd:
            self._last_halt_time = datetime.utcnow()
            return True
        return False
    
    def reset(self) -> None:
        """Reset the circuit breaker."""
        self._total_cost = 0.0
        self._last_halt_time = None
        self._interaction_count = 0
    
    def get_status(self) -> dict:
        """Get current status."""
        return {
            "total_cost": self._total_cost,
            "threshold": self.threshold_usd,
            "is_halted": self.should_halt(),
            "interactions": self._interaction_count,
        }

def create_cost_breaker(threshold: float = 0.50) -> CostCircuitBreaker:
    """Factory function to create a cost circuit breaker."""
    return CostCircuitBreaker(threshold_usd=threshold)
```

**Test Coverage:** `tests/test_cost_breaker.py`

---

#### **Module #5: `kazma-core/kazma_core/tracing.py`**

**Test Reference:** `tests/test_tracing.py`

**Required Exports:**
```python
from dataclasses import dataclass
from typing import Optional, Any
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class TraceEvent:
    """Represents a single trace event."""
    timestamp: str
    event_type: str  # "llm_call", "tool_execution", "checkpoint", etc.
    data: dict[str, Any]

class KazmaTracer:
    """Handles tracing to Langfuse, OpenTelemetry, or console."""
    
    def __init__(self, backend: str = "console", config: Optional[dict] = None):
        self.backend = backend
        self.config = config or {}
        self._events: list[TraceEvent] = []
    
    def trace_llm_call(
        self,
        model: str,
        prompt: str,
        response: str,
        tokens: int,
        cost: float,
        duration_ms: float,
    ) -> None:
        """Trace an LLM call."""
        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            event_type="llm_call",
            data={
                "model": model,
                "prompt_preview": prompt[:100],
                "response_preview": response[:100],
                "tokens": tokens,
                "cost_usd": cost,
                "duration_ms": duration_ms,
            }
        )
        self._events.append(event)
        if self.backend == "console":
            logger.info(f"LLM call: {model} - {tokens} tokens - ${cost:.4f} - {duration_ms:.0f}ms")
    
    def trace_tool_execution(
        self,
        tool_name: str,
        input_data: dict,
        output_data: dict,
        duration_ms: float,
        success: bool,
    ) -> None:
        """Trace a tool execution."""
        event = TraceEvent(
            timestamp=datetime.utcnow().isoformat(),
            event_type="tool_execution",
            data={
                "tool": tool_name,
                "input": input_data,
                "output": output_data,
                "duration_ms": duration_ms,
                "success": success,
            }
        )
        self._events.append(event)
        if self.backend == "console":
            logger.info(f"Tool '{tool_name}' executed in {duration_ms:.0f}ms (success={success})")
    
    def get_events(self) -> list[TraceEvent]:
        """Get all recorded events."""
        return self._events
    
    def shutdown(self) -> None:
        """Clean shutdown of the tracer."""
        if self.backend == "langfuse":
            # Flush Langfuse events
            pass
        logger.info(f"Tracer shutdown ({self.backend})")

def create_tracer(backend: str = "console", config: Optional[dict] = None) -> KazmaTracer:
    """Factory function to create a tracer."""
    return KazmaTracer(backend=backend, config=config)
```

**Test Coverage:** `tests/test_tracing.py`, `tests/test_tracestore.py`

---

#### **Module #6: `kazma-core/kazma_core/authority.py`**

**Test Reference:** `tests/test_authority.py`

**Required Exports:**
```python
from typing import Any, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class ContextAuthority:
    """Enforces 80% context window compaction to prevent exhaustion."""
    
    model: str
    window: int  # Total context tokens available
    llm_client: Any  # LLMProvider instance
    compaction_threshold: float = 0.8
    
    async def check_and_enforce(self, state: dict[str, Any]) -> dict[str, Any]:
        """Check if context is at 80%, and compact if needed."""
        messages = state.get("messages", [])
        estimated_tokens = self._estimate_tokens(messages)
        
        if estimated_tokens >= (self.window * self.compaction_threshold):
            logger.info(
                f"Context at {estimated_tokens / self.window * 100:.1f}% — triggering compaction"
            )
            state = await self._compact(state)
        
        return state
    
    def _estimate_tokens(self, messages: list[dict]) -> int:
        """Estimate token count for messages."""
        # Rough estimation: ~4 chars per token
        total_chars = sum(
            len(str(msg.get("content", ""))) 
            for msg in messages
        )
        return total_chars // 4
    
    async def _compact(self, state: dict[str, Any]) -> dict[str, Any]:
        """Compact messages by summarizing older ones."""
        # Use LLM to summarize early conversation
        # Replace early messages with summary
        logger.info("Compaction not yet implemented — returning state as-is")
        return state

def create_authority(
    model: str,
    window: int,
    llm_client: Any,
) -> ContextAuthority:
    """Factory function to create a context authority."""
    return ContextAuthority(model=model, window=window, llm_client=llm_client)
```

**Test Coverage:** `tests/test_authority.py`, `tests/test_compaction.py`

---

## 🟡 MEDIUM PRIORITY ISSUES

### Issue #3: Other Missing Monorepo Packages

These packages have empty directories but no implementation:

#### **`kazma-memory/`**
**Status:** Empty directory

**Tests expecting:** `tests/test_tantivy_backend.py`

**Minimal Implementation Needed:**
```python
# kazma-memory/kazma_memory/__init__.py
from kazma_memory.sqlite_backend import SQLiteMemoryBackend
from kazma_memory.tantivy_backend import TantivySearchBackend
from kazma_memory.router import SearchBackendRouter

__all__ = ["SQLiteMemoryBackend", "TantivySearchBackend", "SearchBackendRouter"]
```

Create:
- `kazma-memory/kazma_memory/sqlite_backend.py` — SQLite vector store
- `kazma-memory/kazma_memory/tantivy_backend.py` — Tantivy FTS backend
- `kazma-memory/kazma_memory/router.py` — Route between backends

---

#### **`kazma-providers/`**
**Status:** Empty directory

**Tests expecting:** (referenced in `agent.py` initialization)

**Minimal Implementation Needed:**
```python
# kazma-providers/kazma_providers/__init__.py
from kazma_providers.base import BaseProvider
from kazma_providers.openai import OpenAIProvider

__all__ = ["BaseProvider", "OpenAIProvider"]
```

---

#### **`kazma-connectors/`**
**Status:** Empty directory

**Minimal Implementation Needed:**
```python
# kazma-connectors/kazma_connectors/__init__.py
# Stub for now, expand later with Telegram/Discord/Slack adapters
```

---

#### **`kazma-skills/`**
**Status:** Empty directory

**Tests expecting:** `tests/test_hub_manifest.py`, `tests/test_hub_registry.py`

**Minimal Implementation Needed:**
```python
# kazma-skills/kazma_skills/__init__.py
from kazma_skills.loader import SkillLoader

__all__ = ["SkillLoader"]
```

---

#### **`kazma-ui/`**
**Status:** Empty directory

**Tests expecting:** (FastAPI + HTMX app)

**Minimal Implementation Needed:**
```python
# kazma-ui/kazma_ui/app.py
from fastapi import FastAPI

async def create_app():
    app = FastAPI(title="Kazma UI")
    
    @app.get("/health")
    async def health():
        return {"status": "ok"}
    
    return app

async def main():
    import uvicorn
    app = await create_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

#### **`kazma-cli/`**
**Status:** Empty directory

**Tests expecting:** `tests/test_hub_cli.py`

**Minimal Implementation Needed:**
```python
# kazma-cli/kazma_cli/main.py
import click

@click.group()
def main():
    """Kazma CLI"""
    pass

@main.command()
def hub():
    """Hub management commands"""
    click.echo("Hub commands not yet implemented")

if __name__ == "__main__":
    main()
```

---

#### **`kazma-tui/`**
**Status:** Empty directory

**Tests expecting:** `tests/test_tui.py`

**Minimal Implementation Needed:**
```python
# kazma-tui/kazma_tui/tui.py
from textual.app import ComposeResult, RenderableType
from textual.containers import Container

def main():
    """Terminal UI entry point"""
    print("Kazma TUI - not yet implemented")
```

---

### Issue #4: Missing Core Modules Beyond agent.py

Several other core modules are imported by tests but don't exist:

**Missing in `kazma-core/kazma_core/`:**
- `rbac.py` — Role-based access control
- `division_sandbox.py` — Division-level isolation
- `mcp_client.py` — MCP protocol client
- `security/` directory with security-related modules
- `hub/` directory with skill registry
- `delegation/` directory with agent-to-agent protocol
- And many others...

**Strategy:** Start with the 6 critical modules (state, llm_provider, tool_registry, cost_breaker, tracing, authority). These are blocking everything else.

---

### Issue #5: `serve.py` Missing Error Handling

**Location:** `serve.py`, lines 14-26

**Current Code:**
```python
proc = subprocess.Popen(
    [python_exe, "-m", "uvicorn", app_factory, "--factory", "--host", "0.0.0.0", "--port", "8000"],
)
```

**Problems:**
- No error handling if port 8000 is already in use
- No handling if `uvicorn` is not installed
- No subprocess error propagation
- No graceful shutdown handling

**Solution:**
```python
#!/usr/bin/env python3
"""Kazma serve script - starts the WebUI server."""

import subprocess
import sys
import signal
import time
from pathlib import Path

# Use the same Python that's running this script
python_exe = sys.executable

# Can override the app factory via environment variable
app_factory = "kazma_ui.app:create_app"

try:
    # Start the server
    proc = subprocess.Popen(
        [python_exe, "-m", "uvicorn", app_factory, "--factory", "--host", "0.0.0.0", "--port", "8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    
    print(f"Server started with PID {proc.pid}")
    print("Open http://localhost:8000 in your browser")
    print("Press Ctrl+C to stop\n")
    
    # Wait for server to start
    time.sleep(2)
    
    # Check if process is still running
    if proc.poll() is not None:
        stdout, stderr = proc.communicate()
        if stderr:
            print(f"❌ Server failed to start:\n{stderr.decode()}")
            sys.exit(1)
    
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("Server stopped")
        
except FileNotFoundError:
    print("❌ Error: uvicorn not found")
    print("Install with: pip install uvicorn[standard]")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
```

---

## 📋 Implementation Checklist

### Phase 1: CRITICAL (Blocks everything)
- [ ] Fix `pyproject.toml` line 55 (complete packages list)
- [ ] Create `kazma-core/kazma_core/state.py`
- [ ] Create `kazma-core/kazma_core/llm_provider.py`
- [ ] Create `kazma-core/kazma_core/tool_registry.py`
- [ ] Create `kazma-core/kazma_core/cost_breaker.py`
- [ ] Create `kazma-core/kazma_core/tracing.py`
- [ ] Create `kazma-core/kazma_core/authority.py`

**Goal:** Get basic imports working

### Phase 2: CORE FUNCTIONALITY
- [ ] Create remaining `kazma-core/kazma_core/` modules referenced by tests
- [ ] Create `kazma-memory/` stub implementations
- [ ] Create `kazma-providers/` stub implementations
- [ ] Ensure all test imports work

**Goal:** Tests can at least run (may fail on logic, but won't error on imports)

### Phase 3: FULL IMPLEMENTATION
- [ ] Implement actual logic in each module (not just stubs)
- [ ] Make all tests pass
- [ ] Update `serve.py` with error handling
- [ ] Validate end-to-end agent workflow

**Goal:** Agent runs successfully and tests pass

---

## 🎯 Success Criteria

✅ All these conditions must be true:

1. `pyproject.toml` builds without errors
2. `python -m pytest tests/ -v` runs (at least starts)
3. `python -m kazma_core.agent` imports without errors
4. `./serve.py` starts uvicorn without errors
5. All 50+ test files can at least import their modules
6. Core ReAct loop in `agent.py` can execute

---

## 📚 Additional Context

### Test Files Provide Specifications

Each test file essentially **specifies what the implementation should do**:

- `tests/test_llm_provider.py` → What `llm_provider.py` should implement
- `tests/test_tool_registry.py` → What `tool_registry.py` should implement
- `tests/test_cost_breaker.py` → What `cost_breaker.py` should implement
- etc.

**Strategy:** Read the test file first to understand requirements, then implement the module to satisfy those tests.

### Incremental Approach

Rather than trying to implement everything at once:

1. Fix `pyproject.toml` 
2. Create the 6 critical modules (even as stubs)
3. Run tests to see what fails
4. Implement failures incrementally
5. Add missing modules as tests require them

This way you make constant progress and don't get overwhelmed.

---

## 🚀 Next Steps for Your Agent

When implementing, the agent should:

1. **Prioritize CRITICAL issues first** (Phase 1 above)
2. **Read test files** to understand specifications
3. **Create minimal implementations** that satisfy imports
4. **Run tests frequently** to validate progress
5. **Expand implementations** based on test failures
6. **Document decisions** in inline code comments

**Key Command to Run Frequently:**
```bash
python -m pytest tests/ -v --tb=short
```

This will show which imports fail first, which tests fail next, etc.

---

**Created by:** Code Analysis  
**Status:** Needs Implementation  
**Blocking:** Agent startup, test suite, CI/CD
