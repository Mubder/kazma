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
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any, get_type_hints

__all__ = ["LocalTool", "LocalToolRegistry", "get_tool_registry", "tool"]

logger = logging.getLogger(__name__)

# Strong references for background dispatch tasks so the GC doesn't
# kill them before they complete (and persist to TaskStore).
_pending_dispatch_tasks: set = set()

#: ContextVar set by the graph's interrupt() gate after HITL approval.
#: This is the ONLY trusted source for the "already approved" flag —
#: the ``_hitl_approved`` key in LLM-supplied tool arguments is always
#: stripped and never honored (prevents prompt-injection bypass).
_hitl_approved_ctx: ContextVar[bool] = ContextVar("_hitl_approved", default=False)

#: ContextVar set by the supervisor graph's tool_worker_node whenever the
#: graph is compiled WITH a HITL config (i.e. the graph's interrupt() gate
#: is the authority for single-agent chat). When True, ``execute()`` skips
#: the SwarmMessageBus ``safety.check()`` (mechanism B) so a graph-gated
#: danger tool is not prompted twice — once by the graph interrupt and
#: again by the bus. The bus gate remains the authority for /swarm dispatch
#: and IDE paths (which do not set this ContextVar).
_graph_hitl_gate_ctx: ContextVar[bool] = ContextVar("_graph_hitl_gate", default=False)


def _record_procedural_outcome(tool_name: str, arguments: dict[str, Any], *, success: bool) -> None:
    """Feed a tool-execution outcome into the V2 procedural DAG memory.

    Best-effort fire-and-forget on a daemon thread — never blocks the
    tool result or raises into the tool path. Records the outcome so the
    procedural recorder can cluster recurring tool patterns and compute
    Laplace-smoothed confidence. No-op when the V2 schema is absent.
    """
    try:
        import threading

        def _run() -> None:
            try:
                import sqlite3

                from kazma_core.memory.config import read_memory_cfg
                from kazma_core.memory.procedural import record_procedural_outcome
                from kazma_core.memory.schema_v2 import ensure_primary_schema
                from kazma_core.paths import primary_memory_db

                conn = sqlite3.connect(
                    primary_memory_db(), check_same_thread=False, isolation_level=None
                )
                try:
                    ensure_primary_schema(conn)
                    # Arguments are the preconditions; the tool name + args
                    # form the DAG signature. Postcondition is success bool.
                    record_procedural_outcome(
                        conn,
                        name=tool_name,
                        description=f"Tool: {tool_name}",
                        preconditions={"tool": tool_name, "args_keys": sorted(arguments.keys())},
                        dag_steps=[{"tool": tool_name, "args": arguments}],
                        postconditions={"succeeded": success},
                        success=success,
                        cfg=read_memory_cfg(),
                    )
                finally:
                    conn.close()
            except Exception:
                logger.debug("[ToolRegistry] procedural record failed", exc_info=True)

        threading.Thread(target=_run, daemon=True, name="kazma-procedural-record").start()
    except Exception:
        logger.debug("[ToolRegistry] could not spawn procedural-record thread", exc_info=True)


def _is_under_agent_skill_dir(resolved_p: Path) -> bool:
    """True if path is inside a known Agent Skills install/scan directory.

    Allows progressive disclosure (tier 3) — agents can ``file_read``
    scripts/references/assets under installed skills without opening the
    whole filesystem. Write/delete ops must still reject these paths.
    """
    try:
        from kazma_core.agent_skills.discovery import skill_base_dirs

        for _scope, base in skill_base_dirs():
            if not base.is_dir():
                continue
            try:
                resolved_p.relative_to(base.resolve())
                return True
            except ValueError:
                continue
    except Exception:
        pass
    return False


