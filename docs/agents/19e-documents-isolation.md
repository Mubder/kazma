<!-- AGENTS.md, piece 19e-documents-isolation. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**E. Fence + security honesty.**
LLM-visible document text goes through untrusted fences
(`source="document"` or chat `document_attachment`), the document intent
handler's "reorganize this document" prompt included (it put it in raw
until 2026-10-15; the unreached document pipeline was removed 2026-10-27,
audit AUD-075; `tests/test_document_text_fenced_to_model.py`,
the unfenced prompt as control). Auto-index is **off** by
default. Redaction UI confirm is Web-only; API/tools can redact under ACL.
**Malware:** `documents/malware.py` runs on quarantine via
`scan_if_configured` (`auto`/`on`/`off` + fail-closed). Uses system
`clamscan`/`clamdscan` only — no third-party upload of document bytes.
Sandbox, `KAZMA_DOCUMENT_WORKER_ISOLATION=process` (default): scrubbed env;
Windows Job Object memory/CPU-rate/process limits with fail-closed parser
start -- the worker starts SUSPENDED and is resumed only once the job holds it
(DOC-A12, 2026-10-13: a venv's `python.exe` is a launcher, and a job assigned
a moment late held only the launcher, so the first run of every process ran
uncapped while reporting "enforced"); the CPU cap is `cpu_cores=1`, sized by
`security.process_budget.job_cpu_rate` (CpuRate is a share of the WHOLE
machine: the old 100*100 was no cap). Every Job Object assignment in product
code holds a suspended child (gate `tests/test_job_objects_hold_suspended_children.py`,
`code_exec` too; busy-worker and memory-hog measurements with the old launch
as controls in `tests/test_document_sandbox.py`); Unix memory/CPU-time
`setrlimit` (no process-count limit). It confines
no filesystem/network access; readiness says `hostile_document_profile:
unqualified`. `container` (2026-10-13): both worker call sites (service
parser, operations mutation/renderer) branch to
`worker_container.run_container_worker` -> the broker
(`documents/worker_broker.py`, stdlib-only), the ONLY holder of the Docker
socket -- never mount it into Kazma. One container per job under one fixed
policy (no network/mounts, read-only root, tmpfs, cgroup limits, caps
dropped, uid 10001); the broker attests it from inside the image before
serving -- the exact uid and gid and an empty capability bounding set too
(2026-10-15: any non-root uid passed, and the bounding set was read and never
checked). Inputs/outputs are digest-checked bundles (`worker_bundle.py`); the
output is untrusted. No fallback to `process`; an invalid mode refuses every
run; a crash with no stream is `parser_result_missing`, as locally. Never
block the output pump on a kill: an attached container whose stdout backs up
cannot be killed (Docker 29). In container mode the four readiness factories
(`get_parser_registry`, `get_renderer_registry`, `get_mutation_registry`,
`get_ocr_health`) answer from the image's own capability report, as live
views, never from host packages; the broker refuses to serve without it. A
broker that is not ready answers the retryable `worker_isolation_unavailable`
(a full one `worker_isolation_busy`), never a permanent engine error. The
image reports a fingerprint of its document code: readiness says
`image_code_matches: false` and `document_worker_image_outdated` when the
image was built from other code than Kazma runs (rebuild it:
`scripts/document_worker_image.py build`; the guard's broker switches by
itself, E1). The image carries no Docling and no httpx: local salvage is not part
of the container profile, and remote parsing stays in the Kazma process (K).
It carries the neural OCR engine (G4) and its models: the `ocr-models` build
stage (hash-locked `deploy/document-worker/ocr-models.txt`, CPU PyTorch) runs
`scripts/ocr_models.py install`, only `/models` reaches the final image
(`KAZMA_OCR_MODEL_DIR=/opt/kazma/ocr-models`, read-only, verified at build),
and `parser_container_request` points the parse at that copy -- the host's
model directory is never sent. The capability report names both OCR engines
(`ocr.engines`); an older image's report without them reads neural as absent.
**The image's locks keep up with Kazma's own** (2026-11-11): the engines'
direct pins (`deploy/document-worker/requirements.in`) are versions `uv.lock`
holds, and no package either hash lock holds is below a floor pyproject.toml
declares or a recorded advisory's fix; the engines hold no httpx, Docling or
torch. The locks had kept Pillow 12.3.0, pypdf 6.19.0, brotli 1.2.0 and, in
the build stage, filelock 3.32.3 and setuptools 83.0.0 -- 72 advisories --
after Kazma's own environment had moved past each. Gate:
`tests/test_document_worker_locks.py` (the old locks as the control). A new
lock needs the image rebuilt.
`security.execution.container_required` refuses the `process` profile
(`worker_isolation_required`; unreadable policy refuses too) -- checked where
Kazma starts a worker, never inside one. Gates:
`tests/test_document_worker_container.py`; real Docker
(`KAZMA_TEST_DOCUMENT_WORKER_IMAGE`, CI job `Document worker container
profile`): `tests/test_document_worker_container_docker.py`.
Rendering engines: a readiness probe imports exactly what its render path
uses -- WeasyPrint 70 (the locked floor) removed `default_url_fetcher`, and
every HTML->PDF job failed while the engine read "ready". The renderer's
fetcher (a `URLFetcher` subclass) allows only `data:` and files inside the
approved asset folder (`tests/test_weasyprint_renderer.py`). Every LibreOffice
call goes through `binaries.run_soffice_cli`, which kills the whole process
tree on timeout and bounds the last wait: `subprocess.run(timeout=)` waited
without a limit for the pipes soffice.bin still held and hung the
certification, and the legacy Office parser still called it that way until
2026-10-15 (`tests/test_soffice_call_bounded.py`: the old call as its control,
the legacy parser, and a gate -- no product module naming LibreOffice starts a
process itself, two probes of other programs declared).

