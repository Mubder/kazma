# Industry Stack Audit — 2026-08-25

**Product version:** 0.9.4 at audit; public base **0.10.0** after the 2026-08-25 freeze landed.  
**Date:** 2026-08-25  
**Scope:** Full-stack industry-level gap analysis of every major Kazma subsystem
against 2026 world-class agent products (Claude Code, OpenAI Codex, Cursor,
Devin) and the production agent stack (LangGraph, LiteLLM/Portkey, pgvector/
Qdrant, E2B/Firecracker, Temporal, Langfuse/Braintrust, LiveKit).  
**Method:** Live source inspection of kazma-core / gateway / ui / tui / cli /
skills + existing audits + 2026 market comparison. Three parallel deep passes
(brain/LLM, memory/swarm/safety/IDE/docs, UI/gateway/auth/ops).  
**Not a code-fix audit.** Structural invariants were last verified in
[`AUDIT_DEEP_STRUCTURE_2026-08-19.md`](AUDIT_DEEP_STRUCTURE_2026-08-19.md).
Memory findings M-01..M-17 were closed in
[`AUDIT_MEMORY_SYSTEM_2026-08-24.md`](AUDIT_MEMORY_SYSTEM_2026-08-24.md).
July 21 production/security audit is historical:
[`AUDIT_PRODUCTION_READINESS_2026-07-21.md`](AUDIT_PRODUCTION_READINESS_2026-07-21.md).

**How to read the labels**

| Label | Meaning |
|-------|---------|
| **Keep** | Already the right idea. Do not rewrite. |
| **Upgrade** | Keep the design, replace the weak engine inside it. |
| **Replace** | This part will force a rebuild later if you leave it. Pick once. |

---

## 1. Honest score

Kazma is already a **serious, battle-tested agent operating system for one
trusted operator**. It is **not** yet a 2026 world-class agent product in the
same class as Claude Code, Codex, Cursor, or Devin.

That is not an insult. Most of the brain, safety, memory, and delivery work is
**better than typical open-source agent kits**. The gap is that several layers
were built in-house when the industry now has a clear “never rebuild this”
choice — and a few parts will **fail under real production load or multi-user
SaaS**, even if they work well on one machine.

| If the goal is… | Verdict |
|-----------------|---------|
| Best self-hosted personal / team agent on one machine | **Strong. Keep building here.** |
| One of the top AI agents in the world (Claude Code / Codex / Cursor class) | **Not yet. Several layers must be upgraded or replaced, not patched.** |
| Public multi-user SaaS / many replicas | **Will not hold.** Docs already say this; the code still says it. |

The product today is closest to: **LangGraph brain + operator cockpit
(Web/TUI/Telegram) + unusually deep safety and memory.** World-class 2026
products split that into a streaming harness, a durable runtime, a real
editor/CLI, and a cloud control plane. Kazma still tries to be all of those
in one Python process.

---

## 2. What is already world-class (do not replace)

These are the parts you would be stupid to throw away:

1. **LangGraph supervisor as the brain.** In 2026 LangGraph is still the
   production standard for stateful agents with interrupts, checkpoints, and
   human approval (Uber, LinkedIn, Klarna). Do not switch to CrewAI, AutoGen,
   or a custom loop.
2. **Three HITL gates + the commitment layer.** Graph interrupt, swarm bus
   (fail-closed), pipeline checkpoints, plus “don’t invent a date and overwrite
   memory.” Most famous agents are weaker here.
3. **Honest failures.** When the model dies, Kazma sets `turn_failed` and does
   **not** fake an answer. That bug class (“model stopped thinking”) is still
   common in the industry.
4. **Platform isolation.** Telegram/Discord/Slack IDs never enter graph state.
   That is correct forever.
5. **V2 memory as a cognitive model.** Beliefs, episodes, source-trust, prompt
   fencing, hybrid recall. This is ahead of Claude Code / Cursor (they mostly
   use chat history + notes). Do **not** replace this with Mem0/Zep and call
   it done.
6. **Turn Delivery V2** on web chat (cursor resume, event journal). This is the
   Discord/SSE-spec pattern. Keep it.
7. **Document chain-of-custody** (quarantine, job state machine, sandbox,
   ClamAV, fences). Keep the platform; only upgrade extractors.
8. **Agent Skills (`SKILL.md`).** That is now the industry format (Claude,
   Codex, Cursor). Keep it.
9. **Migration + vault pairing.** Vault key and `vault.db` travel as a pair.
   That is a real production lesson. Keep it.

---

## 3. Part-by-part: keep / upgrade / replace

Every row is “what we have” → “what the top of the market uses today” →
“what to freeze as our final choice.”

### 3.1 Agent brain (the loop)

**Have:** One LangGraph graph: Supervisor ↔ Tools → Respond.
`graph_builder.py` is ~3,200 lines. The supervisor function itself is ~1,500
lines and does intent, memory, trimming, failover, language lock, and the LLM
call in one place.

**Industry bar:** Claude Code / Codex are **streaming-first**: tokens appear
immediately, tools stream, you can cancel mid-token. The loop is thin. Context
is a stable prefix so the provider can **prompt-cache**.

