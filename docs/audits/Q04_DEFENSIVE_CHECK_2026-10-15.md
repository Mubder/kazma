# Q04 defensive check: the review pack's claims against the code

2026-10-15, at the owner's request, before the independent review.

**This is not Q04.** Q04 needs a review by someone other than the authors
("never substitute self-review"). This check was made by the team that wrote
the code and the claims, so it shares their blind spots. It compared each claim
in `docs/audits/Q04_DOCUMENTS_SECURITY_REVIEW_PACK_2026-10-15.md` (section 4)
with the code that enforces it, ran the tests that hold it, and ran the
container profile on real Docker. It attempted no attack. Q04 stays open.

## How

- Three readers, each given five claims, the pack and AGENTS.md section 19,
  read the enforcing code and the named tests and reported where the claim
  said more than the code did, or no test held a part of it. Read only.
- Every reported gap was checked against the code before anything changed.
- Each fix landed with a behavioural test that the old code fails (run on the
  old code to confirm), and a gate where the gap was a class.
- Runs:
  - the claims' tests: 373 passed at `83a89b4f`, before the fixes;
  - the touched test files after the fixes;
  - on real Docker (Docker Desktop, Windows 11), with the worker image built
    from the fixed code (`kazma-document-worker:src-1c924e2af486b5cf`):
    - attestation verified, no problems: uid and gid 10001, an empty bounding
      set;
    - the qualification suites: 144 passed;
    - the compose-shaped topology check: a wrong token refused, no job
      container left, the broker stopped cleanly.

## Results

| Claim | Verdict before | What changed |
|---|---|---|
| C1 PDF policy | Holds | Tests: every action type and shape of the policy now has a case; the default budgets are asserted |
| C2 No PDF opened in the server | Holds, narrower than worded | Wording: three declared checks of Kazma's own generated PDFs |
| C3 Container policy | Holds, attestation looser than worded | **Fixed:** the attestation checks the exact uid and gid (any non-root uid passed) and the capability bounding set (read, never checked) |
| C4 No fallback to `process` | Holds | Test citations corrected |
| C5 Broker socket, token, loopback | Partly | **Fixed:** a token file that cannot be restricted to the guard's account gets no secret and the broker does not start (it logged a warning and served the token); an existing token file is restricted again at every start (it was reused as found). Wording: socket scope, native versus compose |
| C6 Bundles | Holds | Wording: the output's digests are the worker's own |
| C7 Readiness and certification | Mismatch | **Fixed:** certification passed the container profile with an image whose code match was not reported, an older image or a fallback build; readiness flagged only a code mismatch, not an older or fallback image, though AGENTS.md said both |
| C8 Job reads | Holds, two gaps | **Fixed:** cancelling needed only read access (retry needed write); a caller with no name was answered "unknown" when the job did not exist, instead of refused |
| C9 Admission and body caps | Holds | **Gate:** FastAPI is asked which document routes it reads a body for (none); the source gate could not see a model or dict parameter |
| C10 Remote parse egress | Holds, one gap | **Fixed:** a refused or failed remote parse (no audit scope, an audit that could not be written, a provider error) was logged only at DEBUG; now a WARNING naming the error type. **Gate:** local salvage callers outside the worker pass `allow_local=False` |
| C11 Rendering and LibreOffice | Mismatch | **Fixed:** the legacy Office parser (.doc, .xls, .ppt) ran LibreOffice with `subprocess.run(timeout=)` itself, so a timeout waited for the pipes LibreOffice's children held; it goes through `run_soffice_cli` now. **Gate:** no product module that names LibreOffice starts a process itself (two probes of other programs declared) |
| C12 Document text fenced | **Mismatch** | **Fixed:** the document intent handler and the document pipeline put a document's text into the prompt that asks the model to reorganize it, unfenced (no tools were offered, but planted text could steer the generated document) |
| C13 Append-only audit | Holds on both backends | Wording: against the application's own writes; a database owner and Postgres TRUNCATE are outside it. Test citations corrected |
| C14 Deleted means not searchable | **Mismatch** | **Fixed:** with the Knowledge Library unavailable (no adapter), delete tombstoned a document the store records as indexed, which stayed searchable; and delete did not hold the document's publication lock from reading its libraries to the tombstone, so an index published in between survived |

## The fixes and their tests

| Fix | Code | Test (fails on the old code) |
|---|---|---|
| Document text fenced in the intent handler and pipeline | `agent/intent/handlers/document.py`, `agent/pipelines/document.py` | `tests/test_document_text_fenced_to_model.py` |
| Delete refuses without Knowledge while indexed; one lock to the tombstone | `documents/service.py` | `tests/test_document_library_membership.py` (two new tests) |
| Legacy Office parser through the bounded runner | `documents/parsers/ooxml.py`, `documents/binaries.py` (`executable=`) | `tests/test_soffice_call_bounded.py` (the parser test, and the gate) |
| Token restricted at every start, never written unrestricted | `scripts/service/kazma_guard.py` (`_ensure_token`) | `tests/test_guard_document_broker.py` (two new tests) |
| Exact uid and gid, bounding set attested | `documents/worker_broker.py` (`_check_attestation`) | `tests/test_document_worker_container.py` (new cases) |
| Certification needs an image known to run this code | `documents/certification.py` | `tests/test_document_certification_honesty.py` |
| Older or fallback image is `document_worker_image_outdated` | `documents/ingestion.py` (`readiness`) | `tests/test_document_worker_container.py` |
| Cancel needs write; a nameless caller is refused first | `documents/ingestion.py` | `tests/test_document_job_read_matrix.py` |
| A remote parse that does not run is a WARNING | `documents/extract_salvage.py` | `tests/test_extract_salvage.py` |

## What this does not claim

- That the container profile resists a determined attacker: nothing was
  attacked. That is what the independent review is for.
- Anything about the `process` profile, kernel escapes, ClamAV, or remote
  providers (out of scope, pack section 3).
- That every gap was found: the readers were told which claims to check and
  shared the authors' framing of them.
