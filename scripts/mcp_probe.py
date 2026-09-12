#!/usr/bin/env python
"""Do exactly what an MCP client does to `kazma mcp`, and report the truth.

Asking an agent to describe its own tool surface does not work -- it reports
the function list in its system prompt, which is a different thing from the
server's tools/list response. This speaks the protocol.

    python mcp_probe.py "C:/Users/balfa/kazma/.venv/Scripts/kazma.exe"
"""
from __future__ import annotations

import json
import subprocess
import sys

exe = sys.argv[1] if len(sys.argv) > 1 else "kazma"

proc = subprocess.Popen(
    [exe, "mcp"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, encoding="utf-8", bufsize=1,
)


def send(obj):
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def read_id(want):
    while True:
        line = proc.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == want:
            return msg


send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "kazma-bridge-probe", "version": "1"},
}})
init = read_id(1)
send({"jsonrpc": "2.0", "method": "notifications/initialized"})

send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
resp = read_id(2)

try:
    proc.stdin.close()
    proc.wait(timeout=5)
except Exception:
    proc.kill()

err = proc.stderr.read() or ""
banner_lines = [l for l in err.splitlines() if "[kazma mcp]" in l] or ["(no banner seen)"]
banner = ("\n" + " " * 9).join(banner_lines)

if not resp:
    print("NO tools/list RESPONSE")
    print("banner:", banner)
    print(err[-1500:])
    raise SystemExit(1)

tools = (resp.get("result") or {}).get("tools") or []
names = {t.get("name") for t in tools}
destructive = [
    t for t in tools
    if (t.get("annotations") or {}).get("destructiveHint") is True
]

print("=" * 62)
print("banner :", banner)
print("server :", ((init or {}).get("result") or {}).get("serverInfo"))
print("-" * 62)
print(f"total tools published      : {len(tools)}")
print(f"annotated destructiveHint  : {len(destructive)}")
print("-" * 62)
for t in ("shell_exec", "file_write", "git_push", "file_read", "web_search"):
    print(f"  {t:12s} {'PUBLISHED' if t in names else 'withheld'}")
print("=" * 62)
if len(destructive) > 0 and "shell_exec" in names:
    print("RESULT: bridge ACTIVE -- danger tools are published and will queue")
else:
    print("RESULT: bridge NOT active -- danger tools withheld (see banner)")
