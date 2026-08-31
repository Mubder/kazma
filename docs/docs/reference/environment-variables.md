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
| `KAZMA_REMOTE_PARSE` | `1` | `0` skips LlamaParse/Reducto salvage |
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
| `KAZMA_REALTIME_CODEC` | `0` | `1` uses REST STT/TTS as the audio codec. OpenAI Realtime / Gemini Live are skipped |

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
| `KAZMA_DOCUMENTS_METADATA_BACKEND` | `auto` | `sqlite` / `postgres` / `auto` (auto follows jobs backend). Postgres metadata enables multi-replica CRUD; GC SQL still SQLite-only (skipped on PG with honest error) |
| `KAZMA_DOCUMENT_SOAK_ITERATIONS` | `100` | Soak iteration count for `certify_documents.py --soak` |

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
| `KAZMA_CORS_ORIGINS` | unset | If browser cross-origin | Comma-separated origins. |
| `KAZMA_ENV_FILE` | unset | No | Absolute path to an extra `.env`, loaded **last** so it wins. The `.env` ladder is: `<kazma home>/.env` → `<cwd>/.env` → this. Nothing outside the installation is read unless you name it here. |
| `KAZMA_TRUSTED_PROXIES` | unset | **Yes** behind any proxy | Comma-separated addresses the reverse proxy connects **from** (`127.0.0.1` for same-host nginx/Caddy, the bridge IP under Docker). Only these peers may set `X-Forwarded-For` / `X-Forwarded-Proto`. See the warning below. |
| `KAZMA_LOOPBACK_AUTOLOGIN` | `0` | Keep `0` | Re-enables credential-less loopback login even with a proxy declared. Only for a host where `127.0.0.1` really is just you. |
| `KAZMA_TRUST_LAN` | `0` | Keep `0` unless needed | LAN trust for auth middleware. |
| `KAZMA_AUTH_DISABLED` | unset | **Never in prod** | Disables auth helpers — dev only. |
| `KAZMA_ALLOW_YOLO` | unset | Avoid | Only way to re-enable YOLO when `KAZMA_PRODUCTION=1`. |
| `KAZMA_VERBOSE_ERRORS` | `0` | Keep `0` | Appends the real exception message to API errors (still redacted for paths and credentials). Dev only — production returns a code plus a correlation id. |

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
does. `serve.py` passes `--proxy-headers --forwarded-allow-ips` to uvicorn
from this variable automatically.

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
| `KAZMA_PG_POOL_RETRIES` | `5` | Optional | Connection-pool creation retry count. Handles transient failures (Windows Docker-bridge, container mid-startup). |
| `KAZMA_PG_POOL_RETRY_DELAY` | `1.0` | Optional | Seconds between pool-creation retries. |
| `KAZMA_PG_POOL_MIN` | `1` | Optional | Minimum connections in the psycopg pool. |
| `KAZMA_PG_POOL_MAX` | `10` | Optional | Maximum connections in the psycopg pool. |

---

## Sandbox (E2B) & durable swarm (Temporal)

