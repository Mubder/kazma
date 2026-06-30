---
sidebar_position: 3
---

# Hardening Guide

## Server binding

Kazma defaults to **localhost-only** (`127.0.0.1`) for security. To bind on all interfaces:

```powershell
# Windows PowerShell
$env:KAZMA_SECRET="your-secure-secret-here"
uv run kazma-web

# Linux/macOS
export KAZMA_SECRET="your-secure-secret-here"
uv run kazma-web
```

The server refuses to bind `0.0.0.0` unless `KAZMA_SECRET` is explicitly set. This prevents accidental exposure of the Web UI to the network.

## Tool sandboxing

### shell_exec — Binary allowlist

Only 60 safe binaries are allowed. Dangerous commands like `rm`, `bash`, `nc`, `curl|sh` are blocked:

```python
# Blocked — binary not in allowlist
shell_exec("rm -rf /")

# Blocked — shell injection via $(), backticks, pipes
shell_exec("echo $(cat /etc/passwd)")

# Allowed
shell_exec("ls -la /tmp")
shell_exec("python3 --version")
shell_exec("git status")
```

The allowlist includes: `ls`, `cat`, `head`, `tail`, `grep`, `find`, `wc`, `sort`, `uniq`, `echo`, `git`, `python3`, `python`, `uv`, `pip`, `npm`, `node`, `docker`, `curl`, `wget`, `ssh`, `make`, `cargo`, `go`, `rustc`, `java`, `dotnet`, `pytest`, `mypy`, `ruff`, `sqlite3`, `psql`, `redis-cli`, `systemctl`, `journalctl`, `sleep`, `date`, `which`, `whoami`, `id`, `df`, `du`, `free`, `uptime`, `uname`, `env`, `printenv`, `tar`, `zip`, `unzip`, `jq`, `awk`, `sed`, `tr`, `cut`, `shasum`, `sha256sum`, `md5sum`.

### shell_exec — No shell interpretation

Uses `subprocess.run(args, shell=False)` with `shlex.split()`. No shell metacharacters are interpreted:

- `$(...)` — blocked (no shell)
- `` `...` `` — blocked (no shell)
- `|` — blocked (no shell)
- `>` — blocked (no shell)
- `&&` — blocked (no shell)

### sqlite_query — Path restriction

Only databases under `kazma-data/` or `~/.kazma/` can be queried. Multi-statement SQL injection is blocked:

```python
# Blocked — path traversal
sqlite_query("SELECT * FROM users", db_path="../../etc/passwd")

# Blocked — multi-statement injection
sqlite_query("SELECT 1; DROP TABLE memories;")

# Allowed
sqlite_query("SELECT * FROM checkpoints WHERE name = ?", params=["my-agent"])
```

## WebSocket security

Both `/ws/chat` and `/ws/dashboard` endpoints validate `X-Kazma-Secret` on connection. Unauthenticated WebSocket connections are rejected.

## Hub API authentication

Write endpoints (`POST /api/v1/skills/submit`) require `X-Kazma-Secret` header with timing-safe HMAC comparison. Read endpoints remain open.

## API token storage

API tokens are stored as SHA-256 hashes. The raw token is only returned once at creation.

## Password hashing

User passwords use PBKDF2-SHA256 with a 16-byte random salt and timing-safe comparison (`hashlib.compare_digest`).

## HMAC signing key

The disclosure signing key is a per-installation secret — no hardcoded constant.

## Dashboard XSS prevention

All user-originating data is rendered via `textContent` (not `innerHTML`). Rich markup is escaped.

## Skill loader integrity

Skills are checksum-verified (SHA-256) before `exec_module`. Mismatched checksums block loading.

## Danger-tier tool approval

The SafetyMiddleware gates these tools behind operator approval via the SwarmMessageBus:

| Tool | Tier |
|---|---|
| `shell_exec` | danger |
| `file_write` | danger |
| `file_delete` | danger |
| `python_exec` | danger |
| `code_exec` | danger |
| `spawn_agent` | danger |
| `spawn_agents` | danger |
| `schedule_task` | danger |

Approval cards are posted to Telegram with `[👍 Approve] [👎 Reject]` buttons and expire after 60 seconds.

## Work directory

All sensitive files (logs, checkpoints, registries) live under:
- `kazma-data/` — project-scoped data
- `~/.kazma/` — user-scoped config

No runtime artifacts are stored at the repository root.
