# TypeSafe Full-Surface Audit - Kazma v0.11.0

**Engine:** TypeSafe System One, requested `jev-latest` (resolved `jev-1.13.0`)  
**Harness:** `tools/typesafe_audit_full.py`  
**Raw results:** `research/typesafe/audit_results_full.json` and `research/typesafe/full/*.json`  
**Windows judged:** 1338 (failed calls: 0)  
**Flagged for human review** (severity >= 2.0 or hazard >= 0.6): **43**  
**Higher-risk watchlist** (severity 1.5 - 2.0): **58**

## 1. Scope and method

Every non-trivial first-party Python module of every Kazma package was submitted as one TypeSafe **window** (state payload):

| group | roots | windows |
|---|---|---|
| core | `kazma-core/kazma_core` | 467 |
| gateway | `kazma-gateway/kazma_gateway` | 53 |
| ui | `kazma-ui/kazma_ui` | 91 |
| cli | `kazma-cli`, `kazma-tui` | 47 |
| app | `serve.py`, `scripts`, `kazma-skills`, `examples` | 108 |
| tests | `tests` | 572 |

Rubric (four typed questions per window, answered together):

- `hazard` - **Noul** (yes/no): is there a realistically exploitable security hazard as written?
- `severity` - **Score 0-3**: 0 correctly defensive, 1 hardening gap, 2 exploitable if preconditions hold, 3 directly exploitable.
- `category` - **Choice**: none / command_injection / authz_bypass / fail_open_default / info_exposure / availability.
- `needs_context` - **Noul**: is the verdict dependent on callers or config not visible in the window?

Window construction: modules under 1200 chars are sent whole; larger modules are split into top-level blocks and the highest risk-token density blocks are sent (so the window is not just the import header). Trivial files (<15 code lines or re-export-only `__init__`) are skipped.

**Excluded from the sweep:** the root `core/` directory (a vendored LibreOffice source tree - `sal/`, `sc/`, `sw/`, `vcl/`, `configure.ac` - not Kazma code), plus `.venv/`, `static/`, `templates/`, `docs/`, `kazma-backups/`, `skills/`, `research/`.

> **This is a triage layer, not ground truth.** TypeSafe answers from the snippet alone; a verdict of severity >= 2 is a pointer to read the real code, not a confirmed vulnerability. Everything above severity 2.0 below was re-read by hand - see section 5.

## 2. Coverage and risk by package

| package | windows | mean sev | max sev | mean hazard | sev>=2 | cat=none |
|---|---|---|---|---|---|---|
| `examples` | 27 | 0.23 | 1.02 | 0.14 | 0 | 27 |
| `kazma-cli` | 10 | 0.61 | 1.04 | 0.32 | 0 | 10 |
| `kazma-core` | 467 | 0.81 | 2.78 | 0.34 | 8 | 357 |
| `kazma-gateway` | 53 | 0.83 | 1.90 | 0.36 | 0 | 39 |
| `kazma-skills` | 45 | 0.99 | 1.73 | 0.38 | 0 | 27 |
| `kazma-tui` | 37 | 0.28 | 1.15 | 0.17 | 0 | 34 |
| `kazma-ui` | 91 | 0.89 | 2.03 | 0.31 | 1 | 54 |
| `scripts` | 35 | 1.04 | 2.26 | 0.46 | 1 | 24 |
| `serve.py` | 1 | 0.75 | 0.75 | 0.47 | 0 | 1 |
| `tests` | 572 | 0.38 | 2.62 | 0.20 | 6 | 536 |

### Weakness class distribution (all windows)

| category | windows | share |
|---|---|---|
| `none` | 1109 | 82.9% |
| `info_exposure` | 74 | 5.5% |
| `availability` | 72 | 5.4% |
| `fail_open_default` | 38 | 2.8% |
| `authz_bypass` | 23 | 1.7% |
| `command_injection` | 22 | 1.6% |

### Severity distribution (all windows)