**Missing / weak:**

- **No real token streaming.** `LLMProvider.chat()` posts a full
  `/chat/completions` with **no `stream: true`**. The UI “streams” LangGraph
  events, then pastes the final answer. HITL resume even has to use `ainvoke`
  because there are no model-stream events (`sse_chat.py`). This is a
  **replace-the-I/O-layer** problem, not a UI bug.
- **No provider prompt cache.** Zero `cache_control` / cached tokens. System
  notes are reshuffled every turn (`hoist_system_messages`). That kills
  Anthropic/OpenAI prefix cache. You will burn money forever until this
  changes.
- **God file.** You cannot add streaming, caching, or evals cleanly while the
  whole brain is one function.
- Fake “Please proceed automatically…” user messages pollute history.
- Live compaction **drops the middle of the conversation**. The smarter LLM
  summarizer (`compaction.py`) exists and is **not** on the hot path.

| Piece | Call | Best option (freeze this) |
|-------|------|---------------------------|
| LangGraph + HITL + `turn_failed` | **Keep** | LangGraph 1.x + Postgres checkpointer |
| `graph_builder.py` as one file | **Upgrade** | Split into prompt / LLM call / route / tool worker / respond. Same policies. |
| LLM HTTP client | **Replace I/O** | Official SDKs **or** one gateway (below), with **true SSE streaming** |
| Mid-turn trim | **Upgrade** | Restore semantic compact on overflow; keep deterministic trim as the cheap first cut |

**Do not replace the brain with Claude Agent SDK or OpenAI Agents SDK.** Those
lock you to one lab. LangGraph is the durable choice.

Keep the policies (`turn_failed`, commitment-before-HITL, loop-kill, sanitize).
Replace the container, not the flight computer.

---

### 3.2 Models / providers / routing

**Have:** Custom httpx clients for OpenAI-compatible, Anthropic, Gemini, Azure,
Bedrock. Auto-corrects “wrong provider for this model.” Failover + retries.
YAML even says `router: litellm` — **LiteLLM is not actually the gateway**.
That label is a leftover.

**Industry bar (2026):** One **LLM gateway** in front of every call: routing,
budgets, fallbacks, semantic cache, spend caps. Self-hosted winner: **LiteLLM**.
Managed winner: **Portkey**. Zero-ops: OpenRouter (not for data-residency).

| Piece | Call | Best option |
|-------|------|-------------|
| Four-branch registry (Google/Anthropic/Azure/Bedrock/else) | **Keep** | Your registry, talking **through** a gateway |
| Hand-rolled `/chat/completions` | **Replace** | **LiteLLM** (self-hosted, data stays yours) as the only egress. Portkey only if you want a managed SaaS gateway. **Addressed 2026-08-25 (optional, not exclusive):** `llm_gateway.py` + `KAZMA_LITELLM_URL` for generic OpenAI-compat only; four-branch native stays direct. |
| Keyword task router (`"code"` → coding model) | **Replace** | Small classifier **or** explicit `models.defaults.<kind>` (you already have the second; make it the only router). Keyword lists will misroute forever. |
| Semantic response cache (optional, identity-unsafe) | **Replace** | Provider **prompt cache** on a stable system prefix. Not “cache this whole chat.” |

**Freeze:** LiteLLM proxy as the one pipe. Kazma registry still decides *which*
model. Never let the graph talk HTTP by hand again.

---

### 3.3 Tools, sandbox, computer use

**Have:** In-process tool registry (~3,000 lines, tools inlined). Docker jail
for Python (`--network none`). After one HITL approve, `shell_exec` is still
**host power**. Browser = Playwright on one shared page. No Claude-style
computer-use loop. No lifecycle **hooks** (PreToolUse / PostToolUse). Tool JSON
schemas are generated from type hints — sloppy for strict-mode models.

**Industry bar:** Untrusted code runs in **Firecracker microVMs** (E2B).
Editors apply **patches**, not whole-file writes. Computer use is a first-class
screenshot→action loop. Hooks are how products stay programmable.

| Piece | Call | Best option |
|-------|------|-------------|
| Single execute chokepoint + HITL | **Keep** | Never add a second write/exec path |
| Docker `python_exec` for one trusted operator | **Keep for now** | Docker + network=none is fine on one box |
| Same Docker jail for **multi-user / untrusted code** | **Replace** | **E2B** (Firecracker). Self-host Firecracker only if you must. Daytona/plain Docker is weaker isolation. |
| Host `shell_exec` after one approval | **Upgrade** | Workspace-scoped, allowlist forever; for SaaS, shell runs **inside** the microVM, not on Kazma’s host |
| Playwright browser tools | **Upgrade** | Keep Playwright for “open this page.” Add a real **computer-use** path (Anthropic CUA / Gemini Computer Use) as a separate tool family. **Addressed 2026-08-25:** `computer_use` screenshot→action loop (HITL); Playwright remains the actuator. |
| Schema generation | **Upgrade** | Strict JSON Schema, `additionalProperties: false`. **Addressed 2026-08-25:** closed object schemas always; `KAZMA_STRICT_TOOLS=1` for OpenAI `function.strict`. `response_format` on `LLMProvider.chat` (not on every supervisor turn). |
| Whole-file `file_write` | **Upgrade** | Search-replace / apply-patch (Aider/Morph style). This is how Claude Code and Cursor stay accurate |
| Hooks | **Missing — add** | Claude Code–style PreToolUse / PostToolUse. Do not invent a third permission system. **Addressed 2026-08-25:** `agent/tool_hooks.py` on `execute()` + MCP. Deny/rewrite/observe. Cannot auto-approve HITL. `KAZMA_TOOL_HOOKS=0`. |

