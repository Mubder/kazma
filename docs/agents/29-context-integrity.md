<!-- AGENTS.md, piece 29-context-integrity. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 29. Context Integrity (2026-08-30 incident hardening)

Born from the @KazmaAI tweet-batch incident: 8 approved drafts vanished
between proposal and approval because deterministic trim deleted them, the
scratchpad built to prevent it was clobbered every turn, the summary net
never fired (24K→160K dead band), and a misread `what going on?` disarmed
recall. Plan + execution report:
`docs/plans/CONTEXT_INTEGRITY_HARDENING_PLAN.md`; the duplicated-stream
reproduction + fix: `docs/plans/S3_2_DUPLICATED_STREAM_INVESTIGATION.md`.
Tests: `tests/test_context_integrity.py` (56),
`tests/test_s32_stream_duplication.py` (3).

**A. The scratchpad channel is a merge reducer — never re-introduce a
replacing write path.** `SupervisorState.scratchpad` is
`Annotated[dict[str, str], merge_scratchpad]` (24 keys × 4000 chars,
oldest evicted, `SCRATCHPAD_CLEAR` sentinel for deliberate resets). The
transports must NEVER contribute a `scratchpad` key:
`build_turn_working_memory()` deliberately omits it, and the old
`scratchpad: {}` transport payload is precisely what wiped the
checkpointed value every user turn (LastValue replace). Adding any new
write path that puts a full replacement dict into the graph input
reopens the incident.

**B. Durable artifacts live in `agent/artifacts.py`, not in graph state.**
The SQLite store keyed `(tenant_id, thread_id, key)` is the source of
truth for scratchpad findings and proposals; graph state is a read-through
cache. House patterns are load-bearing: open through
`apply_sqlite_pragmas()`, DB under `kazma-data/` (universal backup for
free), GC rides the commitment-GC cadence (`worker_bootstrap.py` — no new
sweeper loop). Drafts are a row; the model reads the row. Context loss can
no longer destroy approvable content.

**C. The proposal chokepoint is the commitment resolver — the supervisor
nudge is NOT the guarantee.** `x_post` / `x_schedule_post` / `book_x_post`
refuse without a resolvable `proposal_id`
(`safety/commitment/authorize.py:_resolve_proposal_backed_post`, inside
`_resolve_send_outbound_act`); a broken artifact store denies fail-closed.
When the id resolves, the gate REWRITES `text` to the STORED text — the id
wins over whatever the model holds in context. The iteration-0 nudge in
`graph_supervisor.py` is advisory only and must never be the thing
standing between a draft and the wire. A scheduled post
(`x_schedule_post`) is the same incident with a delay — it is on the
required list for exactly that reason. The tool worker's proposal check
(`_commitment_resolve_gate`) passes ONLY the publish calls the resolver
verified in the same pass and refuses the rest (commitment layer off). It
must never refuse a verified call: from 2026-09-17 to 2026-09-24 it refused
every one, no chat post went out, and an eval test had locked that in as a
"property". `tests/test_x_post_proposal_gate.py` +
`test_a_verified_publish_stops_at_the_approval_card` pin both halves. The HITL card (Web `renderHitlCard`
+ gateway `_build_approval_prompt`) renders the stored proposal text, so
what the user approves is what publishes. That covers the time too
(Scheduled audit SCH-E08, 2026-10-14): a rebook of a cancelled, failed or
expired booking is a new key revision at the new time, a booking still live
at another time answers `booking_conflict`, and every booking reply gives
the stored time and state, `ok` only when the state is `scheduled`
(`tests/test_x_rebook_and_fire_cycle.py`).