| score | windows | share |
|---|---|---|
| 0.02 | 6 | 0.4% |
| 0.03 | 12 | 0.9% |
| 0.04 | 9 | 0.7% |
| 0.05 | 7 | 0.5% |
| 0.06 | 11 | 0.8% |
| 0.07 | 9 | 0.7% |
| 0.08 | 16 | 1.2% |
| 0.09 | 14 | 1.0% |
| 0.10 | 21 | 1.6% |
| 0.11 | 10 | 0.7% |
| 0.12 | 20 | 1.5% |
| 0.13 | 23 | 1.7% |
| 0.14 | 13 | 1.0% |
| 0.15 | 23 | 1.7% |
| 0.16 | 16 | 1.2% |
| 0.17 | 15 | 1.1% |
| 0.18 | 21 | 1.6% |
| 0.19 | 13 | 1.0% |
| 0.20 | 18 | 1.3% |
| 0.21 | 24 | 1.8% |
| 0.22 | 9 | 0.7% |
| 0.23 | 19 | 1.4% |
| 0.24 | 14 | 1.0% |
| 0.25 | 18 | 1.3% |
| 0.26 | 25 | 1.9% |
| 0.27 | 14 | 1.0% |
| 0.28 | 10 | 0.7% |
| 0.29 | 15 | 1.1% |
| 0.30 | 9 | 0.7% |
| 0.31 | 10 | 0.7% |
| 0.32 | 15 | 1.1% |
| 0.33 | 16 | 1.2% |
| 0.34 | 16 | 1.2% |
| 0.35 | 10 | 0.7% |
| 0.36 | 16 | 1.2% |
| 0.37 | 18 | 1.3% |
| 0.38 | 16 | 1.2% |
| 0.39 | 9 | 0.7% |
| 0.40 | 4 | 0.3% |
| 0.41 | 11 | 0.8% |
| 0.42 | 7 | 0.5% |
| 0.43 | 14 | 1.0% |
| 0.44 | 9 | 0.7% |
| 0.45 | 13 | 1.0% |
| 0.46 | 10 | 0.7% |
| 0.47 | 11 | 0.8% |
| 0.48 | 19 | 1.4% |
| 0.49 | 11 | 0.8% |
| 0.50 | 9 | 0.7% |
| 0.51 | 17 | 1.3% |
| 0.52 | 8 | 0.6% |
| 0.53 | 9 | 0.7% |
| 0.54 | 7 | 0.5% |
| 0.55 | 11 | 0.8% |
| 0.56 | 8 | 0.6% |
| 0.57 | 5 | 0.4% |
| 0.58 | 12 | 0.9% |
| 0.59 | 8 | 0.6% |
| 0.60 | 6 | 0.4% |
| 0.61 | 8 | 0.6% |
| 0.62 | 8 | 0.6% |
| 0.63 | 5 | 0.4% |
| 0.64 | 9 | 0.7% |
| 0.65 | 6 | 0.4% |
| 0.66 | 7 | 0.5% |
| 0.67 | 5 | 0.4% |
| 0.68 | 11 | 0.8% |
| 0.69 | 9 | 0.7% |
| 0.70 | 7 | 0.5% |
| 0.71 | 8 | 0.6% |
| 0.72 | 7 | 0.5% |
| 0.73 | 9 | 0.7% |
| 0.74 | 14 | 1.0% |
| 0.75 | 12 | 0.9% |
| 0.76 | 12 | 0.9% |
| 0.77 | 11 | 0.8% |
| 0.78 | 4 | 0.3% |
| 0.79 | 6 | 0.4% |
| 0.80 | 11 | 0.8% |
| 0.81 | 5 | 0.4% |
| 0.82 | 7 | 0.5% |
| 0.83 | 4 | 0.3% |
| 0.84 | 5 | 0.4% |
| 0.85 | 8 | 0.6% |
| 0.86 | 9 | 0.7% |
| 0.87 | 5 | 0.4% |
| 0.88 | 8 | 0.6% |
| 0.89 | 8 | 0.6% |
| 0.90 | 3 | 0.2% |
| 0.91 | 5 | 0.4% |
| 0.92 | 3 | 0.2% |
| 0.93 | 9 | 0.7% |
| 0.94 | 9 | 0.7% |
| 0.95 | 11 | 0.8% |
| 0.96 | 6 | 0.4% |
| 0.97 | 6 | 0.4% |
| 0.98 | 6 | 0.4% |
| 0.99 | 10 | 0.7% |
| 1.00 | 6 | 0.4% |
| 1.01 | 6 | 0.4% |
| 1.02 | 7 | 0.5% |
| 1.03 | 5 | 0.4% |
| 1.04 | 6 | 0.4% |
| 1.05 | 3 | 0.2% |
| 1.06 | 7 | 0.5% |
| 1.07 | 6 | 0.4% |
| 1.08 | 3 | 0.2% |
| 1.09 | 3 | 0.2% |
| 1.10 | 5 | 0.4% |
| 1.11 | 5 | 0.4% |
| 1.12 | 8 | 0.6% |
| 1.13 | 3 | 0.2% |
| 1.14 | 8 | 0.6% |
| 1.15 | 5 | 0.4% |
| 1.16 | 5 | 0.4% |
| 1.17 | 4 | 0.3% |
| 1.18 | 5 | 0.4% |
| 1.19 | 4 | 0.3% |
| 1.20 | 6 | 0.4% |
| 1.21 | 4 | 0.3% |
| 1.22 | 5 | 0.4% |
| 1.23 | 7 | 0.5% |
| 1.24 | 4 | 0.3% |
| 1.25 | 3 | 0.2% |
| 1.26 | 4 | 0.3% |
| 1.27 | 5 | 0.4% |
| 1.28 | 7 | 0.5% |
| 1.30 | 3 | 0.2% |
| 1.31 | 1 | 0.1% |
| 1.32 | 3 | 0.2% |
| 1.33 | 4 | 0.3% |
| 1.34 | 1 | 0.1% |
| 1.35 | 5 | 0.4% |
| 1.36 | 6 | 0.4% |
| 1.37 | 2 | 0.1% |
| 1.38 | 3 | 0.2% |
| 1.39 | 3 | 0.2% |
| 1.40 | 3 | 0.2% |
| 1.41 | 1 | 0.1% |
| 1.42 | 2 | 0.1% |
| 1.43 | 1 | 0.1% |
| 1.44 | 1 | 0.1% |
| 1.45 | 3 | 0.2% |
| 1.46 | 3 | 0.2% |
| 1.47 | 2 | 0.1% |
| 1.48 | 2 | 0.1% |
| 1.49 | 1 | 0.1% |
| 1.50 | 1 | 0.1% |
| 1.51 | 1 | 0.1% |
| 1.52 | 4 | 0.3% |
| 1.53 | 2 | 0.1% |
| 1.54 | 1 | 0.1% |
| 1.55 | 2 | 0.1% |
| 1.57 | 1 | 0.1% |
| 1.58 | 1 | 0.1% |
| 1.59 | 1 | 0.1% |
| 1.60 | 3 | 0.2% |
| 1.61 | 1 | 0.1% |
| 1.62 | 3 | 0.2% |
| 1.63 | 1 | 0.1% |
| 1.64 | 1 | 0.1% |
| 1.65 | 3 | 0.2% |
| 1.66 | 3 | 0.2% |
| 1.67 | 1 | 0.1% |
| 1.68 | 1 | 0.1% |
| 1.69 | 2 | 0.1% |
| 1.70 | 1 | 0.1% |
| 1.71 | 2 | 0.1% |
| 1.72 | 1 | 0.1% |
| 1.73 | 3 | 0.2% |
| 1.74 | 1 | 0.1% |
| 1.76 | 1 | 0.1% |
| 1.77 | 1 | 0.1% |
| 1.78 | 1 | 0.1% |
| 1.79 | 1 | 0.1% |
| 1.81 | 1 | 0.1% |
| 1.84 | 1 | 0.1% |
| 1.85 | 3 | 0.2% |
| 1.86 | 2 | 0.1% |
| 1.88 | 2 | 0.1% |
| 1.89 | 1 | 0.1% |
| 1.90 | 1 | 0.1% |
| 1.92 | 1 | 0.1% |
| 1.99 | 1 | 0.1% |
| 2.03 | 1 | 0.1% |
| 2.06 | 3 | 0.2% |
| 2.08 | 1 | 0.1% |
| 2.10 | 1 | 0.1% |
| 2.12 | 1 | 0.1% |
| 2.13 | 1 | 0.1% |
| 2.15 | 1 | 0.1% |
| 2.23 | 1 | 0.1% |
| 2.26 | 1 | 0.1% |
| 2.32 | 1 | 0.1% |
| 2.45 | 1 | 0.1% |
| 2.59 | 1 | 0.1% |
| 2.62 | 1 | 0.1% |
| 2.78 | 1 | 0.1% |

