---
id: api-routes
title: API & Route Matrix
sidebar_label: API Routes
description: Primary HTTP/SSE/WebSocket routes exposed by kazma-ui and gateway control
---

> Primary surfaces of the FastAPI app (`kazma_ui`). Auth is **default-deny** for `/api/*` unless listed open. HITL danger applies to tool execution, not every HTTP route.

## Always open (health)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | Open | Liveness |
| GET | `/health/live` | Open | Process live |
| GET | `/health/ready` | Open | Readiness (DB ping when configured) |

## Auth & account

| Method | Path | Auth scope | Description |
|--------|------|------------|-------------|
| GET/POST | `/login` | Public page | Multi-mode login (user / secret / OIDC) |
| POST | `/api/auth/*` | Varies | Login, logout, session (see `auth.py`, `saas_api.py`) |
| GET | `/api/saas/*` | Admin / operator | Tenants, platform users (RBAC) |

## Chat & sessions

| Method | Path | Auth | HITL | Description |
|--------|------|------|------|-------------|
| POST | `/api/chat/stream` | Session/secret | Graph interrupt on danger tools | SSE chat stream |
| POST | `/api/approve/{thread_id}` | Session | Resumes graph HITL | Approve danger tool |
| * | Session CRUD under `/api/sessions*` | Session | — | Thread list / history (`session_manager`) |

## IDE

| Method | Path | Auth | HITL | Description |
|--------|------|------|------|-------------|
| * | `/api/ide/*` | Session | Via tool registry | Files, run, git, swarm send (`ide_api.py`) |
| GET | `/ide` | Session | — | IDE page |

## Swarm / Command Center

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| * | `/api/swarm/*` | Session / RBAC | Workers, dispatch, tasks, metrics |
| GET | SSE swarm events | Session | Live task stream (`swarm_sse`) |
| GET | `/swarm` | Session | Swarm panel page |
| * | `/api/replay/*` | Session | Time travel: threads, snapshots, restore, fork, compare, clear (`replay_routes.py`) |
| GET | `/replay` | Session | Time Travel panel page |
| * | `/api/research/*` | Session | Research: list, detail, compare, export, archive, unarchive (`research_panel/routes.py`) |
| POST | `/api/research/sessions` | Session | Start deep research session (background pipeline) |
| GET | `/api/research/sessions` | Session | List durable research sessions |
| GET | `/api/research/sessions/{id}` | Session | Session status / log / report path |
| GET | `/api/research/sessions/{id}/stream` | Session | SSE progress (`snapshot` / `progress` / `done`) |
| GET | `/api/research/eval` | Session | Structural rubric for `?path=` or `?session_id=` |
| POST | `/api/memory/v2/eval/golden` | Session | Run golden memory recall cases (pass rate) |
| GET | `/research` | Session | Research panel page (start form + live progress) |

