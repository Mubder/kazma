<!-- AGENTS.md, piece 19a-documents-store. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 19. Document Intelligence (`kazma-core/kazma_core/documents/`)

Durable document platform (phases 0–10), hardened 2026-10-13 against the
Documents audit (tracker `docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md`,
F01–F10 / Q01–Q05; an independent review and the 48–72 h soak are still
open, and the default `process` worker profile is not qualified for hostile
documents, E). Docs SoT:
`docs/docs/guide/document-intelligence.md`, phases
`docs/docs/guide/document-phases.md`, security
`docs/docs/security/document-security.md`, ops
`docs/docs/ops/document-processing.md`.

**A. Two boundaries — do not invent a third path.**
- **Durable public boundary:** `DocumentIngestionService` — Web
  `/api/documents/*` (`documents_api.py`), native `document-platform` tools,
  gateway `/documents`/`/docs`, TUI `DocumentsPanel` (through the Web API).
  Tenant/actor ACL, capacity, audit, jobs, index.
- **Execution boundary:** `DocumentService` — sniff/parse/OCR/render inside
  an isolated worker (a bounded subprocess, or a per-job container: E).
  Durable workers call it; chat
  `agent_handler/attachments.py` may call it for **transient** fenced excerpts
  only (not a second durable store).
- Gateway/UI **must not** import `documents.parsers`, `documents.ocr`,
  `documents.renderers`, `parser_worker`, `mutation_worker`, etc.

**B. Job state machine (canonical).**
`received → quarantined → validating → ready_to_parse|ocr_required →
parsing|ocr_running → normalizing → indexing → verifying → ready`
(+ `retry_wait`, `rejected`, `cancelled`, `dead_letter`). Do not reintroduce
generic PENDING/ACCEPTED/PROCESSING labels in product code or docs.

