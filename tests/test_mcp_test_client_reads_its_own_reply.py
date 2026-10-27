"""Settings' MCP Test reads the reply to its own request, and drains stderr.

Audit 2026-10-26 AUD-038: ``MCPClient`` (the Test-connection client) took the
first stdout line as the reply, with no JSON-RPC id match, and read stderr
only after EOF. A server that logs a notification first reported 0 tools;
an ``npx`` server writing a lot of warnings filled its stderr pipe, blocked
before answering, and the Test timed out after 90 s.

Driven against a real child process speaking stdio JSON-RPC: it writes
256 KiB to stderr before every answer (more than any pipe holds), sends a
log notification and a ``ping`` request first, and only then the reply.
Negative control: the old "first line is the reply" rule, on the same
stream, reads the notification as the result.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import textwrap

import pytest
from kazma_core.mcp_client import MCPClient, MCPServerConfig, _jsonrpc_response

_SERVER = textwrap.dedent(
    """
    import json, sys
    out = sys.stdout.buffer
    def send(obj):
        out.write((json.dumps(obj) + "\\n").encode()); out.flush()
    for raw in sys.stdin.buffer:
        msg = json.loads(raw)
        if "id" not in msg or "method" not in msg:
            continue  # a notification, or our answer to the server's ping
        sys.stderr.write("npm warn deprecated something\\n" * 9000); sys.stderr.flush()
        send({"jsonrpc": "2.0", "method": "notifications/message",
              "params": {"level": "info", "data": "starting"}})
        send({"jsonrpc": "2.0", "id": "srv-1", "method": "ping"})
        if msg["method"] == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {}}
        elif msg["method"] == "tools/list":
            result = {"tools": [{"name": "read_thing"}, {"name": "write_thing"}]}
        else:
            result = {}
        send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
    """
)


@pytest.mark.asyncio
async def test_the_test_lists_tools_from_a_noisy_server(tmp_path):
    script = tmp_path / "noisy_server.py"
    script.write_text(_SERVER, encoding="utf-8")
    client = MCPClient()
    try:
        await asyncio.wait_for(
            client.connect(MCPServerConfig(
                name="noisy", transport="stdio", command=[sys.executable, str(script)], timeout=20,
            )),
            timeout=60,
        )
        tools = await asyncio.wait_for(client.list_tools(), timeout=60)
    finally:
        await client.disconnect()
    assert [tool["name"] for tool in tools] == ["read_thing", "write_thing"]
    assert "npm warn deprecated" in client.stderr_tail()


def test_a_reply_to_another_request_is_skipped():
    client = MCPClient()
    client._config = MCPServerConfig(name="stale", timeout=5)
    stream = io.BytesIO(
        b'{"jsonrpc": "2.0", "id": 7, "result": {"tools": []}}\n'
        b'{"jsonrpc": "2.0", "id": 8, "result": {"tools": [{"name": "t"}]}}\n'
    )

    class _Proc:
        stdin = io.BytesIO()
        stdout = stream

    assert json.loads(client._read_reply(_Proc(), 8))["result"]["tools"] == [{"name": "t"}]


def test_negative_control_the_first_line_is_a_notification():
    first_line = '{"jsonrpc": "2.0", "method": "notifications/message", "params": {}}'
    assert _jsonrpc_response(first_line) == {}  # read as an empty result: 0 tools
