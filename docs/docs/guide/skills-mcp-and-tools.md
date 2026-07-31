---
id: skills-mcp-and-tools
title: Skills, MCP & Tools
sidebar_label: Skills, MCP & Tools
description: Kazma Skills, MCP & Tools — code-audited reference (unified docs, v0.6.1+)
---
> Skill manifests, cryptographic signing, MCP transports, tool classification, the Hub, and how to extend Kazma with new tools — all source-referenced.

---

## 1. Concepts

| Term | Meaning |
|---|---|
| **Tool** | A function the supervisor can call (file ops, shell, memory, web, …). Registered in `ToolRegistry`. |
| **Skill** | A packaged, optionally-signed Python entry point + manifest that registers one or more tools. Lives under `kazma-skills/manifests/` or the Hub registry. |
| **MCP server** | An external Model Context Protocol server (stdio, SSE, or Streamable HTTP) whose tools are discovered at runtime and proxied into the agent. |
| **Hub** | The skill registry/marketplace (`kazma hub …`) with certification and signing. |

---

## 2. The ToolRegistry

`kazma-core/kazma_core/agent/tool_registry.py` is the registry the supervisor consults each turn. Key points:

- **`execute(tool_name, arguments)`** (`tool_registry.py:335`) — the single execution path. It:
  1. Pops `_hitl_approved` from args (line 349) — the double-gate flag.
  2. For danger tools, calls `await safety.check(...)` unless already approved (lines 384-417).
  3. **Fail-closed:** any exception in the safety check returns `is_error=True` "blocked — SafetyMiddleware unavailable" (lines 411-417).
- **Built-in tools** include `memory_search`, `memory_store`, filesystem/shell tools, and the **web research** set (`web_search`, `read_url`, `read_url_to_file`, `crawl_site`, chunk/digest helpers) registered at startup.
- **Memory tools** route through the V2 cognitive engine: `memory_search` → `recall()` (bi-temporal beliefs + PPR); `memory_store` → `mutate_belief()` + `mirror_episode()`. The legacy `set_vector_memory(...)` singleton was removed with the V1 stack.
- **Native skills** (e.g. `advanced-web-crawler`) auto-load via `NativeSkillLoader` — distinct from installable Agent Skills.

Web research playbooks: [Web research](web-research). Full list: [Tools catalog](../reference/tools-catalog).

See [Security & Safety](security-and-safety) for the danger-tool classification that governs execution.

---

## 3. Skills

### 3.1 Manifest location & discovery

- Config: `skills.path: kazma-skills/manifests/`, `skills.auto_discover: true` (`kazma.yaml:55-57`).
- On startup, the loader scans the path and loads each `skill_manifest.yaml`.

### 3.2 Manifest shape

A skill manifest declares the entry point, capabilities, and (when signed) integrity fields:

```yaml
# skill_manifest.yaml
name: my-skill
version: 1.0.0
description: "Example skill"
entry_point: my_skill.py        # Python file implementing the tool(s)
capabilities: [mcp, file_read]
author: your-org
# Added by `kazma hub sign`:
checksum: <sha256 of entry_point file>
signature: <HMAC-SHA256 of the checksum>
```

### 3.3 Cryptographic signing (HMAC-SHA256) — VERIFIED {#cryptographic-signing}

Skill signing is real, fail-closed, and lives in the Hub subsystem.

**Signing** — `kazma hub sign &lt;path>` (`kazma_core/hub/cli.py:703-770`):

```python
# Read the entry-point .py file
raw = py_file.read_bytes()
actual_hash = hashlib.sha256(raw).hexdigest()

# HMAC-SHA256 over the checksum, keyed by KAZMA_SECRET
signing_secret = secret or os.environ["KAZMA_SECRET"]
sig = hmac.new(
    signing_secret.encode(),
    actual_hash.encode(),
    hashlib.sha256,
).hexdigest()

# Write both into skill_manifest.yaml
manifest["checksum"] = actual_hash
manifest["signature"] = sig
```

Requires `KAZMA_SECRET` (env or `--secret`); exits if unset (`cli.py:728-733`).

**Verification on load (fail-closed)** — `kazma_core/hub/loader.py:206-266` (`SkillLoader._load_module_from_file`):

