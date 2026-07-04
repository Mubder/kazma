"""Local Tool Registry — Lightweight tool registration with auto-schema generation.

This module provides a decorator-based system for registering Python
functions as agent tools.  Each tool is automatically introspected to
produce an OpenAI-compatible JSON schema that LiteLLM / Kazma can
consume for function-calling.

Unlike the MCP-based ``ToolRegistry`` in ``kazma_core.tool_registry``,
this class runs tools **in-process** — no subprocess or network hop.
It also serves as the canonical tool schema provider for the Supervisor
graph.

Usage
─────

    from kazma_core.agent.tool_registry import LocalToolRegistry, tool

    registry = LocalToolRegistry()

    @registry.register(
        description="Read a file from the local filesystem.",
        category="filesystem",
    )
    async def file_read(path: str, encoding: str = "utf-8") -> str:
        ...

    # Or use the standalone decorator:
    @tool(description="Search the SQLite database.")
    async def sqlite_search(query: str, limit: int = 10) -> list[dict]:
        ...

    # Register built-ins at init:
    registry = LocalToolRegistry(include_builtins=True)

    # Get OpenAI-format definitions for the LLM:
    defs = registry.get_tool_definitions()

    # Execute a tool:
    result = await registry.execute("file_read", {"path": "/etc/hostname"})
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import sqlite3
import time
import types as _types
import typing as _typing
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any, get_type_hints

logger = logging.getLogger(__name__)


def _workspace_scope_error(p: Path, path: str, op: str) -> str | None:
    """Return a safety error string if *p* is outside the workspace.

    Returns ``None`` when the path is allowed.  Denies by default when
    the workspace module cannot be imported (fail-closed) so a broken
    install never silently opens the whole filesystem.
    """
    try:
        from kazma_core.tools.file_write import _ALLOW_ABSOLUTE, _WORKSPACE_ROOT
    except (ImportError, OSError):
        return f"Safety: workspace module unavailable — {op} denied. Path: {path}"
    if _WORKSPACE_ROOT and not _ALLOW_ABSOLUTE:
        if not p.is_relative_to(_WORKSPACE_ROOT) and not p.is_relative_to(Path("/tmp")):
            return f"Safety: {op} outside workspace are not allowed. Path: {path}"
    return None

# ── VectorMemory singleton for RAG tools ─────────────────────────────
_vector_memory: Any = None


def set_vector_memory(vm: Any) -> None:
    """Set the global VectorMemory instance (called by app.py at startup)."""
    global _vector_memory
    _vector_memory = vm


def get_vector_memory() -> Any:
    """Get the global VectorMemory instance."""
    return _vector_memory


# ══════════════════════════════════════════════════════════════════════════
# Schema generation from type hints
# ══════════════════════════════════════════════════════════════════════════

_PY_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json_schema(tp: Any) -> dict[str, Any]:
    """Convert a Python type annotation to a JSON Schema fragment.

    Handles:
      - Primitives (str, int, float, bool)
      - list[T] → {"type": "array", "items": ...}
      - dict[K, V] → {"type": "object"}
      - Optional[T] → T (nullable handled at parameter level)
      - Union[str, None] → T (same as Optional)
    """
    # Handle None type
    if tp is type(None):
        return {"type": "null"}

    # Direct primitive mapping
    if tp in _PY_TO_JSON:
        return {"type": _PY_TO_JSON[tp]}

    # Generic types (list[T], dict[K, V])
    origin = getattr(tp, "__origin__", None)

    if origin is list:
        args = getattr(tp, "__args__", ())
        if args:
            return {"type": "array", "items": _python_type_to_json_schema(args[0])}
        return {"type": "array"}

    if origin is dict:
        return {"type": "object"}

    # Optional[X] = Union[X, None] or X | None (Python 3.10+)
    if origin is _typing.Union or isinstance(tp, _types.UnionType):
        args = getattr(tp, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _python_type_to_json_schema(non_none[0])

    # Fallback
    return {"type": "string"}


def _generate_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """Generate an OpenAI-compatible JSON schema from a function signature.

    Inspects:
      - Parameter names and type hints
      - Default values (optional parameters)
      - Docstring for parameter descriptions
    """
    sig = inspect.signature(func)
    try:
        hints = get_type_hints(func)
    except Exception:
        hints = {}

    # Parse docstring for param descriptions
    param_descriptions: dict[str, str] = {}
    doc = inspect.getdoc(func) or ""
    for line in doc.split("\n"):
        line = line.strip()
        if ":" in line:
            # Handle "param_name: description" or "param_name (type): description"
            parts = line.split(":", 1)
            candidate = parts[0].strip().split("(")[0].strip().split(" ")[0].strip()
            if candidate and candidate in sig.parameters:
                param_descriptions[candidate] = parts[1].strip()

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        param_type = hints.get(name, str)
        schema_fragment = _python_type_to_json_schema(param_type)

        # Add description if found
        if name in param_descriptions:
            schema_fragment["description"] = param_descriptions[name]

        # Handle defaults
        if param.default is not inspect.Parameter.empty:
            schema_fragment["default"] = param.default
        else:
            required.append(name)

        properties[name] = schema_fragment

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


# ══════════════════════════════════════════════════════════════════════════
# Registered tool descriptor
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class LocalTool:
    """Metadata for a registered local tool."""

    name: str
    description: str
    category: str
    func: Callable[..., Any]
    input_schema: dict[str, Any]
    is_async: bool = True


# ══════════════════════════════════════════════════════════════════════════
# Registry
# ══════════════════════════════════════════════════════════════════════════


class LocalToolRegistry:
    """Lightweight, in-process tool registry with auto-schema generation.

    Register functions via the ``register`` decorator or ``register_function``.
    The registry auto-generates OpenAI-compatible JSON schemas from type hints.

    Optionally includes built-in tools for filesystem, SQLite search,
    and HTTP requests.
    """

    def __init__(self, include_builtins: bool = True) -> None:
        self._tools: dict[str, LocalTool] = {}
        if include_builtins:
            self._register_builtins()

    # ── Registration ────────────────────────────────────────────────

    def register(
        self,
        description: str = "",
        category: str = "general",
        name: str | None = None,
    ) -> Callable:
        """Decorator to register an async function as a tool.

        Usage::

            @registry.register(description="Read a file", category="fs")
            async def file_read(path: str) -> str:
                ...
        """

        def decorator(func: Callable) -> Callable:
            tool_name = name or func.__name__
            schema = _generate_schema(func)
            is_async = asyncio.iscoroutinefunction(func)

            self._tools[tool_name] = LocalTool(
                name=tool_name,
                description=description or inspect.getdoc(func) or f"Tool: {tool_name}",
                category=category,
                func=func,
                input_schema=schema,
                is_async=is_async,
            )
            logger.debug("Registered tool '%s' (category=%s, async=%s)", tool_name, category, is_async)
            return func

        return decorator

    def register_function(
        self,
        name: str,
        func: Callable[..., Any],
        description: str = "",
        category: str = "general",
    ) -> None:
        """Imperatively register a function as a tool."""
        schema = _generate_schema(func)
        is_async = asyncio.iscoroutinefunction(func)

        self._tools[name] = LocalTool(
            name=name,
            description=description or inspect.getdoc(func) or f"Tool: {name}",
            category=category,
            func=func,
            input_schema=schema,
            is_async=is_async,
        )

    # ── Schema export (OpenAI format) ───────────────────────────────

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return all registered tools in OpenAI function-calling format.

        Compatible with:
          - OpenAI ``tools`` parameter
          - LiteLLM ``tools`` parameter
          - Kazma tool schema format
        """
        definitions = []
        for tool in self._tools.values():
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
            )
        return definitions

    # ── Execution ───────────────────────────────────────────────────

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a registered tool by name.

        Args:
            tool_name: The tool name as registered.
            arguments: Tool arguments dict. May contain a private
                ``_hitl_approved`` key (bool) set by the graph's
                interrupt() gate to skip the redundant bus check.

        Returns:
            Dict with ``content`` (str) and ``is_error`` (bool).
        """
        # Pop the private HITL flag before tool execution — it is not a
        # real argument and must not leak into the tool call.
        _hitl_already_approved = bool(arguments.pop("_hitl_approved", False))
        tool = self._tools.get(tool_name)
        if tool is None:
            return {
                "content": f"Tool '{tool_name}' not found. Available: {list(self._tools.keys())}",
                "is_error": True,
            }

        # ── Retryable exception types (network/timeout only) ──────
        retryable_exc: tuple[type[Exception], ...] = (ConnectionError, TimeoutError, asyncio.TimeoutError)
        try:
            import httpx

            retryable_exc = retryable_exc + (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            )
        except ImportError:
            pass

        # Load retry config
        try:
            from kazma_core.retry import load_retry_config

            cfg = load_retry_config()
            max_attempts = cfg["max_attempts"]
            min_wait = cfg["min_wait"]
            max_wait = cfg["max_wait"]
        except Exception:
            max_attempts = 1  # No retry if config unavailable
            min_wait = 2
            max_wait = 10

        # ── Safety check — gate danger-tier tools (HITL) ───────────
        # Use the async check() so a real bus adapter can post an approval
        # request and await the operator's response. check_sync() only
        # blocks; it can never approve. Skip when the graph's interrupt()
        # gate already approved this call.
        if not _hitl_already_approved:
            try:
                import json as _json

                from kazma_core.swarm.safety import get_safety

                safety = get_safety()
                # Pre-filter: non-danger tools skip the bus entirely.
                if safety.enabled and safety.is_danger_tool(tool_name):
                    task_id = str(arguments.get("task_id", "")) if isinstance(arguments, dict) else ""
                    worker_name = str(arguments.get("worker_name", "")) if isinstance(arguments, dict) else ""
                    approved = await safety.check(
                        tool_name=tool_name,
                        tool_args=_json.dumps(arguments, default=str)[:200],
                        task_id=task_id,
                        worker_name=worker_name,
                    )
                    if not approved:
                        return {
                            "content": f"Tool '{tool_name}' denied by HITL approval gate.",
                            "is_error": True,
                        }
            except Exception:
                # Safety unavailable — fail closed (do not execute danger tools).
                return {
                    "content": f"Tool '{tool_name}' blocked — SafetyMiddleware unavailable.",
                    "is_error": True,
                }

        start = time.monotonic()
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                if tool.is_async:
                    result = await tool.func(**arguments)
                else:
                    # Run sync functions in a thread pool
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, lambda: tool.func(**arguments))

                duration_ms = (time.monotonic() - start) * 1000
                logger.info("Tool '%s' executed in %.0fms", tool_name, duration_ms)

                # Normalize result to string
                if isinstance(result, str):
                    content = result
                elif isinstance(result, dict | list):
                    content = json.dumps(result, ensure_ascii=False, indent=2)
                else:
                    content = str(result)

                return {"content": content, "is_error": False}

            except retryable_exc as exc:
                last_exc = exc
                if attempt < max_attempts:
                    wait_time = min(min_wait * (2 ** (attempt - 1)), max_wait)
                    logger.warning(
                        "Tool '%s' attempt %d/%d failed: %s (retrying in %ds)",
                        tool_name,
                        attempt,
                        max_attempts,
                        exc,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                # If last attempt, fall through to error return below

            except Exception as exc:
                # Non-retryable error — return immediately
                duration_ms = (time.monotonic() - start) * 1000
                logger.error("Tool '%s' failed after %.0fms: %s", tool_name, duration_ms, exc)
                return {"content": f"Error: {exc}", "is_error": True}

        # All retry attempts exhausted
        duration_ms = (time.monotonic() - start) * 1000
        logger.error("Tool '%s' failed after %d attempts (%.0fms): %s", tool_name, max_attempts, duration_ms, last_exc)
        return {"content": f"Error: {last_exc}", "is_error": True}

    # ── Introspection ───────────────────────────────────────────────

    def list_tools(self) -> list[dict[str, str]]:
        """Return a summary of all registered tools."""
        return [
            {
                "name": t.name,
                "description": t.description[:120],
                "category": t.category,
                "async": str(t.is_async),
            }
            for t in self._tools.values()
        ]

    def get_tool(self, name: str) -> LocalTool | None:
        """Get a specific tool by name."""
        return self._tools.get(name)

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def connected(self) -> bool:
        """Always True for local tools (no external connection)."""
        return True

    async def disconnect_all(self) -> None:
        """No-op for local tools (compatibility with MCP ToolRegistry)."""

    # ── Built-in tools ──────────────────────────────────────────────

    def _register_builtins(self) -> None:
        """Register the core built-in tools."""

        @self.register(
            description="Read a file from the local filesystem. Returns the file contents as text.",
            category="filesystem",
        )
        async def file_read(path: str, encoding: str = "utf-8") -> str:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return f"Error: File not found: {path}"
            if not p.is_file():
                return f"Error: Not a file: {path}"
            # Workspace scoping — block reads outside workspace (fail-closed)
            scope_err = _workspace_scope_error(p, path, "reads")
            if scope_err:
                return scope_err
            if p.stat().st_size > 1_000_000:  # 1MB cap
                return f"Error: File too large ({p.stat().st_size} bytes). Max 1MB."
            return p.read_text(encoding=encoding)

        @self.register(
            description="Write content to a local file. Creates parent directories if needed. Overwrites existing content.",
            category="filesystem",
        )
        async def file_write(path: str, content: str, encoding: str = "utf-8") -> str:
            p = Path(path).expanduser().resolve()
            # Workspace scoping — block writes outside workspace (fail-closed)
            scope_err = _workspace_scope_error(p, path, "writes")
            if scope_err:
                return scope_err
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding=encoding)
            return f"Wrote {len(content)} chars to {path}"

        @self.register(
            description="List files and directories at a path. Returns names sorted alphabetically.",
            category="filesystem",
        )
        async def file_list(path: str = ".", pattern: str = "*") -> str:
            p = Path(path).expanduser().resolve()
            if not p.exists():
                return f"Error: Path not found: {path}"
            if not p.is_dir():
                return f"Error: Not a directory: {path}"
            entries = sorted(str(child.name) for child in p.glob(pattern))
            if not entries:
                return f"No files matching '{pattern}' in {path}"
            return "\n".join(entries[:200])  # cap at 200 entries

        @self.register(
            description=(
                "Search for text inside files using regex. Returns matching lines with file paths and line numbers."
            ),
            category="filesystem",
        )
        async def file_search(
            pattern: str,
            path: str = ".",
            glob: str = "*.py",
            limit: int = 20,
        ) -> str:
            import re

            root = Path(path).expanduser().resolve()
            if not root.exists():
                return f"Error: Path not found: {path}"
            # Workspace scoping — block searches outside workspace (fail-closed)
            scope_err = _workspace_scope_error(root, path, "searches")
            if scope_err:
                return scope_err

            regex = re.compile(pattern)
            results: list[str] = []

            for file_path in root.rglob(glob):
                if file_path.is_file() and file_path.stat().st_size < 500_000:
                    try:
                        for i, line in enumerate(file_path.read_text(errors="replace").splitlines(), 1):
                            if regex.search(line):
                                results.append(f"{file_path}:{i}: {line.strip()}")
                                if len(results) >= limit:
                                    return "\n".join(results)
                    except Exception:
                        continue

            return "\n".join(results) if results else f"No matches for '{pattern}' in {path}/{glob}"

        @self.register(
            description=(
                "Execute a read-only SQL query against the local SQLite database. "
                "SELECT queries only. Returns rows as JSON."
            ),
            category="database",
        )
        async def sqlite_query(
            query: str,
            db_path: str = "kazma-data/checkpoints.db",
            params: list[Any] | None = None,
            limit: int = 100,
        ) -> str:
            # Safety: only allow SELECT
            normalized = query.strip().upper()
            if not normalized.startswith("SELECT"):
                raise ValueError("Only SELECT queries are allowed for safety.")
            # Block multi-statement queries
            if ";" in query.strip().rstrip(";"):
                raise ValueError("Multi-statement queries are not allowed.")

            path = Path(db_path).expanduser().resolve()

            # Security: restrict to known Kazma data directories
            _ALLOWED_DB_ROOTS = [
                Path("kazma-data").resolve(),
                Path.home() / ".kazma",
                Path("/tmp"),  # for tests
            ]
            allowed = any(
                path.is_relative_to(root) or path == root
                for root in _ALLOWED_DB_ROOTS
            )
            if not allowed:
                return (
                    f"Error: Access denied. Database path must be under "
                    f"kazma-data/ or ~/.kazma/. Got: {db_path}"
                )

            if not path.exists():
                return f"Error: Database not found: {db_path}"

            try:
                conn = sqlite3.connect(str(path))
                conn.row_factory = sqlite3.Row
                cursor = conn.execute(query, params or [])
                rows = cursor.fetchmany(limit)
                conn.close()

                if not rows:
                    return "[]"

                result = [dict(row) for row in rows]
                return json.dumps(result, ensure_ascii=False, indent=2)
            except Exception as exc:
                return f"SQL Error: {exc}"

        @self.register(
            description=(
                "Search long-term memory for relevant past conversations, facts, or preferences. "
                "Use this before answering questions that may require context from earlier sessions."
            ),
            category="memory",
        )
        async def memory_search(query: str, limit: int = 5) -> str:
            mem = get_vector_memory()
            if mem is None:
                return "Error: VectorMemory not initialized. RAG not available."
            results = mem.search(query=query, n_results=limit)
            if not results:
                return "No relevant memories found."
            return json.dumps(results, ensure_ascii=False, indent=2)

        @self.register(
            description=(
                "Store a fact, preference, or conversation fragment in long-term memory. "
                "Use this when the user shares personal info, preferences, or important context "
                "that should be remembered across sessions."
            ),
            category="memory",
        )
        async def memory_store(text: str, metadata: str = "{}") -> str:
            mem = get_vector_memory()
            if mem is None:
                return "Error: VectorMemory not initialized. RAG not available."
            try:
                meta = json.loads(metadata) if isinstance(metadata, str) else metadata
            except json.JSONDecodeError:
                meta = {"raw": metadata}
            doc_id = mem.add(text=text, metadata=meta)
            return f"Stored memory (id={doc_id})"

        @self.register(
            description="Get the current date, time, and timezone in ISO-8601 format.",
            category="utility",
        )
        async def current_datetime() -> str:
            from datetime import datetime

            now = datetime.now(UTC)
            return now.isoformat()

        @self.register(
            description="Execute a shell command and return stdout+stderr. Use with caution.",
            category="system",
        )
        async def shell_exec(command: str, timeout: int = 30) -> str:
            import shlex
            import subprocess
            # Log all shell_exec invocations — this is a dangerous tool
            logger.warning(
                "[SECURITY] shell_exec called: %s",
                command[:200] if len(command) > 200 else command,
            )
            # Parse command into args — NO shell interpretation
            try:
                args = shlex.split(command)
            except ValueError as exc:
                return f"Error: Invalid command syntax: {exc}"

            if not args:
                return "Error: Empty command"

            # Restricted PATH — only allow read-only / build-safe binaries
            # NO interpreters (python, node), NO network tools (curl, wget),
            # NO container runtimes (docker), NO file modification (chmod, sed)
            _SAFE_BINARIES = {
                # Read-only system
                "ls", "cat", "head", "tail", "grep", "find", "wc", "sort",
                "uniq", "echo", "printf", "date", "whoami", "pwd", "env",
                "df", "du", "free", "uptime", "uname", "hostname",
                # Build tools
                "git", "uv", "pytest", "ruff", "mypy",
                # Archive
                "tar", "gzip", "gunzip", "zip", "unzip",
                # Process info (read-only)
                "ps", "pgrep",
                # Text processing (read-only)
                "jq", "tr", "cut",
                # File ops (read-only)
                "mkdir", "cp", "mv", "touch",
                # Process control (safe)
                "sleep",
                # Kazma internal
                "hermes", "kazma",
            }
            binary = Path(args[0]).name  # resolve paths like /full/path/ls → ls
            if binary not in _SAFE_BINARIES:
                # Also check if it's an absolute path to a safe binary
                if not any(args[0].endswith(f"/{b}") for b in _SAFE_BINARIES):
                    return (
                        f"Error: '{binary}' is not in the allowed binary list. "
                        f"Allowed: {', '.join(sorted(_SAFE_BINARIES))}"
                    )

            try:
                result = subprocess.run(
                    args,
                    capture_output=True,
                    timeout=timeout,
                    text=True,
                    shell=False,
                    env=None,  # inherit OS environment
                )
                output = result.stdout
                if result.stderr:
                    output += f"\n[stderr]\n{result.stderr}"
                if result.returncode != 0:
                    output += f"\n[exit code: {result.returncode}]"
                return output[:10_000]  # cap output
            except TimeoutError:
                return f"Error: Command timed out after {timeout}s"
            except FileNotFoundError:
                return f"Error: Command not found: {args[0]}"
            except Exception as exc:
                return f"Error: {exc}"

        # ── Generic send_message tool ─────────────────────────────
        @self.register(
            description=(
                "Send a text message to the current conversation thread. "
                "Use this to reply to the user. The platform and delivery "
                "channel are handled automatically."
            ),
            category="communication",
        )
        async def send_message(
            target_id: str,
            text: str,
            backend: str = "telegram",
        ) -> str:
            from kazma_core.tools.send_message import send_message as _send

            return await _send(target_id=target_id, text=text, backend=backend)

        # ── Sub-agent spawning tools ─────────────────────────────
        @self.register(
            description=(
                "Spawn a sub-agent to handle a focused task independently. "
                "The sub-agent has its own context and tools. Use this for "
                "research, code generation, file operations, or any task that "
                "benefits from dedicated focus. Returns a summary when done."
            ),
            category="delegation",
        )
        async def spawn_agent(
            goal: str,
            context: str = "",
            tools: str = "[]",
        ) -> str:
            import json as _json

            from kazma_core.agent.sub_agent import get_sub_agent_manager

            manager = get_sub_agent_manager()
            if manager is None:
                return "Error: Sub-agent manager not initialized."

            try:
                tool_list = _json.loads(tools) if isinstance(tools, str) else tools
            except _json.JSONDecodeError:
                tool_list = None

            result = await manager.spawn(goal=goal, context=context, tools=tool_list)
            return _json.dumps(result.to_dict(), ensure_ascii=False, indent=2)

        @self.register(
            description=(
                "Spawn multiple sub-agents in parallel for independent tasks. "
                "Use this when you have 2-3 unrelated tasks that can run concurrently. "
                "Returns a list of results, one per task."
            ),
            category="delegation",
        )
        async def spawn_agents(tasks: str) -> str:
            import json as _json

            from kazma_core.agent.sub_agent import get_sub_agent_manager

            manager = get_sub_agent_manager()
            if manager is None:
                return "Error: Sub-agent manager not initialized."

            try:
                task_list = _json.loads(tasks) if isinstance(tasks, str) else tasks
            except _json.JSONDecodeError:
                return "Error: tasks must be a JSON array."

            if not isinstance(task_list, list):
                return "Error: tasks must be a JSON array."

            results = await manager.spawn_parallel(task_list)
            return _json.dumps(
                [r.to_dict() for r in results],
                ensure_ascii=False,
                indent=2,
            )

        # ── Cron scheduling tools ─────────────────────────────────
        @self.register(
            description=(
                "Schedule a task to run at a future time. The task runs autonomously "
                "without you needing to be present. Results are delivered to this conversation.\n\n"
                "Timing: '5m' (5 minutes), '1h' (1 hour), 'daily at 9am', '2026-06-25T09:00:00'"
            ),
            category="automation",
        )
        async def schedule_task(timing: str, prompt: str) -> str:
            import json as _json

            from kazma_core.cron.scheduler import get_cron_scheduler

            scheduler = get_cron_scheduler()
            if scheduler is None:
                return "Error: Cron scheduler not initialized."

            result = await scheduler.schedule(timing=timing, prompt=prompt)
            return _json.dumps(result, ensure_ascii=False, indent=2)

        @self.register(
            description="List all scheduled tasks and their status.",
            category="automation",
        )
        async def list_scheduled() -> str:
            import json as _json

            from kazma_core.cron.scheduler import get_cron_scheduler

            scheduler = get_cron_scheduler()
            if scheduler is None:
                return "Error: Cron scheduler not initialized."

            jobs = await scheduler.list_jobs()
            return _json.dumps(jobs, ensure_ascii=False, indent=2)

        @self.register(
            description="Cancel a scheduled task by job ID.",
            category="automation",
        )
        async def cancel_scheduled(job_id: str) -> str:
            import json as _json

            from kazma_core.cron.scheduler import get_cron_scheduler

            scheduler = get_cron_scheduler()
            if scheduler is None:
                return "Error: Cron scheduler not initialized."

            result = await scheduler.cancel(job_id)
            return _json.dumps(result, ensure_ascii=False, indent=2)

        # ── Code execution tool ───────────────────────────────────
        @self.register(
            description=(
                "Execute Python code in a sandboxed subprocess. Returns stdout + stderr. "
                "Max 30s timeout, 512MB memory, isolated mode (no site-packages). "
                "Use for calculations, data processing, prototyping."
            ),
            category="code",
        )
        async def python_exec(code: str, timeout: int = 30) -> str:
            from kazma_core.tools.code_exec import python_exec as _exec

            return await _exec(code=code, timeout=timeout)

        # ── Context window indicator ──────────────────────────────
        @self.register(
            description=(
                "Show context window usage — token count, percentage, and summarization "
                "threshold. Use '/context details' for per-role breakdown."
            ),
            category="diagnostics",
        )
        async def context_info(details: bool = False) -> str:
            from kazma_core.tools.context_cmd import context_cmd as _ctx
            from kazma_core.tools.export_session import get_current_session_messages

            # Messages come from the per-invocation ContextVar set by the
            # graph's tool-worker node.  This keeps concurrent sessions
            # isolated (no shared module-global list).
            messages = get_current_session_messages()
            return await _ctx(messages, detailed=details)

        # ── Register tools from kazma_core/tools/ ──────────────────────
        try:
            from kazma_core.tools.web_search import web_search
            self.register_function("web_search", web_search,
                description="Search the web using DuckDuckGo. Returns markdown results with titles, URLs, and snippets.",
                category="search")
        except ImportError:
            logger.debug("web_search not available (missing duckduckgo-search)")

        try:
            from kazma_core.tools.read_url import read_url
            self.register_function("read_url", read_url,
                description="Fetch and extract readable content from a URL. Returns text content.",
                category="search")
        except ImportError:
            logger.debug("read_url not available (missing trafilatura)")

        try:
            from kazma_core.tools.image_gen import generate_image
            self.register_function("generate_image", generate_image,
                description="Generate an image from a text prompt using pollinations.ai. Returns the saved file path.",
                category="media")
        except ImportError:
            logger.debug("generate_image not available")

        try:
            from kazma_core.tools.vision_analyze import analyze_image
            self.register_function("analyze_image", analyze_image,
                description="Analyze an image using LLM vision. Provide a local path or URL and an optional question.",
                category="media")
        except ImportError:
            logger.debug("analyze_image not available")

        try:
            from kazma_core.tools.export_session import export_session
            self.register_function("export_session", export_session,
                description="Export the current conversation session to a file (JSON or Markdown format).",
                category="utility")
        except ImportError:
            logger.debug("export_session not available")

        logger.info("Registered %d built-in tools", len(self._tools))


# ══════════════════════════════════════════════════════════════════════════
# Standalone decorator (convenience)
# ══════════════════════════════════════════════════════════════════════════

# Module-level registry for quick standalone use
_default_registry = LocalToolRegistry(include_builtins=False)

# Singleton with built-in tools for runtime consumers (swarm workers, etc.)
_builtin_registry: LocalToolRegistry | None = None


def get_tool_registry() -> LocalToolRegistry:
    """Return a module-level :class:`LocalToolRegistry` with built-in tools.

    Built-in tools (web_search, file_read, file_write, shell_exec, etc.)
    are included so that callers — especially swarm workers — can resolve
    and execute tools without constructing their own registry.

    The instance is cached; subsequent calls return the same object.
    """
    global _builtin_registry
    if _builtin_registry is None:
        _builtin_registry = LocalToolRegistry(include_builtins=True)
    return _builtin_registry


def tool(
    description: str = "",
    category: str = "general",
    name: str | None = None,
) -> Callable:
    """Standalone decorator — registers into the module-level default registry.

    Usage::

        from kazma_core.agent.tool_registry import tool

        @tool(description="Do something cool")
        async def my_tool(x: int) -> str:
            return str(x * 2)
    """
    return _default_registry.register(description=description, category=category, name=name)
