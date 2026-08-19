# Deep Structure Audit — 2026-08-19

**Scope:** Full-repo structural audit of the Kazma monorepo (kazma-core, kazma-gateway,
kazma-ui, kazma-tui, kazma-cli, tests/, CI, docs) with two goals:

1. Verify that every documented load-bearing invariant (AGENTS.md §1–§24) actually
   holds in code, with `file:line` evidence.
2. Build a change-impact map so future fixes don't break distant subsystems.

**Method:** Seven parallel deep-exploration passes (core brain/LLM providers, swarm,
safety/commitment/memory/skills/cron/lifecycle, gateway, UI/TUI,
IDE/documents/migration/data-layer, tests/CI/docs). The four highest-severity new
findings were re-verified directly in source before publication.

**Verdict:** Of ~40 documented invariants checked, **34 held exactly as documented,
6 had doc drift but sound code, and 0 core safety mechanisms were broken.** The audit
found real bugs and traps that AGENTS.md did not know about (§5). The drift items in
AGENTS.md were corrected the same day (§6, §8).

---

## 1. System structure

```
kazma-core    441 .py   The brain: LangGraph supervisor, LLM providers, swarm engine,
                        V2 memory, safety/HITL/commitment, IDE, documents, migration
kazma-gateway  69 .py   Platform adapters (Telegram/Discord/Slack) + agent_handler
kazma-ui       59 .py   FastAPI app (~2080-line app.py builder) + SSE/WS chat + static JS
kazma-tui      42 .py   Textual dashboard; shares core's IdeService singleton
kazma-cli       9 .py   `kazma migrate` CLI front-end
tests/        351 files + 6 package suites ≈ 5,042 tests, 91k LOC
```

**One message, end to end:** platform poll/webhook → `IncomingMessage` (raw IDs only
in `ctx`) → `GatewayManager` bounded queue → `agent_handler/graph.py:handler()`
(~20 sequential intercepts: /models, /hitl, session cmds, /undo /edit /replay /fork,
swarm, ide, kb, documents, research, skill) → `_build_initial_state` scrubs platform
keys, keeps only `state["_gateway"]` → `run_agent_turn` → supervisor graph
(`START → SUPERVISOR ⇄ TOOL_WORKER → RESPOND → END`, `graph_builder.py:3412-3449`)
→ reply routed back via `_build_target_id()` from SessionStore.

**Key data stores:** `memory_state.db` (hot cognitive reads) vs `memory_ops.db`
(cold queue/audit/commitments — the split is load-bearing, AGENTS.md §15E),
`kazma-data/config.db` (ConfigStore singleton), `snapshots.db` (time travel),
`cron.db`, `settings.db` (workspaces), document store (CAS tree), optional Postgres
for shared state (`KAZMA_PG_TABLES` is the backup/verify SoT) + Neo4j belief-graph
dual-write.

---

## 2. Verified "do-not-break" contract (condensed SoT)

All confirmed in code; anchors are where the invariant lives:

