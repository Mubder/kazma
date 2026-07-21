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

## OIDC / multi-user

| Variable | Default | Prod required? | Purpose |
|----------|---------|----------------|---------|
| `KAZMA_OIDC_ISSUER` | unset | If SSO | OIDC issuer URL. |
| `KAZMA_OIDC_CLIENT_ID` | unset | If SSO | Client id. |
| `KAZMA_OIDC_CLIENT_SECRET` | unset | If SSO | Client secret. |

See [OIDC IdP Setup](../ops/oidc-setup) and [Multi-user SaaS](../products/multi-user-saas).

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
