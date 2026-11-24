<!-- AGENTS.md, piece 07-hitl-approval-gates. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 7. HITL Approval Gates (3 execution paths + 1 registry — all must stay wired)
There are **three independent HITL execution paths** and **one decision
registry** (§30). Breaking any execution path creates an unattended-danger-tool
gap. Minting a second gate from `LocalToolRegistry.execute` on the web/chat
path (H-8) reopens ghost cards. Decision truth = `hitl_gates.db`; execution
truth = LangGraph checkpoint. Surfaces render; they never infer Approved.

**A. Graph interrupt() — single-agent chat (Web SSE + Telegram/Discord/Slack)**
- `graph_tool_worker.py:tool_worker_node` calls LangGraph `interrupt()` for
  danger tools (wired from `graph_builder.py` closures — do not look for the
  gate body in `graph_builder.py`)
- Gate is active ONLY when `hitl_config` is passed to `build_supervisor_graph()`
- Required build sites: `agent_runner.get_streaming_graph()`, `agent_runner._ensure_graph`,
  and `app.py` startup recompile into `_graph_holder`. Omitting HITL on any site =
  dormant gate on that path.
- Resume: `graph.ainvoke(Command(resume=…), config)` via `POST /api/approve/{thread_id}`
  (SSE — the Web SoT), or gateway `/hitl approve|deny {thread_id}`. The
  WebSocket takes no approvals: it refuses `approve_tool` (§31 D).
- State persists in the checkpointer — paused turns survive restarts
- Gateway approval notification throttles reset at a fresh operator request
  (`graph._handler_body`, before building its new state), never at an approval
  resume. Identical args can represent a new deliberate operation after a
  refusal/expiry; keeping the old three-minute mute created an invisible real
  Telegram gate (2026-10-09). Within-turn duplicate/burst suppression remains.
  Gate: `tests/test_gateway_approval_card_retry.py` (all three transports,
  unchanged throttle as negative control, resume and other-thread isolation).
- Double-gating prevention, for ONE call (Swarm audit SW-S01, 2026-10-14):
  the graph binds `_graph_hitl_gate_ctx` / `_hitl_approved_ctx` to the tool it
  decided about (`safety/call_authority.py`: `call_authority(var, tool)`), and
  that call's first execution consumes it (`claim_call_authority`). Work the
  call starts -- `dispatch_swarm`'s task, a thread, another tool -- finds it
  spent and asks the bus; detached starters run in `detached_context()`. The
  old step-wide booleans travelled through `asyncio.create_task`'s context copy,
  and a swarm worker's `file_write`/`send_message` ran with no card anywhere.
  §30's "decision truth = hitl_gates.db" holds for detached swarm work and the
  bus too: a bus request is a gate row (mechanism `swarm_bus`) with the human
  actor and its own `timeout`/`cancelled` endings.

**B. Swarm bus — `/swarm` + IDE `LocalToolRegistry.execute` path**
- `tool_registry.py:execute()` calls `safety.check()` (async) for danger tools
- `check_sync()` is **fail-closed** (default): blocks danger tools when no real
  bus adapter is present. `allow_headless_danger=True` is the test/dev escape hatch
- Canonical floor, ON by default since audit 2026-09-16 F-5: CANONICAL is always
  unioned into the effective `require_approval_for`, so Settings/YAML cannot
  narrow below it. `KAZMA_HITL_CANONICAL_FLOOR=0` is the warned opt-out. (It
  was an opt-in `=1` from 2026-08-19; docs describing it that way are stale.)
  The drift warning repeats every 15 min either way.
- Bus adapters: `TelegramBusAdapter`, `DiscordBusAdapter`, `SlackBusAdapter`
- App wiring: **one** adapter if only one platform; **`FanOutBusAdapter`** when
  multiple are configured. NullBus = internal-only / fail-closed danger.
