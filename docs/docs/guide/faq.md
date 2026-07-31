---
id: faq
title: FAQ
sidebar_label: FAQ
description: Kazma FAQ — code-audited reference (unified docs, v0.6.1+)
---
> Short answers to the questions operators and integrators actually ask.

---

## General

### Is Kazma production-ready?

The core agent, swarm orchestration, HITL gates, gateways, and chat memory (V2 cognitive engine — bi-temporal beliefs + per-turn PPR recall + auto-store) are implemented and wired. Install `pip install -e ".[rag]"` for full vector recall. Treat the safety layer as production-grade; memory is production-usable on single-node with health monitoring (see [Memory & RAG](memory-and-rag)).

### What language is the UI in by default?

Arabic, RTL (`agent.language: ar`, `agent.rtl: true`). Set to `en` for English. The `kazma-lang` cookie switches per-browser without a restart.

### What's "Kazma" mean?

**Kazma** in Latin script; Arabic brand **كاظمه** (preferred) or **كاظمة**. The spelling **كازما** is wrong. The Majlis protocol (`majlis.py`) implements Gulf Arabic conversational rhythms.

### How do I connect Gmail or Microsoft email?

See [Email integration](email-integration). Sandbox works with no setup.

**Settings → Email** (`/settings?tab=email`) has a mode switcher per provider: **OAuth | IMAP | POP**.

| Provider | Recommended | Alternatives |
|----------|-------------|--------------|
| **Gmail** | Browser **OAuth** (Gmail API) — Google Cloud OAuth Web client + Test user + `gmail.modify` scopes | IMAP/POP with a **Google App Password** (not your normal password) |
| **Microsoft 365** | Browser **OAuth** (Graph) or device-code fallback | IMAP/POP to `outlook.office365.com` if the tenant still allows basic auth |

Mutating tools (`email_send`, `email_delete`, `email_categorize`) need HITL approval. If Gmail returns **403 insufficient scopes**, add Gmail scopes on the OAuth consent screen, Disconnect, and Connect again.

### How do I make the agent research the web?

There is **no** `/research` slash command. Ask in **chat** (e.g. “Research X and cite sources”) or use **`/swarm research …`** for multi-worker dispatch. The agent uses `web_search`, `read_url` / `read_url_to_file`, optional `crawl_site`, and `digest_research_file`. See [Web research](web-research).

### Why is a long page truncated?

`read_url` returns a **window** (default 16k, env `KAZMA_READ_URL_MAX_CHARS`) and supports `offset` / `max_chars` paging. For the full page, use `read_url_to_file` then `digest_research_file` or `read_research_chunk`. Graph results for research tools allow a higher truncate cap (`KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS`).

---

## Providers & models

### Which LLM providers work?

