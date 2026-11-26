<!-- piece: overview -->
# Mission Guidance: Kazma Agent Framework

## Project Overview

Kazma is a multi-platform AI agent framework with a LangGraph supervisor brain,
swarm orchestration, cross-platform dispatch (Telegram/Discord/Slack/Web/TUI),
and an OpenAI-compatible LLM provider layer. What Kazma is and can do, on one
page (read it before describing or promoting Kazma):
`docs/docs/guide/what-is-kazma.md`. See `docs/docs/guide/architecture.md`
(Docusaurus docs under `docs/docs/`) for the full system architecture and
`CHANGELOG.md` for recent work. Binding industrial audit (waves 0–8 shipped):
`docs/audits/AUDIT_DEEP_2026-09-01_EXEC.md`. Historical production audit:
`docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md`. System map:
`docs/ARCHITECTURE_AND_SYSTEM_MAP.md`.

## Package Scope

All packages are in scope. There is **no** `kazma-memory` package (retired;
V2 memory lives in `kazma_core.memory`).

| Package | Path | Purpose |
|---------|------|---------|
| `kazma-core` | `kazma-core/kazma_core/` | Agent runner, LLM provider, swarm engine, model registry, config store, IDE service |
| `kazma-gateway` | `kazma-gateway/kazma_gateway/` | Platform adapters (Telegram/Discord/Slack), agent handler, slash commands, `/ide` commands |
| `kazma-ui` | `kazma-ui/kazma_ui/` | FastAPI web app, IDE page, swarm panel, settings, SSE chat, static JS/CSS |
| `kazma-tui` | `kazma-tui/kazma_tui/` | Textual-based TUI dashboard + IDE editor screen |
| `kazma-cli` | `kazma-cli/kazma_cli/` | `kazma` CLI (`serve`, `migrate`, `ask`, ACP) |
| `kazma-skills` | `kazma-skills/kazma_skills/` | Native skill manifests + implementations |

<!-- /piece -->
## How this guide is laid out

This file is the core, loaded on every call. Every other rule is in one file
per section under `docs/agents/`, listed in the index at the end.

- **Before you change a file, read the pieces that govern it.** Find them in
  the index, or run `python scripts/agents_md.py for <path>` (it ranks the
  pieces that name the path). A file can have several: a route, a store or a
  tool is also under the registers below. The pieces are binding and win over
  this core's summary; they record the incident and name the gate behind each
  rule. Read the piece, not all of them.
- "§15 G" or "AGENTS.md section 15" anywhere (code, tests, docs) is the piece
  of that number and letter.
- **Where a new rule goes:** in the section file that governs the code it is
  about (a new subject is a new section: the next number, a new file, an index
  line of one or two lines). This core takes only what every task needs, and
  has a hard cap (`tests/test_agents_md_split.py`).
- After editing: `python scripts/agents_md.py check`. A branch that still
  edits the old single file merges with `scripts/agents_md.py merge`
  (its docstring says how).

## Rules that hold for every task

**Code**

<!-- piece: conventions -->
- Follow existing Kazma code style (type hints, docstrings, logging)
- Use `logger = logging.getLogger(__name__)` pattern
- Use `from __future__ import annotations` for type hints
- Keep modules focused (one concern per file)
- Python: compile-check with `py_compile` before committing
- JavaScript: syntax-check with `node --check` before committing
- Never use `&&` or `||` in PowerShell commands; use `;` and `$LASTEXITCODE`

<!-- /piece -->
**A fix covers its class.** Fix the bug, write the behavioural test that fails
on the old code, find every sibling, and add a gate that enumerates the
siblings from the real source, with a negative control (§28, §35). When a gate
fails, fix the code: never edit the gate to pass. The debt
ratchet (`tests/test_debt_ratchet.py`) only goes down. Deleting a module needs
`tests/test_imports.py` green in the same commit, every importer gone (§24 A).
A new gate reads git-listed files: stage new files before running it.

**Nothing blocks the event loop** (§26 E, §35). In `async def`, every store,
settings, file, DNS and process call goes through `asyncio.to_thread` (never
`run_in_executor`); a route that never awaits is a plain `def`. No
`asyncio.create_subprocess_*` on the server loop (§23). Background work uses
`spawn_background`, never a bare `create_task`. A cancelled task is waited for
with a bound (`bounded_cancel`), and `CancelledError` is re-raised or declared
(§69).