- **One request, one decision** (`swarm/shared_approvals.py`, Swarm audit
  Stage 1, 2026-10-14; replaces Wave 6 H-12's tri-state vote). `check()` opens
  the request under a freshly minted id bound to the tool and an arguments hash
  -- never an id from the tool's arguments (SW-S02: one click approved every
  later call carrying that `task_id`). Every platform's card shows every
  argument, bounded and marked when cut (`swarm/approval_card.py`, SW-S03), and
  waits on ONE shared row (`swarm.approval.<id>` in the settings store,
  compare-and-set). The first human decision on any platform decides it -- an
  Approve, or a Deny, which is final (owner decision D7) -- and every card shows
  that outcome (SW-S05). The request's deadline is the only one: `timeout` and
  `cancelled` are outcomes, never a deny (SW-S04). Do not bring back per-adapter
  timers or votes. This is still **not** web `claim_gate`.
- H-9: `is_danger_tool()` is `safety.hitl.approval_floor()`, the floor
  `requires_approval()` uses (SW-S11: it gated only `danger`/`unsafe`, so an
  unclassified tool passed the bus)
- Approval buttons: `shared_approvals.decide_from_button(data, actor=,
  platform=)` from each platform's click handler; no adapter instance is needed,
  and a decision that cannot be recorded is a WARNING with the actor
  (`kazma_swarm_failures_total{site="approval_resolve"}`). The gate row records
  the human who pressed. Gate: `tests/test_swarm_bus_approvals.py`.
- **The Web dashboard answers the same request** (SW-S03, 2026-10-14). Every
  card carries `approval_card.web_link(id)` (`ApprovalRequest.web_link`, shown
  before the code block): `/swarm?approval=<id>` on `KAZMA_PUBLIC_URL`. The
  Swarm page's panel (`swarm_panel/routes_approvals.py`: GET for any signed-in
  user, `POST .../decide` admin) shows `shared_approvals.describe(id)` -- the
  whole call from the gate record, the card's hash, the state -- and decides
  with `resolve(..., platform="web")`. `describe` answers only a `swarm_bus`
  gate: a chat approval is not that page's to show. The id comes from the
  template (`data-approval-id`), never `location.search` (a soft navigation runs
  the script first). A `swarm_bus` gate is never a "twin" of another
  (`hitl_gates._lookup_live_gate`, `_supersede_pending_twins`): two identical
  calls asking at once are two requests; matched as twins, the second's outcome
  was recorded on the first's row. Gates: `tests/test_swarm_approval_page.py`,
  `tests/e2e/test_swarm_approval_panel.py` (EN/AR, reached by soft navigation).

**C. Pipeline checkpoints — swarm PIPELINE tasks** (separate from A and B)
- `engine.py:_handle_pipeline_checkpoint` + `approve_checkpoint`
- Every ending of a task goes through `SwarmEngine._finalize_task`: it saves
  the task, tells the panel, and (`_close_open_checkpoint`) closes a paused
  pipeline's checkpoint — entry, auto-reject timer, gate row. Cancel skipped
  the close and left Approve on cancelled tasks. Code that sets a terminal
  status itself must be declared in `tests/test_swarm_paused_task_endings.py`
  with how it covers those three.
- A pipeline paused before a restart is restored as a PAUSED in-flight task
  (`_active_tasks`, Swarm audit SW-E09: the history is an LRU, and an evicted
  restored pipeline could not be cancelled and came back paused at every boot);
  paused tasks do not count against admission. `reject_checkpoint`
  decides "already finished?" BEFORE the handler runs — the handler marks the
  shared task failed, and judging after it left restored rejects unsaved
  (2026-09-25: four `200`s, still paused at the next boot).

**An always-ask tool takes a one-time approval only** (2026-10-16).
`ALWAYS_HITL_TOOLS` (X writes, and `request_path_access`) are never skipped
by YOLO or a grant, so their card offers Approve once / Deny only: the
interrupt carries `always_ask`
(`graph_tool_worker`; `yolo_allowed` is no marker -- it is also false wherever
YOLO is off for the install), every hop to the page passes it (the SSE frame,
the socket card, the pending-approvals items, the client copies), and the
chat card and the approvals panel drop "Allow tool". The approve route
refuses a `tool` or `yolo` answer for one before anything is claimed or
resumed (409 `one_time_only`, the gate stays pending), and `grant_tool`
never stores one (`hitl_grants.one_time_only`). Live 2026-10-16 an "Allow tool
(session)" click on an x_post card -- beside a tip saying grants cannot skip
the tool -- approved THAT call and published a post the owner read as "it
will still ask me before posting". Gates: `tests/test_always_ask_one_time_only.py`
(the route without the refusal as control), `tests/e2e/test_always_ask_one_time_approval.py`.

