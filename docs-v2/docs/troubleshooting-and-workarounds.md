# Troubleshooting & Workarounds

> Practical fixes for the issues you will actually hit: provider limits, Windows/Docker specifics, SQLite concurrency, Arabic tokenization edge cases, and the known codebase gaps the audit uncovered. Every item is source-referenced.

---

## 1. Provider & LLM issues

### 1.1 "Function not found" / 404 on tool calls (NVIDIA NIM)

**Cause:** Some providers (notably NVIDIA NIM) reject tool definitions.

**What Kazma does:** `llm_provider.py:285-300` detects `status_code == 404 and "function" in detail` (and 400/422 with tool/function language) and **retries once without tools**. The caller still gets a text response.

**Workaround if it persists:** Disable tools for that provider, or route through an OpenAI-compatible proxy that translates tool definitions. **Do not remove the fallback branch** — it's a documented invariant.

### 1.2 Provider/model mismatch (wrong endpoint)

**Symptom:** Requests go to the wrong API endpoint after switching models.

**Cause:** Changing the model without switching the provider.

**Fix:** Always use `set_active_model()` (it auto-switches the provider via `find_provider_for_model()`) or `set_active_provider()` — never set one without the other. `get_client()` auto-corrects (`model_registry.py:275-303`) but only persists the provider change when `model is None`.

**Verify:**
```bash
kazma status          # shows active provider/model
```

### 1.3 Anthropic auth header mismatch

**Cause:** The Anthropic preset declares `auth_header: x-api-key`, but `LLMProvider.chat()` always sends `Authorization: Bearer` (`llm_provider.py:171-172`). The preset header only takes effect during `discover_models()`.

**Fix:** Route Anthropic through an OpenAI-compatible proxy, or extend `LLMProvider` to honor the preset `auth_header` for chat.

### 1.4 No rate-limit (429) handling

Kazma has **no 429 backoff**. The retry layer (`retry.py:107-109`) explicitly does not retry 4xx. If you hit rate limits, either:
- Lower concurrency (`BoundedConcurrency`, default 5), or
- Put a rate-limiting proxy in front of the provider.

### 1.5 Cost breaker not halting runaway spend

**Cause:** `CostCircuitBreaker` (default $0.50, 5-min silence) is a standalone dataclass — **not auto-wired** into `chat()`. The agent layer must call `record_cost` / `record_user_interaction` / `should_halt`.

**Fix:** Wire the breaker in your agent loop, or set `KAZMA_MAX_COST` / `KAZMA_SILENCE_WINDOW` and drive it explicitly.

---

## 2. Memory & RAG issues

### 2.1 "My agent doesn't seem to remember anything"

