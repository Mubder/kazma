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
import atexit
import inspect
import json
import logging
import queue
import sqlite3
import threading
import time
import types as _types
import typing as _typing
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from typing import Any, get_type_hints

__all__ = [
    "LocalTool",
    "LocalToolRegistry",
    "get_tool_registry",
    "reset_permission_manager_cache",
    "tool",
]

logger = logging.getLogger(__name__)

from kazma_core.agent.tool_schema import (
    _generate_schema,
    _python_type_to_json_schema,
    apply_openai_strict_tools,
    filter_tool_arguments,
    strict_tools_enabled,
)
from kazma_core.agent.tool_scope import _is_under_agent_skill_dir, _workspace_scope_error

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


# ── YAML permission allowlist (opt-in) ───────────────────────────────
_PERMISSION_MANAGER: Any = None
_PERMISSION_MANAGER_RESOLVED = False


class _FailClosedPermissions:
    """Used when enforce is on and the YAML cannot be loaded."""

    def is_allowed(self, tool_name: str, user: str = "default") -> bool:
        return False


def reset_permission_manager_cache() -> None:
    """Drop the process cache (tests / live YAML reload)."""
    global _PERMISSION_MANAGER, _PERMISSION_MANAGER_RESOLVED
    _PERMISSION_MANAGER = None
    _PERMISSION_MANAGER_RESOLVED = False


def _get_permission_manager() -> Any:
    """Return a PermissionManager when enforcement is active, else ``None``.

    Active when ``kazma-permissions.yaml`` has a ``users:`` map **or**
    ``KAZMA_PERMISSIONS_ENFORCE=1``. The shipped file is a divisions
    template with no ``users`` key — that stays off. Load errors fail
    closed only when enforcement was requested.
    """
    global _PERMISSION_MANAGER, _PERMISSION_MANAGER_RESOLVED
    if _PERMISSION_MANAGER_RESOLVED:
        return _PERMISSION_MANAGER
    _PERMISSION_MANAGER_RESOLVED = True
    try:
        from kazma_core.permissions import (
            PermissionManager,
            permissions_enforce_requested,
            should_enforce_permissions,
        )

        pm = PermissionManager()
        if should_enforce_permissions(pm):
            _PERMISSION_MANAGER = pm
            logger.info(
                "[ToolRegistry] permissions allowlist active for users: %s",
                pm.users(),
            )
        elif permissions_enforce_requested():
            _PERMISSION_MANAGER = _FailClosedPermissions()
            logger.warning(
                "[ToolRegistry] KAZMA_PERMISSIONS_ENFORCE=1 but YAML unusable "
                "— denying all tools"
            )
    except Exception as exc:
        from kazma_core.permissions import permissions_enforce_requested

        logger.debug("[ToolRegistry] permissions manager unavailable: %s", exc)
        if permissions_enforce_requested():
            _PERMISSION_MANAGER = _FailClosedPermissions()
            logger.warning(
                "[ToolRegistry] permissions load failed under enforce — deny all"
            )
    return _PERMISSION_MANAGER


# ── Procedural-outcome recorder: ONE worker, not a thread per tool call ──
#
# This used to spawn a fresh `daemon=True` thread on every tool execution,
# each opening its own SQLite connection, running `ensure_primary_schema`
# (full DDL + FTS5 rebuild probes) and writing a row. Under any burst of tool
# calls that is several threads writing the same two databases at once, and it
# crashed the interpreter:
#
#     Windows fatal exception: access violation
#
# The faulting traceback moved around — `_ensure_fts5`, `config_store.get`,
# `ensure_primary_schema` — which is why it read like several unrelated bugs.
# Measured on `tests/test_truncation_retry.py`: 4/10 runs crashed, and 0/10
# with these threads disabled, so the concurrency between them is the fault.
# Three narrower hypotheses were tested and are NOT the cause (each still
# crashed at ~the same rate): draining the threads at exit, giving ConfigStore
# a per-thread connection, and serialising `ensure_primary_schema`. Making the
# threads non-daemon did not help either, so it is not an interpreter-teardown
# race.
#
# Rather than keep hunting which shared object corrupts, remove the
# concurrency: a single worker thread drains a bounded queue, so exactly one
# thread ever touches these databases. That also fixes the unbounded
# thread-per-call spawn, which was its own problem.
_PROCEDURAL_QUEUE: queue.Queue[tuple[str, dict[str, Any], bool, Any] | None] = queue.Queue(
    maxsize=512
)
_PROCEDURAL_WORKER: threading.Thread | None = None
_PROCEDURAL_WORKER_LOCK = threading.Lock()
_PROCEDURAL_STOPPING = threading.Event()

#: How long to wait for the queue to drain at exit. These are single-row
#: writes; anything slower is a lock fight not worth blocking shutdown for.
_PROCEDURAL_DRAIN_SECONDS = 3.0