Default remains Docker/local `python_exec` and in-process swarm. These are
opt-in for untrusted code and multi-hour work. See [Architecture](../guide/architecture)
and extras `kazma[sandbox]` / `kazma[durable]`.

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_E2B_API_KEY` / `E2B_API_KEY` | unset | Untrusted / multi-user code | E2B Firecracker for HITL-approved `python_exec`. |
| `KAZMA_E2B` | auto if key set | No | `0` keeps Docker/local even with a key. |
| `KAZMA_CODE_EXEC_DOCKER` | `auto` | Single-operator jail | `1`/`force` Docker; `0` local (ignored when production forbids local). |
| `KAZMA_TEMPORAL_HOST` / `TEMPORAL_ADDRESS` | unset | Multi-hour swarm | Temporal frontend (`localhost:7233`). Wraps swarm `_dispatch_inner`. |
| `KAZMA_TEMPORAL` | auto if host set | No | `0` keeps in-process swarm. |
| `KAZMA_TEMPORAL_REQUIRED` | unset | Strict HA | `1` = fail the task if Temporal/SDK is down (no in-process fallback). |
| `KAZMA_TEMPORAL_NAMESPACE` | `default` | No | Temporal namespace. |
| `KAZMA_TEMPORAL_QUEUE` | `kazma-swarm` | No | Task queue for the in-process Temporal worker. |
| `KAZMA_CODE_INDEX` | on | No | `0` disables the workspace symbol index + `codebase_search`. |
| `KAZMA_IDE_LSP` | on | No | `0` disables Monaco hover/complete/definition/diagnostics on `/ide`. |

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
| `GOOGLE_CALENDAR_TOKEN` / `MS_CALENDAR_TOKEN` | unset | Optional | OAuth token for the calendar skill's Google / Outlook backend. |
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
| `KAZMA_PGVECTOR` | auto when a Postgres DSN is set | No | `0` keeps sqlite-vec even if Postgres is on. Unset = pgvector auto-select from `KAZMA_DATABASE_URL` / `memory.backends.state.url`. Explicit Qdrant in Settings still wins. |

---

## Web search, scrape & research

See [Web research](../guide/web-research) for playbooks. Tools are used from **chat** (no `/research` slash command).

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_SEARXNG_URL` | multi-base auto-discovery | Preferred search backend for `web_search` (also ConfigStore `search.searxng_url`). |
| `KAZMA_READ_URL_MAX_CHARS` | `16000` | Default window size for one `read_url` / `crawl_page` response. |
| `KAZMA_TOOL_RESULT_MAX_CHARS` | `4000` | Graph truncate cap for ordinary tools. |
| `KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS` | `16000` | Higher graph truncate for research tools (`read_url`, digests, `crawl_site`, …). |
| `KAZMA_RESEARCH_DIR` | `research` | Default workspace subfolder for auto-named `read_url_to_file` / crawl saves. |
| `KAZMA_RESEARCH_DIGEST_MAX` | `12000` | Max output size of `digest_research_file`. |
| `KAZMA_CRAWL_MAX_PAGES` | `50` | Hard ceiling for `crawl_site` `max_pages`. |
| `KAZMA_CRAWL_MAX_DEPTH` | `5` | Hard ceiling for `crawl_site` `max_depth`. |
| `KAZMA_FETCH_BACKEND` | `auto` | `auto` \| `httpx` \| `jina` \| `firecrawl`. |
| `KAZMA_FIRECRAWL_API_KEY` | unset | Optional Firecrawl scrape API (used in pre-fetch + hard-page recovery). |
| `KAZMA_FIRECRAWL_URL` | `https://api.firecrawl.dev` | Firecrawl API base (self-host OK). |
| `KAZMA_JINA_READER` | unset (recovery-on) | `1` = always try first; unset = last-resort recovery; `0`/`off` = never use Jina. |
| `JINA_API_KEY` / `KAZMA_JINA_API_KEY` | unset | Optional Jina Bearer token for higher rate limits. |

Optional package: Playwright via `pip install 'kazma[web]'` then `playwright install chromium` (bot walls / thin JS shells).

**SearXNG ops:** `docker compose --profile search up -d searxng` → `http://127.0.0.1:8088` (JSON enabled in `deploy/searxng/settings.yml`).

---

## OIDC / multi-user

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_OIDC_ISSUER` | unset | If SSO | OIDC issuer URL. |
| `KAZMA_OIDC_CLIENT_ID` | unset | If SSO | Client id. |
| `KAZMA_OIDC_CLIENT_SECRET` | unset | If SSO | Client secret (required for HS* `id_token`). |
| `KAZMA_WS_GRAPH` | unset | No | `1` restores WS `send_prompt` / `approve_tool` as a second graph client (debug). Default: SSE only. |

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
| `KAZMA_X_POST` | unset (on if Settings enabled) | `0` hard-disables posting (Studio, chat, and scheduled fire). |
| `KAZMA_X_SCHEDULE` | unset (on) | `0` disables scheduling only (`book_x_post` / Studio Schedule). |
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
| `KAZMA_HITL_CANONICAL_FLOOR` | unset (off) | Union the canonical danger-tool list back into the effective `require_approval_for`, so Settings/YAML cannot narrow below it. Strict multi-operator deployments should set `1`. The drift warning repeats every 15 min regardless. |
| `KAZMA_GATEWAY_STRICT_ALLOWLIST` | unset (compat) | Stop forcing `_allow_all` on the Telegram/Discord/Slack adapters — an empty allowlist then fails closed (no messages). Without it, forced allow-all logs a WARNING naming both remediations when no allowlist is configured. |
| `KAZMA_MCP_SCOPE_GUARD` | `1` (on) | Fail-close MCP tool calls when a per-task `workspace_scope` targets a different root than the process-bound MCP root (prevents silent wrong-repo operations). Set `0` only if the guard blocks a legitimate flow. |
| `KAZMA_EMBED_ALLOW_DOWNLOAD` | unset (contextual) | Force-allow the local embedder to download its model from HuggingFace. Fallback embedders (unknown provider / broken remote config) never download — they check the local HF cache and degrade to no embeddings with an actionable warning instead of stalling on a ~2GB download. Deliberate `local` configs keep first-run download rights. |

---

## Cost, chaos, tests

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_MAX_COST` | cost breaker default | USD budget ceiling. |
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