**B2. Intake admission is durable; a partial publication is undone only by
its owner or after its owner is PROVEN dead.**
`documents/admission.py` reserves pending queue and tenant rate/byte budgets in
the job backend before durable ingestion reads bytes. SQLite uses a short
`BEGIN IMMEDIATE`; Postgres locks the one `document_intake_mutex` row
(`SELECT ... FOR UPDATE`). No DB lock is held
during streaming. Declared lengths are enforced before CAS publication; unknown
lengths reserve the intake maximum and reconcile actual bytes. Replay is checked
before fresh admission. Retries reserve a slot and require write ACL on a live
document. A published job closes its reservation. Since 2026-10-13 every
reservation records its OWNER (install, host, pid, process start time) and,
before any metadata write, the ids it publishes under; the rows it creates
carry its `intake_token`. A failed intake rolls its own rows back
(`_abandon_own_intake`). At start and every minute while the workers run
(`_sweep_stranded_intakes`), `_recover_stranded_intakes` claims (CAS) and
rolls back intakes whose owner is provably dead (same install and host, pid
gone or reused) or restored from a backup. An owner that cannot be proven dead
(another host, an unreadable process) holds a LEASE: it renews
`heartbeat_epoch` every quarter of its own `lease_seconds`
(`documents.intake.lease_seconds`, default 300, stored on the row) while the
intake runs, and is taken over only once that has lapsed. The takeover FENCES
it: `jobs.enqueue(intake_fence=)` creates the publishing job only in a
transaction that finds the reservation still active and still the caller's,
under the admission mutex the claim also takes, and a claim refuses a
reservation whose job exists. A fenced owner removes the rows tagged with its
token (`_discard_tagged`) and answers `intake_taken_over` (409); the sweep
removes what a fenced owner wrote and left, for a day after the takeover
(`fenced_epoch`). A legacy row (no owner or no heartbeat: a build without the
fence) is never taken over and is reported (`stranded`, readiness
`document_intake_reservations_stranded`; a remote intake renewing its lease is
not stranded). Never add age-only expiry without the fence. The Web route
answers every budget BEFORE reading the body (`precheck_upload`; F08 below).
Gates: `tests/test_document_admission_hardening.py`,
`tests/test_document_crash_recovery.py` (a real child killed after each of 7
intake stages and inside 4 job stages, SQLite and real Postgres),
`tests/test_document_intake_lease.py` (a real child on "another host",
paused, taken over and resumed; without the fence it publishes over the
takeover, without the sweep its rows stay), the real-Postgres races in
`tests/test_document_jobs_pg_idempotency.py`, and the opt-in
`tests/test_document_intake_lease_containers.py`
(`KAZMA_TEST_CONTAINER_LEASE_DRILL_IMAGE`): the stale owner in a real other
container -- frozen with `docker pause`, or cut off the network -- is taken
over on the database's clock and fenced when it returns; with its renewals
blind the fence alone refuses it; blind and unfenced it publishes (control).
**A stop waits for the sweep, and this process's own leftovers are taken
back** (2026-11-08, the drill VM's 48-hour soak, which restarts the service
in one process). The sweep's pass runs in a thread, and cancelling its task
did not stop it: `close()` took the database away under it ("could not be
rolled back (ProgrammingError)"), and the intake it had just claimed named
this process as owner, so every later sweep here skipped it as running until
the process exited. `stop_workers` now sets the sweep's stop flag and waits
for the pass (`wait_then_cancel`, 30 s), which stops between intakes; and
`admission.is_live` -- the tokens `reserve` hands out until `finish`, and the
one a sweep is rolling back (`holding`) -- tells an intake this process is
working on from one it left: a "self" owner not live is rolled back like a
dead one. Gate: `tests/test_document_intake_sweep_stop.py` (the old stop and
the old "self is always running" rule as controls).

**B3. Admission, scratch and claims are bounded and fair** (2026-10-13, F08).
The Web upload refuses a declared length past the limit, a disabled platform,
a disk whose free space minus the incoming file is below the floor, a full
queue, a tenant cap or a spent rate window before reading a byte, and holds
one of `documents.capacity.max_concurrent_uploads` spool slots (per process)
while receiving. Spools, staged copies and parser/operation run directories
live under the storage root: the floor measures them and GC sweeps what a
crash left (`retention._sweep_scratch`). A worker stage below the floor is
retried (`storage_full`), never started. Postgres metadata enforces the tenant
blob quota (an advisory lock per tenant serializes check and insert). Claims
are fair across tenants (`jobs.fair_claim_order`: fewest running first, then
by when the job became ready, so a retry does not jump the queue), on both
backends. The ten JSON routes (`/import`, `/generate`, `/merge`, `/convert`,
`/split`, `/fill-form`, `/redact`, `/index`, `/unindex`, `/search`) read their
body through `documents_api._json_body`: a declared length past 2 MiB is
refused unread, an undeclared one is cut off at the cap, malformed or
too-deep JSON is a 422. FastAPI's `Body(...)` parsed any size into memory
before a route could look at it; never take a document route's body that way.
Gates: `tests/test_document_upload_admission.py` (body-read counter,
the old route as its control), `tests/test_document_claim_fairness.py`,
`tests/test_document_quota_both_backends.py`,
`tests/test_document_json_body_cap.py` (`Body(...)` as its control).

**B4. Every job read names its caller** (2026-10-13, F05). `job_status`,
`job_events`, `jobs_for_document`, `cancel_job` and `retry_job` take a required
`actor_id`; `ingestion.SYSTEM_ACTOR` (not a string) is for code that already
authorized the read. A missing actor is refused -- it used to mean "no check".
An unreadable job reads exactly like an unknown one on the Web routes, the
agent's tools and the gateway (no existence oracle; a replay by another actor
is `idempotency_conflict`). `/api/documents/ops/audit` is admin-only. A
nameless caller is refused before the lookup, whether or not the job exists,
and cancelling needs write access, as a retry does (2026-10-15, the Q04
defensive check: read was enough). Gate:
`tests/test_document_job_read_matrix.py` (signature gate with its control).

