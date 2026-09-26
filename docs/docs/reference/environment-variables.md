---
id: environment-variables
title: Environment Variables
sidebar_label: Environment Variables
description: Master reference for Kazma environment variables (dev, single-operator, production)
---

> Complete env reference for local, Docker, and production. Prefer strong secrets; never commit `.env` with real keys. Also see [Configuration](../guide/configuration) for `kazma.yaml` and ConfigStore.

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_COMPUTER_USE` | `1` | `0` disables the `computer_use` tool |
| `KAZMA_LANGFUSE` | (unset) | `0` forces console tracing even when Langfuse keys exist |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | | With `logging.langfuse.enabled: auto`, both set → Langfuse backend |
| `KAZMA_EMBED_FLEET` | (unset) | `1` + OpenAI/Voyage key → hosted embeddings (issue #78) |
| `KAZMA_DOCLING` | `1` | `0` skips Docling salvage on weak PDF extracts |
| `KAZMA_REMOTE_PARSE` | `0` (veto only) | **Off by default.** Remote PDF salvage (LlamaParse/Reducto) ships documents to a third party, so it is governed by `documents.security.remote_parse`, which defaults to `False`. This variable is a **veto, not a switch**: `0` force-disables salvage even where the policy allows it, and setting it to `1` does **not** enable salvage on its own. Having an API key configured is not consent — turn the policy on deliberately. |
| `LLAMAPARSE_API_KEY` / `REDUCTO_API_KEY` | | Hard-PDF remote extract (parent process; not the parser sandbox) |
| `KAZMA_SILERO_VAD` | (unset) | `1` tries Silero VAD (falls back to energy) |
| `KAZMA_LITELLM_URL` | (unset) | LiteLLM proxy for OpenAI-compatible providers only (e.g. `http://127.0.0.1:4000`) |
| `KAZMA_LITELLM` | `1` | `0` disables the LiteLLM proxy even if a URL is set |
| `KAZMA_LITELLM_LOCAL` | (unset) | `1` also routes loopback Ollama/LM Studio through the proxy |
| `KAZMA_LITELLM_FALLBACK_DIRECT` | (unset) | `1` retries the original provider URL if the proxy is unreachable |
| `LITELLM_MASTER_KEY` / `LITELLM_API_KEY` / `KAZMA_LITELLM_KEY` | | Bearer key sent to the proxy (else the provider key is reused) |
| `LIVEKIT_URL` | (unset) | LiveKit WebRTC URL for web duplex voice (e.g. `wss://…livekit.cloud`) |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | | LiveKit API credentials (room tokens). All three required to enable duplex |
| `KAZMA_VOICE_DUPLEX` | `1` | `0` disables LiveKit duplex even if credentials are set |
| `KAZMA_CUA_PLANNER` | `1` | `0` keeps `computer_use` on vision-JSON (no Anthropic CUA / Gemini mapping) |
| `KAZMA_MCP_SAMPLING` | `0` | `1` allows MCP `sampling/createMessage` after a HITL card (no tools on that LLM call) |
| `KAZMA_MCP_SAMPLING_TIMEOUT` | `60` | Seconds to wait for the sampling HITL card |

## Precedence (reminder)

1. **Specific helpers** may read env first (`KAZMA_SECRET`, vault, disclosure).  
2. **ConfigStore DB** wins for most runtime settings.  
3. **`kazma.yaml`** seeds missing DB keys.  
4. **Hardcoded defaults** last.

Generic `ConfigStore.get()` does **not** automatically overlay every env var — only documented keys below that code explicitly reads.

### Document Intelligence

Document platform limits, OCR, workers, retention, capacity, and rollout flags
are **ConfigStore / `kazma.yaml` keys** (live-read), not a parallel env matrix.
Primary keys are nested, for example:

- `documents.enabled` / `documents.shadow` / `documents.default_authoritative`
- `documents.intake.max_bytes`, `documents.limits.max_pages`
- `documents.ocr.*`, `documents.workers.*`, `documents.capacity.*`
- `documents.retention.*`, `documents.gc.*`, `documents.security.*`

See [Document Intelligence — live configuration](../guide/document-intelligence.md#live-configuration)
and `kazma_core.documents.config.DocumentConfig`. Optional cert soak size:
`KAZMA_DOCUMENT_SOAK_ITERATIONS` (used by `scripts/certify_documents.py --soak`).

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_DOCUMENTS_JOBS_BACKEND` | auto | Force `sqlite` for job queue (else follow Postgres when configured) |
| `KAZMA_DOCUMENTS_METADATA_BACKEND` | `auto` | `sqlite` / `postgres` / `auto` (auto follows jobs backend). Postgres metadata enables multi-replica CRUD; GC mark/sweep is backend-agnostic (`repository.gc_mark`) |
| `KAZMA_DOCUMENT_SOAK_ITERATIONS` | `100` | Soak iteration count for `certify_documents.py --soak` |
| `KAZMA_DOCUMENT_FONT_DIR` | vendored `documents/assets/fonts/` | Font directory for generated PDF/DOCX/HTML. A path that is not a directory is ignored with a warning, and the vendored IBM Plex Sans Arabic is used. Fonts are still chosen by verified glyph coverage. |

---

## Core process & bind

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_HOST` | `127.0.0.1` | Set deliberately | Bind address. Non-loopback **requires** `KAZMA_SECRET`. |
| `KAZMA_PORT` / serve arg | `9090` (CLI) | No | HTTP port (`kazma serve [port]`). Docker images may differ — check compose. |
| `KAZMA_SECRET` | generated on loopback | **Yes** on public bind | Auth shared secret / session material. Known-bad default **refused**. |
| `KAZMA_PRODUCTION` | unset | **Yes** for prod | Enables vault-required, workspace root, code_exec policy, YOLO hard-block, etc. |
| `KAZMA_ENV` | unset | Optional | Some paths treat `production` specially. |
| `KAZMA_PUBLIC_URL` | unset | Recommended behind proxy | Public origin for OAuth/OIDC redirects. |
| `KAZMA_CORS_ORIGINS` | unset | If browser cross-origin | Explicit comma-separated HTTP(S) origins trusted for credentialed CORS and API mutations. Scheme and port must match; wildcards are rejected. Local development ports are not trusted by default. Declare the reverse proxy's external origin with `KAZMA_PUBLIC_URL`. Restart after changing these settings. |
| `KAZMA_ENV_FILE` | unset | No | Absolute path to an extra `.env`, loaded **last** so it wins. The `.env` ladder is: `<kazma home>/.env` → `<cwd>/.env` → this. Nothing outside the installation is read unless you name it here. |
| `KAZMA_TRUSTED_PROXIES` | unset | **Yes** behind any proxy | Comma-separated addresses or CIDR ranges the reverse proxy connects **from** (`127.0.0.1` for same-host nginx/Caddy or Cloudflare Tunnel, the bridge IP or `172.17.0.0/16` under Docker). Only these peers may set `X-Forwarded-For` / `X-Forwarded-Proto`; `*` is ignored. See the warning below. |
| `KAZMA_LOOPBACK_AUTOLOGIN` | `0` | Keep `0` | Re-enables credential-less loopback login even with a proxy declared. Only for a host where `127.0.0.1` really is just you. |
| `KAZMA_TRUST_LAN` | `0` | Keep `0` unless needed | LAN trust for auth middleware. |
| `KAZMA_AUTH_DISABLED` | unset | **Never in prod** | Disables auth helpers — dev only. |
| `KAZMA_ALLOW_YOLO` | unset | Avoid | Only way to re-enable YOLO when `KAZMA_PRODUCTION=1`. |
| `KAZMA_VERBOSE_ERRORS` | `0` | Keep `0` | Appends the real exception message to API errors (still redacted for paths and credentials). Dev only — production returns a code plus a correlation id. |
| `KAZMA_BASE_URL` | unset | No | An extra address the agent may use to reach this server's own API (after `KAZMA_PUBLIC_URL` in the agent's runtime note, and after the loopback candidates for local API calls). |
| `KAZMA_GIT_SHA` | `git rev-parse` | No | Commit id shown in the version string (`0.10.0+g<sha>`). Read first, then `GITHUB_SHA`, then git; set it in images that ship without a `.git` directory. |

:::danger `KAZMA_TRUSTED_PROXIES` is required behind a reverse proxy

