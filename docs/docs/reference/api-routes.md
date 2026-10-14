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
| * | `/api/ide/*` | Session | Via tool registry | Files, apply_patch, codebase search, LSP (`GET/POST /lsp`), run, git, swarm send (`ide_api.py`) |
| GET | `/ide` | Session | — | IDE page |

## Swarm / Command Center

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| * | `/api/swarm/*` | Session (GET) / Admin (everything else) | Workers, dispatch, tasks, metrics. One table, `kazma_ui/swarm_panel/route_roles.py`; refused with `409 swarm_single_operator` on an install with platform users |
| POST | `/api/swarm/workers/{name}/circuit-breaker/reset` | Admin | Close a worker's breaker now (the worker card's **Reset**, shown while the breaker is open or half-open) |
| GET | `/api/swarm/tasks/export` | Session | Task history as JSON, or CSV with `?format=csv` (newest 1,000; filters `type`, `status`, `q` as the history list). Task History's **Export CSV** |
| GET | `/api/swarm/tasks/{id}/stream` | Session | Live task stream (`swarm_sse`): the task's events so far, then live ones until it ends. A finished task's stream always ends with `task_completed` and the task's result, rebuilt from the stored task when the server no longer holds its events (after a restart). |
| GET | `/swarm` | Session | Swarm panel page |
| * | `/api/replay/*` | Session | Time travel: threads, snapshots, restore, fork, compare, clear (`replay_routes.py`) |
| GET | `/replay` | Session | Time Travel panel page |
| * | `/api/research/*` | Session | Research: list, detail, compare, export, archive, unarchive (`research_panel/routes.py`). A report a request names is a path inside a `research/reports` folder; nothing else is read |
| POST | `/api/research/sessions` | Session | Start deep research session (background pipeline) |
| GET | `/api/research/sessions` | Session | List durable research sessions |
| GET | `/api/research/sessions/{id}` | Session | Session status / log / report path |
| GET | `/api/research/sessions/{id}/stream` | Session | SSE progress (`snapshot` / `progress` / `done`) |
| GET | `/api/research/eval` | Session | Structural rubric for `?path=research/reports/…` or `?session_id=` |
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
| GET | `/api/memory/v2/entities` | Session | Entity list for `/memory` ops. Flags: `empty`, `isolated`, `protected`, **`core`** (always protected, never unprotectable), **`is_self`**, **`graph_id`** (self shells → `"user"`). Query: `?q=`, `?empty_only=`, `?isolated_only=`, `?limit=`. |
| POST | `/api/memory/v2/entities/{id}/protect` | Session | `{ "protected": true\|false }`: a protected entity cannot be deleted or merged away (the Memory page's **Protect** / **Unprotect**). Core entities refuse `false`. |
| POST | `/api/memory/v2/entities/{id}/rename` | Session | Display rename only (`{ "name": "…" }`). Id stable; aliases preserved. Self/person User shells also upsert hub `entities.user`. Returns `hub_synced`, `graph_id`. |
| POST | `/api/memory/v2/entities/merge` | Session | Merge source into target (beliefs rewired, aliases union). |
| POST | `/api/memory/v2/entities/link` | Session | Create belief edge (`subject`, `predicate`, `object`). |
| DELETE | `/api/memory/v2/entities/{id}` | Session | Delete entity shell (blocked for protected ids: `user`, `assistant`, …). Copies matching `entity_merges` rows to `entity_merges_archive` before dropping live ledger rows (FK). |
| GET | `/api/memory/v2/admin/summary` | Session | Counts for ops chips (live/invalidated beliefs, empty/isolated entities). |
| GET/POST | `/api/memory/v2/hygiene/*` | Session | Preview + run empty purge / near-dup invalidate / archive. |
| GET/POST | `/api/memory/v2/entity-merges*` | Session | Quarantine merge list + approve/reject. |
| GET/POST/DELETE | `/api/memory/v2/graph/groups*` | Session | View-only groupings (list/create/delete/move/tier). Never mutates beliefs. Create refuses a node that already has a parent (the answer names the move route); **Group under** on a grouped node moves it, and **Tier…** sets a grouped node's tier. Canvas poll uses `groups` on `GET /graph`; Ungroup is `DELETE …/groups/{id}`. |
| GET | `/api/memory/v2/graph/export` | Session | On-demand JSON or GraphML (`?format=json\|graphml`). |
| POST | `/api/memory/v2/probe` | Session | Recall dry-run (explain chips). |
| POST | `/api/memory/v2/federated-search` | Session | Memory + KB labeled search. |
| POST | `/api/memory/v2/eval/golden` | Session | Golden recall suite. |

Page: `GET /memory` (HTML admin). Guide: [Memory & RAG](../guide/memory-and-rag) · [Memory best path](../guide/memory-best-path).

## Settings & config

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| * | `/api/settings*`, config export | Admin/operator | ConfigStore-backed settings UI |
| PUT | `/api/settings/single`, `/api/settings` | Admin | Save one setting, or several in one transaction. Both apply the same value checks (`settings_validation`): a time zone the scheduler cannot resolve, a retention the sweep cannot read, a server-status event Kazma does not send answer 400 and nothing is written. |
| GET | `/api/settings/system/backup` | Admin | The settings backup (YAML): every stored setting, keys as vault references, no credential and none of Kazma's own state. [Configuration](../guide/configuration#settings-backup). |
| GET | `/api/settings/export?format=yaml\|json` | Admin | The same backup as YAML or JSON. |
| POST | `/api/settings/system/restore?dry_run=&sections=&expect=` | Admin | Body: a backup (YAML or JSON, at most 10 MB). `dry_run=true` returns the plan (`changed`, `keys_restored`, `keys_to_reenter`, `refused`, `kept`, `digest`) and writes nothing. Without it the plan is written; with `expect` (the preview's `digest`) a plan that changed since is refused with 409 and the plan as it is now. 400 for a file that is not a backup, 413 over 10 MB, 503 while settings are held in memory (database away). `sections=agent,providers` restores only those. |
| GET / POST | `/api/settings/system/restore/undo` | Admin | GET: what undoing the last restore would put back (`available`). POST (`expect=` the GET's `digest`): put it back; a setting changed again since and a key the restore brought back stay. 409 when there is nothing to undo. |
| GET/PUT | `/api/settings/agent/nonstop` | Session / Admin | Non-Stop & Self-Healing watchdog/failover/ledger settings |
| * | Workspace routes `/api/workspaces*` | Session | WorkspaceStore CRUD |
| GET/PUT | `/api/workspace/extra-roots` | Session | Folders outside the workspace the agent may use without asking (Workspace page). PUT replaces the list; a new root must be a full path to an existing folder, not a whole drive and not one holding Kazma's own files (400 says why). A failed read is a 500, never an empty list. |
| GET | `/api/notifications/turn-complete` | Session | The "notify when a task finishes" switch, the one reader the Web Push sender uses too |

## Documents / Document Intelligence

Mounted from `documents_api.py` at `/api/documents/*`. Thin transport over the
shared `DocumentIngestionService` (no parallel parser path). Tenant, actor and
workspace come from the request, never from the body: the tenant from the
tenant middleware, the actor from the signed-in principal's name (`operator`
for the shared secret sent as a header, `local` for a request without a
credential), the workspace from the active workspace row. Clients never supply trusted server paths.

A document belongs to the actor that ingested it; every read, job read and
action checks the document's ACL. A document or job the caller cannot read
answers exactly like one that does not exist (404). On a single-operator
install these routes, the chat commands and the agent's tools all act as the
install's operator (`documents.principal`, since 2026-10-13), so each sees the
documents the others ingested; with platform users (multi-user mode) these
routes act as each user, and chat and the agent refuse.

Guide: [Document Intelligence](../guide/document-intelligence) ·
Ops: [Document processing](../ops/document-processing) ·
Security: [Document security](../security/document-security).

### Intake, library, jobs

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/documents` | Session | Streamed upload. Body: the file's bytes; filename in `X-Document-Filename` (URL-encoded) or `?filename=`; optional `X-Idempotency-Key` and `?force_ocr=1`. The declared size, writes switched on, the storage floor, the queue, the tenant's caps and its rate window are answered **before the body is read** (see the answers below; the tenant byte quota is checked while the file is stored); at most `documents.capacity.max_concurrent_uploads` (default 4) uploads are received at once per process (503 `uploads_busy`). The body is spooled under the storage root and cut off at `documents.intake.max_bytes` or at its declared length (413). |
| POST | `/api/documents/import` | Session | Ingest a file of the active workspace: `{"path": …}` (relative to the workspace, or absolute inside it); 400 `path_outside_workspace` otherwise |
| POST | `/api/documents/generate` | Session | Generate + durably ingest: `target_format`, `payload` (an object of at most 1 MiB serialized, 413 above; nesting deeper than 32 is a 400), optional `output_name`, `title` |
| GET | `/api/documents` | Session | The documents the caller can read, each with its newest job's state |
| GET | `/api/documents/health` | Session | Parser/OCR/renderer/PDF-mutator readiness + worker status (read-only diagnostic). With `KAZMA_DOCUMENT_WORKER_ISOLATION=container` the engines are those the worker image reports |
| POST | `/api/documents/merge` | Session | Merge the PDFs of `document_ids` (at least two) into a new artifact |
| GET | `/api/documents/{document_id}` | Session | Detail (versions + jobs + artifacts) |
| GET | `/api/documents/{document_id}/versions` | Session | Version list |
| GET | `/api/documents/{document_id}/content` | Session | Normalized content, fenced as untrusted data: `?page=` or `?page_start=&page_end=`, `offset`, `max_chars` (default 20,000, at most 200,000), with a continuation. 409 `not_ready` before processing finishes |
| GET | `/api/documents/{document_id}/artifacts` | Session | Derived artifacts for a document |
| GET | `/api/documents/artifacts/{artifact_id}/download` | Session | Download an artifact by opaque ID (404 when the caller cannot read it) |
| POST | `/api/documents/{document_id}/convert` | Session | Convert the current version: `target_format`, optional `output_name` → a new artifact |
| GET | `/api/documents/{document_id}/pdf-info` | Session | Structural PDF report |
| POST | `/api/documents/{document_id}/split` | Session | Split a page range: `start_page`, `end_page`, optional `output_name` |
| POST | `/api/documents/{document_id}/fill-form` | Session | Fill AcroForm fields: `fields` (object), optional `output_name` |
| POST | `/api/documents/{document_id}/redact` | Session | Physically redact `terms` (list) → a new artifact; the original is unchanged. No confirmation dialog on the API (the page asks first) |
| POST | `/api/documents/{document_id}/index` | Session | Publish the current version to the Knowledge library `library_id` |
| POST | `/api/documents/{document_id}/unindex` | Session | Remove it from the library `library_id` |
| POST | `/api/documents/search` | Session | Search a library: `library_id`, `query`, `top_k` (default 5, 1–50); hits come back inside an untrusted-data fence |
| GET | `/api/documents/jobs/{job_id}` | Session | Job status. 404 `Job not found` for an unknown job and for one whose document the caller cannot read, identically |
| GET | `/api/documents/jobs/{job_id}/events` | Session | Append-only job event history (the same 404 rule) |
| POST | `/api/documents/jobs/{job_id}/cancel` | Session | Cooperative cancel (the same 404 rule; 409 for a job that can no longer be cancelled) |
| POST | `/api/documents/jobs/{job_id}/retry` | Session | Re-run a `dead_letter` or `rejected` job as a fresh job (409 for any other state). Needs write permission on the document and passes the same admission as an upload (429/503/507) |
| POST | `/api/documents/{document_id}/delete` | Session | Tombstone: the document leaves every Knowledge library first (a library it cannot leave stops the delete, 500 `document_unindex_failed`, so a retry can finish it), then its metadata is archived; garbage collection reclaims the bytes later. Optional body `{"reason": …}` |
| DELETE | `/api/documents/{document_id}` | Session | The same tombstone as `POST …/delete` (one implementation); the reason goes in `?reason=` |

**Answers shared by these routes.** The routes with a JSON body (import,
generate, merge, convert, split, fill-form, redact, index, unindex, search, and
the optional body of `POST /{id}/delete`) read at most 2 MiB: a declared length
past it is refused before a byte is read and an undeclared one is cut off (413
`request_too_large`); malformed JSON, a body that is not an object or nesting
too deep is a 422 `invalid_json`. A live document policy that cannot be read or
holds an invalid saved value refuses the call with 503 and `Retry-After: 30`
(`document_policy_unavailable`). **One status per error code on every route**
(`documents_api._STATUS_BY_CODE`, since 2026-10-13; the route helpers kept
three tables that disagreed): a refusal the server's state causes is a 503 with
`Retry-After: 30` wherever it happens -- durable writes switched off
(`document_platform_disabled`: reads, PDF info and cancel keep working), a
required malware scanner that cannot run (`malware_scanner_unavailable`,
answered by the upload before its body is read), no permitted worker profile
(`worker_isolation_*`). A code the table does not name keeps its route's
default: 400 for intake and lifecycle, 422 for the action routes. Refusals of
upload, import and retry carry a `code`; the 429, 503 and 507 ones also a
`reason` and a `Retry-After` header:

| Status | Code | When |
|--------|------|------|
| 413 | `intake_too_large`, `quota_exceeded` | Past `documents.intake.max_bytes` (declared or received), or past the tenant byte quota |
| 507 | `storage_full` | Free space minus the incoming file would fall below `documents.capacity.storage_free_floor_bytes` |
| 503 | `queue_full`, `capacity_unavailable`, `uploads_busy` | The durable queue is at `documents.capacity.max_queued_jobs`; a capacity measurement (storage, queue, tenant load, admission store) could not be made; too many uploads being received |
| 429 | `tenant_queue_full`, `tenant_active_full`, `rate_limited` | The tenant's queued/active caps or its files/bytes-per-minute window |
| 409 | `idempotency_conflict`, `intake_in_progress`, `intake_taken_over` | The key was used for another request or by another actor; the same key is still being received; the upload took so long its lease lapsed and another process rolled it back (nothing was saved: send it again) |

When a secret is configured, upload, import, generate, merge, convert, split,
fill-form, redact, index and search also count against the per-principal HTTP
rate limit bucket `documents` (429 with `Retry-After`): 10 requests a minute,
search allowed up to 30 in the same window; a saved
`api.rate_limit.documents_per_minute` replaces both.

### Operations (`/api/documents/ops/*`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/documents/ops/metrics` | Session | Content-free metrics snapshot |
| GET | `/api/documents/ops/capacity` | Session | Backpressure snapshot + `degraded_reasons` |
| GET | `/api/documents/ops/readiness` | Session | Read-only: storage backends and multi-replica honesty (`jobs_multi_replica`, `metadata_multi_replica`; a SQLite backend chosen on purpose is not degraded), `policy` (configured vs effective, `restart_required`), `malware`, `worker_isolation` (`hostile_document_profile`, `image_code_matches`), `stranded_intake_reservations`, `rollout`, `accepting_durable_writes`. `status` is `degraded` when a configured backend fell back or `degraded_reasons` names a problem (worker isolation unavailable, stranded intake reservations, an outdated worker image, malware scanning blocking intake), `disabled` while durable writes are off |
| GET | `/api/documents/ops/retention` | Session | Live retention/GC policy view |
| GET | `/api/documents/ops/audit` | Admin | Keyset-paged operational audit of the tenant (`?document_id=&event_type=&limit=&before_id=`). Admin-only: the trail names documents and actors the caller may not be able to read |
| POST | `/api/documents/ops/maintenance/dry-run` | Admin | GC dry-run report |
| POST | `/api/documents/ops/maintenance/run` | Admin | GC run (the page always dry-runs and confirms first; the route does not require it) |

HTML page: `GET /documents` (session). The TUI Documents tab reads
`GET /api/documents` and `GET /api/documents/{document_id}/content` from the
running server.

## Email (`email_api.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/email/status` | Session | Active provider, auth modes, presets summary |
| GET | `/api/email/accounts` | Session | Every mail account: `source` `main` / `settings` / `env`, name, type, address, `auth` (`oauth` / `password` / `incomplete`), `calendar`, `removable` |
| POST | `/api/email/accounts` | Session | Add another account that signs in with a password (`alias`, `type`, `address`, `password`, hosts); the login is tried first and a failing one is refused (400) |
| POST | `/api/email/accounts/{alias}/remove` | Session | Forget an account added in Settings (its tokens and password) |
| GET | `/api/email/presets` | Session | Gmail/Microsoft IMAP/POP host presets |
| POST | `/api/email/protocol/connect` | Session | Save IMAP/POP for gmail\|microsoft\|generic |
| POST | `/api/email/protocol/disconnect` | Session | Clear protocol + OAuth tokens for provider |
| POST | `/api/email/gmail/connect` | Session | Gmail app-password → IMAP |
| POST | `/api/email/gmail/disconnect` | Session | Clear Gmail creds |
| POST | `/api/email/oauth/gmail/client` | Session | Save Google OAuth client id/secret |
| GET | `/api/email/oauth/gmail/start` · `start.json` | Session | Browser OAuth redirect / JSON; `?account=<name>` signs in another account under that name |
| GET | `/api/email/oauth/gmail/callback` | Open (OAuth) | Token exchange; redirects to Settings |
| POST | `/api/email/oauth/microsoft/client` | Session | Save Azure app id/secret |
| GET | `/api/email/oauth/microsoft/start` · `start.json` | Session | Browser OAuth; `?account=<name>` for another account |
| GET | `/api/email/oauth/microsoft/callback` | Open (OAuth) | Token exchange |
| POST | `/api/email/oauth/microsoft/device/start` · `…/poll` | Session | Device-code fallback; body `{"account": "<name>"}` for another account |
| POST | `/api/email/oauth/microsoft/disconnect` | Session | Clear Microsoft mail: tokens, the sign-in's address and scopes (Outlook Calendar keeps its own switch) |
| GET | `/api/calendar/status` | Session | Each calendar's state and address, and the active provider |
| GET | `/api/calendar/oauth/google/start` · `start.json` | Session | Google Calendar sign-in (callback: the Gmail one) |
| GET | `/api/calendar/oauth/microsoft/start.json` | Session | Outlook Calendar sign-in for the calendar only (callback: the Microsoft one, `purpose=calendar`); mail is left as it is |
| POST | `/api/calendar/oauth/microsoft/device/start` | Session | The same by device code (poll with `/api/email/oauth/microsoft/device/poll`) |
| POST | `/api/calendar/oauth/google/disconnect` · `…/microsoft/disconnect` | Session | Turn that calendar off until it is connected from the card again; mail stays connected |

Agent mail ops use tools (`email_list`, …), not these HTTP routes. Guide: [Email integration](../guide/email-integration).

## X publisher

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/x` | Session | **X Studio** page (composer + X-only planner). |
| GET | `/api/x/status` | Session | Configured?, handle, caps. Never returns secrets. |
| POST | `/api/x/preview` | Session | Dry-run ToU policy (`evaluate_post`). No network, no ledger. |
| GET | `/api/x/drafts` | Session | Flattened `save_proposal` items for the Studio inbox. |
| GET | `/api/x/audit` | Session | Recent `x_audit.db` rows (`?action=&limit=`). |
| POST | `/api/x/post` | Session + CSRF | Immediate post (`publish_x_post`). Operator click is the approval. Optional `reply_to_id`, `proposal_id` (stored text wins). |
| POST | `/api/x/delete` | Session + CSRF | Delete a live tweet (`delete_x_post`). Operator click is the approval. |
| POST | `/api/x/credentials` | Session + CSRF | Save four OAuth 1.0a keys (vaulted) + handle + caps. |
| POST | `/api/x/test` | Session + CSRF | `GET /2/users/me` with stored keys. |
| POST | `/api/x/disconnect` | Session + CSRF | Delete keys, disable posting. |
| GET | `/api/x/reply` | Session | Auto-reply config + live poller status. |
| POST | `/api/x/reply` | Session + CSRF | Save auto-reply settings; starts/stops the mentions poller. |
| POST | `/api/x/reply/preview` | Session + CSRF | Dry-run a draft. Nothing published or recorded. |
| GET | `/api/x/reply/conversations` | Session | Summon log (parent + mention + draft/reason). |
| POST | `/api/x/reply/poll` | Session + CSRF | Fetch mentions from X now (ignores stuck cursor). |
| POST | `/api/x/reply/approve` | Session + CSRF | Publish a held (or wire-failed) draft. |
| POST | `/api/x/reply/deny` | Session + CSRF | Park a held draft. |
| POST | `/api/x/reply/retry` | Session + CSRF | Re-run a skipped/failed/drafting summon. |
| POST | `/api/x/reply/delete` | Session + CSRF | Delete the posted reply on X (if any) and drop the log row. |

Chat tweets still go through `x_post` (always HITL + `proposal_id`). Guide: [X publisher](../guide/x-publisher). Mentions: [X auto-reply](../guide/x-auto-reply).

## Scheduled (cron + X)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/scheduled` | Session | Mixed clock page (cron jobs **and** X posts). |
| GET | `/api/scheduled/tasks` | Session | Every cron job (upcoming and finished, each with its status class and the `actions` the server allows) and the X publication store's operations. Prompts are display summaries; the address is a `destination` label. |
| GET | `/api/scheduled/cron/{job_id}` | Session | One job with its full prompt and `version`, for the edit dialog. |
| POST | `/api/scheduled/cron` | Session + CSRF | Book a job: `timing`, `prompt`, optional `kind` (`reminder`/`task`) and `capabilities` (`read`/`read_write`). Caps, length limits and the `scheduled` rate bucket apply. |
| PUT | `/api/scheduled/cron/{job_id}` | Session + CSRF | Edit `timing` and/or `prompt` with the `expected_version` the form read: 200 `rescheduled`, 202 `edit_pending` (during a fire), 409 `conflict` / `not_editable`. |
| DELETE | `/api/scheduled/cron/{job_id}` | Session + CSRF | Cancel: 200 `cancelled`, 202 `cancel_pending` (during a fire), 409 for a finished job. |
| POST | `/api/scheduled/cron/{job_id}/restore` | Session + CSRF | **Run again**: book a finished job anew at `timing`. |
| POST | `/api/scheduled/x` | Session + CSRF | Book an X post (`book_x_post`). Optional `reply_to_id`, `proposal_id`. Answers the stored time and state. |

X posts on this page are rescheduled and cancelled through `POST /api/x/queue/{operation_id}/reschedule` and `.../cancel` (with the revision the page read), the routes X Studio uses. Every mutating route checks the same origin (`kazma_ui/same_origin.py`). On an install with platform users every route answers **409** `scheduled_single_operator`. Behaviour: [Scheduled reminders and tasks](../guide/native-skills#scheduled-reminders-and-tasks).

X Studio's **All clocks** link is this page. The Studio planner itself is X-only.

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
| Static | `/`, `/chat`, `/settings`, `/x`, `/scheduled`, … | Cookie/session | HTML pages |

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
