---
id: environment-variables
title: Environment Variables
sidebar_label: Environment Variables
description: Master reference for Kazma environment variables (dev, single-operator, production)
---

> Complete env reference for local, Docker, and production. Prefer strong secrets; never commit `.env` with real keys. Also see [Configuration](../guide/configuration) for `kazma.yaml` and ConfigStore.

## Precedence (reminder)

1. **Specific helpers** may read env first (`KAZMA_SECRET`, vault, disclosure).  
2. **ConfigStore DB** wins for most runtime settings.  
3. **`kazma.yaml`** seeds missing DB keys.  
4. **Hardcoded defaults** last.

Generic `ConfigStore.get()` does **not** automatically overlay every env var — only documented keys below that code explicitly reads.

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
| `KAZMA_TRUST_LAN` | `0` | Keep `0` unless needed | LAN trust for auth middleware. |
| `KAZMA_AUTH_DISABLED` | unset | **Never in prod** | Disables auth helpers — dev only. |
| `KAZMA_ALLOW_YOLO` | unset | Avoid | Only way to re-enable YOLO when `KAZMA_PRODUCTION=1`. |

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

---

## LLM / provider

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `OPENAI_API_KEY` | unset | One provider key | OpenAI-compatible key. |
| `KAZMA_API_KEY` | unset | Fallback | Generic API key fallback. |
| `KAZMA_PROVIDER` | unset | Optional boot | Provider id at startup. |
| `KAZMA_MODEL` | unset | Optional boot | Model id at startup. |
| Provider-specific | — | As used | e.g. Anthropic, DeepSeek, xAI, OpenRouter, Google ADC — see Configuration. |

---

## Workspace, memory, demo

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_WORKSPACE` | active WorkspaceStore / data dir | Prod root policy | Agent filesystem workspace root. |
| `KAZMA_VECTOR_COLLECTION` | `agent_memory` | No | Chroma collection name. |
| `KAZMA_VECTOR_MODEL` | `all-MiniLM-L6-v2` | No | Embedding model id. |
| `KAZMA_DEMO_MODE` | unset | **No** | Demo fixtures — never enable in real prod. |

---

## Web search, scrape & research

See [Web research](../guide/web-research) for playbooks. Tools are used from **chat** (no `/research` slash command).

| Variable | Default | Purpose |
|----------|---------|---------|
| `KAZMA_SEARXNG_URL` | auto `http://localhost:8088` if up | Preferred search backend for `web_search`. |
| `KAZMA_READ_URL_MAX_CHARS` | `16000` | Default window size for one `read_url` / `crawl_page` response. |
| `KAZMA_TOOL_RESULT_MAX_CHARS` | `4000` | Graph truncate cap for ordinary tools. |
| `KAZMA_TOOL_RESULT_RESEARCH_MAX_CHARS` | `16000` | Higher graph truncate for research tools (`read_url`, digests, `crawl_site`, …). |
| `KAZMA_RESEARCH_DIR` | `research` | Default workspace subfolder for auto-named `read_url_to_file` / crawl saves. |
| `KAZMA_RESEARCH_DIGEST_MAX` | `12000` | Max output size of `digest_research_file`. |
| `KAZMA_CRAWL_MAX_PAGES` | `50` | Hard ceiling for `crawl_site` `max_pages`. |
| `KAZMA_CRAWL_MAX_DEPTH` | `5` | Hard ceiling for `crawl_site` `max_depth`. |
| `KAZMA_FETCH_BACKEND` | `auto` | `auto` \| `httpx` \| `jina` \| `firecrawl`. |
| `KAZMA_FIRECRAWL_API_KEY` | unset | Optional Firecrawl scrape API. |
| `KAZMA_FIRECRAWL_URL` | `https://api.firecrawl.dev` | Firecrawl API base (self-host OK). |
| `KAZMA_JINA_READER` | unset | Set `1` / `true` to try Jina Reader (`r.jina.ai`). |