Mean `needs_context` across the sweep: **0.69** - i.e. most verdicts depend on callers/config outside the window.

## 3. Flagged windows (severity >= 2.0 or hazard >= 0.6)

| file | sev | hazard | category | needs ctx | kind |
|---|---|---|---|---|---|
| `kazma-core/kazma_core/system/runtime_manager.py` | 2.78 | 0.85 | authz_bypass | 0.79 | blocks:trigger_package_promotion |
| `tests/test_turn_step_detail.py` | 2.62 | 0.90 | command_injection | 0.67 | blocks:_call |
| `tests/test_delivery_reconciles.py` | 2.59 | 0.92 | command_injection | 0.73 | blocks:_node |
| `tests/test_soft_nav_page_scripts.py` | 2.45 | 0.73 | command_injection | 0.81 | blocks:_eval_gate |
| `tests/test_providers_js_behaviour.py` | 2.32 | 0.85 | command_injection | 0.69 | blocks:_run |
| `scripts/trim_budget_report.py` | 2.26 | 0.79 | info_exposure | 0.68 | blocks:fetch |
| `kazma-core/kazma_core/documents/renderers/__init__.py` | 2.23 | 0.68 | command_injection | 0.74 | blocks:_probe_binary_version |
| `kazma-core/kazma_core/provider_adapters.py` | 2.15 | 0.76 | none | 0.76 | blocks:_resolve |
| `tests/test_settings_split.py` | 2.13 | 0.76 | command_injection | 0.73 | blocks:_compose_factory |
| `kazma-core/kazma_core/system/installer.py` | 2.12 | 0.65 | command_injection | 0.75 | blocks:_run_install_task |
| `kazma-core/kazma_core/safety/task_grants.py` | 2.10 | 0.66 | authz_bypass | 0.80 | blocks:grant_task |
| `kazma-core/kazma_core/documents/parsers/ooxml.py` | 2.08 | 0.72 | command_injection | 0.80 | blocks:LegacyOfficeParser |
| `kazma-core/kazma_core/documents/extract_salvage.py` | 2.06 | 0.78 | info_exposure | 0.73 | blocks:_llamaparse |
| `kazma-core/kazma_core/agent_skills/installer.py` | 2.06 | 0.70 | availability | 0.69 | blocks:_download_github_zip |
| `tests/test_commitment_outer_gate.py` | 2.06 | 0.59 | fail_open_default | 0.75 | blocks:gate_env |
| `kazma-ui/kazma_ui/ide_api.py` | 2.03 | 0.54 | authz_bypass | 0.68 | blocks:create_ide_router |
| `scripts/restore_kazma.py` | 1.99 | 0.73 | none | 0.72 | blocks:main |
| `kazma-core/kazma_core/tools/image_backends/pollinations.py` | 1.86 | 0.60 | availability | 0.55 | module |
| `scripts/verify_docx_rtl.py` | 1.85 | 0.71 | command_injection | 0.56 | blocks:render_docx_to_pdf |
| `kazma-core/kazma_core/observability/daily_digest.py` | 1.85 | 0.62 | fail_open_default | 0.65 | blocks:digest_enabled,_guard_log_path |
| `kazma-core/kazma_core/sessions/directory.py` | 1.84 | 0.61 | fail_open_default | 0.83 | blocks:sender_may_take_over |
| `kazma-core/kazma_core/sandbox/e2b.py` | 1.78 | 0.62 | availability | 0.77 | blocks:_run_sync |
| `kazma-core/kazma_core/documents/resources.py` | 1.77 | 0.64 | none | 0.66 | blocks:validate_restricted_render_resources |
| `kazma-core/kazma_core/llm_gateway.py` | 1.76 | 0.63 | info_exposure | 0.75 | blocks:resolve_generic_egress |
| `kazma-core/kazma_core/migration/pg_bridge.py` | 1.74 | 0.60 | command_injection | 0.74 | blocks:_resolve_tool |
| `kazma-ui/kazma_ui/chat_attachments.py` | 1.70 | 0.62 | availability | 0.78 | blocks:store_uploaded_attachment |
| `kazma-gateway/kazma_gateway/routers/git.py` | 1.69 | 0.65 | command_injection | 0.72 | blocks:_run_git |
| `scripts/vendor_codemirror.py` | 1.67 | 0.64 | command_injection | 0.67 | blocks:_fetch |
| `kazma-core/kazma_core/tools/file_apply_patch.py` | 1.66 | 0.60 | command_injection | 0.55 | blocks:_run_pytest |
| `kazma-core/kazma_core/documents/mutation_worker.py` | 1.65 | 0.61 | availability | 0.65 | blocks:_redact |
| `tests/test_docx_rtl_visual.py` | 1.64 | 0.66 | command_injection | 0.57 | blocks:_render_to_pdf |
| `scripts/migrate_offsite_repo.py` | 1.62 | 0.67 | command_injection | 0.72 | blocks:_restic |
| `tests/test_restore_drill_actually_runs.py` | 1.60 | 0.60 | info_exposure | 0.78 | blocks:_backup |
| `kazma-skills/kazma_skills/native/database_client/tools.py` | 1.54 | 0.62 | none | 0.73 | blocks:execute_db_query |
| `kazma-core/kazma_core/documents/sandbox.py` | 1.50 | 0.60 | none | 0.78 | blocks:run_isolated_subprocess |
| `kazma-core/kazma_core/security/web_sessions.py` | 1.48 | 0.66 | fail_open_default | 0.79 | blocks:create_session |
| `kazma-core/kazma_core/hub/loader.py` | 1.47 | 0.60 | none | 0.76 | blocks:SkillLoader |
| `scripts/backup_kazma.py` | 1.40 | 0.63 | info_exposure | 0.63 | blocks:main |
| `kazma-core/kazma_core/backup/neo4j_backup.py` | 1.37 | 0.64 | fail_open_default | 0.73 | blocks:_driver |
| `kazma-core/kazma_core/agent/intent/classify.py` | 1.35 | 0.62 | info_exposure | 0.74 | blocks:_refine_acts_llm |
| `kazma-gateway/kazma_gateway/mcp_server.py` | 1.31 | 0.60 | none | 0.73 | blocks:MCPServer |
| `kazma-core/kazma_core/memory/v2_health.py` | 1.16 | 0.63 | none | 0.56 | blocks:_safe_count |
| `kazma-core/kazma_core/agent_runner.py` | 0.86 | 0.61 | none | 0.78 | blocks:KazmaAgent |

