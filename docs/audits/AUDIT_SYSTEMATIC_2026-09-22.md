## 1. Executive Summary

**Overall health: High Risk for deployments that browse untrusted websites or depend on unattended disaster recovery.** Kazma has substantial defensive architecture and regression coverage, but browser recovery bypasses the direct scraper's network restrictions, and backup status can overstate verification and offsite protection. No Critical-severity vulnerability was established in this review.

Audited checkout: `147c1989ced2d75f4ff559e53a4432bc03de09c0`, 2026-09-22. Scope included all six product packages, dependency/build configuration, CI workflows, architecture documentation, and selected regression suites. Review combined repository-wide searches with detailed tracing of security, recovery, settings, transport and file-reading paths; it was not a line-by-line certification of every module.

Top three risks requiring immediate intervention:

1. **Browser recovery has no private-network request enforcement.** A public page reached by the fallback Chromium browser can redirect or initiate requests to internal services.
2. **Unverified PostgreSQL archives can receive PASS.** Missing `pg_restore` turns mandatory verification into successful checks, including for a deliberately invalid archive.
3. **Failed offsite PostgreSQL copies do not fail or separately retry the backup job.** A successful local dump masks failure of its configured restic destination.

Validation: **187 tests passed, one skipped**, across 14 selected test files. The skipped test requires `pg_restore` on PATH. Independent harmless probes reproduced the IPv6 rejection, lost Host port, false archive PASS, sampled-cache collision, and successful queue status after simulated offsite failure. Ruff's undefined-name scan over all six package trees found two annotation references to missing `Any` imports.

The old MCP graph-approval bypass and messages-only replay finding are **not current findings**: this checkout uses explicit MCP approval/allowlisting and the shared full-state snapshot helper. Their targeted regression suites passed. Import/static/render boundary tests also passed.

Limits: no server was started or restarted, no live backup restore or external exploit was performed, and no application source was changed. Full-suite, browser end-to-end, live PostgreSQL, live branch-protection, and current dependency-advisory checks were not performed. Dependency findings below concern repository configuration, not a claimed CVE inventory. Historical audit conclusions were not treated as current evidence without checking implementation.

## 2. Detailed Audit Findings

### 🐛 1. Bugs & Functional Flaws

- **[Severity: High]** `[kazma-core/kazma_core/backup/restore_drill.py:379,486]` - *Description*: Missing PostgreSQL restore tooling is recorded as success. `_check_pg_dump()` accepts the five-byte `PGDMP` header and adds a successful `postgres:toc` check when `resolve_pg_restore()` fails; `_check_pg_data_section()` likewise adds a successful `postgres:data` check. `DrillResult` therefore cannot distinguish verified recovery material from skipped verification. **Reproduction:** an isolated file containing `PGDMPgarbage`, with the resolver mocked unavailable, produced `PASS: 3/3 checks passed`. This proves false success of these checks, not that every complete backup will pass. `tests/test_restore_drill.py::test_a_host_without_pg_restore_still_passes` explicitly preserves the behavior. **Fix:** introduce passed/failed/unverified states; an enabled PostgreSQL backup must not receive an overall verified PASS when its required archive checks cannot run. Update the existing test contract and operator alerting.

- **[Severity: Medium]** `[kazma-ui/kazma_ui/csrf.py:38]` - *Description*: `_host_of()` manually splits the authority at `:`, so the IPv6 origin `http://[::1]:9090` becomes `[` while Starlette reports the request hostname as `::1`. Legitimate browser API mutations over IPv6 return 403. The forwarded-host parser repeats the mistake. **Reproduction:** a real Starlette request with matching Host and Origin `[::1]:9090` returned 403. **Fix:** parse with `urlsplit().hostname` and normalized origin tuples, including bracketed IPv6 and explicit/default port tests.