Ten presets ship: OpenAI, Anthropic, DeepSeek, Google Gemini (ADC), xAI, OpenRouter, Ollama, LM Studio, NVIDIA NIM, Custom. Any OpenAI-compatible endpoint works. See [Configuration → provider presets](configuration#52-built-in-provider-presets).

### Do I need LiteLLM?

No. `models.router: litellm` is a **string** that only gates the fallback-model branch. Kazma never imports LiteLLM. If you run a LiteLLM proxy, point `base_url` at `http://host:4000/v1`.

### Why doesn't `DEEPSEEK_API_KEY` / `ANTHROPIC_API_KEY` work?

They're in `.env.example` but **no code reads them**. Key those providers via the ConfigStore provider list or `kazma.yaml`. Only `OPENAI_API_KEY` and `KAZMA_API_KEY` are generic env-var fallbacks.

### How do I switch models safely?

Use `set_active_model()` (auto-switches provider) or the Web UI / `POST /api/provider/switch`. Never set model without provider — `get_client()` auto-corrects, but explicit is safer.

### How do I use a local model?

Point the provider at Ollama (`http://127.0.0.1:11434/v1`) or LM Studio (`http://localhost:1234/v1`). No API key needed — dummy keys are injected automatically.

---

## Memory & RAG

### Why doesn't my agent remember things?

By default, memory **is** automatic:

1. **Per-turn RAG** injects relevant past facts on every user message (`memory.per_turn_retrieval`).
2. **Auto-store** writes durable user facts (and optional turn snapshots) after each reply (`memory.auto_store`).
3. The LLM can also call `memory_store` / `memory_search` tools explicitly.

If recall is empty: install the RAG extra (`pip install -e ".[rag]"`), confirm Dashboard memory health is `ACTIVE`, and ensure `memory.enabled` is true (TUI Settings or `kazma.yaml`). See [Memory & RAG](memory-and-rag).

### Is the "4-layer memory" real?

That was the **V1** stack (`UnifiedMemoryAdapter`: Chroma L1 + graph L2 + FTS5 L3 + sqlite-vec L4, RRF-blended). V1 was **removed** in the V1→V2 cutover. The current and only stack is the **V2 Cognitive Engine** — bi-temporal belief graph + 4-tier episodes + Local Ego-Graph PPR recall (`memory/recall.py:recall()`). It is the chat default for per-turn recall, tools, auto-store, and compaction.

### Do I need to install ChromaDB?

V2 recall runs on **sqlite-vec** (bundled via the `[rag]` extra), not ChromaDB. Install `pip install -e ".[rag]"` for the full vector stack (sqlite-vec + sentence-transformers; chromadb is still pulled in for the embedder types and the semantic router). Without it, V2 degrades to FTS5-only episode search; the Dashboard memory health board shows what is available.

### Why is `tiktoken` mentioned if it's not a dependency?

`TokenCounter` uses `tiktoken` if installed, else a chars/4 heuristic. Install it yourself (`pip install tiktoken`) for accurate counts.

---

## Safety & HITL

### A danger tool executed without asking — why?

Three things to check (see [Troubleshooting §3](troubleshooting-and-workarounds#3-hitl--safety-issues)):

1. Was `hitl_config` passed to your graph build? (Custom builds via `create_supervisor_graph()` without it = dormant gate.)
2. Is the tool on the **right** danger list? (Three lists: yaml, `_EXTENDED_DANGER`, `classify_mcp_tool`.)
3. Is `allow_headless_danger=True` set? (Should be `False` in production.)

### Do I need `KAZMA_SECRET`?

**Yes** for any non-localhost deployment. It protects `/api/approve`. Without it, approval endpoints are unauthenticated. `kazma serve` only binds `0.0.0.0` when it's set.

### Are skills cryptographically signed?

Yes. `kazma hub sign` writes an HMAC-SHA256 signature; the loader verifies it fail-closed with `hmac.compare_digest`. See [Skills, MCP & Tools](skills-mcp-and-tools#cryptographic-signing).

### Are MCP servers authenticated?

Only SSE transport (bearer/custom header). **Stdio has no auth** — sandbox it.

---

## Swarm

### How do I run a multi-worker task?

```bash
kazma swarm dispatch researcher "summarize X"
kazma swarm fanout --workers a,b,c --aggregation vote "question"
kazma swarm pipeline --workers researcher,builder "build Y"
```

See [CLI Reference → swarm](cli-reference#6-kazma-swarm).

### Why did my handoff loop break with an error?

Handoffs are capped at depth 5 and 2 visits per worker (`handoff_guards.py`). A→B→A→B… is intentionally blocked. Legitimate A→B→A *return* handoffs work (2 visits allowed).

### Is there Prometheus?

No. Metrics are in-memory + SQLite. See [Roadmap](roadmap-and-future#8-observability).

---

## Deployment

### Docker or bare metal?

Docker Compose is the primary path. Bare `kazma serve` works for single-host dev. See [Deployment](deployment).

### Can I deploy on Cloudflare Pages / edge?

No. Kazma is a stateful Python service. Don't attempt serverless packaging.

### The Kubernetes manifests don't seem right.

They deploy a **Hub API** service (PostgreSQL + Redis), not the main agent. Don't apply them for the main agent. See [Deployment §4](deployment#4-kubernetes-hub-service-only--read-carefully).

### Why does the container bind 0.0.0.0?

Required inside Docker so the published port reaches the service. Docker's network isolation is the security boundary (explained in the Dockerfile comment). For bare metal, use `127.0.0.1` + a reverse proxy.

---

## Concurrency

### I got "database is locked" — what now?

WAL + `busy_timeout=5000` is set everywhere. You likely have a long write transaction or a second connection. Use `batch_set()` for multi-key writes and always use `get_config_store()`. See [Troubleshooting §4](troubleshooting-and-workarounds#4-sqlite-concurrency).

### Can I run multiple Kazma processes on the same DB?

Not recommended. WAL allows one writer. If you scale horizontally, give each process its own `kazma-data/` or shard by tenant.

---

## Documentation audit notes

This FAQ reflects verified behavior. If an answer here contradicts older README text, this FAQ is the accurate one as of v0.6.1+.