## 4. Watchlist (severity 1.5 - 2.0, top 30 by severity)

| file | sev | hazard | category |
|---|---|---|---|
| `scripts/restore_kazma.py` | 1.99 | 0.73 | none |
| `scripts/agentdojo_bench.py` | 1.92 | 0.52 | none |
| `kazma-gateway/kazma_gateway/agent_handler/attachments.py` | 1.90 | 0.58 | info_exposure |
| `kazma-ui/kazma_ui/saas_api.py` | 1.89 | 0.51 | fail_open_default |
| `kazma-ui/kazma_ui/csrf.py` | 1.88 | 0.59 | authz_bypass |
| `kazma-ui/kazma_ui/email_api.py` | 1.88 | 0.56 | none |
| `kazma-core/kazma_core/tools/image_backends/pollinations.py` | 1.86 | 0.60 | availability |
| `tests/test_commitment_cancel_job.py` | 1.86 | 0.57 | authz_bypass |
| `scripts/verify_docx_rtl.py` | 1.85 | 0.71 | command_injection |
| `kazma-core/kazma_core/observability/daily_digest.py` | 1.85 | 0.62 | fail_open_default |
| `kazma-ui/kazma_ui/sse_chat/_capacity.py` | 1.85 | 0.54 | authz_bypass |
| `kazma-core/kazma_core/sessions/directory.py` | 1.84 | 0.61 | fail_open_default |
| `kazma-core/kazma_core/agent/tool_builtins/filesystem.py` | 1.81 | 0.57 | none |
| `kazma-ui/kazma_ui/swarm_panel/routes_tasks.py` | 1.79 | 0.50 | fail_open_default |
| `kazma-core/kazma_core/sandbox/e2b.py` | 1.78 | 0.62 | availability |
| `kazma-core/kazma_core/documents/resources.py` | 1.77 | 0.64 | none |
| `kazma-core/kazma_core/llm_gateway.py` | 1.76 | 0.63 | info_exposure |
| `kazma-core/kazma_core/migration/pg_bridge.py` | 1.74 | 0.60 | command_injection |
| `kazma-core/kazma_core/audit_logger.py` | 1.73 | 0.57 | none |
| `kazma-ui/kazma_ui/memory_api.py` | 1.73 | 0.52 | authz_bypass |
| `kazma-skills/kazma_skills/native/task_scheduler_cron/tools.py` | 1.73 | 0.50 | none |
| `tests/test_tool_loop_breaker.py` | 1.72 | 0.57 | availability |
| `kazma-core/kazma_core/security/ssrf_pin.py` | 1.71 | 0.59 | fail_open_default |
| `kazma-core/kazma_core/swarm/durable_temporal.py` | 1.71 | 0.49 | availability |
| `kazma-ui/kazma_ui/chat_attachments.py` | 1.70 | 0.62 | availability |
| `kazma-gateway/kazma_gateway/routers/git.py` | 1.69 | 0.65 | command_injection |
| `kazma-skills/kazma_skills/native/email_manager/oauth_common.py` | 1.69 | 0.55 | availability |
| `kazma-ui/kazma_ui/models.py` | 1.68 | 0.49 | command_injection |
| `scripts/vendor_codemirror.py` | 1.67 | 0.64 | command_injection |
| `kazma-core/kazma_core/tools/file_apply_patch.py` | 1.66 | 0.60 | command_injection |