**B4b. The asker, in the asker's space** (Spaces S1-12, 2026-11-18; it
replaced DOC-A3's one stand-in principal, `operator`). A document's tenant is
its space and its owner the account that ingested it. The page, chat
`/documents` and the agent's tools ask one function,
`documents.principal.surface_scope`, and act as the scope's asker: Spaces
enforced, the bound scope (its asker must be a member; none bound refuses,
`NO_SPACE_REFUSAL`); one operator, the owner's account in the bound tenant;
platform users without Spaces (the older multi-user mode), as before -- the
page acts as each user's name, chat and the agent refuse
(`MULTI_USER_REFUSAL`, D1). Members of a space share it:
`DocumentIngestionService.grant_member_access` gives the asker an `owner` ACL
row on the space's documents it lacks (granted by each document's owner,
audited `grant_member`, ownership unchanged, re-checked at most once a minute
per space and member, at once after a new document in this process); that is
also how documents made before Spaces (owned by `operator`, `auto-cookie`,
`agent`) reach the owner's account, with no step to run. The grant is made
through the built service: the agent's tools build it first
(`_ensure_service`, then `_scope(svc)`), so the first call of a process, or
of a turn during boot, already reads them. Chat `/documents` reads the tenant
its own turns run in (`_chat_document_scope`: memory's
`resolve_tenant_id`, the one the turn's state and the agent's tools carry):
with a retired memory mode stored, `telegram` or the sender, until Spaces is
enforced or the owner presses "Use one memory" (S1-09's rule); a rule that
cannot be read (a settings outage) means the owner's space, as before, with
a WARNING. The run
supervisor's buttons use `owner_actor` (the owner's account,
single-operator only) until S1-21. `surface_scope`, `owner_actor` and
`grant_member_access` are loop-stall helpers. No surface passes a literal
actor. Gates: `tests/test_document_spaces.py` (one real intake through the
page and the agent, seeded documents for the rest; the old `agent`
principal resolved through the tool's own scope path, older documents with
no grant, a grant through the installed service during boot, the first
chat rule on a retired memory mode and a literal actor as controls), the
matrix's Documents cases (`tests/_document_space_cases.py`).

**B4c. Each space's files in its own folder, once Spaces is enforced**
(Spaces S1-12). A space's folder is `storage.space_folder` (the root for the
owner's space, so an install's documents never move; `spaces/<id>` for any
other). Content is read and written only through
`ContentAddressedStorage.for_space(tenant)` (`SpaceStorage`); the
install-level object has no content call.
- **Before enforcement nothing changes** (`storage.writes_folder`): every
  tenant writes to the root, as before Spaces -- the older multi-user mode's
  tenants and a retired memory mode's (`telegram`) too -- so a chat finds its
  documents where it always did, a backup has the shape it always had, and an
  older build reads every file. Once enforced, each space writes to its own
  folder.
- **Reads look in both places** (`storage.blob_rels` / `manifest_rels`): the
  space's folder, then the root.
- **The root keeps every tenant's files in this release** (a revert reads the
  root only): GC's root folder keeps every live reference of any tenant (its
  recheck asks any tenant; a catalog row with no tenant name is kept there
  too), and `spaces/<f>` keeps only its own tenants'
  (`repository.gc_marks_from_rows` gives references per tenant,
  `gc_is_live_reference(tenant_ids=)`). Removing a copied space's root copies
  is a later step, after S1-33.