---

### 3.4 Memory

**Have:** A real cognitive engine (beliefs, episodes, PPR, fencing, nightly
backup). Default vectors = **sqlite-vec** on SQLite. Optional Qdrant/pgvector
exist but are not the default primary. Recall only looks at a **capped**
candidate set (~400), not the full corpus.

**Industry bar:** Under ~10M vectors, **pgvector on Postgres** is the 2026
default. Qdrant if you need speed + self-host. Pinecone if you want zero ops.
Chroma is a **dev** store, not a production memory plane.

Honest production ceiling today: **one operator / one process / ~10⁵
beliefs+episodes**. SQLite WAL is fine for a laptop/server agent. Quality falls
off past hundreds of high-importance beliefs unless you cut over to
Qdrant/pgvector **and** keep embeddings current. Postgres-primary recall is
ILIKE assist, not pgvector+FTS fusion.
   **Addressed 2026-08-25:** Postgres-primary recall fuses ILIKE sparse with
   pgvector dense (RRF). sqlite-vec remains the one-node default.

| Piece | Call | Best option |
|-------|------|-------------|
| Belief / episode / fence design | **Keep** | Do not replace with Mem0 |
| sqlite-vec default | **Upgrade for scale** | **pgvector** as primary when you already run Postgres. **Qdrant** if recall latency becomes the bottleneck. |
| Chroma leftover | **Do not grow** | Leave as optional KB fossil; do not build on it |
| Local `BAAI/bge-m3` embeddings | **Upgrade at fleet scale** | Hosted embed API (OpenAI / Cohere / Voyage) for multi-replica; local is fine for one box |
| Golden memory tests | **Keep and grow** | This is the right eval seed |

**Freeze:** Cognitive model stays Kazma. Retrieval engine becomes **Postgres +
pgvector** (the adapter already exists). Do not adopt Mem0/Letta as the brain.

---

### 3.5 Swarm / long work / cron

**Have:** Rich in-process swarm (pipeline, fan-out, breakers, handoff caps,
autoscaler). Workers are **the same Python process**. Crash = in-flight work
dies. Cron is SQLite, poll every 30s, 4 jobs at a time. Fine for reminders.
Not a job platform.

**Industry bar:** Codex/Devin run **cloud VMs**. Long workflows use **Temporal**
(or Inngest) so a crash resumes, not restarts.

| Piece | Call | Best option |
|-------|------|-------------|
| Swarm *planning* (DAG, HITL, breakers, phonebook) | **Keep** | This is yours |
| Swarm *execution* (in-process asyncio) | **Upgrade if you want cloud agents** | Temporal (or Inngest) for durable steps + **one isolated runtime per worker** (E2B or a container) |
| Personal cron / `schedule_task` | **Keep** | `delivery_target`-at-schedule-time is correct |
| SaaS job platform | **Replace** | Temporal / Cloud Scheduler. Do not grow `cron.db` into that |

**Do not** replace the swarm planner with CrewAI.

---

### 3.6 IDE / coding agent

**Have:** Workspace RPC + HITL. Web editor is **CodeMirror 5 from a CDN**
(2010s editor). No LSP, no tree-sitter, no codebase index, no multi-file
apply, no ACP. TUI is an **ops dashboard**, not Claude Code. CLI is
installer/`serve`, not `kazma ask`.

**Industry bar:** Claude Code (deepest harness), Cursor (in-editor), Codex
(async cloud). Monaco or native editor. Codebase index. ACP if you want other
IDEs to plug in.

This is the **largest product gap** if “top AI agent in the world” means
coding.

| Piece | Call | Best option |
|-------|------|-------------|
| IdeService as the only mutate path | **Keep** | |
| CodeMirror 5 CDN | **Replace** | **Monaco** (VS Code engine) or don’t ship a fake IDE |
| No index / LSP / apply-patch | **Upgrade** | tree-sitter + ripgrep + embeddings for search; LSP diagnostics; patch apply. **Addressed 2026-08-25:** index + apply-patch + IDE LSP façade (`POST /api/ide/lsp`). |
| TUI | **Keep as ops console** | Do not market it as a coding CLI |
| CLI | **Upgrade or stop implying** | If you compete with Claude Code: a real `kazma` agent CLI with streaming JSON. If not: keep CLI as serve/migrate/update only |
| ACP | **Missing — add later** | Agent Client Protocol so Cursor/VS Code can host Kazma. Don’t invent a private protocol. **Addressed 2026-08-25:** `kazma acp` stdio JSON-RPC (`initialize` / `session/new` / `session/prompt` / `session/update` / `session/request_permission`). `kazma ask` streams tokens and prompts HITL on a TTY. |