- **[Severity: Medium]** `[kazma-core/kazma_core/security/ssrf_pin.py:40]` - *Description*: IP pinning overwrites the Host header with the hostname alone. For `https://example.com:8443/page`, the connection correctly retains port 8443 but the transmitted authority becomes `Host: example.com`. Port-sensitive virtual hosts, reverse proxies and applications can select the wrong resource or construct incorrect redirects. **Reproduction:** intercepting the parent transport showed URL `https://93.184.216.34:8443/page` with `Host=example.com`. **Fix:** preserve the original request Host authority, including port and IPv6 brackets, while keeping SNI as the original hostname. Add transport-level tests rather than only asserting that a pin transport exists.

- **[Severity: Low]** `[kazma-core/kazma_core/tools/file_read.py:192,260]` - *Description*: Files larger than 1 MiB are validated with eight sampled windows. An equal-length modification outside those windows that preserves the timestamp leaves the cache stamp unchanged; the agent can then receive stale text explicitly described as identical to the current file. **Reproduction:** changing byte 100,000 of a 2 MiB file and restoring its nanosecond timestamp left `_stat_stamp()` unchanged. This is a constrained, already-documented residual, not a claim that ordinary writes are generally missed. **Fix:** hash the returned byte range or complete content when correctness requires it, or bypass deduplication for verification reads. Do not claim exact freshness from a sampled fingerprint.

### 🕳️ 2. Architectural & Implementation Gaps

- **[Impact: High]** `[kazma-core/kazma_core/memory/worker_bootstrap.py:1117,1178]` - *Description*: `_snapshot_pg_to_restic()` logs unsuccessful `backup()` results and returns no status. `_handle_native_pg_backup()` then reports completion and returns `True`, so the durable queue cannot retry a failed configured offsite copy. The failure branch itself does not issue an ops alert. **Reproduction:** with dump creation mocked successful and restic mocked to return `ok=False`, the actual handler attempted the copy once and returned `True`. The local dump remains valid; the missing guarantee is offsite delivery. **Fix:** represent dump creation and replication as separate durable tasks or return structured per-destination results; retry failed destinations with bounded backoff and use the existing ops-alert channel. Do not repeatedly regenerate the dump just to retry transmission.

- **[Impact: Medium]** `[.github/workflows/ci.yml:76,190; .github/workflows/release.yml:99; pyproject.toml:209]` - *Description*: Release and backend validation do not establish the full advertised installation contract. CI installs editable source; release builds a wheel without subsequently installing that wheel into a clean environment and validating packaged entry points/assets. PostgreSQL coverage is a selected 17-file list, not shared backend-contract parity. Coverage configuration includes core and gateway only, leaving UI, CLI, TUI and skills outside that configured measurement. These are validation gaps, not evidence that every omitted path is broken. **Fix:** add installed-wheel import/asset/CLI smoke tests, parameterized SQLite/PostgreSQL store contracts, and coverage reporting for all product packages. Test the locked dependency set used for the release SBOM as well as any intentionally supported floating-dependency install.

### 🔌 3. Un-wired Components

- **[Impact: Medium]** `[kazma-tui/kazma_tui/settings_panel.py:92,138; kazma-core/kazma_core/cost_breaker.py:34,147]` - *Description*: The TUI renders and saves `cost.breaker_enabled`, but the cost breaker does not consume it. Runtime control reads `safety.max_cost`, `safety.hard_max_cost`, `safety.silence_window`, and `KAZMA_DISABLE_COST_BREAKER`; `should_halt()` never checks the TUI flag. The UI can announce a saved change without changing breaker behavior. Repository searches found the flag only in the TUI definition; a mocked-store probe confirmed the constructor's actual read keys. **Fix:** remove the unsupported toggle or wire it to one documented runtime setting with explicit precedence and a behavioral test that toggles through the settings path and evaluates the breaker.

