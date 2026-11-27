<!-- AGENTS.md, piece 49-security-report. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 49. The security report measures what it names (`kazma_core/security/hardening.py`, 2026-10-02)

Live 2026-10-02, `GET /api/security/hardening` reported 4 of 8 checks
failed, 3 of them critical, and none of the failures was true. The 48
"secrets" and 576 "escalation vectors" came from a package cache inside the
install. The 202 "vulnerable dependencies" came from asking OSV about each
requirement's minimum version. The skills check said "no manifests" because
it looked for the wrong file name. The four checks that passed did so on a
keyword found anywhere: "sandbox" in any YAML file, "permission" in any
Python file, "https" in `kazma.yaml`, and an audit class that nothing wrote
to.

- **Each check reads the answer the product uses.** Product files come from
  `project_files.iter_project_files` (§10 E2), with tests, docs and `.env`
  left out. Credentials are judged by `security/secret_scan.find_credentials`,
  the one detector, which never returns a value. A known configuration name
  whose value is a method, not a secret (`password_encryption` set to
  `scram-sha-256` or `md5`), is not a finding; a password beside that line
  still is. MCP secrets come from
  `mcp_servers_store.servers_with_plaintext_secrets`, the list that
  `move_plaintext_secrets` empties. The scope guard is read through
  `mcp.manager.mcp_scope_guard_enabled`. Access and TLS come from
  `boot_guard.exposure` / `check_exposure_posture`, with
  `WEAK_SECRET_CHARS` in one place. Built-in skills are checked with
  `NativeSkillLoader.resolve_skill`/`problems()`, which `register_skill`
  also uses. Agent skills go through `catalog.activation_integrity`, the
  check activation applies, and `discovery.skill_files`. The audit check
  reads `hitl_gates.recorded_decision_count`. Shell and eval sites are
  checked against `_REVIEWED_SITES` (file, function, call, and the reason),
  and a test fails if an entry no longer matches the code.
- **A scan that did not run is never clean.** The dependency scan asks OSV
  about installed versions (`dependency_scanner.audit_installed`). It merges
  GHSA and PYSEC aliases, follows pages, and raises `DependencyQueryError`
  when OSV cannot be asked. A check that raises is reported as failed and
  does not hide the other seven. Each check's body runs in a worker thread,
  and the route is a read-only diagnostic.
- **Removed:** `SecurityAuditTrail` (written by nothing). The dead parts of
  the dependency scanner: multi-source queries without a version, a scan
  history database, `gh issue create`, and a cache kept forever. Also
  `fix_issues`, which wrote a `.env` into the install, and the
  `kazma_core.security` package's re-exports. `security_audit.db`,
  `security_scan.db` and `vuln_cache.json` are retired (`legacy`) in §37's
  registry.
- **A declared proxy is exposure** (`boot_guard.exposure`). Behind a tunnel
  the bind is loopback, so `KAZMA_AUTH_DISABLED` used to start cleanly with
  every `/api` route open to the internet. The boot guard now refuses it,
  and `KAZMA_DEV_WS_BYPASS`, when `KAZMA_TRUSTED_PROXIES` names a proxy. The
  middleware answers 503 to a proxied request while sign-in is off
  (`auth._arrived_through_a_proxy`), which also covers a proxy nobody
  declared.
- Gates: `tests/test_hardening.py` (each check's live failure shape and its
  keyword-era pass), `tests/test_secret_scan.py` (the old patterns as the
  negative control), `tests/test_dependency_scanner.py` (OSV faked with
  `httpx.MockTransport`; a silent-clean scanner as the control),
  `tests/test_security_report_sources.py`, and
  `tests/test_auth_disabled_behind_proxy.py` (the bind-only rule and an
  always-false proxy check as the controls).