**Freeze:** Kazma remains the **agent + HITL + workspace**. The editor chrome
should be Monaco (or a hosted Claude-Code-style terminal). Do not try to
out-Cursor Cursor with Alpine + CodeMirror 5.

---

### 3.7 Web UI / chat / frontend

**Have:** FastAPI + Jinja + Alpine. `chat.js` ~4,100 lines. Two live chat
pipes: **SSE (primary) and WebSocket (still mounted)** even though a comment
in `chat.py` says WS was removed. Turn Delivery V2 is excellent. No
TypeScript, no bundler, no Playwright in CI.

**Industry bar:** Typed SPA, virtualized message list, one transport, e2e tests
on every PR.

| Piece | Call | Best option |
|-------|------|-------------|
| FastAPI API | **Keep** | FastAPI is still the right Python web layer |
| Settings / ops pages in Alpine | **Keep** | Fast enough for operators |
| Chat + IDE JS | **Upgrade** | Split, TypeScript, virtualize the transcript |
| Dual SSE + WS | **Replace (pick one)** | **SSE + Turn Delivery V2** is already the right contract. Retire live WS chat or you will keep fixing the same bugs twice |
| Full React/Next rewrite | **Only if you sell cloud SaaS** | Incremental upgrade is cheaper for self-host |

God-file class on the web side: `chat.js` (~4.1k), `sse_chat.py` (~2.5k),
`routes_direct.py` (~3.2k), `app.py` (~1.9k), `ws_chat.py` (~2.4k).

---

### 3.8 Gateway (Telegram / Discord / Slack)

**Have:** Real adapters, HITL buttons, voice notes, platform isolation.
In-memory queue of 100. Polling **and** Telegram webhook can both be live. No
durable outbox. Session TTL 5 minutes (reminders must snapshot the destination
at schedule time — already done; don’t “simplify” that).

| Piece | Call | Best option |
|-------|------|-------------|
| Adapter split + isolation | **Keep** | |
| Polling as default (no public IP) | **Keep** | Superpower for self-host |
| Poll + webhook together | **Upgrade** | XOR. Never both on one bot token |
| Outbound | **Upgrade** | Durable **outbox** in Postgres (retry, dead letter). Web has this; chat apps don’t |
| New platforms (Teams / WhatsApp) | After outbox | Don’t add mouths until send cannot vanish on crash |

---

### 3.9 Auth, tenants, SaaS

**Have:** Shared secret, opaque sessions, local users, OIDC with PKCE, CSRF
host check, in-process rate limits. Docs honestly say “foundation, not a cloud
product” (`docs/docs/products/multi-user-saas.md`). Tenant isolation on memory
is **off by default**.

**Production-unsafe today:**

- OIDC **falls back to an unverified id_token** if JWKS verify fails
  (`security/oidc.py` — `claims = await _verify_id_token(...) or claims`).
  That is not acceptable for SSO. Fail closed.
- `KAZMA_DEMO_MODE` turns auth off.
- No MFA, no passkeys, no SCIM, no per-tenant memory/files/cron by default.
- Rate limits are per-process (each replica has its own counter).

| Piece | Call | Best option |
|-------|------|-------------|
| Opaque sessions + RBAC + CSRF | **Keep and harden** | |
| OIDC verify fallback | **Replace that line** | Fail closed. Then keep your OIDC; don’t buy Auth0 unless you want their dashboard |
| Multi-tenant data | **Upgrade** | Tenant on **every** store, default **on** in production |
| MFA / passkeys | **Add** | WebAuthn. Don’t wait for a rewrite |
| “We are a SaaS” | **Don’t claim it** until isolation is default-on |

For a public cloud, the industry default is **Clerk or Auth0 + your RBAC**.
For self-host / private team, **your OIDC + passkeys** is the right freeze —
after the fail-closed fix.

---

### 3.10 Data / HA / deploy

**Have:** SQLite WAL by default (correct for one node). Postgres for *some*
shared state. HA compose **does not share `kazma-data`**. Memory, cron, vault,
documents, research, turn journal can stay on one box. Nginx HA config has
**no WebSocket upgrade** and **no sticky cookie**. Kubernetes folder is the
**Hub**, not the agent.

**On Postgres when `KAZMA_DATABASE_URL` is set:** settings, chat sessions,
swarm tasks/metrics, LangGraph checkpoints, platform users, web sessions,
document **jobs** (metadata only if `KAZMA_DOCUMENTS_METADATA_BACKEND=postgres|auto`).

**Still SQLite:** memory (`memory_state.db` / `memory_ops.db`), cron, snapshots,
vault, research sessions, vector memory, document blobs + default metadata,
turn journal (process RAM).

