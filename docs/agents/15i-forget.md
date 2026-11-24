<!-- AGENTS.md, piece 15i-forget. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**I. The user decides what is kept (U1, 2026-09-27, `memory/forget.py`).**
"Nothing lost" (F) guards against accidents; a user who takes a memory back
has decided otherwise, and every path that rebuilds memory must respect it.
- **Forgetting is a tombstone, never a delete.** The chat store still holds
  the conversation: a deleted episode would be re-created by turn reconcile,
  and one with its text gone looks erased to recovery. `forget_episode`
  empties question, answer and summary (`''`, never NULL -- NULL is what
  `rehydrate.ERASED_SQL` treats as erased, and it excludes tier `forgotten`
  too), drops the vector, sets tier `forgotten` (not in `RECALLABLE_TIERS`,
  no tier rule moves it), and does it to EVERY copy of the turn (the live
  write and reconcile use different keys of the chat).
- **The forget ledger** (`memory_forgotten`: tenant, chat key, turn number,
  question hash; turn 0 with no hash = the whole chat) is written under
  every key of the chat (`chat_keys`: session id and thread id, from the chat
  store). `dual_write.mirror_episode` asks it FIRST (`refuses_write`) -- it
  embeds, upserts the remote index and mirrors to Postgres even when its
  insert is ignored -- and so do the swarm bridge and the legacy restore.
  It asks under every id the chat has WHEN IT WRITES (2026-10-02): a web
  chat's thread id is saved with its first message, so a chat kept out
  before that has its row under the session id alone, and turn reconcile
  writes under the thread id; a test chat on live reached memory that way.
  Every product site that inserts an episode is declared in
  `tests/test_memory_forget.py`, and the live writers must ask the ledger.
- The facts a forgotten turn produced (`source_session`/`source_turn`) are
  invalidated and lose their subject, predicate, value, vector and every
  metadata field but where they came from (`forget._EMPTIED_FACT_COLUMNS`,
  `embedding = NULL`; an operator edit's `previous` goes with it; archived
  copies alike, `_scrub_archived_rows`). Subject and predicate were kept
  until 2026-11-21 (the memory framework audit's F5, part D: a forgotten
  "Sara was diagnosed with ..." read `sara` / `diagnosed_with` and the index
  answered "diagnosed"); `forget.finish_old_tombstones` empties older ones on
  the convergence pass. A forget cut short (`invalidate_belief` commits as
  it goes) is finished by asking again. The Postgres mirror gets the
  tombstones; the remote vector index loses the turn's AND its facts'
  vectors. Gate: `tests/test_forget_means_gone.py` (the word planted in a
  fact's subject, predicate and value; every kind of forget; restores;
  Arabic in two spellings; today's scrub, a case-only fold, an entry
  without ids or keys, and the old journal loop as negative controls).
- **Every copy is on one list** (`memory/memory_stores.py`, Memory audit
  M01, 2026-10-13): each table of `memory_state.db` and `memory_ops.db`,
  and each store outside them, with what Forget does there, its export
  section or why it has none, its retention and its bound. Forget reached
  the episode and the fact's value column only: the value stayed in the
  fact's vector and edit history, `beliefs_archive` (Hygiene and the
  180-day sweep), `memory_audit_log` (now redacted to a hash, the event
  kept), a queued turn's payload, the legacy tables, the full-text index
  segments (`purge_search_index`: FTS5 keeps a deleted row's words until a
  merge, and a merge on one level below the top keeps them too, so it
  goes through `db/fts5_purge.purge_deleted_rows`, §24 G) and
  `unified_items`, a copy of every fact and Knowledge chunk with
  no delete path that federated search served -- retired, dropped by
  `ensure_ops_schema`. A tool record keeps argument NAMES, never values
  (`procedural.strip_argument_values` rewrites older ones). What Forget
  cannot reach is the erasure horizon (`ERASURE_HORIZON`: backups, restic,
  exports, the pre-rebuild copy, freed SQLite pages, the Postgres dumps and
  WAL archive, Neo4j's transaction logs, migration bundles, the backup
  folder copy), said in the Forget dialog and the guide. Gates: `tests/test_memory_store_registry.py` (one
  word planted through every writer, forget, every column of every table of
  both databases read for it -- the forget of the day before and the
  missing purge as negative controls; every table declared, a planted one
  fails), `tests/test_memory_forget_witnesses.py` (the audit's witnesses,
  inverted).