**E1. Which profile runs is the admin's setting; the guard runs the broker**
(2026-10-15). Live that day: the install required containers, documents ran
in `process`, no broker ran -- the container profile needed three `.env`
lines and a hand-run process -- and every document was refused.
- **One answer for the profile** (`worker_container.worker_isolation_mode` /
  `isolation_selection`): `KAZMA_DOCUMENT_WORKER_ISOLATION` wins when set
  (the environment's floor); else the setting
  `security.execution.document_worker_isolation` (`process`/`container`,
  Settings -> Documents, `PUT /api/settings/documents` `worker_isolation`,
  validated by `settings_validation`); none saved is `process`. Under
  `security.` so `config_save` refuses it, and a restore never changes it
  (`KEY_RULES` credential class: an older backup must not move documents out
  of containers). An unreadable setting refuses (`document_policy_unavailable`),
  never guesses `process`.
- **A worker never reads settings**: every worker entry point (`main` of the
  parser, mutation and renderer workers, and `worker_entry`) calls
  `worker_process.mark()` first, and the profile is then `process` (its own
  engines). The local profile scrubs the worker's environment, so a settings
  read there would open the default data folder's store. A new worker entry
  point marks itself (`test_every_worker_entry_point_marks_itself_first`).
- **Kazma asks the guard** (`documents/broker_supervision.py`): it writes
  `want` and the broker URL it uses to `KAZMA_GUARD_BROKER_REQUEST_FILE` --
  at boot (`start_broker_requests`, in a thread), on a profile change (a
  ConfigStore change listener that publishes on a retained thread of its own,
  `_drain_publishes`) and on the maintenance sweep "document worker broker";
  only when the decision changed. An unreadable profile leaves the request
  standing. A URL that is not the guard's (a `.env` broker) means the guard
  runs none. Readiness reads the guard's record back (`supervisor_report`:
  `guard`, `old_guard` -- `document_broker` missing from `guard_features` --,
  `external`, `none`).
- **One step that fixes it**: `worker_isolation.action` (`isolation_action`)
  is a `code` the pages word from the catalog (`documents.isolation_action.*`),
  an English `message` and the `command` to run; the Documents page shows it,
  Settings -> Documents offers *Use the container profile* when the install
  requires containers. Never tell the owner to edit `.env` for what a setting
  decides.