| Piece | Call | Best option |
|-------|------|-------------|
| SQLite for single-node | **Keep** | Best default on earth for one operator |
| Partial Postgres | **Upgrade until complete, or don’t do HA** | One rule: **if it must survive a replica hop, it lives in Postgres (or object storage)** |
| Document blobs / attachments | **Upgrade** | S3-compatible (MinIO self-host, or R2/S3) |
| k8s Hub manifests in the main tree | **Replace/quarantine** | Don’t look like you support k8s for the agent if you don’t |
| HA nginx | **Upgrade** | Sticky sessions **or** shared turn journal in Redis/Postgres; WebSocket map |

**Freeze:** Single-node = SQLite. Multi-replica = **Postgres for all shared
state + object storage for files**. No half-HA.

---

### 3.11 Observability and evals

**Have:** Prometheus metrics, `/health/deep` (real canaries — good), optional
Langfuse (off by default), SQLite LLM call ledger. Memory golden tests. **No**
trajectory evals. **No** prompt-change CI gate. Ruff/Bandit **do not fail CI**.
Playwright e2e **not in CI**.

LangChain’s own 2026 survey: most teams have traces; **only about half have
evals**. Skipping evals is how “demo that dies in week three” happens.

| Piece | Call | Best option |
|-------|------|-------------|
| `/health/deep` + Prometheus + llm_ledger | **Keep** | |
| Langfuse | **Upgrade to always-on in prod** | **Langfuse** if you self-host traces+scores. **LangSmith** if you already live in LangChain Cloud. **Braintrust** if evals-first SaaS. Pick **one**. **Addressed 2026-08-25:** `enabled: auto` turns Langfuse on when keys exist (`KAZMA_LANGFUSE=0` off). |
| Product eval harness | **Missing — add** | Golden tool-traces in CI (Promptfoo or Langfuse datasets). No merge if the supervisor regresses |
| CI lint advisory | **Upgrade** | Gate on tests (you do) **and** a small eval pack. Don’t gate on a 10-year Bandit backlog in one shot |

---

### 3.12 Voice

**Have:** Turn-based STT → LLM → TTS. Energy VAD. Fine for voice notes. Not a
2026 voice agent (not OpenAI Realtime, not LiveKit, not barge-in duplex).

| Piece | Call | Best option |
|-------|------|-------------|
| Provider list for notes | **Keep** | |
| Realtime duplex | **Replace the path** | **OpenAI Realtime** or **Gemini Live** for the model; **LiveKit** if you need WebRTC rooms. Energy VAD will never be Silero/semantic VAD. **Addressed 2026-08-25:** LiveKit room tokens + web barge-in; LangGraph remains the brain (not LiveKit Agents / Realtime replacing HITL). |

If voice is not a core product, **don’t replace it** — just don’t market it as
realtime.

---

### 3.13 Documents / research / scraping

| Piece | Call | Best option |
|-------|------|-------------|
| Document platform (jobs, sandbox, ACL, malware) | **Keep** | |
| Tesseract + PyMuPDF extract | **Upgrade** | Adapter: **Docling** (local) and/or **LlamaParse / Reducto** for hard PDFs. Keep *your* store. **Addressed 2026-08-25:** salvage after a weak native score; keys stay out of the parser sandbox. |
| `read_url` ladder + SSRF + scraping proxy | **Keep** | Add Bright Data / Oxylabs as providers when you need them. Never put LLM keys on that proxy |
| Firecrawl / Jina | **Keep as rungs** | Correct design |

Document production ceiling: **single-replica ingestion** until metadata is
Postgres. Electronic PDFs are competitive; scanned/complex layout is not
LlamaParse/Reducto.

---

### 3.14 MCP / skills

| Piece | Call | Best option |
|-------|------|-------------|
| MCP client (stdio, SSE, streamable HTTP, OAuth) | **Keep** | Ahead of most self-hosted agents |
| Resources / prompts / sampling | **Upgrade** | Track the official MCP spec; wrap it with your HITL |
| Agent Skills | **Keep** | agentskills.io is the standard |

Kazma is a strong **MCP client**, not a full 2026 MCP **server** product for
other IDEs (no first-class resources, prompts, sampling, elicitation, roots).

---

## 4. Scorecard vs 2026 world-class

