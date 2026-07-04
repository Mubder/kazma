"""Kazma IDE MCP Server — stdio-based JSON-RPC server for IDE integration.

Exposes code-search, file read/write, and test-running tools via the
Model Context Protocol so VS Code (or any MCP-capable IDE) can delegate
to a local Kazma agent.

Usage:
    python -m kazma_gateway.mcp_server          # stdio mode (default)
    from kazma_gateway.mcp_server import MCPServer
    server = MCPServer(root="/path/to/project")
    await server.run(reader, writer)

Config in kazma.yaml:
    mcp:
      ide_server:
        enabled: true
        root: .               # project root (defaults to cwd)
        max_file_size: 1048576 # 1 MB read limit
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

logger = logging.getLogger("kazma.mcp.ide_server")

# ═══════════════════════════════════════════════════════════════════
# Tool definitions
# ═══════════════════════════════════════════════════════════════════

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_code",
        "description": "Search for a pattern in project files. Returns matching lines with file paths and line numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "glob": {
                    "type": "string",
                    "description": "File glob filter (e.g. '*.py')",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "read_file",
        "description": "Read the contents of a file. Returns the file text with line numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative or absolute file path",
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start from (1-indexed)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to read",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file. Creates parent directories if needed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative or absolute file path",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run pytest on the project. Returns stdout/stderr and exit code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Test path or file (default: tests/)",
                },
                "keyword": {
                    "type": "string",
                    "description": "-k expression for pytest",
                },
                "verbose": {
                    "type": "boolean",
                    "description": "Run with -v flag",
                },
            },
        },
    },
]

TOOL_MAP = {t["name"]: t for t in TOOLS}

# ═══════════════════════════════════════════════════════════════════
# Tool implementations
# ═══════════════════════════════════════════════════════════════════


def _resolve(root: Path, p: str) -> Path:
    """Resolve a path relative to root, preventing escape."""
    root_resolved = root.resolve()
    target = (root / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
    # Use relative_to() instead of str.startswith() to avoid prefix bypass
    # (e.g. /home/user/project-evil starts with /home/user/project)
    try:
        target.relative_to(root_resolved)
    except ValueError:
        raise PermissionError(f"Path {p} escapes project root")
    return target


def _tool_search_code(root: Path, args: dict[str, Any]) -> str:
    """Search for regex pattern in project files."""
    import re

    pattern = args["pattern"]
    glob = args.get("glob")
    # Validate regex complexity to prevent ReDoS
    if len(pattern) > 500:
        return "Error: pattern too long (max 500 chars)"
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Error: invalid regex: {e}"
    matches: list[str] = []

    search_files = root.rglob(glob) if glob else root.rglob("*")
    for fpath in search_files:
        if not fpath.is_file():
            continue
        # Skip binary / large files
        if fpath.stat().st_size > 1_000_000:
            continue
        try:
            text = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                rel = fpath.relative_to(root)
                matches.append(f"{rel}:{i}: {line.rstrip()}")
            if len(matches) >= 50:
                break
        if len(matches) >= 50:
            break

    if not matches:
        return "No matches found."
    return "\n".join(matches)


def _tool_read_file(root: Path, args: dict[str, Any]) -> str:
    """Read a file with optional offset/limit."""
    fpath = _resolve(root, args["path"])
    if not fpath.exists():
        return f"Error: file not found: {args['path']}"
    if not fpath.is_file():
        return f"Error: not a file: {args['path']}"
    if fpath.stat().st_size > 1_048_576:
        return "Error: file exceeds 1 MB read limit"

    lines = fpath.read_text(encoding="utf-8", errors="ignore").splitlines()
    offset = max(args.get("offset", 1), 1)
    limit = args.get("limit", 500)
    slice_ = lines[offset - 1 : offset - 1 + limit]

    numbered = [f"{offset + i}|{line}" for i, line in enumerate(slice_)]
    return "\n".join(numbered) if numbered else "(empty file)"


def _tool_write_file(root: Path, args: dict[str, Any]) -> str:
    """Write content to a file."""
    fpath = _resolve(root, args["path"])
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(args["content"], encoding="utf-8")
    return f"Wrote {len(args['content'])} chars to {args['path']}"


def _tool_run_tests(root: Path, args: dict[str, Any]) -> str:
    """Run pytest and return output."""
    cmd = [sys.executable, "-m", "pytest"]
    test_path = args.get("path", "tests/")
    cmd.append(test_path)
    if args.get("keyword"):
        cmd.extend(["-k", args["keyword"]])
    if args.get("verbose"):
        cmd.append("-v")
    cmd.extend(["--tb=short", "-q"])

    result = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = result.stdout
    if result.stderr:
        output += "\n--- stderr ---\n" + result.stderr
    output += f"\n(exit code: {result.returncode})"
    return output


DISPATCH = {
    "search_code": _tool_search_code,
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "run_tests": _tool_run_tests,
}

# ═══════════════════════════════════════════════════════════════════
# JSON-RPC protocol
# ═══════════════════════════════════════════════════════════════════


@dataclass
class MCPRequest:
    """Parsed JSON-RPC request."""

    jsonrpc: str
    method: str
    id: int | str | None = None
    params: dict[str, Any] = field(default_factory=dict)


def parse_request(line: str) -> MCPRequest:
    """Parse a JSON-RPC 2.0 request from a single line."""
    data = json.loads(line)
    if data.get("jsonrpc") != "2.0":
        raise ValueError("Invalid JSON-RPC version")
    return MCPRequest(
        jsonrpc=data["jsonrpc"],
        method=data["method"],
        id=data.get("id"),
        params=data.get("params", {}),
    )


def make_response(req_id: int | str | None, result: Any) -> str:
    """Build a JSON-RPC success response."""
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def make_error(req_id: int | str | None, code: int, message: str) -> str:
    """Build a JSON-RPC error response."""
    return json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


# ═══════════════════════════════════════════════════════════════════
# MCP Server
# ═══════════════════════════════════════════════════════════════════


class MCPServer:
    """Stdio-based MCP server for IDE integration."""

    def __init__(self, root: str | Path = ".", max_file_size: int = 1_048_576):
        self.root = Path(root).resolve()
        self.max_file_size = max_file_size
        self._running = False

    def handle_request(self, line: str) -> str | None:
        """Process a single JSON-RPC line. Returns response string or None for notifications."""
        try:
            req = parse_request(line)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            return make_error(None, -32700, f"Parse error: {exc}")

        # Notifications (no id) get no response
        if req.id is None:
            return None

        if req.method == "initialize":
            return make_response(req.id, self._handle_initialize(req.params))
        if req.method == "tools/list":
            return make_response(req.id, self._handle_tools_list())
        if req.method == "tools/call":
            return self._handle_tools_call(req.id, req.params)
        if req.method == "shutdown":
            self._running = False
            return make_response(req.id, None)

        return make_error(req.id, -32601, f"Method not found: {req.method}")

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle MCP initialize handshake."""
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "kazma-ide", "version": "0.1.0"},
        }

    def _handle_tools_list(self) -> dict[str, Any]:
        """Return available tools."""
        return {"tools": TOOLS}

    def _handle_tools_call(self, req_id: int | str, params: dict[str, Any]) -> str:
        """Execute a tool and return the result."""
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name not in DISPATCH:
            return make_error(req_id, -32602, f"Unknown tool: {tool_name}")

        try:
            result = DISPATCH[tool_name](self.root, arguments)
            return make_response(
                req_id,
                {"content": [{"type": "text", "text": result}], "isError": False},
            )
        except PermissionError as exc:
            return make_response(
                req_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        except Exception as exc:
            logger.exception("Tool %s failed", tool_name)
            return make_response(
                req_id,
                {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            )

    async def run(
        self,
        reader: asyncio.StreamReader | None = None,
        writer: asyncio.StreamWriter | None = None,
    ) -> None:
        """Run the server loop reading from stdin (or provided streams)."""
        self._running = True

        if reader is None:
            protocol = asyncio.StreamReaderProtocol(asyncio.StreamReader())
            transport, _ = await asyncio.get_event_loop().connect_read_pipe(
                lambda: protocol, sys.stdin
            )
            reader = protocol._stream_reader  # type: ignore[attr-defined]
        if writer is None:
            transport, protocol = await asyncio.get_event_loop().connect_write_pipe(
                asyncio.streams.FlowControlMixin, sys.stdout
            )
            writer = asyncio.StreamWriter(transport, protocol, None, asyncio.get_event_loop())  # type: ignore[arg-type]

        while self._running:
            line = await reader.readline()
            if not line:
                break  # stdin closed
            text = line.decode("utf-8").strip()
            if not text:
                continue

            response = self.handle_request(text)
            if response is not None:
                writer.write((response + "\n").encode("utf-8"))
                await writer.drain()

    def run_sync(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> None:
        """Synchronous blocking run — reads line by line from stdin."""
        self._running = True
        inp = stdin or sys.stdin
        out = stdout or sys.stdout

        for line in inp:
            text = line.strip()
            if not text:
                continue
            response = self.handle_request(text)
            if response is not None:
                out.write(response + "\n")
                out.flush()

        self._running = False


# ═══════════════════════════════════════════════════════════════════
# CLI entrypoint
# ═══════════════════════════════════════════════════════════════════


def main() -> None:
    """Run the MCP server on stdio."""
    import argparse

    parser = argparse.ArgumentParser(description="Kazma IDE MCP Server")
    parser.add_argument("--root", default=".", help="Project root directory")
    parser.add_argument(
        "--max-file-size", type=int, default=1_048_576, help="Max file read size in bytes"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    server = MCPServer(root=args.root, max_file_size=args.max_file_size)
    logger.info("Kazma IDE MCP server starting (root=%s)", server.root)
    server.run_sync()


if __name__ == "__main__":
    main()