def _workspace_scope_error(p: Path, path: str, op: str) -> str | None:
    """Return a safety error string if *p* is outside the workspace.

    Returns ``None`` when the path is allowed.  Denies by default when
    the workspace module cannot be imported (fail-closed) so a broken
    install never silently opens the whole filesystem.

    Read-like ops (``reads``, ``listings``, ``searches``) may also access
    Agent Skills directories so skill resources load on demand.
    
    O2 fix: Removed temp directory fallbacks (/tmp, system temp) that
    weakened path isolation. All paths must be under the workspace root
    (or skill directories for reads) — no exceptions.
    """
    try:
        from kazma_core.tools.file_write import _get_workspace
        from kazma_core.workspace.binding import allow_absolute_paths
    except (ImportError, OSError):
        return f"Safety: workspace module unavailable — {op} denied. Path: {path}"

    workspace = _get_workspace().resolve()
    resolved_p = p.expanduser().resolve()
    if not allow_absolute_paths():
        try:
            resolved_p.relative_to(workspace)
        except ValueError:
            # Allow skill resource reads outside workspace
            if op in ("reads", "listings", "searches") and _is_under_agent_skill_dir(
                resolved_p
            ):
                return None
            return f"Safety: {op} outside workspace are not allowed. Path: {path}"
    return None

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
    except Exception as _e:
        logger.debug("get_type_hints failed for %s: %s", getattr(func, "__name__", func), _e)
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
        # Strip the private HITL flag from LLM-supplied arguments — it is
        # not a real argument and must not leak into the tool call. We NEVER
        # trust this flag from the arguments dict (prompt-injection risk);
        # only the ContextVar set by graph_builder is honored.
        arguments.pop("_hitl_approved", None)
        _hitl_already_approved = _hitl_approved_ctx.get()
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
        except Exception as exc:
            logger.debug("[ToolRegistry] Failed to load retry config, using default limits: %s", exc)
            max_attempts = 1  # No retry if config unavailable
            min_wait = 2
            max_wait = 10

        # ── Safety check — gate danger-tier tools (HITL) ───────────
        # Use the async check() so a real bus adapter can post an approval
        # request and await the operator's response. check_sync() only
        # blocks; it can never approve. Skip when the graph's interrupt()
        # gate already approved this call, OR when the supervisor graph
        # itself is the HITL authority (single-agent chat) — in that case
        # the graph's interrupt() is the only gate and a second bus prompt
        # would deadlock the turn (the bus waits for an approval the user
        # already gave to the graph).
        if not _hitl_already_approved and not _graph_hitl_gate_ctx.get():
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
            except Exception as exc:
                # Safety unavailable — fail closed (do not execute danger tools).
                logger.warning("[ToolRegistry] Safety check failed or unavailable for %s: %s", tool_name, exc)
                return {
                    "content": f"Tool '{tool_name}' blocked — SafetyMiddleware unavailable.",
                    "is_error": True,
                }

        start = time.monotonic()
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                # Filter arguments to only those the function accepts. The LLM
                # sometimes injects extra keys (e.g. "raw") that aren't in the
                # function signature, causing TypeError on **kwargs splat.
                import inspect as _inspect
                try:
                    sig = _inspect.signature(tool.func)
                    valid_params = {
                        k: v for k, v in arguments.items()
                        if k in sig.parameters
                    }
                    if len(valid_params) < len(arguments):
                        dropped = set(arguments) - set(valid_params)
                        logger.debug(
                            "Tool '%s': dropped unexpected args: %s",
                            tool_name, dropped,
                        )
                except (ValueError, TypeError):
                    valid_params = arguments

                if tool.is_async:
                    result = await tool.func(**valid_params)
                else:
                    # Run sync functions in a thread pool
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, lambda: tool.func(**valid_params))

                duration_ms = (time.monotonic() - start) * 1000
                logger.info("Tool '%s' executed in %.0fms", tool_name, duration_ms)

                # Normalize result to string
                if isinstance(result, str):
                    content = result
                elif isinstance(result, dict | list):
                    content = json.dumps(result, ensure_ascii=False, indent=2)
                else:
                    content = str(result)

                _record_procedural_outcome(tool_name, arguments, success=True)
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
                _record_procedural_outcome(tool_name, arguments, success=False)
                return {"content": "Error: Tool execution failed. Check server logs for details.", "is_error": True}

        # All retry attempts exhausted
        duration_ms = (time.monotonic() - start) * 1000
        logger.error("Tool '%s' failed after %d attempts (%.0fms): %s", tool_name, max_attempts, duration_ms, last_exc)
        _record_procedural_outcome(tool_name, arguments, success=False)
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
            description=(
                "Read a file from the local filesystem. Returns line-numbered text. "
                "Supports line range slicing via start_line/end_line or offset/limit."
            ),
            category="filesystem",
        )
        async def file_read(
            path: str,
            offset: int = 0,
            limit: int = 2000,
            start_line: int | None = None,
            end_line: int | None = None,
            encoding: str = "utf-8",
        ) -> str:
            from kazma_core.tools.file_read import file_read as _fr_tool

            if start_line is not None:
                offset = start_line
            if end_line is not None:
                start = start_line if start_line is not None else offset
                limit = max(1, end_line - start + 1)
            return await _fr_tool(path, offset=offset, limit=limit)

        @self.register(
            description="Write content to a local file. Creates parent directories if needed. Overwrites existing content.",
            category="filesystem",
        )
        async def file_write(path: str, content: str, encoding: str = "utf-8") -> str:
            from kazma_core.tools.file_write import file_write as _fw_tool
            return await _fw_tool(path, content)

        @self.register(
            description=(
                "Delete a file or directory. Directories are removed recursively. "
                "Restricted to the workspace. Danger-tier (requires HITL approval)."
            ),
            category="filesystem",
        )
        async def file_delete(path: str) -> str:
            import shutil as _shutil

            p = Path(path).expanduser().resolve()
            scope_err = _workspace_scope_error(p, path, "deletions")
            if scope_err:
                return scope_err
            if not p.exists():
                return f"Error: Path not found: {path}"
            try:
                if p.is_dir():
                    _shutil.rmtree(p)
                else:
                    p.unlink()
                return f"Deleted: {path}"
            except Exception as exc:
                return f"Error deleting {path}: {exc}"

        @self.register(
            description="List files and directories at a path. Returns names sorted alphabetically.",
            category="filesystem",
        )
        async def file_list(path: str = ".", pattern: str = "*") -> str:
            p = Path(path).expanduser().resolve()
            # Workspace scoping — block listing outside workspace (fail-closed)
            scope_err = _workspace_scope_error(p, path, "listings")
            if scope_err:
                return scope_err
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
                    except Exception as exc:
                        logger.debug("[ToolRegistry] Failed to read %s in search: %s", file_path, exc)
                        continue

            return "\n".join(results) if results else f"No matches for '{pattern}' in {path}/{glob}"



        @self.register(
            description=(
                "Search long-term memory for relevant past conversations, facts, or preferences. "
                "Use this before answering questions that may require context from earlier sessions."
            ),
            category="memory",
        )
        async def memory_search(query: str, limit: int = 5) -> str:
            # V2 cognitive recall — the single memory read path (V1 removed).
            # Returns its results even when empty: an empty result is a real
            # "no memories match", not a signal to consult a legacy store.
            try:
                from kazma_core.memory.recall import recall as v2_recall
                from kazma_core.safety.hitl import get_current_tenant_id

                result = v2_recall(query, limit=limit, tenant_id=get_current_tenant_id())
                out: list[dict[str, Any]] = []
                for h in result.beliefs:
                    out.append({
                        "id": h.id, "content": h.content, "score": h.score,
                        "kind": "belief", "source": h.source, "metadata": h.metadata,
                    })
                for h in result.episodes:
                    out.append({
                        "id": h.id, "content": h.content, "score": h.score,
                        "kind": "episode", "source": h.source, "metadata": h.metadata,
                    })
                if out:
                    return json.dumps(out, ensure_ascii=False, indent=2)
                return "No relevant memories found."
            except Exception as exc:
                logger.warning("[memory_search] V2 recall failed: %s", exc)
                return "No relevant memories found."

        @self.register(
            description=(
                "Store a fact, preference, or conversation fragment in long-term memory. "
                "Use this when the user shares personal info, preferences, or important context "
                "that should be remembered across sessions."
            ),
            category="memory",
        )
        async def memory_store(text: str, metadata: str = "{}") -> str:
            try:
                meta = json.loads(metadata) if isinstance(metadata, str) else metadata
            except json.JSONDecodeError:
                meta = {"raw": metadata}
            # V2 native write — the single memory write path (V1 removed).
            # Stores as a V2 episode (raw text snapshot) + a user-explicit
            # belief (importance 5, trust 1.0 — the user explicitly asked to
            # remember it, so it's the highest-confidence source).
            import sqlite3

            from kazma_core.memory.belief_mutation import mutate_belief
            from kazma_core.memory.dual_write import mirror_episode
            from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
            from kazma_core.paths import memory_ops_db, primary_memory_db

            primary = sqlite3.connect(
                primary_memory_db(), check_same_thread=False, isolation_level=None
            )
            primary.row_factory = sqlite3.Row
            ops = sqlite3.connect(
                memory_ops_db(), check_same_thread=False, isolation_level=None
            )
            try:
                ensure_primary_schema(primary)
                ensure_ops_schema(ops)
                from kazma_core.safety.hitl import get_current_tenant_id

                _tenant = get_current_tenant_id()
                # Episode (raw text snapshot)
                eid = mirror_episode(
                    session_id=str(meta.get("session_id", "memory_store")),
                    turn_number=int(meta.get("turn", 0)),
                    user_text=text,
                    source="memory_store_tool",
                    tenant_id=_tenant,
                )
                # User-explicit belief (highest trust/importance)
                action = mutate_belief(
                    primary, "user", "noted", text[:1000],
                    ops_conn=ops,
                    predicate_type="set",  # noted facts are additive
                    confidence=1.0, importance=5,
                    extraction_method="user_explicit",
                    tenant_id=_tenant,
                    cfg=None,
                )
                bid = action.get("belief_id", "")
                if eid or bid:
                    return f"Stored memory (v2 episode={eid or 'n/a'}, belief={bid or 'n/a'})"
                return "Error: memory store failed — V2 write returned no ids."
            except Exception as exc:
                logger.warning("[memory_store] V2 write failed: %s", exc)
                return f"Error: memory store failed — {exc}"
            finally:
                primary.close()
                ops.close()

        @self.register(
            description=(
                "List Knowledge Libraries (documentation corpora) available for "
                "knowledge_search. Shows id, name, chunk_count, seed_url."
            ),
            category="knowledge",
        )
        async def knowledge_list_libraries() -> str:
            try:
                from kazma_core.stores.knowledge import get_knowledge_store

                libs = get_knowledge_store().list_libraries()
                if not libs:
                    return (
                        "No knowledge libraries yet. Create one with "
                        "knowledge_create_library, then knowledge_ingest_url."
                    )
                lines = [f"# Knowledge libraries ({len(libs)})"]
                for lib in libs:
                    lines.append(
                        f"- **{lib.get('id')}** — {lib.get('name') or '(unnamed)'} "
                        f"({lib.get('chunk_count', 0)} chunks)"
                        + (
                            f" seed={lib.get('seed_url')}"
                            if lib.get("seed_url")
                            else ""
                        )
                    )
                return "\n".join(lines)
            except Exception as exc:
                return f"Error: list libraries failed — {exc}"

        @self.register(
            description=(
                "Create a Knowledge Library (empty corpus) for documentation RAG. "
                "library_id should be a short slug (e.g. smoke_realwork_kb). "
                "Then call knowledge_ingest_url to add pages. Search with knowledge_search."
            ),
            category="knowledge",
        )
        async def knowledge_create_library(
            library_id: str,
            name: str = "",
            description: str = "",
            seed_url: str = "",
            exist_ok: bool = True,
        ) -> str:
            try:
                from kazma_core.stores.knowledge import get_knowledge_store

                store = get_knowledge_store()
                lib_id = (library_id or "").strip()
                if not lib_id:
                    return "Error: library_id is required."
                display = (name or "").strip() or lib_id.replace("_", " ").replace("-", " ")
                existing = store.get_library(lib_id)
                if existing:
                    if exist_ok:
                        return (
                            f"Knowledge library '{existing.get('id')}' already exists and is ready for use "
                            f"(name={existing.get('name')!r}, chunks={existing.get('chunk_count', 0)}). "
                            "Use knowledge_ingest_url to add pages."
                        )
                    return (
                        f"Error: Library already exists: id={existing.get('id')} "
                        f"name={existing.get('name')!r}."
                    )
                created = store.create_library(
                    lib_id,
                    display,
                    description=description or "",
                    seed_url=seed_url or "",
                )
                return (
                    f"Created knowledge library id={created.get('id')} "
                    f"name={created.get('name')!r}. "
                    "Next: knowledge_ingest_url(library_id, url)."
                )
            except Exception as exc:
                return f"Error: create library failed — {exc}"

        @self.register(
            description=(
                "Ingest a single documentation page URL into a Knowledge Library "
                "(fetch → chunk → index). Creates the library if missing. "
                "For multi-page trees prefer knowledge_ingest_site with a small max_pages. "
                "Then knowledge_search to retrieve. SSRF-safe (blocks private IPs)."
            ),
            category="knowledge",
        )
        async def knowledge_ingest_url(
            library_id: str,
            url: str,
            document_title: str = "",
            name: str = "",
        ) -> str:
            try:
                from kazma_core.stores.knowledge import get_knowledge_store
                from kazma_core.stores.knowledge_ingest import ingest_url

                store = get_knowledge_store()
                lib_id = (library_id or "").strip()
                page = (url or "").strip()
                if not lib_id or not page:
                    return "Error: library_id and url are required."
                if not store.get_library(lib_id):
                    display = (name or "").strip() or lib_id.replace("_", " ")
                    store.create_library(
                        lib_id, display, description="auto-created by knowledge_ingest_url", seed_url=page
                    )
                result = await ingest_url(
                    lib_id, page, document_title=(document_title or "").strip()
                )
                lib = store.get_library(lib_id) or {}
                if result.pages_failed and not result.chunks_new:
                    err = "; ".join(result.errors[:3]) if result.errors else "fetch failed"
                    return f"Error: ingest failed for {page!r} — {err}"
                return (
                    f"Ingested page into library '{lib_id}': "
                    f"fetched={result.pages_fetched} failed={result.pages_failed} "
                    f"chunks_new={result.chunks_new} chunks_skipped={result.chunks_skipped}. "
                    f"Library total chunks={lib.get('chunk_count', '?')}. "
                    f"Search with knowledge_search(query, library={lib_id!r})."
                )
            except Exception as exc:
                return f"Error: knowledge_ingest_url failed — {exc}"

        @self.register(
            description=(
                "Ingest a small documentation site tree into a Knowledge Library "
                "(sitemap/BFS discover + fetch + chunk + index). Caps max_pages "
                "(default 5, hard max 15) so agent turns stay bounded. "
                "Creates the library if missing. Prefer knowledge_ingest_url for one page."
            ),
            category="knowledge",
        )
        async def knowledge_ingest_site(
            library_id: str,
            seed_url: str,
            max_pages: int = 5,
            name: str = "",
        ) -> str:
            try:
                from kazma_core.stores.knowledge import get_knowledge_store
                from kazma_core.stores.knowledge_ingest import ingest_site

                store = get_knowledge_store()
                lib_id = (library_id or "").strip()
                seed = (seed_url or "").strip()
                if not lib_id or not seed:
                    return "Error: library_id and seed_url are required."
                cap = max(1, min(int(max_pages or 5), 15))
                if not store.get_library(lib_id):
                    display = (name or "").strip() or lib_id.replace("_", " ")
                    store.create_library(
                        lib_id,
                        display,
                        description="auto-created by knowledge_ingest_site",
                        seed_url=seed,
                    )
                final_msg = ""
                last = None
                async for upd in ingest_site(lib_id, seed, max_pages=cap):
                    last = upd
                    final_msg = getattr(upd, "message", "") or final_msg
                lib = store.get_library(lib_id) or {}
                discovered = getattr(last, "discovered", 0) if last else 0
                fetched = getattr(last, "fetched", 0) if last else 0
                failed = getattr(last, "failed", 0) if last else 0
                chunks_new = getattr(last, "ingested", 0) if last else 0
                return (
                    f"Site ingest finished for library '{lib_id}' (max_pages={cap}). "
                    f"discovered={discovered} fetched={fetched} failed={failed} "
                    f"chunks_indexed≈{chunks_new}. "
                    f"Library total chunks={lib.get('chunk_count', '?')}. "
                    f"Last: {final_msg or 'done'}. "
                    f"Search with knowledge_search(query, library={lib_id!r})."
                )
            except Exception as exc:
                return f"Error: knowledge_ingest_site failed — {exc}"

        @self.register(
            description=(
                "Search an ingested Knowledge Library (documentation corpus) for "
                "technical reference material — API endpoints, parameters, error codes, "
                "configuration, examples. Use this when the user asks about a documented "
                "system (e.g. the WhatsApp Cloud API) and you need authoritative info with "
                "sources. Each hit includes the source URL and section so you can cite it. "
                "Leave `library` empty to search across all libraries. "
                "If none exist, create with knowledge_create_library + knowledge_ingest_url."
            ),
            category="knowledge",
        )
        async def knowledge_search(query: str, library: str = "", top_k: int = 5) -> str:
            # Knowledge Libraries are a managed RAG corpus, decoupled from
            # chat memory.  See `kazma_core/stores/knowledge_index.py`.
            try:
                from kazma_core.stores.knowledge import get_knowledge_store
                from kazma_core.stores.knowledge_index import get_knowledge_index

                store = get_knowledge_store()
                index = get_knowledge_index()

                # Pick target library/libraries.
                lib_id = (library or "").strip()
                if lib_id:
                    if not store.get_library(lib_id):
                        return (
                            f"Error: knowledge library '{lib_id}' not found. "
                            "Create it with knowledge_create_library or "
                            "knowledge_ingest_url (auto-creates)."
                        )
                    hits = await index.search(query, lib_id, top_k=top_k)
                else:
                    libs = store.list_libraries()
                    if not libs:
                        return (
                            "No knowledge libraries have been ingested yet. "
                            "Create one: knowledge_create_library(id, name), then "
                            "knowledge_ingest_url(id, url). Or use the /knowledge UI / /kb add."
                        )
                    # True cross-library RRF: pool raw per-layer results from
                    # every library into one fused ranking (not flatten+sort,
                    # which would double-count RRF contributions).
                    hits = await index.search_all(
                        query, [l["id"] for l in libs], top_k=top_k,
                    )

                if not hits:
                    scope = f"library '{lib_id}'" if lib_id else "any library"
                    return f"No knowledge hits in {scope} for: {query!r}"

                lines = [f"# Knowledge search — {len(hits)} hit(s)"]
                for i, h in enumerate(hits, start=1):
                    cite = f"{h.source_url}"
                    if h.section_header:
                        cite += f" — {h.section_header}"
                    lines.append(f"\n## [{i}] score={h.score:.4f} — {cite}")
                    if h.document_title:
                        lines.append(f"*(page: {h.document_title})*")
                    lines.append(h.content)
                # Citation directive: the user wants every KB-derived answer
                # to carry a visible footer naming the source library, so they
                # can tell where the information came from. Per-item libraries
                # vary when searching across libraries; collect the unique set.
                cited_libs = sorted({h.library_id for h in hits})
                if len(cited_libs) == 1:
                    lib_footer = f'📚 This data is from Knowledge "{cited_libs[0]}".'
                else:
                    lib_footer = (
                        "📚 This data is from Knowledge libraries: "
                        + ", ".join(f'"{l}"' for l in cited_libs) + "."
                    )
                lines.append(
                    "\n---\n"
                    + lib_footer + "\n"
                    "You MUST include this footer verbatim at the end of any answer "
                    "that uses the material above."
                )
                return "\n".join(lines)
            except Exception as exc:
                return f"Error: knowledge search failed — {exc}"

        @self.register(
            description="Get the current date, time, and timezone in ISO-8601 format.",
            category="utility",
        )
        async def current_datetime() -> str:
            from datetime import datetime

            now = datetime.now(UTC)
            return now.isoformat()

        @self.register(
            description=(
                "Save a configuration setting to the persistent settings store. "
                "Use this when the user asks to save, update, or configure a setting "
                "(e.g. Telegram allowed users, Discord tokens, model preferences). "
                "Common keys: connectors.telegram.allowed_users (comma-separated user IDs), "
                "connectors.discord.allowed_users, agent.personality, agent.language. "
                "This tool requires user approval before applying changes."
            ),
            category="system",
        )
        async def config_save(key: str, value: str) -> str:
            from kazma_core.config_store import get_config_store, is_sensitive_config_key

            # Block security-critical + any secret-class keys (audit H8)
            _BLOCKED_PREFIXES = (
                "security.",
                "kazma_secret",
                "vault.",
                "yolo.",
            )
            if any(key.startswith(p) or key == p.rstrip(".") for p in _BLOCKED_PREFIXES):
                return f"Error: Cannot modify restricted key '{key}'."
            if is_sensitive_config_key(key):
                return (
                    f"Error: '{key}' is a sensitive credential. "
                    "Change it in Settings UI — not via tools."
                )

            store = get_config_store()
            store.set(
                key,
                value,
                category="connectors" if key.startswith("connectors.") else "general",
            )
            logger.info("[config_save] Saved setting: %s", key)
            # Never echo secret-like values back into chat
            return f"Setting saved: {key}"

        @self.register(
            description=(
                "Read a configuration setting from the persistent settings store. "
                "Returns a structured status so you can tell missing vs unset vs set: "
                "status=missing (key never stored), unset (key present but empty), "
                "set (has a value), or secret (value exists but is hidden). "
                "Use for allowed users, agent.personality, agent.max_iterations, etc."
            ),
            category="system",
        )
        async def config_read(key: str) -> str:
            import json as _json

            from kazma_core.config_store import get_config_store

            if not key or not str(key).strip():
                return _json.dumps(
                    {
                        "key": key or "",
                        "status": "error",
                        "value": None,
                        "message": "No key provided.",
                    },
                    ensure_ascii=False,
                )

            store = get_config_store()
            _MISSING = object()
            val = store.get(key, _MISSING)

            # Key alias fallback resolution (e.g. agent.model -> registry.active_model)
            if val is _MISSING:
                key_clean = (key or "").strip().lower()
                aliases: dict[str, list[str]] = {
                    "agent.model": ["registry.active_model", "registry.active_chat_model", "models.default", "llm.model"],
                    "model": ["registry.active_model", "registry.active_chat_model", "models.default", "llm.model"],
                    "active_model": ["registry.active_model", "registry.active_chat_model", "models.default"],
                    "agent.active_model": ["registry.active_model", "registry.active_chat_model", "models.default"],
                    "agent.provider": ["registry.active_provider", "llm.provider"],
                    "provider": ["registry.active_provider", "llm.provider"],
                    "active_provider": ["registry.active_provider", "llm.provider"],
                }
                for alt_key in aliases.get(key_clean, []):
                    alt_val = store.get(alt_key, _MISSING)
                    if alt_val is not _MISSING and alt_val is not None:
                        val = alt_val
                        break

            key_l = (key or "").lower()
            secret_markers = (
                "api_key", "apikey", "token", "secret", "password",
                "passwd", "private_key", "credentials", "auth",
            )
            is_secret_key = any(m in key_l for m in secret_markers)

            if val is _MISSING:
                payload = {
                    "key": key,
                    "status": "missing",
                    "value": None,
                    "message": (
                        f"Key '{key}' is not stored (not in ConfigStore/YAML). "
                        "It may still have a code default when the app reads it."
                    ),
                }
            elif val is None or val == "" or val == [] or val == {}:
                payload = {
                    "key": key,
                    "status": "unset",
                    "value": None,
                    "message": f"Key '{key}' exists but has an empty value.",
                }
            elif is_secret_key:
                payload = {
                    "key": key,
                    "status": "secret",
                    "value": None,
                    "message": (
                        f"Key '{key}' is set (value hidden — secrets are not "
                        "readable via tools). Change in Settings UI if needed."
                    ),
                }
            else:
                # Coerce non-string values for display
                display = val if isinstance(val, (str, int, float, bool)) else str(val)
                payload = {
                    "key": key,
                    "status": "set",
                    "value": display,
                    "message": f"{key} is set.",
                }
                if key == "agent.personality":
                    try:
                        from kazma_core.personalities import load_personality
                        profile = load_personality(config={})
                        p_name = profile.get("name") or display
                        p_desc = profile.get("description") or ""
                        p_prompt = profile.get("system_prompt") or ""
                        payload["description"] = f"{p_name}: {p_desc}".strip(": ")
                        payload["prompt_preview"] = p_prompt[:300] + ("..." if len(p_prompt) > 300 else "")
                        payload["message"] = f"agent.personality is set to '{display}' ({p_desc})."
                    except Exception:
                        pass
            return _json.dumps(payload, ensure_ascii=False)

        @self.register(
            description=(
                "Execute a shell command (allowlisted binaries only) and return "
                "stdout+stderr. Prefer native tools first: file_list/file_read/"
                "file_search/file_write, git_status/git_*, python_exec/code_exec, "
                "install_agent_skill. Do NOT use shell for: cd (not allowed — "
                "cwd is already the workspace), cat/ls (use file_*), git "
                "(use git tools), python/node/bash (use python_exec). "
                "Multi-step shell needs absolute paths under the workspace."
            ),
            category="system",
        )
        async def shell_exec(command: str, timeout: int = 30) -> str:
            import asyncio
            import shlex
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

            # Restricted PATH — only allow read-only / build-safe binaries.
            # NO interpreters (python/node/bash/sh) — those are RCE vectors
            # even after a single HITL approval. Use python_exec / code_exec
            # for code. Aligns with swarm ShellTool._READ_ONLY_COMMANDS.
            # NO network tools (curl, wget), NO container runtimes (docker).
            from kazma_core.safety.post_hitl import (
                production_archive_allowed,
                resolve_shell_binary,
                restricted_child_env,
                shell_mutate_allowed,
                shell_strict_mode,
            )

            _SAFE_BINARIES = {
                # Read-only system (no `env` — dumps secrets after one HITL)
                "ls", "cat", "head", "tail", "grep", "find", "wc", "sort",
                "uniq", "echo", "printf", "date", "whoami", "pwd",
                "df", "du", "free", "uptime", "uname", "hostname",
                # Build tools (no shell interpreters)
                "git", "uv", "pytest", "ruff", "mypy",
                # Text processing (read-only) — no `ps` (env leak on some OS)
                "jq", "tr", "cut",
                # Process control (safe)
                "sleep",
                # Note: `kazma` / `ps` removed from prod allowlist (audit H4)
            }
            # File ops: off in multi-user/prod unless KAZMA_SHELL_ALLOW_MUTATE=1
            if shell_mutate_allowed():
                _SAFE_BINARIES |= {"mkdir", "cp", "mv", "touch"}
            # Archives: disabled by default in production strict mode
            # (tar/zip can write outside cwd via absolute entries).
            if production_archive_allowed():
                _SAFE_BINARIES |= {
                    "tar", "gzip", "gunzip", "zip", "unzip",
                }
            # Dev-only extras when not in production
            import os as _os_bin
            if (_os_bin.environ.get("KAZMA_PRODUCTION") or "").lower() not in (
                "1", "true", "on", "yes",
            ):
                _SAFE_BINARIES = set(_SAFE_BINARIES) | {"ps", "pgrep", "kazma"}
            import os

            p = Path(args[0])
            binary = p.name
            if os.name == "nt" and p.suffix.lower() == ".exe":
                binary = p.stem

            if binary not in _SAFE_BINARIES:
                # Also check if it's an absolute/relative path to a safe binary
                posix_path = p.as_posix()
                if not any(
                    posix_path.endswith(f"/{b}") or (os.name == "nt" and posix_path.endswith(f"/{b}.exe"))
                    for b in _SAFE_BINARIES
                ):
                    hint = ""
                    low = command.lower()
                    if binary in ("node", "npm", "npx") or "skills add" in low or "agent-skills" in low:
                        hint = (
                            "\n\nTo install an Agent Skill (agentskills.io / SKILL.md), "
                            "use install_agent_skill(source='owner/repo') instead — "
                            "e.g. install_agent_skill(source='shadcn/improve'). "
                            "Node/npm/npx are intentionally blocked; skill install "
                            "does not need them."
                        )
                    elif binary in ("cd", "pushd", "popd", "chdir"):
                        hint = (
                            "\n\n`cd` is not allowed (shell builtins). "
                            "Commands already run with cwd = active workspace. "
                            "Use absolute paths under the workspace, or native "
                            "file_*/git_*/python_exec tools instead of multi-step shell."
                        )
                    elif binary in ("cat", "less", "more", "head", "tail") and "file_read" not in low:
                        hint = (
                            "\n\nPrefer file_read / file_list / file_search for workspace files."
                        )
                    elif binary == "git":
                        hint = (
                            "\n\nPrefer native git tools (git_status, etc.) when available."
                        )
                    elif binary in ("python", "python3", "node", "bash", "sh", "zsh"):
                        hint = (
                            "\n\nInterpreters are blocked in shell_exec. Use python_exec "
                            "or code_exec for short scripts."
                        )
                    return (
                        f"Error: '{binary}' is not in the allowed binary list. "
                        f"Allowed: {', '.join(sorted(_SAFE_BINARIES))}"
                        f"{hint}"
                    )

            try:
                # Restrict context strictly to active workspace
                from kazma_core.tools.file_write import _get_workspace
                cwd = _get_workspace()
                cwd_s = str(cwd)

                # Resolve binary under restricted PATH (post-HITL hardening)
                child_env = restricted_child_env(cwd=cwd_s)
                if shell_strict_mode():
                    resolved = resolve_shell_binary(
                        args[0], restricted_path=child_env.get("PATH", "")
                    )
                    if not resolved:
                        return (
                            f"Error: could not resolve '{args[0]}' under restricted PATH. "
                            "Post-HITL shell only runs system/build tools on the "
                            "allowlist (set KAZMA_SHELL_STRICT=0 to relax in lab)."
                        )
                    args = [resolved, *args[1:]]

                # Reject absolute paths outside workspace (audit H4)
                for a in args[1:]:
                    if not a or a.startswith("-"):
                        continue
                    # Rough path detection
                    looks_path = (
                        a.startswith("/")
                        or a.startswith("\\")
                        or (len(a) > 2 and a[1] == ":" and a[0].isalpha())
                        or ".." in a.replace("\\", "/")
                    )
                    if not looks_path:
                        continue
                    try:
                        import os as _os

                        cand = _os.path.realpath(
                            a if _os.path.isabs(a) else _os.path.join(cwd_s, a)
                        )
                        root_n = _os.path.realpath(cwd_s)
                        if _os.name == "nt":
                            cand, root_n = cand.lower(), root_n.lower()
                        if cand != root_n and not cand.startswith(root_n + _os.sep):
                            return (
                                f"Error: path '{a}' is outside the workspace "
                                f"({cwd_s}). Absolute paths must stay inside the workspace."
                            )
                    except Exception:
                        pass

                # git subcommand denylist (destructive / credential / rewrite)
                if binary == "git" and len(args) > 1:
                    sub = args[1].lstrip("-")
                    blocked_git = {
                        "push", "credential", "credential-manager",
                        "credential-store", "credential-cache",
                        "reset", "rebase", "filter-branch", "filter-repo",
                        "remote", "submodule",
                    }
                    joined = " ".join(args[1:]).lower()
                    if (
                        sub in blocked_git
                        or "config --global" in joined
                        or "clean -fd" in joined
                        or " push " in f" {joined} "
                        or "--force" in joined
                        or " -f " in f" {joined} "
                    ):
                        return (
                            f"Error: git subcommand/args not allowed: {' '.join(args[1:])}. "
                            "push/force/credential/reset/rebase/global config/clean -fd are blocked."
                        )

                # Check if command uses shell pipelines or metacharacters (| && ; > <)
                has_pipe = any(op in command for op in ("|", "&&", ";", ">", "<"))

                if has_pipe:
                    proc = await asyncio.create_subprocess_shell(
                        command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=cwd,
                        env=child_env,
                    )
                else:
                    proc = await asyncio.create_subprocess_exec(
                        args[0],
                        *args[1:],
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=cwd,
                        env=child_env,
                    )
                
                # Bounded stream reader to cap memory allocations for large outputs
                async def _read_stream_capped(stream: asyncio.StreamReader | None, limit: int) -> bytes:
                    if stream is None:
                        return b""
                    buf = bytearray()
                    while len(buf) < limit:
                        chunk = await stream.read(min(4096, limit - len(buf)))
                        if not chunk:
                            break
                        buf.extend(chunk)
                    return bytes(buf)

                try:
                    async def _communicate_capped():
                        so_task = asyncio.create_task(_read_stream_capped(proc.stdout, 20_000))
                        se_task = asyncio.create_task(_read_stream_capped(proc.stderr, 10_000))
                        so, se = await asyncio.gather(so_task, se_task)
                        await proc.wait()
                        return so, se

                    stdout, stderr = await asyncio.wait_for(_communicate_capped(), timeout=timeout)
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                        await proc.wait()
                    except ProcessLookupError:
                        pass
                    finally:
                        if proc.stdout:
                            proc.stdout.close()
                        if proc.stderr:
                            proc.stderr.close()
                    return f"Error: Command timed out after {timeout}s"

                output = stdout.decode("utf-8", errors="replace")
                err_output = stderr.decode("utf-8", errors="replace")
                if err_output:
                    output += f"\n[stderr]\n{err_output}"
                if proc.returncode != 0:
                    output += f"\n[exit code: {proc.returncode}]"
                return output[:10_000]  # cap output
            except FileNotFoundError:
                return f"Error: Command not found: {args[0]}"
            except Exception as exc:
                return "Error: Shell command execution failed."



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

        # ── Swarm dispatch (visible in /swarm panel) ──────────────
        @self.register(
            description=(
                "Dispatch a research or analysis task to the Swarm engine. "
                "The task appears in the Swarm panel (/swarm) with full worker "
                "progress, results, cost, and traceability. Returns a task ID "
                "immediately — use check_swarm_task to retrieve the result when "
                "ready. Use this instead of spawn_agent when you want the work "
                "to be visible and traceable in the panel."
            ),
            category="swarm",
        )
        async def dispatch_swarm(
            prompt: str,
            worker: str = "auto",
            context: str = "",
        ) -> str:
            import asyncio as _asyncio

            from kazma_core.swarm import SwarmTask, TaskType, get_swarm_engine

            engine = get_swarm_engine()
            if engine is None:
                return (
                    "Error: Swarm engine not initialized. "
                    "Configure swarm workers in kazma.yaml."
                )

            # Auto-register a default "researcher" worker if none exist, so
            # dispatch_swarm works out of the box without manual setup.
            if not engine.worker_names:
                try:
                    from kazma_core.swarm.config import WorkerConfig, WorkerCapabilities
                    from kazma_core.model_registry import get_model_registry

                    reg = get_model_registry()
                    profile = reg.get_active_profile()
                    engine.add_worker(WorkerConfig(
                        name="researcher",
                        type="in_process",
                        model=profile.get("model", ""),
                        provider=profile.get("provider", ""),
                        role="researcher",
                        system_prompt=(
                            "You are a Researcher worker. Follow the research protocol: "
                            "≥2 search queries, ≥2 full sources via read_url_to_file, "
                            "digest long pages, then structured findings with URL citations. "
                            "For comprehensive papers use run_research_pipeline. "
                            "Never conclude from search snippets alone."
                        ),
                        capabilities=WorkerCapabilities(
                            role="researcher",
                            expertise=["research", "analysis", "writing"],
                            tools=[
                                "web_search",
                                "read_url",
                                "read_url_to_file",
                                "crawl_site",
                                "list_research_chunks",
                                "read_research_chunk",
                                "summarize_research_file",
                                "digest_research_file",
                                "synthesize_from_digests",
                                "run_research_pipeline",
                                "file_write",
                            ],
                        ),
                    ))
                    logger.info("[dispatch_swarm] Auto-registered 'researcher' worker")
                except Exception as exc:
                    return (
                        f"Error: No swarm workers registered and could not "
                        f"auto-create one: {exc}. Add workers in the Swarm "
                        f"panel or kazma.yaml."
                    )

            # Resolve "auto" to the first available worker.
            if worker == "auto":
                worker = engine.worker_names[0] if engine.worker_names else "researcher"

            task = SwarmTask(
                prompt=prompt,
                workers=[worker],
                type=TaskType.DISPATCH,
                context=context,
                timeout=300.0,
                metadata={"source": "chat", "kind": "research"},
            )
            # Dispatch in the background so the tool returns immediately.
            # Register on engine._task_handles so cancel_task / panel Stop
            # can cancel the live asyncio work (not just mark maps cancelled).
            _bg_task = _asyncio.create_task(engine.dispatch(task))
            _pending_dispatch_tasks.add(_bg_task)
            try:
                engine.register_task_handle(task.id, _bg_task)
            except Exception as reg_exc:
                logger.debug(
                    "[dispatch_swarm] register_task_handle failed: %s", reg_exc
                )

            def _cleanup_handle(
                h: Any, tid: str = task.id, eng: Any = engine
            ) -> None:
                _pending_dispatch_tasks.discard(h)
                try:
                    if eng is not None and hasattr(eng, "unregister_task_handle"):
                        eng.unregister_task_handle(tid)
                except Exception:
                    pass

            _bg_task.add_done_callback(_cleanup_handle)
            return (
                f"Swarm task dispatched to worker '{worker}' "
                f"(id: {task.id}). It's visible in the Swarm panel. "
                f"Use check_swarm_task('{task.id}') to get the result."
            )

        @self.register(
            description=(
                "Check the status and result of a dispatched Swarm task. "
                "Returns the full result when the task is complete, or a "
                "status message if still running. Poll this every few seconds "
                "until you get a completed result."
            ),
            category="swarm",
        )
        async def check_swarm_task(task_id: str) -> str:
            from kazma_core.swarm import get_swarm_engine

            engine = get_swarm_engine()
            # Check in-memory active tasks first, then TaskStore (persisted).
            task = None
            if engine:
                task = engine.get_active_task(task_id)
            if task is None and engine and getattr(engine, "_task_store", None):
                task = engine._task_store.get_task(task_id)
            if task is None:
                return f"Task {task_id} not found."

            status_str = str(task.status).lower().replace("taskstatus.", "")
            if status_str in ("running", "pending"):
                elapsed_str = ""
                started_iso = getattr(task, "started_at", None)
                if started_iso:
                    try:
                        from datetime import datetime, timezone
                        dt = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
                        elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
                        tot_timeout = getattr(task, "timeout", None) or 300.0
                        elapsed_str = f" (elapsed: {elapsed:.1f}s, timeout: {tot_timeout:.0f}s)"
                    except Exception:
                        pass
                workers_str = f", worker: {task.workers[0]}" if task.workers else ""
                return (
                    f"Task {task_id} is still {status_str}{elapsed_str}{workers_str}. "
                    f"Check again in a moment."
                )

            result = task.result
            if result and result.error:
                return f"Task {task_id} failed: {result.error}"
            if result:
                output = (
                    result.aggregated_output
                    or result.synthesized_output
                    or ""
                )
                if not output and result.worker_results:
                    output = result.worker_results[0].output
                cost = getattr(result, "total_cost", 0.0)
                duration = getattr(result, "duration_seconds", 0.0)
                return (
                    f"Task {task_id} completed.\n"
                    f"Cost: ${cost:.4f}\n"
                    f"Duration: {duration:.1f}s\n\n"
                    f"{output}"
                )
            return f"Task {task_id} status: {status_str} (no result yet)."


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
            self.register_function(
                "web_search",
                web_search,
                description=(
                    "Search the public web (SearXNG if configured, else DuckDuckGo, "
                    "Bing HTML last). Returns markdown titles/URLs/**snippets only**. "
                    "For thorough research: run ≥2 queries, then fetch full pages with "
                    "read_url_to_file / read_url — do not answer from snippets alone. "
                    "Prefer KAZMA_SEARXNG_URL. Args: query, max_results=8."
                ),
                category="search",
            )
        except ImportError:
            logger.debug("web_search not available (missing duckduckgo-search)")

        try:
            from kazma_core.tools.read_url import (
                digest_research_file,
                list_research_chunks,
                read_research_chunk,
                read_url,
                read_url_to_file,
                summarize_research_file,
            )

            self.register_function(
                "read_url",
                read_url,
                description=(
                    "Fetch one public URL; text window (default ~16k, KAZMA_READ_URL_MAX_CHARS). "
                    "Args: url, offset=0, max_chars=None. Hard sites: Firecrawl/Jina recovery. "
                    "For research: prefer read_url_to_file then digest_research_file; multi-page: crawl_site."
                ),
                category="search",
            )
            self.register_function(
                "read_url_to_file",
                read_url_to_file,
                description=(
                    "Fetch URL and save FULL extract under the workspace "
                    "(default research/). Preferred for multi-source research so you can "
                    "digest later. Args: url, path=workspace-relative."
                ),
                category="search",
            )
            self.register_function(
                "list_research_chunks",
                list_research_chunks,
                description=(
                    "List chunk indices and previews for a saved research file. "
                    "Args: path, chunk_size=4000."
                ),
                category="search",
            )
            self.register_function(
                "read_research_chunk",
                read_research_chunk,
                description=(
                    "Read one chunk of a saved research file. "
                    "Args: path, chunk_index=0, chunk_size=4000."
                ),
                category="search",
            )
            self.register_function(
                "summarize_research_file",
                summarize_research_file,
                description=(
                    "Light extractive outline (per-chunk previews). "
                    "Args: path, chunk_size=4000, max_chunks=40."
                ),
                category="search",
            )
            self.register_function(
                "digest_research_file",
                digest_research_file,
                description=(
                    "Walk ALL chunks in-tool and return one bounded extractive digest "
                    "(default ~12k). Not LLM analysis — use synthesize_from_digests for "
                    "cross-source analysis. Args: path, chunk_size=4000, max_output_chars=12000."
                ),
                category="search",
            )
        except ImportError:
            logger.debug("read_url / research tools not available (missing trafilatura)")

        try:
            from kazma_core.tools.research_synthesize import synthesize_from_digests

            self.register_function(
                "synthesize_from_digests",
                synthesize_from_digests,
                description=(
                    "LLM multi-source synthesis from saved research files/digests. "
                    "Args: paths (list or comma-separated), question, outline='', max_chars=20000. "
                    "Use after acquiring ≥2 sources via read_url_to_file."
                ),
                category="search",
            )
        except ImportError:
            logger.debug("synthesize_from_digests not available")

        try:
            from kazma_core.tools.research_pipeline import run_research_pipeline

            self.register_function(
                "run_research_pipeline",
                run_research_pipeline,
                description=(
                    "Deep research paper mode: multi-query search → parallel acquire → "
                    "digest → LLM synthesis → research/reports/.../report.md (+ optional DOCX). "
                    "Args: topic, depth='deep'|'standard', max_sources=8, language=''. "
                    "Use for comprehensive/thorough research or a full report."
                ),
                category="search",
            )
        except ImportError:
            logger.debug("run_research_pipeline not available")

        try:
            from kazma_core.tools.web_research import crawl_site

            self.register_function(
                "crawl_site",
                crawl_site,
                description=(
                    "Bounded multi-page crawl (same-domain by default). "
                    "Args: start_url, max_pages=8 (hard max 50; use 12–20 for deep docs), "
                    "max_depth=2, same_domain_only=True, delay_ms=300, save=True. "
                    "Saves pages under workspace; returns markdown index. SSRF-safe."
                ),
                category="search",
            )
        except ImportError:
            logger.debug("crawl_site not available")

        try:
            from kazma_core.tools.image_gen import generate_image
            self.register_function("generate_image", generate_image,
                description="Generate an image from a text prompt. provider can be 'auto' (first available), 'pollinations' (free, no key), 'dall-e' (OpenAI), 'stability' (SDXL), or 'flux' (FAL). Returns the saved file path.",
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

        # ── Agent Skills (agentskills.io / SKILL.md) ───────────────────
        try:
            from kazma_core.agent_skills.tools import (
                activate_skill,
                install_agent_skill,
                list_agent_skills,
                uninstall_agent_skill,
            )
            self.register_function(
                "list_agent_skills",
                list_agent_skills,
                description=(
                    "List installed Agent Skills (SKILL.md / agentskills.io format). "
                    "Shows name, description, and location for each skill."
                ),
                category="skills",
            )
            self.register_function(
                "activate_skill",
                activate_skill,
                description=(
                    "Load full instructions for an installed Agent Skill into context. "
                    "Call this when a task matches a skill's description before proceeding. "
                    "Pass the skill name from list_agent_skills / the available_skills catalog."
                ),
                category="skills",
            )
            self.register_function(
                "install_agent_skill",
                install_agent_skill,
                description=(
                    "Install an Agent Skill from GitHub or a local path. "
                    "Preferred over npx/npm (node is not in the shell allowlist). "
                    "Accepts owner/repo (e.g. 'shadcn/improve'), a GitHub URL, "
                    "or a local path with SKILL.md. One approval covers the whole install. "
                    "Hub: https://agentskills.io/"
                ),
                category="skills",
            )
            self.register_function(
                "uninstall_agent_skill",
                uninstall_agent_skill,
                description="Uninstall a user-level Agent Skill by name.",
                category="skills",
            )
        except Exception as e:
            logger.error("Failed to register agent skills tools: %s", e, exc_info=True)

        # Load and register Top 10 Native Skills
        try:
            from kazma_skills.native_loader import NativeSkillLoader
            loader = NativeSkillLoader(self)
            loader.register_all()
        except Exception as e:
            logger.error("Failed to load native skills: %s", e, exc_info=True)

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