def _procedural_worker() -> None:
    """Drain queued tool outcomes on one connection. Never raises."""
    conn = None
    conn_path: str | None = None
    try:
        import sqlite3

        from kazma_core.memory.procedural import record_procedural_outcome
        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        while True:
            item = _PROCEDURAL_QUEUE.get()
            try:
                if item is None:  # shutdown sentinel
                    return
                tool_name, arguments, success, cfg = item

                # Resolve the target per item rather than once at startup.
                # The data directory is fixed in production but not under
                # test, where each case gets its own; binding the connection
                # at thread start would pin the first directory this worker
                # ever saw and silently write every later record to it.
                path = str(primary_memory_db())
                if conn is None or path != conn_path:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                    conn = sqlite3.connect(
                        path, check_same_thread=False, isolation_level=None
                    )
                    # Once per database, not once per tool call.
                    ensure_primary_schema(conn)
                    conn_path = path
                # Arguments are the preconditions; the tool name + args form
                # the DAG signature. Postcondition is the success bool.
                record_procedural_outcome(
                    conn,
                    name=tool_name,
                    description=f"Tool: {tool_name}",
                    preconditions={"tool": tool_name, "args_keys": sorted(arguments.keys())},
                    dag_steps=[{"tool": tool_name, "args": arguments}],
                    postconditions={"succeeded": success},
                    success=success,
                    cfg=cfg,
                )
            except Exception:
                logger.debug("[ToolRegistry] procedural record failed", exc_info=True)
            finally:
                _PROCEDURAL_QUEUE.task_done()
    except Exception:
        logger.debug("[ToolRegistry] procedural worker stopped", exc_info=True)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _stop_procedural_worker(timeout: float = _PROCEDURAL_DRAIN_SECONDS) -> None:
    """Drain the queue and stop the worker. Registered with :mod:`atexit`."""
    _PROCEDURAL_STOPPING.set()
    worker = _PROCEDURAL_WORKER
    if worker is None or not worker.is_alive():
        return
    try:
        _PROCEDURAL_QUEUE.put_nowait(None)
    except queue.Full:
        pass
    worker.join(timeout=timeout)


atexit.register(_stop_procedural_worker)