| Invariant | Anchor |
|---|---|
| 4-branch provider dispatch (`google/anthropic/azure/bedrock/else-generic`) exists in **three separate sites** — a new provider needs all three | `model_registry.py:455-491, 518-535, 562-579` |
| `hoist_system_messages()` called exactly once, inside `LLMProvider.chat()` — Azure/Gemini inherit it, Anthropic/Bedrock split system natively | `llm_provider.py:331` |
| `LLMError.transient` (ReadError/429 = transient) + `respond_node` skips synthesis on `turn_failed` | `llm_provider.py:565-571`, `graph_builder.py:3051-3058` |
| Commitment gate runs **before** the HITL split in `tool_worker_node` | `graph_builder.py:2448 → 2460` |
| Graph-build sites must all pass `hitl_config` + `snapshot_recorder` (3 in agent_runner + app.py recompile) | `agent_runner.py:824-834, 864-875, 943-954`, `app.py:1486-1498` |
| Snapshot capture in `_supervisor` wrapper, stamped into state | `graph_builder.py:3324-3331` |
| Platform isolation: no `chat_id/user_id/message_id` at top level of graph state; only `_gateway.delivery_target` (namespaced, by design) | `agent_handler/store.py:18-33, 236-244` |
| Handoff guards: `MAX_HANDOFF_DEPTH=5`, `MAX_VISITS=2`, threaded through 5 call layers | `handoff_guards.py:18-19`, `engine.py:778-901` |
| Breaker `_probe_in_flight` single-probe semantics + `finally` release | `reliability.py:261-309`, `worker_dispatch.py:330-333` |
| WAL + `busy_timeout=5000` on every SQLite store; `json_each()` worker filter | `config_store.py:241-259`, `task_store.py:407-411` |
| Danger-tool SoT: `CANONICAL_DANGER_TOOLS` ≡ `kazma.yaml` list (set-equal); guarded by 2 parity tests | `hitl.py:128`, `test_agent_skills.py:187-205` |
| Soul deltas: ConfigStore-backed, fenced at creation **and** apply, injected fenced at 3 chat sites + worker path, live kill-switch | `self_improvement.py:202/286/335/729` |
| `check_sync()` fail-closed (lives in `swarm/safety.py`) | `swarm/safety.py:210-254` |
| Workspace ladder: scope ContextVar → WorkspaceStore row → pin → env → sandbox | `workspace/binding.py:94-153` |
| IDE mutations go through `LocalToolRegistry.execute()` only | `ide/service.py:179-195` |
| Migration: staging → verify → rewrite → backup → atomic swap; vault key+DB travel as a pair; PathMap longest-first | `importer.py:158-309`, `vault_pairing.py:38-126`, `path_rewrite.py:74-75` |
| Proxy: scraping-only, live-read, never on LLM path | `proxy/registry.py:43-71`, `client.py:144-185` |
| Cron: `graph_builder=` mandatory or every fire raises; `delivery_target` captured at schedule time | `cron/scheduler.py:587-588, 656` |
| Windows selector-loop: **every** `create_subprocess_*` site has a Popen/to_thread fallback (full inventory clean) | `mcp/manager.py:848-872` et al. |
| CSRF uses `request.url.hostname`; tests use real ASGI Requests | `csrf.py:73`, `test_csrf.py` |

---

## 3. Change-impact cheat sheet ("touch X → must check Y")

- **`agent/graph_builder.py` (3450 lines)** — highest blast radius. It couples: HITL
  ContextVars shared with `tool_registry.py`, snapshot stamping read by `sse_chat.py`,
  `turn_failed` consumed by `respond_node`, commitment gate ordering, and intent-patch
  merging where a partial return silently drops task-status continuity
  (`graph_builder.py:1049-1051`). Any edit here needs the fast suite +
  `test_supervisor.py` + `test_hitl_graph_integration.py`.
- **`model_registry.py` provider branches** — three sites must move together; the
  generic `LLMProvider` can't reach Anthropic-native/Azure/Bedrock auth.
- **`llm_provider.py:chat()`** — removing the hoist breaks every LM Studio/Ollama/Qwen
  turn with mid-stream system notes. The `_semantic_cache_singleton` is a `globals()`
  hack — refactoring the cache lookup out of `chat()` breaks it with NameError
  (`llm_provider.py:305-308, 408`).
- **HITL has 3 mechanisms (graph interrupt / swarm bus / pipeline checkpoints) + a
  4th gate (commitment)** — a danger tool added to `CANONICAL_DANGER_TOOLS` must also
  reach `kazma.yaml` (2 parity tests) and ideally
  `scripts/generate_tools_catalog.py` (a third, **manual** copy that already drifts).
- **`engine.py` delegates** — post-refactor, ~20 one-line delegates forward to
  `dispatch_helpers`/`task_lifecycle`/`task_control`; behavioral fixes must land in
  the helper, and delegate signature changes silently desync the facade.
  `approve_checkpoint` still reaches into `CheckpointManager._paused` directly
  (`engine.py:1105`).
- **SSE vs WS vs gateway** — three transports each implement HITL extraction and
  prompt injection, unified only at `build_turn_messages` + `build_resume_command`.
  A prompt-injection change must be applied in `sse_chat.py:1417-1476`,
  `ws_chat.py` (mirror), and gateway `graph.py:1204-1217` + `worker.py:302-373`.
- **Deleting any module** — requires green `tests/test_imports.py` in the same commit
  (the crawl.py incident class). Note the reverse gap: the embedded gateway suite
  `kazma-gateway/kazma_gateway/tests/` (6 files) was **not in testpaths** and never
  ran in CI — same orphaning class fixed before for `kazma-core/tests`
  (pyproject comment at line 145). *(Fixed same day — §8.)*
