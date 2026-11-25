<!-- AGENTS.md, piece 15f-memory-findable. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**F. Every memory findable (2026-09-26, `docs/plans/MEMORY_NOTHING_LOST_PLAN.md`).**
Measured on live that day: meaning search compared episodes inside an
unordered `LIMIT` slice (the 60 newest of 300 were never searched), facts
inside the 400 "most important", fact meaning search ran only when keywords
came up short, and archiving had erased 76 memories.
- **A memory keeps all of its text** (2026-11-19, the owner: "Keep more,
  so find a way or a workaround to keep all text"; `memory/limits.py`, the
  one home of every length memory holds itself to). Every episode writer
  cut: 4,000 characters of a turn's question and of its answer
  (`dual_write.episode_row`, `turn_reconcile`), 2,000 of the summary, 8,000
  of a swarm result (`swarm_bridge`), 16,000 of a turn waiting in the queue
  (`consolidator._defer_turn_memory`). Now every writer stores through
  `limits.episode_text`: whole, up to `EPISODE_TEXT_MAX` per field (2 MiB,
  the web chat's request body limit: a safety ceiling nothing reaches; one
  past it keeps its start with a WARNING). Ids are unchanged (they hash the
  first 512/200 characters). **Old rows still match**: code that finds a
  turn's row by its text compares `limits.turn_key` (the first 4,000
  characters, stripped) -- turn reconcile's "already stored" count, the
  forget ledger's key (`forget.question_sha` hashes `question_key`), the
  weekly summaries' copy key; the archive's copies are matched in either
  form (`forget._scrub_legacy_copies`), the legacy-copy rule by prefix as
  before, and recovery (`rehydrate`) reproduces the old writers with
  `LEGACY_*`. Compared whole, every long turn already in memory was written
  a second time. **The cut text comes back** (`memory/text_fill.py`,
  maintenance entry "memory text fill", after turn reconcile): a turn's row
  (`e_`) whose question or answer is exactly 4,000 characters, not
  forgotten, not yet marked, is filled from the chat store and its spool
  (`chat_history.conversations_for`, which now returns each copy's tenant;
  another space's copy is never read in) when the row holds exactly the
  chat text's start and its id is that turn's -- one such text, or it is
  left; the forget ledger is asked inside the write's transaction;
  compare-and-set on the text read and the tier; the full-text index and the
  mirror outbox follow through their triggers; the vector is written again
  only when its text changed. Each row is marked once (`text_fill`: filled,
  whole, unavailable) and memory health counts them (`findability.cut_text`,
  with the swarm results and summaries no chat can fill). **Meaning search
  covers the start of a long memory** (`EMBED_TEXT_CHARS`, 4,000: the vector
  is what it always was), **keyword search all of it**. **Passages** (the
  memory plan's W5 + W1b, 2026-11-22, `memory/passages.py`, behind
  `passages.in_use()`, off):
  vectors of about 256 tokens (`limits.PASSAGE_TOKENS`, whole sentences, 32
  overlapping) of what a memory's own vector misses -- every side but the
  vector's, and the vector's own past `EMBED_TEXT_CHARS` -- in
  `episode_passages` (memory id, space, side, offsets, vector, model; no
  text). Triggers on `episodes` take them with a row's delete, a forget, a
  text change and a move of space (with the switch off they cost an insert
  nothing and a forget about 3 µs: the plan's W5); the writer fills them
  after its own vector (`write_passages`: vectors first, then a
  compare-and-set on the text), the vector repair sweep fills older memories
  (`fill_passages`, 200 a pass, behind the switch), the benchmark's seeding
  fills its stores (`benchmark.fill_passages_whole`, refusing a store
  missing any). **They never lift a memory's meaning**: counted as
  max(vector, best passage) they lost answers and precision on Kazma's
  benchmark and no threshold the plan's rule allows recovered them (the
  plan's W5 has the numbers; the main session's decision: no cosine lane of
  their own). **They bring the reranker's candidates** (W3, below): the
  memories whose best passage is among the question's nearest are read
  beside that passage, and the block shows the stretch a memory was kept by
  (`display_at(anchor=)`, W1b). Gate: `tests/test_memory_passages.py`
  (EN/AR; the writer off, a stale write and a forget as controls).
  **Relevance: a cross-encoder decides what is shown** (the memory plan's
  W3 + W2, 2026-11-22, `memory/reranker.py`, `recall._by_relevance`, behind
  `reranker.in_use()`, off: **not accepted** -- under the plan's rule no
  floor keeps every answer on the four stores with either model, and the
  reference model is several times the latency budget; the plan's W3 has
  the numbers): inside `_rank_by_evidence`, the
  one ranking of every path, the shipping model (`reranker.MODEL`, pinned,
  local files only, no remote code) reads the question beside the best 20
  candidates by today's evidence and every memory a passage brought (a
  memory's start and its passage's stretch, the better counting), a few
  pairs at a time until the recall's budget (`reranker.BUDGET_S`, all kinds
  together) ends; the probability it answers is a logistic fit of the
  evidence and the model's score together (`reranker.CALIBRATION`, per kind,
  fitted on the benchmark's first set by `scripts/memory_rerank_calibrate.py`),
  and a memory is shown at or above the kind's floor and within its gap of
  the best (`_RERANK_*_FLOOR`, `_RERANK_*_GAP`). What the budget left unread
  keeps today's rule, ranked after what was read, and the recall says so
  (`RecallResult.rerank`: "whole" / "partial"); no model on disk is today's
  rule (said once, tried again after ten minutes). Facts are not reranked
  (`_RERANK_KINDS`: their calibrated probability ranked no better than the
  evidence). Measurements replay the model's recorded scores
  (`memory_bench.py rerank-scores`, not committed while the reranker is
  off; `benchmark.ReplayReranker` refuses a pair it lacks).
  Gate: `tests/test_memory_rerank.py` (EN/AR, both recall paths, a scripted
  model; today's rule, no floor, the budget ended and passages alone as
  controls).
  **Shown bounded, read whole**: the recall block shows a memory with its
  own bounds -- **room by rank** (the memory plan's W1a, 2026-11-22,
  `recall._room_by_rank`, `episode_text.display_at`), **for the
  conversations recall rates strong only** (a "possibly related" one keeps
  the fixed bounds, `_roomy`: the block grows only where recall is fairly
  sure it holds the answer): the best of them up to `RECALL_LEAD_SHOW`
  (1,500) characters, every other `RECALL_OTHER_SHOW` (500), each side cut
  around the stretch holding the
  most of the question's words (`query_terms.word_term`: search's reading,
  folded, the article and one-letter prefixes, a light plural); when that
  stretch is not the side's start, the side's opening sentence comes first
  (up to `RECALL_KEEP_OPENING`, 120 characters, at most half its room),
  then " … " and the stretch -- a memory often names its subject first --
  unless the stretch shown alone would begin inside the opening; when the
  block's budget would leave a line out the others give way first (to
  `RECALL_OTHER_LEAST`), then the best (to `RECALL_LEAD_LEAST`). **The
  budget is the whole block's** (`max_tokens`, 1,500 from the supervisor):
  rules, speakers note, fence and lines, counted as the context trim counts
  (`summarizer.estimate_text_tokens`, Arabic at about 2.5 characters a
  token). Until 2026-11-22 it bounded a quarter of the lines' characters,
  so the frame and Arabic's shorter tokens fell outside it and a block
  passed 1,500 (`tests/test_recall_block_budget.py`, Arabic counted as Latin
  as the negative control). Recall
  keeps each conversation's first `RECALL_FOCUS_READ` characters a side
  and how much more it holds (`_keep_sides`, in the asker's tenant); a
  follow-up joined to its turn and another member's turn keep recall's
  line (`_roomy`), and a hit with no sides is shown at `RECALL_SHOW_*` as
  before. Gate: `tests/test_recall_room_by_rank.py` (the fixed bounds, the
  block without the ladder, a raw word reading, the stretch alone and a
  plain opening rule as negative controls) --
  and, when it leaves some out, a note in the
  memory's language naming how many characters and `memory_read("<id>")`
  (`episode_text.more_note`); `memory_search` and the probe show up to
  `MEMORY_SHOW_MAX` (1,500); `memory_read` (read tier, fenced, the caller's
  tenant, `memory/episode_read.py`) reads one whole, `MEMORY_READ_PAGE`
  characters a page, and a page never ends in whitespace (the data fence
  drops a block's trailing whitespace: cut at a fixed size, the pages joined
  back lost a space at each boundary); the Memory page's list and probe offer
  "The whole memory" folded (`GET /api/memory/v2/episodes/{id}/text`), only
  for a memory the preview cut -- the list route says how much (`more`,
  from `display_parts`; guessed from the two lengths, a short memory of
  several lines read as "N more"). Model inputs stay
  bounded: the deep pass reads `DEEP_PASS_QUESTION`/`DEEP_PASS_ANSWER`
  (1,500/800), weekly summaries 400/600 per turn. Gate:
  `tests/test_memory_keeps_all_text.py` (every writer, a long turn through
  the real code; old cut rows matching; the fill, the ledger, the race,
  another space's chat; the bounded views and the reader; a long turn end
  to end -- stored by the live writer, found by the real `recall` by a word
  only its end holds, shown bounded with the note, read back whole by
  `memory_read`, every character; no slice of a memory's text in memory
  code whose bound is not a `limits` name -- the old cut put back through
  `limits`, the old cut lines and fixed-size pages as the negative
  controls), and in a browser by the sidebar, EN/AR,
  `tests/e2e/test_memory_page_browser.py` (the list's fold reads a long
  memory to its last word; a short one of several lines has none).
- **Exact meaning search, every row** (`vector_engine.py`):
  `vec_distance_cosine` in SQL over every comparable row (same size, same
  model; NULL/'' version = legacy same model), NumPy chunked fallback, zero
  vectors excluded, read-only (no temp tables). Gate: no `LIMIT` in a
  candidate fetch unless it orders by distance.
- **Facts** (`recall._recall_beliefs`): meaning search always runs, over
  every current fact; ranking is §15G's evidence, with the belief thresholds.
- **Vector repair** (`reembed.run_vector_repair_pass`, 15 min, ~20 s): no
  vector, wrong size or another model's vector -> re-encoded in place,
  newest first; with nothing to repair it never loads the model.
  `global_reconsolidation` calls the same function.
- **Recovery of erased rows** (`rehydrate.run_rehydrate_pass`, 15 min):
  sources are earlier backups of the memory DB (a same-row copy whose text
  reproduces the stub), the chat store + spool and checkpoint history
  (`chat_history.py`, both backends), `noted` beliefs, `memory_store` tool
  calls (resumable scan). Anything but a backup must
  reproduce the episode id (`dual_write._episode_id` /
  `swarm_bridge.bridge_episode_id`) AND the stub (`_archive_stub`, checked
  against the old SQL). Sources must agree or nothing is restored; text is
  never overwritten; an unreadable source leaves the row pending (an outage
  is never a verdict). Verdicts live in `metadata.rehydrated` /
  `metadata.rehydrate`; memory health shows them (`memory_findability`).
- **Every turn reaches memory** (item H): `kazma_ui.turn_runtime.close_turn`
  -- the closer every transport runs -- hands a finished turn (graph
  terminal, not paused, and the `_post_turn_memory` record's turn index
  equal to the state's) to `consolidator.remember_turn`, once per turn.
  Nothing else calls it or `_schedule_post_turn_memory` (gate in
  `tests/test_memory_every_turn.py`). Until 2026-09-26 only the gateway
  handler did, so no web chat turn reached memory from 2026-08-08.
  `extract_turn_texts` pairs a question with ITS answer (it used to take the
  previous turn's when there was none); the episode `turn_number` is the
  turn index (`user_turn_index`), not the iteration count that made a
  repeated question collide into one memory.
- **A poison turn is set aside** (2026-10-13, Memory audit M27): a
  reconciled turn whose fact extraction fails `_FACT_ATTEMPTS` (3) passes in
  a row is marked `metadata.facts_failed` (a WARNING names it) and the facts
  cursor moves on; the count rides the reconcile state (`fact_attempts`). A
  locked database is never a verdict: it raises to the runner. One failing
  turn used to keep the cursor before it for good. The embedding rebuild
  (M28) loads the model first and re-encodes in place (a row keeps its old
  vector until the new one is written; it NULLed every vector and committed
  before the model loaded), and takes its pre-rebuild copy again each time
  (it kept the first one forever). `macro_sleep`'s tier moves are
  compare-and-set on the tier and last touch it read (M29: a forget during
  the sweep had its "forgotten" overwritten; `moves_skipped` counts them).
  Gate: `tests/test_memory_lifecycle_cas.py`.
- **Turn reconcile** (`turn_reconcile.run_turn_reconcile_pass`, 15 min, ~60
  s): every chat-store turn older than 10 minutes without an episode gets
  one, with its own time, through `mirror_episode`. Then the turns it wrote
  get their facts as a live turn does -- the heuristic pass at once, the LLM
  deep pass queued (`micro_consolidation`, at most `_DEEP_PER_PASS` = 60 a
  pass) -- at the turn's own time (§15H; a rowid cursor, `facts_after`).
  Repeated questions are counted, erased-row stubs count as present, and the
  cursor passes only settled sessions. On live it found 1,004 of 1,174 chat
  turns missing. **The cursors live in `memory_state.db`**
  (`memory_reconcile_state`, `turn_reconcile.read_state`, 2026-10-14): in
  the settings store a restored older memory database met a cursor already
  past the chats changed since its backup, and their newer turns were never
  memories again. Without the row (an older build's database, an older
  backup) every chat is read once. `memory.backup.restore_memory_backup` --
  the Memory page's restore -- copies the backup over the live file, then
  applies the forget journal and runs turn reconcile at once (forgets made
  since came back until the next 15-minute pass). Gates:
  `tests/test_memory_restore_drill.py` (RPO/RTO at any size,
  `KAZMA_TEST_MEMORY_RESTORE_DRILL_TURNS`; the cursor of after the backup as
  the negative control). `scripts/memory_soak.py` is the soak harness
  (private stores, a process per cycle, killed cycles, restores; checks every
  cycle), held by `tests/test_memory_soak_harness.py`.
- **Knowledge Library text is injected, never written into memory** (Memory
  audit M04, 2026-10-13). The supervisor copied up to two injected chunks a
  turn into the chat's memory (`promote_kb_hits_to_episodes`, setting
  `memory.v2.promote_kb_to_episodes`, on by default), inline in the async
  node -- a chat-store read, an insert, embeddings, the vector index and the
  Postgres mirror on the loop -- as `user_text="[Knowledge: ...] ..."`:
  crawled text recalled as history, grouped into summaries as the user's,
  and kept after its library was deleted. The promotion, its setting and
  Settings toggle, and recovery's library source are gone; the maintenance
  sweep "retired knowledge promotions" (`forget.retire_knowledge_promotions`)
  empties the copies an earlier build left -- no ledger row, the library
  keeps the text. `mirror_episode` is a loop-stall helper. Gate:
  `tests/test_memory_kb_never_in_memory.py` (a real supervisor call that
  injects library text under a loop SQLite/embedder tracer: no memory write;
  recall shows none; the old call on the loop as the tracer's control; the
  retirement; nothing writes the old source).
- **Nothing repoints live memory** (item I): no product code rebinds a
  `kazma_core.paths` function or resets the shared `dual_write` writer
  (`_reset_mirror` is a test helper; gate in `tests/test_memory_every_turn.py`).
  The Dashboard's golden eval did both inside the live server, so any turn
  written during -- and, through the writer left on its temp file, after --
  a run was stored in a file nobody read. It now seeds and recalls on a
  private database (`recall(conn=...)`), refuses the live one, and runs off
  the event loop.
- **Nothing stranded in a table recall does not read** (item J,
  `memory/legacy_tables.py`): a one-off operation on 2026-08-02/03 (never
  committed) moved 329 episodes into `episodes_archive`, which no code
  reads. The recovery sweep puts each back as a cold (`archived`) memory --
  own id, text, vector, tenant, time -- skipping an id `episodes` holds and
  a text the tenant already holds; the legacy table is left as it is. Health
  counts what is still stranded. `entities_archive` stays archived (junk
  nodes and 200-character truncations of memories held in full).
- **Conversation history has one reader** (`memory/chat_history.py`): the
  past-chats fallback and the recovery read Postgres `kazma_chat_sessions` or
  SQLite `sessions`, overlaid with the save spool (`paths.chat_spool_db`, the
  web UI's rule too), tenant-filtered, scored in SQL over every session. The
  fallback used to open only the SQLite file -- on Postgres a July leftover.
- **The past-chats search reads every spelling, and checks before it cuts**
  (2026-11-25). The question was folded and the stored text was not: 14 of 21
  Arabic spellings missed, "القهوة" as stored among them. A word that is not
  plain English letters is looked for by `documents.arabic.spelling_pattern`,
  built from `fold_for_search` itself (never a second table; a chat is not
  folded to be searched: 9 MB/s against 650), in SQLite, Postgres, the spool
  and the snippet, and confirmed with the fold. `search_sessions` ranks every
  matching chat without its text, then asks `confirm` about each in rank
  order, 15 texts at a time, until enough are confirmed: 15 rows cut first
  hid the chat that answered behind 20 with "resize". A chat must hold
  `at_least` of the terms to be ranked. **What a spelling cannot read is
  folded** (review M1): a letter only the fold reads (`arabic.folded_otherwise`,
  asked of the fold: a decomposed accent, "ﬁ", "ß", "２", "ﷺ") makes its chat
  ranked on any one term, after the others, and the words that hold such a
  letter are folded themselves; the two together answer as folding the whole
  text does (a property test over every script). A word over
  `LONGEST_SPELLED` (64) is looked for as it stands, uncached: a pattern grows
  with its word (Postgres refuses a 16,000-letter one). One search reads
  `_TEXTS_READ` (500) texts at most, best first, then newest, and says so at
  INFO; a store that cannot be searched is said at WARNING (`described_error`),
  never DEBUG. `system` rows (the old `/compact`
  wrote the prompt and "About me" as such) are not read, nor exported
  (`export_session`). Gate: `tests/test_past_chats_search.py` (both
  databases; the cut before the checks, and a class taken out of the fold,
  as the controls).