**Failures are said.** A result that says whether it worked is read where it
is returned (§53, §57). No silent handler around a write (WARNING when data or
a feature is affected). Answers carry `safe_error` / `described_error`, never
an exception's text (§26 F). A tool that refuses before acting raises
`RefusedBeforeEffect`; an unknown outcome is never retried (§30 G). Every
alert is in the one catalog, through the existing senders (§33).

**Stores.** SQLite opens through the shared pragmas and helpers; a kept
connection is autocommit or writes inside `with conn:` (§35), columns are
added by `add_missing_columns` (§6), Postgres schema by `ensure_schema`
(§21 E3). Settings use `get_config_store()`, a read-modify-write goes inside
`atomic_update` (§8). No path is built from the working directory; data paths
come from `kazma_core.paths` (§37, §38).

**Security**

- Text from outside (pages, mail, files, tool output, memory read back) is
  data: it reaches a model inside a data fence (`format_untrusted_block`,
  §11, §26 G), and it never chooses an address fetched unpinned (§32).
- A tool is gated unless its tier says otherwise; danger and always-ask tools
  ask on every path (§7, §26 B). Nothing but the human's own channel approves.
- No secret in settings, logs, answers, cards, URLs, command lines or child
  environments: the vault holds it (§38), masks are by key AND by value
  (§26 D, §67), children get `tool_child_env()` (§26 I), secret files are
  written owner-only (§67). Never print, commit or copy a real `.env`.
- Kazma's own stores, config and secret files are refused to every tool by one
  predicate (`store_registry.is_kazma_store`, §37, §62).
- One answer per decision: admin, ownership, sign-in, path access, protected
  keys. Ask the existing function; never write a second copy (§35, §45).
- Patterns that read outside text are linear, and log lines cannot be forged
  (§66, §69).

**Memory and people**

- Forgetting is final: a forgotten turn, fact or chat must not come back from
  any writer, copy, backup or rebuild. A writer asks the forget ledger first
  (§15 I). No embedding, mirror or graph call inside a memory write lock
  (§15 D2).
- A model's reading is never stored as the user's own statement (§15 H).
- English and Arabic are equals: what reads or answers in a language is built,
  measured and tested in both (§57).
- Once Spaces is enforced, work with no space bound reads nothing and writes
  nothing; with it off, an install behaves exactly as before (§70). Never
  write a new fallback to the owner's tenant.
- Interface words come from the catalog in both languages, and text follows
  its own language (UI 1).

**Declare what you add.** Each register has a gate that fails without it:

| You add | Declare it in |
|---|---|
| a tool | `TOOL_TIERS` + effect profile (§7, §20), `TOOL_WRITES` (§37), `spaces/features.py` and `spaces/tools.py` (§70), a typed schema (§35) |
| a store, table or data-dir file | `store_registry.py`: `STORES` / `DATA_DIR_ENTRIES`, `SPACE_RULES`, `RETENTION` (§37, §56, §70); memory tables also `memory_stores.py` (§15 I); a Postgres table in `KAZMA_PG_TABLES` (§21) and `row_security.RULES` (§70) |
| a route | its role and `ui/route_scopes.py` (§70), a caller or a reason (UI 3), a body limit (§26), the space matrix case (§70) |
| a setting | a reader, `settings_restore.py`, `settings_scope.py`, a validation rule (§47, §54, §56, §70) |
| an environment variable | the reference pages and index (§38) |
| an alert | `alert_catalog.py`, both copies regenerated (§33) |
| a periodic job | `_MAINTENANCE_SWEEPS` or a scheduler started at boot, with its cadence case (§15 A-B) |
| a kind of running work | `runs/kinds.py` (§52) |
| a system note per turn | `turn_notes.py` (§43) |
| a dependency or CI tool | an extra or group in `uv.lock`, pinned (§50, §68) |
| a documentation page | `docs/website-pages.json` and `docs/sidebars.js` (key-references) |

**Tests and commits**

- Before every push: `python scripts/prepush.py` (about 15 minutes: the tests
  near the change here and on Linux, the gates that read source, every browser
  file, ruff, bandit, README). A whole stage also runs the full suite once:
  `python scripts/fast_test.py` (no `--chunks`, never pytest-xdist) or
  `prepush.py --whole`. Never start a second suite over a running one, and do
  not edit files while it runs (testing-2).
