"""`kazma mcp` — Kazma's whole tool registry exposed to other agents, HITL-gated.

Not to be confused with ``tests/test_mcp_server.py``, which covers the older,
narrower ``kazma_gateway.mcp_server`` ("kazma-ide"). See the module docstring
of ``kazma_core.mcp.server`` for how the two divide.

The claim this feature makes is narrow and load-bearing: *any MCP client can
call Kazma's tools, and the dangerous ones still stop for a human.* These
tests hold that claim to the fire.

The gate itself is not re-implemented here and must never be — it lives in
``LocalToolRegistry.execute``, the single tool-execution chokepoint (audit
H-8). What is tested here is that the server routes through it, publishes an
honest tool list, and fails closed when nothing can approve.
"""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import pytest

from kazma_core.mcp import server as mcp_server
from kazma_core.mcp.server import (
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    ApprovalPath,
    MCPServer,
    build_tool_list,
    call_tool,
    serve_stdio,
)

GATED = ApprovalPath(gated=True, reason="test: approval path present")
UNGATED = ApprovalPath(gated=False, reason="test: HITL disabled")


def _drive(messages: list[dict[str, Any]], approval: ApprovalPath = GATED) -> list[dict]:
    """Run messages through the real stdio loop and return parsed responses."""
    stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
    stdout = io.StringIO()
    rc = asyncio.run(serve_stdio(stdin, stdout, approval=approval))
    assert rc == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


# ── Protocol ────────────────────────────────────────────────────────────────


def test_initialize_advertises_tools_and_server_info() -> None:
    [resp] = _drive(
        [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
    )
    result = resp["result"]
    assert result["capabilities"]["tools"] is not None
    assert result["serverInfo"]["name"] == "kazma"
    assert result["protocolVersion"] == PROTOCOL_VERSION


@pytest.mark.parametrize("requested", SUPPORTED_PROTOCOL_VERSIONS)
def test_initialize_echoes_a_version_we_support(requested: str) -> None:
    [resp] = _drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": requested},
            }
        ]
    )
    assert resp["result"]["protocolVersion"] == requested


def test_initialize_falls_back_for_an_unknown_version() -> None:
    [resp] = _drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "1999-01-01"},
            }
        ]
    )
    assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION


def test_notifications_get_no_response() -> None:
    """A reply to a notification is a protocol violation, not a stray frame."""
    responses = _drive(
        [
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 7, "method": "ping"},
        ]
    )
    assert len(responses) == 1
    assert responses[0]["id"] == 7


def test_unknown_method_is_method_not_found() -> None:
    [resp] = _drive([{"jsonrpc": "2.0", "id": 1, "method": "does/not/exist"}])
    assert resp["error"]["code"] == METHOD_NOT_FOUND


def test_malformed_line_does_not_kill_the_session() -> None:
    """One bad frame must not take the server down — the client keeps going."""
    stdin = io.StringIO('{"not json\n{"jsonrpc":"2.0","id":2,"method":"ping"}\n')
    stdout = io.StringIO()
    asyncio.run(serve_stdio(stdin, stdout, approval=GATED))
    responses = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    assert responses[0]["error"]["code"] == PARSE_ERROR
    assert responses[1]["id"] == 2 and "result" in responses[1]


def test_tools_call_requires_a_name() -> None:
    [resp] = _drive(
        [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"arguments": {}}}]
    )
    assert "error" in resp


def test_stdout_carries_only_protocol_frames() -> None:
    """Every stdout line must be a JSON-RPC object.

    This is why `serve_stdio` repoints `sys.stdout` at stderr: one stray
    print between two frames is a parse error the client cannot recover from.
    """
    responses = _drive(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    )
    for frame in responses:
        assert frame["jsonrpc"] == "2.0"
        assert "id" in frame


# ── The gate ────────────────────────────────────────────────────────────────


def test_danger_tools_are_published_when_an_approval_path_exists() -> None:
    names = {t["name"] for t in build_tool_list(GATED)}
    assert {"shell_exec", "file_write"} <= names, (
        "gating danger tools is the product; withholding them when HITL works "
        "would make this server pointless"
    )


def test_danger_tools_are_withheld_when_nothing_can_approve() -> None:
    """Fail closed. An MCP client calls in with no human watching.

    `execute()` returns early for danger tools when HITL is off, so an
    un-gated server would hand an arbitrary client unattended shell access.
    """
    names = {t["name"] for t in build_tool_list(UNGATED)}
    assert "shell_exec" not in names
    assert "file_write" not in names
    assert "file_read" in names, "read-tier tools stay available"


