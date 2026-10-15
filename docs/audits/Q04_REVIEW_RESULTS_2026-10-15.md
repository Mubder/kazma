# Q04 independent review: results

2026-10-15. An independent reviewer (no Kazma authorship, no owner
credentials or infrastructure used) reviewed the Documents security profile
against the pack `docs/audits/Q04_DOCUMENTS_SECURITY_REVIEW_PACK_2026-10-15.md`.
Their report and these results are the Q04 evidence
(`docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md`, decision D4).

**Status: CLOSED 2026-10-15.** The reviewer retested the fix commit
`7a6a98b9` and confirmed FIND-01 resolved, with OBS-02 and OBS-03 verified
and their old-code-fails controls reproduced; no regression and no
over-blocking (section "Retest" below). Independent review done, its one
finding fixed and confirmed.

## Scope the reviewer tested

Commit `5cafcd5b` on Windows 11, Docker Desktop 29.8.2 (Linux containers,
cgroup v2), the worker image built from that checkout. They read every
enforcing file and the tests, re-tested each claim with inert fixtures (no
external hosts; crafted PDFs; `--network none` containers), ran the claim
suites and the real-Docker container suites, and checked the broker argv,
attestation, bundles, audit, rendering, fencing and delete. Out of scope as
the pack states: the live install, kazma.ai, third parties, container-to-kernel
escapes, the `process` profile as a hostile defence, ClamAV internals,
multi-user installs; and not run: Linux/systemd guard supervision, Postgres
multi-replica, the 48-hour soak, sustained load.

## Verdicts

Thirteen of the fourteen claims held (C2, C3, C5 with wording caveats the
reviewer accepted as documented). One did not:

**FIND-01 (Medium, CVSS 6.5), breaks C1 — fixed.** The PDF active-content
policy refused the explicit remote actions (`/GoToR`, `/GoToE`, `/Launch`)
but allowed a `/GoTo` or `/Thread` action carrying an `/F` file
specification, which is a remote go-to by another spelling: a viewer that
opens the stored document resolves the named file or UNC share (an NTLM
credential leak or phishing). All three enforcement points (intake, mutation,
render) shared the gap, because they share `assert_pdf_safe`.

Fixed in `documents/pdf_policy.py`: a `/GoTo` or `/Thread` with an `/F`
(string, filespec dict, or indirect) is refused. Tests
(`tests/test_document_pdf_policy.py`, each failing on the old policy): the
three `/F` shapes and a UNC target refused through the policy and the real
mutation worker; plain in-document `/GoTo` and `/Thread` still parse; an
indirect filespec refused.

## Observations (not claim breaks)

- **OBS-02 (defence in depth) — done.** Action and key names were matched
  case-sensitively, so a wrong-case `/JAVASCRIPT` was not matched. PDF names
  are case-sensitive (so most viewers ignore the wrong case) and pypdf
  already decodes the genuinely dangerous `#`-escape form, so this was
  defence in depth; it is now matched case-insensitively, as the sniffer's
  regex is. Test added.
- **OBS-03 (gate robustness) — done.** The C2 opener gate matched fixed
  `module.attr` spellings, so a future out-of-worker opener written as an
  aliased import or an attribute call (`pypdf.PdfReader`, `fitz.Document`)
  could pass. The reviewer found no such product bypass today. The gate now
  resolves each module's imports; a control locks in the alias and attribute
  shapes.
- **OBS-04 (inherent limit) — accepted, no change.** Attestation judges facts
  the container reports about itself, so a malicious image could forge good
  facts. This is operator trust (the operator builds the image), already named
  in pack section 6 and the threat model. Readiness `image_code_matches` /
  `image_current` compare labels, not runtime bytes.

## After the fix

The fix ships at the commit recorded below. The reviewer will re-run the
three PoCs (must refuse), the plain-`/GoTo` control (must still parse),
`tests/test_document_pdf_policy.py` and the certification hostile corpus on
that commit. On their confirmation, Q04 closes; until then it stays open.

Fix commit: `7a6a98b9` (see `CHANGELOG.md`, the Q04 FIND-01 entry).

## Retest (2026-10-15, commit `7a6a98b9`)

The reviewer re-ran their pre-fix proof-of-concept PDFs and the repository
tests on the fix commit, on their own machine, clean tree, nothing published:

- The three remote-file PoCs (`/GoTo` + `/F` string, `/GoTo` + `/F` filespec
  dict, `/Thread` + `/F`) now **refuse** with "PDF references a remote file
  (a GoTo or Thread action with a file specification)"; they were allowed on
  `5cafcd5b`.
- The negative controls still **parse**: plain `/GoTo`, plain `/Thread`, a
  blank PDF, navigation with an `https` link, `mailto:`, a leading-space
  `https`. No over-blocking.
- Every other hostile shape (JS, Launch, EF, XFA, SubmitForm, Rendition, the
  non-web URI schemes, hex `#`-escape names, `/Next` chains, `/AA`, indirect
  refs, the structural budget on deep nesting, a proper compressed-object
  stream, incremental update) stays refused or fail-closed, on both commits.
- `tests/test_document_pdf_policy.py` + `tests/test_pdf_openers_stay_in_workers.py`:
  **65 passed** (re-run here on `7a6a98b9` to confirm).
- The three new policy tests and the opener-gate alias/attribute cases were
  reproduced failing on pristine `5cafcd5b` (genuine regression tests).
- OBS-02: wrong-case `/JAVASCRIPT`, `/jAvAsCrIpT`, `/js` now refused (allowed
  on `5cafcd5b`). OBS-03: the four alias/attribute opener shapes now found
  by the gate (missed on `5cafcd5b`).

Reviewer note recorded for a future touch of the policy: `/S` is read as the
exact key while `/F` is matched case-insensitively over keys. That is the
right call -- PDF dict keys are case-sensitive per spec, so a lowercase `/s`
or `/f` is inert in a real viewer -- and is worth one comment line if the
code is changed again.

**Verdict:** FIND-01 fixed; OBS-02 and OBS-03 addressed and verified; no
regression. Q04 closed on commit `7a6a98b9`.