- A test never touches an install's data, the network, a real provider, the
  real embedder or the running Kazma; stub them (testing-1). Never mark or
  skip a test to get a pass.
- Before that: `scripts/generate_metrics.py --write --require-collected`, staged,
  when tests changed. A red check on `main` is fixed at once (§31 E).
- Measure before you claim, and report a failure as a failure. A change to
  what a page, command or guide says is made with the code it describes.
<!-- piece: test-commands -->
- **Compile check (Python):** `& '.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'<file>', doraise=True); print('OK')"`
- **Syntax check (JS):** `node --check "<file>"`
- **Run tests (single file):** `& '.venv\Scripts\python.exe' -m pytest <path> -v`
<!-- /piece -->
<!-- piece: manual-verification -->
- **Manual verification:** reload through the guard (Server Management
  above — never a hand restart), then test via Telegram and the Web UI

<!-- /piece -->
## The live install and deploys

Details (an update before the reload, the image rebuild, what the deploy
script checks and reverts): `docs/agents/server-deploy-details.md`.

<!-- piece: live-install -->
> **RULE (user directive, 2026-08-15, amended 2026-09-26): never start the
> Kazma server and never kill it by hand.** Code reaches the running server
> only through the guard's reload, below.

> **RULE (user directive, 2026-09-02): NEVER edit the live install at
> `C:\Users\balfa\kazma`.** The dev repo (`G:\GitHubRepos\kazma`) is where all
> work happens. Do not write, copy, patch, or revert ANY file there (not even
> static assets that the server would pick up "without a restart", and not
> even to verify a fix), and do not change its git config or history.