**Root cause (most likely):** memory retrieval is **not automatic**. The chat agent only retrieves when the LLM voluntarily calls `memory_search`. There is no injection of retrieved context into the prompt, and no short-term→permanent consolidation. See [Memory & RAG → Honest status](memory-and-rag.md#7-honest-status-notes-read-before-relying-on-memory-features).

**Workarounds:**
- Instruct the model (via `system_prompt`) to call `memory_search` for relevant queries and `memory_store` for important facts.
- For automatic retrieval during long conversations, note that **compaction's memory-retrieval step is a no-op** in the default wiring (`agent_runner.py:162-166` passes no `memory_store` to `create_authority`). Only LLM summarisation runs.

### 2.2 `distance(embedding, ?)` errors from `search_backend.py`

**Cause:** `_vector_search` (`search_backend.py:343,354`) calls `distance(...)`, which is **not a valid sqlite-vec function**. The path throws and returns `[]` from the `except`.

**Why you probably haven't seen it:** callers don't pass `semantic_search=True` with an embedding. The `SQLiteMemoryBackend` (agent `self.memory`) isn't queried during chat retrieval anyway.

### 2.3 `sqlite-vec` "not loaded" despite being installed

**Cause:** `search_backend.py:55-60` runs `SELECT sqlite_version()` (always succeeds) and never calls `load_extension`, so `_vec_available` is unreliable. The correct pattern (used in `swarm/memory/sqlite_vec.py:73-89`) is `conn.load_extension("vec0")` + a `SELECT vec_version()` probe.

### 2.4 Embedding dimension mismatch

`kazma.yaml` declares `storage.vector_dim: 1536`, but the actual model (`all-MiniLM-L6-v2`) is **384-d**. The 4-layer sqlite-vec store correctly uses `FLOAT[384]`. Don't rely on the yaml value for dimension logic.

### 2.5 Long documents retrieved poorly

**Cause:** there is **no chunking strategy**. Each `memory_store` call embeds one whole document.

**Fix:** pre-split long text in your skill/tool before calling `memory_store`.

---

## 3. HITL / safety issues

### 3.1 Danger tools execute without approval

**Check 1 — is the graph gate active?** All production build sites pass `hitl_config` (see [Security & Safety §2.2](security-and-safety.md)). If you wrote a custom build via `create_supervisor_graph()` without `hitl_config`, the gate is **dormant**.

**Check 2 — is the tool on the right list?** There are **three** lists:
- Graph path: `kazma.yaml safety.hitl.require_approval_for`.
- Swarm bus: `_EXTENDED_DANGER` (`swarm/safety.py:23`).
- MCP: pattern-based `classify_mcp_tool`. Adding to one does **not** add to the others.

**Check 3 — is `allow_headless_danger=True` set?** That disables fail-closed in headless/test mode. Ensure it's `False` in production.

### 3.2 HITL pauses never resume

**Cause:** the resume endpoint is `POST /api/approve/{thread_id}` in `routes_direct.py:454` (not `app.py`). It requires `KAZMA_SECRET` if set, and enforces ownership (403 on cross-user).

**Fix:** ensure the approving caller has the matching identity fields and the correct secret.

### 3.3 Pipeline tasks stay "paused" after restart

**Cause:** `restore_paused_tasks()` re-arms auto-reject timeouts on startup (`checkpoint_manager.py:222-230`). If the approval never arrives within `checkpoint_timeout`, the task is auto-rejected.

**Fix:** approve before the timeout, or raise `checkpoint_timeout` in task metadata.

---

## 4. SQLite concurrency

### 4.1 "database is locked"

All Kazma stores use `PRAGMA busy_timeout=5000` + WAL. If you still see lock errors:

- **One writer at a time.** WAL allows concurrent readers but a single writer. Long write transactions block others for up to 5 s.
- **Use `batch_set()` for multi-key writes** (atomic, single transaction) rather than looping `set()`.
- **Always use `get_config_store()`** — constructing `ConfigStore()` directly bypasses the singleton and can open a second connection.
- **Don't share a connection across processes.** Kazma is single-process; if you fork workers, each gets its own connection and they'll contend on the same DB file.

### 4.2 In-memory fallback silently active

If SQLite init fails, `get_config_store()` returns an `_InMemoryStore` (TTL eviction, 1-hour, 10k entries). **Settings then don't survive a restart.** Check startup logs for SQLite init errors.

---

## 5. Windows specifics

### 5.1 PowerShell command chaining

Never use `&&` or `||` in PowerShell. Use `;` and check `$LASTEXITCODE` (per AGENTS.md):

```powershell
& '.venv\Scripts\python.exe' -m pytest kazma-core/tests/ -v
if ($LASTEXITCODE -ne 0) { Write-Error "tests failed" }
```

### 5.2 Path length / non-ASCII paths

The repo path `G:\GitHubRepos\kazma` and Arabic content in `kazma.yaml`/templates are fine, but some Windows tools choke on long paths or non-ASCII. If you hit `FileNotFoundError` on deeply nested test temp dirs, enable long paths (`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled = 1`).

### 5.3 The `.pytest_tmp_*` directories

The repo root contains many `.pytest_tmp_*` directories (artifacts from test runs). They are git-ignored clutter; safe to delete: `Remove-Item -Recurse -Force .pytest_tmp_*`.

---

## 6. Docker specifics

### 6.1 Vector volume path mismatch

`docker-compose.yml` mounts `kazma_vectors:/root/.kazma/vector_memory`, but the container runs as user `kazma` (home `/home/kazma`). Set `KAZMA_VECTOR_PATH=/app/kazma-data/vector_memory` (aligned with the data volume) to avoid writes to an unmounted path.

### 6.2 OOM with RAG extras

ChromaDB + sentence-transformers can exceed 512 Mi. The K8s manifest's `512Mi` limit is too small for the main agent with RAG. Use ≥1 Gi for the main agent container.

### 6.3 Health check path

Use `/api/gateway/status` (as `docker-compose.yml` does) or `/health/live` — **not** `/api/v1/health` (which belongs to the separate Hub API).

---

## 7. Arabic tokenization edge cases

### 7.1 Conflicting hamza rules

`ؤ` and `ئ` are normalized by **two** overlapping rules in `arabic_tokenizer.py` (Yeh normalization at lines 220-232 and the Waw/Ya-Hamza rules at lines 154-157). This is a known minor conflict; in practice the later rule wins. If you see inconsistent search results for hamza-bearing words, this is why.

### 7.2 Stemmer is basic

The stemmer (`_init_stemmer`, lines 104-130) does regex suffix/prefix stripping only — it's **not** a lemmatizer. Plural/gender variants may not collapse to a common stem. For higher recall, consider pre-normalizing queries or adding domain synonyms.

### 7.3 Tatweel (ـ) in stored text

Tatweel is stripped on tokenize, so stored `content_arabic` won't contain it — but if you query with raw text containing `ـ`, the same normalization applies, so matches still work.

---

## 8. Framework / build quirks

### 8.1 Version drift

Three independent version strings:
- `pyproject.toml`: `0.3.0`
- `kazma.yaml agent.version`: `0.2.0`
- CLI `--help` text: `v0.2.0`

They are **not** synced. Don't rely on any single one for "the Kazma version."

### 8.2 `models.router: litellm` does not import LiteLLM

The string `"litellm"` only gates the fallback-model branch (`llm_provider.py:336`). Kazma never `import litellm`. If you point at a LiteLLM proxy, use `http://host:4000/v1` as `base_url`.

### 8.3 `.env.example` lists unused env vars

`DEEPSEEK_API_KEY` and `ANTHROPIC_API_KEY` appear in `.env.example` but **no code reads them**. Key those providers via the ConfigStore provider list instead. Don't waste time wondering why setting them has no effect.

### 8.4 `mcp.servers[].trust` is a no-op

The `trust: trusted` string in `kazma.yaml` MCP config is **not read by any code**. It's documentation-only. "Trust tiers" are not a code feature.

### 8.5 `/undo` and `/edit` are stubs

Both slash commands exist in the help system but are explicitly "not yet implemented" (`slash_commands.py:257, 267`). Don't advertise them to users.

### 8.6 `UnifiedModelRegistry` is just `ModelRegistry`

The alias (`model_registry.py:950`) exists for backward compatibility. They are the same class.

---

## 9. Diagnostics checklist

When something is wrong, run through these:

```bash
# 1. Is the server up? Versions sane?
kazma status

# 2. Health
curl -s http://127.0.0.1:8000/health/details | jq

# 3. Active provider/model
curl -s http://127.0.0.1:8000/api/provider/active | jq

# 4. Gateway adapters
curl -s http://127.0.0.1:8000/api/gateway/status | jq

# 5. Swarm status + breaker states
curl -s http://127.0.0.1:8000/api/swarm/status | jq
curl -s http://127.0.0.1:8000/api/swarm/circuit-breakers | jq

# 6. Pending HITL approvals
curl -s http://127.0.0.1:8000/api/pending-approvals | jq
```

Enable JSON logging for structured diagnostics:

```yaml
logging:
  level: DEBUG
  format: json
```

---

## Documentation Audit Notes

This file consolidates the **actionable** findings from the whole audit. The most important gotchas for new operators:

1. **Memory is opt-in (§2.1)** — don't assume recall.
2. **Three HITL lists (§3.2)** — adding a danger tool in one place isn't enough.
3. **`KAZMA_SECRET` gates approval auth (§3.2)** — set it always.
4. **K8s manifests deploy the wrong thing (Deployment §4)** — don't apply them for the main agent.
5. **No 429 handling (§1.4)** — proxy or throttle externally.
