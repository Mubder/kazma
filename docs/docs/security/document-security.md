---
id: document-security
title: Document Security
sidebar_label: Document Security
description: Security architecture and threat model for the Kazma document intelligence platform.
---

# Document Security

> This page is published at [kazma.ai/docs/security/document-security/](https://kazma.ai/docs/security/document-security/) ([Arabic](https://kazma.ai/ar/docs/security/document-security/)).

The document intelligence platform is designed with the assumption that every
document is hostile until proven otherwise. This page documents the security
architecture, threat model, and defense-in-depth measures, and states where
each one stops.

**Related:** [Document Intelligence](../guide/document-intelligence.md) ·
[Document processing ops](../ops/document-processing.md) ·
[Phase map](../guide/document-phases.md)

---

## Threat model

### Attacker capabilities (assumed)

- Can upload crafted documents (PDF, DOCX, XLSX, PPTX, images, plain text, CSV, HTML, RTF).
- Can embed malicious content: prompt injection, XXE, macros, active content, polyglot files, compression bombs, archive bombs, corrupt/malformed structures.
- Can attempt path traversal through filename or embedded references.
- Can attempt SSRF through external OOXML relationships, PDF links and actions, or resources a rendered document names.
- Can attempt redaction bypass.
- Can try to read or control another actor's documents and jobs.

### Attacker capabilities (out of scope / mitigated externally)

- Filesystem access (workspace root sandboxing for import paths).
- OS-level filesystem and network isolation of parser processes in the default
  `process` profile. The `container` profile provides both, attested per
  deployment; see [Layer 3](#layer-3-isolated-execution) for what is and is not
  qualified.
- Kernel escapes from a job container (containers share the host or VM kernel;
  no gVisor/Kata runtime is qualified).
- GPU/memory side-channels beyond the resource limits enforced by the OS.
- ClamAV's own handling of a hostile file: the scanner runs on the Kazma host,
  outside both worker profiles.
- What a remote parsing provider does with the bytes it receives, once an
  operator turns remote parsing on.

---

## Defense layers

### Layer 1: Intake gate

- **Streamed limits:** the document platform accepts at most 50 MiB per file
  (`documents.intake.max_bytes`) and at most 10 inputs to one merge
  (`documents.intake.max_files`); the Web upload takes one file per request.
  Chat gateway attachments use 20 MiB per file, 10 files and 50 MiB aggregate
  per turn. Limits are enforced while streaming, before unbounded buffering.
  The Web routes that take JSON read at most 2 MiB of body.
- **Workspace containment (import path):** workspace-safe local imports resolve
  through `workspace.binding.resolve_active_root()`; a path whose resolved
  location leaves the active workspace (`..`, an absolute path elsewhere, a
  link out) is refused.
- **Extension/content mismatch:** MIME sniffing rejects mismatched file
  extensions before any parser sees the content.
- **Admission before the body:** the Web upload answers its budgets -- declared
  length, writes enabled, storage floor (counting the incoming file), queue,
  tenant caps, rate window -- before reading a byte, and a bounded number of
  uploads are received at once per process; the spool lives under the storage
  root, measured and swept.
- **A taken-over intake cannot publish:** an intake whose owner is proven dead,
  or whose lease lapsed (its host crashed or was cut off), is rolled back by
  another process, and the job that would publish it is created only while the
  reservation is still its own -- a resumed stale intake can never put a
  document beside, or over, the recovery. Its leftover rows carry its token
  and are removed.
- **Scope comes from the server:** the Web routes take the tenant from the
  request's tenant context and the actor from the signed-in principal, never
  from the request body.
- **Job reads are authorized like document reads:** every job status, history,
  cancel and retry names its caller and checks the document's ACL (cancel and
  retry need write access); a job the caller cannot read is indistinguishable from one
  that does not exist (Web, agent tools, gateway), and an idempotency key used
  by another actor answers `idempotency_conflict`. The operations audit trail
  is admin-only. On a single-operator install every surface (Web, chat, the
  agent's tools) acts as the operator, not as each chat user; older documents
  get an audited `owner` ACL row for it (`documents.principal`). With
  platform users chat and the agent refuse rather than act for everyone.

### Layer 2: Content sniffing

Inside the parser worker, before a parser opens a file, content sniffing
checks:

| Check | Defense |
|---|---|
| OOXML structural analysis | Rejects unsafe member paths (absolute, drive letter, `..`), symbolic-link members, encrypted archives, XXE (`<!DOCTYPE`, `<!ENTITY`, also UTF-16/32 encoded), external relationships (`TargetMode="External"`), macro payloads (`vbaProject.bin`, and macro-enabled extensions such as `.docm`), embedded objects (`embeddings/`, `activeX/`, `oleObjects/`), nested archives, and compression bombs (expanded size, expansion ratio and image count over configurable budgets) |
| PDF policy | Rejects encrypted PDFs, polyglot (PDF/ZIP) files and active content: JavaScript, XFA forms, launch, remote and embedded GoTo (including a `/GoTo` or `/Thread` action that names another file through `/F`), SubmitForm, ImportData, rendition/rich-media/sound/movie actions, embedded files (with or without `/Type`) and links that are not absolute `http`/`https`/`mailto` (file and network-share targets leak credentials). In-document navigation stays. Action and key names are matched case-insensitively. One bounded walk (`documents/pdf_policy.py`: at most 100,000 objects and depth 128; an unreadable or indeterminate structure is refused) applied in the worker on intake, every mutation input and output (redaction included), and every rendered PDF. A PDF a user supplies is opened only inside the worker; the three in-process openers left check PDFs Kazma itself just rendered, each declared in `tests/test_pdf_openers_stay_in_workers.py` |
| UTF-8 validity | Rejects invalid byte sequences without attempting lossy decode |
| Content-type validation | Rejects binaries claiming to be text, HTML claiming to be plain |
| Archive member count | Enforces configurable max archive members |
| Rendering (HTML/Markdown to PDF) | Fetches nothing external: only `data:` URIs and files in the job's approved asset folder; anything else stops the render (`external_resource_denied`) |

No setting loosens these checks. The settings
`documents.security.allow_encrypted_documents` and
`documents.security.allow_external_render_resources` were read and never
consulted; they were removed on 2026-10-13, and every remaining document
setting has a reader (`tests/test_document_config_fields_read.py`).

### Layer 3: Isolated execution — two profiles {#layer-3-isolated-execution}

The admin's setting `security.execution.document_worker_isolation`
(Settings → Documents) selects where every parse, OCR, render and mutation job
runs; `KAZMA_DOCUMENT_WORKER_ISOLATION` wins when it is set. The setting is
protected from the agent's config tools and never changed by a settings
restore; one that cannot be read refuses documents. `GET /api/documents/ops/readiness` and
`GET /api/documents/health` report the profile in force (`worker_isolation`):
its mode, whether filesystem and network isolation hold, and for the
container profile the broker's attestation. Readiness's
`hostile_document_profile` reads `unqualified` for `process`, and
`container_broker` (or `unavailable` while the broker is not ready) for
`container`.

Structural PDF/OOXML preflight and local Docling salvage also execute in the
bounded parser worker. PDF inspection requires a readable structure and follows
nested, indirect and chained actions under traversal budgets. Missing inspection
dependencies or an unreadable structure cannot authorize a file.

#### `process` (default): host subprocess, no filesystem or network jail

- `python -I` (isolated mode — no user site packages).
- **Scrubbed environment:** the worker inherits only an allowlist of system
  variables (`PATH`, locale, temp folders and the like); secrets and unsafe
  variables (`PYTHONPATH`, `LD_*` and the like) are not passed.
- Windows: one Job Object bounds the worker's whole process tree — memory
  (`documents.workers.memory_mb`, default 1,024 MiB), 32 active processes,
  and a hard cap of one logical processor (the Job Object's CPU rate is a
  share of the whole machine, so it is sized from the processor count). The
  worker starts suspended and runs only once the Job Object holds it: a
  venv's `python.exe` is a launcher, and a job assigned a moment late held
  only the launcher, so before 2026-10-13 the first run of each process ran
  without these limits (and the CPU cap capped nothing). The configured
  wall-clock deadline remains the total runtime bound; this is not an
  aggregate CPU-seconds quota. Unix applies memory (address space) and
  CPU-time limits with `setrlimit`, per process, not per tree;
  process-count limiting is not yet implemented there.
- Configurable timeout — process killed, not leaked.
- Stdout/stderr bounded; output size limited.
- On timeout/OOM/silent exit: **fail closed** — no partial data escapes.

**Containment limit:** the subprocess boundary does not confine host filesystem
access or establish a network namespace/firewall jail. Scrubbed credentials and
resource limits do not establish air-gapped execution. Windows resource-limit
setup fails closed before parser code starts if its Job Object cannot be
configured. Readiness reports this profile as
`hostile_document_profile: unqualified`; use the container profile for
documents you do not trust.

#### `container`: one attested container per job (Docker deployment profile)

- A separate **broker** (`kazma_core/documents/worker_broker.py`; its own
  container in `deploy/docker-compose.document-broker.yml`, or on a native
  install a process the guard runs beside Kazma) is the only process that
  holds the Docker engine connection. The engine API is host-root authority:
  it is never handed to Kazma. The guard's broker listens on loopback only,
  authenticates every request with a token file only the guard's account can
  read, and runs with an environment reduced to what Python and the docker
  CLI need (the guard's own secrets stay out).
- Kazma sends the request and exactly the files it names as one bounded,
  digest-checked stream (`worker_bundle.py`). Shared document storage is never
  mounted; the parser's scratch `storage_root` is a per-job directory.
- Each job runs in a fresh container of **one** image, pinned by its image id
  when the broker starts: `--network none`, no mounts, read-only root, a
  size-bounded `/tmp` tmpfs (`nosuid,nodev,noexec`), memory equal to the
  memory+swap limit (no swap), CPU and PID limits, `nofile` and `core`
  ulimits, `--ipc none`, a private cgroup namespace, no container log, every
  capability dropped, `no-new-privileges`, uid 10001, Docker's default seccomp
  profile, and `--init` to reap descendants. The broker's operator sets the
  limits when starting it (defaults: 2 GiB memory, one CPU, 256 PIDs, 768 MiB
  `/tmp`, two jobs at once). A client can ask for one of three fixed workers
  (parser, mutation, renderer) and a shorter deadline — never an image,
  command, environment variable, mount or limit; the fourth worker, the probe,
  runs only for the broker's own attestation and engine report (and stress
  jobs from a broker started with `--qualification`, never in production).
- **Attestation:** before it serves, the broker runs a probe inside a real job
  container and checks identity (exactly uid and gid 10001, not merely non-root),
  capabilities (the effective, permitted, ambient and bounding sets all empty),
  `no_new_privs`,
  seccomp, network interfaces (`lo` only), a refused outside connection and
  DNS lookup, visible host paths, every mount, the read-only root, the cgroup
  memory/swap/PID/CPU limits and the tmpfs size. Any mismatch refuses to start
  the broker.
- A deadline watchdog, the output cap and a client that disconnects each kill
  the job container. Every container is inspected (an OOM kill is reported as
  such) and removed, and the removal is verified; a container that cannot be
  removed keeps its slot out of service. A starting broker removes containers
  a previous run left.
- The returned stream is untrusted: only the worker's result document and one
  `output.<ext>` are accepted, sizes are capped before anything is written,
  digests are checked, and no existing file is overwritten.
- **Fail closed:** an unconfigured, unreachable, unattested or busy broker
  refuses the run with a typed error (`worker_isolation_unavailable` /
  `worker_isolation_busy`, retried within the queue's finite budget); an
  invalid setting refuses every run (`worker_isolation_invalid`). There is
  no fallback to the `process` profile.

**Qualified** on Docker Engine 29.8.2 (Docker Desktop, Linux containers,
cgroup v2) on Windows 11, 2026-10-13: attestation, parsing parity with the
local worker on English/Arabic/bilingual fixtures, the hostile corpus,
mutation and rendering, memory/PID/CPU/scratch/deadline/output limits,
client-disconnect cancellation, descendant cleanup, a stale-container sweep,
and a broker-container/client-container topology run (evidence and its
limits: `docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md`, F02).

**Independent review.** On 2026-10-15 an independent reviewer (no Kazma
authorship, no owner infrastructure) tested these layers against the claims
in `docs/audits/Q04_DOCUMENTS_SECURITY_REVIEW_PACK_2026-10-15.md`. Thirteen of
fourteen held. The finding (a `/GoTo` or `/Thread` naming another file passed
the PDF policy) was fixed and retested:
`docs/audits/Q04_REVIEW_RESULTS_2026-10-15.md`.

- **Engine readiness is the image's.** The broker collects the image's own
  parser, renderer, mutator and OCR report at start (and refuses to serve
  without it); in this profile Kazma decides what it can run from that report,
  never from the host's packages, and readiness flags an image built from
  other code, an older image or a fallback build (`image_code_matches`,
  `image_current`, `image_fallback`).
- `security.execution.container_required` (the strict execution profile)
  refuses the `process` profile for documents (`worker_isolation_required`);
  a policy that cannot be read refuses as well.

**Not qualified / limits:** a Linux Docker Engine host (CI's
`Document worker container profile` job runs it, and had not yet had its first
run on 2026-10-13), rootless Docker, Podman and Kubernetes;
containers share the host (or VM) kernel, and no gVisor or Kata runtime is
qualified; the local Docling salvage tier is not in the worker image; each job pays a container start (about 0.35–0.8 s on
the qualification host); the broker's TCP transport is token-authenticated
plaintext for private networks only (a non-loopback address needs
`--allow-non-loopback`; prefer the unix socket); amd64 image only; no
representative soak.

#### Remote parsing (third-party egress)

When a parse scores below the salvage threshold, the remote tier can upload
the **original document bytes** to LlamaParse or Reducto.

- **Off by default** (`documents.security.remote_parse`). A provider API key in
  the environment is not consent. `KAZMA_REMOTE_PARSE=0` (and `KAZMA_DOCLING=0`
  for the local tier) can only turn a tier off, never on.
- It runs in the Kazma process after the worker returns, outside both
  isolation profiles, because it needs the network.
- Before **each** provider the live setting is read again (a revocation stops
  the next upload) and a durable `egress` audit row (`remote_parse_attempt`)
  carrying the job's tenant, actor, document, version and job is written.
  Failure to persist it, or a job with no actor, refuses the upload. The
  outcome is audited as `remote_parse_success` or `remote_parse_failure`, and
  a failed provider falls through to the next configured one. A refused or
  failed remote parse is logged at WARNING with its error type; the local
  reading is kept.
- The provider client ignores proxy environment variables, follows no
  redirects and refuses an answer over 32 MiB. Salvaged text carries no
  physical page numbers.
- A transient read (a chat attachment) has no durable audit scope, so its
  remote attempt is refused before any byte moves.

#### Malware scan (ClamAV, pluggable)

Intake runs
`kazma_core.documents.malware.scan_if_configured` on quarantined bytes, and
every queued parse scans the original again under the policy in force then.
The scanner is the local `clamdscan` (preferred) or `clamscan`; document bytes
never go to a network scanning service.

| Mode (`documents.security.malware_scan`) | Behavior |
|---|---|
| `off` | Never scan |
| `auto` (default) | Scan when `clamscan` or `clamdscan` is on PATH; otherwise, and when the scanner fails, skip with a log line (unless fail-closed) |
| `on` | Require a working scanner |

When a scan is required (`on`, or `documents.security.malware_fail_closed=true`,
which is the default under `KAZMA_PRODUCTION=1`) and cannot run, the document
is held, never judged unsafe: an upload is refused with
`malware_scanner_unavailable`, readiness reports
`accepting_durable_writes: false`, and a queued job is retried within the queue's
finite budget, then dead-lettered. Infected files are rejected with
`malware_detected`. Install ClamAV system packages separately (not a pip
dependency).

### Layer 4: Prompt fencing

Document text reaches the model inside an untrusted-data fence, for example:

```
<kazma:data source="document" untrusted="true">
[document content]
</kazma:data>
```

| Path | Fence source |
|---|---|
| A document read through the agent's document tools | `document` |
| A chat attachment's auto-excerpt (the first 2,000 characters, after a best-effort transient `DocumentService` parse) | `document_attachment` |
| Document hits from a Knowledge library search | `document_knowledge` |
| A document's text in the "organize this into Markdown" prompt (the document intent handler and pipeline) | `document` |

Each of these surfaces fences its output itself (`format_untrusted_block()`).
It is not a setting: nothing switches the fence off (the unused
`documents.security.fence_llm_content` was removed on 2026-10-13).

This fence tells the model that the content is observation data, not
instructions — mitigating prompt injection from document text. It reduces
attack success; it does not eliminate it (measured numbers:
[the prompt fence](../guide/security-and-safety.md#10-the-prompt-fence--what-it-stops-measured)).

Self-improvement prompt deltas go through the same fence machinery via
`format_untrusted_block()` with the `prompt_fence` safety check.

### Layer 5: Redaction verification

Redaction is a PDF operation (`pdf:redact`, PyMuPDF and Pillow inside the
mutation worker):

1. The source passes the PDF policy first. A page with images or vector
   drawings is refused (`redaction_source_not_verifiable`): it needs a reviewed
   redaction.
2. Every term must be found with coordinates (`redaction_term_not_located`
   otherwise).
3. Each page is rasterized at 200 dpi, the matches are painted over, and the
   page is rebuilt as an image — physical/raster redaction, not an overlay
   box over text that is still there.
4. The output is verified: no term can be re-extracted or found in the raw
   bytes, no metadata, attachments, annotations or form fields remain, the
   page count is unchanged and every page renders. It then passes the PDF
   policy, like every mutation output.
5. The result is a **new immutable artifact** — originals are not silently
   rewritten. Redaction terms are never logged or audited.

**Confirmation model:**

| Path | Interactive confirm? |
|---|---|
| Web UI Documents page | Yes — `kazmaPrompt` / `kazmaConfirm` before `POST …/redact` |
| REST API / agent `document_redact` / gateway `/documents redact` | No UI dialog — caller is trusted as an authenticated actor; ACL still applies |

Insecure overlay redaction (opaque boxes over text without removing underlying
content) was identified in pre-platform code and is **not** the platform path.

### Layer 6: Audit immutability

`document_audit_events` **rejects** any modification after insert on both
metadata stores; only the retention sweep deletes aged-out rows. On SQLite
(`documents.db`) UPDATE/DELETE triggers refuse it, and the sweep deletes
through a guarded control flag inside its own transaction. On Postgres one row
trigger (`document_audit_guard`, function `kazma_document_audit_guard`) refuses
every UPDATE and every DELETE outside a transaction the sweep marks
(`SET LOCAL kazma.document_audit_prune = 'on'`); the sweep prunes in bounded
batches there too. Until 2026-10-13 the Postgres table had no trigger and the
prune ran only on SQLite (on Postgres it recorded an `audit_prune` error and
kept every row). `pg_dump -t` carries a trigger but not its function, so every
restore creates the function first (`db/pg_backup.KAZMA_PG_FUNCTIONS`), and a
gate requires every trigger function in product code to be declared there
(`tests/test_document_audit_pg.py`, real Postgres, with a fresh-database
restore drill).

The audit trail is tenant-scoped. Details are allowlisted to safe scalars —
document content, filenames, redaction terms, and secrets never enter the
audit. Ordinary audit writes are best-effort (a failure is logged and the
operation continues); the remote-parse egress audit is required, and its
failure refuses the upload.

### Layer 7: Storage integrity

- **Content-addressed storage:** Every blob is identified by SHA-256.
  Physical path: `{root}/{kind}/sha256/{aa}/{bb}/{full_hash}`.
- **Hash verification on write:** bytes are hashed while they stream in,
  checked against the expected digest and size, promoted atomically and hashed
  again after the move; a mismatch removes the file (`BlobChecksumError`).
  This covers quarantine → originals promotion.
- **Dedup:** a blob whose hash is already stored reuses the stored file after
  re-hashing it; a stored file whose digest or size no longer matches refuses
  the write.
- **Links:** garbage collection does not follow or delete through a symlink or
  junction, or a file whose real path leaves the store, and the migration
  export's data-directory copy never follows a link. The native document
  backup copies the blobs its snapshot references from their content-address
  paths and verifies every copy against its SHA-256.

---

## Hostile corpus certification

A **deterministic, programmatically generated** hostile corpus (26 cases,
`kazma_core/documents/hostile_corpus.py`) covers:

| Category | Cases |
|---|---|
| Archive bombs | Compression bomb, nested archive, member flood |
| XML attacks | XXE, external OOXML relationships |
| Active content | Macros; PDF JavaScript/launch/embedded file, XFA form, SubmitForm, rendition, an embedded file without `/Type` |
| External references | Remote GoTo to a network share, a link to a network share |
| Encryption | PDF with /Encrypt declarations |
| Polyglot | PDF/ZIP combined file |
| Type mismatch | HTML bytes with a `.txt` extension |
| Corruption | Truncated OOXML, malformed cross-reference, invalid UTF-8 |
| Limit attacks | Page, pixel, cell count exceeds budgets |
| Prompt injection | Instruction-like text in documents |
| Unicode/BiDi | Arabic-English with confusable extensions and bidi controls |
| Navigation control | A PDF with in-document navigation and an https link, which must still parse (negative control) |
| Parser crash | Synthetic crash containment test |

Every case has:

- A reviewed description.
- Expected disposition (reject / fenced).
- Expected error codes.
- A recorded SHA-256 and size.

The committed manifest (`tests/fixtures/documents/hostile_manifest.json`) is
compared with the generated one field by field. The SHA-256 and size are left
out of that comparison, because ZIP compression differs between zlib builds;
any other difference fails the `hostile_corpus` check. Cases marked `sniff`
are checked by calling the sniffer in the certifying process; `parse` cases go
through the isolated worker and must come back rejected with an expected code
or fenced; the `sandbox` case removes the worker's result and must fail
closed.

The check runs in CI's test job (`tests/test_document_certification_phase10.py`)
and locally:

Run: `python scripts/certify_documents.py`

The script also reports the configured isolation profile (`process` is
CONDITIONAL, never "confined"), the extraction-fidelity corpus and a SQLite
backup/restore drill, and marks multi-replica, multi-day soak and external
review NOT RUN.

---

## Rollback safety

| Action | Safe? | Detail |
|---|---|---|
| Set `documents.enabled=false` | ✅ | Stops new durable writes; existing blobs, jobs, manifests, metadata preserved |
| Set `documents.shadow=true` | ✅ | Shadow/canary posture; no data loss on toggle |
| Set `documents.default_authoritative=true` | ✅ | Routes product path to platform; rollback preserves data |
| Delete `documents.db` (or the Postgres document tables) | ❌ | Destroys metadata, job state, audit trail — backup first |
| Delete content store directory | ❌ | Destroys all blobs — irrecoverable without backup |

---

## Recommendations for production

1. **Run documents you do not trust in the container profile**
   (Settings → Documents; the guard runs the broker on a native install), confirm
   readiness `worker_isolation.ready` and `image_code_matches: true`, and set
   `security.execution.container_required` so the `process` profile is refused.
2. **Install ClamAV** (`clamscan`/`clamdscan` on PATH) for production; verify
   readiness `malware.available` and set `documents.security.malware_scan=on`
   (or keep `auto` with `documents.security.malware_fail_closed=true`, the
   default under `KAZMA_PRODUCTION=1`).
3. **Leave `documents.security.remote_parse` off** unless original documents
   may go to LlamaParse or Reducto; when it is on, review the `egress` rows of
   the operations audit.
4. **Set `documents.capacity.storage_free_floor_bytes` conservatively** (default
   is 512 MiB; many operators prefer ≥ 1 GiB).
5. **Review `documents.retention.*` / `documents.gc.*`** for compliance.
6. **Run the bounded certification smoke**, then qualify the intended deployment
   with a real sustained mixed-load recovery soak. `scripts/certify_documents.py
   --soak` remains an extended smoke, not multi-day evidence.
7. **Monitor** `GET /api/documents/ops/capacity` for degraded reasons.
8. **Audit** `GET /api/documents/ops/audit` periodically (admin only).
9. **Backup** document state and content alongside your primary backup. The
   scheduled six-hour job snapshots a SQLite store, or takes the coordinated
   Postgres backup (`kazma_core/documents/backup_pg.py`) when the installed
   store is on Postgres. Restore drills have run on fixture stores; measure
   RPO/RTO on your own hardware and store size.
10. **Treat redaction via API/tools as privileged** — UI confirm is not a
    server-side gate on those paths.
