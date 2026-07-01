# Kazma Security Audit Report

**Date:** 2026-06-30
**Scope:** Full repository at `/home/balfaris/kazma/`
**Auditor:** Hermes Agent (automated)

---

## Executive Summary

The Kazma codebase demonstrates **above-average security awareness** for an AI agent framework. It includes purpose-built security infrastructure (SSRF protection, security linter, HITL gates, RBAC, audit logging, tool sandbox, dependency scanner). However, several real vulnerabilities and design weaknesses remain. This report documents **14 findings** across Critical, High, Medium, and Low severity.

---

## Findings

### 1. CRITICAL — Arbitrary Shell Command Execution via `shell_exec` Tool

**File:** `kazma-core/kazma_core/agent/tool_registry.py`, lines 591–606

**Description:**
The `shell_exec` built-in tool uses `asyncio.create_subprocess_shell()` to execute arbitrary shell commands with **no sandboxing, no allowlist, and no workspace restriction**. Unlike `file_write` (which has workspace scoping) and `python_exec` (which uses `-I` isolated mode + resource limits), `shell_exec` runs in the host environment with full PATH access. An LLM prompt injection or compromised skill can execute `rm -rf /`, exfiltrate `/etc/passwd`, install malware, etc.

```python
async def shell_exec(command: str, timeout: int = 30) -> str:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
```

**Impact:** Full host compromise. Arbitrary file read/write, network exfiltration, privilege escalation.

**Recommended Fix:**
- Remove `shell_exec` from the default tool registry or move it behind the HITL gate (it's already in `DEFAULT_DANGER_TOOLS` but the tool registry itself doesn't enforce HITL — that's the graph's responsibility).
- If retained, use `shlex.split()` + `create_subprocess_exec()` instead of `create_subprocess_shell()`.
- Add an allowlist of permitted commands.
- Consider running in a container/namespace with seccomp restrictions.

---

### 2. CRITICAL — File Tools (`file_read`, `file_write`, `file_list`, `file_search`) in Tool Registry Bypass Workspace Scoping

**File:** `kazma-core/kazma_core/agent/tool_registry.py`, lines 436–503

**Description:**
The standalone `file_write`/`file_read` tools in `kazma_core/tools/file_write.py` properly enforce workspace scoping via `_is_within_workspace()`. However, the **tool registry's own built-in `file_read`, `file_write`, `file_list`, and `file_search`** (lines 436–503) use bare `Path(path).expanduser().resolve()` with **no workspace boundary check**:

```python
async def file_read(path: str, encoding: str = "utf-8") -> str:
    p = Path(path).expanduser().resolve()
    # NO workspace check — reads ANY file on the system
    ...
    return p.read_text(encoding=encoding)
```

This means the agent can read `/etc/shadow`, `~/.ssh/id_rsa`, write to `/etc/cron.d/`, etc.

**Impact:** Arbitrary file read/write on the host filesystem.

**Recommended Fix:**
- Import and use the workspace-scoped `file_write`/`file_read` from `kazma_core.tools.file_write` instead of reimplementing.
- Alternatively, add `_is_within_workspace()` checks to all four built-in tools.

---

### 3. HIGH — Hardcoded Dummy API Key in Chat Module

**File:** `kazma-ui/kazma_ui/chat.py`, lines 207, 214

**Description:**
```python
chat_api_key = "sk-local-dev"  # Prevent cloud fallback
```

While this is a dummy key to prevent cloud API calls, the pattern of hardcoding API-key-like strings (`sk-local-dev`) is a security anti-pattern. If this value accidentally matches a real key format or is reused elsewhere, it creates confusion in audits and could mask real credential exposure.

**Impact:** Low direct risk, but creates audit noise and masks real secrets.

**Recommended Fix:**
- Use an empty string or a clearly non-functional sentinel (e.g., `"not-a-real-key"`).
- Or better: raise an error when no API key is configured instead of substituting a dummy.

---

### 4. HIGH — `shell_exec` Also Uses `create_subprocess_shell` in Swarm Worker

**File:** `kazma-core/kazma_core/swarm/worker.py`, line 308

**Description:**
The swarm worker uses `asyncio.create_subprocess_shell()` to invoke `hermes` CLI. While `shlex.quote()` is used for argument construction (line 305), the shell still interprets the command, leaving a residual injection surface:

```python
cmd = f"hermes -p {shlex.quote(self.profile)} {shlex.quote(prompt)}"
proc = await asyncio.create_subprocess_shell(cmd, ...)
```

**Impact:** If `prompt` contains carefully crafted payloads, shell metacharacter injection may still be possible despite `shlex.quote()`.

**Recommended Fix:**
- Use `create_subprocess_exec()` with an argument list instead:
  ```python
  proc = await asyncio.create_subprocess_exec(
      "hermes", "-p", self.profile, prompt,
      stdout=asyncio.subprocess.PIPE,
      stderr=asyncio.subprocess.PIPE,
  )
  ```

---

### 5. HIGH — `sqlite_query` Tool Allows Arbitrary DB Path

**File:** `kazma-core/kazma_core/agent/tool_registry.py`, lines 512–540

**Description:**
The `sqlite_query` tool accepts a user-supplied `db_path` parameter and opens any SQLite database on the filesystem. Combined with the lack of workspace scoping (Finding #2), this allows reading arbitrary SQLite databases (e.g., browser credential stores, other application databases):

```python
async def sqlite_query(query: str, db_path: str = "kazma-data/checkpoints.db", ...):
    path = Path(db_path).expanduser().resolve()
    conn = sqlite3.connect(str(path))
```

Additionally, the SELECT-only guard (line 519–521) uses a simple `startswith("SELECT")` check that can be bypassed with `SELECT ...; DROP TABLE ...` multi-statement queries (though Python's `sqlite3.execute()` only runs one statement by default).

**Impact:** Arbitrary database file read; potential data exfiltration.

**Recommended Fix:**
- Restrict `db_path` to a known set of Kazma databases.
- Add workspace scoping for the db_path.
- Parse the SQL statement to reject anything with `;` or multiple statements.

---

### 6. HIGH — Auth Middleware Bypass When `KAZMA_SECRET` Is Unset

**File:** `kazma-ui/kazma_ui/auth.py`, lines 156–158

**Description:**
When `KAZMA_SECRET` env var is not set, **all sensitive endpoints are completely open**:

```python
if not expected:
    return await call_next(request)  # Open mode
```

This is documented as "backward compatible" but means that by default, anyone on the network can:
- Read/modify all settings (including API keys, tokens)
- Start/stop the swarm
- Manage MCP servers
- Export the full config (including secrets)

**Impact:** Full configuration takeover in default deployments.

**Recommended Fix:**
- Generate a random secret on first run and persist it.
- Print the secret to stdout on startup with instructions.
- Or: require explicit opt-in to open mode via `KAZMA_AUTH_DISABLED=true`.

---

### 7. MEDIUM — CORS `allow_headers=["*"]` with `allow_credentials=True`

**File:** `kazma-ui/kazma_ui/app.py`, lines 97–103

**Description:**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["*"],  # ← Too permissive
)
```

While `allow_origins` is properly restricted to localhost by default, `allow_headers=["*"]` combined with `allow_credentials=True` allows any custom header. If a user ever configures `KAZMA_CORS_ORIGINS=*`, this becomes a full CORS misconfiguration.

**Impact:** Potential CSRF-like attacks if origins are misconfigured.

**Recommended Fix:**
- Explicitly list allowed headers: `["X-Kazma-Secret", "Content-Type", "Authorization"]`.
- Add a warning log if `KAZMA_CORS_ORIGINS` contains `*`.

---

### 8. MEDIUM — HITL Gate Can Be Disabled via Config

**File:** `kazma-core/kazma_core/safety/hitl.py`, lines 75–76

**Description:**
```python
def requires_approval(tool_name: str, hitl_config: dict[str, Any]) -> bool:
    if not hitl_config.get("enabled", True):
        return False  # All tools allowed without approval
```

Setting `safety.hitl.enabled: false` in `kazma.yaml` completely disables the HITL safety gate. While this is configurable, there's no confirmation step or audit log entry when HITL is disabled — it silently opens all dangerous operations.

**Impact:** Accidental or malicious disabling of safety gates.

**Recommended Fix:**
- Log a CRITICAL-level warning when HITL is disabled.
- Require an environment variable override (`KAZMA_DISABLE_HITL=true`) in addition to the config file.
- Record HITL disable events in the audit log.

---

### 9. MEDIUM — `file_write` Tool Bypasses Workspace When `allow_absolute=True`

**File:** `kazma-core/kazma_core/tools/file_write.py`, lines 21–30, 91–94

**Description:**
```python
def configure_workspace(workspace=None, allow_absolute=False):
    _ALLOW_ABSOLUTE = allow_absolute

# In file_write:
if not within:
    if not _ALLOW_ABSOLUTE:
        return "Safety: writes outside workspace are not allowed."
```

If `allow_absolute` is set to `True` (via code or config), all workspace safety is bypassed and the agent can write anywhere. The `file_read` tool also respects this flag.

**Impact:** Full filesystem write access when enabled.

**Recommended Fix:**
- Add an audit log entry when `allow_absolute=True` is configured.
- Consider requiring an env var override for this dangerous setting.
- The flag should be immutable after startup.

---

### 10. MEDIUM — Settings Export Leaks Exception Details

**File:** `kazma-ui/kazma_ui/settings.py`, lines 117–119

**Description:**
```python
except Exception as e:
    logger.error("Failed to export: %s", e)
    return Response(content=f"Error: {e}", media_type="text/plain", status_code=500)
```

Exception messages are returned directly to the client. This can leak internal paths, database connection strings, or other sensitive information.

**Impact:** Information disclosure of internal system details.

**Recommended Fix:**
```python
except Exception as e:
    logger.error("Failed to export: %s", e)
    return Response(content="Export failed", media_type="text/plain", status_code=500)
```

---

### 11. MEDIUM — Hub API Endpoints Lack Authentication

**File:** `kazma-core/kazma_core/hub/api.py`, lines 183–387

**Description:**
The Hub API (`/api/v1/health`, `/api/v1/skills`, `/api/v1/skills/submit`, `/api/v1/skills/{id}/download`) has **no authentication or rate limiting**. Anyone can:
- Submit skills (`POST /api/v1/skills/submit`)
- Download any skill tarball
- Search all skills

**Impact:** Unauthorized skill submission; potential supply chain attack vector.

**Recommended Fix:**
- Add API key or token authentication for write operations.
- Add rate limiting on submission endpoints.
- Validate and sanitize skill content before storage.

---

### 12. MEDIUM — WebSocket Endpoints Accept All Connections

**File:** `kazma-ui/kazma_ui/app.py`, lines 222–257; `kazma-ui/kazma_ui/chat.py`, lines 108–116

**Description:**
WebSocket endpoints (`/ws/dashboard`, `/ws/chat`) accept connections without any authentication. The auth middleware only gates HTTP requests, not WebSocket upgrades.

```python
@app.websocket("/ws/chat")
async def ws_chat(websocket: WebSocket) -> None:
    await websocket.accept()  # No auth check
```

**Impact:** Unauthorized access to real-time dashboard data and chat functionality.

**Recommended Fix:**
- Validate `X-Kazma-Secret` in the WebSocket query parameters or first message.
- Or check the header during the WebSocket handshake.

---

### 13. MEDIUM — `KAZMA_SECRET` Exposed in Jinja2 Templates

**File:** `kazma-ui/kazma_ui/app.py`, line 126

**Description:**
```python
templates.env.globals["kazma_secret"] = os.environ.get("KAZMA_SECRET", "")
```

The secret is embedded in every template's global context. While HTML templates use it for the HITL approval form, this means:
- The secret is present in the server-side template rendering context for every page.
- If any template accidentally renders `{{ kazma_secret }}` outside a JS context, it leaks.

**Impact:** Potential secret leakage to frontend.

**Recommended Fix:**
- Only pass the secret to the specific HITL approval template, not globally.
- Use a hash or one-time token instead of the raw secret.

---

### 14. LOW — `file_search` Tool Has Unbounded Regex Execution (ReDoS)

**File:** `kazma-core/kazma_core/agent/tool_registry.py`, lines 477–503

**Description:**
```python
async def file_search(pattern: str, path: str = ".", glob: str = "*.py", limit: int = 20):
    regex = re.compile(pattern)
    for file_path in root.rglob(glob):
        ...
            if regex.search(line):  # No timeout on regex
```

User-supplied regex patterns are compiled and executed without timeout. Malicious patterns like `(a+)+$` can cause catastrophic backtracking (ReDoS).

**Impact:** Denial of service via CPU exhaustion.

**Recommended Fix:**
- Use `re.search(pattern, line, timeout=5)` (Python 3.13+) or wrap in a thread with timeout.
- Or: restrict to simple substring matching.

---

## Positive Security Findings (What's Done Well)

| Area | Assessment |
|------|-----------|
| **YAML Loading** | ✅ All 25+ call sites use `yaml.safe_load()` — no insecure deserialization |
| **SQL Injection** | ✅ All database operations use parameterized queries (`?` placeholders) |
| **SSRF Protection** | ✅ Comprehensive `ssrf.py` with IP range checks, DNS rebinding prevention |
| **Timing-Safe Auth** | ✅ `hmac.compare_digest` used for secret comparison |
| **Error Messages** | ✅ Generic error responses in `app.py` exception handlers (no `str(exc)`) |
| **Code Execution Sandbox** | ✅ `python_exec` uses `-I` isolated mode, resource limits, temp directory |
| **Tool Sandbox** | ✅ `ToolSandbox` with deny-list and dangerous-pattern detection |
| **Security Linter** | ✅ AST-based linter detects hardcoded secrets, eval/exec, shell injection |
| **Audit Logging** | ✅ Comprehensive `AuditLogger` for RBAC decisions |
| **Workspace Scoping** | ✅ Standalone `file_write`/`file_read` properly scoping (but see Finding #2) |
| **shlex.quote Usage** | ✅ Used in swarm worker command construction |

---

## Dependency Audit

No `requirements.txt`, `pyproject.toml`, or `Pipfile` was found at the repository root (only `.venv/` exists). **This is itself a finding**: without pinned dependency versions, reproducible builds and vulnerability scanning are impossible.

**Recommended Fix:**
- Create a `pyproject.toml` or `requirements.txt` with pinned versions.
- Run `pip-audit` or `safety check` against the dependency list.

---

## Summary Table

| # | Severity | Category | File | Summary |
|---|----------|----------|------|---------|
| 1 | **Critical** | Command Injection | `tool_registry.py:591` | `shell_exec` runs arbitrary commands unsandboxed |
| 2 | **Critical** | Path Traversal | `tool_registry.py:436-503` | File tools bypass workspace scoping |
| 3 | **High** | Hardcoded Secret | `chat.py:207,214` | Dummy `sk-local-dev` API key |
| 4 | **High** | Command Injection | `swarm/worker.py:308` | `create_subprocess_shell` in worker |
| 5 | **High** | Data Exposure | `tool_registry.py:512` | `sqlite_query` accepts arbitrary DB path |
| 6 | **High** | Authentication | `auth.py:156` | All endpoints open when `KAZMA_SECRET` unset |
| 7 | **Medium** | CORS | `app.py:102` | `allow_headers=["*"]` with credentials |
| 8 | **Medium** | Safety Bypass | `hitl.py:75` | HITL can be silently disabled |
| 9 | **Medium** | Path Traversal | `file_write.py:91` | Workspace bypass when `allow_absolute=True` |
| 10 | **Medium** | Info Leakage | `settings.py:119` | Exception details in export response |
| 11 | **Medium** | Authentication | `hub/api.py` | Hub API lacks auth on write endpoints |
| 12 | **Medium** | Authentication | `app.py:222-257` | WebSocket endpoints accept unauthenticated connections |
| 13 | **Medium** | Info Leakage | `app.py:126` | `KAZMA_SECRET` in global template context |
| 14 | **Low** | ReDoS | `tool_registry.py:489` | Unbounded regex in `file_search` |
| — | **Medium** | Dependencies | repo root | No pinned dependency manifest found |

---

## Prioritized Remediation Plan

1. **Immediate** (Critical): Restrict `shell_exec` behind HITL or remove it; add workspace scoping to registry's built-in file tools.
2. **This Sprint** (High): Use `create_subprocess_exec` in swarm worker; restrict `sqlite_query` to known paths; generate random `KAZMA_SECRET` on first run.
3. **Next Sprint** (Medium): Restrict CORS headers; audit-log HITL disable; fix settings export error messages; add WebSocket auth; add Hub API auth.
4. **Backlog** (Low): Add regex timeout; create dependency manifest.