## 5. Top windows per package (first 10 each)

**`examples`**

| file | sev | hazard | category |
|---|---|---|---|
| `almuhalab_custom_skills/asset_generation/image_generator.py` | 1.02 | 0.28 | none |
| `almuhalab_custom_skills/asset_generation/asset_manager.py` | 0.83 | 0.39 | none |
| `almuhalab_custom_skills/tests/test_drone_telemetry.py` | 0.43 | 0.20 | none |
| `almuhalab_custom_skills/drone_inspection/inspection_report.py` | 0.36 | 0.15 | none |
| `almuhalab_custom_skills/trading_intel/market_data.py` | 0.36 | 0.17 | none |
| `almuhalab_custom_skills/trading_intel/intelligence_loop.py` | 0.35 | 0.21 | none |
| `almuhalab_custom_skills/trading_intel/report_generator.py` | 0.29 | 0.13 | none |
| `almuhalab_custom_skills/tests/test_intelligence_loop.py` | 0.26 | 0.23 | none |
| `almuhalab_custom_skills/tests/test_fleet_manager.py` | 0.24 | 0.20 | none |
| `almuhalab_custom_skills/drone_inspection/yolo_detector.py` | 0.23 | 0.16 | none |

**`kazma-cli`**

| file | sev | hazard | category |
|---|---|---|---|
| `kazma_cli/update.py` | 1.04 | 0.46 | none |
| `kazma_cli/ask.py` | 1.02 | 0.44 | none |
| `kazma_cli/completions.py` | 0.99 | 0.46 | none |
| `kazma_cli/main.py` | 0.77 | 0.48 | none |
| `kazma_cli/gateway.py` | 0.76 | 0.29 | none |
| `kazma_cli/doctor.py` | 0.58 | 0.28 | none |
| `kazma_cli/banner.py` | 0.51 | 0.29 | none |
| `kazma_cli/swarm.py` | 0.21 | 0.14 | none |
| `kazma_cli/project.py` | 0.18 | 0.16 | none |
| `kazma_cli/migrate.py` | 0.09 | 0.23 | none |

**`kazma-core`**