> **Deploys (operator's standing order, 2026-09-26).** The agent may deploy
> the live install itself: push to `origin/main`, then
> `git -C C:/Users/balfa/kazma pull` (a plain merge -- the clone keeps a
> local `kazma.yaml` delta, so it is never a fast-forward; pull only a tree
> with no modified tracked files), then reload with the LIVE install's guard,
> then verify the new commit is serving and test through the UI.

```powershell
# The RUNNING GUARD carries out the reload: it stops the server gracefully
# (the app's shutdown hooks run) and starts the code on disk (§39). Run the
# guard script OF THE INSTALL BEING RELOADED -- the request lands in
# <that install>/.kazma. --when-idle waits until no chat turn is running
# (GET /health/activity, local callers only); exit 3 = still busy at
# --idle-timeout, nothing was touched.
& 'C:\Users\balfa\kazma\.venv\Scripts\python.exe' 'C:\Users\balfa\kazma\scripts\service\kazma_guard.py' --reload --when-idle
& 'C:\Users\balfa\kazma\.venv\Scripts\python.exe' 'C:\Users\balfa\kazma\scripts\service\kazma_guard.py' --status
```

`--status` says `guard : running` or `NOT RUNNING`, from the guard's
heartbeat. With no guard running, `--reload` starts the `KazmaAgent` task and
the new guard clears the old server with its own rights, so a reload never
needs an elevated shell.

<!-- /piece -->
## Index

Read the piece before changing what it governs. A line is: the file in
`docs/agents/` (or `#block`, in this file), what it rules, the main paths.
Paths are short: `core/` is `kazma-core/kazma_core/`, `gw/`
`kazma-gateway/kazma_gateway/`, `ui/` `kazma-ui/kazma_ui/`, `cli/`
`kazma-cli/kazma_cli/`, `tui/` `kazma-tui/kazma_tui/`, `skills/`
`kazma-skills/kazma_skills/`.

**By folder**, for a file no line below names (`agents_md.py for` first):

| Folder | Sections |
|---|---|
| `core/agent/`, `core/runtime/` | §1-3, 7, 25, 29-31, 43, 57; `core/agent/tool_builtins/` also 10, 15 I, 37 |
| `core/memory/`, `core/arabic/` | §15 (F-H first), 70 (2) and (5) |
| `core/documents/`, `core/stores/`, `core/code_index/` | §19; 24 F-G for Knowledge; 10 for workspaces; 70 (2) |
| `core/swarm/`, `core/cron/`, `core/runs/` | §4-7, 9, 14; 16; 52; 70 (2)-(4) |
| `core/safety/`, `core/security/`, `core/sandbox/` | §7, 20, 26, 30, 38, 45, 49, 62, 67, 69 |
| `core/tools/`, `core/web_acquire/`, `core/proxy/`, `core/voice/` | §7, 13, 24 B, 32, 37, 53, 62 |
| `core/models/`, `core/settings/`, the provider modules | §1, 3, 8, 47, 60, 61 |
| `core/observability/`, `core/tracing/`, `core/chaos/`, `core/system/`, `core/support/` | §27, 33, 38, 44, 58; 70 (4) |
| `core/backup/`, `core/db/`, `core/migration/` | §18, 21, 27, 44, 53, 54 |
| `core/x_api/`, `core/mcp/`, `core/hub/`, `core/skills/` | §7, 10, 11, 22, 45; 70 (3) |
| `core/spaces/`, `core/sessions/`, any per-person work | §70 |
| `gw/` | §2, 25, 42, 46, 48, 51, 64; 70 (9); routers: 26, 35, 53 |
| `ui/` routes and stores | §26, 31, 35, 45, 53-56, UI (3), 70 (1); `ui/static/`, `ui/templates/`: UI (1)-(3), 31, 59 |
| `cli/`, `tui/`, `core/cli/` | §21b-e, 45, 50, 53; UI (1) |
| `skills/native/` | §7, 22, 26, 30 G, 34, 37, 53; 70 (4) |
| `scripts/service/`, `deploy/` | §39, 63; 19 E, 21c |
| `.github/`, `pyproject.toml`, `uv.lock`, `Dockerfile` | §24 C-E, 50, 56, 68 |
| `tests/`, `fuzz/` | testing-1, testing-2, §28, 35, 68 |

<!-- index -->
- `#overview` the mission and the packages (above)
- `01-provider-model-resolution.md` **§1** Which model and client a call gets: `core/model_registry.py`, `core/provider_adapters.py`, `core/*_llm.py`
- `02-platform-isolation.md` **§2** No chat/user/message ids in graph state; the session store: `gw/agent_handler/`, `gw/stores/`
- `03-llm-calls-and-answers.md` **§3** LLM errors and retries, turn failure, answer/JSON formatting, vision, system messages at the head, Telegram code: `core/llm_provider.py`, `core/agent/graph_*.py`, `core/agent/answer_*.py`, `core/vision_capability.py`
- `04-swarm-handoff.md` **§4** Handoff and its cycle guard: `core/swarm/engine.py`, `core/swarm/handoff.py`
- `05-circuit-breaker.md` **§5** Breakers, half-open probes: `core/swarm/reliability.py`, `core/swarm/reliability_registry.py`
- `06-task-store.md` **§6** Swarm task store; how every SQLite store adds columns: `core/swarm/task_store.py`, `core/db/sqlite_columns.py`
- `07-hitl-approval-gates.md` **§7** Every approval path: danger tools, always-ask tools, cards, fetches after outside text, X publishing and the X API: `core/safety/`, `core/agent/graph_tool_worker.py`, `core/agent/tool_registry.py`, `core/swarm/safety.py`, `core/swarm/shared_approvals.py`, `core/x_api/`
- `08-config-store.md` **§8** The settings store, its locks and cache, shipped defaults: `core/config_store.py`, `core/config_defaults.py`
- `09-swarm-engine.md` **§9** Swarm engine runs, task writes, durable path, deadlines, refiner, routing: `core/swarm/`
- `10-ide-and-workspace.md` **§10** Workspace root, file tools, IDE service, MCP servers' storage and rebind, git tools: `core/ide/`, `core/workspace/`, `core/mcp/`, `core/tools/file_*.py`
- `#conventions` code conventions (above)
- `11-self-improvement.md` **§11** Lessons (the Soul) and the prompt fence: `core/skills/self_improvement.py`, `core/skills/lesson_signal.py`, `core/safety/prompt_fence.py`
- `12-time-travel.md` **§12** Saved steps, restore, fork: `core/time_travel.py`, `ui/thread_rewind.py`, `ui/thread_locks.py`, `ui/replay_routes.py`
- `13-proxy.md` **§13** The scraping proxy: `core/proxy/`
- `14-swarm-autoscaler.md` **§14** Worker templates, spawning, the worker registry, finished tasks: `core/swarm/autoscaler.py`, `core/swarm/registry.py`
- `15a-memory-worker.md` **§15 A-E** Memory schedulers, maintenance sweeps, the durable queue, no slow call under a memory lock, the split databases: `core/memory/worker_bootstrap.py`, `core/memory/task_queue.py`, `core/memory/vector_write.py`
- `15f-memory-findable.md` **§15 F** Nothing lost: whole texts, passages, reranker, vectors, recovery, every turn reaches memory, turn reconcile: `core/memory/vector_engine.py`, `core/memory/rehydrate.py`, `core/memory/turn_reconcile.py`, `core/memory/text_fill.py`
- `15g-recall.md` **§15 G (1)** Recall: evidence ranking, words, small talk, deadline, dates, fact names, list facts, "forget X" tools: `core/memory/recall.py`, `core/memory/query_terms.py`, `core/memory/episode_text.py`, `core/memory/predicates.py`
- `15g-memory-benchmarks.md` **§15 G (2)** Measuring memory: benchmark, lock, sealed set, pgvector, LongMemEval, product measures: `core/memory/benchmark.py`, `scripts/memory_*.py`, `scripts/longmemeval_*.py`
- `15g-recall-hot-path.md` **§15 G (3)** Recall off the loop, install YAML, FTS triggers, part F (time, follow-ups, lists, ids): `core/memory/time_words.py`, `core/memory/question_shape.py`
- `15h-facts.md` **§15 H, H2** Writing facts: time, deep and quick pass, own words, whose statement, changes and their questions, endings; history import: `core/memory/belief_extractor.py`, `core/memory/belief_mutation.py`, `core/memory/own_words.py`, `core/memory/changes.py`, `core/memory/arabic_facts.py`
- `15i-forget.md` **§15 I** Forget, keep a chat out, About me, export, pages that forget: `core/memory/forget.py`, `core/memory/forget_journal.py`, `core/memory/memory_stores.py`, `core/memory/profile.py`
- `15j-summaries.md` **§15 J** Weekly summaries and roll-ups: `core/memory/topic_summaries.py`, `core/memory/topic_rollups.py`
- `15k-mirrors.md` **§15 K, K2** Memory's copies in Postgres and Neo4j: `core/memory/state_backend.py`, `core/memory/graph_sync.py`, `core/memory/graph_backend.py`
- `15l-graph-and-page.md` **§15 L** Entities, the graph, the Memory page and map, health, operator actions, bounds: `core/memory/entity_*.py`, `core/memory/graph_*.py`, `ui/memory_api.py`, `ui/static/js/memory_console.js`
- `15m-running-it.md` **§15 M** Recall records, quality pages, narrowed search, sweep cursors, "what Kazma knows about you": `core/memory/recall_records.py`, `core/memory/quality_watch.py`, `core/memory/vector_codes.py`, `core/memory/sweep_cursors.py`
- `16-scheduled.md` **§16** Reminders and tasks, delivery outbox, timing, tenants of memory, entity keys: `core/cron/`, `ui/scheduled_api.py`
- `17-lifecycle-card.md` **§17** The start card: `core/lifecycle_notifier.py`
- `18-migration.md` **§18** Bundles, import, path rewrite: `core/migration/`, `cli/migrate.py`
- `19a-documents-store.md` **§19 A-D** Documents: boundaries, jobs, admission, spaces, config, replicas: `core/documents/ingestion.py`, `core/documents/admission.py`, `core/documents/repository*.py`, `core/documents/jobs*.py`, `ui/documents_api.py`
- `19e-documents-isolation.md` **§19 E** Documents: fences, malware, sandbox and container worker, PDF policy: `core/documents/worker_*.py`, `core/documents/broker_supervision.py`, `core/documents/pdf_policy.py`, `deploy/document-worker/`
- `19f-documents-backup.md` **§19 F** Document backup, restore, GC, audit: `core/documents/backup*.py`, `core/documents/retention.py`
- `19g-documents-ocr.md` **§19 G** Certification, fidelity corpus, OCR: `core/documents/fidelity.py`, `core/documents/ocr/`
- `19h-documents-rendering.md` **§19 H-J** Arabic and RTL, layout, fonts, tables, PDF text: `core/documents/arabic.py`, `core/documents/engines/`, `core/documents/parsers/`, `core/documents/fonts.py`
- `19k-documents-egress-images.md` **§19 K, L** Remote parsing; pictures in documents: `core/documents/extract_salvage.py`, `core/documents/image_*.py`
- `20-commitment.md` **§20** The commitment layer: `core/safety/commitment/`, `core/safety/side_effects.py`
- `21-postgres-and-backups.md` **§21** Postgres dumps, schema, service setup, upgrades, WAL, data folder; universal and offsite backup: `core/db/`, `core/backup/`, `core/postgres_*.py`, `core/data_folder*.py`
- `21b-system-dependencies.md` **§21b** What setup installs on the machine: `core/system_deps.py`, `cli/setup.py`
- `21c-install-parts.md` **§21c** Install modes and parts, ports, container services, setup scripts, problem report, uninstall: `core/install_components.py`, `cli/install.py`, `setup.ps1`, `setup.sh`
- `21d-setup-guide.md` **§21d** The /setup page: `ui/setup_api.py`
- `21e-windows-installer.md` **§21e** Windows installer, release installs, signing: `scripts/windows_installer/`, `cli/release_tree.py`
- `22-agent-skills.md` **§22** SKILL.md skills, their switches: `core/agent_skills/`, `core/skills/switches.py`
- `23-windows-subprocess.md` **§23** No asyncio subprocess on the server loop; Playwright: any code that starts a process
- `24a-import-gates.md` **§24 A** Every import resolves; deleting a module: `tests/test_imports.py`
- `24b-web-acquisition.md` **§24 B (1)** Fetching the web: ladder, browser, robots, origins, crawls: `core/web_acquire/`, `core/tools/read_url.py`
- `24b-research-pipeline.md` **§24 B (2)** Deep research and web search: `core/tools/research_*.py`, `core/tools/web_search.py`
- `24c-csrf-research-sessions-ci.md` **§24 C-E** CSRF, rate limits; research sessions; deep health; CI: `ui/csrf.py`, `ui/rate_limit.py`, `core/tools/research_session.py`, `.github/workflows/ci.yml`
- `24f-knowledge-vectors.md` **§24 F** Knowledge meaning search, Chroma: `core/stores/knowledge_index.py`, `core/memory/chroma_local.py`, `core/memory/vector_store_global.py`
- `24g-knowledge-store.md` **§24 G** The Knowledge store, its purge, search, admission: `core/stores/knowledge.py`, `core/stores/knowledge_*.py`, `ui/kb_api.py`
- `25-long-task.md` **§25** /long, Partial, continue: `core/agent/long_task.py`
- `26-default-deny.md` **§26** Sign-in and peer trust, HITL default, exec arguments, masking, off-loop rule, errors, fences, child env: `ui/auth.py`, `core/security/`, `core/errors.py`
- `27-backups-signals.md` **§27** Backups that prove themselves, restic, weekly report, daily digest: `core/backup/restic_repo.py`, `core/observability/firing_ledger.py`, `core/observability/daily_digest.py`
- `28-guards-need-controls.md` **§28** Every gate has a negative control: any gate test
- `29-context-integrity.md` **§29** Scratchpad, drafts, trims, context window, recovery never re-streams: `core/agent/artifacts.py`, `core/agent/turn_input.py`, `core/summarizer.py`
- `30-gate-registry.md` **§30** The approval registry, decisions, expiry, tool receipts, refusals: `core/safety/hitl_gates.py`, `core/agent/effect_journal.py`, `core/agent/tool_refusal.py`, `ui/hitl_*.py`
- `31-turn-delivery.md` **§31** Turn journal, the unified turn block, time and token limits, cut turns: `ui/turn_*.py`, `ui/delivery.py`, `ui/sse_chat/`, `ui/static/js/chat.js`, `ui/static/js/modules/turn_*.js`, `core/agent/turn_*.py`
- `32-ssrf.md` **§32** Pinned fetches and SSRF: `core/security/ssrf*.py`, `core/security/pinned_request.py`
- `33-alerts.md` **§33** Every alert, its catalog, card, receipts, Settings -> Alerts: `core/observability/ops_alerts.py`, `core/observability/alert_*.py`, `ui/settings_alerts.py`
- `34-mail-calendar.md` **§34** Mail, calendar, their sign-ins: `skills/native/email_manager/`, `skills/native/calendar/`
- `35-class-gates.md` **§35** The class gates: one copy of a decision, off-loop, kept SQLite connections, loop stalls: `tests/test_static_gates.py`, any store or route
- `36-session-spool.md` **§36** Chat saves are never only in memory: `ui/session_manager.py`, `ui/session_spool.py`
- `37-store-registry.md` **§37** Every store and tool write declared: `core/store_registry.py`
- `38-hardening-2026-09-25.md` **§38** Vault scopes, model fallback notice and drill, pytest isolation, env variables, python sandbox, PATH, vector store probe: `core/security/vault.py`, `core/observability/model_fallback.py`, `core/path_refresh.py`
- `39-guard.md` **§39** The guard, probes, broker, Docker and disk and power watch, outside observer: `scripts/service/`, `deploy/observer/`, `core/docker_cli.py`
- `40-checkpoint-serde.md` **§40** Strict checkpoint types: `core/checkpoint_serde.py`
- `41-checkpoint-retention.md` **§41** `core/checkpoint_retention.py`: how many steps a chat keeps
- `42-chat-apps.md` **§42** Receiving, the Test, replies, the private bot: `gw/adapters/`, `gw/receive_log.py`, `gw/sender_rule.py`
- `43-turn-notes.md` **§43** Per-turn system notes, the base prompt on every call: `core/turn_notes.py`, `core/agent/graph_helpers.py`
- `44-database-outage.md` **§44** Riding out Postgres, read-only start: `core/db/availability.py`, `core/db/postgres_pool.py`, `ui/health.py`
- `45-audit-2026-09-30.md` **§45** Markdown images, CSP, protected config, sign-in, sockets, MCP secrets and presets, PyPI names, releases: `ui/security_headers.py`, `core/mcp/secrets.py`, `cli/update.py`
- `46-slash-commands.md` **§46** Chat-app slash commands and who may use them: `gw/agent_handler/commands.py`, `gw/slash_commands.py`
- `47-settings-restore.md` **§47** Settings backup and restore, key classes: `core/settings_restore.py`, `core/settings_validation.py`
- `48-live-chat-settings.md` **§48** A saved chat-app setting applies now: `gw/chat_adapters.py`
- `49-security-report.md` **§49** The security report: `core/security/hardening.py`
- `50-dependencies.md` **§50** Dependency floors, `kazma update`, locked versions: `pyproject.toml`, `uv.lock`, `cli/update.py`, `core/install_requirements.py`
- `51-send-results.md` **§51** A send says if it was delivered; the chat outbox: `core/tools/send_message.py`, `gw/chat_outbox.py`
- `52-run-supervision.md` **§52** Running work and incidents: `core/runs/`
- `53-audit-2026-10-26.md` **§53** The October audit's fixes (tool arguments, importer, masks, loop-stall, many small): spread over the product
- `54-functional-audit-1.md` **§54** Settings store during an outage, restore layout, update stash, dead settings, app role, service exposure: spread
- `55-functional-audit-2.md` **§55** Secret echoes, provider honesty, failure results, shutdown budget, malware signatures, logging queue: spread
- `56-functional-audit-3.md` **§56** Error envelopes, upload limits, store retention, file bounds, prompt rules, release and workflow pins: `core/store_registry.py`, `.github/workflows/`, `core/shutdown.py`
- `57-weak-spot-roadmap.md` **§57** Chat reasoning level, failure results, drills, paired languages, advisories: `core/agent/chat_reasoning.py`, `tests/test_paired_languages.py`
- `58-signals.md` **§58** Quiet logs, open problems, weekly targets: `core/observability/`
- `59-ui-truth.md` **§59** What the chat page says is true; the chaos drill: `ui/static/js/chat.js`, `scripts/chaos_week.py`
- `60-providers-1.md` **§60** Prices, requests, addresses, the provider check, backups: `core/model_prices.py`, `core/provider_check.py`, `core/failover_backup.py`
- `61-providers-2.md` **§61** Provider catalog, model references and details, extra keys: `core/providers.py`, `core/model_refs.py`, `core/model_details.py`, `core/provider_keys.py`
- `62-security-audit-2026-11.md` **§62** Saved extracts, shell_exec paths, git tools, vault tools, cards, OAuth attempts, CSP, SearXNG key: `core/safety/shell_*.py`, `core/safety/git_*.py`, `core/security/oauth_attempts.py`
- `63-container-engine.md` **§63** Kazma's own Docker engine, moving to it, Windows shutdown: `core/container_engine*.py`, `core/engine_move.py`, `core/shutdown_notice.py`
- `65-a2a.md` **§65** Other agents calling Kazma: `ui/a2a_api.py`, `core/a2a_protocol.py`
- `64-whatsapp.md` **§64** `gw/adapters/whatsapp.py`: webhook, buttons, sending
- `66-code-scanning.md` **§66** CodeQL findings: JS parses, session labels, masked logs, linear patterns
- `67-secret-files.md` **§67** Owner-only files, URL passwords, token shapes: `core/security/owner_only.py`, `core/security/file_access.py`, `core/security/token_shapes.py`
- `68-scorecard.md` **§68** Workflow permissions, hash-locked installs, fuzz targets, coverage: `.github/`, `fuzz/`
- `69-sonarcloud.md` **§69** Regex sweep, log forging, cancel handling, accessible names, model server address: any pattern, log line or control
- `70a-spaces-foundation.md` **§70 (1)** Spaces: scope, identity, store and route rules, the matrix: `core/spaces/scope.py`, `core/spaces/identity.py`, `ui/route_scopes.py`, `ui/space_binding.py`
- `70b-spaces-chat-memory-docs.md` **§70 (2)** S1-08..14: chat state, memory, lessons, documents, Knowledge, scheduled, per space
- `70c-spaces-swarm-accounts.md` **§70 (3)** S1-15..17: swarm, research, mail, X, GitHub accounts per space: `core/spaces/connected_accounts.py`
- `70d-spaces-vault-files-admin.md` **§70 (4)** S1-18..26, 33: vault, files, host tools, runs, ledger, settings areas, admin, keys, the switch: `core/spaces/files.py`, `core/spaces/tools.py`, `core/spaces/space_keys.py`, `core/spaces/preconditions.py`, `core/settings_scope.py`
- `70e-spaces-data-speakers.md` **§70 (5)** S1-27..34, S1-11: count/export/erase, backups, notes, bell, host roots, who is "I", push, echoes: `core/spaces/space_data.py`, `core/memory/speakers.py`
- `70f-spaces-limits-features-shared.md` **§70 (6)** S2-02, S2-11, S2-12: daily limits, features, shared spaces: `core/spaces/daily_limits.py`, `core/spaces/features.py`, `core/spaces/people.py`
- `70g-spaces-approvals-tools-models.md` **§70 (7)** S2-05..08, 01, 03: whose card, grants, tool and model lists, space limits: `core/spaces/approvals.py`, `core/spaces/person_models.py`, `core/spaces/space_limits.py`
- `70h-spaces-fairness-people-keys.md` **§70 (8)** S4-04, 05, 08, S2-09, 10, 04: fairness, members' code, activity, own keys, cloud profile: `core/spaces/fairness.py`, `core/spaces/member_code.py`, `core/spaces/own_keys.py`, `core/security/cloud_profile.py`
- `70i-spaces-chat-links.md` **§70 (9)** Stage 3: linked chat-app accounts and its review: `core/spaces/chat_links.py`, `core/spaces/chat_apps.py`
- `70j-spaces-sign-in.md` **§70 (10)** S4-03, S4-02: open switches refused, second factor, invitations: `core/security/second_factor.py`, `core/spaces/invitations.py`
- `ui-1-words-and-direction.md` **UI (1)** Dialogs, catalog words in EN and AR, text direction, TUI names: `ui/templates/`, `ui/static/js/`, `ui/i18n/`, `tui/`
- `ui-2-look-and-components.md` **UI (2)** Toasts, logo, colours, shell, shared components, x-cloak, bindings: `ui/static/css/`
- `ui-3-pages-and-controls.md` **UI (3)** Page loading, soft navigation, deep links, bell, controls and routes, dates and counts, empty states, polling: `ui/templates/`, `ui/static/js/modules/nav.js`
- `server-deployment-policies.md` Workspace-root and execution policies, container python runner, approval facts: `core/safety/deployment_policy.py`, `core/agent/approval_facts.py`
- `#live-install` the live install and deploys (above)
- `server-deploy-details.md` Update before reload, image rebuild, what `scripts/deploy_live.py` does and reverts
- `testing-1-isolation.md` **Testing (1)** Answer-quality ledger, runtime ownership, temp folders, data shield, egress guard, `real_docker`, published files, diagrams: `conftest.py`, `tests/`
- `#test-commands` compile check, syntax check, one test file (above)
- `testing-2-full-suite.md` **Testing (2)** `scripts/prepush.py`, CI's shards (`scripts/ci_shards.py`), `scripts/fast_test.py`
- `#manual-verification` manual verification (above)
- `key-references.md` Every document worth knowing: guides, audits, trackers, plans, and the rules of the docs folders
<!-- /index -->
