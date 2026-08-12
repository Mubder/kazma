---
id: web-ui
title: Web UI
sidebar_label: Web UI
description: FastAPI + Alpine web dashboard — chat, settings, swarm, IDE, login
---

# Web UI

The primary operator surface is **`kazma-ui`**: FastAPI + Jinja templates + Alpine.js + SSE streaming.

## Start

```bash
kazma serve          # default 127.0.0.1:9090
# or
python serve.py
```

Open `http://127.0.0.1:9090` (or your `KAZMA_HOST`/`KAZMA_PORT`).

## Main pages

| Page | Path | Purpose |
|------|------|---------|
| Chat | `/` or chat route | SSE streaming agent chat (`sse_chat.py`, `streaming.js`) |
| Dashboard | `/dashboard` | Observability + **Memory & Governance** (health board, L2 property graph explorer, backups) |
| Settings | `/settings` | Models, providers, safety, account, **Email** (`?tab=email`), **Documents** (`?tab=documents`), **Proxy Provider** (System tab) |
| Swarm / Command Center | `/swarm` | Workers, live tasks, dispatch UI |
| Time Travel | `/replay` | Snapshot timeline browser, restore (rewind), fork (branch), compare diff, live SSE snapshot events |
| Research | `/research` | **Start deep research** (live SSE sessions), results list, archive, compare, export |
| Documents | `/documents` | Document Intelligence: upload, library, content preview, convert/redact, **ops panel** (capacity, readiness, audit, GC dry-run + confirm) |
| IDE | `/ide` | Workspace files, run, git, AI-assisted edit |
| Login | `/login` | Secret / local user / OIDC |

### Documents page (product path)

1. Drop or pick a file (optional **Force OCR**).
2. Library lists opaque ids + processing state (`received` … `ready` / fail states).
3. Detail: fenced content preview (`dir="auto"` for BiDi), versions, jobs, convert/PDF tools when engines are ready.
4. Ops card: capacity `degraded_reasons`, readiness (jobs/metadata multi-replica), audit, GC dry-run → `kazmaConfirm` → run.
5. Settings → **Documents** for live limits and malware/rollout without editing YAML.

REST: `/api/documents/*` · Guide: [Document Intelligence](../guide/document-intelligence) · Ops: [Document processing](../ops/document-processing).

## Dashboard metrics

Cards are fed by the in-memory **`TraceStore`** (LLM/tool traces) plus the **cost circuit breaker** budget state:

| Card | Source | Notes |
|------|--------|--------|
| Total Cost | TraceStore total + cost breaker | API returns **numbers**; UI formats `$…`. Resets on process restart. |
| Total Tokens | TraceStore | Same process lifetime as traces. |
| Tool Calls | TraceStore (`trace_type=tool`) | Supervisor tool worker traces. |
| Circuit Breaker | **Cost** breaker (OK / WARNING / HALTED) | Not swarm worker breakers. |
| Uptime | TraceStore start time | Process lifetime of the store singleton. |

`GET /api/dashboard/status` and WebSocket `/ws/dashboard` push numeric metrics (legacy string forms like `"$0.00"` / `"1,234"` are parsed safely in `dashboard.js`). Chat footer token/cost on a turn still comes from the SSE `done` event for that session.

### Memory & Governance (Dashboard)

- **Component health** from `build_memory_health()` (V2 stack, embedder, packages, optional Neo4j/KB inject).
- **V2 topology** on the Dashboard canvas (belief graph; SQLite paint by default).
- Backups/maintenance for memory stores.

### Memory admin (`/memory`)

Dedicated ops page (not the chat sidebar):

| Area | What you can do |
|------|-----------------|
| Graph & health (top) | Belief canvas, KPIs, probe, refresh/export; truncation banner when capped |
| Entities | Rename display labels, merge/link shells, delete empty, **click row → focus graph**. Pagination + diacritic-insensitive/alias-aware search |
| Beliefs | Search (FTS5), **Edit** triple (single modal), invalidate batch with **[Undo]**; click row → focus edge endpoints + see "recalled N×" history |
| Pending merges | Approve/reject quarantine; merge shows a rewired-belief-count receipt |
| Hygiene | Purge empty, dedupe noted, archive dead (preview counts before run) |
| Hub identity | Person **User** / `ent_…` rename to **Mubder** updates canvas hub (not a second “You” node) |

Multi-tenant isolation: set `KAZMA_MEMORY_ENFORCE_TENANT=1` to scope memory by the request tenant (off by default). APIs under `/api/memory/v2/*`. Full guide: [Memory & RAG](../guide/memory-and-rag) · [Memory best path](../guide/memory-best-path). Remaining work: [`MEMORY_REMAINING.md`](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md).

Research / scrape runs through **chat tools** and the **Research panel** start form + live SSE sessions (see [Web research](../guide/web-research)). Tour of the latest stack: [Recent features](../guide/recent-features).

### Memory context (chat workbench)

With **Settings → Memory → Explain recall** on, each turn’s workbench can show a
**Memory context** panel (beliefs / episodes / KB hits with channel chips:
`fts5`, `dense`, `belief_ppr`, `kb_rrf`, …). Dashboard probe uses the same
explain tags; **Run golden eval** exercises the golden recall set.

## Email (Settings → Email)

Connect real mailboxes for the agent without env-only setup:

| Card | Modes |
|------|--------|
| Sandbox | Always on (demo SQLite mailbox) |
| Gmail / Workspace | **OAuth** (Gmail API) · **IMAP** · **POP** |
| Microsoft 365 | **OAuth** (Graph browser or device code) · **IMAP** · **POP** |

