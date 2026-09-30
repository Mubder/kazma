# Coverage table — full audit 2026-09-30

One row per product and script file (vendored minified bundles and tests
excluded; tests were scanned for stale endpoint references). `Scanner
hits` counts high-signal ruff/bandit results that were triaged (most were
cleared as false positives — see the report's "Audited and cleared").
`Read by hand` names the lines read; "—" means covered by the automated
checks in `Automated` only.

Files: 921 · lines: 375,595 · read by hand (full or in part): 49

| File | Lines | Automated | Read by hand | Scanner hits | Findings |
|---|---:|---|---|---:|---|
| `kazma-cli/kazma_cli/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-cli/kazma_cli/ask.py` | 197 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-cli/kazma_cli/banner.py` | 321 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-cli/kazma_cli/completions.py` | 380 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-cli/kazma_cli/doctor.py` | 318 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-cli/kazma_cli/gateway.py` | 290 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-cli/kazma_cli/main.py` | 807 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-cli/kazma_cli/migrate.py` | 298 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-cli/kazma_cli/project.py` | 245 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-cli/kazma_cli/swarm.py` | 839 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-cli/kazma_cli/update.py` | 1768 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 59 | — |
| `kazma-core/kazma_core/__init__.py` | 73 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/__init__.py` | 46 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/artifacts.py` | 924 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/agent/capacity_commands.py` | 518 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/graph_builder.py` | 329 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 17 | — |
| `kazma-core/kazma_core/agent/graph_helpers.py` | 511 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/agent/graph_respond.py` | 368 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/agent/graph_supervisor.py` | 2236 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 56 | — |
| `kazma-core/kazma_core/agent/graph_tool_worker.py` | 1620 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 34 | — |
| `kazma-core/kazma_core/agent/hitl_supersede.py` | 118 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/intent/__init__.py` | 28 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/intent/classify.py` | 279 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/agent/intent/config.py` | 49 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/agent/intent/entities.py` | 142 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/agent/intent/handlers/__init__.py` | 0 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/intent/handlers/compose.py` | 143 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/agent/intent/handlers/document.py` | 418 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 19 | — |
| `kazma-core/kazma_core/agent/intent/handlers/research.py` | 110 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/intent/heuristics.py` | 255 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/agent/intent/metrics.py` | 26 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/agent/intent/policy.py` | 239 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/intent/registry.py` | 101 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/agent/intent/types.py` | 82 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/intent_router.py` | 136 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/long_task.py` | 880 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 13 | — |
| `kazma-core/kazma_core/agent/nonstop.py` | 222 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/pipeline_registry.py` | 127 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/agent/pipeline_schema.py` | 84 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/pipelines/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/pipelines/document.py` | 690 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 12 | — |
| `kazma-core/kazma_core/agent/plan_fence.py` | 346 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/agent/plan_mode.py` | 397 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/agent/research_policy.py` | 305 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/resilient_chat.py` | 294 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/agent/semantic_compact.py` | 264 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/agent/slash_turns.py` | 165 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/agent/state.py` | 447 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/steer.py` | 209 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/sub_agent.py` | 276 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/supervisor_watchdog.py` | 288 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/agent/task_ledger.py` | 476 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/agent/tool_builtins/__init__.py` | 56 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/agent/tool_builtins/external.py` | 541 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/agent/tool_builtins/filesystem.py` | 749 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 15 | — |
| `kazma-core/kazma_core/agent/tool_builtins/knowledge.py` | 290 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/agent/tool_builtins/mcp.py` | 79 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent/tool_builtins/memory.py` | 863 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-core/kazma_core/agent/tool_builtins/research.py` | 124 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/agent/tool_builtins/system.py` | 978 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 100-180 | 22 | AUD-007 |
| `kazma-core/kazma_core/agent/tool_hooks.py` | 475 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 1-300 | 12 | AUD-007 |
| `kazma-core/kazma_core/agent/tool_loop_breaker.py` | 357 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/tool_registry.py` | 980 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 21 | — |
| `kazma-core/kazma_core/agent/tool_schema.py` | 338 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/tool_scope.py` | 56 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/agent/topic_drift.py` | 381 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/agent/turn.py` | 255 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/agent/turn_client.py` | 202 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 150-180 | 7 | — |
| `kazma-core/kazma_core/agent/turn_input.py` | 1614 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 20 | — |
| `kazma-core/kazma_core/agent_runner.py` | 1345 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 31 | — |
| `kazma-core/kazma_core/agent_skills/__init__.py` | 49 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent_skills/catalog.py` | 224 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/agent_skills/discovery.py` | 303 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/agent_skills/installer.py` | 662 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 250-380 | 15 | AUD-005 |
| `kazma-core/kazma_core/agent_skills/integrity.py` | 212 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/agent_skills/parser.py` | 217 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/agent_skills/tools.py` | 184 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/anthropic_llm.py` | 697 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/arabic/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/arabic/kuwaiti_lexicon.py` | 116 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/audit_logger.py` | 288 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/authority.py` | 95 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/authorization_flow.py` | 357 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/azure_llm.py` | 135 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/background.py` | 142 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/backup/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/backup/cloud_sync.py` | 1074 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 335-360 | 35 | AUD-006 |
| `kazma-core/kazma_core/backup/neo4j_backup.py` | 390 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/backup/restic_repo.py` | 908 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-core/kazma_core/backup/restore.py` | 468 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/backup/restore_drill.py` | 1059 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/backup/restore_rehearsal.py` | 215 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/backup/universal.py` | 1157 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 25 | — |
| `kazma-core/kazma_core/bedrock_llm.py` | 456 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/chaos/__init__.py` | 680 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/chat_files.py` | 164 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/checkpoint_retention.py` | 380 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/checkpoint_serde.py` | 47 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/checkpoints_pg.py` | 78 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/checkpoints_shared.py` | 193 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/cli/__init__.py` | 5 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/cli/ask.py` | 1126 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 28 | — |
| `kazma-core/kazma_core/cli/wizard.py` | 341 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/code_index/__init__.py` | 20 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/code_index/indexer.py` | 172 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/code_index/ripgrep.py` | 115 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/code_index/search.py` | 113 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/code_index/store.py` | 111 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/code_index/symbols.py` | 184 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/code_index/walk.py` | 71 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/compaction.py` | 421 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/config_defaults.py` | 82 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/config_loader.py` | 154 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/config_schema.py` | 228 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/config_store.py` | 2556 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 34 | — |
| `kazma-core/kazma_core/constants.py` | 131 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/cost_breaker.py` | 275 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/cron/__init__.py` | 5 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/cron/scheduler.py` | 1280 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 36 | — |
| `kazma-core/kazma_core/cultural_context.py` | 389 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/cultural_context_enrichment.py` | 123 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/db/__init__.py` | 31 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/db/backend.py` | 66 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/db/pg_backup.py` | 352 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/db/pg_helpers.py` | 85 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/db/postgres_pool.py` | 299 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/db/shared_store_peers.py` | 268 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/db/sqlite_session.py` | 70 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/diagnostic_scope.py` | 125 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/dialect_detector.py` | 285 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/division_runtime.py` | 271 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/division_sandbox.py` | 278 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/docker_cli.py` | 90 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/__init__.py` | 196 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/arabic.py` | 446 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/documents/artifacts.py` | 102 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/audit.py` | 510 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 370-400 | 5 | — |
| `kazma-core/kazma_core/documents/backup.py` | 251 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/binaries.py` | 136 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/documents/capacity.py` | 339 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/documents/certification.py` | 537 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/documents/config.py` | 784 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/documents/content_model.py` | 131 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/engines/__init__.py` | 13 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/engines/docx.py` | 988 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 12 | — |
| `kazma-core/kazma_core/documents/engines/html.py` | 483 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/documents/engines/pdf.py` | 699 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/documents/engines/pptx.py` | 226 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/documents/engines/xlsx.py` | 200 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/documents/errors.py` | 73 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/extract_salvage.py` | 311 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/documents/fonts.py` | 307 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/documents/heading_text.py` | 59 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/hostile_corpus.py` | 464 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/indexer.py` | 366 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/ingestion.py` | 2273 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/documents/jobs.py` | 1290 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 13 | — |
| `kazma-core/kazma_core/documents/jobs_pg.py` | 1064 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 13 | — |
| `kazma-core/kazma_core/documents/knowledge.py` | 352 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/documents/malware.py` | 197 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/math_text.py` | 232 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/models.py` | 511 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/mutation.py` | 35 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/mutation_worker.py` | 480 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/documents/ocr/__init__.py` | 28 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/ocr/base.py` | 117 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/ocr/pipeline.py` | 395 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/documents/ocr/raster.py` | 288 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/documents/ocr/tesseract.py` | 338 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/operations.py` | 779 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/documents/parser_worker.py` | 170 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/documents/parsers/__init__.py` | 221 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/documents/parsers/common.py` | 156 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/parsers/image.py` | 61 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/parsers/ooxml.py` | 200 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/parsers/pdf.py` | 493 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 19 | — |
| `kazma-core/kazma_core/documents/parsers/pdf_layout.py` | 233 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/parsers/text.py` | 127 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/profile.py` | 260 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/quality.py` | 236 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/registry.py` | 259 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/documents/renderer_worker.py` | 745 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/documents/renderers/__init__.py` | 303 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/documents/repository.py` | 1421 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-core/kazma_core/documents/repository_pg.py` | 1355 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-core/kazma_core/documents/resources.py` | 42 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/documents/retention.py` | 643 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/documents/rich_render.py` | 897 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/documents/sandbox.py` | 475 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/documents/service.py` | 1212 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/documents/sniff.py` | 451 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `kazma-core/kazma_core/documents/storage.py` | 315 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/style_theme.py` | 190 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/telemetry.py` | 400 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/documents/worker.py` | 513 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/env_files.py` | 116 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/errors.py` | 161 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/eventloop.py` | 137 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/exceptions.py` | 184 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/git_identity.py` | 487 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/google_llm.py` | 427 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/http_pool.py` | 57 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/http_tls.py` | 57 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/hub/__init__.py` | 22 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/hub/__main__.py` | 9 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/hub/api.py` | 428 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | AUD-013 |
| `kazma-core/kazma_core/hub/badges.py` | 279 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/hub/cli.py` | 805 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/hub/loader.py` | 302 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/hub/manifest_schema.py` | 145 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/hub/registry.py` | 383 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/hub/validator.py` | 260 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/hub/versioning.py` | 79 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/ide/__init__.py` | 14 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/ide/env_context.py` | 435 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 21 | — |
| `kazma-core/kazma_core/ide/file_checkpoints.py` | 233 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/ide/hunks.py` | 61 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/ide/lsp.py` | 388 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/ide/service.py` | 738 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/ide/workspace_scope.py` | 196 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/kuwaiti_tokenizer.py` | 270 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/language_lock.py` | 108 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/lifecycle_notifier.py` | 522 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 200-460 | 1 | — |
| `kazma-core/kazma_core/llm_gateway.py` | 216 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/llm_provider.py` | 1945 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 800-855 | 31 | AUD-001 |
| `kazma-core/kazma_core/llm_stream.py` | 313 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/logging_config.py` | 406 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-core/kazma_core/majlis.py` | 355 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/majlis_runtime.py` | 77 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/mcp/__init__.py` | 19 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/mcp/manager.py` | 2500 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 44 | — |
| `kazma-core/kazma_core/mcp/oauth.py` | 538 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/mcp/reconnect.py` | 316 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/mcp/server.py` | 508 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/mcp/spec_client.py` | 385 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/mcp/spec_tools.py` | 148 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/mcp_client.py` | 428 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/mcp_servers_store.py` | 396 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/memory/__init__.py` | 20 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/memory/backends.py` | 1707 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 315-335, 660-670 | 40 | — |
| `kazma-core/kazma_core/memory/backfill_v2.py` | 738 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 17 | — |
| `kazma-core/kazma_core/memory/backup.py` | 144 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/memory/belief_extractor.py` | 642 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 14 | — |
| `kazma-core/kazma_core/memory/belief_mutation.py` | 992 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 31 | — |
| `kazma-core/kazma_core/memory/benchmark.py` | 366 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/memory/chat_history.py` | 586 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/memory/config.py` | 502 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/memory/consolidator.py` | 619 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-core/kazma_core/memory/current_facts.py` | 227 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/memory/dual_write.py` | 526 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/memory/ego_anchor.py` | 286 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/memory/embedder.py` | 754 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 13 | — |
| `kazma-core/kazma_core/memory/entity_counts.py` | 141 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/memory/entity_resolution.py` | 609 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 21 | — |
| `kazma-core/kazma_core/memory/episode_text.py` | 114 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/memory/eval_golden.py` | 255 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/memory/export.py` | 159 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/memory/federated_search.py` | 373 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/memory/forget.py` | 550 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-core/kazma_core/memory/fts_health.py` | 72 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/memory/global_reconsolidation.py` | 332 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-core/kazma_core/memory/graph_backend.py` | 723 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 22 | — |
| `kazma-core/kazma_core/memory/health.py` | 818 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 17 | — |
| `kazma-core/kazma_core/memory/hygiene.py` | 312 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 15 | — |
| `kazma-core/kazma_core/memory/legacy_tables.py` | 286 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 12 | — |
| `kazma-core/kazma_core/memory/macro_sleep.py` | 292 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/memory/ppr.py` | 170 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/memory/predicates.py` | 161 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/memory/procedural.py` | 258 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/memory/profile.py` | 108 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/memory/query_terms.py` | 162 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/memory/recall.py` | 2313 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 74 | — |
| `kazma-core/kazma_core/memory/reembed.py` | 556 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 24 | — |
| `kazma-core/kazma_core/memory/rehydrate.py` | 777 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/memory/remember_request.py` | 59 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/memory/schema_v2.py` | 669 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 15 | — |
| `kazma-core/kazma_core/memory/self_hub.py` | 225 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/memory/state_backend.py` | 1340 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 46 | — |
| `kazma-core/kazma_core/memory/swarm_bridge.py` | 255 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/memory/task_queue.py` | 503 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-core/kazma_core/memory/topic_summaries.py` | 1029 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 17 | — |
| `kazma-core/kazma_core/memory/transcript_recall.py` | 246 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/memory/turn_reconcile.py` | 278 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/memory/unified_index.py` | 121 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/memory/v2_health.py` | 352 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 29 | — |
| `kazma-core/kazma_core/memory/vector_engine.py` | 263 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/memory/vector_store_global.py` | 453 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/memory/worker_bootstrap.py` | 1549 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 41 | — |
| `kazma-core/kazma_core/metrics.py` | 382 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/migration/__init__.py` | 49 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/migration/bundle.py` | 328 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/migration/exporter.py` | 527 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 390-410 | 20 | — |
| `kazma-core/kazma_core/migration/importer.py` | 862 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 19 | — |
| `kazma-core/kazma_core/migration/path_rewrite.py` | 325 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 220-265 | 10 | AUD-028 |
| `kazma-core/kazma_core/migration/pg_bridge.py` | 373 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/migration/vault_pairing.py` | 185 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/model_registry.py` | 1517 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 19 | — |
| `kazma-core/kazma_core/model_registry_store.py` | 280 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/models/__init__.py` | 5 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/models/discovery.py` | 569 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 12 | — |
| `kazma-core/kazma_core/models/modality.py` | 74 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/models/router.py` | 255 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/models/selection.py` | 322 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/msa_tokenizer.py` | 250 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/observability/__init__.py` | 7 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/observability/alert_card.py` | 72 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/observability/alerts.py` | 456 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/observability/cadence.py` | 64 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/observability/connector_health.py` | 307 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/observability/correlation.py` | 78 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/observability/daily_digest.py` | 279 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/observability/firing_ledger.py` | 674 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | full (rewritten this window) | 5 | — |
| `kazma-core/kazma_core/observability/genai_otel.py` | 411 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 17 | — |
| `kazma-core/kazma_core/observability/llm_ledger.py` | 202 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/observability/loop_stall.py` | 197 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/observability/model_fallback.py` | 240 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/observability/ops_alerts.py` | 494 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/observability/resilience_manifest.py` | 319 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/observability/supervisor_watch.py` | 139 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/pacing.py` | 379 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/path_refresh.py` | 133 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/paths.py` | 517 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/permissions.py` | 240 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/personalities.py` | 268 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/playwright_loop.py` | 92 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/process_priority.py` | 161 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | full (written this window) | 0 | — |
| `kazma-core/kazma_core/product_knowledge.py` | 191 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/prompt_cache.py` | 172 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/provider_adapters.py` | 104 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/provider_probe.py` | 114 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/providers.py` | 329 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/proxy/__init__.py` | 29 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/proxy/anyip.py` | 99 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/proxy/base.py` | 83 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/proxy/brightdata.py` | 42 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/proxy/client.py` | 197 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-core/kazma_core/proxy/oxylabs.py` | 42 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/proxy/registry.py` | 75 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/rbac.py` | 439 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/retry.py` | 176 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/router.py` | 206 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/routing_engine.py` | 347 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/runtime/__init__.py` | 49 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/runtime/live_llm.py` | 288 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/runtime/local_api.py` | 196 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/runtime/model_switch.py` | 446 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 19 | — |
| `kazma-core/kazma_core/runtime/turn_model.py` | 73 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/safety/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/safety/bus_bridge.py` | 323 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `kazma-core/kazma_core/safety/commitment/__init__.py` | 33 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/safety/commitment/authorize.py` | 1283 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 880-915, 1255-1300 | 9 | AUD-007 |
| `kazma-core/kazma_core/safety/commitment/config.py` | 158 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/safety/commitment/constraints.py` | 124 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/safety/commitment/proposals.py` | 28 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/safety/commitment/python_denylist.py` | 176 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/safety/commitment/relative_time.py` | 943 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/safety/commitment/resume.py` | 157 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/safety/commitment/scope.py` | 172 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/safety/commitment/store.py` | 626 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/safety/hitl.py` | 681 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 12 | — |
| `kazma-core/kazma_core/safety/hitl_gates.py` | 870 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 17 | — |
| `kazma-core/kazma_core/safety/hitl_grants.py` | 206 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/safety/post_hitl.py` | 255 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/safety/prompt_fence.py` | 328 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/safety/side_effects.py` | 368 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 120-150 | 1 | — |
| `kazma-core/kazma_core/safety/task_grants.py` | 157 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/safety/yolo.py` | 313 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/sandbox/__init__.py` | 7 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/sandbox/e2b.py` | 138 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/security/__init__.py` | 71 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/security/audit_trail.py` | 236 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/security/boot_guard.py` | 124 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/security/browser_egress.py` | 105 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/security/certification.py` | 349 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/security/child_env.py` | 91 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/security/dependency_scanner.py` | 908 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/security/disclosure.py` | 521 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 280-305 | 3 | — |
| `kazma-core/kazma_core/security/hardening.py` | 664 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/security/linter.py` | 479 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/security/oidc.py` | 356 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/security/platform_rbac.py` | 347 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 140-330 | 3 | — |
| `kazma-core/kazma_core/security/rlimits.py` | 93 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/security/safe_xml.py` | 42 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/security/ssrf.py` | 343 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/security/ssrf_pin.py` | 59 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/security/url_credentials.py` | 179 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/security/vault.py` | 882 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/security/web_sessions.py` | 240 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | full | 4 | — |
| `kazma-core/kazma_core/service_container.py` | 120 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/sessions/__init__.py` | 44 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/sessions/directory.py` | 628 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 15 | — |
| `kazma-core/kazma_core/sessions/ttl.py` | 49 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/settings/__init__.py` | 10 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/settings/model_registry.py` | 155 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/settings_manager.py` | 1450 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-core/kazma_core/settings_mcp.py` | 206 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 13 | — |
| `kazma-core/kazma_core/shutdown.py` | 137 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/skills/self_improvement.py` | 902 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 18 | — |
| `kazma-core/kazma_core/skills/switches.py` | 93 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/state.py` | 51 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/store_registry.py` | 555 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/stores/__init__.py` | 67 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/stores/bookmarks.py` | 252 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/stores/kb_jobs.py` | 157 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/stores/knowledge.py` | 1358 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 23 | — |
| `kazma-core/kazma_core/stores/knowledge_chunker.py` | 350 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/stores/knowledge_index.py` | 1006 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-core/kazma_core/stores/knowledge_ingest.py` | 1725 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 45 | — |
| `kazma-core/kazma_core/stores/workspaces.py` | 463 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-core/kazma_core/summarizer.py` | 307 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/swarm/__init__.py` | 114 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/aggregator.py` | 223 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/swarm/autoscaler.py` | 452 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/swarm/blackboard.py` | 104 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/broadcast.py` | 115 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/bus.py` | 445 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/swarm/checkpoint.py` | 215 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/checkpoint_manager.py` | 399 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/swarm/config.py` | 193 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/consultation.py` | 269 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/dag_schema.py` | 195 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/swarm/dispatch_helpers.py` | 175 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/dispatch_inner.py` | 391 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/swarm/durable.py` | 216 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/swarm/durable_temporal.py` | 45 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/engine.py` | 1615 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 27 | — |
| `kazma-core/kazma_core/swarm/handoff.py` | 89 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/handoff_guards.py` | 84 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/manager.py` | 134 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/memory/__init__.py` | 16 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/memory/pipeline_logger.py` | 227 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/swarm/metrics.py` | 285 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/middleware.py` | 93 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/patterns.py` | 821 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 350-380, 490-505 | 9 | — |
| `kazma-core/kazma_core/swarm/phonebook.py` | 127 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/swarm/registry.py` | 341 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/swarm/reliability.py` | 1110 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/swarm/reliability_registry.py` | 232 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/safety.py` | 584 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 14 | — |
| `kazma-core/kazma_core/swarm/semantic_cache.py` | 268 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 1-200 | 14 | AUD-001 |
| `kazma-core/kazma_core/swarm/semantic_router.py` | 310 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/swarm/shared_approvals.py` | 317 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/swarm/sse_bridge.py` | 43 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/swarm/task.py` | 384 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/task_control.py` | 126 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/task_lifecycle.py` | 89 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/swarm/task_store.py` | 957 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 400-440 | 22 | — |
| `kazma-core/kazma_core/swarm/tracing.py` | 618 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/swarm/worker.py` | 622 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/swarm/worker_dispatch.py` | 408 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/swarm/worker_factory.py` | 73 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/system/__init__.py` | 29 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/system/installer.py` | 300 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-core/kazma_core/system/maintenance.py` | 108 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/system/runtime_manager.py` | 174 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/telemetry.py` | 298 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/tenant_context.py` | 78 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tenant_isolation.py` | 100 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/text_display.py` | 127 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/time_travel.py` | 919 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/token_counter.py` | 118 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/tokenizer.py` | 94 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/tone_adapter.py` | 322 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/__init__.py` | 57 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/code_exec.py` | 722 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 27 | — |
| `kazma-core/kazma_core/tools/computer_use.py` | 335 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 12 | — |
| `kazma-core/kazma_core/tools/computer_use_planners.py` | 407 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/tools/context_cmd.py` | 105 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/tools/export_session.py` | 154 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/file_apply_patch.py` | 362 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `kazma-core/kazma_core/tools/file_read.py` | 392 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/tools/file_write.py` | 156 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/tools/image_backends/__init__.py` | 3 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/image_backends/base.py` | 26 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/image_backends/dall_e.py` | 70 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/image_backends/flux.py` | 72 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/image_backends/pollinations.py` | 29 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/image_backends/router.py` | 73 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/image_backends/stability.py` | 67 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/image_gen.py` | 195 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/tools/personality_cmd.py` | 107 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/read_url.py` | 1530 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 15 | — |
| `kazma-core/kazma_core/tools/research_eval.py` | 174 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/tools/research_evidence.py` | 170 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/tools/research_pipeline.py` | 848 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 45 | — |
| `kazma-core/kazma_core/tools/research_planner.py` | 292 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/tools/research_readiness.py` | 233 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/tools/research_session.py` | 733 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 26 | — |
| `kazma-core/kazma_core/tools/research_synthesize.py` | 193 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/tools/send_message.py` | 266 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/tools/text_newlines.py` | 54 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/tools/vision_analyze.py` | 449 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `kazma-core/kazma_core/tools/web_research.py` | 387 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/tools/web_search.py` | 556 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-core/kazma_core/tracing/__init__.py` | 424 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-core/kazma_core/tracing/events.py` | 258 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/tracing/langfuse_enable.py` | 75 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/turn_notes.py` | 125 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/url_utils.py` | 232 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/version.py` | 169 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/vision_capability.py` | 235 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/voice/__init__.py` | 27 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/voice/audio_format.py` | 63 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/voice/livekit.py` | 140 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/voice/mode.py` | 131 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/voice/pcm.py` | 72 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/voice/stt.py` | 712 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 640-713 | 16 | AUD-009 |
| `kazma-core/kazma_core/voice/tts.py` | 585 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-core/kazma_core/voice/vad.py` | 206 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/web_acquire/__init__.py` | 42 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/web_acquire/fetch.py` | 115 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/web_acquire/profiles.py` | 91 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/web_acquire/rank.py` | 165 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/web_acquire/serp.py` | 111 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/workspace/__init__.py` | 32 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/workspace/binding.py` | 242 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/workspace/mcp_rebind.py` | 302 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/workspace/path_grants.py` | 379 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `kazma-core/kazma_core/workspace/path_policy.py` | 388 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-core/kazma_core/x_api/__init__.py` | 39 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/x_api/audit.py` | 329 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-core/kazma_core/x_api/booking.py` | 253 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/x_api/client.py` | 349 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-core/kazma_core/x_api/config.py` | 178 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/x_api/ledger.py` | 205 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/x_api/mentions_fire.py` | 550 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 375-412 | 11 | — |
| `kazma-core/kazma_core/x_api/oauth1.py` | 99 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/x_api/policy.py` | 145 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-core/kazma_core/x_api/reply.py` | 1416 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-core/kazma_core/x_api/reply_store.py` | 464 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-core/kazma_core/x_api/schedule.py` | 367 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-core/kazma_core/x_api/scheduled_fire.py` | 258 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-core/kazma_core/x_api/stance.py` | 805 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-gateway/kazma_gateway/__init__.py` | 31 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/__init__.py` | 18 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/callback_store.py` | 132 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-gateway/kazma_gateway/adapters/discord.py` | 901 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 14 | — |
| `kazma-gateway/kazma_gateway/adapters/discord_bus.py` | 339 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-gateway/kazma_gateway/adapters/discord_callbacks.py` | 46 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-gateway/kazma_gateway/adapters/discord_diagnose.py` | 282 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-gateway/kazma_gateway/adapters/discord_keyboards.py` | 35 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/discord_parse.py` | 119 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-gateway/kazma_gateway/adapters/discord_receive.py` | 58 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/discord_send.py` | 105 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/discord_stt.py` | 67 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/platform_callbacks.py` | 106 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-gateway/kazma_gateway/adapters/platform_keyboards.py` | 261 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/slack.py` | 1233 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 80-200, 591-1010 | 20 | — |
| `kazma-gateway/kazma_gateway/adapters/slack_blocks.py` | 35 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/slack_bus.py` | 370 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-gateway/kazma_gateway/adapters/slack_callbacks.py` | 43 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-gateway/kazma_gateway/adapters/slack_diagnose.py` | 260 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 1-60, 230-262 | 2 | — |
| `kazma-gateway/kazma_gateway/adapters/slack_parse.py` | 102 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-gateway/kazma_gateway/adapters/slack_receive.py` | 56 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/slack_send.py` | 94 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/slack_stt.py` | 104 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/telegram.py` | 1932 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 850-945 | 32 | — |
| `kazma-gateway/kazma_gateway/adapters/telegram_bus.py` | 534 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-gateway/kazma_gateway/adapters/telegram_callbacks.py` | 17 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/telegram_diagnose.py` | 249 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-gateway/kazma_gateway/adapters/telegram_keyboards.py` | 107 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/telegram_parse.py` | 82 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/telegram_receive.py` | 45 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/telegram_send.py` | 265 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-gateway/kazma_gateway/adapters/telegram_stt.py` | 91 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/adapters/voice_helpers.py` | 384 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-gateway/kazma_gateway/adapters/ws_shutdown.py` | 59 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/agent_handler/__init__.py` | 83 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/agent_handler/attachments.py` | 411 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-gateway/kazma_gateway/agent_handler/commands.py` | 2461 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 58 | — |
| `kazma-gateway/kazma_gateway/agent_handler/graph.py` | 2439 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 72 | — |
| `kazma-gateway/kazma_gateway/agent_handler/hitl.py` | 1061 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 26 | — |
| `kazma-gateway/kazma_gateway/agent_handler/session_commands.py` | 184 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-gateway/kazma_gateway/agent_handler/store.py` | 338 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-gateway/kazma_gateway/agent_handler/swarm_dispatch.py` | 619 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-gateway/kazma_gateway/agent_handler/swarm_output.py` | 396 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-gateway/kazma_gateway/allowlists.py` | 181 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-gateway/kazma_gateway/chat_tables.py` | 95 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/connector_test.py` | 124 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/gateway.py` | 970 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-gateway/kazma_gateway/mcp_server.py` | 669 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-gateway/kazma_gateway/rate_feedback.py` | 144 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/receive_log.py` | 211 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 30-215 | 0 | — |
| `kazma-gateway/kazma_gateway/routers/__init__.py` | 21 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/routers/bookmarks.py` | 176 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-gateway/kazma_gateway/routers/git.py` | 180 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-gateway/kazma_gateway/routers/github.py` | 1372 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 530-600 | 32 | AUD-024 |
| `kazma-gateway/kazma_gateway/routers/github_client.py` | 488 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-gateway/kazma_gateway/routers/pipeline.py` | 114 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-gateway/kazma_gateway/routers/workspace.py` | 349 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-gateway/kazma_gateway/routers/workspaces.py` | 393 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 14 | — |
| `kazma-gateway/kazma_gateway/slash_commands.py` | 784 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-gateway/kazma_gateway/stores/__init__.py` | 6 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-gateway/kazma_gateway/stores/checkpoint.py` | 562 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 18 | — |
| `kazma-gateway/kazma_gateway/stores/sqlite.py` | 224 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-gateway/kazma_gateway/suggestions.py` | 323 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-gateway/kazma_gateway/swarm_notify.py` | 425 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-gateway/kazma_gateway/telegram_format.py` | 207 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-gateway/kazma_gateway/typing_keepalive.py` | 122 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-skills/kazma_skills/__init__.py` | 5 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/coding_skills.py` | 89 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-skills/kazma_skills/manifest.py` | 170 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-skills/kazma_skills/native/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/_subprocess.py` | 72 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/advanced_web_crawler/tools.py` | 105 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-skills/kazma_skills/native/arabic_bilingual_nlp/tools.py` | 188 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-skills/kazma_skills/native/browser_automation/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/browser_automation/tools.py` | 368 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-skills/kazma_skills/native/calendar/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/calendar/backends/__init__.py` | 2 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/calendar/backends/base.py` | 40 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/calendar/backends/google_calendar.py` | 208 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-skills/kazma_skills/native/calendar/backends/outlook_calendar.py` | 256 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-skills/kazma_skills/native/calendar/backends/sandbox.py` | 114 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/calendar/credentials.py` | 331 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-skills/kazma_skills/native/calendar/oauth_google.py` | 288 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-skills/kazma_skills/native/calendar/router.py` | 181 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/calendar/tools.py` | 183 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-skills/kazma_skills/native/chat_platform_dispatcher/tools.py` | 129 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-skills/kazma_skills/native/code_analyzer_linter/tools.py` | 144 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-skills/kazma_skills/native/database_client/tools.py` | 558 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 15 | — |
| `kazma-skills/kazma_skills/native/document_generator/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/document_generator/tools.py` | 340 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-skills/kazma_skills/native/document_platform/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/document_platform/tools.py` | 439 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-skills/kazma_skills/native/document_processor/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/document_processor/tools.py` | 299 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-skills/kazma_skills/native/email_manager/__init__.py` | 5 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/email_manager/accounts.py` | 465 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-skills/kazma_skills/native/email_manager/analyze.py` | 158 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-skills/kazma_skills/native/email_manager/backends/__init__.py` | 5 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/email_manager/backends/gmail_api.py` | 318 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 14 | — |
| `kazma-skills/kazma_skills/native/email_manager/backends/imap_smtp.py` | 326 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 30 | — |
| `kazma-skills/kazma_skills/native/email_manager/backends/microsoft_graph.py` | 359 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-skills/kazma_skills/native/email_manager/backends/pop_smtp.py` | 247 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 18 | — |
| `kazma-skills/kazma_skills/native/email_manager/backends/sandbox.py` | 270 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-skills/kazma_skills/native/email_manager/credentials.py` | 291 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-skills/kazma_skills/native/email_manager/models.py` | 108 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/email_manager/oauth_common.py` | 88 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 1-95 | 0 | — |
| `kazma-skills/kazma_skills/native/email_manager/oauth_gmail.py` | 520 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-skills/kazma_skills/native/email_manager/oauth_ms.py` | 270 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-skills/kazma_skills/native/email_manager/oauth_ms_browser.py` | 178 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-skills/kazma_skills/native/email_manager/presets.py` | 64 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/email_manager/protocol_connect.py` | 256 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-skills/kazma_skills/native/email_manager/refreshed_grants.py` | 125 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/email_manager/router.py` | 602 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-skills/kazma_skills/native/email_manager/tools.py` | 368 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-skills/kazma_skills/native/environment_bootstrapper/tools.py` | 134 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-skills/kazma_skills/native/git_github_manager/tools.py` | 842 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 30 | — |
| `kazma-skills/kazma_skills/native/secret_vault/tools.py` | 133 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-skills/kazma_skills/native/system_health_monitor/tools.py` | 191 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 139-195 | 5 | AUD-004 |
| `kazma-skills/kazma_skills/native/task_scheduler_cron/tools.py` | 145 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-skills/kazma_skills/native/visual_interpreter_generator/tools.py` | 71 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-skills/kazma_skills/native/x_publisher/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-skills/kazma_skills/native/x_publisher/tools.py` | 251 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-skills/kazma_skills/native_loader.py` | 84 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/__init__.py` | 10 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-tui/kazma_tui/__main__.py` | 10 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/app.py` | 695 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 46 | — |
| `kazma-tui/kazma_tui/chat.py` | 1444 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 47 | — |
| `kazma-tui/kazma_tui/dashboard.py` | 745 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 19 | — |
| `kazma-tui/kazma_tui/documents.py` | 140 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/editor.py` | 416 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-tui/kazma_tui/files.py` | 158 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-tui/kazma_tui/header.py` | 79 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-tui/kazma_tui/memory_panel.py` | 487 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 30 | — |
| `kazma-tui/kazma_tui/nav_rail.py` | 187 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-tui/kazma_tui/screens/__init__.py` | 1 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/season_load.py` | 205 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 12 | — |
| `kazma-tui/kazma_tui/settings_panel.py` | 381 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-tui/kazma_tui/slash_complete.py` | 63 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/swarm.py` | 424 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 16 | — |
| `kazma-tui/kazma_tui/theme.py` | 488 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/themes/__init__.py` | 13 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/themes/theme_manager.py` | 241 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-tui/kazma_tui/traces.py` | 288 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-tui/kazma_tui/widgets/__init__.py` | 43 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/widgets/accessibility.py` | 500 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-tui/kazma_tui/widgets/command_bar.py` | 189 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-tui/kazma_tui/widgets/command_palette.py` | 449 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 17 | — |
| `kazma-tui/kazma_tui/widgets/confirm_dialog.py` | 119 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-tui/kazma_tui/widgets/hitl_modal.py` | 190 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-tui/kazma_tui/widgets/log_stream.py` | 139 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/widgets/model_picker.py` | 252 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `kazma-tui/kazma_tui/widgets/sparkline.py` | 94 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-tui/kazma_tui/widgets/status_bar.py` | 263 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-tui/kazma_tui/widgets/toast.py` | 112 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-tui/kazma_tui/widgets/tutorial.py` | 340 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-ui/kazma_ui/__init__.py` | 9 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/__main__.py` | 10 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/active_turns.py` | 305 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-ui/kazma_ui/agents.py` | 249 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-ui/kazma_ui/app.py` | 2816 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 125-180, 640-670, 2030-2080, 2270-2300 | 113 | AUD-025 |
| `kazma-ui/kazma_ui/auth.py` | 1601 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | full | 35 | AUD-014, AUD-021, AUD-022, AUD-023 |
| `kazma-ui/kazma_ui/browser_origins.py` | 70 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/calendar_api.py` | 110 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-ui/kazma_ui/chat.py` | 88 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/chat_attachments.py` | 124 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/commitment_api.py` | 96 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-ui/kazma_ui/csrf.py` | 83 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | full | 0 | — |
| `kazma-ui/kazma_ui/dashboard.py` | 510 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-ui/kazma_ui/delivery.py` | 482 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-ui/kazma_ui/documents_api.py` | 1127 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-ui/kazma_ui/email_api.py` | 731 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 370-420 | 15 | — |
| `kazma-ui/kazma_ui/gate_view.py` | 521 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `kazma-ui/kazma_ui/gateway_monitor.py` | 70 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/health.py` | 810 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 25 | — |
| `kazma-ui/kazma_ui/hitl_approval.py` | 268 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-ui/kazma_ui/hitl_decision.py` | 211 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/hitl_gate_bridge.py` | 555 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 23 | — |
| `kazma-ui/kazma_ui/hitl_status.py` | 297 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-ui/kazma_ui/hitl_timeout.py` | 359 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 12 | — |
| `kazma-ui/kazma_ui/i18n/__init__.py` | 181 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-ui/kazma_ui/i18n/catalog/__init__.py` | 47 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/agents.py` | 251 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/chat.py` | 1347 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/common.py` | 1915 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/dashboard.py` | 907 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/documents.py` | 558 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/knowledge.py` | 339 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/memory.py` | 2219 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/packages.py` | 367 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/research.py` | 443 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/scheduled.py` | 287 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/settings.py` | 5067 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/swarm.py` | 1667 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/tool.py` | 403 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/workspace.py` | 739 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/i18n/catalog/x_studio.py` | 302 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/ide_api.py` | 458 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 28 | — |
| `kazma-ui/kazma_ui/kb_api.py` | 540 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-ui/kazma_ui/mcp_presets.py` | 176 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/mcp_ui.py` | 427 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 19 | — |
| `kazma-ui/kazma_ui/memory_api.py` | 2437 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | all 27 SQL sites + 340-440, 2355-2410 | 90 | — |
| `kazma-ui/kazma_ui/metrics.py` | 198 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-ui/kazma_ui/models.py` | 430 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/models_route.py` | 215 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/providers.py` | 896 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 14 | — |
| `kazma-ui/kazma_ui/proxy_headers.py` | 71 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | full | 0 | — |
| `kazma-ui/kazma_ui/push.py` | 224 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-ui/kazma_ui/rate_limit.py` | 151 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-ui/kazma_ui/replay_routes.py` | 301 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-ui/kazma_ui/replica_affinity.py` | 76 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/reply_sink.py` | 666 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 10 | — |
| `kazma-ui/kazma_ui/research_panel/__init__.py` | 5 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/research_panel/routes.py` | 849 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 110-180, 780-835 | 25 | — |
| `kazma-ui/kazma_ui/routes/__init__.py` | 5 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/routes/ws_chat.py` | 2910 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 1355-1600 | 97 | AUD-026 |
| `kazma-ui/kazma_ui/routes/ws_graph.py` | 29 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/routes_chaos.py` | 179 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-ui/kazma_ui/routes_chat_upload.py` | 146 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 60-120 | 0 | — |
| `kazma-ui/kazma_ui/routes_direct/__init__.py` | 42 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/routes_direct/_shared.py` | 134 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/routes_direct/auth.py` | 319 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | full | 9 | AUD-002, AUD-003, AUD-020 |
| `kazma-ui/kazma_ui/routes_direct/backup.py` | 158 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-ui/kazma_ui/routes_direct/memory.py` | 1527 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | all 11 SQL sites | 99 | — |
| `kazma-ui/kazma_ui/routes_direct/misc.py` | 1202 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 36 | — |
| `kazma-ui/kazma_ui/routes_direct/settings.py` | 245 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `kazma-ui/kazma_ui/routes_direct/system.py` | 966 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 51 | AUD-016 |
| `kazma-ui/kazma_ui/routes_voice.py` | 483 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 1-110 | 7 | AUD-008, AUD-009 |
| `kazma-ui/kazma_ui/routes_voice_ws.py` | 587 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 22 | — |
| `kazma-ui/kazma_ui/saas_api.py` | 240 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-ui/kazma_ui/scheduled_api.py` | 370 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-ui/kazma_ui/services.py` | 335 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 12 | — |
| `kazma-ui/kazma_ui/session_manager.py` | 1411 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 20 | — |
| `kazma-ui/kazma_ui/session_spool.py` | 231 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/settings.py` | 1897 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 39 | — |
| `kazma-ui/kazma_ui/setup_api.py` | 136 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-ui/kazma_ui/skills_ui.py` | 455 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 17 | — |
| `kazma-ui/kazma_ui/sse_chat/__init__.py` | 2241 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 67 | AUD-026 |
| `kazma-ui/kazma_ui/sse_chat/_capacity.py` | 204 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-ui/kazma_ui/sse_chat/_helpers.py` | 211 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `kazma-ui/kazma_ui/sse_chat/_persistence.py` | 297 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `kazma-ui/kazma_ui/sse_chat/_streaming.py` | 1760 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 24 | — |
| `kazma-ui/kazma_ui/sse_chat/_time_travel.py` | 63 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/sse_utils.py` | 210 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/static/css/fonts.css` | 30 | not security-relevant (no expressions) | — | 0 | — |
| `kazma-ui/kazma_ui/static/css/kazma.css` | 6611 | not security-relevant (no expressions) | — | 0 | — |
| `kazma-ui/kazma_ui/static/css/kazma.v5.css` | 686 | not security-relevant (no expressions) | — | 0 | — |
| `kazma-ui/kazma_ui/static/font-preview.html` | 118 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/agents.js` | 133 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/app.js` | 56 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/auth-guard.js` | 157 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/bidi.js` | 301 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/chat.js` | 8634 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/chat_slash.js` | 38 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/dash_lists.js` | 99 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/dashboard.js` | 723 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/documents.js` | 687 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/hitl_approval.js` | 460 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/icons.js` | 279 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | lines 200-235 | 0 | — |
| `kazma-ui/kazma_ui/static/js/ide.js` | 1280 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | lines 1225-1262 | 0 | — |
| `kazma-ui/kazma_ui/static/js/kb.js` | 406 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/locale_format.js` | 119 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/mcp.js` | 560 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/memory.js` | 832 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/memory_console.js` | 4702 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/models.js` | 117 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/components.js` | 462 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/delivery_cursor.js` | 80 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/nav.js` | 695 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/push_client.js` | 64 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/search_pages.js` | 20 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/stores.js` | 547 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/turn_document.js` | 1128 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/turn_preferences.js` | 233 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/turn_presentation.js` | 305 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/turn_view.js` | 746 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/turn_visibility.js` | 159 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/modules/util.js` | 152 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/providers.js` | 319 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/replay.js` | 357 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/research.js` | 1046 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/scheduled.js` | 518 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/settings.js` | 39 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/settings_agent.js` | 859 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/settings_core.js` | 842 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/settings_hub.js` | 1285 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/settings_integrations.js` | 1368 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/settings_ops.js` | 1120 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/skills.js` | 179 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/stores/agentStore.js` | 1334 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/streaming.js` | 972 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | lines 380-760 | 0 | AUD-018 |
| `kazma-ui/kazma_ui/static/js/swarm.js` | 3122 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/turn_detail.js` | 130 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/voice.js` | 853 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/js/x_studio.js` | 551 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/static/sw.js` | 28 | node --check (CI), API-call/route cross-ref, sink grep (innerHTML/x-html/redirect) | — | 0 | — |
| `kazma-ui/kazma_ui/swarm_panel/__init__.py` | 142 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-ui/kazma_ui/swarm_panel/routes_general.py` | 569 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `kazma-ui/kazma_ui/swarm_panel/routes_metrics.py` | 37 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/swarm_panel/routes_tasks.py` | 856 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `kazma-ui/kazma_ui/swarm_panel/routes_workers.py` | 527 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `kazma-ui/kazma_ui/swarm_sse.py` | 307 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/telemetry_route.py` | 105 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `kazma-ui/kazma_ui/templates/agents.html` | 302 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/base.html` | 291 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/chat.html` | 537 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/components/header.html` | 127 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/components/memory_console.html` | 414 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/components/modal.html` | 92 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/components/sidebar.html` | 238 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/components/toast.html` | 42 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/dashboard.html` | 462 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/documents.html` | 359 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/error.html` | 36 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/ide.html` | 475 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/knowledge_base.html` | 260 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/login.html` | 214 | directive/sink grep (x-html, |safe), API-call cross-ref | lines 138-190 | 0 | AUD-019 |
| `kazma-ui/kazma_ui/templates/mcp.html` | 188 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/memory.html` | 463 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/replay.html` | 90 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/research.html` | 161 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/scheduled.html` | 311 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/settings.html` | 3956 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/skills.html` | 180 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/swarm.html` | 834 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/workspace.html` | 1660 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/templates/x_studio.html` | 286 | directive/sink grep (x-html, |safe), API-call cross-ref | — | 0 | — |
| `kazma-ui/kazma_ui/thread_ownership.py` | 135 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `kazma-ui/kazma_ui/turn_document.py` | 806 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `kazma-ui/kazma_ui/turn_liveness.py` | 233 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `kazma-ui/kazma_ui/turn_runtime.py` | 479 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 21 | — |
| `kazma-ui/kazma_ui/turn_usage.py` | 69 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `kazma-ui/kazma_ui/voice_turn.py` | 424 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 19 | — |
| `kazma-ui/kazma_ui/workspace_api.py` | 318 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `kazma-ui/kazma_ui/x_api.py` | 415 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 13 | — |
| `kazma-ui/kazma_ui/x_reply_api.py` | 680 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/agentdojo_bench.py` | 1199 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `scripts/backup_kazma.py` | 100 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `scripts/certify_documents.py` | 75 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `scripts/check_docs_sync.py` | 276 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `scripts/check_fresh_imports.py` | 144 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `scripts/ci/lint-absolute-paths.sh` | 35 | shell/PowerShell: reviewed for secrets and shell use by grep | — | 0 | — |
| `scripts/cleanup_live_leftovers.py` | 485 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 14 | — |
| `scripts/docker-entrypoint.sh` | 11 | shell/PowerShell: reviewed for secrets and shell use by grep | — | 0 | — |
| `scripts/eval_pack.py` | 33 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/fast_test.py` | 630 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | full | 5 | — |
| `scripts/fix-cloudflare-tunnel-tasks.ps1` | 112 | shell/PowerShell: reviewed for secrets and shell use by grep | — | 0 | — |
| `scripts/generate_env_reference.py` | 198 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 4 | — |
| `scripts/generate_metrics.py` | 1116 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `scripts/generate_tools_catalog.py` | 249 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `scripts/industry_smoke.ps1` | 18 | shell/PowerShell: reviewed for secrets and shell use by grep | — | 0 | — |
| `scripts/injection_live.py` | 810 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `scripts/injection_report.py` | 111 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/light_version_bump.py` | 311 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `scripts/live_api_smoke.py` | 231 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `scripts/mcp_probe.py` | 93 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `scripts/memory_bench.py` | 900 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `scripts/memory_smoke.ps1` | 17 | shell/PowerShell: reviewed for secrets and shell use by grep | — | 0 | — |
| `scripts/migrate_offsite_repo.py` | 342 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `scripts/migrate_sqlite_to_postgres.py` | 275 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 7 | — |
| `scripts/pg_backup.py` | 133 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `scripts/postgres_suite.py` | 92 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/provider_conformance.py` | 450 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 9 | — |
| `scripts/reconcile_memory_mirror.py` | 76 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `scripts/reembed.py` | 205 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `scripts/replace_ui_emojis.py` | 160 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `scripts/repro_server_smoke.py` | 156 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/restore_kazma.py` | 98 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/restore_rehearsal.py` | 281 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 8 | — |
| `scripts/run_gates.py` | 94 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/scan_old_sessions.py` | 471 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 11 | — |
| `scripts/service/install_service.py` | 551 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 100-300 | 3 | — |
| `scripts/service/kazma_guard.py` | 2756 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | lines 1560-1620, 1750-1800, 1880-1925 | 105 | — |
| `scripts/shipped_defaults.py` | 123 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `scripts/site_screenshots.py` | 83 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `scripts/smoke_production.py` | 146 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 13 | — |
| `scripts/smoke_research_deep.py` | 73 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `scripts/smoke_research_stack.py` | 156 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/smoke_topic_shift_p0.py` | 99 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 0 | — |
| `scripts/start-web.sh` | 58 | shell/PowerShell: reviewed for secrets and shell use by grep | — | 0 | — |
| `scripts/sync_site_metrics.py` | 314 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/trim_budget_report.py` | 122 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 1 | — |
| `scripts/update_github_issues.sh` | 71 | shell/PowerShell: reviewed for secrets and shell use by grep | — | 0 | — |
| `scripts/verify_documents.py` | 215 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 3 | — |
| `scripts/verify_docx_rtl.py` | 156 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
| `scripts/verify_v2_coverage.py` | 206 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 6 | — |
| `scripts/website_sync_plan.py` | 525 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 5 | — |
| `scripts/wsl_fixed_access.ps1` | 183 | shell/PowerShell: reviewed for secrets and shell use by grep | — | 0 | — |
| `serve.py` | 145 | ruff 38 families, bandit, AST (SQL/log/closure/deps/routes) | — | 2 | — |