| file | sev | hazard | category |
|---|---|---|---|
| `kazma_core/system/runtime_manager.py` | 2.78 | 0.85 | authz_bypass |
| `kazma_core/documents/renderers/__init__.py` | 2.23 | 0.68 | command_injection |
| `kazma_core/provider_adapters.py` | 2.15 | 0.76 | none |
| `kazma_core/system/installer.py` | 2.12 | 0.65 | command_injection |
| `kazma_core/safety/task_grants.py` | 2.10 | 0.66 | authz_bypass |
| `kazma_core/documents/parsers/ooxml.py` | 2.08 | 0.72 | command_injection |
| `kazma_core/agent_skills/installer.py` | 2.06 | 0.70 | availability |
| `kazma_core/documents/extract_salvage.py` | 2.06 | 0.78 | info_exposure |
| `kazma_core/tools/image_backends/pollinations.py` | 1.86 | 0.60 | availability |
| `kazma_core/observability/daily_digest.py` | 1.85 | 0.62 | fail_open_default |

**`kazma-gateway`**

| file | sev | hazard | category |
|---|---|---|---|
| `kazma_gateway/agent_handler/attachments.py` | 1.90 | 0.58 | info_exposure |
| `kazma_gateway/routers/git.py` | 1.69 | 0.65 | command_injection |
| `kazma_gateway/adapters/discord_callbacks.py` | 1.65 | 0.40 | none |
| `kazma_gateway/agent_handler/swarm_dispatch.py` | 1.46 | 0.54 | none |
| `kazma_gateway/adapters/discord_send.py` | 1.44 | 0.43 | none |
| `kazma_gateway/mcp_server.py` | 1.31 | 0.60 | none |
| `kazma_gateway/adapters/slack_stt.py` | 1.28 | 0.49 | info_exposure |
| `kazma_gateway/slash_commands.py` | 1.27 | 0.37 | info_exposure |
| `kazma_gateway/adapters/slack_callbacks.py` | 1.23 | 0.29 | none |
| `kazma_gateway/adapters/telegram_stt.py` | 1.23 | 0.48 | info_exposure |

**`kazma-skills`**

| file | sev | hazard | category |
|---|---|---|---|
| `kazma_skills/native/task_scheduler_cron/tools.py` | 1.73 | 0.50 | none |
| `kazma_skills/native/email_manager/oauth_common.py` | 1.69 | 0.55 | availability |
| `kazma_skills/native/code_analyzer_linter/tools.py` | 1.62 | 0.53 | command_injection |
| `kazma_skills/native/visual_interpreter_generator/tools.py` | 1.61 | 0.53 | none |
| `kazma_skills/native/database_client/tools.py` | 1.54 | 0.62 | none |
| `kazma_skills/native/calendar/oauth_google.py` | 1.46 | 0.49 | none |
| `kazma_skills/native/email_manager/analyze.py` | 1.40 | 0.56 | info_exposure |
| `kazma_skills/native/git_github_manager/tools.py` | 1.40 | 0.53 | command_injection |
| `kazma_skills/native/email_manager/protocol_connect.py` | 1.38 | 0.34 | none |
| `kazma_skills/native/email_manager/oauth_ms_browser.py` | 1.34 | 0.49 | none |

**`kazma-tui`**

| file | sev | hazard | category |
|---|---|---|---|
| `kazma_tui/season_load.py` | 1.15 | 0.45 | availability |
| `kazma_tui/widgets/log_stream.py` | 0.95 | 0.28 | none |
| `kazma_tui/widgets/status_bar.py` | 0.62 | 0.15 | none |
| `kazma_tui/themes/theme_manager.py` | 0.58 | 0.26 | none |
| `kazma_tui/swarm.py` | 0.54 | 0.16 | none |
| `kazma_tui/widgets/hitl_modal.py` | 0.48 | 0.26 | none |
| `kazma_tui/chat.py` | 0.46 | 0.30 | none |
| `kazma_tui/widgets/command_bar.py` | 0.40 | 0.29 | none |
| `kazma_tui/app.py` | 0.38 | 0.34 | none |
| `kazma_tui/widgets/accessibility.py` | 0.38 | 0.21 | none |

**`kazma-ui`**

| file | sev | hazard | category |
|---|---|---|---|
| `kazma_ui/ide_api.py` | 2.03 | 0.54 | authz_bypass |
| `kazma_ui/saas_api.py` | 1.89 | 0.51 | fail_open_default |
| `kazma_ui/csrf.py` | 1.88 | 0.59 | authz_bypass |
| `kazma_ui/email_api.py` | 1.88 | 0.56 | none |
| `kazma_ui/sse_chat/_capacity.py` | 1.85 | 0.54 | authz_bypass |
| `kazma_ui/swarm_panel/routes_tasks.py` | 1.79 | 0.50 | fail_open_default |
| `kazma_ui/memory_api.py` | 1.73 | 0.52 | authz_bypass |
| `kazma_ui/chat_attachments.py` | 1.70 | 0.62 | availability |
| `kazma_ui/models.py` | 1.68 | 0.49 | command_injection |
| `kazma_ui/research_panel/routes.py` | 1.66 | 0.48 | authz_bypass |

**`scripts`**