**D. The summary net fires on EVERY trim that drops turns.** Never gate
`inject_summary_of_dropped` on a percentage-of-window again — trim fires at
`min(24K, window×0.6)` and the old 80% gate meant the net had effectively
never protected a trim. The injected note NAMES what was dropped ("4
assistant turns including 8 enumerated draft items"); under ~2K dropped
tokens the heuristic summarizer runs (zero extra LLM cost). Trims are
counted: `kazma_context_trims_total{summary=fired|missed}` +
`kazma_context_trim_dropped_messages_total` — a growing `missed` is a bug
report. **The budget is the context window** (95 %; the owner's decision
2026-10-18, `turn_input.resolve_trim_token_budget`): the 24K cap this plan
kept until real traffic was read trimmed a live day-long chat to its latest
message on every step. `agent.trim.token_budget` still sets an absolute
budget (clamped to [4000, window×0.95]). No other pruner caps the history
at a fixed size: `prune_messages_if_exceeding_cap` (30,000 tokens, every
tool output cut to 2,000 characters, saved to the checkpoint) ran first and
undid the window budget until 2026-10-22 (`tests/test_no_fixed_context_cap.py`).
**The window is configured, never
guessed** (`token_counter.resolve_context_window`): per model
`models.context_window.<model>`, else `context.max_context_tokens`, else
`memory.max_context_tokens`, else `DEFAULT_CONTEXT_WINDOW` -- 1,000,000,
shipped in kazma.yaml with installs on the old 128,000 following
(`config_defaults.RETIRED_DEFAULTS`). The prefix table that guessed windows by
model name (deepseek 64,000, gpt-4o 128,000...) is gone: stale, it capped
models below what the owner had set.

**E. `shift` is split — recall is disarmed only by an explicit pivot.**
`shift_explicit` (regex — the user verifiably said so; legacy `"shift"`
from old checkpoints counts) suppresses recall and supersedes the task.
`shift_inferred` (embedding drift) re-ranks only: recall stays ON and
`stub_prior_tool_chains(..., keep_assistant_prose=True)` collapses tool
payloads but keeps assistant prose IN FULL — a misread pivot must not
erase the thing being asked about. Interrogative check-ins
(`is_interrogative_checkin`, EN + Arabic شنو/وش/ليش/شفيه/وين/متى/شلون/شصار)
are gated BEFORE the embedder and can never classify as drift;
`_MIN_CHARS` is 25 plus a content-word requirement (length alone was never
the right signal). **An explicit pivot names the subject it leaves** ("new
topic", "let's switch topics", "خلنا نغير الموضوع") **or opens the message
with a word that drops it** ("never mind, ...", "مو مهم، ...")
(`turn_input._TOPIC_SHIFT_RE`, 2026-11-22, the memory plan's finding 11):
"switch to", "anyway", "unrelated", "forget it" and "مو مهم" anywhere were
pivots, and recall was suppressed for "did I switch to more water per
tablespoon?", a question about the user's own memory. Gate:
`tests/test_topic_shift_own_past.py` (EN/AR through `classify_turn_intent`,
the old rule as the negative control).

**F. A recovery attempt never re-streams content (the duplicated-prefix
invariant).** The SSE/WS bubble APPENDS token deltas; only `turn_complete`
has replace semantics. Once any delta of a user-visible LLM call has been
emitted, no recovery attempt of that same call may emit content deltas
again — the authoritative text arrives via the final response +
`turn_complete` backfill. Three sites enforce it: supervisor retries pass
`emit_deltas=(attempt == 1)` to `invoke_llm_chat`, the failover chain
passes `emit_deltas=False`, and `LLMProvider.chat_stream` tracks
`_emitted_any` so its blocking fallbacks yield only the final
`StreamDelta(response=…)`. Removing any one of these brings back the
`The proposal turn is The proposal turn is` incident string (locked by
`tests/test_s32_stream_duplication.py`).

**I. The trim keeps the conversation's skeleton** (2026-10-18,
`turn_input._with_conversation_skeleton`). Over budget, the deterministic
trim kept the latest message and its last tool rounds and dropped every
earlier turn: live, a day-long Telegram chat building a PDF archive of the X
posts was over its 24,000 tokens, and "send me the file here" reached the
model with nothing before it -- it listed the workspace, read all 67 posts
again (three times in one turn, each page trimmed away as the next came) and
asked which file was meant. The leftover budget now holds the earlier
requests and plain replies, newest first (16 messages, 1,500 characters
each, no tool chains), between the anchors and the latest message; it never
spends budget the trim did not leave; with the budget now the window (D
above) it is the net for a chat that outgrows even that. `x_status` gives each post's time in the operator's zone
(`created`, `time_zone`): the epoch alone sent the model to `python_exec`, an
approval card, to read it. Gate: `tests/test_trim_keeps_the_conversation.py`
(the old trim as the control), `tests/test_x_status_pages.py`.
`x_status(metrics=true)` reads the listed posts' `public_metrics` in ONE
budgeted request (`XClient.get_public_metrics`); a post X did not return is
`metrics_missing`, a failed read `metrics_error`, never a zero. And
`save_proposal` gives each X draft's length as X counts it
(`text_length.validate_text`). Live 2026-10-20 the agent answered "likes
unknown" from the posts' public pages, and asked to approve a `python_exec`
that printed `len(draft)`. Gate: `tests/test_x_post_metrics_and_draft_length.py`.
**A post's video is downloaded through the X API** (2026-10-24):
`x_download_media(post)` reads its media (`XClient.get_media`, one budgeted
read), fetches each file with `web_acquire.http.fetch_public` scoped to X's
media hosts (`_MEDIA_HOSTS`), a video's best MP4 under 50 MB (send_file's
limit), and saves into `<workspace>/downloads/x` (gitignored) for `send_file`.
Every failure is a `KnownEffectError`. Live, the agent searched the owner's
folders through six approval cards for a file nobody had downloaded. Gate:
`tests/test_x_download_media.py`.

**H. The running turn's tool outputs are not cut to fit** (2026-10-18,
`summarizer.prune_tool_outputs`). The supervisor prunes tool outputs when the
chat is over its budget and SAVES the pruned list to the checkpoint, so a cut
is permanent. The rule kept the newest 3 outputs of the whole chat and capped
every output past 1,500 characters, the running turn's included: live, an X
archive turn's `x_status` (1,571 characters) lost its fourth post as it
arrived and was cut to 250 two calls later; the archive held three posts, and
the model told the owner the tool could return no more. Now older turns' outputs
go first (to 250, then past 1,500), and only then the running turn's, past
8,000 characters, oldest first, stopping once the context fits. The running
turn starts at the last user message. Gate:
`kazma-core/tests/test_prune_tool_outputs.py` (the live shape; the old rule as
the control).

**G. Recovery spirals get an honest exit.** ≥3 turn-cumulative queries
against session/checkpoint/audit stores hunting the assistant's own prior
output force RESPOND with "what's missing + one concrete question"
(`tool_loop_breaker.count_recovery_probes`); digging only grew the history
that caused the trim.