Kazma treats a **loopback client as the local operator** and auto-issues an
admin session to it — that is what makes single-operator localhost use work
with no login.

Behind a same-host nginx/Caddy, `request.client.host` is `127.0.0.1` for
*every* internet visitor. Without `KAZMA_TRUSTED_PROXIES`, each of them is
therefore treated as the operator and handed an admin session on the first
page load, over HTTP **and** WebSocket. This was audit finding F-01
(2026-08-29).

Set it to the proxy's address and Kazma reads the real client from
`X-Forwarded-For` instead, and stops treating peer address as a credential:

```bash
KAZMA_TRUSTED_PROXIES=127.0.0.1
```

Your proxy must send the forwarded headers and must *overwrite* rather than
append a client-supplied value; the shipped `deploy/nginx-ha.conf` already
does. Kazma applies this variable itself (an in-app middleware), and every
launcher starts uvicorn with `proxy_headers=False`; launch it yourself with
`--no-proxy-headers`.

Under **Docker** this is the proxy container's bridge address (often
`172.17.0.1` or the compose network gateway), not `127.0.0.1`. Getting it
wrong no longer fails open: a forwarded header from an undeclared peer
disables peer-address trust for the process and logs the address to set.

**Verify after deploy** — `authenticated` must read `false` before login, and
`proxy.state` should read `declared` (`direct` behind a proxy means the
variable did not take; `undeclared_proxy` means it is set to the wrong
address, and `proxy.hint` names the right one):

```bash
curl -s https://your.domain/api/auth/status
```
:::

---