- **Session TTL (300 s) interacts with HITL** — cross-thread approvals read the
  *target* thread's session row; eviction ⇒ "session not found" while the graph is
  still paused (`hitl.py:313-327`). Anything extending card lifetimes must reckon
  with this.
- **Streaming vs run path asymmetry** — `get_streaming_graph` normalizes
  `enabled=False → None` (`agent_runner.py:806-808`); `_ensure_graph` passes the raw
  dict. With HITL *enabled* both behave identically, but "normalizing" either side
  changes which mechanism (graph gate vs bus `check`) guards execution — a classic
  silent-divergence trap.

---

## 4. Runtime architecture notes

- The supervisor node is a ~1500-line sequential gauntlet (intent → memory recall in
  `to_thread` → trimming → steer interrupts → `_call_llm_resilient` retry+failover).
  Broad `try/except` blocks mean partial failures degrade silently by design.
- Streaming uses `astream_events` with a detached pump + 300 s watchdog; **HITL resume
  deliberately uses `ainvoke`** because the custom provider emits no model-stream
  events (`sse_chat.py:210-293`).
- Memory V2: beliefs (functional supersede chains with source-trust gate), tiered
  episodes (working→episodic→recall→archived), 3-cascade entity resolution,
  procedural DAGs with Laplace confidence, hybrid RRF recall — all prompt-fenced on
  output. Background tier = durable queue + **four** schedulers (not two — §6).
- Swarm dispatch: admission control → pattern execution
  (pipeline/fan-out/consult/conditional) → per-worker breaker/retry/timeout → handoff
  with cycle guards → idempotent finalize. The codebase encodes prior audit findings
  as inline comments (M11/H7/H8…) — those comments *are* the TODO ledger; there are
  essentially zero literal TODO markers repo-wide.

---

## 5. Findings — new issues discovered (prioritized)

### Real bugs (functional, confirmed)

1. **Gateway working-memory pinning is dead** — `user_text` used at
  `kazma-gateway/kazma_gateway/agent_handler/graph.py:520` before its first
  assignment at `:1121`; the `NameError` is swallowed by a blanket `except` at debug
  level. §17 WM pinning never ran on the Telegram/Discord/Slack path.
  *(Verified in source; fixed same day — §8.)*
2. **`format_skill_activation` UnboundLocalError path** — if the integrity `try`
  raises before `vr` is assigned, `agent_skills/catalog.py:123` crashes, defeating
  its own "never crash activation" guard.
3. **Slack empty-chunk send** — `adapters/slack_send.py:54-57` returned `[""]` for
  empty text; the equivalent Telegram/Discord fixes were never ported, so an empty
  outbound with attachments was rejected and attachments dropped. *(Fixed same day.)*
4. **Swarm BROADCAST cancellation leak** — `engine.py:382-383` returned before the
  try/except that handles `CancelledError`; a cancelled broadcast stayed RUNNING
  until watchdog reap (timeout+30 s), no `task_completed` SSE. *(Fixed same day.)*
5. **Checkpoint reject double-finalized** — `engine.py:1179-1193`: the H7 idempotency
  guard doesn't fire for paused tasks ⇒ duplicate SSE + duplicate `persist_task`.
  *(Fixed same day.)*
6. **`/fork` copies a stale `thread_id`** into the new session row
  (`graph.py:1870-1874`) — consumers reading `ctx["thread_id"]` on the fork get the
  original thread.

### Security-relevant

7. **WebDAV cloud-sync uploads with `verify=False`**
  (`backup/cloud_sync.py:623,641`) — and the universal backup zip deliberately
  contains the plaintext `.env` (vault recovery key). A MITM'd non-loopback WebDAV =
  full secret exfiltration. *(Verified in source; fixed same day — verify now
  defaults to on, opt-out via explicit config.)*
8. **Fence-import fallback injects unfenced untrusted text** — if the
  `prompt_fence` import fails, memories/skills are injected raw with the injection
  filter off (`compaction.py:373-397`, `graph_builder.py:461-491`). Intentional
  degrade, but it is the one unfenced path left.
9. **Commitment gate fail direction is inconsistent**: graph path fails **open** on
  resolver exceptions (`graph_builder.py:2105-2130`), registry path fails **closed**
  (`tool_registry.py:518-529`).
10. **HITL narrowing is diagnostic-only** — Settings can remove any danger tool from
    approval with just a warning log (`hitl.py:222-256`).