- **Forget converges, and a restore does not undo it** (Memory audits
  M08/M10, 2026-10-13; `memory/forget_journal.py`). Every forget, keep-out,
  "remember again" and summary forget is appended to the forget journal
  (`<data dir>/memory_forget_journal.db`, its own file) BEFORE it changes
  anything -- a forget by text (`mentions`) and a turn kept out
  (`keep_turn_out`, a `turn` entry naming no memory) too since 2026-11-21 --
  and an entry of a chat records the chat's keys (`keys_detail`; a replay
  reads them with `entry_keys`: a chat forgotten and then deleted came back
  under its other key, since the store no longer named it); the memory
  database records what it applied
  (`memory_forget_applied`, inside it, so a restored older copy lacks the
  marks of later forgets). `forget_episode` runs EVERY step every time (the
  "already" path finished the facts only; summaries stayed active) and
  marks its entry applied only after the last one. The maintenance entry
  "forget convergence" (first of the memory sweeps, and at boot) applies
  pending entries in order; one that fails stays pending with every later
  entry of the same chat (a keep-out then "remember" must keep their order),
  and other chats' entries go on (it stopped at the first failure); memory
  health's "Forgets applied" check (`forget_journal.health`) warns when one
  fails or waits past a pass. It enforces the ledger on stored rows
  (`forget.enforce_ledger`: a forgotten turn's copy, a row written into a
  kept-out chat after the keep-out, a chat's past forgotten in the last day,
  an active summary from a forgotten turn) and retries remote deletes that
  failed (`forget_remote_pending`, also in the journal file). The writer's
  ledger check and insert are one `BEGIN IMMEDIATE`, and it pushes the row
  as it is now to the mirror and the remote index, never its arguments.
  Recovery and `scripts/scan_old_sessions.py` ask the ledger. A tombstone
  keeps a note's chat and turn (provenance, not content). The restore drill
  reports the forgets a backup would replay. Gate:
  `tests/test_memory_forget_converges.py` (a fault after each of ten steps,
  then one pass -> the M01 completeness scan passes; without the pass each
  leaves something; the three L05 witnesses inverted; a keep-out racing the
  writer on another thread; a pre-forget copy restored -> forgotten again,
  reconcile does not rebuild; negative control without the replay; only the
  forget code may pass `journal=False`).
- **A turn number the ledger holds may be a newer turn's** (2026-11-24).
  After an undo the chat's next turn has the same number, and the pass took
  every fact and given day of a ledgered number: what was said instead was
  forgotten within 15 minutes. `forget._ledger_until` says how far the
  ledger's word reaches: what was written by the time of the forget, and --
  while no other question is remembered under that number -- what was stated
  by then and written after it (a fact pass that had read the turn, AUD-010).
  `enforce_ledger`'s facts, `_ledgered_days` and a forget asked again of a
  ledgered turn (`_forget_turn`: the facts, archived copies, notes, days and
  changes) keep to it; `replay_rewind` binds `_TAKEN_BEFORE` as before. Gate:
  `tests/test_memory_undo_reuses_the_turn_number.py` (EN/AR, the real rewind;
  the pass by the number as the negative control).
- **"Don't remember this chat"** is the whole-chat ledger row: the writer
  refuses its turns, the post-turn worker (`_run_turn_memory`) extracts no
  facts, the past-chats search skips the chat -- and reads other chats
  without their forgotten turns. Web: the chat's menu (Memory...); chat
  apps: `/memory off` / `/memory on`. Turning it off also forgets what the
  chat left (`forget_past`).
