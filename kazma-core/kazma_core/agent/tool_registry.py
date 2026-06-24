"""Local Tool Registry — Lightweight tool registration with auto-schema generation.

This module provides a decorator-based system for registering Python
functions as agent tools.  Each tool is automatically introspected to
produce an OpenAI-compatible JSON schema that LiteLLM / Hermes can
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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any, get_type_hints

logger = logging.getLogger(__name__)


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

    # Optional[X] = Union[X, None]
    if origin is type:
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
          - Hermes tool schema format
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
            arguments: Tool arguments dict.

        Returns:
            Dict with ``content`` (str) and ``is_error`` (bool).
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            return {
                "content": f"Tool '{tool_name}' not found. Available: {list(self._tools.keys())}",
                "is_error": True,
            }

        start = time.monotonic()
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

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            logger.error("Tool '%s' failed after %.0fms: %s", tool_name, duration_ms, exc)
            return {"content": f"Error: {exc}", "is_error": True}

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
            if p.stat().st_size > 1_000_000:  # 1MB cap
                return f"Error: File too large ({p.stat().st_size} bytes). Max 1MB."
            return p.read_text(encoding=encoding)

        @self.register(
            description="Write content to a local file. Creates parent directories if needed. Overwrites existing content.",
            category="filesystem",
        )
        async def file_write(path: str, content: str, encoding: str = "utf-8") -> str:
            p = Path(path).expanduser().resolve()
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

            path = Path(db_path).expanduser().resolve()
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
            from kazma_core.memory.vector_store import VectorMemory

            mem = VectorMemory()
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
            from kazma_core.memory.vector_store import VectorMemory

            mem = VectorMemory()
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
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                proc.kill()
                return f"Error: Command timed out after {timeout}s"

            output = stdout.decode(errors="replace")
            if stderr:
                output += f"\n[stderr]\n{stderr.decode(errors='replace')}"
            return output[:10_000]  # cap output

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

        logger.info("Registered %d built-in tools", len(self._tools))


# ══════════════════════════════════════════════════════════════════════════
# Standalone decorator (convenience)
# ══════════════════════════════════════════════════════════════════════════

# Module-level registry for quick standalone use
_default_registry = LocalToolRegistry(include_builtins=False)


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