11. **MCP filesystem root is process-global** — per-task `workspace_scope` does not
    rebind it; concurrent multi-repo swarm tasks can hit another repo's root via MCP
    (documented in code, but a live multi-repo leak surface).
12. **`_allow_all=True` forced in app wiring** (`app.py:764,793`) neutralizes the
    adapters' fail-closed empty-allowlist defaults.

### Consistency traps / operational

13. **app.py recompile captured boot-time HITL config** (`app.py:1467-1469`) —
    recompiled graphs used stale HITL after a Settings change until restart.
    *(Fixed same day — config re-read live at each recompile.)*
14. **CWD-relative paths**: autoscaler templates (`autoscaler.py:41`) and
    `SQLiteCronStore` default (`scheduler.py:176`) break if the process starts from
    another directory.
15. **Typing keepalive not refcounted** (two turns in one chat cancel each other's
    indicator); **RateLimiter sleeps holding its lock** (one 429 stalls all senders
    platform-wide, `gateway.py:180-192`); **Telegram media/STT run inline in the poll
    loop** (a 19 MB download stalls all Telegram updates).
16. **`GET /api/providers` registered twice** (`providers.py:154` shadows
    `sse_chat.py:2166`, different shapes) — silent drift hazard.
17. Fire-and-forget tasks without strong refs (`worker_dispatch.py:290-297`) — same
    GC bug class already fixed for alerts; `PostTaskSuggester.suggest()` and
    `swarm_notify.all_tasks` are dead code.

### Test/CI gaps

18. **Embedded gateway suite orphaned from CI** (not in testpaths — verified
    personally; repeats the exact incident class fixed in commit dc0273b7).
    *(Fixed same day — added to testpaths after verifying the suite passes.)*
19. **The only `slow`-marked test is the G1 latency gate** — the documented <20 ms
    commitment-latency invariant never runs in CI (`-m "not slow"`).
20. **CI installs only `.[test]`** — Playwright/PIL/pymupdf/sqlite-vec live in other
    extras, so e2e/vector/PDF tests `importorskip` silently on the merge gate.
    Ruff/bandit/metrics advisory; mypy strict configured but no job runs it; coverage
    configured but never collected.
21. Root conftest autouse `allow_headless_danger=True` + `allow_absolute=True` — the
    fail-closed posture is only exercised by tests that construct their own
    middleware.

### Docs drift

22. `docs/DOCS_CONSOLIDATION_PLAN.md` moved to `docs/plans/done/` (3 stale
    references incl. AGENTS.md); `archive/` links point at a nonexistent dir;
    Docusaurus config + intro point at the wrong GitHub org (`kazma-ai/kazma` vs
    `Mubder/kazma`).

---

## 6. AGENTS.md inaccuracies found (doc drift; code sound)

1. **§20 claimed 3 `authorize_effect` choke points** — actually **2**
   (`graph_builder.py:2448`, `tool_registry.py:508-515`);
   `belief_mutation._mutate_functional` (`belief_mutation.py:539`) is an independent
   source-trust gate, not an `authorize_effect` call.
2. **§7 "both lists are alphabetical"** — `hitl.py:128-154` is grouped thematically,
   YAML (`kazma.yaml:131-155`) is alphabetical; sets are equal (both parity tests
   pass). The comment at `hitl.py:240` ("YAML ships as a subset") is also inaccurate.
3. **§10C env_context "3 sites incl. graph_builder"** — actual sites:
   `agent_runner.py:257-263` (not graph_builder), `sse_chat.py:1461-1467` per-turn,
   `swarm/worker.py:359-366` per dispatched worker, plus a 4th attach in
   `ide/service.py:471-473` (send_to_swarm).
4. **§15 "two schedulers"** — actually **four**: macro-sleep 6 h, backup/export 24 h,
   reconsolidation 24 h (`worker_bootstrap.py:511-552`), commitment GC 15 min
   (`worker_bootstrap.py:466-508`).
5. **§24B read_url ladder "Jina → Firecrawl → …"** — actual: Firecrawl first (if key
   configured), Jina opt-in only (`KAZMA_JINA_READER=1`), recovery chain
   Firecrawl→Jina→Playwright (`tools/read_url.py:686-723`).
6. **§24E CI "bare pytest --timeout=300"** — CI now runs
   `python scripts/fast_test.py --chunks 4 --chunk-timeout 1500` with
   `-m "not slow" --timeout=120` per chunk.