| Condition | Behavior |
|---|---|
| `checksum` present, mismatch | `SkillLoadError` — "may have been tampered with" (lines 227-232). |
| `signature` present, no `KAZMA_SECRET` | `SkillLoadError` (lines 237-241). |
| `signature` present, HMAC mismatch | `SkillLoadError` (lines 242-250). |
| No `checksum` at all | Warning logged; loads unsigned (backward compat, lines 251-257). |
| Any verification error | Fatal — not swallowed (lines 259-266). |

Verification uses `hmac.compare_digest` (constant-time) for both checksum and signature.

### 3.4 Adding a custom skill (minimal example)

1. Create `kazma-skills/manifests/my-skill/skill_manifest.yaml` + `my_skill.py`.
2. Implement the tool function(s) your skill exposes.
3. (Recommended) Sign it:

```bash
export KAZMA_SECRET="$(openssl rand -hex 32)"
kazma hub sign kazma-skills/manifests/my-skill
kazma hub validate kazma-skills/manifests/my-skill
```

4. Restart the server (or rely on `skills.auto_discover`). The loader verifies the signature with `KAZMA_SECRET` and refuses to load on mismatch.

---

## 4. The Hub (`kazma hub`)

The Hub is a Click-based CLI for the skill registry/marketplace (`kazma_core/hub/cli.py:104`). See [CLI Reference → hub](cli-reference#8-kazma-hub--skill-hub-click-group) for the full subcommand list.

### 4.1 Hub API authentication

Write endpoints (`kazma_core/hub/api.py:26-47`, `_require_auth`) require an `X-Kazma-Secret` header matched via `hmac.compare_digest`. **Fail-closed:** if `KAZMA_SECRET` is unset, all writes are rejected.

### 4.2 Certification

- `kazma hub certified` — list certified skills.
- `kazma hub badge &lt;skill_ref>` — show a certification badge.
- `kazma hub check-certification &lt;path>` — check a skill against certification criteria.
- The manifest carries a plain boolean `certified: true` flag (`manifest.py:87-90` `is_certified`).

> **"Trust tiers" do NOT exist as a cryptographic/security feature.** The only "trust" references in the codebase are (a) the plain `certified: bool` flag and (b) the `trust: trusted` string in `kazma.yaml` MCP config, which no code reads. This is explicitly flagged because older docs implied a tiered trust model.

### 4.3 Finding & installing skills (consumer workflow)

**Search** the registry by text, capability, tag, or author (`cli.py:171-234`):
```bash
kazma hub search "weather"
kazma hub search --capabilities "image_analysis,data_processing"
kazma hub search --tags "utility,beginner-friendly"
kazma hub search --author "kazma-team"
```

**Browse** installed skills and inspect one in detail (`cli.py:208-303`):
```bash
kazma hub list
kazma hub info author/skill-name
```

**Install** a specific version or the latest (`cli.py:234-266`):
```bash
kazma hub install author/skill-name@1.0.0
kazma hub install author/skill-name
```
Or use the interactive **skill-installation wizard** (`kazma_core/cli/wizard.py`, `main.py:117-123`):
```bash
kazma wizard
```

> **⚠ `hub install`/`hub update` are currently stubbed** — `registry.py:269` only updates a DB row and performs no real fetch. Verify the skill source out-of-band until the installer is fully wired.

---

## 5. MCP (Model Context Protocol)

`kazma-core/kazma_core/mcp/manager.py` discovers and proxies external MCP servers.

### 5.1 Transports

| Transport | Config | Auth |
|---|---|---|
| `stdio` | `command: [argv]` — subprocess spawn. | **None.** The subprocess inherits the process environment. |
| `sse` | `url` + optional `auth` field. | **Yes** — `AsyncMCPManager._connect_sse` supports a first-class `auth` config injecting `Authorization: Bearer &lt;token>` or a custom header. |
| `streamable_http` (alias `http`) | `url` + optional `auth` field. MCP **2025-03-26 spec** — single POST endpoint with SSE response streaming + `Mcp-Session-Id` resumption. | **Yes** — same `auth` field as SSE. |

> **There is no authentication inside `mcp/manager.py` for the stdio transport.** Run stdio MCP servers you trust, in a sandboxed environment.

### 5.2 Tool classification (`classify_mcp_tool`)

MCP tools are runtime-discovered, so they can't be on a static danger list. `classify_mcp_tool()` (`manager.py:71-88`) classifies by **name-pattern substring matching**:

| Category | Matched keywords |
|---|---|
| **`danger`** | `write, delete, remove, exec, run, shell, bash, command, kill, terminate, install, deploy, upload, download, fetch, request, post, put, patch` |
| **`safe`** | `read, list, search, get, info, status, check, describe, query, count, exists, help` |
| **`unknown`** | (neither set matched) |

The gate at `UnifiedToolExecutor.execute()` (`manager.py:725-727`) treats **both `danger` and `unknown` as requiring approval** — i.e. unknown defaults to danger (fail-safe).

### 5.3 Configuring an MCP server

```yaml
mcp:
  servers:
    - name: filesystem
      transport: stdio
      trust: trusted          # informational only — not enforced
      command:
        - npx
        - '-y'
        - '@modelcontextprotocol/server-filesystem'
        - kazma-data/workspace
    - name: secured-api
      transport: sse
      url: https://mcp.example.com/sse
      auth:
        type: bearer
        token: ${MCP_API_TOKEN}   # supply via env
    - name: remote-mcp
      transport: streamable_http   # MCP 2025-03-26 spec
      url: https://mcp.example.com/mcp
      trust: approval_required
  ide_server:
    enabled: true
    root: .
    max_file_size: 1048576
```

### 5.4 Adding MCP servers via the Web UI

The `/mcp` page provides a visual **Add Server** modal that replaces manual YAML editing. It has two modes:

**Quick add (preset)** — a dropdown of 85+ known MCP servers grouped by category (Filesystem, Web, Database, Code, AI, Communication, etc.). Pick one and the form auto-fills the name, transport, command, and env var keys. You just fill in the API key value. Presets are loaded from `certified_servers.yaml` (81 servers) plus 5 extra high-value servers (firecrawl, playwright, sequential-thinking, memory, time).

**Custom** — the same raw command form, with three safety nets:
1. **shlex-style parsing** — quoted args with spaces survive (`npx -y foo "path/with spaces"`).
2. **Auto-rewrite** — common install-command mistakes are detected and fixed:
   - `npm install -g firecrawl-mcp` → `npx -y firecrawl-mcp`
   - `pip install mcp-server` → `python -m mcp_server`
   - `pipx install some-mcp` → `pipx run some-mcp`
   A blue notice explains the rewrite so you know what changed.
3. **Validate-before-save** — before persisting, the server connection is tested via `/api/mcp/test-config`. If it fails (spawn error, 0 tools, bad key), the error + subprocess stderr is shown inline and the server is **not saved**. No more "0 tools, no idea why."

Servers added via the UI are persisted to `kazma.yaml` (atomic write) and survive restarts.

### 5.5 Tool name namespacing

MCP tool names are **namespaced** as `mcp__<server>__<tool>` before being sent to the LLM. This prevents collisions between MCP servers and built-in tools (e.g. the Playwright MCP's `browser_click` vs the browser_automation skill's `browser_click`). Without namespacing, providers that require unique tool names (DeepSeek, OpenAI) reject the entire request with `400 Tool names must be unique`, causing Kazma to strip ALL tools for the turn — the root cause of the "agent stopped talking" bug.

The namespace prefix is transparently stripped when routing the tool call back to the originating MCP server (`execute_mcp_tool` handles both namespaced and raw forms).

### 5.6 IDE server

The in-process IDE/file MCP server (`mcp.ide_server`) exposes file read/write over the workspace root with a 1 MB per-file cap (`max_file_size`). Per audit reports, it is expected to require `_secret` matching `KAZMA_SECRET` via `hmac.compare_digest`; verify against `mcp_server.py` before relying on it.

---

## 6. Secret Vault (encrypted credential storage)

The Secret Vault is a native skill that provides **encrypted-at-rest** storage for API keys, tokens, passwords, and other secrets. It uses AES-256-GCM encryption with a PBKDF2-derived key.

### 6.1 Architecture

| Component | File | Role |
|---|---|---|
| Vault engine | `kazma-core/kazma_core/security/vault.py` | AES-256-GCM encrypt/decrypt, PBKDF2 key derivation, SQLite storage |
| Skill manifest | `kazma-skills/kazma_skills/native/secret_vault/skill_manifest.yaml` | Native skill declaration |
| LLM tools | `kazma-skills/kazma_skills/native/secret_vault/tools.py` | `vault_store`, `vault_retrieve`, `vault_list`, `vault_delete` |

### 6.2 Security model

| Aspect | Implementation |
|---|---|
| **Master key** | `KAZMA_VAULT_KEY` environment variable. If unset, vault is disabled (all tools return graceful error). |
| **Key derivation** | PBKDF2-HMAC-SHA256, 600,000 iterations, per-installation 32-byte random salt. |
| **Encryption** | AES-256-GCM with 12-byte random nonce per record. Auth tag built into GCM. |
| **Storage** | Separate encrypted SQLite DB at `kazma-data/vault.db` (NOT the plaintext `settings.db`). |
| **Tenant isolation** | Uses `get_current_tenant_id()` ContextVar. Tenant-specific secrets + global fallback. |
| **HITL gating** | `vault_retrieve` and `vault_delete` require human approval before execution. |

### 6.3 Tools

| Tool | HITL? | Description |
|---|---|---|
| `vault_store(name, value, category, metadata)` | No | Store (or update) a secret. Encrypted before persistence. |
| `vault_retrieve(name)` | **Yes** | Retrieve and decrypt a secret. Returns `[SECRET — handle with care]\n&lt;value>`. |
| `vault_list()` | No | List all secret names + categories (values NOT shown). |
| `vault_delete(name)` | **Yes** | Permanently delete a secret. |

### 6.4 Enabling the vault

```dotenv
# .env
KAZMA_VAULT_KEY=your-vault-passphrase
```

Any string works — it's a passphrase, not a pre-derived key. The PBKDF2 derivation converts it into the 256-bit AES key.

### 6.5 Security note on retrieval

When a secret is retrieved (after HITL approval), the decrypted value enters the conversation context as a tool result. This means it will appear in:
- The chat history (message stream)
- The LangGraph checkpointer (`checkpoints.db`) if checkpointing is active
- Any enabled tracing (Langfuse)

This is by design — the LLM needs the value to make authenticated API calls. The HITL gate ensures a human approves each retrieval. Only retrieve secrets when actually needed.

---

## 7. Delegation (agent-to-agent) — library only (not runtime)

> **Status (2026-07):** the multi-agent **delegation** package is **archived /
> library-only**. Production multi-worker orchestration is **SwarmEngine**
> (`kazma_core/swarm/*`). See `docs/audits/UNWIRED_INVENTORY.md`.

Historical code (Ed25519 + AES-GCM protocol) may still exist under
`archive/delegation/` or as retained library modules for future product
decisions — it is **not** wired into the default agent / swarm execute path.
Do not configure production systems as if live cross-agent cryptographic
delegation is active.

---

## 8. Adding a new tool (extension point)

The simplest extension is a registered tool function. Minimal pattern:

```python
# my_tools.py
from kazma_core.agent.tool_registry import register_tool

@register_tool(
    name="weather_lookup",
    description="Look up current weather for a city.",
    danger=False,            # set True if it should trigger HITL
)
async def weather_lookup(city: str) -> str:
    # ... your implementation ...
    return f"Weather in {city}: sunny, 25C"
```

Register it during startup (or via a skill's entry point). The supervisor will expose it to the LLM as a callable tool. If `danger=True`, execution flows through the HITL gate (see [Security & Safety](security-and-safety)).

---

## Documentation Audit Notes

- **HMAC skill signing is real and fail-closed** — contrary to what one might assume from the mix of subsystems, the loader genuinely refuses tampered/unsigned-by-required skills.
- **"Trust tiers" are NOT a code feature.** Documented explicitly to counter any implication of a tiered trust model. Only a boolean `certified` flag and an unused `trust: trusted` string exist.
- **MCP stdio transport has no auth.** SSE and Streamable HTTP support bearer/custom-header auth; stdio inherits the process environment. This is a meaningful security boundary for production planning.
- **`classify_mcp_tool` unknown → danger** is the safe default and should be preserved.