def test_calling_a_withheld_danger_tool_explains_itself() -> None:
    result = asyncio.run(call_tool("shell_exec", {"command": "echo hi"}, UNGATED))
    assert result["isError"] is True
    text = result["content"][0]["text"]
    assert "danger-tier" in text
    assert "HITL disabled" in text or "approval path" in text


def test_danger_tools_are_annotated_destructive_for_the_client_ui() -> None:
    """Clients render these hints in their own approval prompt."""
    tools = {t["name"]: t for t in build_tool_list(GATED)}
    assert tools["shell_exec"]["annotations"]["destructiveHint"] is True
    assert tools["file_read"]["annotations"]["destructiveHint"] is False


def test_danger_descriptions_say_approval_is_coming() -> None:
    tools = {t["name"]: t for t in build_tool_list(GATED)}
    assert "human approval" in tools["shell_exec"]["description"]


def test_unclassified_tools_are_treated_as_dangerous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control on the classifier's failure mode.

    If tier lookup breaks, an unclassified mutator must not be published as
    safe. Fail toward withholding, never toward exposure.
    """

    def _boom(_name: str) -> str:
        raise RuntimeError("tier lookup unavailable")

    monkeypatch.setattr("kazma_core.safety.hitl.get_tool_tier", _boom)
    assert mcp_server._is_danger("anything_at_all") is True
    assert build_tool_list(UNGATED) == []


def test_call_tool_routes_through_the_registry_chokepoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server must not grow its own execution path.

    Everything safety-relevant — commitment, hooks, the permission allowlist,
    the HITL bus — lives inside `execute()`. A second path around it is the
    H-8 collision recipe.
    """
    seen: list[tuple[str, dict]] = []

    class _Registry:
        async def execute(self, name: str, arguments: dict) -> dict:
            seen.append((name, arguments))
            return {"content": "ok", "is_error": False}

    monkeypatch.setattr(
        "kazma_core.agent.tool_registry.get_tool_registry", lambda: _Registry()
    )
    result = asyncio.run(call_tool("file_read", {"path": "x"}, GATED))
    assert seen == [("file_read", {"path": "x"})]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "ok"


def test_tool_errors_surface_as_is_error_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Boom:
        async def execute(self, name: str, arguments: dict) -> dict:
            raise RuntimeError("disk on fire")

    monkeypatch.setattr(
        "kazma_core.agent.tool_registry.get_tool_registry", lambda: _Boom()
    )
    result = asyncio.run(call_tool("file_read", {}, GATED))
    assert result["isError"] is True
    assert "disk on fire" in result["content"][0]["text"]


# ── Publication surface ─────────────────────────────────────────────────────