7. Minor: the handoff constant is `MAX_VISITS` (not `_MAX_VISITS`), and
   `swarm/engine.py` is ~1295 lines (not 1573).

*(All corrected in AGENTS.md the same day — §8.)*

---

## 7. Test/CI guardrail status

- CI gates: compile-check (py_compile over every repo `.py`), the fast_test.py suite
  (`-m "not slow"`, chunked serial, segfault-tolerant, POISON quarantine), and
  `node --check` over static JS. The test step has **no** `|| true` (removed in
  dc0273b7). Lint/bandit/metrics remain advisory; mypy never runs.
- Invariant→test mapping highlights: import integrity (`test_imports.py`), danger
  parity (2 tests), side-effects fail-closed (`test_side_effects.py`), commitment
  corpus/G2 (`test_commitment_corpus.py` + fixtures), hoist/transient/turn_failed
  (`test_llm_provider.py`, `test_retry_backoff.py`, `kazma-core/tests/`), handoff
  guards (`test_handoff_guards.py`), WAL assertions, snapshots (`test_time_travel.py`),
  CSRF real-ASGI-Requests (`test_csrf.py`), pg_backup (`test_pg_backup.py`).
- Known gaps: see §5 items 18–21.

---

## 8. Post-audit actions taken same day (2026-08-19)

1. Wrote this document.
2. Fixed finding #1 (gateway `user_text` NameError — WM pin now feeds the message
   text).
3. Fixed finding #3 (Slack empty-chunk — no zero-length chunk emitted; attachments
   no longer dropped).
4. Fixed finding #4 (BROADCAST cancel/timeout now finalizes through the same
   exception path as other task types).
5. Fixed finding #5 (checkpoint reject no longer double-finalizes).
6. Fixed finding #7 (WebDAV TLS verification now defaults to ON; explicit opt-out
   required).
7. Fixed finding #13 (graph recompile re-reads HITL config live instead of reusing
   the boot-time snapshot).
8. Fixed finding #18 (`kazma-gateway/kazma_gateway/tests` added to pyproject
   testpaths after verifying the suite passes — 18/18 green).
9. Corrected the six AGENTS.md drift items listed in §6 (plus the
   `MAX_VISITS` name, the engine.py line count, and the §15 scheduler
   enumeration).
10. Added `tests/test_audit_deep_structure_fixes.py` — 6 regression tests
    guarding fixes #3, #4, #5 (both halves), and #7.

Validation: py_compile over every edited file; targeted suites green
(swarm engine/checkpoints/task-control/cloud-sync 64, slack+gateway 71,
broadcast flows+manager 53, embedded gateway suite 18, new regressions 6,
import-integrity gate 13).

## 9. Patch 2 (same day, second batch)

1. Fixed finding #2 — `agent_skills/catalog.py:format_skill_activation`
   no longer raises `UnboundLocalError` when the integrity check itself
   errors (`vr` degrades to the unsigned note).
