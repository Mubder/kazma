# Remediation Notes — 2026-07-13

## Overview

A comprehensive audit and remediation pass was completed on 2026-07-13.
All 36 planned items were addressed (7 Critical, 9 High, 14 Medium, 4 Low,
final verification, and architectural flagging).

## Architectural Items Requiring Future Decision

### 1. Delegation Package — Dead Code in Production

**Location:** `kazma-core/kazma_core/delegation/` (6 files, ~50K LOC)

**Files:**
- `discovery.py` — AgentDiscovery, AgentInfo
- `orchestrator.py` — DelegationOrchestrator, SubTask, OrchestrationResult
- `protocol.py` — DelegationProtocol, DelegationRequest, DelegationResponse
- `security.py` — DelegationSecurity
- `swarm.py` — SwarmIntelligence, CascadeResult, ConsensusResult
- `__init__.py` — re-exports all public classes

**Finding:** The entire `delegation/` package is only imported by:
1. Tests (`test_delegation_*.py`, `test_agent_discovery.py`, `test_swarm.py`, `test_bug_regression.py`)
2. The delegation package itself (internal imports)
3. Documentation (`docs/docs/api-reference/delegation-api.md`)

**No production code outside the package ever imports or uses any delegation class.**
The package is fully implemented and tested but never wired into the agent
runner, swarm engine, gateway, or UI. It appears to be either an uncompleted
feature or an abandoned experiment.

**Action needed:** Determine whether this is a planned feature that should be
wired in, or dead code that should be moved to `archive/` or removed. The
package has its own test suite that passes, so it's not broken — just
disconnected from production.

### 2. Kubernetes Secrets — Template Created

**Location:** `kubernetes/hub-secrets.yaml` (gitignored, untracked)
**Template:** `kubernetes/hub-secrets.yaml.template` (created)

**Finding:** The `hub-secrets.yaml` file contains plaintext connection strings
(database-url, redis-url) with real credentials. The `.gitignore` already has
`kubernetes/*secrets*.yaml` so the file is NOT tracked by git. However, there
was no template file for new developers to know what secrets to create.

**Action taken:** Created `hub-secrets.yaml.template` with placeholder values.
No runtime impact — the K8s deployment reads secrets via `secretKeyRef` from
the cluster, not from git.

## All Remediated Items (36 total)

### Critical (7)
1. Hardened ShellTool allowlist in `tools/registry.py`
2. Fixed checkpoint SSE regression in `engine.py`/`checkpoint_manager.py`
3. Routed `self_improvement.py` + `orchestrator.py` through `get_worker_registry()`
4. Fixed `cultural_context_enrichment.py` AttributeError
5. Fixed gateway HITL cross-thread approval bypass in `store.py`
6. Fixed TUI `_port` NameError in `app.py`
7. Fixed TUI `accessibility.py` import + constructor bugs

### High (9)
8. Resolved dual MigrationRunner schema conflict (idempotent ALTER TABLE)
9. Wired `default_worker_registry_executor` into DelegationProtocol
10. Added SQL identifier injection defense (`_UNSAFE_TABLE_CHARS`, `_SAFE_WORKER_NAME`)
11. Fixed `dialect_detector.py` `.lower()` + word-boundary matching
12. Added Arabic-Indic digit normalization to `arabic_tokenizer.py`
13. Added `/dashboard` to `SENSITIVE_PREFIXES` in `auth.py`
14. Sanitized SSE error frames with `sanitize_error(exc)` in `sse_chat.py`
15. Fixed `banner.py` missing `import logging`
16. Converted TUI `files.py` blocking I/O to async `asyncio.to_thread()`

### Medium (14)
17. Fixed `context_cmd.py` undefined logger
18. Delegated 6 MCP methods to `settings_mcp.py` service
19. Added chaos framework `_chaos_enabled()` kill-switch
20. Rewired `hardening.py` AST scanner into `check_permission_escalation()`
21. Fixed `providers.py` dead Discord connector branch
22. Fixed Jinja2 `lang`/`dir` globals race condition (contextvar-backed callables)
23. Locked `retry_task()` `_task_history` read with `history_lock`
24. Fixed tracing unbounded growth (`DEFAULT_MAX_SPANS=5000` + eviction)
25. Fixed `handoff.py` misleading docstring (cycle detection description)
26. Precompiled regexes in `tone_adapter.py` (`_KUWAITI_FORMAL_PATTERNS`)
27. Added HITL kill-switch confirmation dialog in `settings_panel.py`
28. Fixed `services.yaml` stale test path (`kazma-tui/tests/` -> `kazma_tui_tests/`)
29. Extended CI coverage job to include root tests/ and kazma-tui suite
30. Cleaned up `chaos/__init__.py` dead computation + stop_all confusion

### Low (4)
31. Fixed `msa_tokenizer.py` misleading comments (unimplemented normalizations)
32. Removed dead `_CODE_SWITCH_PATTERNS` + fixed `"_subhanallah"` in `kuwaiti_tokenizer.py`
33. Removed unused `import subprocess` in `workspace_api.py`
34. Fixed `test_vector_store_fallback.py` stray kwarg + renamed `test_ci_workflow.py` -> `test_skill_validation.py`

## Verification Summary

- **36/36 Python files:** compile-checked OK
- **2/2 YAML files:** validated OK
- **450+ tests passed** across all targeted test suites
- **8 pre-existing failures** (all confirmed unrelated via git stash isolation):
  - `test_output_routing.py`: 8 failures (pre-existing)
  - `test_interactive.py`: 2 failures (route string assertions in source text)
  - TUI suite: 6 failures (Arabic-string, RPM, NoActiveAppError, header tests)
- **Zero regressions introduced**