API: `/api/email/status`, OAuth start/callback, `POST /api/email/protocol/connect`. Full guide: [Email integration](../guide/email-integration).

## Proxy Provider (Settings → System)

Opt-in scraping-resilience addon. When enabled, scrape/crawl paths route through
a residential rotating proxy (httpx ladder, Playwright, KB discover, Bing/Wiki
SERP). Local SearXNG, Jina/Firecrawl APIs, and LLM traffic stay direct.

- **Provider:** `None` (direct) or **anyIP.io** (residential/mobile rotation).
- Fields: host/port/username/password (password vault-encrypted), network type,
  optional country, sticky-session toggle.
- **Test Connection** fetches `api.ipify.org` through the proxy and shows the exit IP.
- Live config — a Settings change takes effect without a restart.

API: `GET/PUT /api/settings/proxy`, `POST /api/settings/proxy/test`.  
Coverage matrix: [Recent features → Proxy](../guide/recent-features#2-shared-web-acquisition--proxy-provider) · [Web research](../guide/web-research#bulletproof-scraping-proxy-provider-addon-ipua-rotation).

## Non-Stop Execution & Self-Healing (Settings → Agent)

Configurable self-healing supervisor watchdog and model failover controls for long-running autonomous workflows:

- **Master Toggle:** Enable/disable supervisor stall watchdog and automated checkpoint recovery.
- **Stall & Timeout Controls:** Configure stall threshold (default 60s) and per-tool execution timeout (`agent.tool_timeout_seconds`, default 120s).
- **Recovery Budget:** Set maximum recovery attempts (default 3) and exponential backoff parameters (`backoff_base_seconds`, `backoff_max_seconds`).
- **Model Failover Chain:** Toggle failover and specify an ordered list of fallback models with per-model cooldown periods (default 300s).
- **Call Ledger:** Toggle durable SQLite logging (`kazma-data/llm_calls.db`) for all LLM calls.
- Full EN/AR i18n support and live re-read (`get_nonstop_config()`) — settings apply immediately without server restart.

API: `GET/PUT /api/settings/agent/nonstop`.
Coverage matrix: [Recent features → Non-Stop Execution](../guide/recent-features#4d-non-stop-execution--self-healing-engine-2026-08).

## Auth

- Single-operator: `KAZMA_SECRET` (opaque sessions preferred over legacy raw cookie).  
- Multi-user: platform users + RBAC + optional OIDC — see [Multi-user SaaS](multi-user-saas).  
- API routes default-deny when secret is configured.

## UI conventions

- **Dialogs**: `window.confirm`, `window.alert`, and `window.prompt` are
  **globally overridden** — every call (current and future) routes through
  the styled Kazma modal (Alpine-based). Developers can still write
  `if (!await confirm('Delete?'))` and get the branded dialog. The
  `kazmaConfirm` / `kazmaAlert` / `kazmaPrompt` helpers remain available
  for opts-based calls (title, danger, confirmText, etc.).
- **Chat Stop button**: the send button transforms into a red pulsing Stop
  button during generation. Click it or press **Escape** to abort the SSE
  stream. The input field stays enabled so the user can type their next
  message while the agent works.
- **Toasts**: `window.showToast` / Alpine `$store.toast`.
- **Research archive**: each research card has an archive button. Archived
  items move to the "Archived" tab (with restore + delete). Uses the JSON
  `metadata.archived` flag — no schema migration.
- Soft-nav SPA may be feature-flagged off — full page loads are the reliable path.

## Theming & design tokens

The Web UI runs on the **"ABYSS" design system** — a single token table per
theme, defined in `static/css/kazma.css` (§1 dark, §23 light) with shell polish
in `kazma.v5.css`. The same tokens are mirrored onto the docs site
(`docs/src/css/custom.css`).

| Aspect | Details |
|---|---|
| **Dark palette** | Deep blue-black slate — bg `#0e1626`, panel `#16223a`, text `#eef3fb`. Layered depth (panel → surface → elevated). |
| **Light palette** | Ice-blue paper — bg `#f0f4fa`, panel `#ffffff`, ink `#0c1526`. Muted text kept dark for P0 contrast. |
| **Accents** | Royal blue `#3b82f6` (dark) / `#2563eb` (light) + sky `#38bdf8`. Brand gradient `azure → royal → sky`. |
| **Single token table** | One source of truth per theme — the old v4 cyan palette and the "Abyss" set were **folded into one table** so they no longer fight (design-token unification, P1). |
| **Server-authoritative theme** | Theme is resolved **server-side**, not just client-side, so your choice **persists across devices and browsers** (a switch on one machine holds on another). |
| **Real dark canvas** | `<html>` carries `color-scheme: only <theme>` and is painted with the theme background, so iOS Safari can no longer paint its dark system canvas over Kazma Light. |
| **Mobile chrome** | Opaque header + dock (no see-through), keyboard-aware composer, retired hamburger, and the 769–1280px icon-rail dead strip is gone. |
| **Forced HTML revalidation** | Templates send cache-busting headers so a stale cached page can't survive a deploy. |
| **No `x-show` blink** | `x-cloak` is global; any `x-show` panel also carries `x-cloak`, and inline `display:flex` never sits on an `x-show` element (flex lives in a class). |

Tune the system by editing the token tables in `kazma.css`; the docs site follows
the same values.

## Related

- [IDE](ide) · [Command Center](command-center-swarm) · [API routes](../reference/api-routes)  
- [Deployment](../guide/deployment) · [Troubleshooting](../guide/troubleshooting-and-workarounds)  