| Area | vs world-class | Action | Ceiling today |
|------|----------------|--------|---------------|
| Memory cognition | **Strong** (beliefs/PPR/bi-temporal) | Keep | 1-node, ~10⁵ rows |
| Memory retrieval scale | **Behind** (SQLite + candidate caps) | Upgrade Qdrant/pgvector primary | Not Mem0 cloud — **pgvector auto 2026-08-25** |
| Swarm planning | **Strong** (DAG/HITL/reliability) | Keep | 1 process |
| Swarm durability/isolation | **Behind** Temporal/Codex VMs | Upgrade execution | Crash = lost in-flight — **Temporal wrap 2026-08-25 (opt-in)** |
| Safety/HITL/commitment | **Strong / rare** | Keep + ML injection later | Trusted operator |
| IDE depth | **Far behind** Cursor/Claude Code | Upgrade or don’t compete | Files+HITL, no LSP |
| Documents platform | **Strong ops/security** | Keep | Single replica metadata |
| Documents extract quality | **Behind** LlamaParse/Reducto | Adapter | Tesseract/PyMuPDF |
| MCP client transports/OAuth | **Competitive** | Keep | Tools-only spec |
| MCP resources/prompts/sampling | **Client surfaces 2026-08-25** | Keep (not an MCP *server*) | Fenced resources; sampling HITL |
| Voice realtime | **Web duplex + TTS in-room 2026-08-25** | Keep graph as brain | Telegram still notes; Realtime-as-brain wontfix |
| Scraping | **Good operator stack** | Keep + providers | Not anti-bot invincible |
| Cron | **Fine personal** | Replace if SaaS | 30s poll, 4 concurrent |
| Code sandbox | **OK single-user Docker** | Replace if multi-tenant | Not Firecracker — **E2B opt-in 2026-08-25** |
| LLM I/O | **Behind** (no stream, no prompt cache) | Replace I/O | Turn-first |
| Web chat delivery | **Strong self-host** | Keep V2; pick one transport | Process-local journal |
| Auth/tenancy | **Lab-to-team** | Upgrade | Not SaaS |
| Observability | **Metrics yes; evals no** | Upgrade | Langfuse dormant |
| Coding CLI | **Installer, not Claude Code** | Keep or build new product | Requires running server |

---

## 5. What will not hold in production

These are not “nice to have.” They break, lie, or silently degrade when you
leave one laptop.

1. **SQLite + multiple replicas** — memory, cron, vault, documents, turn
   journal, HITL pause. HA compose without shared data is a trap.
2. **Non-streaming LLM client** — long turns look dead; cancel doesn’t work;
   you cannot match Claude/Cursor UX.
3. **In-process swarm as “cloud agents”** — process crash loses running work.
   No Temporal-style resume.
4. **Docker (or worse, local Python) as a multi-tenant jail** — shared kernel;
   local import blocklist is bypassable. After HITL, shell is the host.
5. **OIDC unverified-token fallback** — SSO that can accept a forged role.
   **Addressed 2026-08-25:** verify is required; UserInfo only if no `id_token`.
6. **Memory tenant flag off** — “SaaS” with cross-tenant recall.
7. **Telegram poll + webhook together** — 409, dropped updates.
8. **Two chat transports** — every delivery bug has a twin. SSE is primary;
   `/ws/chat/{session_id}` is still mounted in `app.py`.
   **Addressed 2026-08-25:** WS graph actions off unless `KAZMA_WS_GRAPH=1`;
   browser always uses SSE for turns and HITL. Socket remains for cursor
   resume / live telemetry.
9. **God files** (`graph_builder.py`, `tool_registry.py`, `chat.js`, `app.py`,
   `routes_direct.py`, `sse_chat.py`) — the next feature will break a distant
   invariant. This already happened (crawl.py import, memory send_prompt,
   Telegram desync).
10. **Keyword model router + `router: litellm` that isn’t LiteLLM** — config
    lies; routing is a toy.
11. **Session TTL 5 minutes** as a lookup for reminders/HITL — already
    documented; still a footgun if someone “fixes” it the wrong way.
12. **Nginx HA without WebSocket / sticky** — voice and WS die; Turn Delivery
    V2 gaps across replicas.
13. **Document metadata still SQLite while jobs are Postgres** — GC skipped;
    disk fills; “HA documents” is a lie until metadata is ported.
14. **No eval harness** — you cannot know you got worse after a prompt change.
15. **Windows SelectorEventLoop + raw asyncio subprocess** — tools that
    “succeed” in 0 ms and do nothing. Many sites are patched; any new spawn
    can reintroduce it (AGENTS.md §23).

---

## 6. Industry requirements we simply do not have yet

These are table stakes for a 2026 top agent, not extras:

| Requirement | Why it matters | What to use |
|-------------|----------------|-------------|
| Token streaming + cancel | UX and control | Stream in the provider; UI already expects events |
| Prompt caching | Cost and latency | Stable system prefix + Anthropic/OpenAI cache |
| Eval pack in CI | Quality that doesn’t rot | Langfuse datasets or Promptfoo golden traces |
| Lifecycle hooks | Programmable agent | Pre/Post tool hooks. **Addressed 2026-08-25:** PreToolUse / PostToolUse on the execute chokepoint; not a permission system. |
| Apply-patch + codebase index | Coding quality | tree-sitter + patch apply; Monaco |
| Isolated durable workers | Long jobs | Temporal + E2B |
| Structured outputs | Reliable tools/JSON | `response_format` / strict tools. **Addressed 2026-08-25:** closed tool schemas + opt-in `KAZMA_STRICT_TOOLS` + `response_format` plumbing. |
| Computer use | Desktop/browser agents | Provider CUA, not only Playwright. **Addressed 2026-08-25:** `computer_use` screenshot→action loop (HITL); Playwright actuator. |
| Default-on tenancy + MFA | Multi-user | WebAuthn + tenant on every DB |
| One LLM gateway | Ops, spend, failover | LiteLLM |
| Observability that is on | Debug production | Langfuse (or LangSmith), not a dormant config key. **Addressed 2026-08-25:** `enabled: auto` when keys exist. |