2. Fixed finding #6 — gateway `/fork` copies the session context with the
   `thread_id` overridden to the fork id (consumers reading
   `ctx["thread_id"]` on the fork's row no longer get the original thread).
3. Fixed finding #14 — CWD-relative defaults resolved: autoscaler
   `_DEFAULT_TEMPLATES_PATH` is now absolute (mirrors `WorkerRegistry`), and
   `SQLiteCronStore` defaults to `paths.data_dir()/cron.db` (app.py wiring
   uses the default) so a server started from a subdirectory opens the same
   DB instead of minting an empty one.
4. Fixed finding #22 — stale doc references: `DOCS_CONSOLIDATION_PLAN.md`
   pointed at its pre-move location (AGENTS.md, intro.md, development.md);
   nonexistent `archive/` links repointed at `docs/audits/archive/` and
   `UNWIRED_INVENTORY.md`; Docusaurus org corrected `kazma-ai` → `Mubder`
   (4 sites); system-map companion-doc paths fixed.
5. Extended `tests/test_audit_deep_structure_fixes.py` to 9 regressions
   (added: catalog integrity-error survival, autoscaler absolute default,
   cron store absolute default).

Patch-2 validation: py_compile + `node --check`; suites green (skills ×2,
cron, dynamic spawning, replay command: 78; regressions: 9).

## 10. Patch 3 (same day, security-hardening batch)

1. Fixed finding #8 — the fence-import fallback no longer injects untrusted
   text raw: both sites (`compaction.py:_build_compacted_system`,
   `graph_builder.py:_format_retrieved_memories`) now DROP the memory block
   with a loud warning when `prompt_fence` is unavailable (fail-closed —
   the content is untrusted and lands in the system prompt).
2. Fixed finding #9 — commitment fail direction unified:
   - `graph_builder.py:_commitment_resolve_gate` per-tool
     `authorize_effect` exceptions now fail CLOSED (blocked with a
     terminal, user-visible error), mirroring the registry choke — a
     broken policy engine can no longer free-fire semantic acts (the
     remind/CoPilot class) on the chat path.
   - Transient `load_constraint_beliefs` failures no longer skip the whole
     gate (which free-fired every semantic tool): the gate proceeds with
     empty memory anchors instead.
   - Structural gate failure stays fail-open (kill-switch posture) but is
     now WARNING-level, not debug.
   - AGENTS.md §20 fail-posture documentation updated to match.
3. Fixed finding #10 — HITL narrowing hardening: the CANONICAL-drift
   warning now repeats on a 15-minute cooldown instead of once per
   process, the inaccurate "YAML ships as a subset" comment corrected, and
   a new opt-in `KAZMA_HITL_CANONICAL_FLOOR=1` flag unions CANONICAL back
   into the effective list so Settings/YAML cannot narrow below it
   (default off — existing deliberately-narrowed installs unchanged).
4. Fixed finding #12 — gateway allowlists: new opt-in
   `KAZMA_GATEWAY_STRICT_ALLOWLIST=1` stops forcing `_allow_all=True` on
   the Telegram/Discord/Slack adapters (empty allowlist → fail-closed,
   the adapters' native default); the backward-compat force now logs a
   WARNING naming both remediation options when no allowlist is set.
5. Finding #11 (process-global MCP filesystem root vs per-task
   `workspace_scope`) is DEFERRED as an architectural item — a real fix
   needs per-workspace MCP server instances; it remains documented in
   `workspace/mcp_rebind.py` and `ide/env_context.py`.

Patch-3 validation: py_compile over all four edited files; commitment
suite 129 passed (+1 slow deselected); HITL wiring + compaction + skills
75 passed; regressions now 13.

## 11. Patch 4 (same day, consistency traps + CI gaps)

1. Fixed finding #15a — `typing_keepalive.py` is now refcounted per target:
   two concurrent turns in one chat bracket the indicator with
   `start()`/`stop()` pairs instead of the second start cancelling the
   first turn's task and the first stop killing the second's.
2. Fixed finding #15b — `RateLimiter.acquire()` now sleeps OUTSIDE its
   lock (loop-and-recheck); a 429 backoff no longer serializes every
   sender behind the lock. (No dedicated regression test: for a single
   shared token bucket the old and new timings are mathematically
   indistinguishable — the fix is lock hygiene, covered by gateway suites.)
3. Fixed finding #15c — DEFERRED: moving Telegram media/STT off the poll
   loop requires the URL-attachment lazy-fetch refactor of the Telegram
   ingest path (async httpx already keeps the event loop unblocked; the
   cost is serialized update processing only).
4. Fixed finding #16 — the shadowed duplicate `GET /api/providers` removed
   from `sse_chat.py` (the providers router owns the path and mounts
   first; `/api/provider/active` remains unique to sse_chat).
5. Fixed finding #17 — fire-and-forget memory-index tasks in
   `worker_dispatch.py` now hold strong references
   (`_MEMORY_INDEX_TASKS` + done-callback discard), mirroring the alert-task
   fix in engine.py; the dead nested `all_tasks` in `swarm_notify.py`
   moved into `SwarmTaskTracker` where it was always intended to live.
   `PostTaskSuggester.suggest()` left as-is (tested library surface; wiring
   it into the post-task path is a product decision).
6. Fixed finding #19 — the `slow` marker removed from
   `test_commitment_g1_latency.py::test_g1_full_curve` (whole file runs in
   ~6s; it was the ONLY slow-marked file, so the documented G1 invariant
   never ran in CI).
7. Fixed finding #20 — CI installs the light pure-wheel deps
   (pillow/pymupdf/sqlite-vec/pypdfium2) alongside `.[test]`, un-skipping
   the PIL/fitz/sqlite-vec/pypdfium2 suites. Playwright e2e deliberately
   stays absent until its flaky boot-wait is stabilized; the torch-bearing
   `rag` extra stays out (CI weight).
8. AGENTS.md §24E blind-spot note updated to reflect the new state.

Patch-4 validation: py_compile over all edited files; gateway + output
routing + swarm flows + settings suites 193 passed; regressions + keepalive
+ G1 latency 17 passed (the G1 gate now runs under `-m "not slow"`).

## 12. Patch 5 (same day, deferrals)

1. Fixed finding #15c (was deferred) — Telegram update processing now runs
   OFF the poll loop via per-chat serialization chains
   (`_dispatch_update_to_chain` / `_process_update_chained` /
   `_process_update`): voice STT and media downloads no longer stall every
   subsequent Telegram update, while same-chat message order is preserved
   (the handler's per-thread lock processes turns in enqueue order, so
   naive task-spawning would have inverted same-chat turns). Different
   chats proceed concurrently; updates without chat identity run unchained
   off-loop; chain tasks are strong-referenced by the chain map itself.
2. Fixed finding #11 (was deferred) — a per-task workspace scope guard in
   `mcp/manager.py:execute_mcp_tool` fail-closes MCP calls when a
   `workspace_scope` targets a different root than the process-bound MCP
   root, with an actionable error naming both remediations. Full
   per-workspace MCP instances remain future work; until then the
   cross-repo leak is a loud denial instead of a silent wrong-repo
   operation. Kill-switch: `KAZMA_MCP_SCOPE_GUARD=0` (default on; only
   fires under an active per-task scope with genuinely differing roots).
3. Follow-up fix — `tests/test_mcp_bridge.py` (4 tests) and
   `tests/test_dedup_tool_registries.py` (1 test) were failing because the
   commitment layer's unregistered-mutator fail-closed DENY (default ON
   since 2026-08-15) blocks their fabricated tool names before routing is
   reached. Added an autouse `KAZMA_COMMITMENT_ENABLED=0` fixture to both
   files (they test routing plumbing, not commitment policy).

