Kazma Project Enhancement Task Plan

This plan covers all proposed enhancements from my previous analysis. It builds directly on the completed audit remediation work (P0 security closures, graph coherence, services facade, swarm_panel scaffolding, error-handling sweeps, and the recent ultimate overhaul).

The plan is phased for realism, with clear dependencies, effort estimates, and strict adherence to project rules:
• All work directly on main (no feature branches).
• py_compile validation on every edited/created file before any commit.
• Use of todo_write for tracking.
• Targeted tests + full collection checks where relevant.
• Public API backward compatibility preserved.
• Focus on high-impact items from the canonical audit reports (maintainability debt, god modules, error handling, test gaps, docs).

Guiding Principles
• Prioritization: Short-term wins first (leverage recent facade + split work). Then medium, then strategic.
• Risk Management: Small, reviewable changes. Always validate compile + relevant tests.
• Metrics of Success: Reduce broad except Exception below 80; complete at least one major god-module split; increase test coverage on new public surfaces; keep UI/API unchanged.
• Tracking: All tasks managed via the project's todo system (updated live).

Phase 1: Short-term High-Impact (1–2 sprints)
Goal: Consolidate recent gains and close quick maintainability wins.

┌──────┬────────────────┬─────────────────────────────┬─────────────────┬────────┬───────────────┬─────────────────┐
│ ID   │ Task           │ Description                 │ Files / Areas   │ Effort │ Dependencies  │ Validation      │
├──────┼────────────────┼─────────────────────────────┼─────────────────┼────────┼───────────────┼─────────────────┤
│ ST-1 │ Complete       │ Finish extracting remaining │ kazma-ui/kazma  │ Medium │ Recent        │ py_compile on   │
│      │ swarm_panel.py │ route logic (general        │ _ui/swarm_panel │        │ partial split │ all; targeted   │
│      │ decomposition  │ routes, full task dispatch, │ .py, swarm_     │        │ (TODO +       │ swarm panel     │
│      │                │ SSE hooks) into swarm_      │ panel/*.py, __  │        │ scaffolding)  │ tests; manual   │
│      │                │ panel/ submodules. Add      │ init__.py       │        │               │ UI smoke        │
│      │                │ routes_general.py or routes │                 │        │               │                 │
│      │                │ _sse.py if needed. Update   │                 │        │               │                 │
│      │                │ SwarmRouterBuilder and __   │                 │        │               │                 │
│      │                │ init__.py.                  │                 │        │               │                 │
├──────┼────────────────┼─────────────────────────────┼─────────────────┼────────┼───────────────┼─────────────────┤
│ ST-2 │ Targeted       │ Reduce broad except         │ app.py, agent_  │ Low-   │ ST-1 (for     │ py_compile;     │
│      │ error-handling │ Exception: and silent       │ handler.py, new │ Medium │ panel files)  │ count reduction │
│      │ purge          │ passes. Target hot paths.   │ panel routers,  │        │               │ verification;   │
│      │                │ Replace with typed          │ llm_provider    │        │               │ relevant unit   │
│      │                │ exceptions or logger.       │ .py, compaction │        │               │ tests           │
│      │                │ debug(..., exc_info=True) + │ .py, others     │        │               │                 │
│      │                │ comments. Aim < 80 total    │                 │        │               │                 │
│      │                │ broad catches.              │                 │        │               │                 │
├──────┼────────────────┼─────────────────────────────┼─────────────────┼────────┼───────────────┼─────────────────┤
│ ST-3 │ Expand         │ Move more logic (metrics    │ kazma-ui/kazma  │ Low    │ None (builds  │ py_compile; new │
│      │ services.py    │ aggregation, additional     │ _ui/services    │        │ on recent     │ facade tests (  │
│      │ facade         │ serialization, config       │ .py, all UI     │        │ facade)       │ see ST-4)       │
│      │                │ access) behind get_swarm_   │ callers (panel, │        │               │                 │
│      │                │ service(). Migrate any      │ metrics, app)   │        │               │                 │
│      │                │ remaining hasattr / ._      │                 │        │               │                 │
│      │                │ fallbacks.                  │                 │        │               │                 │
├──────┼────────────────┼─────────────────────────────┼─────────────────┼────────┼───────────────┼─────────────────┤
│ ST-4 │ Add regression │ Cover dynamic graph holder/ │ tests/test_     │ Low    │ ST-1, ST-3    │ pytest          │
│      │ + integration  │ getter behavior, facade     │ swarm_engine    │        │               │ collection +    │
│      │ tests          │ methods (list_workers, task │ _core.py,       │        │               │ run; full 3497+ │
│      │                │ handles, set_sse_bus), and  │ tests/test_ui_  │        │               │ items still     │
│      │                │ recent HITL/WS paths.       │ services.py,    │        │               │ healthy         │
│      │                │                             │ new test_sse_   │        │               │                 │
│      │                │                             │ graph_coherence │        │               │                 │
│      │                │                             │ .py or similar  │        │               │                 │
└──────┴────────────────┴─────────────────────────────┴─────────────────┴────────┴───────────────┴─────────────────┘

Phase 1 Milestones: All short-term tasks closed. Broad exceptions visibly reduced. Public facade usage consistent. Tests passing.

Phase 2: Medium-term (2–4 sprints)
Goal: Address core maintainability and quality gaps identified in the audits.

┌──────┬────────────────┬───────────────────────────┬────────────────────┬────────┬───────────────┬────────────────┐
│ ID   │ Task           │ Description               │ Files / Areas      │ Effort │ Dependencies  │ Validation     │
├──────┼────────────────┼───────────────────────────┼────────────────────┼────────┼───────────────┼────────────────┤
│ MT-1 │ Continue god-  │ Split agent_handler.py (  │ kazma-gateway      │ Medium │ ST-1 (pattern │ py_compile;    │
│      │ module         │ slash commands, swarm     │ /kazma_gateway     │ -High  │ from panel    │ gateway + UI   │
│      │ decomposition  │ dispatch, HITL resume).   │ /agent_handler.py, │        │ split)        │ tests          │
│      │                │ Begin light split on app. │ kazma-ui/kazma_ui/ │        │               │                │
│      │                │ py (lifecycle vs          │ app.py             │        │               │                │
│      │                │ routers).                 │                    │        │               │                │
├──────┼────────────────┼───────────────────────────┼────────────────────┼────────┼───────────────┼────────────────┤
│ MT-2 │ Strengthen     │ Add browser/E2E layer (   │ tests/, new tests/ │ Medium │ None          │ pytest + E2E   │
│      │ testing story  │ Playwright) for chat +    │ e2e/, test_multi_  │        │               │ runs           │
│      │                │ HITL flows. Expand multi- │ tenant_isolation   │        │               │                │
│      │                │ tenant tests. Add         │ .py, test_settings │        │               │                │
│      │                │ dedicated coverage for    │ .py                │        │               │                │
│      │                │ settings_manager.py and   │                    │        │               │                │
│      │                │ optional RAG paths.       │                    │        │               │                │
├──────┼────────────────┼───────────────────────────┼────────────────────┼────────┼───────────────┼────────────────┤
│ MT-3 │ Documentation  │ Update architecture.md    │ architecture.md,   │ Low    │ ST-3          │ Manual review  │
│      │ & architecture │ with current LOCs, new    │ README.md, docs/   │        │               │ + build of     │
│      │ refresh        │ facade, and subpackage    │                    │        │               │ docs site      │
│      │                │ structure. Reconcile RAG  │                    │        │               │                │
│      │                │ story in README. Add "    │                    │        │               │                │
│      │                │ Public APIs & Facades"    │                    │        │               │                │
│      │                │ section.                  │                    │        │               │                │
├──────┼────────────────┼───────────────────────────┼────────────────────┼────────┼───────────────┼────────────────┤
│ MT-4 │ Dependency &   │ Make RAG/ChromaDB truly   │ pyproject.toml,    │ Medium │ None          │ Install tests  │
│      │ portability    │ optional with clear docs. │ README.md, kazma-  │        │               │ in minimal     │
│      │ hardening      │ Declare python-dotenv     │ core/kazma_core/   │        │               │ env; Windows-  │
│      │                │ explicitly if used.       │ tools/code_exec.py │        │               │ specific       │
│      │                │ Improve Windows code_exec │                    │        │               │ checks         │
│      │                │ sandbox (job objects,     │                    │        │               │                │
│      │                │ restricted PATH).         │                    │        │               │                │
└──────┴────────────────┴───────────────────────────┴────────────────────┴────────┴───────────────┴────────────────┘

Phase 2 Milestones: Additional modules decomposed. Measurable test coverage gains. Docs accurate to current state. Core dependencies cleaned up.

Phase 3: Longer-term / Strategic (Ongoing + 3+ sprints)
Goal: Strategic improvements for reliability, DX, and ecosystem growth.

┌──────┬────────────────┬────────────────────────────┬──────────────────┬────────┬──────────────┬──────────────────┐
│ ID   │ Task           │ Description                │ Files / Areas    │ Effort │ Dependencies │ Validation       │
├──────┼────────────────┼────────────────────────────┼──────────────────┼────────┼──────────────┼──────────────────┤
│ LT-1 │ Observability  │ Promote tracing/metrics to │ kazma-core/      │ Medium │ MT-1, MT-2   │ Observability    │
│      │ & reliability  │ pluggable system. Add      │ kazma_core/      │ -High  │              │ dashboards; load │
│      │ upgrades       │ circuit-breaker/handoff    │ tracing.py,      │        │              │ tests            │
│      │                │ visualization in UI.       │ metrics, swarm_  │        │              │                  │
│      │                │ Structured logging +       │ panel/, UI JS    │        │              │                  │
│      │                │ correlation IDs across     │                  │        │              │                  │
│      │                │ graph/swarm/gateways.      │                  │        │              │                  │
├──────┼────────────────┼────────────────────────────┼──────────────────┼────────┼──────────────┼──────────────────┤
│ LT-2 │ Developer      │ Implement (or prototype)   │ kazma-ui/, new   │ High   │ LT-1         │ ROADMAP item     │
│      │ experience &   │ visual pipeline editor (   │ editor           │        │              │ closure; user    │
│      │ extensibility  │ drag-and-drop DAG).        │ components,      │        │              │ testing          │
│      │                │ Improve skill/SDK          │ kazma-skills/    │        │              │                  │
│      │                │ scaffolding.               │                  │        │              │                  │
├──────┼────────────────┼────────────────────────────┼──────────────────┼────────┼──────────────┼──────────────────┤
│ LT-3 │ Security &     │ Schedule periodic re-      │ Security         │ Medium │ None         │ New audit        │
│      │ compliance     │ audits. Implement          │ modules, docs,   │        │              │ report; security │
│      │ continuation   │ capability disclosure /    │ auth.py          │        │              │ tests            │
│      │                │ audit-trail features (if   │                  │        │              │                  │
│      │                │ still stubbed). Explore    │                  │        │              │                  │
│      │                │ layered auth (JWT on top   │                  │        │              │                  │
│      │                │ of shared secret) for      │                  │        │              │                  │
│      │                │ multi-user deploys.        │                  │        │              │                  │
├──────┼────────────────┼────────────────────────────┼──────────────────┼────────┼──────────────┼──────────────────┤
│ LT-4 │ Ecosystem &    │ Improve CLI/TUI ↔ Web UI   │ kazma-cli/,      │ Medium │ None         │ Deployment smoke │
│      │ distribution   │ integration (shared        │ kazma-tui/,      │        │              │ tests; docs      │
│      │                │ config, remote control).   │ Dockerfiles,     │        │              │ completeness     │
│      │                │ Official Helm chart +      │ docs/            │        │              │                  │
│      │                │ better K8s/Docker support. │                  │        │              │                  │
│      │                │ Publish "Production        │                  │        │              │                  │
│      │                │ Checklist" derived from    │                  │        │              │                  │
│      │                │ audits.                    │                  │        │              │                  │
└──────┴────────────────┴────────────────────────────┴──────────────────┴────────┴──────────────┴──────────────────┘

Phase 3 Milestones: Key strategic ROADMAP items addressed. Stronger observability and DX. Project ready for broader adoption.

Cross-Cutting Execution Rules (apply to every task)
• Direct on main only.
• Pre-commit validation: Run & '.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'<file>', doraise=True); print('OK')" on every modified or new file.
• Testing: Run targeted tests + full pytest --collect-only before commits. Update CHANGELOG.md for each phase.
• Tracking: Use todo_write to mark progress on the tasks above.
• Compatibility: Never break public router signatures or existing UI behavior.
• Scope Control: Deliver in small, reviewable chunks. Pause for user feedback between phases if needed.

Sequencing & Dependencies
1. Phase 1 (ST-1 → ST-4) — do in parallel where possible (tests can start while splitting).
2. Phase 2 builds on Phase 1 patterns.
3. Phase 3 is more open-ended — can run in parallel with Phase 2 for strategic items.

Risks & Mitigations
• Large refactors (splits): Mitigate with incremental PRs (even on main) and heavy use of fallbacks during transition.
• Test env issues (Windows temp permissions): Mitigate by focusing on collection + unit tests first.
• Scope creep: Stick strictly to the listed tasks. Revisit long-term items after Phase 1/2.

This plan is now tracked in the project's todo system (see active todo_write entries).

Next action?
Tell me which phase/task to start first (e.g., "begin ST-1" or "do MT-3 docs refresh"), and I'll proceed directly on main with the required validations, todo updates, and commits.

Would you like me to expand any task with detailed sub-steps, file-by-file breakdown, or a Gantt-style timeline?