**A folder-access request always asks** (2026-11-01, the owner's decision):
on 2026-10-26 a YOLO window let `request_path_access` run with no card (the
daily digest's "ran without asking" found it), and a path grant widens what
every file tool may read or write for the session. It is on
`ALWAYS_HITL_TOOLS` (and `swarm/safety._ALWAYS_HITL_FALLBACK`, the same
list). It is not public: its card keeps the ordinary header and says it asks
every time (chat apps: `ASKS_EVERY_TIME`; web: `chat.hitl_tip_always`), and
`tests/test_public_action_cards.py` declares it in `NOT_PUBLIC`. **A YOLO
window is not a gate that is off:** `get_hitl_config` gives `enabled: False`
under YOLO and `yolo: True` when the gate is otherwise on, and the tool worker
shows an always-ask tool's card there. It refused every always-ask tool under
YOLO as a disabled gate ("Re-enable safety.hitl.enabled"), X posts too; a gate
the operator turned off still refuses them. Gate:
`tests/test_folder_access_always_asks.py` (the old list and the config without
`yolo` as negative controls).

**A fetch after outside text asks, narrowly** (2026-11-11, the owner's
decision; `safety/outside_text_fetch.py`, the one decision). `read_url`,
`crawl_site` and `web_search` are read tier, so text a turn read could send
data out in a URL. Once a turn has read outside text -- a tool result inside a
data fence (`prompt_fence.holds_fence`: only the fence helpers write one), or
a user message quoting a fenced document -- a call that fetches a URL
(`FETCH_URL_FIELDS`, an MCP tool's `url`-like argument) asks ONLY when its
host was named neither in the user's latest message (outside its fences; a
swarm task's prompt) nor in a search result of the same turn
(`SEARCH_TOOLS`, read with `serp.extract_urls_from_search`), and is not
`KAZMA_PUBLIC_URL`'s host. An ordinary one-time card; YOLO and standing
grants skip it (`hitl.standing_approval`, said by `note_ran_without_asking`);
its note says why (`card_notes` code `outside_fetch`, `safety/card_notes.py`
`NOTE_TEXT` = catalog `chat.hitl_note_outside_fetch`, EN/AR). Both chokes
ask it: the chat graph's split (`graph_tool_worker`, state in the declared
`SupervisorState.outside_text`, updated after each step, kept across an
approval pause, reset by `initial_supervisor_state` and by the supervisor at
iteration 0) and `LocalToolRegistry.execute` (a swarm dispatch binds
`outside_text_scope` around the worker with its prompt; the registry asks
`SafetyMiddleware.check` with `force_danger` and `card_notes`). A new
fetching tool is declared in
`FETCH_URL_FIELDS` or `NOT_FETCHES`. Gate: `tests/test_outside_text_fetch.py`
(a real graph and scripted model; the graph without the check as the
control).

**A task a turn starts, a page it saved, a page its history keeps**
(2026-11-15, the defensive check before the independent review: a sub-agent's
goal counted as the user's words, so a delegated fetch to an attacker's host
ran with no card). A task started after outside text carries the turn's state
(`delegation_state`): a sub-agent's goal, a swarm task's prompt, a handoff's
task and a scheduled task's stored prompt are the model's words, never the
user's. The task starts read, and only the real user's hosts
(`OutsideText.user_hosts`) and the turn's search hosts count as named. The
chat tool worker binds `turn_scope` (a snapshot from before the step); a swarm
dispatch binds `outside_text_scope(prompt, inherited=, context_text=)`, and
the innermost scope wins. Carriers: `SubAgentManager.spawn`, `dispatch_swarm`
metadata, `HandoffRequest` (via `dispatch_helpers.with_outside_text`), and the
cron column `outside_text` (cleared by a prompt the owner edits on the page).
`DELEGATIONS` names every DELEGATE and SCHEDULE tool. A call that fetched an
address and ran marks the turn read whatever it returned; `READS_OUTSIDE`
names the readers with no address argument. A file Kazma saved from the web
(`research_files.SAVED_FROM_WEB_FIRST_LINES`) comes back fenced from
`file_read` and `file_search`, and from `shell_exec` and `python_exec`
(2026-11-16: `cat`, `grep -r` or a snippet's `open()` returned it raw) when
the output holds a saved page's first line, or a file the command names or a
folder it walks (`shell_paths.read_paths`), or a path a snippet's string
names, is or holds one (`research_files.any_saved_from_web`; gate
`tests/test_saved_page_fenced_everywhere.py`). At iteration 0, after stub and trim, a fenced
tool result or user message in the kept history, or the Knowledge block,
makes the turn read; Kazma's own system notes do not; hosts the kept history
named stay named. Fence markers in disguise (spaced, fullwidth, zero-width,
Unicode dashes, DeepSeek's tokens) are redacted on a folded copy
(`_fold_for_markers`, `_FOLDED_MARKER_RE`), `filter_injection` reads it too,
and `_SENTINEL_RE` is linear. Hosts from search results still count as named
(the owner's call). Gates: `tests/test_outside_text_delegation.py`,
`tests/test_fence_marker_variants.py`, `tests/test_regexes_stay_linear.py`.

**A draft is published only after the user has seen it, on a card that says
it publishes** (2026-10-16, the same incident: "check the metrics" saved a
draft and asked to post it 2 s later, in that turn, and the card was approved
as part of the reading). `safety/commitment/proposals.bind_turn` names the
running turn (thread + `created_at`, the effect journal's turn scope) in
`tool_worker_node` before the commitment gate; `save_proposal` stamps the
draft with it (`saved_turn`); `_resolve_proposal_backed_post` refuses a draft
stamped with the running turn, or stamped with no turn running
(`drafted_this_turn`), telling the model to show the draft (its text is in the
refusal) and post after the user answers. An unstamped draft (a page, an older
build) is not judged. The card names the public action
(`safety/public_actions.PUBLIC_ACTIONS`, catalog `chat.hitl_public_<tool>` and
`_btn`, chat.js `_PUBLIC_ACTION_LABELS`: one list, held to the public ones of `ALWAYS_HITL_TOOLS`):
"Publish to X — public, can't be undone", the post first and whole, **Post now**
-- on the chat card (which keeps that title on every pending render,
`data-pending-title`), the approvals panel and the chat apps, whose keyboards
take `approve_label` and drop "Approve for task" (`task=False`), and whose
`/hitl approve_task` on an always-ask gate approves nothing. Gates:
`tests/test_proposal_seen_before_publish.py`, `tests/test_public_action_cards.py`,
the e2e above (two turns, real graph, browser EN/AR; all fail on the old code).

**X Studio drafts from the operator's reference documents** (2026-10-26,
`x_api/references.py`). `connectors.x.drafting.reference_files` (Settings → X,
up to three text files, 40,000 characters together) are read whole at every
Generate: the drafter gets them fenced as the facts its posts may state, the
fact check as evidence a claim can cite (`evidence.source_hold` accepts
`kind: reference_file`, bound to the file and the hash of the text read, dated
by the file's last change). A file that cannot be read stops the draft before
any model call. For posts about Kazma itself the reference is
`docs/docs/guide/what-is-kazma.md`. Gate: `tests/test_x_drafting_references.py`
(brief-only drafting as the negative control). **Chat drafts through the same
pipeline**: `x_draft_posts` (x-publisher skill, write tier) runs `draft_posts`
and saves under the running turn (`saved_turn`, so never published in it);
product knowledge and `save_proposal`'s description send X post writing there
(`tests/test_x_draft_posts_tool.py`). **A post has its own length**
(`connectors.x.drafting.post_length`, full by default,
`reply_style.with_post_length`; replies keep the reply length;
`tests/test_x_post_length.py`).

**X model calls turn hidden reasoning off** (2026-10-26). `deepseek-flash`
thinks by default and its reasoning counts against `max_tokens`: an X check
given 1,800 tokens got 1,800 reasoning tokens and no answer, reported as
"invalid JSON". `x_chat` passes `reasoning="off"` when the client's
`can_turn_off_reasoning()` is True; `LLMProvider._chat_payload` then sends
DeepSeek's `thinking: {"type": "disabled"}` -- to DeepSeek's own API only
(an unknown field can be refused elsewhere; providers with their own `chat`
never get the argument). A refused switch is retried once without it. A
verifier reply cut off at its limit is `_VerifierCutOff`, never "invalid
JSON". Gate: `tests/test_x_reasoning_off.py`.

**The whole X API, from X's own spec** (2026-11-17; live, asked on WhatsApp
for new followers, the agent opened x.com in the browser).
`x_api/x_api_operations.json` is X's OpenAPI spec reduced
(`scripts/x_api_catalog.py --write`, one operation a line);
`x_api/operations.py` is the one answer to what each is: `read`, `stream` (read at
most 60 s / 100 events), `write`, or `refused` with the reason (posting and
deleting a post go through `x_post` / `x_delete_post` and their drafts and
gate; media upload is `x_upload_media`; an answer that is a credential, a
file body). `x_api/api_call.prepare` checks every call before it is sent:
path values against X's pattern and `_PATH_BREAKERS`, declared query values
only (type, enum, range), declared body fields only, required ones, 64 KiB.
Tools: `x_api_operations` and `x_api_read` (read tier; one read from the X
read budget, the answer fenced), `x_api_act` (danger, `ALWAYS_HITL_TOOLS`, a
public action "Act on X with your account", CRITICAL so no swarm worker
runs it; a refusal from X is `KnownEffectError`, an unknown outcome says so),
`x_upload_media` (chunked, alt text; `media_ids` on `x_post` /
`x_schedule_post`). Three sign-ins (`x_api/auth_tokens.py`, chosen in
`operations.AUTH_ORDER`): the OAuth 1.0a keys, Sign in with X (OAuth 2.0 PKCE,
`x_api/oauth2_sign_in.py`, `/api/x/oauth2/*`; the verifier rebuilt from the
state with `KAZMA_SECRET`, never stored; used only with the scopes the
operation lists; refreshed before its two hours end; its tokens are
`CREDENTIAL` in `settings_restore`) and the app's Bearer token. Settings → X
→ The whole X API. A secret setting is cleared with `delete`, never an empty
write (the store refuses it: X's Disconnect left the four keys in place until
this day). Gate: `tests/test_x_api_operations.py` (every operation
classified, the shipped catalog equal to the script's rendering, hostile path
values, the sign-in end to end with another browser refused, the old
Disconnect's empty write as the control).

**Danger tool list SoT (must stay one list):**
- **Canonical:** `kazma_core.safety.hitl.CANONICAL_DANGER_TOOLS`
- **YAML:** `kazma.yaml` `safety.hitl.require_approval_for` (parity-tested by
  `tests/test_agent_skills.py::TestHitlWiring::test_yaml_parity` and
  `tests/test_hitl_wiring.py` — the tests compare SETS; `hitl.py` groups the
  tools thematically while the YAML list is alphabetical. Add a new danger
  tool to BOTH or the tests fail)
- **Settings UI / ConfigStore:** `safety.require_approval_for` — consumed by
  `get_hitl_config()` (runtime override)
- **Swarm bus:** `swarm/safety.py` `_EXTENDED_DANGER` is a materialized copy of
  CANONICAL with identical contents (CANONICAL is a tuple, so it cannot be the
  same object — spawn tools only if on CANONICAL)
- **MCP tools:** `classify_mcp_tool()` name patterns (write/exec/delete → danger).
  Gate is in `UnifiedToolExecutor.execute()`

**Diagnosis map (multi-path “X relates to Y”):** `docs/docs/ops/diagnosis-map.md`