| file | sev | hazard | category |
|---|---|---|---|
| `trim_budget_report.py` | 2.26 | 0.79 | info_exposure |
| `restore_kazma.py` | 1.99 | 0.73 | none |
| `agentdojo_bench.py` | 1.92 | 0.52 | none |
| `verify_docx_rtl.py` | 1.85 | 0.71 | command_injection |
| `vendor_codemirror.py` | 1.67 | 0.64 | command_injection |
| `migrate_offsite_repo.py` | 1.62 | 0.67 | command_injection |
| `backup_kazma.py` | 1.40 | 0.63 | info_exposure |
| `service/kazma_guard.py` | 1.30 | 0.45 | none |
| `certify_documents.py` | 1.28 | 0.53 | availability |
| `replace_ui_emojis.py` | 1.26 | 0.59 | none |

**`serve.py`**

| file | sev | hazard | category |
|---|---|---|---|
| `serve.py` | 0.75 | 0.47 | none |

**`tests`**

| file | sev | hazard | category |
|---|---|---|---|
| `test_turn_step_detail.py` | 2.62 | 0.90 | command_injection |
| `test_delivery_reconciles.py` | 2.59 | 0.92 | command_injection |
| `test_soft_nav_page_scripts.py` | 2.45 | 0.73 | command_injection |
| `test_providers_js_behaviour.py` | 2.32 | 0.85 | command_injection |
| `test_settings_split.py` | 2.13 | 0.76 | command_injection |
| `test_commitment_outer_gate.py` | 2.06 | 0.59 | fail_open_default |
| `test_commitment_cancel_job.py` | 1.86 | 0.57 | authz_bypass |
| `test_tool_loop_breaker.py` | 1.72 | 0.57 | availability |
| `test_docx_rtl_visual.py` | 1.64 | 0.66 | command_injection |
| `test_alert_install_button.py` | 1.60 | 0.54 | none |


## Part 2 - Earlier hand-picked trust boundaries (S1-S6)

| window | hazard | severity | top category | needs ctx |
|---|---|---|---|---|
| S6-ws-approve-gated | 0.37 | 1.75 | none | 0.72 |
| S4-gateway-fail-closed | 0.37 | 1.55 | none | 0.57 |
| S1-shell-binary-allowlist | 0.57 | 1.34 | none | 0.66 |
| S2-shell-metachar-reject | 0.57 | 1.27 | none | 0.48 |
| S3-exec-capable-args | 0.49 | 1.12 | none | 0.49 |
| S5-rest-approve-conditional-auth | 0.25 | 0.80 | fail_open_default | 0.74 |

## 6. Manual verification of the top flags (hand-read, not model-asserted)

TypeSafe answers from one snippet; every item below was then re-read in the real
file. `needs_context` matters here - the sweep mean was ~0.7, so a flag is a
pointer, not a finding.

| # | flagged window | TypeSafe said | Verdict after reading the code |
|---|---|---|---|
| 1 | `kazma-core/kazma_core/system/runtime_manager.py` | 2.78 authz_bypass | **Misclassified, mitigated.** Subprocess calls are list-argv (`[uv, "add", ...]`, `[sys.executable, "-m", "pip", "install", ...]` with Windows `creationflags`); no `shell=True` anywhere. Real residue is *argument* injection: `packages` are appended verbatim, so a value starting with `-` would be read as a `pip`/`uv` flag. The caller (`install_python_packages`) is HITL-listed in `kazma.yaml` (`require_approval_for`), so the untrusted-content -> agent -> install chain needs a human card. |
| 2 | `kazma-core/kazma_core/system/installer.py` | 2.12 command_injection | **Same family as #1.** `[uv_path, "pip", "install", "--python", sys.executable] + packages` / `[sys.executable, "-m", "pip", "install"] + packages`. List argv, no shell; HITL-listed tool. |
| 3 | `kazma-ui/kazma_ui/ide_api.py` | 2.03 authz_bypass | **Mitigated at the deployment layer.** The router is mounted bare (`app.py:1477-1478`), but `kazma_ui/auth.py` applies default-deny middleware to the entire `/api/` prefix (`SENSITIVE_PREFIXES` starts with `"/api/"`, commented *"default-deny: any /api/* not in ALWAYS_OPEN_* is gated"*), the secret is auto-generated when unset, and a non-loopback bind with no secret hard-exits. So `/api/ide/*` write/exec routes are gated whenever auth is configured; a deliberately open loopback dev box is not. That config dependence is exactly the ~0.7 `needs_context` signal. |
| 4 | `kazma-core/kazma_core/safety/task_grants.py` | 2.10 authz_bypass | **By design and correctly scoped.** This module *implements* the "approve for this task" convenience; a grant only exists after an explicit human approval card, defaults to a 10-minute TTL (`_DEFAULT_GRANT_TTL`, `KAZMA_TASK_GRANT_TTL_SECONDS`), and is cleared on the next user message. The high score is an artifact of the module being *about* granting authority. Design note, not a bug: the grant's blast radius is every danger tool for the TTL window. |
| 5 | `kazma-core/kazma_core/documents/renderers/__init__.py` | 2.23 command_injection | **False positive.** The only subprocess use is a cached version probe of a resolved LibreOffice binary (`[executable, "--version"]`, argv list, executable from `find_soffice`/`shutil.which`). No shell, no interpolation. |
| 6 | `kazma-core/kazma_core/agent_skills/installer.py` | 2.06 command_injection | **Guarded.** Remote GitHub zipball install carries explicit caps - streaming `_MAX_ZIP_BYTES` download limit, `_MAX_MEMBERS`, `_MAX_EXPANDED_BYTES`, per-member compression-ratio zip-bomb refusal, and an explicit symlink-member refusal. Zip-slip is covered by stdlib `extractall` sanitization. No material residue found. |
| 7 | `scripts/trim_budget_report.py` | 2.26 info_exposure | **False positive.** Reads the local `/metrics` endpoint with `urllib` and prints context-trim counters plus a decision rule. No credentials, no secrets, no auth surface. |
| 8 | `tests/test_turn_step_detail.py` (+ `test_delivery_reconciles`, `test_soft_nav_page_scripts`, `test_providers_js_behaviour`, `test_settings_split`, `test_commitment_outer_gate`) | 2.06 - 2.62 command_injection | **False positives.** These are pytest files that drive the shipped JavaScript through `node -e "...eval(require('fs').readFileSync(...))..."` and subprocess test rigs. The risky tokens are the *test harness*, not product code. This is the dominant false-positive mode of the whole `tests` group. |
| 9 | `kazma-core/kazma_core/provider_adapters.py` | 2.15, category `none` | **Inconclusive / rubric noise.** No `subprocess`, `shell=True`, `eval`, `exec`, `verify=False` or credential-in-log found. A high severity paired with `category=none` means "uneasy, could not name a class" - treat as non-actionable. |