### New finding #23 (discovered during patch 5): CI on main is RED and
### predates this audit

Live CI history shows `failure` verdicts going back BEFORE today's audit
work (e7225be4, f46f51a2 — 134 failing tests in that run, including
crashed-chunk artifacts), 47 failing tests at patch-2 (of which 11
`test_session_directory.py` failures pass locally and are environmental).
Clusters: the commitment-choke-vs-fake-tools class (partially fixed in
item 3 above), `test_browser_boot_policy`, `test_chat_sse_fix` (flaky
chunk), `kazma-core/tests` memory/IDE files, a README-drift test, and
document-certification. The audit's original CI analysis examined
configuration, not live run history — the gate itself is honest (no
`|| true`) and has been faithfully reporting a red main. Triage of the
remaining ~40 CI failures is recommended as the next work item.

Patch-5 validation: py_compile over all edited files; telegram (6 files) +
gateway + output-routing 88 passed; MCP suites (hitl/servers-store/
win32-shim/auth + embedded gateway) 32 passed; bridge + dedup + audit
regressions 56 passed (17 regressions total in the audit file).

## 13. Patch 6 (same day, CI triage round 1 — finding #23)

Baseline: patch-3 CI run (5a1a1ea5) = 49 failing tests + 2 POISON files;
25 of the 49 reproduced locally on Windows and were root-caused:

1. **Commitment-choke class (8 more tests)** — `test_supervisor.py` (3),
   `test_retry_backoff.py` (2), `test_tools_quickwins.py` truncation (2)
   and `TestReadUrl` adjacency, and
   `kazma-core/tests/test_memory_v2_phase5.py::test_procedural_feed` (1;
   the DENY prevented the procedural recorder from ever observing a run,
   so `procedural_dags` was never written). Autouse
   `KAZMA_COMMITMENT_ENABLED=0` fixtures added, same pattern as patch 5.
2. **Stale settings.js paths (7 tests)** — the settings JS split moved
   `captureShortcut` (settings_ops.js) and `saveConnector`/`saveModel`
   (settings_hub.js) out of settings.js; `test_ui001_quick_fixes.py` (3),
   `test_ui004_ui008_gateway_misc.py` (2),
   `test_model_selection_pipeline.py` (2) now read the concatenated
   `settings*.js` family.