- **The image is named by its build inputs** (`documents/worker_image.py`,
  stdlib-only, loaded by the guard by path): the Dockerfile, its
  `.dockerignore`, the requirements and every file of `kazma_core` the build
  context sends, CRLF-insensitive -> `kazma-document-worker:src-<16 hex>`.
  `test_the_tag_covers_exactly_what_the_build_sends` holds the walk to the
  `.dockerignore`. `scripts/document_worker_image.py build` builds and labels
  it (`io.kazma.document-worker-image.source` / `.documents-code`); never make
  a boot build an image. A build then removes the builds of other code
  (`_prune_older`, `--no-prune` keeps them): this code's, what the broker
  serves or asks for, the newest other one (a revert finds it) and one a
  job's container still uses are kept, a refused removal is said and never
  fails the build. Every build used to stay, one per deploy: 34 (about 7.7
  GB) on the live engine by 2026-11-14 (`tests/test_document_worker_image.py`,
  `--no-prune` as the control). The broker serves the newest labelled build while
  the tag asked for is missing (`--fallback-to-newest-built`, status
  `image_fallback`/`image_source`) and exits 6 to switch, idle, once it
  appears. Readiness: `image_current: false` or `image_fallback` is
  `document_worker_image_outdated`; one ops alert per build
  (`documents.worker_image_outdated`, stamped
  `system.documents.worker_image_alerted`).
- **Broker exit codes** are the guard's vocabulary: 2 cannot start, 3 policy or
  no engines, 4 no image (`_NoWorkerImageError`), 5 no Docker engine
  (`_DockerEngineUnavailableError`, `check_engine` first), 6 newer image, 7 the
  TCP address cannot be listened on (a test bind, first, before attesting). The
  broker handles the guard's CTRL_BREAK (SIGBREAK) and SIGTERM, also while it
  attests, and removes its job containers on the way out.
Gates: `tests/test_document_worker_profile.py`, `tests/test_document_worker_image.py`
(the CTRL_BREAK stop with an unhandled one as control),
`tests/test_guard_document_broker.py`; real Docker (opt-in
`KAZMA_TEST_DOCUMENT_WORKER_IMAGE`): `tests/test_document_broker_guard_docker.py`.

**E2. A deleted document is never still searchable** (2026-09-28).
`DocumentService.delete_document` takes the document out of every library
that holds it (`DocumentKnowledgeAdapter.libraries_holding`, the one answer,
also shown on the Documents page) BEFORE the tombstone; a library it cannot
leave stops the delete (`document_unindex_failed`, naming the library) so a
retry can finish it. It used to swallow the failure and archive a document
chat could still quote. The whole sequence, from reading the libraries to the
tombstone, holds the document's publication lock (an index published in
between survived the delete), and with no knowledge adapter (Knowledge failed
to start) a document the store records as indexed is not deleted (it was
tombstoned and stayed searchable; 2026-10-15).
`tests/test_document_library_membership.py`.

**E3. One PDF active-content policy, applied only inside the worker**
(2026-10-13, F01). `documents/pdf_policy.assert_pdf_safe` walks the object
graph (not page streams or extracted text), bounded at 100,000 objects and
depth 128, and refuses script (JavaScript, XFA), actions that run, open,
submit or fetch outside the document (Launch, remote GoTo, SubmitForm,
ImportData, renditions, rich media, sound, movie), embedded files (a bare
`/EF` without `/Type` too) and links that are not absolute http, https or
mailto (`file:`, UNC and relative targets leak credentials when opened).
A `/GoTo` or `/Thread` with an `/F` file specification is a remote go-to by
another spelling and is refused too (Q04 FIND-01, 2026-10-15: the independent
review found it passed as navigation). Action and key names are matched
case-insensitively (OBS-02), as the sniffer's regex is. Navigation inside the
document and web links stay. A graph it cannot read, or
one past its budget, is refused, never passed. It runs on intake (`sniff`), on
every mutation input and output (redaction opened its source before the
policy until 2026-10-13) and on every PDF Kazma renders (LibreOffice,
WeasyPrint, ReportLab), always in the worker. No PDF library opens a PDF in
the server process: the document intent handler reads through
`DocumentService` (a removed pipeline used to fall back to fitz exactly
when the worker had refused a file), and structural preflight and
local salvage run in the worker too. Gates:
`tests/test_pdf_openers_stay_in_workers.py` (any PDF-library opener outside
the worker-side modules fails, except declared checks of a PDF Kazma itself
just generated; the old reader as its control), `tests/test_document_pdf_policy.py`
(real inert PDFs: each denial, navigation and web links kept, shared indirect
`/URI`, the call-site matrix through the real worker),
`tests/test_document_worker_preflight.py`.