Optional package: Playwright via `pip install 'kazma[web]'` then `playwright install chromium` (bot walls / thin JS shells).

---

## OIDC / multi-user

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_OIDC_ISSUER` | unset | If SSO | OIDC issuer URL. |
| `KAZMA_OIDC_CLIENT_ID` | unset | If SSO | Client id. |
| `KAZMA_OIDC_CLIENT_SECRET` | unset | If SSO | Client secret. |

See [OIDC IdP Setup](../ops/oidc-setup) and [Multi-user SaaS](../products/multi-user-saas).

---

## Email (Gmail / Microsoft Graph / sandbox)

Native skill `email-manager`. Default provider **`auto`**: real account if configured, else sandbox.

| Variable | Purpose |
|----------|---------|
| `EMAIL_DEFAULT_PROVIDER` | `auto` \| `sandbox` \| `gmail` \| `microsoft` \| `imap` |
| `EMAIL_GMAIL_ADDRESS` | Gmail address (filled by OAuth or manual) |
| `EMAIL_GMAIL_APP_PASSWORD` | Optional app password (often blocked on Workspace) |
| `EMAIL_GMAIL_CLIENT_ID` / `EMAIL_GMAIL_CLIENT_SECRET` | Google OAuth web client (recommended) |
| `EMAIL_GMAIL_ACCESS_TOKEN` / `EMAIL_GMAIL_REFRESH_TOKEN` | Set by OAuth callback / refresh |
| `EMAIL_GMAIL_REDIRECT_URI` | Override callback (default `{public}/api/email/oauth/gmail/callback`) |
| `EMAIL_MS_ACCESS_TOKEN` | Graph bearer token (short-lived) |
| `EMAIL_MS_REFRESH_TOKEN` | Graph refresh token |
| `EMAIL_MS_CLIENT_ID` | Azure app client id |
| `EMAIL_MS_CLIENT_SECRET` | Azure app secret (confidential clients) |
| `EMAIL_MS_TENANT_ID` | Tenant (`common` default) |
| `EMAIL_MS_REDIRECT_URI` | Override callback (default `{public}/api/email/oauth/microsoft/callback`) |
| `KAZMA_PUBLIC_URL` | Public origin for OAuth redirects behind proxy |
| `EMAIL_ADDRESS` / `EMAIL_PASSWORD` | Generic IMAP user |
| `EMAIL_IMAP_HOST` / `EMAIL_IMAP_PORT` | IMAP (default 993) |
| `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` | SMTP (default 587 STARTTLS) |
| `EMAIL_ACCOUNTS` | Comma-separated multi-account aliases |
| `EMAIL_ACCOUNT_{ALIAS}_TYPE` | `gmail` \| `microsoft` \| `imap` |
| `EMAIL_ACCOUNT_{ALIAS}_ADDRESS` / `_PASSWORD` | Per-account credentials |
| `EMAIL_ACCOUNT_{ALIAS}_*` | `IMAP_HOST`, `REFRESH_TOKEN`, `CLIENT_ID`, … |

API: `GET /api/email/status`, `POST /api/email/oauth/microsoft/device/start|poll`, `POST /api/email/oauth/microsoft/disconnect`.  
HITL: `email_send`, `email_delete`, `email_categorize`. Guide: [Email integration](../guide/email-integration).

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
KAZMA_TRUST_LAN=0
KAZMA_CODE_EXEC_DOCKER=force
```

### Multi-replica SaaS

```bash
KAZMA_DATABASE_URL=postgresql://…
KAZMA_PRODUCTION=1
KAZMA_SECRET=…
KAZMA_VAULT_KEY=…
KAZMA_PUBLIC_URL=https://…
# optional OIDC_*
```

---

## Related

- [Configuration](../guide/configuration) — YAML keys & ConfigStore  
- [Postgres & SaaS](../ops/postgres-and-saas)  
- [Production checklist](../ops/production-checklist)  
- [Security & Safety](../guide/security-and-safety)  