- **[Impact: High]** `[.github/workflows/skill-review.yml:5,20,43,75,94]` - *Description*: The skill-review workflow is disconnected from the current monorepo. Its path filters target `skills/*/...`, so changes to shipped native implementations under `kazma-skills/kazma_skills/native/` do not trigger it. When triggered, it attempts `pip install -e ./kazma-core`, but that directory has neither `pyproject.toml` nor `setup.py`. Its security step imports `SecurityValidator`, which is absent; the module exports `SkillValidator` with an async `validate()` interface. **Fix:** update the trigger and manifest discovery for each supported skill layout, install the root project, and call the actual validator API. Validate the workflow with a deliberately invalid fixture. Other CI checks still exist; this finding is specifically about the dedicated skill-review guarantee.

### 👻 4. Dead Code & Obsolete Assets

- **[Recommendation: Refactor]** `[kazma-ui/kazma_ui/static/js/memory_console.js:695]` - *Description*: The memory console's inactive-V2 branch still describes a legacy RRF reader and dual-write fallback. `memory/config.py:488` explicitly states that the legacy reader was removed and disabling V2 disables injection/post-turn consolidation. This is obsolete product guidance in a reachable UI branch, not an available fallback. **Fix:** render the current OFF/degraded semantics and lock the explanatory text to backend status fixtures. Do not restore the retired memory stack to match the stale copy.

- **[Recommendation: Refactor]** `[pyproject.toml:21,40,78,99]` - *Description*: TUI and document extras repeat dependencies already required by the base distribution, including Textual and document libraries. Consequently omitting those extras does not provide the lightweight installation their optional grouping suggests. Pip normally deduplicates distributions, so this is not duplicate installation of identical packages. **Fix:** either make the capabilities genuinely optional with graceful import boundaries or document them as base requirements and retain extras only as explicit compatibility aliases.

### ⚠️ 5. Weaknesses & Technical Debt

- **[Category: Security]** `[kazma-core/kazma_core/tools/read_url.py:530,797]` - *Description*: **High severity: browser recovery bypasses SSRF enforcement.** The direct HTTP path validates destinations, pins DNS results and checks peer addresses. `_recover_hard_page()` can instead invoke Chromium, whose implementation calls `page.goto()` and lets the page execute without a request route, private-address checks, redirect validation or a network containment policy in this code path. A validated public page can redirect to a private address or cause browser subrequests to internal services; initial URL validation does not constrain those later requests. Preconditions are an installed Playwright browser, entry into the recovery path, and network reachability from the browser host. This is a source-confirmed enforcement gap; no live internal service was contacted. **Fix:** apply a shared browser egress policy to every navigation and subrequest and enforce private-network denial at the browser process/proxy boundary, including DNS rebinding, IPv6 and redirects. Disable this recovery backend until equivalent restrictions are available. The Chromium launch also uses `--no-sandbox`, increasing the importance of process isolation.

- **[Category: Security]** `[kazma-ui/kazma_ui/csrf.py:73; kazma-ui/kazma_ui/app.py:476]` - *Description*: **Medium severity, conditional on another service sharing the host:** CSRF compares only hostnames, dropping scheme and port, while default credentialed CORS explicitly trusts several development ports. An unrelated service at an allowed origin such as `http://localhost:8000` is therefore trusted relative to Kazma at `http://localhost:9090`. A real-request probe confirmed that the cross-port POST passes the CSRF middleware. This is not proof of arbitrary internet-origin access; exploitation requires attacker-controlled content on the same host/origin surface and usable ambient authentication. **Fix:** compare exact normalized browser origins, derive proxy-facing origins from trusted configuration, and make additional development origins explicit opt-ins. Add cross-port/scheme tests that check actual mutation rejection, not merely browser response visibility.

- **[Category: Performance]** `[kazma-core/kazma_core/tools/file_read.py:260,289]` - *Description*: Cache validation performs synchronous file open/read/hash operations inside the async tool before the actual content read is offloaded. Small files are hashed in full; larger files cause eight seeks and approximately 512 KiB of reads even on a cache hit. Slow disks or network-mounted workspaces can stall the event loop serving other agent activity. This is a code-established blocking path; no production latency benchmark was performed. **Fix:** move stamp calculation and reading into the same bounded worker-thread operation, with before/after identity checks for concurrent mutation. Add a scheduling-responsiveness regression using controlled slow I/O.