- **The copy** (`documents.space_files.copy_into_space_folders`, at start,
  only while enforced) checks every tenant at every start and copies what a
  space's folder lacks, a file an older build wrote during a revert included.
  Journal `.space-files.json`: `copying` recorded before, `moved` after; each
  file digest-checked and renamed into place; a stop or crash resumes. A
  reference whose id names no file, a tenant with no name, a file that cannot
  be read or does not match its digest is a WARNING and the copy goes on
  (an ERROR at every start would trip the deploy's log check).
- **Backups and the export record each file where the live store keeps it**
  (`backup._held_rel`, `backup_pg.held_paths`); a verify accepts either place.
  So a backup taken before enforcement has the old shape; one taken after
  holds `spaces/...` paths, which an older build cannot verify. An import
  drops the journal (the next start re-checks).
Gate: `tests/test_document_space_files.py` (a store writing the space folders
before enforcement, one folder for every tenant once enforced, and the first
collector that let the root forget a copied tenant, as controls).

**B6. One status per error code** (2026-10-13, DOC-A4/A9).
`documents_api._STATUS_BY_CODE` answers every route: writes off, an unavailable
required scanner and `worker_isolation_*` are 503 with `Retry-After`
everywhere (writes-off was 422 on the action routes; the scanner 400). The
upload precheck asks `malware_blocks_intake` before reading the body. Every
body goes through the 2 MiB reader (`_json_body`, `_optional_json_body` for
delete); no route calls `request.json()`/`body()`/`form()`. Gates:
`tests/test_document_api_statuses.py`, `tests/test_document_json_body_cap.py`.

**B5. Outages are retried; only content is rejected** (2026-10-13, F07). A
required malware scanner that is unavailable, a full disk (`storage_full`), a
locked or unreachable metadata store (`metadata_unavailable`, SQLite or
Postgres: `errors.infrastructure_errors`) go to the queue's finite retry
budget, then dead-letter -- never REJECTED. (At upload, a required scanner
that cannot run refuses the upload with `malware_scanner_unavailable`; nothing
is published.) A store error while recording a
stage's outcome leaves the job leased for lease recovery and never ends the
worker loop. Gate: `tests/test_document_fault_transitions.py` (old handling as
the negative-control witness).

**C. Config is ConfigStore-nested.** Primary keys are
`documents.intake.*`, `documents.limits.*`, `documents.ocr.*`,
`documents.workers.*`, `documents.capacity.*`, `documents.retention.*`,
`documents.gc.*`, `documents.security.*`, `documents.indexing.*`,
`documents.quotas.*`, `documents.render.*` and `documents.storage_root`, plus
rollout `documents.enabled` / `shadow` / `default_authoritative`. Prefer
`get_document_config()` live reads. Legacy aliases exist for a few keys only
(`documents.intake_max_bytes` / `intake_max_files`,
`documents.worker.lease_seconds` / `max_retries`,
`documents.shadow_mode`, `documents.authoritative_by_default`,
`documents.limits.max_uncompressed_bytes`) — do not invent
`documents.max_pages` style flat keys without wiring aliases. A default
coordinator captures one strict immutable policy per operation
from `ConfigStore.snapshot("documents")` (one SELECT, no per-key TTL). Share its
`DocumentPolicy` with execution, render, capacity, knowledge and GC layers; never
refresh a shared mutable config during an operation. Explicit configs stay fixed.
Worker tasks must detach startup's ContextVar and capture again per stage.
Unavailable/invalid live policy refuses execution; rollout never enables writes
on a settings outage. Remote parsing still checks live denial before each upload.
Storage root, committed-reference quota, worker concurrency/lease/heartbeat and
queue retry timing are constructed-resource settings: pin them until coordinator
reconstruction and report configured/effective differences in readiness `policy`.
Intake version metadata and parsed manifests record the effective policy hash.
Backup/migration resolve the installed CAS root (`get_document_storage_root`),
never a pending proposed root, and offline resolution does not create a service.
Backup and migration take their path from where the catalog is
(`backup.installed_document_backends`, F3); a fallback SQLite database never
stands in for a Postgres catalog.
A new document Postgres table goes in `db.pg_backup.KAZMA_PG_TABLES` (gate:
`tests/test_pg_backup.py::test_kazma_pg_tables_covers_document_ddl`, which reads
the DDL) AND in `backup_pg.DOCUMENT_PG_TABLES` (gate since 2026-10-13, DOC-A13:
`tests/test_document_pg_tables.py`, the DDL and a started store on real
Postgres, both directions). The Postgres store's backup and restore are F2;
RPO/RTO on deployment hardware and store sizes remain open.
Every `DocumentConfig` field has a reader outside `config.py` (DOC-A2: eight
settings were read, validated and acted on by nothing -- `fence_llm_content`,
`allow_encrypted_documents`, `allow_external_render_resources` among them --
and were removed; none was shipped in kazma.yaml, so no stored default to
retire). Gate: `tests/test_document_config_fields_read.py`.
Gates: `tests/test_document_live_policy.py`, `tests/test_config_store_snapshot.py`.

**D. Multi-replica honesty.**
`jobs_pg.py` can claim jobs with `SELECT … FOR UPDATE SKIP LOCKED` when
Postgres is configured. The job queue is on Postgres whenever Kazma's backend
is (`KAZMA_DOCUMENTS_JOBS_BACKEND=sqlite` keeps it local). Document
**metadata** (documents/versions/blobs) follows the job backend when
`KAZMA_DOCUMENTS_METADATA_BACKEND` is unset or `auto` -- so it is on Postgres
by default on a Postgres install -- `postgres` forces `repository_pg.py` and
`sqlite` keeps SQLite; either Postgres store needs the pool up. Readiness
must report single-replica for metadata **whenever the SQLite backend is
active** — never claim multi-replica HA from the availability of the Postgres
path alone, only from the backend actually in use. Single-replica is a
fact, not a degradation: `status` is `degraded` only when a configured
backend is not the one serving (`*_fell_back_to_sqlite`); a SQLite backend
chosen in `.env` is `ready` with `metadata_multi_replica: false`
(`tests/test_document_operations_phase9.py::test_readiness_status_means_a_configured_backend_is_serving`).
The live Documents page said "Storage: degraded" in red over a deliberate
SQLite choice (2026-09-28).

