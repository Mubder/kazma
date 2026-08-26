"""Headless ``kazma ask`` + ACP stdio — the graph IS the runtime.

Does **not** start uvicorn. Same LangGraph supervisor, tools, commitment,
and workspace binding as the Web/gateway mouths.

Streaming: token deltas via ``register_delta_queue`` (same bridge as SSE);
tool start/end via a wrapping executor.

HITL: MemorySaver + graph ``interrupt()``. CLI prompts on a TTY; ACP calls
the client's ``session/request_permission``. Danger tools fail closed when
there is no operator (not a TTY, cancelled, or reject). ``--yolo`` skips.

ACP (Zed Agent Client Protocol) is JSON-RPC 2.0 over stdio.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, TextIO

logger = logging.getLogger(__name__)

ACP_PROTOCOL_VERSION = 1
_TOKEN_SENTINEL = object()

__all__ = [
    "ACP_PROTOCOL_VERSION",
    "AcpSessionState",
    "AskOptions",
    "AskResult",
    "extract_hitl_interrupt",
    "handle_acp_request",
    "map_permission_outcome",
    "parse_ask_argv",
    "prompt_from_acp_blocks",
    "run_acp_stdio",
    "run_ask",
    "tool_kind_for",
]


@dataclass
class AskOptions:
    prompt: str = ""
    workspace: str = ""
    yolo: bool = False
    plan: bool = False
    json_out: bool = False
    acp: bool = False
    thread_id: str = ""
    model: str = ""
    help: bool = False
    stream: bool = True


@dataclass
class AskResult:
    ok: bool
    text: str
    thread_id: str = ""
    error: str = ""
    plan: bool = False
    yolo: bool = False
    streamed: bool = False


def parse_ask_argv(argv: list[str]) -> AskOptions:
    """Parse ``kazma ask`` / ``kazma acp`` arguments. Does not read stdin."""
    opts = AskOptions()
    rest: list[str] = []
    i = 0
    args = list(argv)
    while i < len(args):
        a = args[i]
        if a in ("-h", "--help", "help"):
            opts.help = True
            i += 1
            continue
        if a == "--acp":
            opts.acp = True
            i += 1
            continue
        if a == "--yolo":
            opts.yolo = True
            i += 1
            continue
        if a == "--plan":
            opts.plan = True
            i += 1
            continue
        if a == "--json":
            opts.json_out = True
            i += 1
            continue
        if a == "--no-stream":
            opts.stream = False
            i += 1
            continue
        if a in ("--workspace", "-w") and i + 1 < len(args):
            opts.workspace = args[i + 1]
            i += 2
            continue
        if a.startswith("--workspace="):
            opts.workspace = a.split("=", 1)[1]
            i += 1
            continue
        if a == "--thread" and i + 1 < len(args):
            opts.thread_id = args[i + 1]
            i += 2
            continue
        if a == "--model" and i + 1 < len(args):
            opts.model = args[i + 1]
            i += 2
            continue
        if a == "--":
            rest.extend(args[i + 1 :])
            break
        if a.startswith("-") and a not in ("-",):
            raise ValueError(f"Unknown flag: {a}")
        rest.append(a)
        i += 1
    opts.prompt = " ".join(rest).strip()
    return opts


def ask_help_text() -> str:
    return (
        "Usage: kazma ask [options] <prompt>\n"
        "       kazma ask -                 Read prompt from stdin\n"
        "       kazma acp                   ACP JSON-RPC on stdio (Zed / JetBrains)\n"
        "\n"
        "Run the LangGraph supervisor in-process. Does not start the web server.\n"
        "Tokens stream to stdout; tool lines go to stderr. --json emits NDJSON events.\n"
        "\n"
        "Options:\n"
        "  --workspace, -w PATH  Workspace root (default: cwd)\n"
        "  --yolo                Allow danger tools without HITL (headless)\n"
        "  --plan                Enter plan mode (read-only until the model plans)\n"
        "  --json                NDJSON events on stdout (token/tool/done)\n"
        "  --no-stream           Wait for the full reply (no live tokens)\n"
        "  --thread ID           Reuse a thread id\n"
        "  --model NAME          Switch model for this process\n"
        "  --acp                 Agent Client Protocol stdio server\n"
        "\n"
        "HITL: on a TTY, danger tools prompt y/N. Not a TTY → deny unless --yolo.\n"
        "ACP: session/request_permission so the editor can approve.\n"
    )


def prompt_from_acp_blocks(prompt: Any) -> str:
    """Flatten ACP ``session/prompt`` content blocks to text."""
    if isinstance(prompt, str):
        return prompt.strip()
    if not isinstance(prompt, list):
        return str(prompt or "").strip()
    parts: list[str] = []
    for block in prompt:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "")
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind == "resource":
            res = block.get("resource") if isinstance(block.get("resource"), dict) else {}
            uri = str(res.get("uri") or "")
            text = str(res.get("text") or "")
            if text:
                parts.append(f"Attached {uri}:\n{text}" if uri else text)
        elif kind == "resource_link":
            uri = str(block.get("uri") or "")
            if uri:
                parts.append(f"[resource {uri}]")
    return "\n".join(p for p in parts if p).strip()


def tool_kind_for(name: str) -> str:
    n = (name or "").lower()
    if any(k in n for k in ("delete",)):
        return "delete"
    if any(k in n for k in ("write", "patch", "apply", "edit")):
        return "edit"
    if any(k in n for k in ("shell", "exec", "run")):
        return "execute"
    if any(k in n for k in ("search", "grep", "index")):
        return "search"
    if any(k in n for k in ("read", "list", "get", "status", "hover")):
        return "read"
    return "other"


def _hitl_from_value(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "value") and not isinstance(value, dict):
        value = getattr(value, "value", None)
    if isinstance(value, (list, tuple)) and value:
        value = value[0]
        if hasattr(value, "value") and not isinstance(value, dict):
            value = getattr(value, "value", None)
    if isinstance(value, dict) and (
        value.get("type") == "hitl_approval"
        or "tool" in value
        or "tools" in value
    ):
        return value
    return None


def extract_hitl_interrupt(snapshot: Any) -> dict[str, Any] | None:
    """Pull a HITL payload from ``graph.aget_state`` / a fake snapshot."""
    if snapshot is None:
        return None
    if isinstance(snapshot, dict):
        found = _hitl_from_value(
            snapshot.get("__interrupt__") or snapshot.get("interrupts")
        )
        if found:
            return found
    tasks = getattr(snapshot, "tasks", None) or ()
    for task in tasks:
        for intr in getattr(task, "interrupts", None) or ():
            found = _hitl_from_value(intr)
            if found:
                return found
    return _hitl_from_value(getattr(snapshot, "interrupts", None))


def map_permission_outcome(result: dict[str, Any] | None) -> dict[str, Any]:
    """Map ACP ``session/request_permission`` result → graph resume value."""
    raw = result or {}
    outcome = raw.get("outcome") if isinstance(raw.get("outcome"), dict) else raw
    kind = str(outcome.get("outcome") or "")
    if kind == "cancelled":
        return {"approved": False, "cancelled": True}
    oid = str(outcome.get("optionId") or raw.get("optionId") or "").strip().lower()
    if oid in ("allow-once", "allow-always", "allow", "allow_once", "allow_always"):
        return {
            "approved": True,
            "always": oid in ("allow-always", "allow_always"),
        }
    return {"approved": False}


def _boot_env(*, workspace: str = "", model: str = "") -> None:
    try:
        from kazma_core.eventloop import set_windows_selector_policy

        set_windows_selector_policy()
    except Exception:
        pass
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        cwd_env = Path.cwd() / ".env"
        if cwd_env.exists():
            load_dotenv(dotenv_path=cwd_env, override=True)
    except Exception:
        pass
    from pathlib import Path as _Path

    root = (workspace or "").strip() or str(_Path.cwd())
    try:
        from kazma_core.workspace.binding import configure_workspace

        configure_workspace(workspace=root)
    except Exception:
        logger.debug("[ask] configure_workspace skipped", exc_info=True)
    if model:
        try:
            from kazma_core.model_registry import get_model_registry

            get_model_registry().set_active_model(model)
        except Exception:
            logger.warning("[ask] set_active_model(%s) failed", model, exc_info=True)


def _emit(on_event: Callable[[dict[str, Any]], Any] | None, event: dict[str, Any]) -> None:
    if on_event is None:
        return
    try:
        on_event(event)
    except Exception:
        logger.debug("[ask] on_event failed", exc_info=True)


class _EmittingExecutor:
    """Delegate to the real executor; fire tool_start / tool_end events."""

    def __init__(self, inner: Any, on_event: Callable[[dict[str, Any]], Any] | None) -> None:
        self._inner = inner
        self._on_event = on_event

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def execute(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tid = str((arguments or {}).get("id") or uuid.uuid4())
        _emit(self._on_event, {
            "event": "tool_start",
            "tool": tool_name,
            "tool_id": tid,
            "kind": tool_kind_for(tool_name),
            "args": dict(arguments or {}),
        })
        result = await self._inner.execute(tool_name, arguments)
        if not isinstance(result, dict):
            result = {"content": str(result), "is_error": False}
        _emit(self._on_event, {
            "event": "tool_end",
            "tool": tool_name,
            "tool_id": tid,
            "kind": tool_kind_for(tool_name),
            "ok": not bool(result.get("is_error")),
            "text": str(result.get("content") or "")[:2000],
        })
        return result


def _token_text(ev: Any) -> str:
    if not isinstance(ev, dict):
        return ""
    if ev.get("event") != "on_chat_model_stream":
        return ""
    data = ev.get("data") or {}
    chunk = data.get("chunk") if isinstance(data, dict) else {}
    if isinstance(chunk, dict):
        return str(chunk.get("content") or "")
    return ""


async def run_ask(
    prompt: str,
    opts: AskOptions | None = None,
    *,
    on_event: Callable[[dict[str, Any]], Any] | None = None,
    hitl_decide: Callable[[dict[str, Any]], Any] | None = None,
) -> AskResult:
    """Run one supervisor turn. Never starts uvicorn."""
    opts = opts or AskOptions()
    text = (prompt or opts.prompt or "").strip()
    if not text:
        return AskResult(ok=False, text="", error="Empty prompt")
    _boot_env(workspace=opts.workspace, model=opts.model)
    thread_id = (opts.thread_id or "").strip() or f"cli-{uuid.uuid4()}"
    if opts.plan:
        try:
            from kazma_core.agent.plan_mode import enable_plan_mode

            enable_plan_mode(thread_id, actor="cli")
        except Exception:
            logger.debug("[ask] plan mode enable failed", exc_info=True)
    if opts.yolo:
        try:
            from kazma_core.safety.yolo import enable_yolo

            enable_yolo(thread_id, actor="cli")
        except Exception:
            logger.debug("[ask] yolo enable failed", exc_info=True)
        try:
            from kazma_core.swarm.safety import get_safety

            get_safety().allow_headless_danger = True
        except Exception:
            logger.debug("[ask] allow_headless_danger failed", exc_info=True)
    try:
        reply = await _invoke_supervisor(
            text,
            thread_id=thread_id,
            yolo=opts.yolo,
            stream=opts.stream,
            on_event=on_event,
            hitl_decide=hitl_decide,
        )
    except Exception as exc:
        logger.error("[ask] invoke failed: %s", exc, exc_info=True)
        try:
            from kazma_core.retry import friendly_llm_error

            msg = friendly_llm_error(exc)
        except Exception:
            msg = f"⚠️ {exc}"
        _emit(on_event, {"event": "error", "text": msg})
        return AskResult(
            ok=False, text=msg, thread_id=thread_id, error=str(exc),
            plan=opts.plan, yolo=opts.yolo,
        )
    _emit(on_event, {
        "event": "done",
        "ok": True,
        "text": reply,
        "thread_id": thread_id,
    })
    return AskResult(
        ok=True, text=reply, thread_id=thread_id, plan=opts.plan, yolo=opts.yolo,
        streamed=on_event is not None and opts.stream,
    )


async def _invoke_supervisor(
    prompt: str,
    *,
    thread_id: str,
    yolo: bool = False,
    stream: bool = True,
    on_event: Callable[[dict[str, Any]], Any] | None = None,
    hitl_decide: Callable[[dict[str, Any]], Any] | None = None,
) -> str:
    """Build a graph (MemorySaver when HITL is live) and ainvoke, with resume."""
    from kazma_core.agent.graph_builder import build_supervisor_graph
    from kazma_core.agent.state import initial_supervisor_state
    from kazma_core.agent_runner import KazmaAgent, load_config
    from kazma_core.safety.hitl import (
        get_hitl_config,
        reset_current_thread_id,
        set_current_thread_id,
    )

    agent = KazmaAgent(load_config())
    executor: Any = agent.tools
    if on_event is not None:
        executor = _EmittingExecutor(agent.tools, on_event)

    hitl = None
    checkpointer: Any = None
    if not yolo:
        hitl = get_hitl_config(agent.config.raw)
        if isinstance(hitl, dict) and not hitl.get("enabled", True):
            hitl = None
        if hitl:
            from langgraph.checkpoint.memory import MemorySaver

            checkpointer = MemorySaver()

    graph = build_supervisor_graph(
        llm=agent.llm,
        system_prompt=agent.system_prompt,
        tool_definitions=agent.tools.get_tool_definitions(),
        tool_executor=executor,
        cost_breaker=agent.cost_breaker,
        authority=agent.authority,
        tracer=agent.tracer,
        hitl_config=hitl,
        checkpointer=checkpointer,
        snapshot_recorder=getattr(agent, "_snapshot_recorder", None),
    )

    state = initial_supervisor_state(thread_id=thread_id)
    state["messages"] = [{"role": "user", "content": prompt}]
    try:
        from kazma_core.agent.long_task import resolve_turn_budgets

        recursion = int(resolve_turn_budgets(thread_id)["recursion_limit"])
    except Exception:
        recursion = 100
    config = {
        "configurable": {"thread_id": thread_id, "checkpoint_ns": ""},
        "recursion_limit": recursion,
    }

    from kazma_core.llm_stream import register_delta_queue, unregister_delta_queue

    token_q: asyncio.Queue[Any] = asyncio.Queue()
    drain_task: asyncio.Task[Any] | None = None
    if stream and on_event is not None:
        register_delta_queue(thread_id, token_q)

        async def _drain() -> None:
            while True:
                item = await token_q.get()
                if item is _TOKEN_SENTINEL:
                    return
                piece = _token_text(item)
                if piece:
                    _emit(on_event, {"event": "token", "text": piece})

        drain_task = asyncio.create_task(_drain())

    token = set_current_thread_id(thread_id)
    payload: Any = state
    last_text = ""
    try:
        hitl_rounds = 0
        while True:
            try:
                result = await graph.ainvoke(payload, config)
            except Exception as exc:
                # Some LangGraph versions raise GraphInterrupt instead of
                # returning a paused state. aget_state still has the payload.
                if type(exc).__name__ not in ("GraphInterrupt", "NodeInterrupt"):
                    raise
                result = {}
            messages = (result or {}).get("messages") or []
            for msg in reversed(messages):
                if isinstance(msg, dict) and msg.get("role") == "assistant" and msg.get("content"):
                    last_text = str(msg["content"])
                    break
            snap = None
            if checkpointer is not None:
                try:
                    snap = await graph.aget_state(config)
                except Exception:
                    snap = None
            hitl_payload = extract_hitl_interrupt(snap)
            if not hitl_payload:
                hitl_payload = extract_hitl_interrupt(result)
            if not hitl_payload:
                break
            hitl_rounds += 1
            if hitl_rounds > 32:
                logger.error("[ask] HITL interrupt loop exceeded — stopping")
                break
            _emit(on_event, {
                "event": "hitl",
                "tool": hitl_payload.get("tool"),
                "message": hitl_payload.get("message") or "",
                "args": hitl_payload.get("args") or {},
            })
            decision = {"approved": False}
            if hitl_decide is not None:
                try:
                    maybe = hitl_decide(hitl_payload)
                    if asyncio.iscoroutine(maybe):
                        maybe = await maybe
                    if isinstance(maybe, dict):
                        decision = maybe
                except Exception:
                    logger.warning("[ask] HITL decide failed — denying", exc_info=True)
                    decision = {"approved": False}
            if decision.get("always") and decision.get("approved"):
                try:
                    from kazma_core.safety.yolo import enable_yolo

                    enable_yolo(thread_id, actor="cli-allow-always")
                except Exception:
                    pass
            from kazma_core.safety.commitment.resume import build_resume_command

            payload = build_resume_command(
                hitl_payload,
                approved=bool(decision.get("approved")),
                reason="cli" if decision.get("approved") else "denied",
            )
            if payload is None:
                # Stale HITL card — nothing pending to resume.
                break
    finally:
        reset_current_thread_id(token)
        if drain_task is not None:
            try:
                token_q.put_nowait(_TOKEN_SENTINEL)
            except Exception:
                pass
            try:
                await asyncio.wait_for(drain_task, timeout=2.0)
            except Exception:
                drain_task.cancel()
            unregister_delta_queue(thread_id)
    return last_text


# ── ACP JSON-RPC ───────────────────────────────────────────────────────


@dataclass
class AcpSessionState:
    initialized: bool = False
    sessions: dict[str, str] = field(default_factory=dict)
    yolo: bool = False
    workspace: str = ""


def handle_acp_request(
    message: dict[str, Any],
    state: AcpSessionState,
    *,
    runner: Any | None = None,
) -> dict[str, Any] | None:
    """Handle one JSON-RPC request. Returns a response dict or None (notification)."""
    _ = runner
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "Invalid Request")
    method = str(message.get("method") or "")
    msg_id = message.get("id")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}

    if method == "initialize":
        state.initialized = True
        ver = params.get("protocolVersion", ACP_PROTOCOL_VERSION)
        try:
            chosen = int(ver)
        except (TypeError, ValueError):
            chosen = ACP_PROTOCOL_VERSION
        if chosen != ACP_PROTOCOL_VERSION:
            chosen = ACP_PROTOCOL_VERSION
        try:
            from kazma_core.version import get_version

            version = get_version()
        except Exception:
            version = "0.10.0"
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": chosen,
                "agentCapabilities": {
                    "loadSession": False,
                    "promptCapabilities": {
                        "image": False,
                        "audio": False,
                        "embeddedContext": True,
                    },
                },
                "agentInfo": {
                    "name": "kazma",
                    "title": "Kazma",
                    "version": version,
                },
                "authMethods": [],
            },
        }

    if method == "authenticate":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "session/new":
        sid = str(uuid.uuid4())
        cwd = str(params.get("cwd") or "").strip()
        if cwd:
            state.workspace = cwd
        state.sessions[sid] = sid
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"sessionId": sid}}

    if method == "session/cancel":
        return None  # notification

    if method == "shutdown":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "session/prompt":
        if msg_id is None:
            return None
        sid = str(params.get("sessionId") or "")
        if sid and sid not in state.sessions:
            state.sessions[sid] = sid
        prompt = prompt_from_acp_blocks(params.get("prompt"))
        return {
            "_async": "prompt",
            "id": msg_id,
            "sessionId": sid or next(iter(state.sessions), str(uuid.uuid4())),
            "prompt": prompt,
        }

    if msg_id is None:
        return None
    return _rpc_error(msg_id, -32601, f"Method not found: {method}")


def _rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _write_rpc(obj: dict[str, Any], out: TextIO) -> None:
    out.write(json.dumps(obj, ensure_ascii=False) + "\n")
    out.flush()


class _JsonRpcStdio:
    """Bidirectional JSON-RPC over stdio (client requests + our calls)."""

    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pending: dict[Any, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._eof = False

    async def read_loop(self) -> None:
        while True:
            line = await asyncio.to_thread(self._stdin.readline)
            if line == "":
                self._eof = True
                await self._inbox.put({"_eof": True})
                return
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    continue
                while True:
                    hdr = await asyncio.to_thread(self._stdin.readline)
                    if hdr in ("", "\n", "\r\n"):
                        break
                line = await asyncio.to_thread(self._stdin.read, length)
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                _write_rpc(_rpc_error(None, -32700, "Parse error"), self._stdout)
                continue
            if not isinstance(message, dict):
                continue
            if message.get("method"):
                await self._inbox.put(message)
                continue
            msg_id = message.get("id")
            fut = self._pending.pop(msg_id, None)
            if fut is not None and not fut.done():
                fut.set_result(message)

    async def next_request(self) -> dict[str, Any] | None:
        msg = await self._inbox.get()
        if msg.get("_eof"):
            return None
        return msg

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        rid = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[rid] = fut
        _write_rpc(
            {"jsonrpc": "2.0", "id": rid, "method": method, "params": params},
            self._stdout,
        )
        try:
            resp = await asyncio.wait_for(fut, timeout=600.0)
        except TimeoutError:
            self._pending.pop(rid, None)
            return {"outcome": {"outcome": "cancelled"}}
        if resp.get("error"):
            return {"outcome": {"outcome": "cancelled"}}
        result = resp.get("result")
        return result if isinstance(result, dict) else {}

    def notify(self, method: str, params: dict[str, Any]) -> None:
        _write_rpc({"jsonrpc": "2.0", "method": method, "params": params}, self._stdout)

    def reply(self, obj: dict[str, Any]) -> None:
        _write_rpc(obj, self._stdout)


async def run_acp_stdio(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    yolo: bool = False,
    workspace: str = "",
) -> int:
    """Serve ACP on stdio until EOF. Logs go to stderr."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    state = AcpSessionState(yolo=yolo, workspace=workspace)
    _boot_env(workspace=workspace)
    rpc = _JsonRpcStdio(stdin, stdout)
    reader = asyncio.create_task(rpc.read_loop())
    try:
        while True:
            message = await rpc.next_request()
            if message is None:
                return 0
            resp = handle_acp_request(message, state)
            if resp is None:
                continue
            if resp.get("_async") == "prompt":
                sid = str(resp.get("sessionId") or "")
                prompt = str(resp.get("prompt") or "")
                msg_id = resp.get("id")
                try:
                    await _acp_run_prompt(
                        rpc, state, sid=sid, prompt=prompt, msg_id=msg_id,
                        yolo=yolo, workspace=workspace,
                    )
                except Exception as exc:
                    logger.error("[acp] session/prompt failed: %s", exc, exc_info=True)
                    rpc.reply({
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32000, "message": str(exc)[:500]},
                    })
                continue
            rpc.reply(resp)
    finally:
        if not reader.done():
            reader.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await reader
    return 0