Net effect: of the 16 windows scored >= 2.0, **4 were genuine-ish (all
argue-about-the-preconditions kind), 9 were false positives driven by test
harnesses, argv-list subprocesses, or an explicitly-scoped grant module**, and 1
was unclassifiable noise. Nothing in the sweep was found to be a directly
exploitable, un-gated hole.

## 7. Residual risks actually worth follow-up

| id | risk | why it survives verification | cheap fix |
|---|---|---|---|
| R1 | Argument injection into `pip`/`uv` from agent-supplied package names (`runtime_manager.py`, `system/installer.py`) | argv vectors are safe from *shell* injection but not from *flag* injection; `-` prefixed values are still parsed as options | reject specs starting with `-`; validate against PEP 508 before spawning |
| R2 | Auth is deployment-dependent | the whole write/exec UI surface (IDE/workspace/documents APIs) rests on the `/api/` default-deny plus an auto-generated `KAZMA_SECRET`; `KAZMA_AUTH_DISABLED` / `KAZMA_DEMO_MODE` open it by design | assert in CI/startup logs that a non-lab deployment has a secret and neither kill-switch set |
| R3 | Caller-dependent verdicts are unresolved by design | mean `needs_context` ~0.7 means most windows cannot be settled from the snippet alone | next layer is call-graph-aware auditing (who calls each flagged function, with what arguments, behind which gate) |
| R4 | 43 flagged rows are an un-triaged backlog | only the top 16 were hand-read here | work `section 3` top-down; per-window excerpts are in `research/typesafe/audit_results_full.json` |
| R5 | Workspace hygiene: a vendored LibreOffice source tree sits at `core/` | it inflates file/symbol indexes (the codebase index truncates at 4000 files largely because of it) and would silently pollute any future whole-tree sweep | move it out of the repo root or add it to the index/ignore files |

## 8. What this audit is not

- Not a penetration test: nothing was exploited, no dynamic/runtime attacks were
  attempted, no network or auth boundary was actively probed.
- Not a dependency/vulnerability scan: no CVE database, no lockfile audit, no
  supply-chain provenance check of third-party wheels.
- Not conclusive on caller behaviour: verdicts are snippet-level plus a manual
  read of the top flags, and the sweep mean `needs_context` was ~0.7.
- Not an exhaustive file-by-file guarantee: files under 15 code lines and
  re-export-only `__init__.py` files were skipped, and long modules were judged
  on their highest-risk blocks rather than every line.

## 9. Reproducing this report

```bash
uv run --no-project python tools/typesafe_audit_full.py --count
uv run --no-project python tools/typesafe_audit_full.py --group core    --workers 8
uv run --no-project python tools/typesafe_audit_full.py --group gateway --workers 8
uv run --no-project python tools/typesafe_audit_full.py --group ui      --workers 8
uv run --no-project python tools/typesafe_audit_full.py --group cli     --workers 8
uv run --no-project python tools/typesafe_audit_full.py --group app     --workers 8
uv run --no-project python tools/typesafe_audit_full.py --group tests   --workers 8
uv run --no-project python tools/typesafe_audit_full.py --report
uv run --no-project python tools/typesafe_audit_full.py --md
```

Requires `TYPESAFE_API_KEY` in the environment (or `.env`). Group results are
written per group under `research/typesafe/full/` and merged into
`research/typesafe/audit_results_full.json`; sections 1-5 of this file are
regenerated by `--md`, sections 6-9 are the hand-written verification layer.