def _record_procedural_outcome(tool_name: str, arguments: dict[str, Any], *, success: bool) -> None:
    """Feed a tool-execution outcome into the V2 procedural DAG memory.

    Best-effort and non-blocking — never delays the tool result or raises into
    the tool path. Enqueues for the single worker above; drops the record when
    the queue is full rather than growing memory or blocking a tool call.
    """
    if _PROCEDURAL_STOPPING.is_set():
        return
    global _PROCEDURAL_WORKER
    try:
        if _PROCEDURAL_WORKER is None or not _PROCEDURAL_WORKER.is_alive():
            with _PROCEDURAL_WORKER_LOCK:
                if _PROCEDURAL_WORKER is None or not _PROCEDURAL_WORKER.is_alive():
                    _PROCEDURAL_WORKER = threading.Thread(
                        target=_procedural_worker,
                        daemon=True,
                        name="kazma-procedural-record",
                    )
                    _PROCEDURAL_WORKER.start()
        # Read config on the CALLER's thread, never in the worker. A test
        # harness resetting the ConfigStore singleton frees the sqlite handle
        # under any background reader; keeping the worker out of the store
        # entirely means a reset cannot race it at all (see
        # `reset_config_store`, whose default this also corrected).
        from kazma_core.memory.config import read_memory_cfg

        cfg = read_memory_cfg()
        try:
            _PROCEDURAL_QUEUE.put_nowait((tool_name, arguments, success, cfg))
        except queue.Full:
            logger.debug("[ToolRegistry] procedural queue full — outcome dropped")
    except Exception:
        logger.debug("[ToolRegistry] could not enqueue procedural outcome", exc_info=True)




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

        Generated parameter objects always have ``additionalProperties:
        false``. When ``KAZMA_STRICT_TOOLS=1``, qualifying tools also get
        ``function.strict: true`` (all properties required; optionals are
        ``T | null``). Tools with free-form ``dict`` parameters stay
        unstrict so local / Anthropic / Gemini endpoints do not 400.
        """
        definitions: list[dict[str, Any]] = []
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
        if strict_tools_enabled():
            apply_openai_strict_tools(definitions)
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

        # Closed schema: drop invented keys; JSON null on defaulted params
        # becomes "omitted" so Python defaults still apply (OpenAI strict).
        if isinstance(arguments, dict):
            arguments = filter_tool_arguments(arguments, tool.input_schema)

        # PreToolUse hooks (deny / rewrite). NOT a permission system —
        # rewritten args still go through commitment + HITL. Hooks cannot
        # auto-approve. Fail-open if a hook errors.
        try:
            from kazma_core.agent.tool_hooks import apply_pre_tool_hooks

            _denied, arguments = await apply_pre_tool_hooks(tool_name, arguments)
            if _denied is not None:
                return _denied
            if isinstance(arguments, dict):
                arguments = filter_tool_arguments(arguments, tool.input_schema)
        except Exception:
            logger.debug("[ToolRegistry] pre-tool hook failed", exc_info=True)

        # Commitment gate on the IDE/swarm path. Live-reads
        # enforce_unknown_mutators (default on). Deny is honored. Exceptions
        # fail-closed when enforcement is on so a broken gate cannot free-fire.
        try:
            from kazma_core.safety.commitment import authorize_effect as _authorize
            from kazma_core.safety.commitment.config import get_commitment_config

            _enforce = bool(get_commitment_config().get("enforce_unknown_mutators"))
            _dec = _authorize(
                tool_name, arguments, enforce_unknown_mutators=_enforce,
                context={"source": "registry"},
            )
            if _dec.decision == "deny":
                return {"content": f"[commitment] blocked: {_dec.reason}", "is_error": True}
        except Exception:
            logger.debug("[ToolRegistry] authorize_effect choke failed", exc_info=True)
            try:
                from kazma_core.safety.commitment.config import get_commitment_config

                if bool(get_commitment_config().get("enforce_unknown_mutators")):
                    return {
                        "content": "[commitment] blocked: authorization failed closed",
                        "is_error": True,
                    }
            except Exception:
                pass

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
            # Clamp to >= 1: range(1, max_attempts + 1) is empty for 0/neg,
            # so the tool function is never invoked and the method returns a
            # nonsensical "Error: None" for every call (audit finding).
            max_attempts = max(1, int(cfg["max_attempts"]))
            min_wait = cfg["min_wait"]
            max_wait = cfg["max_wait"]
        except Exception as exc:
            logger.debug("[ToolRegistry] Failed to load retry config, using default limits: %s", exc)
            max_attempts = 1  # No retry if config unavailable
            min_wait = 2
            max_wait = 10

        # ── YAML permission allowlist (audit M4, opt-in) ─────────────
        # kazma-permissions.yaml users.<user>.{allowed,denied} was parsed but
        # never enforced outside MCP. Enforced HERE, the single tool-exec
        # chokepoint, but ONLY when the file actually defines a `users:` map —
        # the shipped file is a divisions template (no users), and enforcing
        # an empty allowlist would deny everything on default installs.
        try:
            pm = _get_permission_manager()
            if pm is not None and not pm.is_allowed(tool_name, user="default"):
                logger.warning(
                    "[ToolRegistry] '%s' denied by kazma-permissions.yaml allowlist",
                    tool_name,
                )
                return {
                    "content": (
                        f"Error: Tool '{tool_name}' is not in the permissions allowlist "
                        "(kazma-permissions.yaml users.default). Add it to `allowed` or "
                        "set allowed: ['*'] for the single-operator default."
                    ),
                    "is_error": True,
                }
        except Exception as exc:
            from kazma_core.permissions import permissions_enforce_requested

            if permissions_enforce_requested() or _get_permission_manager() is not None:
                logger.warning(
                    "[ToolRegistry] permissions check failed closed: %s", exc
                )
                return {
                    "content": (
                        f"Error: Tool '{tool_name}' blocked — permissions check "
                        "failed closed (KAZMA_PERMISSIONS_ENFORCE or users: map)."
                    ),
                    "is_error": True,
                }
            logger.debug("[ToolRegistry] permissions check skipped: %s", exc)

        # ── Division sandbox / MCP allowlist (fail-open if unset) ──
        try:
            from kazma_core.division_runtime import check_division_tool

            _div_err = await check_division_tool(tool_name)
            if _div_err:
                return {"content": _div_err, "is_error": True}
        except Exception as exc:
            logger.debug("[ToolRegistry] division check skipped: %s", exc)

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

        async def _with_post(payload: dict[str, Any]) -> dict[str, Any]:
            try:
                from kazma_core.agent.tool_hooks import apply_post_tool_hooks

                return await apply_post_tool_hooks(tool_name, arguments, payload)
            except Exception:
                logger.debug("[ToolRegistry] post-tool hook failed", exc_info=True)
                return payload

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
                    sig = None
                    valid_params = arguments

                # ── Type coercion against signature annotations ────────
                # LLMs routinely pass numbers/bools as strings ("500",
                # "true"). Coerce to the annotated type so strict
                # comparisons and arithmetic inside tools don't blow up
                # (audit M8). Best-effort: un-coercible values pass through
                # unchanged (the tool's own validation reports the error).
                if sig is not None:
                    for pname, pval in list(valid_params.items()):
                        ann = sig.parameters[pname].annotation
                        if ann is _inspect.Parameter.empty or not isinstance(pval, str):
                            continue
                        try:
                            if ann is int:
                                valid_params[pname] = int(pval.strip())
                            elif ann is float:
                                valid_params[pname] = float(pval.strip())
                            elif ann is bool:
                                valid_params[pname] = pval.strip().lower() in ("1", "true", "yes", "on")
                        except (ValueError, TypeError, AttributeError):
                            pass  # leave as-is; tool validation handles it

                # ── Argument validation BEFORE invocation ─────────────
                # Models (notably DeepSeek under long contexts) sometimes
                # emit empty/truncated tool-call JSON. Invoking the tool
                # with missing required args raises a raw TypeError whose
                # message tells the model nothing — it retries the same
                # broken call in a loop. Return a corrective error that
                # names the missing params and the full expected schema so
                # the model can self-repair on the next turn.
                if sig is not None:
                    required = [
                        p.name for p in sig.parameters.values()
                        if p.default is _inspect.Parameter.empty
                        and p.kind in (
                            _inspect.Parameter.POSITIONAL_OR_KEYWORD,
                            _inspect.Parameter.KEYWORD_ONLY,
                        )
                    ]
                    missing = [p for p in required if p not in valid_params]
                    if missing:
                        schema_hint = tool.input_schema or {}
                        logger.warning(
                            "Tool '%s' called without required args %s — raw arguments: %s",
                            tool_name, missing, json.dumps(arguments, ensure_ascii=False)[:500],
                        )
                        _record_procedural_outcome(tool_name, arguments, success=False)
                        return {
                            "content": (
                                f"Error: Tool '{tool_name}' was called with missing required "
                                f"argument(s): {', '.join(missing)}. "
                                f"Expected parameters: {json.dumps(schema_hint, ensure_ascii=False)[:800]}. "
                                "Re-issue the tool call with ALL required arguments as valid JSON."
                            ),
                            "is_error": True,
                        }

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
                    try:
                        content = json.dumps(result, ensure_ascii=False, indent=2)
                    except (ValueError, TypeError):
                        # Circular references / unserializable objects — never
                        # let normalization itself kill the tool result.
                        content = repr(result)
                else:
                    content = str(result)

                # Plain-string tools report failures by returning an
                # "Error: …" string (database_client, file tools, …). Flag
                # those as errors so downstream consumers (supervisor retry
                # logic, tool-result accounting) see the failure. Prefix check
                # only — "Error" as a substring in successful content (e.g. a
                # file named document_error_handling.md) must NOT trip this.
                _is_err = content.startswith("Error:") or content.startswith("⚠️")
                _record_procedural_outcome(tool_name, arguments, success=not _is_err)
                return await _with_post({"content": content, "is_error": _is_err})

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

            except TypeError as exc:
                # Argument-shape mismatch (e.g. wrong types from the model).
                # Surface the ACTUAL error so the model can correct its call
                # instead of retrying blind against "check server logs".
                duration_ms = (time.monotonic() - start) * 1000
                logger.error("Tool '%s' argument error after %.0fms: %s", tool_name, duration_ms, exc, exc_info=True)
                _record_procedural_outcome(tool_name, arguments, success=False)
                schema_hint = tool.input_schema or {}
                return await _with_post({
                    "content": (
                        f"Error: Tool '{tool_name}' rejected the arguments: {exc}. "
                        f"Expected parameters: {json.dumps(schema_hint, ensure_ascii=False)[:800]}. "
                        "Fix the argument names/types and call the tool again."
                    ),
                    "is_error": True,
                })

            except Exception as exc:
                # Non-retryable error — return immediately
                duration_ms = (time.monotonic() - start) * 1000
                logger.error("Tool '%s' failed after %.0fms: %s", tool_name, duration_ms, exc, exc_info=True)
                _record_procedural_outcome(tool_name, arguments, success=False)
                return await _with_post({"content": f"Error: Tool '{tool_name}' failed: {exc}", "is_error": True})

        # All retry attempts exhausted
        duration_ms = (time.monotonic() - start) * 1000
        # exc_info=last_exc (not True) — we are outside an except block here,
        # so exc_info=True logged no traceback on the retryable-failure path.
        logger.error("Tool '%s' failed after %d attempts (%.0fms): %s", tool_name, max_attempts, duration_ms, last_exc, exc_info=last_exc)
        _record_procedural_outcome(tool_name, arguments, success=False)
        return await _with_post({"content": f"Error: {last_exc}", "is_error": True})

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
        """Register the core built-in tools (implementation in tool_builtins)."""
        from kazma_core.agent.tool_builtins import register_builtin_tools

        register_builtin_tools(self)

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