Also missing vs Claude Code / Codex / Cursor specifically: ACP (Agent Client
Protocol) and a coding CLI that *is* the runtime. **Addressed 2026-08-25:**
`kazma ask` runs the supervisor in-process (no uvicorn) with live tokens and
TTY HITL. `kazma acp` is ACP JSON-RPC on stdio including `session/update`
and `session/request_permission`. Plan mode: `/plan on` structural
read-only; `/plan go` executes; HITL stays.

---

## 7. The stack to freeze (so you don’t rebuild twice)

This is the “best on earth today” composition that matches **Kazma’s identity**
(self-hosted, multi-mouth, HITL, memory) without pretending you are Anthropic.

| Layer | Freeze on this | Do not use as the core |
|-------|----------------|------------------------|
| Orchestration | **LangGraph** + Postgres checkpointer | CrewAI, raw while-loops, Claude Agent SDK as the kernel |
| LLM pipe | **LiteLLM** + official provider streaming | Hand-rolled httpx forever; OpenRouter as the only pipe (lock-in + no residency) |
| Frontier models | **Claude Opus / GPT / Gemini** as user choice, routed | Hard-coding one lab |
| Memory mind | **Kazma V2** | Mem0 as a replacement brain |
| Memory search | **pgvector** (then Qdrant if needed) | Chroma as production |
| Safety | **Your HITL + commitment** | “YOLO and hope”; a second permission framework |
| Code sandbox (SaaS / untrusted) | **E2B / Firecracker** | Host Python, Docker-only multi-tenant |
| Long workflows | **Temporal** (planner stays Kazma swarm) | Growing `cron.db` and in-process tasks |
| Editor | **Monaco** + patch apply + index | CodeMirror 5 CDN |
| Coding CLI (only if you compete there) | First-class `kazma` agent CLI + later **ACP** | Textual as a Claude Code clone |
| Voice realtime (only if it’s a product) | **LiveKit** + Realtime/Gemini Live | Energy VAD WebSocket |
| Docs extract | **Docling** local + LlamaParse/Reducto for hard files | Rewriting PDF engines again |
| Traces + evals | **Langfuse** (self-host) | “We’ll look at logs” |
| Web API | **FastAPI** | Rewriting the backend in Node |
| Chat transport | **SSE + Turn Delivery V2** | Keeping WS as a second graph client |
| Files at scale | **S3/MinIO** | SQLite blobs on every replica |
| Auth (self-host) | **OIDC fail-closed + passkeys** | Unverified JWT; Auth0 only if you want their UI |

That list is the point of this audit: **keep Kazma’s mind and safety; stop
home-growing physics that the industry already settled.**

---

## 8. What not to do

- Do not rewrite the whole monorepo in TypeScript / “the Claude Agent SDK.”
  You would lose HITL, commitment, memory, and platform isolation — the actual
  moat.
- Do not replace V2 memory with a vector database and a slogan.
- Do not add more chat platforms, more providers, or more UI pages until
  streaming, one transport, and HA honesty are done.
- Do not call it a top-tier coding agent until the IDE/CLI side has index +
  patch + streaming. Today it is a **strong operator agent with a file editor**.

---

## 9. Practical order

If the goal is “never rebuild this layer”:

1. **Streaming LLM adapter + LiteLLM egress** (unlocks UX, cancel, cache).
   **Done 2026-08-25:** `chat_stream()` + `invoke_llm_chat()` + SSE/WS token
   injection. LiteLLM proxy opt-in via `KAZMA_LITELLM_URL` (generic client
   only). Kill-switch `KAZMA_LLM_STREAM=0`. Prompt cache is still later
   (item 3). Restart the server to pick this up.
2. **Split the two god files** (`graph_builder`, `tool_registry`) without
   changing behavior.
   **Done 2026-08-25:** graph nodes/helpers extracted; builtins/schema/scope
   extracted. Public imports unchanged. Strict JSON Schema is still later.
3. **Prompt-cache-friendly context** (stable prefix; semantic compact on
   overflow).
   **Done 2026-08-25:** `pack_system_messages` + Anthropic `cache_control`;
   overflow/`/compact` summarize dropped turns. Kill-switches
   `KAZMA_PROMPT_CACHE=0` / `KAZMA_SEMANTIC_COMPACT=0`.
4. **Eval pack in CI.**
   **Done 2026-08-25:** `tests/fixtures/eval_pack.json` + `tests/test_eval_pack.py`
   (marker `eval`, collected by `fast_test.py`). Operator:
   `python scripts/eval_pack.py`.
5. **OIDC fail-closed + pick one chat transport.**
   **Done 2026-08-25:** `id_token` must verify (no unverified JWT fallback).
   SSE is the graph client; WS is telemetry/cursor unless `KAZMA_WS_GRAPH=1`.