3. **README honesty contract (2 tests)** — the 2026-08-18 README rewrite
   reintroduced a "Production-Grade" tagline and dropped the honest
   multi-replica statement and the Swarm Panel/YAML references. README
   fixed (tests are the spec): tagline de-claimed, multi-replica honesty
   note restored, Swarm Panel + `swarm:` YAML block re-added.
4. **Stale Discord grammar (1 test)** — expects the pre-change
   `discord:{channel}` sender; current intentional grammar is
   `discord:{user}:{channel}` (audit §5.9). Test updated.
5. **`test_browser_boot_policy.py` (2)** — monkeypatches
   `playwright.sync_api` without importorskip → AttributeError (not skip)
   on CI's playwright-less install. `pytest.importorskip("playwright")`
   added.
6. **REAL BUG — `IdeService.resolve()` POSIX absolute-path bypass** — the
   unconditional `lstrip("/\\")` silently rebased POSIX-absolute inputs
   (`/etc/passwd` → `<workspace>/etc/passwd`), so the documented
   outside-root denial never fired on Linux
   (`kazma-core/tests/test_ide_service.py::test_resolve_blocks_traversal`
   failed on every CI run). Platform-absolute inputs now take the absolute
   branch and containment decides. Windows behavior unchanged (8/8 pass).
7. **fast_test.py runner fixes (unblocks the gate):**
   - pytest exit 5 ("no tests collected", e.g. the Playwright e2e suite
     under a `.[test]`-only install) is no longer classified POISON —
     that alone kept CI permanently red.
   - The runner now PRINTS the per-chunk FAILURES sections (tracebacks)
     it previously captured and discarded — Linux-only failures were
     undiagnosable from CI logs.

Still open for round 2 (CI-only, pass on Windows, now diagnosable via the
new digests): `test_mcp_server` write-file ×3, `test_rich_document_render`
×3, `test_unified_document_layer` toc ×1, document certification ×2,
memory_v2 phase2/phase_b, `test_chat_sse_fix` token-frame ×1,
`test_still_not_doing.py` Linux hang, `test_session_directory` flake ×11
(pass locally), and `test_e2e_playwright` (now benignly exit-5-skipped).

Patch-6 validation: all touched suites green locally — supervisor 24,
retry_backoff 26, tools_quickwins 21, ui001 15, ui004 18,
model_selection 33, discord 21, audit_followthrough + swarm_api 33,
ide_service 8, memory_v2_phase5 10, browser_boot (skipped with
playwright present: passes), mcp_server 26.

## 14. Patch 7 (same day, CI triage round 2 — via the new digests)

Round 1 cut CI from 49 failing tests to **6** (and the e2e exit-5 poison
is gone). Round 2 root-causes the six from the first-ever traceback
digests:

1. **CI never had numpy** — `compute_local_ppr` silently degrades to
   "uniform on seeds" without it (ppr.py:128-136), so
   `test_ppr_directed_flow_decay` KeyErrored on 'france' and the
   belief-graph multi-hop recall failed. numpy (small pure wheel) added to
   the CI install. CI had been testing the DEGRADED pagerank all along.
2. **`test_read_url_extracts_content` mocked the wrong layer** — it
   patched `httpx.AsyncClient`, but the fetch ladder builds clients via
   the scraping factory and fell through to REAL network rungs on CI
   (its sibling `test_read_url_http_error` already documented this
   lesson). Rewritten to mock the module seams
   (`_fetch_via_optional_backends` + `_get_capped`) — deterministic
   everywhere.
3. **`test_pdf_parser_arabic_logical_order_pymupdf` over-specified the
   bake-off winner** — the load-bearing invariant (Arabic logical order,
   not visual reverse) PASSED on CI; only the `extractor == "pymupdf"`
   assertion failed because pypdfium2 legitimately won the scored bake-off
   on CI's font stack. Assertion relaxed to accept either tier-1 backend.
4. **`test_still_not_doing.py` Linux hang** — still undiagnosed (the
   digest only covers failures, and the per-file retry reported only
   "(hang)"). fast_test now reruns hung files verbosely with a 20s
   per-test timeout and names the last-started test in the POISON entry —
   the next CI run pinpoints it.

Patch-7 validation: tools_quickwins + document_parsers_phase4 +
memory_v2 phase2/phase_b 75 passed locally; fast_test + ci.yml
compile/lint-clean.

Server restart required for runtime changes to take effect (per the standing
directive, the server is never restarted by the agent).
