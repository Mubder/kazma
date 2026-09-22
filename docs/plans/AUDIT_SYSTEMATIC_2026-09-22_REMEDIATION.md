# Systematic audit remediation

Objective: resolve every finding in `docs/audits/AUDIT_SYSTEMATIC_2026-09-22.md`
with production-grade implementations and requirement-specific evidence.
Starting checkout: `147c1989`. No live install changes or server restarts.

## Requirements and completion ledger

All items remain open until behavior and relevant regression checks prove them.

| ID | Requirement | Evidence required | Status |
|---|---|---|---|
| B1 | Truthful recovery verdicts | Missing verifier is UNVERIFIED, corruption FAIL, verified archive PASS; CLI and alerts agree | Implemented; targeted tests passed, final integration pending |
| B2 | IPv6 and exact-origin CSRF | Real requests across IPv4/IPv6, proxy public origin, scheme/port mismatch | Implemented; targeted tests passed, final integration pending |
| B3 | Preserve pinned HTTP authority | Transport tests for nonstandard ports, IPv6 and SNI | Implemented; 12 transport/Wave 8 tests passed |
| B4 | Exact file cache freshness | Same-size/timestamp edits anywhere in large files detected | Implemented; full-file digest, large-file gap test passed |
| G1 | Durable offsite PG delivery | Per-destination persisted retries survive restart; no regenerated dump; alerts and retention cooperate | Implemented; failed copy enqueues native_pg_offsite and returns failure until the copy works |
| G2 | Validate shipped installation | Clean installed-wheel smoke, shared DB contracts, all-package coverage, locked-dependency CI | Implemented for the wheel and the lockfile: CI job `shipped-install` exports `uv.lock` with `--frozen`, builds the wheel, and imports all six packages from a clean install. Postgres stays the verified 17-file tripwire; four SQLite-shaped files stay out on purpose |
| U1 | Effective TUI cost control | Settings-to-breaker behavioral tests with explicit precedence | Implemented; env kill switch, then cost.breaker_enabled, then max_cost |
| U2 | Working skill-review workflow | Current paths, root install, real async validator and failing negative fixture | Implemented; 22 native manifests pass SkillValidator, missing manifest is rejected |
| D1 | Accurate memory OFF guidance | Backend status fixtures drive UI assertions | Implemented; OFF text no longer claims a legacy RRF reader |
| D2 | Honest dependency extras | Explicit base/optional contract, installation validation | Implemented; tui and document extras are documented aliases of base dependencies and a test locks that |
| W1 | Browser network containment | Shared enforcement for read_url, knowledge ingestion and browser skill; private redirects/subrequests/rebinding blocked | Implemented for those three entry points via one route guard. Chromium still resolves allowed public hosts itself |
| W2 | Exact browser origin trust | Cross-port/scheme rejection and explicit additional origin configuration | Implemented with shared CORS/CSRF origin policy; integration pending |
| W3 | Nonblocking file validation | Controlled slow I/O does not stall loop; concurrent mutation remains safe | Implemented; stamp runs in a worker thread and a slow stamp still lets the loop tick |
| W4 | Uniform package quality gates | Shared package inventory; all packages lint/import/security/coverage; missing Any imports fixed | Implemented for the Ruff undefined-name gate and coverage sources. Import and security jobs were already repo-wide |
| R1 | Isolated recovery rehearsal | Disposable separate PostgreSQL environment, application/data verification; existing live-server script remains manual | Implemented: schema-only restore of the newest live dump into a disposable Postgres on 127.0.0.1:55432 rebuilt 20 tables, 47 indexes, and 1 extension, then dropped the scratch database. Not scheduled |
| V1 | Final integrated validation | Python/JS checks, full supported test runner, requirements-to-evidence completion audit | JS syntax check passed. fast_test reported 9758 passed, 25 skipped, and 2 failures in the browser-boot fakes, fixed by installing egress only when the page has a context |

## Execution constraints

- Preserve HITL, tenant isolation, workspace containment, and all AGENTS.md invariants.
- Browser policy must cover every connection; URL-name filtering alone is insufficient.
- Keep scheduled production recovery drills read-only. Never schedule the existing
  `scripts/restore_rehearsal.py` against the configured live PostgreSQL server.
- Retain the original audit as baseline evidence. Record implementation and test
  results here as work progresses; no completion claims based on intent alone.

## Evidence log

- Recovery: 82 passed, 1 skipped across the seven recovery test files including
  `test_restore_verification_verdict.py`. The skip requires `pg_restore` on PATH;
  missing-tool verdicts themselves are exercised deterministically. A preexisting
  CLI test was isolated from ambient backup discovery rather than reading local
  backup files while asserting fixture results.
- Authority pinning: 12 passed (`test_ssrf_pin_authority.py`, `test_audit_wave8.py`).
- Exact origins: 28 passed (`test_browser_origin_policy.py`, `test_csrf.py`). All
  69 tests in `test_ssrf_cors.py` passed in the broader run; that run also found
  three new-test failures subsequently resolved and rechecked in the 28-test run.
  IPv6 tests send real Host headers through an in-memory ASGI app because this
  installed Starlette TestClient cannot parse an IPv6 transport base URL.
- No live server was started or restarted. Existing live-server restore rehearsal
  remains manual. `--isolated` refuses a rehearsal URL that matches the live database.
- Follow-through: 10 new checks plus the file-read cache file passed. Browser
  requests to `127.0.0.1` and `[::1]` abort before `route.continue_`. A failed
  restic copy of an existing dump is queued as `native_pg_offsite` and stays
  failed until that copy succeeds. Ruff's undefined-name gate is clean across
  all six packages. 22 native skill manifests pass `SkillValidator`.