- **[Category: Maintainability]** `[.github/workflows/ci.yml:51; kazma-tui/kazma_tui/app.py:100; kazma-skills/kazma_skills/native/git_github_manager/tools.py:563]` - *Description*: The undefined-name Ruff gate covers core, gateway and UI but omits CLI, TUI and skills. Running the equivalent scan over all six package trees found missing `Any` imports in TUI and native GitHub tools. These are local variable annotations and do not, by themselves, establish runtime crashes; they demonstrate inconsistent lint coverage. **Fix:** use one package inventory for lint, import, security and coverage jobs, correct the annotation imports, and add a test that prevents package omissions as the monorepo grows.

## 3. Prioritized Action Plan

1. **Immediate Fixes (Blockers / Security Risks)**
   - Contain or disable browser-based recovery until every browser request is subject to the network policy. Acceptance: a public-page redirect and a public-page subresource request to loopback/private IPv4 and IPv6 are blocked before connection.
   - Change restore verification to distinguish unavailable checks from successful checks. Acceptance: an invalid `PGDMP`-prefixed archive cannot receive verified PASS when `pg_restore` is absent.
   - Give offsite PostgreSQL replication its own durable result and retry path. Acceptance: a failed configured upload remains failed/pending and triggers the existing ops channel while preserving the successful local dump.
   - Tighten the origin boundary where multiple services share a host; explicitly configure any development cross-origin access that is actually required.

2. **Short-term Refactoring (Wiring & Cleanup)**
   - Correct IPv6 origin parsing and preserve the original HTTP Host authority during pinning.
   - Repair the skill-review workflow against the current project layout and validator interface.
   - Wire or remove the ineffective TUI cost switch; replace the obsolete legacy-memory status copy.
   - Extend lint coverage to all packages. Add behavioral negative controls for the new findings; passing broad static gates did not detect them.

3. **Long-term Improvements (Architecture & Performance)**
   - Consolidate HTTP and browser egress policy and test every fallback backend under the same adversarial cases.
   - Establish scheduled isolated restore rehearsals with application-level checks, in addition to archive-readability drills. The existing `scripts/restore_rehearsal.py` is not invoked by the reviewed scheduler/workflows.
   - Add installed-wheel verification, backend contract parity, complete coverage reporting, and a locked-dependency CI lane consistent with release SBOM generation.
   - Move file hashing off the event loop and define exact cache freshness guarantees. Clarify the base-versus-optional dependency contract.

Verification commands executed from the repository root:

```powershell
& .venv/Scripts/python.exe -m pytest tests/test_mcp_hitl.py tests/test_mcp_classification_hardening.py tests/test_replay_command.py tests/test_file_read_cache_invalidation.py tests/test_csrf.py tests/test_audit_wave8.py tests/test_restore_drill.py tests/test_restore_drill_actually_runs.py tests/test_restore_drill_scheduled.py tests/test_config_store_close_race.py tests/test_turn_assets_ship.py -q --timeout=60
# 145 passed, 1 skipped

& .venv/Scripts/python.exe -m pytest tests/test_imports.py tests/test_static_gates.py tests/test_turn_render_boundary.py -q --timeout=60
# 42 passed

& .venv/Scripts/python.exe -m ruff check kazma-core kazma-ui kazma-cli kazma-tui kazma-skills kazma-gateway --select F821,F823,E9 --output-format concise
# Two F821 findings: missing Any imports in local annotations.
```

Additional interpreter probes used temporary files, isolated SQLite configuration where needed, real Starlette request objects, and mocked HTTP/backup dependencies. No credential values or real network destinations were used.