## Memory / RAG

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/system/status` | Session | Memory health board + embedder/layer probes (`build_memory_health`) |
| GET | `/api/memory/graph` | Session | Property graph JSON (`nodes`/`edges`); optional `?q=` filter |
| GET | `/api/memory/graph/stats` | Session | Node/edge counts + backend path |
| GET | `/api/memory/graph/search` | Session | FTS search over graph nodes (`?q=&limit=`) |
| POST | `/api/memory/graph/clear` | Session | Bi-temporal invalidate of **active V2 beliefs for one tenant** (`?tenant=` defaults to `default`; no all-tenants mode). Tombstones the PG mirror, deletes Neo4j edges, writes a `graph_clear` audit row. UI confirms. |
| * | `/api/system/memory/*` | Session | Backup / restore / maintenance of memory stores |

### V2 cognitive engine (`/api/memory/v2/*`)

Active when `memory.v2.use_new_stack` is true. All routes return shaped JSON
on error (never a bare 500); non-numeric params yield a FastAPI 422.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/memory/v2/health` | Session | V2 health snapshot — active/superseded/archived belief counts, episode/entity/procedural stats, queue depth. Drives the dashboard KPI grid (`pollV2Health`, 5s cadence). |
| GET | `/api/memory/v2/beliefs` | Session | Active beliefs list. `?q=` FTS filter, `?limit=` (default 50, clamped 1–200). |
| GET | `/api/memory/v2/beliefs/{id}` | Session | Belief detail + supersede chain. |
| POST | `/api/memory/v2/beliefs/{id}/invalidate` | Session | Soft-invalidate one belief (+ best-effort Neo4j edge delete). |
| POST | `/api/memory/v2/beliefs/invalidate-batch` | Session | Soft-invalidate many (`{ "ids": [...] }`). |
| PATCH | `/api/memory/v2/beliefs/{id}` | Session | Operator edit of active triple: optional `subject`, `predicate`, `object`, `predicate_type`. Sets `extraction_method=user_explicit`; clears embedding if object changes. |
| GET | `/api/memory/v2/graph` | Session | Belief graph `{nodes, links, stats, groups}` for the canvas. Bi-temporal + filter params: `?at=<unix_ts>` (point-in-time scrub; superseded beliefs marked `superseded=true`), `?type=` (`functional`/`set`/`state` predicate_type), `?entity_type=` (person/tool/concept/…), `?limit=` (default 200), `?source=neo4j` (optional probe). `stats.total_links` vs `stats.links` is the slicing delta shown on the truncation banner. **Invariants:** unique node ids; no virtual fact node when object text equals an entity id; no dangling links; hub node `id=user` with display `name` from `entities.user` (self person shells collapsed onto hub); payload-object subjects carry a hub `related_to` anchor. |
| GET | `/api/memory/v2/entities` | Session | Entity list for `/memory` ops. Flags: `empty`, `isolated`, `protected`, **`is_self`**, **`graph_id`** (self shells → `"user"`). Query: `?q=`, `?empty_only=`, `?isolated_only=`, `?limit=`. |
| POST | `/api/memory/v2/entities/{id}/rename` | Session | Display rename only (`{ "name": "…" }`). Id stable; aliases preserved. Self/person User shells also upsert hub `entities.user`. Returns `hub_synced`, `graph_id`. |
| POST | `/api/memory/v2/entities/merge` | Session | Merge source into target (beliefs rewired, aliases union). |
| POST | `/api/memory/v2/entities/link` | Session | Create belief edge (`subject`, `predicate`, `object`). |
| DELETE | `/api/memory/v2/entities/{id}` | Session | Delete entity shell (blocked for protected ids: `user`, `assistant`, …). Copies matching `entity_merges` rows to `entity_merges_archive` before dropping live ledger rows (FK). |
| GET | `/api/memory/v2/admin/summary` | Session | Counts for ops chips (live/invalidated beliefs, empty/isolated entities). |
| GET/POST | `/api/memory/v2/hygiene/*` | Session | Preview + run empty purge / near-dup invalidate / archive. |
| GET/POST | `/api/memory/v2/entity-merges*` | Session | Quarantine merge list + approve/reject. |
| GET/POST/DELETE | `/api/memory/v2/graph/groups*` | Session | View-only groupings (list/create/delete/move/tier). Never mutates beliefs. Canvas poll uses `groups` on `GET /graph`; Ungroup is `DELETE …/groups/{id}`. |
| GET | `/api/memory/v2/graph/export` | Session | On-demand JSON or GraphML (`?format=json\|graphml`). |
| POST | `/api/memory/v2/probe` | Session | Recall dry-run (explain chips). |
| POST | `/api/memory/v2/federated-search` | Session | Memory + KB labeled search. |
| POST | `/api/memory/v2/eval/golden` | Session | Golden recall suite. |

Page: `GET /memory` (HTML admin). Guide: [Memory & RAG](../guide/memory-and-rag) · [Memory best path](../guide/memory-best-path).

## Settings & config

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| * | `/api/settings*`, config export | Admin/operator | ConfigStore-backed settings UI |
| GET/PUT | `/api/settings/agent/nonstop` | Session / Admin | Non-Stop & Self-Healing watchdog/failover/ledger settings |
| * | Workspace routes `/api/workspaces*` | Session | WorkspaceStore CRUD |

## Documents / Document Intelligence

Mounted from `documents_api.py` at `/api/documents/*`. Thin transport over the
shared `DocumentIngestionService` (no parallel parser path). Tenant/actor come
from request context — clients never supply trusted paths for remote intake.

Guide: [Document Intelligence](../guide/document-intelligence) ·
Ops: [Document processing](../ops/document-processing) ·
Security: [Document security](../security/document-security).

### Intake, library, jobs

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/documents` | Session | Streamed upload intake (bounded; 413 on overflow) |
| POST | `/api/documents/import` | Session | Workspace-safe local file intake |
| POST | `/api/documents/generate` | Session | Generate + durably ingest (payload ≤ 1 MiB) |
| GET | `/api/documents` | Session | List documents (tenant/actor scoped) |
| GET | `/api/documents/health` | Session | Parser/OCR/renderer readiness + worker status |
| POST | `/api/documents/merge` | Session | Merge several documents' PDFs by opaque IDs |
| GET | `/api/documents/{document_id}` | Session | Detail (versions + jobs + artifacts) |
| GET | `/api/documents/{document_id}/versions` | Session | Version list |
| GET | `/api/documents/{document_id}/content` | Session | Paged normalized/fenced content |
| GET | `/api/documents/{document_id}/artifacts` | Session | Derived artifacts for a document |
| GET | `/api/documents/artifacts/{artifact_id}/download` | Session | Download artifact by opaque ID |
| POST | `/api/documents/{document_id}/convert` | Session | Convert current version to target format |
| GET | `/api/documents/{document_id}/pdf-info` | Session | Structural PDF report |
| POST | `/api/documents/{document_id}/split` | Session | Split page range from PDF version |
| POST | `/api/documents/{document_id}/fill-form` | Session | Fill AcroForm fields |
| POST | `/api/documents/{document_id}/redact` | Session | Physical redact terms → new artifact (no UI confirm on API) |
| POST | `/api/documents/{document_id}/index` | Session | Publish current version to a Knowledge library |
| POST | `/api/documents/{document_id}/unindex` | Session | Remove from a library |
| POST | `/api/documents/search` | Session | Library search (fenced hits) |
| GET | `/api/documents/jobs/{job_id}` | Session | Job status |
| GET | `/api/documents/jobs/{job_id}/events` | Session | Append-only job event history |
| POST | `/api/documents/jobs/{job_id}/cancel` | Session | Cooperative cancel |
| POST | `/api/documents/jobs/{job_id}/retry` | Session | Re-enqueue dead-letter/rejected job |
| POST | `/api/documents/{document_id}/delete` | Session | Tombstone / delete document |

### Operations (`/api/documents/ops/*`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/documents/ops/metrics` | Session | Content-free metrics snapshot |
| GET | `/api/documents/ops/capacity` | Session | Backpressure snapshot + `degraded_reasons` |
| GET | `/api/documents/ops/readiness` | Session | Multi-replica honesty (`metadata_single_replica`, jobs backend) |
| GET | `/api/documents/ops/retention` | Session | Live retention/GC policy view |
| GET | `/api/documents/ops/audit` | Session | Keyset-paged operational audit (`?limit=&before_id=`) |
| POST | `/api/documents/ops/maintenance/dry-run` | Admin | GC dry-run report |
| POST | `/api/documents/ops/maintenance/run` | Admin | GC run after dry-run/confirm in UI |

HTML page: `GET /documents` (session). TUI Documents tab uses the same coordinator.

## Email (`email_api.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/email/status` | Session | Active provider, auth modes, presets summary |
| GET | `/api/email/accounts` | Session | Multi-account aliases (env) |
| GET | `/api/email/presets` | Session | Gmail/Microsoft IMAP/POP host presets |
| POST | `/api/email/protocol/connect` | Session | Save IMAP/POP for gmail\|microsoft\|generic |
| POST | `/api/email/protocol/disconnect` | Session | Clear protocol + OAuth tokens for provider |
| POST | `/api/email/gmail/connect` | Session | Gmail app-password → IMAP |
| POST | `/api/email/gmail/disconnect` | Session | Clear Gmail creds |
| POST | `/api/email/oauth/gmail/client` | Session | Save Google OAuth client id/secret |
| GET | `/api/email/oauth/gmail/start` · `start.json` | Session | Browser OAuth redirect / JSON |
| GET | `/api/email/oauth/gmail/callback` | Open (OAuth) | Token exchange; redirects to Settings |
| POST | `/api/email/oauth/microsoft/client` | Session | Save Azure app id/secret |
| GET | `/api/email/oauth/microsoft/start` · `start.json` | Session | Browser OAuth |
| GET | `/api/email/oauth/microsoft/callback` | Open (OAuth) | Token exchange |
| POST | `/api/email/oauth/microsoft/device/start` · `…/poll` | Session | Device-code fallback |
| POST | `/api/email/oauth/microsoft/disconnect` | Session | Clear Microsoft tokens |

Agent mail ops use tools (`email_list`, …), not these HTTP routes. Guide: [Email integration](../guide/email-integration).

## Gateways & platforms

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| Webhooks | Telegram/Discord/Slack callbacks | Adapter secrets | Inbound messages + HITL buttons |
| * | `/api/gateway/*` | Session | Gateway status/control from CLI |

## Other

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| WS | Voice routes | Session | `routes_voice_ws.py` |
| * | Chaos routes | `KAZMA_CHAOS_ENABLED` | **Dev only** |
| Static | `/`, `/chat`, `/settings`, … | Cookie/session | HTML pages |

Exact route lists evolve with routers mounted in `app.py`. For extension points see [API & Extension Points](../guide/api-and-extension-points).

## Auth model summary

| Mode | When |
|------|------|
| Shared secret cookie / header | Single-operator |
| Opaque web session | Multi-user default |
| Platform RBAC (viewer/operator/admin) | SaaS APIs |
| OIDC PKCE | SSO login |
| API token / JWT | Programmatic (where enabled) |

See [Multi-user SaaS](../products/multi-user-saas) and [Environment variables](environment-variables).