def test_allowlist_narrows_what_is_published(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_MCP_TOOLS", "file_read, web_search")
    assert {t["name"] for t in build_tool_list(GATED)} == {"file_read", "web_search"}


def test_allowlist_is_enforced_on_call_not_just_on_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client can call a name it was never offered."""
    monkeypatch.setenv("KAZMA_MCP_TOOLS", "file_read")
    result = asyncio.run(call_tool("shell_exec", {"command": "echo hi"}, GATED))
    assert result["isError"] is True
    assert "not published" in result["content"][0]["text"]


def test_published_tools_carry_a_usable_schema() -> None:
    for tool in build_tool_list(GATED):
        schema = tool["inputSchema"]
        assert isinstance(schema, dict) and schema.get("type") == "object", tool["name"]
        assert tool["description"], f"{tool['name']} has no description"


def test_override_env_reopens_the_gate_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_MCP_ALLOW_UNGATED", "1")
    approval = ApprovalPath.detect()
    assert approval.gated is True
    assert approval.overridden is True, "an override must be visible in the banner"


def test_server_object_is_transport_free() -> None:
    """Dispatch is testable without stdio, which is why it is a separate class."""
    server = MCPServer(approval=GATED)
    resp = asyncio.run(server.handle({"jsonrpc": "2.0", "id": 1, "method": "ping"}))
    assert resp == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert asyncio.run(server.handle({"jsonrpc": "2.0", "method": "ping"})) is None


# ── Encoding ────────────────────────────────────────────────────────────────
# Regression: the server crashed mid-session on a real client. `_write` used
# ensure_ascii=False against a cp1252 stdout, so the first tool description
# containing a non-ASCII character (an en-dash, a math symbol) raised
# UnicodeEncodeError and killed the process after `initialize`. Kazma ships
# Arabic, so this was never going to stay theoretical.


def test_non_ascii_survives_a_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = "تم — ≥ 5 ملفات"

    class _Registry:
        def get_tool_definitions(self) -> list[dict[str, Any]]:
            return [{"function": {"name": "file_read", "description": "d",
                                  "parameters": {"type": "object"}}}]

        async def execute(self, name: str, arguments: dict) -> dict:
            return {"content": payload, "is_error": False}

    monkeypatch.setattr(
        "kazma_core.agent.tool_registry.get_tool_registry", lambda: _Registry()
    )
    [resp] = _drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "file_read", "arguments": {}},
            }
        ]
    )
    assert resp["result"]["content"][0]["text"] == payload


def test_the_wire_format_is_pure_ascii() -> None:
    r"""Escaped \uXXXX is valid JSON and cannot fail any stream encoding.

    Without this the server is one en-dash away from dying on a Windows
    console, which is the default box for this project's operator.
    """
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    )
    stdout = io.StringIO()
    asyncio.run(serve_stdio(stdin, stdout, approval=GATED))
    raw = stdout.getvalue()
    assert raw.isascii(), "protocol frames must be ASCII-safe on the wire"
    # ...and still decode back to the real characters.
    json.loads(raw.splitlines()[0])


def test_non_ascii_arguments_are_accepted() -> None:
    """stdin is reconfigured to UTF-8 so Arabic arguments arrive intact."""
    seen: dict[str, Any] = {}

    class _Registry:
        def get_tool_definitions(self) -> list[dict[str, Any]]:
            return [{"function": {"name": "file_read", "description": "d",
                                  "parameters": {"type": "object"}}}]

        async def execute(self, name: str, arguments: dict) -> dict:
            seen.update(arguments)
            return {"content": "ok", "is_error": False}

    import kazma_core.agent.tool_registry as reg

    original = reg.get_tool_registry
    reg.get_tool_registry = lambda: _Registry()  # type: ignore[assignment]
    try:
        _drive(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "file_read", "arguments": {"path": "مجلد/ملف.txt"}},
                }
            ]
        )
    finally:
        reg.get_tool_registry = original  # type: ignore[assignment]
    assert seen["path"] == "مجلد/ملف.txt"


class TestApprovalPathNeedsAReachableBus:
    """HITL *enabled* is not the same as HITL *reachable*.

    `kazma mcp` runs as its own process, spawned by the client. The approval
    bus lives in the running Kazma server, so with no adapter `safety.check()`
    fails closed and DENIES — it does not queue anything for a human. Zed
    reported exactly that on the first real connection: "shell_exec correctly
    denied by the HITL approval gate".

    Publishing danger tools in that state is dishonest rather than unsafe: the
    client's model plans around 55 tools that can only ever be refused.
    """

    @staticmethod
    def _detect(*, enabled: bool, null_bus: bool, headless: bool = False):
        from unittest.mock import patch

        from kazma_core.mcp.server import ApprovalPath
        from kazma_core.swarm.bus import NullBusAdapter

        class _Safety:
            pass

        safety = _Safety()
        safety.enabled = enabled
        safety.allow_headless_danger = headless

        class _Bus:
            _adapter = NullBusAdapter() if null_bus else object()

        with patch("kazma_core.swarm.safety.get_safety", return_value=safety), patch(
            "kazma_core.swarm.bus.get_message_bus", return_value=_Bus()
        ):
            return ApprovalPath.detect()

    def test_no_bus_means_no_approval_path(self):
        approval = self._detect(enabled=True, null_bus=True)
        assert approval.gated is False
        assert "no approval bus" in approval.reason

    def test_a_real_bus_gates_normally(self):
        """Negative control: the in-server case must still publish them."""
        approval = self._detect(enabled=True, null_bus=False)
        assert approval.gated is True

    def test_headless_override_is_marked_as_an_override(self):
        approval = self._detect(enabled=True, null_bus=True, headless=True)
        assert approval.gated is True
        assert approval.overridden is True

    def test_danger_tools_are_withheld_without_a_bus(self):
        from kazma_core.mcp.server import build_tool_list

        names = {t["name"] for t in build_tool_list(self._detect(enabled=True, null_bus=True))}
        assert "shell_exec" not in names
        assert "file_write" not in names
        assert "file_read" in names, "read-tier tools stay available"
