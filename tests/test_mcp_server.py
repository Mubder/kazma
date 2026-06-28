"""Tests for the Kazma IDE MCP Server (gw-056).

Covers:
  - tools/list endpoint
  - search_code tool
  - read_file tool
  - write_file tool
  - run_tests tool
  - JSON-RPC stdio protocol parsing
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from kazma_gateway.mcp_server import (
    TOOLS,
    MCPServer,
    make_error,
    make_response,
    parse_request,
)

# ═══════════════════════════════════════════════════════════════════
# JSON-RPC protocol
# ═══════════════════════════════════════════════════════════════════


class TestStdioProtocol:
    """Test JSON-RPC parsing and response generation."""

    def test_parse_valid_request(self):
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        req = parse_request(line)
        assert req.jsonrpc == "2.0"
        assert req.id == 1
        assert req.method == "tools/list"
        assert req.params == {}

    def test_parse_request_no_params(self):
        line = json.dumps({"jsonrpc": "2.0", "id": 42, "method": "initialize"})
        req = parse_request(line)
        assert req.params == {}

    def test_parse_invalid_version_raises(self):
        line = json.dumps({"jsonrpc": "1.0", "id": 1, "method": "test"})
        with pytest.raises(ValueError, match="Invalid JSON-RPC version"):
            parse_request(line)

    def test_parse_malformed_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            parse_request("not json at all")

    def test_make_response_format(self):
        resp = make_response(7, {"tools": []})
        data = json.loads(resp)
        assert data["jsonrpc"] == "2.0"
        assert data["id"] == 7
        assert data["result"] == {"tools": []}

    def test_make_error_format(self):
        resp = make_error(3, -32601, "Method not found")
        data = json.loads(resp)
        assert data["error"]["code"] == -32601
        assert data["error"]["message"] == "Method not found"

    def test_parse_notification_no_id(self):
        line = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        req = parse_request(line)
        assert req.id is None


# ═══════════════════════════════════════════════════════════════════
# tools/list
# ═══════════════════════════════════════════════════════════════════


class TestToolsList:
    """Test the tools/list endpoint."""

    def test_tools_list_returns_all_tools(self):
        server = MCPServer(root="/tmp")
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        resp = server.handle_request(line)
        data = json.loads(resp)
        tools = data["result"]["tools"]
        assert len(tools) == 4

    def test_tools_list_names(self):
        names = {t["name"] for t in TOOLS}
        assert names == {"search_code", "read_file", "write_file", "run_tests"}

    def test_tools_have_schemas(self):
        for tool in TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"
            assert "properties" in tool["inputSchema"]


# ═══════════════════════════════════════════════════════════════════
# search_code tool
# ═══════════════════════════════════════════════════════════════════


class TestSearchCodeTool:
    """Test the search_code tool."""

    def test_search_code_finds_matches(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("def hello():\n    print('world')\n")
        (tmp_path / "lib.py").write_text("def greet():\n    print('hello')\n")

        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_code", "arguments": {"pattern": "print"}},
        })
        resp = json.loads(server.handle_request(line))
        text = resp["result"]["content"][0]["text"]
        assert "app.py" in text
        assert "lib.py" in text

    def test_search_code_with_glob(self, tmp_path: Path):
        (tmp_path / "main.py").write_text("import os\n")
        (tmp_path / "main.js").write_text("const os = require('os')\n")

        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "search_code",
                "arguments": {"pattern": "os", "glob": "*.py"},
            },
        })
        resp = json.loads(server.handle_request(line))
        text = resp["result"]["content"][0]["text"]
        assert "main.py" in text
        assert "main.js" not in text

    def test_search_code_no_matches(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("x = 1\n")

        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_code", "arguments": {"pattern": "ZZZZNOTFOUND"}},
        })
        resp = json.loads(server.handle_request(line))
        assert resp["result"]["content"][0]["text"] == "No matches found."


# ═══════════════════════════════════════════════════════════════════
# read_file tool
# ═══════════════════════════════════════════════════════════════════


class TestReadFileTool:
    """Test the read_file tool."""

    def test_read_file_basic(self, tmp_path: Path):
        (tmp_path / "hello.py").write_text("print('hi')\n")

        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "hello.py"}},
        })
        resp = json.loads(server.handle_request(line))
        text = resp["result"]["content"][0]["text"]
        assert "1|print('hi')" in text

    def test_read_file_with_offset_limit(self, tmp_path: Path):
        (tmp_path / "multi.py").write_text("line1\nline2\nline3\nline4\nline5\n")

        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "read_file",
                "arguments": {"path": "multi.py", "offset": 2, "limit": 2},
            },
        })
        resp = json.loads(server.handle_request(line))
        text = resp["result"]["content"][0]["text"]
        assert "2|line2" in text
        assert "3|line3" in text
        assert "line1" not in text

    def test_read_file_not_found(self, tmp_path: Path):
        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "nope.py"}},
        })
        resp = json.loads(server.handle_request(line))
        assert "not found" in resp["result"]["content"][0]["text"].lower()

    def test_read_file_escape_blocked(self, tmp_path: Path):
        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "../../etc/passwd"}},
        })
        resp = json.loads(server.handle_request(line))
        assert resp["result"]["isError"] is True


# ═══════════════════════════════════════════════════════════════════
# write_file tool
# ═══════════════════════════════════════════════════════════════════


class TestWriteFileTool:
    """Test the write_file tool."""

    def test_write_file_creates(self, tmp_path: Path):
        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "write_file",
                "arguments": {"path": "new.py", "content": "x = 42\n"},
            },
        })
        resp = json.loads(server.handle_request(line))
        assert resp["result"]["isError"] is False
        assert "Wrote" in resp["result"]["content"][0]["text"]
        assert (tmp_path / "new.py").read_text() == "x = 42\n"

    def test_write_file_creates_dirs(self, tmp_path: Path):
        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "write_file",
                "arguments": {"path": "deep/nested/file.py", "content": "ok"},
            },
        })
        resp = json.loads(server.handle_request(line))
        assert resp["result"]["isError"] is False
        assert (tmp_path / "deep" / "nested" / "file.py").read_text() == "ok"

    def test_write_file_overwrites(self, tmp_path: Path):
        (tmp_path / "existing.txt").write_text("old")
        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "write_file",
                "arguments": {"path": "existing.txt", "content": "new"},
            },
        })
        resp = json.loads(server.handle_request(line))
        assert resp["result"]["isError"] is False
        assert (tmp_path / "existing.txt").read_text() == "new"

    def test_write_file_escape_blocked(self, tmp_path: Path):
        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "write_file",
                "arguments": {"path": "../../tmp/evil", "content": "bad"},
            },
        })
        resp = json.loads(server.handle_request(line))
        assert resp["result"]["isError"] is True


# ═══════════════════════════════════════════════════════════════════
# run_tests tool
# ═══════════════════════════════════════════════════════════════════


class TestRunTestsTool:
    """Test the run_tests tool."""

    def test_run_tests_executes_pytest(self, tmp_path: Path):
        # Create a trivial test file
        (tmp_path / "test_trivial.py").write_text("def test_one():\n    assert 1 == 1\n")

        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "run_tests", "arguments": {"path": "test_trivial.py"}},
        })
        resp = json.loads(server.handle_request(line))
        text = resp["result"]["content"][0]["text"]
        assert resp["result"]["isError"] is False
        assert "exit code:" in text

    def test_run_tests_with_keyword(self, tmp_path: Path):
        (tmp_path / "test_stuff.py").write_text(
            "def test_alpha():\n    assert True\n"
            "def test_beta():\n    assert True\n"
        )

        server = MCPServer(root=tmp_path)
        line = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "run_tests",
                "arguments": {"path": "test_stuff.py", "keyword": "alpha"},
            },
        })
        resp = json.loads(server.handle_request(line))
        text = resp["result"]["content"][0]["text"]
        assert "exit code:" in text


# ═══════════════════════════════════════════════════════════════════
# Server lifecycle
# ═══════════════════════════════════════════════════════════════════


class TestServerLifecycle:
    """Test server initialization and method dispatch."""

    def test_initialize_returns_capabilities(self):
        server = MCPServer(root="/tmp")
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        resp = json.loads(server.handle_request(line))
        assert resp["result"]["protocolVersion"] == "2024-11-05"
        assert "tools" in resp["result"]["capabilities"]
        assert resp["result"]["serverInfo"]["name"] == "kazma-ide"

    def test_unknown_method_returns_error(self):
        server = MCPServer(root="/tmp")
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "bogus"})
        resp = json.loads(server.handle_request(line))
        assert resp["error"]["code"] == -32601
        assert "not found" in resp["error"]["message"].lower()

    def test_notification_returns_none(self):
        server = MCPServer(root="/tmp")
        line = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert server.handle_request(line) is None

    def test_shutdown_stops_server(self):
        server = MCPServer(root="/tmp")
        server._running = True
        line = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "shutdown"})
        server.handle_request(line)
        assert server._running is False

    def test_stdio_run_sync(self, tmp_path: Path):
        """Test synchronous stdin/stdout loop."""
        stdin = StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
        )
        stdout = StringIO()
        server = MCPServer(root=tmp_path)
        server.run_sync(stdin=stdin, stdout=stdout)
        output = stdout.getvalue().strip()
        data = json.loads(output)
        assert len(data["result"]["tools"]) == 4
