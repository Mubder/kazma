<!-- AGENTS.md, piece 24c-csrf-research-sessions-ci. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**C. CSRF + rate limiting (`kazma_ui`).**
- `csrf.py` middleware: a non-GET `/api/*` whose Origin (else Referer) is
  not the origin the request was served on or one of
  `browser_origins.configured_browser_origins()` (`KAZMA_PUBLIC_URL`,
  `KAZMA_CORS_ORIGINS`) → 403; whole origins are compared (scheme, host,
  port) and forwarded headers never add to the trusted set (§45).
  `Authorization`-header requests and origin-less clients
  (curl/CLI/webhooks) are exempt. **Starlette's URL has no `.host`** (the
  2026-08-14 every-browser-POST-500 crash). Tests must build REAL ASGI-scope
  Requests (`tests/test_csrf.py`) — MagicMock auto-attributes and hides
  exactly that bug class.
- `rate_limit.py`: per-principal sliding window (cookie > Authorization > IP),
  one bucket per `rate_limit("<bucket>", n)` dependency: chat, chat uploads,
  voice, research, swarm dispatch, approvals, scheduled jobs, runs,
  documents, IDE exec, skill installs, backups and admin operations (the
  system flush route it also covered was removed 2026-09-30, AUD-015).
  Active ONLY when auth is enabled (never demo mode); live ConfigStore
  `api.rate_limit.<bucket>_per_minute`; env `KAZMA_RATE_LIMIT_ENABLED=0`.

**D. Research sessions.**
- Cancellation is a request, not cleanup proof (2026-10-11). Active sessions enter
  `cancelling`; terminal `cancelled` follows joined pipeline workers. Delete must
  retain the row/subscribers/owner and return typed 409 while `_RUNNING` owns it.
  `update_session` reads and writes under one SQLite writer transaction, preserving
  cancelling/terminal states against late progress. Pre-first-step cancellation
  transfers its lease to a joined terminal writer; never cancel that writer from
  Delete. UI keeps/reconnects progress while cancelling. Gates:
  `tests/test_research_cancel_truth.py`, `tests/js/test_research_cancel.js`.
  Dead process owners reconcile to interrupted; explicit resume links the original
  durable pipeline. Verified terminal output is restored without effects. History
  is bounded to 256 rows/128MiB; archive does not release that allowance.
- `suppress_chat_recording()` (ContextVar, research_session.py): the deep
  pipeline wraps BOTH its search gathers in it — sub-queries must NOT mint
  standalone `record_chat_research` rows (the panel flood: one deep run
  showed 10+ "1 sources" rows). Chat-initiated searches still record.
- Sessions support delete + archive (idempotent `archived` column via ALTER
  TABLE; `list_sessions` EXCLUDES archived by default — pass
  `archived=True`/`None` explicitly for the Archived tab / everything).
  The panel routes `session:`-prefixed ids to `/api/research/sessions/*`
  mutation endpoints — sessions live in `research_sessions.db`, NOT the
  swarm TaskStore.
- Chat-tool snapshots persist up to `_CHAT_RESULT_MAX` (200K — the old
  [:500] cap discarded full output at write time); a longer re-query
  refreshes the stored summary, a shorter one never clobbers it.
- **asyncio objects are touched on their own loop only** (2026-10-01). The
  pipeline writes progress through `asyncio.to_thread(update_session, ...)`
  and the Research routes are plain `def`s, so `update_session`,
  `cancel_session` and `delete_session` run in worker threads; they used to
  `put_nowait` onto the SSE readers' `asyncio.Queue` and `cancel()` the run's
  task right there (asyncio's debug mode refuses both). A subscriber is
  registered with its loop (`_SUBS`, under `_SUBS_LOCK`); `_deliver` and
  `_cancel_on_its_loop` hand over with `call_soon_threadsafe`.
  `tests/test_research_session_threads.py` records the thread of every put
  and cancel (the old calls as negative controls). Any sync function that may
  run in a thread and holds an asyncio Queue, Event, Future or Task follows
  the same rule.

**E. Deep canary + CI gate.**
- `GET /health/deep` (kazma_ui/health.py): one REAL roundtrip per critical
  path — ConfigStore write→read→delete, a real `recall()`, workspace
  binding, research-stack readiness, brain entry-point imports, DB ping.
  503 when any check fails; TTL-cached 30s (aggressive polling is free).
  Poll it in ops dashboards — it exists to catch SILENT no-ops (the
  recall-NameError class), which structural checks cannot see.
- CI (`ci.yml`) GATES on the full suite: `python scripts/fast_test.py --chunks
  4` (crash-tolerant chunked serial pytest over ALL
  testpaths — the package suites were previously orphaned by `pytest tests/`),
  `-m "not slow"`, per-chunk `--timeout=120`. Compile-check (py_compile over
  every repo `.py`) and `node --check` over static JS are separate GATES.
  Never reintroduce `|| true` on the test step — that single flag let every
  regression class in this audit ship silently. Lint/bandit remain advisory
  (`--exit-zero`/`|| true`) until their backlogs are triaged. Known CI blind
  spots (deep-audit 2026-08-19, patched same day): the G1 commitment-latency
  file's `slow` marker was removed (it runs in ~6s and was the ONLY slow file,
  so `-m "not slow"` silently excluded it), and CI now installs the light
  pure-wheel deps (pillow/pymupdf/sqlite-vec/pypdfium2/numpy — without numpy
  the belief-graph PPR silently runs its degraded uniform-seed path) that the
  `.[test]`-only install left `importorskip`ing/degrading. Playwright is a
  separate CI job: smoke (`tests/e2e/test_smoke.py`, polls `/health/live`)
  plus a named GATE step for HITL view-model incidents 2 and 3
  (`tests/e2e/test_hitl_view_model.py`). Remaining `tests/e2e` files run in
  the same job. Incidents 1 and 4 are claimed by
  `tests/e2e/test_unified_turn_browser.py` in the `unified-turn-lifecycle`
  job (§31E). Torch-bearing `rag` extra is still too heavy for CI.