## Secrets, vault, crypto

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_VAULT_KEY` | unset | **Yes** if production | AES vault master material. |
| `KAZMA_JWT_SECRET` | unset | If JWT paths used | JWT signing for tenant/API tokens. |
| `KAZMA_DISCLOSURE_KEY` | unset | Optional | Vulnerability disclosure crypto helper. |

---

## Database & multi-replica

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_DATABASE_URL` | unset (SQLite) | Multi-replica **Yes** | Postgres DSN → dual-backend stores + LangGraph checkpointer. |
| `DATABASE_URL` | unset | Alt | Accepted by migrate script as alias. |
| `KAZMA_DB_BACKEND` | auto | Optional | Force `postgres` / `sqlite`. |
| `KAZMA_DB_CONTAINER` | `kazma-db` | Migration | Docker container name for `pg_dump` / `pg_restore` discovery during `kazma migrate`. See [Migration](../ops/migration). |
| `KAZMA_DB_INTERNAL_PORT` | `5432` | Migration | Container-internal Postgres port when `pg_dump` / `pg_restore` run via `docker exec` (the host's forwarded port is unreachable from inside the container). |
| `KAZMA_DOCKER_BIN` | unset | Optional | Absolute path of the `docker` CLI. Kazma looks for it here, then on `PATH`, then in Docker's standard install folders (e.g. `C:\Program Files\Docker\Docker\resources\bin`). Needed only when docker lives somewhere else: the Postgres dump (`pg_dump` via `docker exec`) and the `python_exec` Docker jail both use it. A Docker Desktop update that drops its folder from `PATH` no longer stops backups (2026-09-25). |
| `KAZMA_PG_POOL_RETRIES` | `5` | Optional | Connection-pool creation retry count. Handles transient failures (Windows Docker-bridge, container mid-startup). |
| `KAZMA_PG_POOL_RETRY_DELAY` | `1.0` | Optional | Seconds between pool-creation retries. |
| `KAZMA_PG_POOL_MIN` | `1` | Optional | Minimum connections in the psycopg pool. |
| `KAZMA_PG_POOL_MAX` | `10` | Optional | Maximum connections in the psycopg pool. |
| `KAZMA_PG_POOL_TIMEOUT` | `5` | Optional | Seconds a caller waits for a free pool connection. A hang here used to freeze `/health/ready` on the event loop. |
| `KAZMA_REPLICA_AFFINITY` | on | Multi-replica | `0` stops setting the `kazma-replica` cookie (HttpOnly). Chat SSE and some swarm state live in one process, so behind a load balancer keep it on and make the balancer sticky on that cookie (or on a source-IP hash). |
| `KAZMA_REPLICA_ID` | hostname | Multi-replica | The value this node writes into the `kazma-replica` cookie. |
| `KAZMA_SHARED_BREAKERS` | on in production / multi-user | Multi-replica | Keep swarm circuit-breaker state in the settings store (`swarm.breaker.<worker>`) so every replica sees a breaker another one opened. `1` / `0` force it either way. |
| `KAZMA_SWARM_MAX_ACTIVE` | swarm `max_concurrent_tasks` (`10`) | No | How many swarm tasks may run at once; the next one is refused ("Swarm at capacity"). Pipelines paused at a checkpoint do not count. |
| `KAZMA_MIGRATE_CHECK_PORT` | off | Migration | `1` makes `kazma migrate import` also refuse while something listens on `KAZMA_PORT` / `PORT` (default `9090`). The main check is the running server's heartbeat; the port probe is off by default because an unrelated dev server on that port would block every import. |
| `KAZMA_DB_BACKEND_SOURCE` | — | Not an input | Written, never read: `kazma migrate export` records the source database backend under this name in the bundle's `meta.env`. |
| `KAZMA_PG_RESTORE_REHEARSAL` | unset (off) | Optional | `1` turns ON the weekly restore rehearsal: the newest `pg_dump` is restored into a scratch database `kazma_restore_rehearsal_<epoch>` on the SAME server, checked (Kazma's tables present, `kazma_settings` not empty) and dropped. `0` vetoes the setting `backups.pg.restore_rehearsal`. The user needs `CREATEDB`; without it the drill reports UNVERIFIED with the grant to add. Leftover scratch databases older than a day are removed by the next run; nothing else is ever created or dropped. |

---

## Sandbox (E2B) & durable swarm (Temporal)

Default remains Docker/local `python_exec` and in-process swarm. These are
opt-in for untrusted code and multi-hour work. See [Architecture](../guide/architecture)
and extras `kazma[sandbox]` / `kazma[durable]`.

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_E2B_API_KEY` / `E2B_API_KEY` | unset | Untrusted / multi-user code | E2B Firecracker for HITL-approved `python_exec`. |
| `KAZMA_E2B` | auto if key set | No | `0` keeps Docker/local even with a key. |
| `KAZMA_CODE_EXEC_DOCKER` | `auto` | Single-operator jail | `1`/`force` Docker; `0` local (ignored when production forbids local). `force` also blocks host `shell_exec` unless `KAZMA_HOST_SHELL=1`. |
| `KAZMA_HOST_SHELL` | unset | Escape hatch | `1` allows host `shell_exec` even when `KAZMA_CODE_EXEC_DOCKER=force`. |
| `KAZMA_CODE_EXEC_IMAGE` | `python:3.12-slim` | No | Image for the Docker jail that runs `python_exec` / `code_exec` (no network, read-only work mount, tmpfs `/tmp`, memory cap). |
| `KAZMA_LIVE_EVAL` | unset | No | `1` runs the opt-in live-model eval (`tests/test_hands_live.py`). CI skips. |
| `KAZMA_TEMPORAL_HOST` / `TEMPORAL_ADDRESS` | unset | Multi-hour swarm | Temporal frontend (`localhost:7233`). Wraps swarm `_dispatch_inner`. |
| `KAZMA_TEMPORAL` | auto if host set | No | `0` keeps in-process swarm. |
| `KAZMA_TEMPORAL_REQUIRED` | unset | Strict HA | `1` = fail the task if Temporal/SDK is down (no in-process fallback). |
| `KAZMA_TEMPORAL_NAMESPACE` | `default` | No | Temporal namespace. |
| `KAZMA_TEMPORAL_QUEUE` | `kazma-swarm` | No | Task queue for the in-process Temporal worker. |
| `KAZMA_CODE_INDEX` | on | No | `0` disables the workspace symbol index + `codebase_search`. |
| `KAZMA_IDE_LSP` | on | No | `0` disables the `/api/ide/lsp` backend. The Web `/ide` editor is CodeMirror 5 (syntax only); hover/complete UI is not bound. |

---

## LLM / provider

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `OPENAI_API_KEY` | unset | One provider key | OpenAI-compatible key; also used for the `dall-e` image-gen backend. |
| `KAZMA_API_KEY` | unset | Fallback | Generic API key fallback. |
| `KAZMA_PROVIDER` | unset | Optional boot | Provider id at startup. |
| `KAZMA_MODEL` | unset | Optional boot | Model id at startup. |
| `ANTHROPIC_API_KEY` | unset | For Claude | Native Anthropic Messages API (`anthropic_llm.py`). |
| `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` / `AZURE_OPENAI_DEPLOYMENT` / `AZURE_OPENAI_API_VERSION` | unset | For Azure | Azure OpenAI (`azure_llm.py`). |
| `AWS_REGION` / `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | unset | For Bedrock | AWS Bedrock via standard boto3 credential chain (`bedrock_llm.py`). |
| `STABILITY_API_KEY` | unset | Optional | Stability SDXL image-gen backend. |
| `FAL_KEY` | unset | Optional | Flux image-gen backend (FAL.ai). |
| `KAZMA_IMAGE_PROVIDER` | unset | Optional | Force an image backend (`pollinations`/`dall-e`/`stability`/`flux`); default `auto`. |
| `KAZMA_STRICT_TOOLS` | unset (off) | No | `1` stamps OpenAI `function.strict: true` on closed tool schemas (all properties required; optionals are `T \| null`). Default is closed objects with `additionalProperties: false` only — local / Anthropic / Gemini often 400 on `strict`. |
| `KAZMA_LLM_STREAM` | on | No | `0` falls back to blocking `chat()` (no token SSE). |
| `KAZMA_TOOL_HOOKS` | on | No | `0` disables PreToolUse / PostToolUse (in-process and command). Empty `agent.hooks.*` lists are a no-op. Hooks cannot skip HITL. |
| `KAZMA_PLAN_MODE` | on | No | `0` disables `/plan` enter/execute. Plan mode is not a HITL bypass. |
| `GOOGLE_CALENDAR_TOKEN` / `MS_CALENDAR_TOKEN` | unset | Optional | Override only. Calendar tokens normally live in the vault (`calendar.google.*` / `calendar.microsoft.*`) via Settings → Email → Connect Calendar / Connect with Microsoft. |
| `KAZMA_CALENDAR_PROVIDER` | `auto` | No | Calendar backend when a call names none: `auto` picks Google if connected, else Microsoft, else the sandbox. `google`, `outlook` (also `microsoft` / `graph`) or `sandbox` force one; a forced provider with no credentials fails instead of falling back. |
| `KAZMA_VISION_MODELS` | unset | No | Comma-separated extra model-id patterns (shell wildcards, case-insensitive) to treat as vision-capable, e.g. so `analyze_image` can pick a model the built-in list does not know. The built-in text-only list still wins. |
| `KAZMA_SEMANTIC_CACHE_TTL_SECONDS` | `86400` | No | Age after which a semantic-cache entry is ignored; `0` = entries never expire. Only matters with `KAZMA_SEMANTIC_CACHE=true` (see the security table). |
| `KAZMA_SEMANTIC_CACHE_MAX_ROWS` | `10000` | No | Size cap of the semantic cache; the oldest rows are dropped first. |
| Provider-specific | — | As used | e.g. DeepSeek, Groq, xAI, OpenRouter, Mistral, Together, Cohere, Fireworks, Perplexity, AI21, Google ADC — see Configuration. |

---

## Workspace, memory, demo

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_WORKSPACE` | active WorkspaceStore / data dir | Prod root policy | Agent filesystem workspace root. |
| `KAZMA_VECTOR_COLLECTION` | `agent_memory` | No | Chroma collection name. |
| `KAZMA_VECTOR_MODEL` | `BAAI/bge-m3` | No | Legacy alias for the embedding model id (prefer `KAZMA_EMBED_MODEL`). |
| `KAZMA_EMBED_PROVIDER` | `local` | No | Embedding provider (`local` or `openai-compatible`). |
| `KAZMA_EMBED_MODEL` | `BAAI/bge-m3` | No | Embedding model id (overrides `memory.embedding.model`). |
| `KAZMA_EMBED_DIM` | `1024` | No | Embedding dimension (must match the model's output). |
| `KAZMA_EMBED_BASE_URL` | unset | Remote only | `/embeddings` endpoint base URL for `openai-compatible`. |
| `KAZMA_EMBED_API_KEY` | unset | Remote only | API key for the remote `/embeddings` endpoint. |
| `KAZMA_DEMO_MODE` | unset | **No** | Demo fixtures — never enable in real prod. |
| `KAZMA_MEMORY_ENFORCE_TENANT` | unset | Multi-tenant only | When `1`/`true`, the `/memory` operator endpoints scope reads, id-keyed mutations, undo tokens, and graph-clear by the request-scoped tenant (set by the auth middleware from verified JWT/opaque-session claims). Unset = single-tenant `default`. Flip on only when you add a second tenant. |
| `KAZMA_MEMORY_STATE_ROLE` | unset (`mirror`) | Multi-replica only | `primary` makes the Postgres state backend the recall SoT (fail-closed if down — no silent SQLite). Dense search is pgvector fused with ILIKE. Do **not** enable until `python scripts/reconcile_memory_mirror.py --dry-run` reports no dead-in-mirror / only-in-mirror rows. |
| `KAZMA_PGVECTOR` | auto when a Postgres DSN is set | No | `0` keeps sqlite-vec even if Postgres is on, without checking the server. Unset = pgvector auto-select from `KAZMA_DATABASE_URL` / `memory.backends.state.url`, used only if that Postgres ships the `vector` extension (checked at boot; `postgres:16-alpine` does not). Explicit Qdrant in Settings still wins. |
| `KAZMA_WORKSPACE_ROOT` | unset | Multi-project hardening | When set, a workspace picked in the UI (Switch Repo, the path picker) must live under this directory. It is also a base under which cloned workspaces may be deleted. |
| `KAZMA_CLONE_DIR` | `~/kazma-repos` | No | Where repositories cloned from the UI or `/ide clone` are put; cloned workspaces under it may be deleted from the UI. |
| `KAZMA_MEMORY_STATE_REGION` | unset | Multi-region only | Region id stamped on the rows this install mirrors to the Postgres state backend. |
| `KAZMA_MEMORY_CONFLICT_POLICY` | `last_write_wins` | Multi-region only | What a mirrored write does to a row that another region wrote first: `last_write_wins` overwrites it; `origin_wins` and `fail_closed` both skip the write (they differ only in the logged reason). |
| `KAZMA_GRAPH_PROVIDER` | `sqlite` | No | `neo4j` puts the knowledge graph on Neo4j, as does setting `KAZMA_NEO4J_URL` / `NEO4J_URI` or `KAZMA_NEO4J_DEFAULT=1`. These only fill settings that are empty: a provider already chosen in Settings wins. |
| `KAZMA_NEO4J_URL` | `bolt://localhost:7687` | No | Neo4j address (`NEO4J_URI` / `NEO4J_URL` are accepted too); setting it selects Neo4j. |
| `KAZMA_NEO4J_USER` | `neo4j` | No | Neo4j user (`NEO4J_USER` is accepted too). |
| `KAZMA_NEO4J_PASSWORD` | unset | No | Neo4j password (`NEO4J_PASSWORD` is accepted too); fills an empty password only. |
| `KAZMA_NEO4J_DEFAULT` | unset | No | `1` selects Neo4j with the defaults above; the `docker-compose.neo4j` profile sets it. |
| `KAZMA_AUTO_STORE_BELIEFS` | `conservative` | No | Which facts the memory extractor saves from a conversation without being asked: `off`, `conservative` (default) or `aggressive` (every extracted belief — the old behaviour). Wins over `memory.auto_store_beliefs`. |
| `KAZMA_TRANSCRIPT_RECALL` | on | No | `0` stops searching past Web chat transcripts when memory recall comes back empty (ConfigStore `memory.transcript_fallback`; the env value wins). Transcript text reaches the model fenced as untrusted data. |

---

## Web search, scrape & research

See [Web research](../guide/web-research) for playbooks. Tools are used from **chat** (no `/research` slash command).

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_SEARXNG_URL` | multi-base auto-discovery | Preferred search backend for `web_search` (also ConfigStore `search.searxng_url`). |
| `KAZMA_READ_URL_MAX_CHARS` | `16000` | Default window size for one `read_url` / `crawl_page` response. |
| `KAZMA_TOOL_RESULT_MAX_CHARS` | `100000` | Characters of one ordinary tool result the model sees; the rest is truncated. `0` or less = no limit. |
| `KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS` | `200000` | Higher cap for research and exec tools (`read_url`, `crawl_site`, digests, `web_search`, `shell_exec`, `python_exec`, …). |
| `KAZMA_RESEARCH_DIR` | `research` | Default workspace subfolder for auto-named `read_url_to_file` / crawl saves. |
| `KAZMA_RESEARCH_DIGEST_MAX` | `12000` | Max output size of `digest_research_file`. |
| `KAZMA_CRAWL_MAX_PAGES` | `50` | Hard ceiling for `crawl_site` `max_pages`. |
| `KAZMA_CRAWL_MAX_DEPTH` | `5` | Hard ceiling for `crawl_site` `max_depth`. |
| `KAZMA_FETCH_BACKEND` | `auto` | `auto` \| `httpx` \| `jina` \| `firecrawl`. |
| `KAZMA_FIRECRAWL_API_KEY` | unset | Optional Firecrawl scrape API (used in pre-fetch + hard-page recovery). |
| `KAZMA_FIRECRAWL_URL` | `https://api.firecrawl.dev` | Firecrawl API base (self-host OK). |
| `KAZMA_JINA_READER` | unset (recovery-on) | `1` = always try first; unset = last-resort recovery; `0`/`off` = never use Jina. |
| `JINA_API_KEY` / `KAZMA_JINA_API_KEY` | unset | Optional Jina Bearer token for higher rate limits. |
| `KAZMA_FETCH_MAX_BYTES` | `5000000` | Most bytes read from one fetched page (at least 65536). The rest is never read, so a huge file or a compression bomb cannot fill memory. |
| `KAZMA_CRAWL_RESPECT_ROBOTS` | `0` | `1` makes `crawl_site` obey each site's `robots.txt` (a call can still choose for itself). |
| `KAZMA_RESEARCH_ROUTE` | `soft` | For a request worded as deep research, the first turn tells the agent to use `run_research_pipeline` once rather than chain many searches by hand. `0` removes the hint. |
| `KAZMA_RESEARCH_SOFT_NUDGE` | `deep` | When the agent has searched but read fewer than `KAZMA_RESEARCH_MIN_SOURCES` full sources, it is told once not to answer from snippets: `deep` for deep-worded requests, any other value for every research request, `0` never. |
| `KAZMA_RESEARCH_MIN_SOURCES` | `2` | Full sources (`read_url`, `read_url_to_file`, crawls, digests) that count as enough for that nudge (1–8). |
| `KAZMA_RESEARCH_ALLOW_THIN` | unset | `1` lets a `depth="deep"` pipeline run finish with fewer sources than its minimum. Without it a deep run fails; a standard run only warns. |
| `KAZMA_RESEARCH_EXPORT_DOCX` | unset | `1` exports every pipeline report as DOCX too, as if `export_docx=True` had been passed. |
| `KAZMA_RESEARCH_GAP_LOOP` | on | After a deep run's first synthesis, a critic may ask for one more round of sources. `0` skips it. |
| `KAZMA_RESEARCH_LLM_PLANNER` | on | `0` plans search queries by heuristic instead of an LLM call. |
| `KAZMA_RESEARCH_LLM_CRITIC` | on | `0` checks a draft for gaps by heuristic instead of an LLM call. |
| `KAZMA_RESEARCH_PREFLIGHT_LIVE` | unset | `1` makes the research readiness check run a real micro-search, not only a configuration check. |
| `KAZMA_RESEARCH_SYNTH_MAX_IN` | `48000` | Characters of research files one `research_synthesize` call reads (4000–200000). |

Optional package: Playwright via `pip install 'kazma[web]'` then `playwright install chromium` (bot walls / thin JS shells).

**SearXNG ops:** `docker compose --profile search up -d searxng` → `http://127.0.0.1:8088` (JSON enabled in `deploy/searxng/settings.yml`).

### Knowledge base (libraries)

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_KB_AUTO_INJECT` | on | Passages from libraries marked auto-inject may be added to a turn's context. `0` turns that off for every library. |
| `KAZMA_KB_AUTO_INJECT_TOP_K` | `3` | Passages injected per turn (1–10). |
| `KAZMA_KB_SMART_SEARCH` | off | `1` also injects from every active library that has content (not only auto-inject ones) when the message looks technical. ConfigStore `knowledge.smart_search`; the env value wins. Tenant and archive filters still apply. |
| `KAZMA_KB_MAX_PAGES` | `200` | Pages one library ingest job may fetch (1–1000). |
| `KAZMA_KB_MAX_DEPTH` | `10` | Link depth of an ingest crawl when the site has no sitemap (0–20). |
| `KAZMA_KB_DELAY_MS` | `300` | Pause between page fetches of one ingest job (0–5000 ms). |
| `KAZMA_KB_SCOPE_MODE` | `tree` | Which links an ingest crawl follows: `tree` (same host and a shared topic path segment), `prefix` (strict path prefix), `domain` (same host, any path) or `exact` (the one page). |
| `KAZMA_KB_JINA_FALLBACK` | on | When a documentation page is bot-walled, ingest may fetch it through the free `r.jina.ai` reader, which sends the page URL to Jina. `0`, or `KAZMA_JINA_READER=0`, stops it. |

---

## OIDC / multi-user

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_OIDC_ISSUER` | unset | If SSO | OIDC issuer URL. |
| `KAZMA_OIDC_CLIENT_ID` | unset | If SSO | Client id. |
| `KAZMA_OIDC_CLIENT_SECRET` | unset | If SSO | Client secret (required for HS* `id_token`). |
| `KAZMA_OIDC_TENANT_CLAIM` | unset | No | The `id_token` claim naming the user's Kazma tenant; bound to the session at login. Unset = every OIDC user shares the default tenant. |
| `KAZMA_WS_GRAPH` | unset | No | `1` restores WS `send_prompt` / `approve_tool` as a second graph client (debug). Default: SSE only. |
| `KAZMA_OIDC_REDIRECT_URI` | `{KAZMA_PUBLIC_URL}/api/auth/oidc/callback` | If no public URL | Callback address registered with the IdP. Login refuses to start when neither this nor `KAZMA_PUBLIC_URL` is set. |
| `KAZMA_OIDC_SCOPES` | `openid profile email` | No | Scopes requested at login. |
| `KAZMA_OIDC_ROLE_CLAIM` | `role` | No | `id_token` claim naming the user's role (`roles`, then `groups`, are tried next). `admin` / `operator` / `viewer` are used as-is; `owner`, `member`, `readonly` and similar are mapped. |
| `KAZMA_OIDC_DEFAULT_ROLE` | `operator` | Keep `operator` or `viewer` | Role for a user whose token names no recognized role. Setting `admin` makes every such user an administrator. |
| `KAZMA_MULTI_USER` | unset | Multi-user | `1` applies the multi-user posture before any platform user exists: per-tenant filtering, opaque sessions always, and an RBAC check that cannot run denies the request instead of allowing it. |
| `KAZMA_SESSION_TTL_SECONDS` | `1209600` (14 days) | No | Lifetime of a browser session; at least 300. |
| `KAZMA_TENANT_ID` | `default` | CLI only | Tenant the `kazma` CLI acts as. |

See [OIDC IdP Setup](../ops/oidc-setup) and [Multi-user SaaS](../products/multi-user-saas).

---

## Email (Gmail / Microsoft Graph / sandbox)

Native skill `email-manager`. Default provider **`auto`**: real account if configured, else sandbox.

| Variable | Purpose |
|----------|---------|
| `EMAIL_DEFAULT_PROVIDER` | `auto` \| `sandbox` \| `gmail` \| `microsoft` \| `imap` \| `pop` |
| `EMAIL_GMAIL_ADDRESS` | Gmail address (filled by OAuth or IMAP/POP) |
| `EMAIL_GMAIL_APP_PASSWORD` | App password for IMAP/POP (often blocked on Workspace) |
| `EMAIL_GMAIL_AUTH` | `oauth` \| `imap` \| `pop` (set by Settings / OAuth) |
| `EMAIL_GMAIL_CLIENT_ID` / `EMAIL_GMAIL_CLIENT_SECRET` | Google OAuth web client (recommended) |
| `EMAIL_GMAIL_ACCESS_TOKEN` / `EMAIL_GMAIL_REFRESH_TOKEN` | Set by OAuth callback / refresh |
| `EMAIL_GMAIL_REDIRECT_URI` | Override callback (default `{public}/api/email/oauth/gmail/callback`) |
| `EMAIL_MS_ACCESS_TOKEN` | Graph bearer token (short-lived) |
| `EMAIL_MS_REFRESH_TOKEN` | Graph refresh token |
| `EMAIL_MS_CLIENT_ID` | Azure app client id |
| `EMAIL_MS_CLIENT_SECRET` | Azure app secret (confidential clients) |
| `EMAIL_MS_TENANT_ID` | Tenant (`common` default) |
| `EMAIL_MS_REDIRECT_URI` | Override callback (default `{public}/api/email/oauth/microsoft/callback`) |
| `EMAIL_MS_AUTH` | `oauth` \| `imap` \| `pop` |
| `EMAIL_MS_ADDRESS` / `EMAIL_MS_PASSWORD` | Microsoft IMAP/POP login |
| `EMAIL_MS_IMAP_HOST` / `EMAIL_MS_POP_HOST` / `EMAIL_MS_SMTP_HOST` | Override M365 protocol hosts |
| `KAZMA_PUBLIC_URL` | Public origin for OAuth redirects behind proxy |
| `EMAIL_ADDRESS` / `EMAIL_PASSWORD` | Generic IMAP/POP user |
| `EMAIL_PROTOCOL` | `imap` \| `pop` for generic account |
| `EMAIL_IMAP_HOST` / `EMAIL_IMAP_PORT` | IMAP (default 993) |
| `EMAIL_POP_HOST` / `EMAIL_POP_PORT` | POP3 SSL (default 995) |
| `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` | SMTP (default 587 STARTTLS) |
| `EMAIL_ACCOUNTS` | Comma-separated multi-account aliases |
| `EMAIL_ACCOUNT_{ALIAS}_TYPE` | `gmail` \| `microsoft` \| `imap` \| `pop` |
| `EMAIL_ACCOUNT_{ALIAS}_ADDRESS` / `_PASSWORD` | Per-account credentials |
| `EMAIL_ACCOUNT_{ALIAS}_*` | `IMAP_HOST`, `POP_HOST`, `REFRESH_TOKEN`, `CLIENT_ID`, … |

API: `GET /api/email/status`, `GET /api/email/presets`, `POST /api/email/protocol/connect\|disconnect`, OAuth start/callback routes.  
HITL: `email_send`, `email_delete`, `email_categorize`. Guide: [Email integration](../guide/email-integration).

---

## X publisher (official API)

Native skill `x-publisher`. Credentials live in Settings → X (vaulted ConfigStore keys) or env. Compose and plan on **X Studio** (`/x`). Guide: [X publisher](../guide/x-publisher).

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_X_POST` | unset (on if Settings enabled) | `0` hard-disables posting (Studio, chat, scheduled fire, and auto-reply publishes). |
| `KAZMA_X_SCHEDULE` | unset (on) | `0` disables scheduling only (`book_x_post` / Studio Schedule). |
| `KAZMA_X_REPLY` | unset (on if Settings enabled) | `0` disables mention auto-reply only. Scheduled posts still work. |
| `X_API_KEY` | unset | OAuth 1.0a consumer key (else `connectors.x.api_key`). |
| `X_API_KEY_SECRET` | unset | Consumer secret. |
| `X_ACCESS_TOKEN` | unset | User access token. |
| `X_ACCESS_TOKEN_SECRET` | unset | User access token secret. |

Do not use the app-only Bearer token. User authentication must be **Read and write**.

---

## Safety guards & embedder downloads (2026-08-19)

Opt-in hardening from the deep-structure audit
(`docs/audits/AUDIT_DEEP_STRUCTURE_2026-08-19.md`) — all default OFF
(current behavior preserved) unless noted.

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_HITL_CANONICAL_FLOOR` | on | The effective `require_approval_for` always includes the canonical danger tools, so Settings/YAML cannot narrow it below them. `0` opts out and logs a warning; the drift warning repeats every 15 min either way. (Opt-in `1` until 2026-09-16; on by default since audit F-5.) |
| `KAZMA_GATEWAY_STRICT_ALLOWLIST` | unset (compat) | Stop forcing `_allow_all` on the Telegram/Discord/Slack adapters — an empty allowlist then fails closed (no messages). Without it, forced allow-all logs a WARNING naming both remediations when no allowlist is configured. |
| `KAZMA_MCP_SCOPE_GUARD` | `1` (on) | Fail-close MCP tool calls when a per-task `workspace_scope` targets a different root than the process-bound MCP root (prevents silent wrong-repo operations). Set `0` only if the guard blocks a legitimate flow. |
| `KAZMA_EMBED_ALLOW_DOWNLOAD` | unset (contextual) | Force-allow the local embedder to download its model from HuggingFace. Fallback embedders (unknown provider / broken remote config) never download — they check the local HF cache and degrade to no embeddings with an actionable warning instead of stalling on a ~2GB download. Deliberate `local` configs keep first-run download rights. |
| `KAZMA_PERMISSIONS_ENFORCE` | unset | `1` enforces the per-user tool allowlists in `kazma-permissions.yaml` even before any `users:` entry exists, and denies every tool if the file cannot be read. Without it, enforcement starts once a user has an allow or deny list. |
| `KAZMA_DIVISION` | unset (ConfigStore `agent.division`) | Runs this install as a division: MCP servers listed under `divisions.<name>.denied_mcp_servers` in `kazma-permissions.yaml` are blocked, and each blocked call files an access request. Unset = no division checks at all. |
| `KAZMA_DIVISION_USER` | `default` | The user id division access requests are filed for. |
| `KAZMA_SELF_IMPROVEMENT` | on | `0` stops the self-improvement engine on the chat and swarm paths: no new Soul deltas are learned or injected. Read live. |
| `KAZMA_BUS_BRIDGE` | on | Lets `kazma mcp` (a process with no approval bus) ask for approval through the gate registry, where the dashboard shows the card. `0` removes that path, so `kazma mcp` withholds every danger tool. |
| `KAZMA_WATCHER_STALE_SECONDS` | `120` | How recent the running server's approval-watcher heartbeat must be for that bridge to engage. Without a fresh one nobody is reading approvals, so the tool is refused at once instead of after a timeout. |

---

## Switches that weaken a security default

Every variable here turns a protection **off**. They are listed together, on
the operator-facing page, because a switch nobody can discover is a switch
nobody can audit — including the operator who set it two years ago. Each row
says what stops protecting you, not just what the flag does.

`tests/test_static_gates.py::test_security_env_vars_are_documented` fails the
build if the code reads one of these and it is missing from **both**
`.env.example` and this table.

| Variable | Default | What it turns OFF | Safe to set when |
|----------|---------|-------------------|------------------|
| `KAZMA_AUTH_DISABLED` | unset | The entire HTTP auth gate. A configured `KAZMA_SECRET` becomes irrelevant — the switch disables the check that reads it. | Loopback only. **Refused at boot on a non-loopback bind** (`security/boot_guard.py`), and refused outright with `KAZMA_PRODUCTION=1`. |
| `KAZMA_DEV_WS_BYPASS` | unset | Authentication on **every WebSocket handshake**, including the chat socket that carries the turn stream. HTTP auth is unaffected, which is what makes it easy to miss. | Loopback only. **Refused at boot on a non-loopback bind**, same guard as `KAZMA_AUTH_DISABLED`. |
| `KAZMA_DEMO_MODE` | unset | The auth gate, deliberately, for a public throwaway demo (`fly.toml`). Allowed on a public bind and announced loudly at boot. | A throwaway instance with no real data, credentials or vault. Never on an install you care about. |
| `KAZMA_AUTOLOGIN_HOSTS` | loopback set | Restricts which hosts may be auto-issued a session cookie without presenting the secret. Widening it grants silent admin sessions to those hosts. | You control every host in the list. Adding a non-loopback host is equivalent to publishing the secret to it. |
| `KAZMA_GATEWAY_ADMINS` | unset | Who may run privileged chat-platform commands. Empty means the adapter's own allowlist is the only gate. | Set it explicitly on any multi-user chat platform. |
| `KAZMA_GATEWAY_STRICT_ALLOWLIST` | unset (compat) | *Enables* fail-closed adapters. Leaving it unset keeps the legacy forced `_allow_all` when no allowlist is configured. | Set to `1` on any deployment more than one person can message. |
| `KAZMA_HITL_CANONICAL_FLOOR` | on | `0` turns the floor off: Settings/YAML may then narrow `require_approval_for` below the canonical danger list. Those tools stay gated by their tier, but the graph interrupt and the swarm bus read the list itself. | Never — no deployment needs a narrower list. |
| `KAZMA_MCP_ALLOW_UNGATED` | unset | HITL approval for MCP tools that were not classified as safe. An MCP server's tool names are untrusted input. | Never, on an install with real credentials. Debugging a single trusted local server at most. |
| `KAZMA_MCP_SAFE_ALLOWLIST` | built-in | Widens the set of MCP tools that skip HITL by name. Names come from the server, so this is a list of names you are trusting a third party to choose honestly. | Only for tools you have read the implementation of. |
| `KAZMA_MCP_TRUSTED_IN_PROD` | unset | The production refusal for MCP servers not marked trusted. | You own every listed server. |
| `KAZMA_CODE_EXEC_ALLOW_LOCAL` | unset | The ban on running `python_exec` as a host subprocess when Docker is absent. The local fallback has an import blocklist; it is **not a jail**. | A lab box with nothing worth stealing. Production and multi-user ban it regardless — see `KAZMA_CODE_EXEC_DOCKER=force`. |
| `KAZMA_SHELL_ALLOW_MUTATE` | unset | The restricted PATH/allowlist applied to `shell_exec` *after* the human approves. Approval is consent; the allowlist is the containment. | You accept that an approved shell command runs unconstrained. |
| `KAZMA_YOLO_TTL_SECONDS` | `3600` | Lengthens the window in which further danger tools auto-approve after one YOLO grant. A long TTL turns one approval into an open session. `off` / `none` / `infinite` = no expiry; numbers are seconds, at least 60 (`0` means 60). | Short values only. This is a blast-radius dial. |
| `KAZMA_DISABLE_COST_BREAKER` | unset | The spend ceiling that stops a runaway loop. | Never unattended. |
| `KAZMA_CHAOS_ENABLED` | unset | *Enables* fault-injection routes. Off by default; the router does not mount without it. | Test environments only — must stay off in production. |
| `KAZMA_WS_ORIGIN_CHECK` | on | `0` turns off the Origin check on WebSocket handshakes. A page on any other site, opened in the operator's browser, can then open an authenticated socket to Kazma from a loopback or trusted-LAN address (cross-site WebSocket hijacking). | Never on an install a browser can reach. |
| `KAZMA_WS_EXTRA_ORIGINS` | unset | Adds origins the WebSocket Origin check accepts (exact `scheme://host[:port]`, comma-separated). Every listed origin may open an authenticated socket. | You serve the UI from that origin yourself (a tunnel, a second domain). |
| `KAZMA_OPAQUE_SESSIONS` | on | `0` makes the browser cookie carry the shared `KAZMA_SECRET` itself instead of a random server-side session id: sessions cannot be revoked one at a time, and a stolen cookie is the install's secret. Multi-user mode ignores it. | Never on a network-reachable install. |
| `KAZMA_RATE_LIMIT_ENABLED` | on when auth is on | `0` removes the per-client rate limits on chat streams, voice, research sessions, swarm dispatch and system flush. (They are already off with no `KAZMA_SECRET` and in demo mode.) | A single-operator loopback install. |
| `KAZMA_TENANT_FILTER` | on | `0` stops scoping knowledge-base libraries and swarm task lists to the caller's tenant in production / multi-user mode, so every tenant reads every tenant's rows. | A single-tenant install, where it changes nothing. |
| `KAZMA_SESSION_OPEN_TAKEOVER` | unset | The owner check on `/session <id>` in chat platforms. With `1` any allowlisted sender can switch onto another user's session, continue it, and answer the approval cards waiting on it (audit H-3). | Every allowlisted sender belongs to one team that shares everything. |
| `KAZMA_SEMANTIC_CACHE` | `false` | Isolation between users' LLM calls. `true` answers prompts from a shared cache of earlier responses that does not know who asked, so one user can receive another's answer to an identical or similar prompt. | Single-operator installs only. |
| `KAZMA_MCP_INHERIT_ENV` | unset | The environment allowlist for stdio MCP servers. With `1` every MCP server process receives Kazma's whole environment — `KAZMA_SECRET`, `KAZMA_VAULT_KEY`, provider API keys — instead of `PATH`, `TEMP`, `HOME` and similar. A server's own `env` / `auth` config is passed either way. | Practically never; pass what one server needs in its config instead. |
| `KAZMA_ALLOW_PRIVATE_LLM` | unset | The SSRF check on model discovery for custom OpenAI-compatible providers. With `1`, listing a provider's models may call a private or internal address, sending that provider's API key to it. Ollama and LM Studio discovery are separate and unaffected. | The model server is on a private network and only administrators can edit providers. |
| `KAZMA_DB_CLIENT_ALLOWED_HOSTS` | unset (loopback only) | Widens which hosts the database-client tools may connect to (comma-separated); loopback is always allowed. Queries stay read-only (`SELECT` / `WITH`). | Hosts you intend the agent to query. |
| `KAZMA_CLONE_HOSTS` | unset (github.com only) | The github.com-only rule for clones from the UI. A listed host is accepted over any scheme, plain `http://` included. | GitHub Enterprise or your own git server, reached over https. |
| `KAZMA_UPDATE_REMOTE_ALLOWLIST` | `github.com/mubder/kazma` | Which `origin` remotes `kazma update` trusts, as comma-separated substrings. The updater hard-resets the install to that remote and runs its installer, so this list decides whose code runs. | A fork you control; make each entry specific enough that it cannot match someone else's repository. |
| `KAZMA_HITL_GRANT_TTL_SECONDS` | `1800` | How long a "for this tool" approval lasts on a thread; until then that tool runs without asking. `off` / `none` / `infinite` keep it until it is cleared; numbers are seconds, at least 60 (`0` means 60). | Short values. Like `KAZMA_YOLO_TTL_SECONDS`, a blast-radius dial. |
| `KAZMA_UNRESTRICTED_TTL_SECONDS` | `3600` | How long `/unrestricted` (mission mode, and every danger tool without approval) stays on after the last user message. `0` / `off` keep it on until `/unrestricted off`; other numbers are at least 60. | Short values; never `0` on an install other people can message. |
| `KAZMA_SHELL_STRICT` | on in production | `0` in production lets an approved `shell_exec` look up its (allowlisted) program on the whole process PATH instead of the system directories, so a same-named executable planted earlier in any PATH directory runs instead. `1` turns strict lookup on outside production. | Never `0` in production. |
| `KAZMA_SHELL_ALLOW_ARCHIVE` | unset | Puts `tar`, `gzip`, `gunzip`, `zip` and `unzip` back on the `shell_exec` allowlist in production strict mode. An archive can write outside the working directory through absolute or `../` member paths. | You need archives in production and accept extraction anywhere the process can write. |
| `KAZMA_GATE_REGISTRY` | on | `0` turns off the approval registry (one row per approval, answered once). The gate-identity check on `/api/approve` goes with it, so a retried Approve can decide a question the human has not seen; approvals fall back to the bare checkpoint. | Only briefly, to rule the registry out while diagnosing it. |
| `KAZMA_COMMITMENT_ENABLED` | on | `0` removes the commitment layer: reminders are no longer checked against memory (a date the model invented is scheduled as written), the exec denylist no longer stops catastrophic commands before the approval card, protected settings keys and the outbound allowlist stop applying, and swarm workers lose their scope cap. | Never on a real install; it exists to rule the layer out while diagnosing. |
| `KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE` | on | `0` lifts the cap on dispatched swarm workers: they may then attempt exec, outbound, config and identity acts, Soul changes included (approval cards still apply). | Never with workers you did not write. |
| `KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM` | off; on in production / multi-user | `0` in production or multi-user mode lets self-improvement Soul changes apply without an operator confirming them. `1` requires the confirmation anywhere. | A single-operator install that reviews Soul changes another way. |
| `KAZMA_COMMITMENT_MODE` | `balanced` | `yolo` skips the reminder and cancel checks, so a reminder is scheduled at whatever time the model wrote — the incident class the layer exists for; `autonomous` stops asking when a reminder is ambiguous and takes the from-now date. Approval cards and the exec denylist still apply. `strict` asks more often. | `balanced` or `strict`; `yolo` for testing only. |

---

## Agent loop, turns & context

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_TURN_TIMEOUT_SECONDS` | `600` | Wall-clock budget for one turn in the agent runner and on the WebSocket chat path; the turn is stopped when it runs out. The shared turn helper other paths use applies it only when it is set. |
| `KAZMA_TOOL_TIMEOUT_SECONDS` | `120` | Wall-clock limit for one tool call; `0` or less disables it. ConfigStore `agent.tool_timeout_seconds` wins. |
| `KAZMA_TOOL_RESULT_FILE_MAX_CHARS` | `32000` | Cap for file tools (`file_read`, `file_search`, `codebase_search`, the filesystem MCP reads, …) — tighter than the research cap, so a file read cannot re-inflate a prompt that was just trimmed. |
| `KAZMA_NO_TRUNCATE` | unset | `1` turns tool-result truncation off entirely; one large result can then fill the context window. |
| `KAZMA_COMPACT_PRESERVE_TOOL_RESULTS` | `12` | Tool results kept when a conversation is compacted. Dropping them all made the agent repeat the same calls. |
| `KAZMA_SEMANTIC_COMPACT` | on | When history is trimmed (`/compact`, the 80% budget, a context-overflow error), the dropped part is summarized back into the conversation. `0` drops it without a summary. |
| `KAZMA_SUMMARIES_MAX_ENTRIES` | `500` | Conversation summaries held in memory, one per thread; the least recently used go first. |
| `KAZMA_PROMPT_CACHE` | on | Packs system notes into one stable prefix plus one per-turn block, so provider prompt caches hit (Anthropic gets explicit cache breakpoints). `0` restores the old one-message-per-note layout. |
| `KAZMA_DETACHED_TTL_S` | `300` | A turn whose client disconnected is cancelled after this many seconds, so nobody pays for LLM calls no one will read. The clock starts when the client leaves, not when the turn began. |
| `KAZMA_TURN_DURABLE_INTERVAL_S` | `2.0` | While an answer streams, its text is saved to the turn journal at least this often (that is what a refresh or reconnect recovers) … |
| `KAZMA_TURN_DURABLE_CHARS` | `600` | … or after this many new characters, whichever comes first. |
| `KAZMA_TURN_LIVENESS_GRACE_S` | `600` | How long a reply turn may sit open with nothing working on it and no approval pending before it is reported (at least 60). |
| `KAZMA_TURN_LIVENESS_HEAL` | off | `1` also closes such a turn. Off by default: closing is what the original bug did wrong, so turn it on only once the reports show what accumulates. |
| `KAZMA_INTENT_ENGINE` | on | The intent engine classifies each turn and may route it to a handler or add a plan note. `0` sends every turn straight to the agent loop (ConfigStore `agent.intent.enabled`). |
| `KAZMA_INTENT_EXECUTE` | on | `0` keeps the classification and plan notes but never runs a handler directly (`agent.intent.execute_enabled`). |
| `KAZMA_INTENT_TIER2` | on | `0` skips the LLM refinement of borderline classifications, leaving heuristics only (`agent.intent.tier2_enabled`). |
| `KAZMA_PERSONALITY` | `default` | Personality when neither a runtime switch nor `agent.personality` names one: `default`, `friendly_expert`, `concise`, `gulf_engineer`, `creative_partner`, `sysadmin`, `teacher` or `code_reviewer`. An unknown name falls back to `default` with a warning. |
| `KAZMA_LONG_TASK_TTL_SECONDS` | `1800` | How long a `/long` budget stays on a thread. `off` / `none` / `infinite` keep it until turned off; numbers are seconds, at least 60 (`0` means 60). |
| `KAZMA_LONG_TASK_MAX_ITER` | `100` | Most iterations a `/long` budget may ask for (5–100). |
| `KAZMA_LONG_TASK_MAX_RECURSION` | `500` | Most graph steps a `/long` turn may take (50–500). |
| `KAZMA_MISSION_MAX_ROUNDS` | `500` | Tool rounds one mission-mode turn may run before it must stop (40–2000). |
| `KAZMA_MISSION_RECURSION` | 5 × rounds | Graph steps for a mission turn (500–5000); follows `KAZMA_MISSION_MAX_ROUNDS` unless set. |

---

## MCP (client and server)

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_MCP_TIMEOUT_MS` | `90000` | Request timeout, in milliseconds, for MCP servers whose config sets no `timeout` (which is in seconds, and wins). |
| `KAZMA_MCP_STDIO_LIMIT` | `16777216` (16 MiB) | Largest single message a stdio MCP server may send (256 KiB–64 MiB). A bigger one used to break the pipe for every later call. |
| `KAZMA_MCP_TOOLS` | unset (all) | Comma-separated names of the tools `kazma mcp` publishes to MCP clients. Danger tools still need an approval path (`KAZMA_BUS_BRIDGE`). |
| `KAZMA_MCP_IDE_ENABLED` | `true` | Anything but `1` / `true` / `yes` makes the gateway's MCP IDE server (`write_file`, `run_tests`, … behind `KAZMA_SECRET` and the safety check) refuse tool calls. |

The switches that loosen MCP (`KAZMA_MCP_INHERIT_ENV`, `KAZMA_MCP_ALLOW_UNGATED`, …) are in [the security table](#switches-that-weaken-a-security-default).

---

## Paths & data locations

Each of these moves one file or directory. A store moved **outside** the data
directory is not in the universal backup or the migration bundle, which both
work from the data directory — to relocate data, move `KAZMA_DATA_DIR` as a
whole.

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_DATA_DIR` | `<install>/kazma-data` | Every store, attachment and backup. |
| `KAZMA_USER_HOME` | `<install>/.kazma` | Logs, guard state, the hub registry, the restic passphrase. A legacy `~/.kazma` is moved here once at startup. |
| `KAZMA_PROJECT_ROOT` | found by walking up to `pyproject.toml` | Install root, used only when that walk finds nothing (the working directory is the last resort). `kazma mcp` pins it when set. |
| `KAZMA_LOG_FILE` | `<.kazma>/kazma.log` | Application log file. |
| `KAZMA_BACKUPS_DIR` | `<data>/backups` | Universal backups. |
| `KAZMA_EXPORTS_DIR` | `<data>/exports` | JSONL / GraphML memory exports. |
| `KAZMA_FTS5_PATH` | `<data>/memory.db` | Full-text memory store. |
| `KAZMA_VECTOR_PATH` | `<data>/vector_memory` | ChromaDB vector memory. |
| `KAZMA_VECTOR_DB` | `<data>/vector.db` | sqlite-vec vector store. |
| `KAZMA_KNOWLEDGE_GRAPH_DB` | `<data>/knowledge_graph.db` | Knowledge graph (SQLite provider). |
| `KAZMA_MEMORY_STATE_DB` | `<data>/memory_state.db` | V2 memory: beliefs, episodes, entities (the hot read path). |
| `KAZMA_MEMORY_OPS_DB` | `<data>/memory_ops.db` | V2 memory task queue and audit log (kept apart so background writes never contend with chat reads). |
| `KAZMA_ARTIFACTS_DB` | `<data>/agent_artifacts.db` | Saved drafts and scratchpad findings. |
| `KAZMA_FILE_CHECKPOINTS_DB` | `<data>/file_checkpoints.db` | Checkpoints that undo the agent's workspace file edits. |
| `KAZMA_HUB_DB` | `<.kazma>/hub/registry.db` | Skill-hub registry (`kazma hub --registry-path` wins). |

---

## Backups & retention

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_BACKUP_RETENTION` | `7` | Universal backups kept (at least 1). Wins over `backups.retention`. |
| `KAZMA_PG_BACKUP_ENABLED` | on with Postgres | `0` stops the 6-hourly `pg_dump` of Kazma's Postgres tables, whatever `backups.pg.enabled` says. |
| `KAZMA_PG_BACKUP_RETENTION` | `3` | Local Postgres dumps kept (restic keeps the history). Wins over `backups.pg.retention`. |
| `KAZMA_RESTIC_PASSWORD` | `<.kazma>/restic.pass` | Passphrase of the restic repository; wins over that file. A missing passphrase is reported as an error — Kazma does not invent one, since a key kept only on the disk the backup protects is no key. Keep a copy off this machine. |
| `KAZMA_CHECKPOINT_RETENTION_DAYS` | on | `0` (or less) stops the daily pruning of the SQLite chat-checkpoint store. Any other value leaves the policy unchanged: the newest 200 checkpoints per thread, 10 for threads idle 30 days. Postgres checkpoints are not pruned. |

---

## Cron & scheduling

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_TZ` | `UTC` | Time zone for cron schedules when `cron.timezone` (Settings) is empty. |
| `KAZMA_CRON_STALE_HOURS` | `24` | A one-off job that fell due while Kazma was down still runs if it is at most this many hours late (at least 1); an older one is skipped. |

---

## Logging, alerts & tracing

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. `logging.level` in Settings wins. |
| `KAZMA_LOG_FORMAT` | `text` | `json` writes one JSON object per line. `logging.format: json` in Settings wins. |
| `KAZMA_LOG_RETENTION_DAYS` | `7` | Daily log files kept before the oldest is deleted. `logging.retention_days` in Settings wins. |
| `KAZMA_OPS_ALERTS` | on | `0` mutes operational alerts (backups, offsite, MCP, persistence, failed turns). Startup and shutdown notices are separate and stay on. |
| `KAZMA_OPS_ALERT_COOLDOWN_S` | `900` | Quiet period before the same alert may fire again. |
| `KAZMA_DAILY_DIGEST` | on | A short daily summary of what ran, failed and recovered, sent through the alert channel, so silence means healthy rather than dead. `0` stops it. |
| `KAZMA_DIGEST_INTERVAL_HOURS` | `24` | Hours between digests. |
| `KAZMA_LOOP_STALL_WATCHDOG` | on | Writes every thread's stack to `.kazma/stall-*.txt` when the event loop stops responding for 15 s. `0` turns it off (the test suite does). |
| `KAZMA_OTLP_ENDPOINT` | unset | OTLP/HTTP collector (e.g. `http://localhost:4318`); swarm spans are posted as JSON to `<endpoint>/v1/traces`. |

---

## CLI: update & hub

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_UPDATE_VERIFY_SIGNATURES` | unset | `1` makes `kazma update` refuse a target commit whose signature `git verify-commit` cannot verify. |
| `KAZMA_HUB_URL` | `https://hub.kazma.ai` | Skill-hub API for `kazma hub` (`--hub-url` wins). |

Which remotes `kazma update` trusts at all is `KAZMA_UPDATE_REMOTE_ALLOWLIST`, in [the security table](#switches-that-weaken-a-security-default).

---

## Guard (supervisor, `scripts/service/kazma_guard.py`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_GUARD_CMD` | `serve.py` | Command the guard runs and supervises. |
| `KAZMA_GUARD_CWD` | repo root | Working directory for it. |
| `KAZMA_GUARD_HEALTH_URL` | `http://127.0.0.1:9090/health/ready` | Probe target. |
| `KAZMA_GUARD_START_TIMEOUT` | `900` | Seconds allowed to first ready. |
| `KAZMA_GUARD_INTERVAL` | `30` | Seconds between probes. |
| `KAZMA_GUARD_PROBE_TIMEOUT` | — | Per-probe timeout. |
| `KAZMA_GUARD_FAILURES` | `3` | Consecutive failed probes before a restart. Probes that cannot get a local port (`WinError 10048/10055`) never count — see [Deployment §6](../guide/deployment). |
| `KAZMA_GUARD_PAGE_COOLDOWN_S` | — | Minimum gap between identical pages. |
| `KAZMA_GUARD_GRACEFUL_STOP_S` | `60` | Seconds a deliberate stop (reload, maintenance pause, guard shutdown) waits for the server to shut itself down before it is killed. |
| `KAZMA_GUARD_LOG` / `KAZMA_GUARD_STATE` | `<install>/.kazma/` | Guard log, and the state file: child PID, the guard's heartbeat, reload acknowledgements. |
| `KAZMA_GUARD_RELOAD_FILE` / `KAZMA_GUARD_PAUSE_FILE` | `<install>/.kazma/` | The `--reload` request and the `--pause` flag. |
| `KAZMA_GUARD_STATE_FILE` | set by the guard | Given to the server the guard spawns: the path of the guard's state file. |
| `KAZMA_GUARD_TELEGRAM_TOKEN` / `KAZMA_GUARD_TELEGRAM_CHAT` | vault / `SWARM_*` | Direct Telegram paging, independent of the app. |

## Cost, chaos, tests

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_MAX_COST` | cost breaker default | USD budget ceiling. |
| `KAZMA_HARD_MAX_COST` | `15.00` | USD ceiling that halts the agent at once, whatever the silence window (`KAZMA_MAX_COST` only trips after the user has been silent). Wins over `safety.hard_max_cost`. |
| `KAZMA_TEST_BACKGROUND_SCHEDULERS` | unset | Test-only: `1` starts the memory worker's background schedulers under pytest, where they otherwise never run. |
| `KAZMA_SILENCE_WINDOW` | cost breaker default | Silence window seconds. |
| `KAZMA_CHAOS_ENABLED` | unset | Chaos routes (must stay off in prod). |
| `KAZMA_SMOKE_BASE` | `http://127.0.0.1:9090` | Smoke test base URL. |
| `KAZMA_TEST_FORCE_OUTPUT_ROUTING` | unset | Test-only gateway output routing. |
| `KAZMA_CODE_EXEC_DOCKER` | policy | `force` in hardened Docker compose for code_exec jail. |
| `KAZMA_MARKET_STUB` | `0` | Example skill market data stub. |

---

## GitHub

| Variable | Purpose |
|----------|---------|
| `GITHUB_TOKEN` / OAuth path | Native git tools & GitHub client (prefer OAuth→PAT chain in app). |
| `KAZMA_BOT_NAME` / `KAZMA_BOT_EMAIL` | Commit identity for the agent's git commits (default `kazma-agent[bot]@users.noreply.github.com`). Setting either turns the bot identity on, as `git.bot_identity.enabled` does; an explicit email also wins over the one derived from a GitHub App. |
| `KAZMA_GITHUB_APP_ID` / `KAZMA_GITHUB_APP_INSTALLATION_ID` | Commit and authenticate as a GitHub App; with both set the app identity is on (overrides `connectors.github.app_*`). |
| `KAZMA_GITHUB_APP_PRIVATE_KEY` / `KAZMA_GITHUB_APP_PRIVATE_KEY_PATH` | The app's private key, inline or as a file path. |
| `KAZMA_GITHUB_APP_SLUG` | The app's slug, used to find the app's own bot email (the one that carries its avatar). |
| `KAZMA_CLONE_DIR` | See [Workspace, memory, demo](#workspace-memory-demo). `KAZMA_CLONE_HOSTS` is in the security table. |

---

## Profiles (quick)

### Local single-operator

```bash
# Loopback; secret auto-generated if missing
KAZMA_HOST=127.0.0.1
# optional OPENAI_API_KEY=...
kazma serve 9090
```

### Docker / reverse proxy

```bash
KAZMA_HOST=0.0.0.0
KAZMA_SECRET=<strong-random>
KAZMA_PRODUCTION=1
KAZMA_VAULT_KEY=<strong-random>
KAZMA_PUBLIC_URL=https://your.domain
KAZMA_TRUSTED_PROXIES=127.0.0.1   # REQUIRED — the proxy's address, not the client's
KAZMA_TRUST_LAN=0
KAZMA_CODE_EXEC_DOCKER=force
```

Under Docker, `KAZMA_TRUSTED_PROXIES` is the proxy **container's** address on
the bridge network (often `172.17.0.1` or the compose network's gateway), not
`127.0.0.1`.

### Multi-replica SaaS

```bash
KAZMA_DATABASE_URL=postgresql://…
KAZMA_PRODUCTION=1
KAZMA_SECRET=…
KAZMA_VAULT_KEY=…
KAZMA_PUBLIC_URL=https://…
KAZMA_TRUSTED_PROXIES=<load-balancer / ingress address>
# optional OIDC_*
```

---

## Related

- [Configuration](../guide/configuration) — YAML keys & ConfigStore  
- [Postgres & SaaS](../ops/postgres-and-saas)  
- [Production checklist](../ops/production-checklist)  
- [Security & Safety](../guide/security-and-safety)  
