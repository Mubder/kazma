"""Kazma as an MCP **server** — the tool seatbelt, exposed to other agents.

Everything else under ``kazma_core.mcp`` is the client half: Kazma consuming
somebody else's tools. This is the inverse, and it is the more interesting
direction. Any MCP client — Claude Desktop, an editor, another agent — can
point at ``kazma mcp`` and call Kazma's tools, and every dangerous call goes
through the same human-in-the-loop gate the chat path uses.

That is the whole product in one sentence: *your existing agent keeps its
brain, and its dangerous tools acquire a seatbelt.*

**No new gate.** ``tools/call`` routes to
:meth:`LocalToolRegistry.execute`, which is the single tool-execution
chokepoint — commitment authorization, PreToolUse hooks, the permission
allowlist and the HITL bus all already live inside it. Minting a second gate
here is the H-8 collision recipe and is explicitly forbidden; this module adds
zero safety logic of its own and deliberately owns none.

**Fail closed when nothing can approve.** If HITL is disabled, ``execute()``
returns early for danger tools and runs them unattended. That is defensible
for a developer typing into their own chat window. It is not defensible for an
MCP client calling in without a human watching, so danger tools are withheld
entirely unless an approval path exists. ``KAZMA_MCP_ALLOW_UNGATED=1`` opts
out, for a lab box where that is genuinely what you want.

Transport is newline-delimited JSON-RPC 2.0 on stdio, per the MCP stdio
transport. Hand-rolled, like ``kazma acp`` and the MCP client: the SDK is not
a dependency and this does not add one.

**Relationship to ``kazma_gateway.mcp_server`` (do not "unify" carelessly).**
That module is an older, deliberately narrow *IDE* server ("kazma-ide", gw-056):
seven hand-written tools — ``search_code``, ``read_file``, ``write_file``,
``run_tests``, ``list_files``, ``run_command``, ``git_status`` — with their own
implementations, a ``KAZMA_SECRET`` shared-secret check, and
``SafetyMiddleware.check_sync()``. It is launched as
``python -m kazma_gateway.mcp_server`` and configured under ``mcp.ide_server``
in ``kazma.yaml``.

This module is the general one: the *entire* tool registry, executed through
``LocalToolRegistry.execute()``. The difference that matters is the gate —
``check_sync()`` can only ever block, never approve, so a danger tool on that
server is refused rather than queued for a human. Here the async path inside
``execute()`` posts an approval request and waits for the answer, which is
what makes "your tools get a seatbelt" a product rather than a refusal.

Consolidating the two is probably right eventually; it is a deliberate
decision with config and test surface attached, not a drive-by.

Run it::

    kazma mcp

Claude Desktop / any MCP client config::

    {"mcpServers": {"kazma": {"command": "kazma", "args": ["mcp"]}}}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any, TextIO

logger = logging.getLogger(__name__)

#: Newest spec revision we implement. We echo back whatever the client asks
#: for when we know it, per the MCP version-negotiation rules.
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")

SERVER_NAME = "kazma"

# JSON-RPC error codes (the spec's reserved range).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _tool_allowlist() -> set[str] | None:
    """``KAZMA_MCP_TOOLS`` narrows what is published. Unset means everything."""
    raw = (os.getenv("KAZMA_MCP_TOOLS") or "").strip()
    if not raw:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


class ApprovalPath:
    """Whether a danger tool called over MCP can actually reach a human.

    Resolved once at startup and reported in the banner, so the operator
    learns the answer from the log rather than from a tool that quietly did
    nothing — or quietly did everything.
    """

    def __init__(self, *, gated: bool, reason: str, overridden: bool = False) -> None:
        self.gated = gated
        self.reason = reason
        self.overridden = overridden

    @classmethod
    def detect(cls) -> ApprovalPath:
        if _env_flag("KAZMA_MCP_ALLOW_UNGATED"):
            return cls(
                gated=True,
                reason="KAZMA_MCP_ALLOW_UNGATED=1: danger tools exposed without a verified approval path",
                overridden=True,
            )
        try:
            from kazma_core.swarm.safety import get_safety

            safety = get_safety()
        except Exception as exc:  # pragma: no cover - defensive
            return cls(gated=False, reason=f"SafetyMiddleware unavailable ({exc})")
        if not getattr(safety, "enabled", False):
            return cls(
                gated=False,
                reason="HITL is disabled (safety.hitl.enabled), so execute() would run danger tools unattended",
            )

        # HITL enabled is not the same as reachable. This server is a separate
        # process, usually spawned by the MCP client, and the approval bus lives
        # in the running Kazma server. With no real adapter, safety.check() does
        # not queue anything for a human -- it fails closed and DENIES. That is
        # safe, but publishing 55 danger tools that can only ever be refused is
        # not honest: the client's model plans around them and every attempt
        # dies. Withhold them and say why.
        try:
            from kazma_core.swarm.bus import NullBusAdapter, get_message_bus

            adapter = get_message_bus()._adapter
        except Exception as exc:  # pragma: no cover - defensive
            return cls(gated=False, reason=f"approval bus unavailable ({exc})")
        if isinstance(adapter, NullBusAdapter):
            if getattr(safety, "allow_headless_danger", False):
                return cls(
                    gated=True,
                    reason="no approval bus, but allow_headless_danger is set",
                    overridden=True,
                )
            # No bus is no longer the end of it. A running Kazma instance
            # heartbeats into the shared gate registry, and `execute()` will
            # queue a card there for a human when it sees a fresh beat. This is
            # a question about the *environment*, asked here only so the banner
            # and the published tool list can tell the truth about it -- the
            # gate itself stays in SafetyMiddleware (H-8).
            try:
                from kazma_core.safety.bus_bridge import live_watcher

                watcher = live_watcher()
            except Exception:  # pragma: no cover - defensive
                watcher = None
            if watcher is not None:
                return cls(
                    gated=True,
                    reason=(
                        "no approval bus, but a live Kazma instance is watching "
                        f"the gate registry ({watcher.age_seconds:.0f}s ago): "
                        "danger tools queue for approval there"
                    ),
                )
            return cls(
                gated=False,
                reason=(
                    "HITL is enabled, no approval bus is reachable from this "
                    "process, and no running Kazma instance is watching the "
                    "gate registry, so danger tools would be denied, not queued"
                ),
            )
        return cls(gated=True, reason="HITL enabled: danger tools require approval")


def _is_danger(tool_name: str) -> bool:
    try:
        from kazma_core.safety.hitl import get_tool_tier

        return get_tool_tier(tool_name) in ("danger", "unsafe")
    except Exception:  # pragma: no cover - defensive
        # Unknown classification is treated as dangerous. An unclassified
        # mutator slipping out un-gated is the failure that matters.
        return True


def _annotations(tool_name: str, danger: bool) -> dict[str, Any]:
    """Map Kazma's tool tiers onto MCP's own hint vocabulary.

    Clients surface these in their approval UI, so a Kazma danger tool shows
    up as destructive in Claude Desktop without the user reading our docs.
    """
    if danger:
        return {"destructiveHint": True, "readOnlyHint": False}
    return {"destructiveHint": False, "readOnlyHint": True}


def build_tool_list(approval: ApprovalPath) -> list[dict[str, Any]]:
    """Published tool set, in MCP ``tools/list`` shape."""
    from kazma_core.agent.tool_registry import get_tool_registry

    allow = _tool_allowlist()
    tools: list[dict[str, Any]] = []
    for definition in get_tool_registry().get_tool_definitions():
        fn = definition.get("function") or {}
        name = str(fn.get("name") or "")
        if not name:
            continue
        if allow is not None and name not in allow:
            continue
        danger = _is_danger(name)
        if danger and not approval.gated:
            # Withheld rather than advertised-then-refused: a tool a client
            # can see is a tool its model will plan around.
            continue
        description = str(fn.get("description") or "").strip()
        if danger:
            description = (
                f"{description}\n\n[Kazma] Danger-tier: this call pauses for a "
                "human approval in Kazma before it runs."
            ).strip()
        schema = fn.get("parameters")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        tools.append(
            {
                "name": name,
                "description": description,
                "inputSchema": schema,
                "annotations": _annotations(name, danger),
            }
        )
    tools.sort(key=lambda t: t["name"])
    return tools


async def call_tool(name: str, arguments: dict[str, Any], approval: ApprovalPath) -> dict[str, Any]:
    """Run one tool through the registry chokepoint, in MCP result shape."""
    from kazma_core.agent.tool_registry import get_tool_registry

    allow = _tool_allowlist()
    if allow is not None and name not in allow:
        return _text_result(
            f"Tool '{name}' is not published by this server (KAZMA_MCP_TOOLS).",
            is_error=True,
        )

    if _is_danger(name) and not approval.gated:
        return _text_result(
            f"Tool '{name}' is danger-tier and this server has no approval path: "
            f"{approval.reason}. Enable HITL (safety.hitl.enabled) so the call can "
            "be approved, or set KAZMA_MCP_ALLOW_UNGATED=1 if running ungated is "
            "genuinely intended.",
            is_error=True,
        )

    # The gate lives here, inside execute() — commitment, hooks, permissions,
    # HITL bus. This module adds nothing and must keep adding nothing.
    try:
        result = await get_tool_registry().execute(name, dict(arguments or {}))
    except Exception as exc:
        logger.exception("[mcp-server] %s raised", name)
        return _text_result(f"Tool '{name}' failed: {exc}", is_error=True)

    content = str(result.get("content", ""))
    return _text_result(content, is_error=bool(result.get("is_error")))


def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _negotiate_version(requested: Any) -> str:
    return (
        str(requested)
        if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
        else PROTOCOL_VERSION
    )


def _server_version() -> str:
    try:
        from kazma_core.version import get_version

        return str(get_version())
    except Exception:  # pragma: no cover - defensive
        return "0"


class MCPServer:
    """Method dispatch for the MCP stdio server.

    Kept transport-free so the tests can drive it directly.
    """

    def __init__(self, approval: ApprovalPath | None = None) -> None:
        self.approval = approval or ApprovalPath.detect()
        self.initialized = False

    async def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Return a JSON-RPC response, or ``None`` for a notification."""
        method = str(message.get("method") or "")
        msg_id = message.get("id")
        is_notification = "id" not in message or msg_id is None
        params = message.get("params")
        params = params if isinstance(params, dict) else {}

        if is_notification:
            # notifications/initialized and friends: acknowledge by silence.
            if method == "notifications/initialized":
                self.initialized = True
            return None

        try:
            result = await self._dispatch(method, params)
        except _RpcError as exc:
            return _error(msg_id, exc.code, exc.message)
        except Exception as exc:
            logger.exception("[mcp-server] %s failed", method)
            return _error(msg_id, INTERNAL_ERROR, str(exc))
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    async def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            self.initialized = True
            return {
                "protocolVersion": _negotiate_version(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": _server_version()},
                "instructions": (
                    "Kazma's tools, with its human-in-the-loop gate in front of the "
                    "dangerous ones. A danger-tier call pauses until the operator "
                    "approves it in Kazma; it does not fail, it waits."
                ),
            }

        if method == "ping":
            return {}

        if method == "tools/list":
            return {"tools": build_tool_list(self.approval)}

        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                raise _RpcError(INVALID_PARAMS, "tools/call requires a string 'name'")
            arguments = params.get("arguments")
            if arguments is not None and not isinstance(arguments, dict):
                raise _RpcError(INVALID_PARAMS, "'arguments' must be an object")
            return await call_tool(name, arguments or {}, self.approval)

        raise _RpcError(METHOD_NOT_FOUND, f"Unknown method: {method}")


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _write(obj: dict[str, Any], out: TextIO) -> None:
    # ensure_ascii=True is deliberate and load-bearing on Windows. The stream
    # may be cp1252, and one tool description containing an en-dash or a "="
    # sign would raise UnicodeEncodeError mid-session and kill the server --
    # found exactly that way, from a real client, on a real box. Escaped
    # \uXXXX is still valid JSON and every client decodes it; nothing about
    # the payload is lost, and no stream encoding can reject it.
    out.write(json.dumps(obj, ensure_ascii=True) + "\n")
    out.flush()


def _force_utf8(stream: Any) -> None:
    """Best-effort UTF-8 on a real stdio stream.

    MCP stdio is UTF-8. On Windows these default to the ANSI code page, so
    stdin would mis-decode a non-ASCII tool argument -- Arabic being the case
    that matters here. ``reconfigure`` exists on TextIOWrapper; anything else
    (a test's StringIO, a pipe wrapper) is left alone.
    """
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # pragma: no cover - stream does not support it
        pass


async def serve_stdio(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    *,
    approval: ApprovalPath | None = None,
) -> int:
    """Serve MCP on stdio until EOF.

    stdout carries protocol frames and nothing else — a stray ``print`` here
    corrupts the stream, which is why the banner goes to stderr.
    """
    owns_real_stdio = stdout is None
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if owns_real_stdio:
        _force_utf8(stdin)
        _force_utf8(stdout)
    server = MCPServer(approval=approval)

    published = len(build_tool_list(server.approval))
    banner = f"[kazma mcp] {published} tools; {server.approval.reason}"
    if server.approval.overridden:
        banner += "  *** ungated by operator override ***"
    print(banner, file=sys.stderr, flush=True)

    # The protocol owns the real stdout, so nothing else may write to it. A
    # single stray line — a library's print(), a log handler someone pointed
    # at stdout, a warning — lands between two JSON-RPC frames and kills the
    # session with a parse error the client cannot explain. Point sys.stdout
    # at stderr for the duration; the captured handle stays ours.
    saved_stdout = None
    if owns_real_stdio:
        saved_stdout = sys.stdout
        sys.stdout = sys.stderr
    try:
        while True:
            line = await asyncio.to_thread(stdin.readline)
            if line == "":
                return 0
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                _write(_error(None, PARSE_ERROR, "Parse error"), stdout)
                continue
            if not isinstance(message, dict):
                _write(_error(None, INVALID_REQUEST, "Request must be an object"), stdout)
                continue
            response = await server.handle(message)
            if response is not None:
                _write(response, stdout)
    finally:
        if saved_stdout is not None:
            sys.stdout = saved_stdout


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``kazma mcp``."""
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and argv[0] in ("-h", "--help", "help"):
        print(
            "Usage: kazma mcp\n\n"
            "Serve Kazma's tools over the Model Context Protocol (stdio), with\n"
            "the human-in-the-loop gate in front of the dangerous ones.\n\n"
            "Client config:\n"
            '  {"mcpServers": {"kazma": {"command": "kazma", "args": ["mcp"]}}}\n\n'
            "Environment:\n"
            "  KAZMA_MCP_TOOLS          comma-separated allowlist (default: all)\n"
            "  KAZMA_MCP_ALLOW_UNGATED  publish danger tools with no approval path\n"
        )
        return 0
    try:
        return asyncio.run(serve_stdio())
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130