async def _acp_run_prompt(
    rpc: _JsonRpcStdio,
    state: AcpSessionState,
    *,
    sid: str,
    prompt: str,
    msg_id: Any,
    yolo: bool,
    workspace: str,
) -> None:
    def on_event(ev: dict[str, Any]) -> None:
        kind = ev.get("event")
        if kind == "token" and ev.get("text"):
            rpc.notify("session/update", {
                "sessionId": sid,
                "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": ev["text"]},
                },
            })
        elif kind == "tool_start":
            rpc.notify("session/update", {
                "sessionId": sid,
                "update": {
                    "sessionUpdate": "tool_call",
                    "toolCallId": str(ev.get("tool_id") or ev.get("tool") or ""),
                    "title": str(ev.get("tool") or "tool"),
                    "kind": ev.get("kind") or "other",
                    "status": "in_progress",
                    "rawInput": ev.get("args") or {},
                },
            })
        elif kind == "tool_end":
            rpc.notify("session/update", {
                "sessionId": sid,
                "update": {
                    "sessionUpdate": "tool_call_update",
                    "toolCallId": str(ev.get("tool_id") or ev.get("tool") or ""),
                    "status": "completed" if ev.get("ok") else "failed",
                    "content": [{
                        "type": "content",
                        "content": {"type": "text", "text": str(ev.get("text") or "")[:1500]},
                    }],
                },
            })

    async def hitl_decide(payload: dict[str, Any]) -> dict[str, Any]:
        tools = payload.get("tools") or []
        tool_id = ""
        if isinstance(tools, list) and tools and isinstance(tools[0], dict):
            tool_id = str(tools[0].get("id") or "")
        tool_id = tool_id or str(payload.get("tool") or "tool")
        rpc.notify("session/update", {
            "sessionId": sid,
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": tool_id,
                "title": str(payload.get("message") or payload.get("tool") or "danger tool"),
                "kind": tool_kind_for(str(payload.get("tool") or "")),
                "status": "pending",
                "rawInput": payload.get("args") or {},
            },
        })
        result = await rpc.call("session/request_permission", {
            "sessionId": sid,
            "toolCall": {
                "toolCallId": tool_id,
                "title": str(payload.get("message") or payload.get("tool") or ""),
                "kind": tool_kind_for(str(payload.get("tool") or "")),
                "status": "pending",
                "rawInput": payload.get("args") or {},
            },
            "options": [
                {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                {
                    "optionId": "allow-always",
                    "name": "Allow for this session",
                    "kind": "allow_always",
                },
                {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
            ],
        })
        return map_permission_outcome(result)

    opts = AskOptions(
        prompt=prompt,
        workspace=state.workspace or workspace,
        yolo=yolo,
        thread_id=sid,
        stream=True,
    )
    result = await run_ask(
        prompt, opts, on_event=on_event, hitl_decide=None if yolo else hitl_decide,
    )
    if not result.streamed and (result.text or result.error):
        rpc.notify("session/update", {
            "sessionId": sid,
            "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": result.text or result.error},
            },
        })
    rpc.reply({
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {"stopReason": "end_turn" if result.ok else "refusal"},
    })