6. **Postgres+pgvector as memory/search primary when you leave one node.**
   **Done 2026-08-25:** pgvector auto-selects when a Postgres DSN is set
   (`KAZMA_PGVECTOR=0` keeps sqlite-vec). Postgres-primary recall is
   ILIKE + pgvector RRF, not ILIKE-only. Qdrant still wins if you pick it.
7. **Monaco + apply-patch** (or a real CLI) if coding is the product.
   **Done 2026-08-25:** Web `/ide` uses Monaco (textarea fallback). New
   HITL-gated ``file_apply_patch`` (search-replace or unified diff) via
   IdeService. **LSP** (hover/complete/definition/diagnostics) shipped
   2026-08-25 (`POST /api/ide/lsp`, `KAZMA_IDE_LSP=0`). ACP remains later.
8. **E2B + Temporal** only when you want untrusted or multi-hour cloud work.
   **Done 2026-08-25:** opt-in adapters. E2B for ``python_exec`` when an API
   key is set (Firecracker). Temporal wraps swarm ``_dispatch_inner`` when
   ``KAZMA_TEMPORAL_HOST`` is set. Planner, HITL, and in-process default
   stay. Kill-switches ``KAZMA_E2B=0`` / ``KAZMA_TEMPORAL=0``.

Items 1–5 are required even to stay a **best self-hosted** agent. Items 6–8
are required to play in the **world top** tier without a second rewrite.

---

## 10. Bottom line

Kazma’s control plane (safety, honesty, memory, mouths, delivery) is rare and
should be kept. The parts that will force a rebuild if you delay are the
**LLM I/O (non-streaming, no cache), in-process execution pretending to be a
fleet, CodeMirror-5 IDE, SQLite-as-HA, and no evals.** Freeze the table in
§7 and treat every new feature as “does this go through the frozen layer, or
are we inventing a sixth copy?”

Highest-leverage first build: the **streaming + LiteLLM pipe**, because almost
every “feels worse than Claude Code” complaint is blocked there.

---

## 11. Evidence anchors (selected)

| Claim | Anchor |
|-------|--------|
| Graph topology SUPERVISOR ⇄ TOOL_WORKER → RESPOND | `kazma_core/agent/graph_builder.py` (module docstring + compile site) |
| Non-streaming `/chat/completions` POST | `kazma_core/llm_provider.py` `chat()` payload — no `stream` flag |
| HITL resume uses `ainvoke` because no model-stream events | `kazma_ui/sse_chat.py` comment on `Command` / `astream_events` hang |
| `router: litellm` in YAML but LiteLLM is not the gateway | `kazma.yaml` `models.router`; LiteLLM used only as a fallback-model flag |
| Four-branch provider dispatch | `kazma_core/model_registry.py` `get_client` / `get_model` / `get_client_by_provider` |
| Monaco editor (CDN) + textarea fallback | `kazma_ui/templates/ide.html`, `static/js/ide.js` |
| WS chat still mounted | `kazma_ui/app.py` `/ws/chat/{session_id}`; `chat.py` docstring claims removed |
| OIDC unverified fallback | `kazma_core/security/oidc.py` `_verify_id_token(...) or claims` |
| Vector backends: sqlite-vec default, Qdrant/pgvector opt-in | `kazma_core/memory/backends.py` `DEFAULT_BACKENDS_CFG` |
| Docker jail vs local fallback | `kazma_core/tools/code_exec.py` |
| HA compose does not mount `kazma-data` | `docker-compose.ha.yml` |
| CI: tests gate; Ruff `--exit-zero`; no Playwright | `.github/workflows/ci.yml` |
| Langfuse dormant unless keys | `kazma.yaml` `logging.langfuse.enabled: auto` (on when keys exist; was `false`) |
| Multi-user is foundation, not SaaS | `docs/docs/products/multi-user-saas.md` |

---

## Related docs

- [`AUDIT_DEEP_STRUCTURE_2026-08-19.md`](AUDIT_DEEP_STRUCTURE_2026-08-19.md) — invariant check + change-impact map
- [`AUDIT_MEMORY_SYSTEM_2026-08-24.md`](AUDIT_MEMORY_SYSTEM_2026-08-24.md) — V2 memory (M-01..M-17 closed)
- [`AUDIT_MODEL_AND_TURN_DELIVERY_2026-08-03.md`](AUDIT_MODEL_AND_TURN_DELIVERY_2026-08-03.md) — model stickiness + delivery (Sprint 1–2; V2 cursor-resume landed 2026-08-23)
- [`AUDIT_PRODUCTION_READINESS_2026-07-21.md`](AUDIT_PRODUCTION_READINESS_2026-07-21.md) — historical security/prod (single-operator GO / SaaS NO-GO)
- [`../ARCHITECTURE_AND_SYSTEM_MAP.md`](../ARCHITECTURE_AND_SYSTEM_MAP.md)
- [`../plans/MEMORY_REMAINING.md`](../plans/MEMORY_REMAINING.md)
- [`../docs/ops/production-checklist.md`](../docs/ops/production-checklist.md)