- **What the agent stores with its memory tools belongs to the chat**
  (2026-09-27). `memory_store` and `memory_link_entities` name the chat and
  turn they run in (`tool_builtins/memory._chat_of_call`: the bound thread
  and `user_turn_index` of the turn's messages) -- on the note
  (`metadata.chat` / `chat_turn`) and on every fact (`source_session` /
  `source_turn`) -- and refuse in a chat kept out of memory. Forgetting a
  turn takes that turn's notes with it; forgetting a chat takes every note
  and every fact naming it (`_chat_notes`, `_forget_chat_facts`). Before,
  a test chat's token ("pebble-live") outlived the chat as a current fact.
  Gate: `tests/test_memory_tool_provenance.py` (every memory write in the
  agent's tool code names its chat).
- **`memory_store` writes only when the user asks to remember** (2026-11-22,
  the memory plan's finding 1, the main session's decision A). Its
  description opened "Use when the user shares personal info", so a fact the
  user simply told was kept twice -- the turn, which Kazma keeps and learns
  from, and the model's note -- and forgetting one copy left the other
  answering. Now it stores a note only when the running turn's latest user
  message, in the user's own words, asks to remember
  (`tool_builtins.memory._asked_to_remember`: `remember_request` through
  `own_words`, English and Arabic); anything else is refused before any
  store opens (`RefusedBeforeEffect`: "this turn is already remembered"). A
  swarm worker (no user message) is refused; a scheduled task's stored
  prompt is its turn's user message. Notes stored before are untouched.
  `memory_invalidate`'s description says to use it only when the user asks
  to forget or says a fact is wrong, never to record a change they told
  (finding 2). Gate: `tests/test_memory_store_only_when_asked.py` (EN/AR;
  today's tool as the control).
- **A swarm task's result follows the chat that started it** (2026-11-20,
  the framework audit's F10, part C2). The dispatch hands the task's
  conversation (`source_thread_id`, `worker_dispatch._source_chat`) and its
  space to `swarm_bridge.store_swarm_result(chat=)`: the result names its
  chat (`metadata.chat`), a chat kept out of memory stores nothing (the
  forget ledger asked for the chat too), and forgetting the chat takes it
  (`forget._chat_notes`, with no turn; a turn forgotten alone leaves it).
  A task no chat started is the worker's alone. Gate:
  `tests/test_swarm_results_follow_their_chat.py` (the dispatch without
  the chat, and a forget that does not look for results, as negative
  controls).
- **Compaction stores nothing in memory** (2026-09-27). Its summary was a
  second record of the chat under the session "compaction" and the tenant
  "default", out of reach of both controls above; every turn already
  reaches memory (F) and the weekly summaries hold the gist (J).
- **"About me" (C1, `memory/profile.py`)** is the user's own text (Settings
  → Memory, `GET/PUT /api/memory/v2/profile`, 2,000 characters, refused --
  never cut -- past that), one row per tenant in `memory_profile`, in the
  export. The supervisor reads it on every call (`get_about`, off the loop)
  and `graph_helpers._ensure_about_user` places it right after the system
  prompt and personality, replacing an earlier copy. Never inferred: a
  profile built from facts would have shown 4 past reset dates every turn.
  Changing it is admin-only under RBAC (`platform_rbac.role_allows`, Memory
  audit M11): it is guidance for every user of the tenant, like the
  personality. `tests/test_memory_about_me.py`, `tests/test_memory_tools_fenced.py`.
- **Kazma suggests About-me lines; the user decides** (2026-11, the memory
  framework audit's F13, decision 10; `memory/profile_suggestions.py`). A
  line ("I live in Porto.", "أسكن في الكويت.") is made only from a current
  fact the user stated themself (`provenance.USER_SOURCES`), never from
  Kazma's reading of a turn, a tool's fact or another member's words; in a
  shared space only from the asker's own entry, into the asker's own About
  me. It is written in the language the user said it in (the fact's turn,
  else the value's script), from a short table (`_LINES`). Nothing joins
  About me without the user's Add (`add_suggestion`: the line at the end,
  refused past the limit, never cut); Dismiss keeps it from coming back; a
  line already there is not offered. A decision is kept by the fact's id in
  `memory_profile_suggestions` (no value stored, so a forgotten fact leaves
  nothing to read). Routes: `GET /api/memory/v2/profile/suggestions`,
  `POST .../{belief_id}/add`, `.../dismiss`; the Memory page's card
  "Suggested for About me" (EN/AR). No model call. Gates:
  `tests/test_about_me_suggestions.py` (a suggester taking every source as
  the control), `tests/e2e/test_about_me_suggestions_browser.py` (by the
  sidebar, EN/AR).
- **Deleting a chat keeps its memories (S3, the owner's rule)** unless the
  dialog's "Also forget what Kazma learned from this chat" is ticked: then
  the page asks `PUT .../chats/{id}/memory {remember: false, forget_past:
  true}` FIRST (the forget finds the chat's other key through the chat
  store, which the delete empties) and deletes only if that succeeded.
  `tests/e2e/test_chat_delete_forget.py` (unticked makes no forget request).
- **Export** (`GET /api/memory/v2/export`, M22 2026-10-13): one section
  per `memory_stores` export section (`memory_api._EXPORT_QUERIES`; the
  export refuses to run when the two disagree) -- About me, facts, archived
  facts, every change to a fact, memories, weekly summaries, entities,
  entity merges, graph groupings, tool procedures, what was forgotten --
  and `not_included`, each table left out with why. A forgotten fact shows
  when, never what (no subject, predicate, chat or turn). Streamed, 500
  rows at a time, never built in memory. Gates in
  `tests/test_memory_store_registry.py` (every table with a tenant names
  its section or why not; a planted table and a section without a reader
  fail). The Memory page's "Memories of conversations" panel forgets one.
  **Every memory and weekly summary is reachable there** (Memory audit M23,
  2026-10-14): the panel listed the newest 30 memories and 40 summaries, and
  an older one could be forgotten only through the API. Both routes page
  (`offset`, `total`; the page shows "1–30 of N"), the memories route takes
  `q` (`routes_direct/memory._every_word_match`: every content word, each in
  any of its search forms -- recall's OR expression, newest first, filled the
  page with any memory sharing one word), and the probe's and "Search both
  stores" memory and summary hits carry Forget (`_forgetEpisode`,
  `_forgetSummary`, one dialog each). Gates:
  `tests/test_memory_forget_reachable.py` (every row reached page by page,
  the first page alone as the negative control; a list with Forget that
  loads without an offset fails), `tests/e2e/test_memory_forget_older.py`
  (the oldest of 35 forgotten through the page, English by paging, Arabic by
  search; ledger and tier checked).

