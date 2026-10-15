# Q04 review pack: Documents security profile

Prepared 2026-10-15 against commit `83a89b4f` of
[`Mubder/kazma`](https://github.com/Mubder/kazma) (public), for an independent
reviewer named by the owner (decision D4 in
`docs/plans/AUDIT_REMEDIATION_PROGRAM_2026-10-13.md`). Revised the same day
after the authors' own defensive check of these claims, which found
gaps in eight of its fourteen claims and fixed them
(`docs/audits/Q04_DEFENSIVE_CHECK_2026-10-15.md`); section 4 states
the claims as the code now holds them.

This pack scopes the review and points at the evidence. It is not a review
and claims nothing: Q04 in `docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md`
closes only on an external review with its scope, results and the fixes it
led to -- "never substitute self-review". Everything below was written by the
team that built the code, so treat each claim as something to break.

## 1. What we ask

1. Test whether the claims in section 4 hold for Kazma's **container** document
   worker profile on a single-operator install, on the commit you pin.
2. Report anything that breaks a claim, and anything section 6 should have
   listed and did not.
3. Deliver the items in section 8.

Depth, method and time are the reviewer's call. Section 6 is where we think
the risk is, not a limit.

## 2. The system in one page

Kazma is an AI agent one operator runs for themselves (`docs/THREAT_MODEL.md`,
"one operator per install"). Documents reach it from the web page, the chat
apps (Telegram, Discord, Slack), the agent's own tools, and email or web
downloads the agent fetches. Any of them can be hostile.

- **Public boundary:** `DocumentIngestionService`
  (`kazma-core/kazma_core/documents/ingestion.py`) behind the Web API
  (`kazma-ui/kazma_ui/documents_api.py`), the agent's document tools, the chat
  apps' `/documents` command and the TUI. It owns ACL, admission, the durable
  job queue, audit and indexing.
- **Execution boundary:** `DocumentService` sniffs, parses, OCRs and renders
  inside a worker, never in the server process.
- **Two worker profiles.** `process` (the default): a scrubbed child process
  with OS resource limits (a Windows Job Object or `setrlimit`), and no
  filesystem or network jail. It is **not** claimed safe for hostile
  documents. `container`: one container per job, started by a broker
  (`documents/worker_broker.py`) that is the only holder of the Docker socket.
  Kazma's guard (`scripts/service/kazma_guard.py`) supervises the broker on a
  native install. The review is about this profile.
- **Images:** `deploy/document-worker/Dockerfile` (the worker, with the neural
  OCR models from the hash-locked `deploy/document-worker/ocr-models.txt`
  stage) and `deploy/document-broker/Dockerfile` (the broker, for a
  compose-style deployment). The worker image's tag is a digest of its build
  inputs (`documents/worker_image.py`).

The full threat model is `docs/docs/security/document-security.md`; the
operations guide is `docs/docs/ops/document-processing.md`; the rules each
part must keep are AGENTS.md §19 (A to K).

## 3. Scope

| In scope | Where |
|---|---|
| Intake: admission, budgets, body caps, upload precheck | `documents_api.py`, `documents/admission.py`, `documents/ingestion.py` |
| Content sniffing and the PDF active-content policy | `documents/sniff.py`, `documents/pdf_policy.py` |
| The container profile: broker, per-job policy, attestation, bundles, readiness | `documents/worker_container.py`, `worker_broker.py`, `worker_bundle.py`, `worker_entry.py`, `worker_image.py`, `broker_supervision.py`, both Dockerfiles |
| The guard's supervision of the broker (token file, port, environment) | `scripts/service/kazma_guard.py` (`DocumentBroker`) |
| Worker output handed back to Kazma (it is untrusted) | `documents/worker_bundle.py`, the parser result path in `documents/service.py` |
| Rendering engines (HTML to PDF, LibreOffice) | `documents/renderers/`, `documents/binaries.py` |
| Third-party parse egress (off by default) | `documents/extract_salvage.py` |
| Job and document access control | `documents/ingestion.py`, `documents/principal.py` |
| Prompt fencing of document text | `kazma_core/safety/prompt_fence.py`, the document tools |
| Audit and storage integrity | `documents/audit.py`, `documents/storage.py`, the Postgres audit trigger |
| Malware scan wiring (not ClamAV itself) | `documents/malware.py` |

**Out of scope** (recorded limits; see section 7):

- The `process` profile as a defence against hostile documents (it is
  documented as unqualified; a finding that it confines nothing is known).
- Escapes from the container to its kernel: containers share the host or VM
  kernel, and no gVisor or Kata runtime is qualified (decision D5).
- ClamAV's own handling of a file (it runs on the host).
- What LlamaParse or Reducto do with bytes once an operator turns remote
  parsing on.
- Multi-user installs: every Documents surface refuses them (decision D1), so
  cross-tenant isolation is defence in depth, not a supported profile.

## 4. Claims to test

Each claim names where it is enforced and the test that holds it today. A
test passing is our evidence, not proof: please try to break the claim, not
the test.

| # | Claim | Enforced in | Held by |
|---|---|---|---|
| C1 | A PDF with script (JavaScript, XFA), actions that run, open, submit or fetch outside the document (Launch, remote GoTo, embedded GoTo, a `/GoTo` or `/Thread` carrying an `/F` file specification, SubmitForm, ImportData, renditions, rich media, sound, movie), embedded files (a bare `/EF` or a stream typed `/EmbeddedFile`) or a link whose target string is not an absolute http, https or mailto address is refused, on intake, on every mutation's input and output, and on every PDF the renderer worker produces. Action and key names are matched case-insensitively. The object walk is bounded (100,000 objects, depth 128); a graph it cannot read is refused. Kazma's fixed fidelity corpus is rendered in-process and is not checked | `documents/pdf_policy.py` (`assert_pdf_safe`), `sniff.py`, `mutation_worker.py`, `renderer_worker.py` | `tests/test_document_pdf_policy.py` |
| C2 | No PDF library opens a PDF in the server process; only the worker does -- except three declared checks of a PDF Kazma itself just generated. The gate resolves each module's imports, so an aliased import or an attribute call (`pypdf.PdfReader`, `fitz.Document`) is caught, not only the canonical spellings | worker-side modules only | `tests/test_pdf_openers_stay_in_workers.py` |
| C3 | Each job runs in its own container: no network, no mounts but Docker's own files (`/etc/hosts`, `/etc/resolv.conf`, `/etc/hostname`, `docker-init`) and `/proc`, `/dev`, `/sys`, read-only root, tmpfs scratch, cgroup limits, every capability dropped, uid and gid 10001. The broker attests this from inside the image before it serves (the exact uid and gid, an empty effective, permitted, ambient and bounding capability set, no_new_privs, seccomp, the interfaces, the mounts, the cgroup limits) and does not start without the image's capability report. The facts are the image's own report | `documents/worker_broker.py` (`_container_argv`, `_check_attestation`), `worker_entry.py` | `tests/test_document_worker_container.py`; real Docker: `tests/test_document_worker_container_docker.py` |
| C4 | The container profile never falls back to `process`; an invalid mode refuses every run; `security.execution.container_required` refuses the `process` profile, and an unreadable policy refuses too | `worker_container.py` (`worker_isolation_mode`, `process_profile_refusal`) | `tests/test_document_worker_container.py`, `tests/test_document_worker_profile.py` |
| C5 | For document jobs only the broker holds the Docker socket; the Kazma service never mounts it (on a native install Kazma's own account can reach Docker for other features, such as code execution). On a native install the broker listens on loopback and its token file is readable by the guard's account only: restricted again at every start, and a file that cannot be restricted gets no secret and stops the broker. The token is compared in constant time. The broker's environment is scrubbed (the guard's Telegram token never reaches it). The compose profile uses a unix socket and a compose secret instead | `worker_broker.py`, `kazma_guard.py` (`_ensure_token`, `_BROKER_ENV_KEEP`) | `tests/test_guard_document_broker.py`; real Docker: `tests/test_document_broker_guard_docker.py`, `scripts/document_broker_topology.py` |
| C6 | Inputs and outputs cross the boundary as digest-checked bundles (names from an allowlist, files created inside the run root, header, record, file and total size caps). The output's digests are declared by the worker, so they prove a consistent stream, not where it came from: Kazma treats the output as untrusted | `documents/worker_bundle.py`, `worker_container.py` | `tests/test_document_worker_container.py` |
| C7 | Readiness tells the truth: the image's own capability report answers the engine questions (never the host's packages); an image built from other document code, older than its tag or served as a fallback is `document_worker_image_outdated`; certification passes the container profile only with an image known to run this code, and never reports `process` as confined | `worker_container.py`, `documents/ingestion.py` (`readiness`), `documents/certification.py` | `tests/test_document_worker_container.py`, `tests/test_document_certification_honesty.py`, `tests/test_document_certification_phase10.py` |
| C8 | Every job read names its caller (a nameless one is refused, whether or not the job exists); a job the caller cannot read answers exactly as an unknown one (no existence oracle); cancelling or retrying needs write access; a replay by another actor is a conflict; the operations audit is admin-only at its Web route | `documents/ingestion.py`, `documents_api.py` | `tests/test_document_job_read_matrix.py`, `tests/test_document_access_hardening.py`, `tests/test_document_operator_principal.py` |
| C9 | An upload is refused before its body is read (declared length, writes off, free-space floor, queue, tenant and rate budgets), and every JSON body is read under a 2 MiB cap: FastAPI itself reads no document route's body | `documents/ingestion.py` (`precheck_upload`), `documents_api.py` (`_json_body`), `documents/admission.py` | `tests/test_document_upload_admission.py`, `tests/test_document_json_body_cap.py`, `tests/test_document_api_statuses.py` |
| C10 | Remote parsing is off by default and an API key is not consent; consent is read again before each provider attempt; a durable audit row exists before any upload; the client follows no redirects, uses no ambient proxy and caps answers at 32 MiB; a refused or failed remote parse is a WARNING; local salvage runs only in the parser worker | `documents/extract_salvage.py` | `tests/test_document_egress_hardening.py`, `tests/test_document_egress_pg.py`, `tests/test_extract_salvage.py`, `tests/test_document_worker_preflight.py` |
| C11 | WeasyPrint rendering fetches nothing outside `data:` URLs and the approved asset folder. Every LibreOffice call goes through one runner that kills its process tree on timeout and bounds the last wait (the tree kill uses psutil; the worker image has none, and there the job container's end ends the tree) | `documents/renderer_worker.py` (the fetcher), `documents/binaries.py` (`run_soffice_cli`) | `tests/test_weasyprint_renderer.py`, `tests/test_soffice_call_bounded.py` |
| C12 | Document text reaches a model inside an untrusted-data fence: chat attachments, the document tools, document search, Knowledge injection, and the document intent handler and pipeline that reorganize a document | the document tools, `agent/intent/handlers/document.py`, `agent/pipelines/document.py`, `safety/prompt_fence.py` | `tests/test_document_processor_security.py`, `tests/test_document_text_fenced_to_model.py` |
| C13 | The audit is append-only against the application's own writes: on SQLite and on Postgres, triggers refuse UPDATE and refuse DELETE outside the marked retention prune. Not covered: a database owner (who can drop a trigger) and, on Postgres, TRUNCATE | `documents/audit.py`, the `document_audit_guard` trigger | `tests/test_document_audit_pg.py` (Postgres), `tests/test_document_operations_phase9.py` (SQLite) |
| C14 | A deleted document is never still searchable: it leaves every library before its tombstone, holding the document's publication lock from reading its libraries to the tombstone; a library it cannot leave stops the delete, and so does an unavailable Knowledge Library while the store records the document as indexed | `documents/service.py` (`delete_document`) | `tests/test_document_library_membership.py` |

## 5. Setting up

Any machine with Docker Engine or Docker Desktop 29+ and Python 3.11 to 3.14.

```bash
git clone https://github.com/Mubder/kazma.git
cd kazma
git checkout <the commit you review>
pip install -e ".[test]" pymupdf
docker build -f deploy/document-worker/Dockerfile -t kazma-document-worker:review .
docker build -f deploy/document-broker/Dockerfile -t kazma-document-broker:review .
```

`python scripts/document_worker_image.py build` builds the worker image
under the tag a running install expects instead.

The checks CI runs for this profile (the "Document worker container profile
(real Docker)" job in `.github/workflows/ci.yml`, a required check on `main`):

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))" > broker-token
python -I kazma-core/kazma_core/documents/worker_broker.py --image kazma-document-worker:review --token-file broker-token --name review --attest-only
KAZMA_TEST_DOCUMENT_WORKER_IMAGE=kazma-document-worker:review python -m pytest tests/test_document_worker_container_docker.py tests/test_document_worker_container.py
python scripts/document_broker_topology.py kazma-document-worker:review kazma-document-broker:review
```

The claims' own tests (section 4) run without Docker, except the real-Docker
ones named there. The bounded certification smoke, with the committed hostile
corpus (`tests/fixtures/documents/hostile_manifest.json`), is
`python scripts/certify_documents.py`.

To see the profile as an operator does: run Kazma, choose the container
profile in Settings, Documents, and read `GET /api/documents/ops/readiness`
(`worker_isolation`). The steps are in `docs/docs/ops/document-processing.md`,
"Switch an install to the container profile".

## 6. Where we think the risk is

Suggestions, roughly by how much damage a hole would do:

1. **Leaving the container.** Network egress from a job (DNS included),
   writing outside its tmpfs, reading the host through anything the image or
   the run command left open, exhausting the host despite the cgroup limits
   (fork bombs, memory, disk through tmpfs), a container that outlives its job.
2. **The broker.** Who can reach the loopback port; forging or replaying a
   request; reading the token file as another local user; making the broker
   run an image or options other than its fixed policy; the attestation
   itself (can a crafted image pass it?).
3. **What comes back.** The worker's output is parsed by Kazma: malformed or
   hostile bundles, oversized results, paths in results, and text that tries
   to steer the model past its fence (C12).
4. **PDF policy coverage.** Actions and links the walk does not see (object
   streams, incremental updates, name trees, annotations reached indirectly,
   a graph built to exhaust the budget at exactly the limit).
5. **Office and HTML inputs.** External OOXML relationships, XXE, macros,
   compression and archive bombs, polyglots, a rendered HTML document naming
   remote resources.
6. **Access control.** Reading or cancelling another actor's jobs through ids,
   idempotency keys or the gateway and TUI surfaces; the operator principal's
   automatic grant (`grant_operator_access`).
7. **Admission.** Holding the intake open (slow bodies, unsized uploads),
   starving other tenants, filling the disk past the floor.
8. **Redaction.** Text that is still extractable after a redaction (the
   threat model's "Layer 5: Redaction verification").

## 7. Known limits (please report only if worse than stated)

- The `process` profile confines resources only: no filesystem or network
  isolation (threat model, Layer 3).
- Kernel escapes are out of scope; no gVisor or Kata runtime is qualified.
- The 48-hour Documents soak (Q03) is running at the time of writing (ends
  around 2026-10-16 20:20 UTC); its result goes into the tracker.
- Postgres multi-replica operation is not qualified (the readiness report says
  single-replica whenever SQLite metadata is in use).
- The guard's broker supervision has not been run under Linux and systemd (the
  POSIX code paths are unit-tested only); the container job itself runs on
  Linux in CI.
- ClamAV runs on the host, outside both profiles, on every upload.
- Redaction through the API or the agent's tools is privileged: the page's
  confirmation is not a server-side gate on those paths.
- The live install the owner runs is Windows 11 with Docker Desktop.

## 8. What to deliver, and how

- **Report privately**, by the channels in `SECURITY.md` (GitHub private
  vulnerability reporting, or email). The repository is public: please do not
  open public issues for findings.
- **A scope statement:** the commit, the platforms, what you tested and how,
  and what you did not test.
- **Each finding:** an id; a title; severity with a CVSS vector; the claim it
  breaks (C1 to C14) or "not covered"; the component and commit; a minimal
  reproduction (files and commands); the impact; and a suggested fix if you
  have one.
- **A retest** of each finding on the commit that fixes it.

How the results are recorded here: the owner adds
`docs/audits/Q04_REVIEW_RESULTS_<date>.md` (scope, findings, fixes, retests),
and Q04's row in the Documents tracker points at it. Each fix lands with a
behavioural test and a negative control (the old code failing it), as every
fix in this repository does. Q04 stays open until that file exists.
