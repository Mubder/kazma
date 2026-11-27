<!-- AGENTS.md, piece 15g-recall-hot-path. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Nothing on the hot path parses `kazma.yaml` (S4):**
  `config_loader.install_yaml_section` -- the install root's file, parsed
  once per file version (mtime + size). Gate: `tests/test_install_yaml.py`.
- **The FTS update triggers fire on indexed columns only (S5):** an access
  bump, archive move or re-embed used to rewrite the index rows it touched.
  `ensure_primary_schema` upgrades old triggers. `tests/test_memory_fts_triggers.py`.
  With S4, recall p50 on the benchmark went 27 ms -> 8 ms.
- **`/new` promotes, never deletes (W3):** `consolidator.promote_working_memory`.
  Every hard delete of an episode, fact or entity is declared with why
  nothing is lost (`tests/test_memory_deletes.py`); the pattern-delete
  `POST /api/settings/memory/clean` (every tool fact, every entity id
  starting with four hex letters, every turn saying "retroactive") is gone.
- **Memory never runs on the event loop (S2):** `recall`,
  `federated_search`, `build_v2_health`, `build_memory_health`,
  `search_transcripts`, `promote_working_memory` and `run_golden_eval` are
  in `_LOOP_STALL_HELPERS` (`tests/test_static_gates.py`); the supervisor's
  knowledge search, the probe and federated-search routes and the packages
  page's health card call them through `asyncio.to_thread`. The
  `memory_search` tool did not until 2026-09-27: it imported `recall as
  v2_recall`, and the gate matched names (it resolves `import ... as` now,
  which also caught the tool worker's per-call `get_hitl_config`). Every
  `/api/memory` route and every agent memory tool is now checked by
  behaviour: `tests/test_memory_routes_tenant_scope.py` traces each SQLite
  statement (`set_trace_callback`) and fails one executed while an event
  loop runs on its thread -- which also catches a connection opened in a
  thread and then used on the loop, the memory admin API's shape in 17
  routes (each is now an async shell reading the body and a `_..._sync`
  body run with `to_thread`).
- **A busy extraction pool defers a turn, never drops it (W2):** the
  post-turn body is `consolidator._run_turn_memory`; with all four threads
  busy (or no thread) the turn's question and answer go to the durable queue
  as `post_turn_memory`, which the memory worker runs with retries. A queue
  that refuses too is a WARNING and a counted `enqueue_fail`.
- **Part F: the question's time, follow-ups, lists, ids** (2026-11-21,
  framework audit F9 and F2 step 3; dataset v6). No model on this path.
  - *Time* (`memory/time_words.py`, `read_time`, EN/AR): in a question
    about the past (a past verb) a period ("last week", "in August", "قبل
    أسبوعين", "قبل ثلاث أسابيع", "السنة هذي" / "هذه السنة" / "هذا العام",
    "past 10 days", "the last 3 weeks", "آخر عشر أيام", "آخر ثلاث أسابيع",
    "الأيام العشرة الماضية", "الأسابيع الثلاثة الماضية", "بداية أغسطس" and
    "بداية شهر أغسطس") is read in the operator's zone, and its words are never
    content words (`recall._without_time_words`; every question-word reader
    in recall goes through it or `_search_words`, a gate). A word that only
    shares those letters ("the last 3 emails", "قبل ثلاث صفحات", "السنة
    الدراسية", "هذي السيارة") is not a period. What was SAID in
    a period keeps only that period's turns and facts (`_turns_in_time`,
    `_facts_in_time`); what was TRUE then keeps the facts that held then,
    an earlier value included (`_belief_history`, `_held_in`). "At first",
    "before we moved" (never "before work") show a fact's earlier values
    before its current one, dated "[May 2026 to Oct 2026]".
  - *Follow-ups* (`memory/question_shape.py`): the supervisor searches a
    follow-up with the user's message before it (`standalone_query`); a
    stored follow-up is joined to its turn's line (`_with_neighbours`:
    nothing copied, so a forgotten turn leaves its neighbour's line; never
    in a shared space). The chat's own turns the model reads are left out
    (`turns_in_context`, `_not_in_context`).
  - *Lists*: a request for every memory (`asks_for_all`) gets up to
    `limits.LIST_LIMIT` of each, compact, twice the gap, and a rule naming
    `memory_list` (`memory/memory_list.py`, read tier, fenced, the caller's
    tenant, a forgotten memory never listed).
  - *A list finds a word in any spelling* (`memory/word_search.py`, W6,
    2026-11-25): what was typed and memory's text are both folded and compared
    word by word, in `memory_list`, `memory_list_beliefs`,
    `memory_list_entities` and the Memory page's three searches; where the
    full-text index answers, beside it (`OR`), never instead. `memory_list`
    looked for the folded word in the unfolded text with `LIKE`: 8 of 8
    English words found, 0-1 of 8 Arabic ones in their own spelling. "My X" is
    one Arabic word and is taken off both what was typed and what was told
    ("سيارتي" and "سيارة"), except off a name (`name_alphabets.is_a_name`: "سامي"
    lists no "سام") and the few words whose ending is their own
    (`speakers._AR_NOT_MINE`: "عادي"). It was taken off only where no row held
    the word itself, and the user's own turns hold it: "سيارتي" lost the car
    (the review of step 2); a dual, a broken plural and "our" are other words.
    A typed letter and its mark are composed before marks are dropped ("café"),
    and a digit inside a Latin word is looked for in any script ("iphone١٥").
    Only a word that may be the one asked for is folded, and only for an
    Arabic word or a number: folding each turn whole took 24 s for "gpt5" in
    20,000 facts and 50,000 turns (0.3 s now; the share of letters folded is
    the gate). Columns go to the functions as bytes (`word_search.holds`): one
    row that is no text ended the search. A `LIKE`,
    `MATCH` or `instr` over memory's text in a page or tool is declared with
    why it may compare text as written. Gate:
    `tests/test_memory_lists_any_spelling.py` (English, Gulf, MSA; its
    controls lose the Arabic words and no English one).
  - *Ids*: every recalled conversation line ends "(memory e_...)", with the
    ids joined to it. A one-digit number is a word in recall's coverage
    (`content_terms(digits=True)`; off for every other reader).
  - *An incomplete recall is said*: `recall_incomplete_note` (turn note
    `memory_incomplete`); the Postgres-primary searches raise
    (`search_state_episodes(strict=True)`), so a failed mirror is a failed
    recall, never empty; every caller of `recall` / `search_with_status`
    reads `degraded` or `failed` or is declared, and `search` (empty on any
    failure) is for Kazma's own context only (gates in
    `tests/test_memory_recall_part_f.py`).
  - *Measured, not adopted*: the store's median (and 0.85-0.95 quantiles)
    as the background, word rarity, a number weight. The background is one
    number per question: it decides admission, never order, and no setting
    kept every earlier answer. The dense Gulf paraphrases need a reranker
    (decision 14).
  - *Benchmark v6*: `dense`, `follow_up`, `list`, `temporal`; each part F
    change taken out loses its category's answers
    (`tests/test_memory_benchmark_part_f.py`; `memory_bench.py vectors`
    records the raw follow-ups too, under a long recall deadline). The
    dataset records `ids_hold_since` (`benchmark.memory_fingerprint` of v5's
    own file): a sealed set written for v5 still holds, and
    `benchmark.dataset_as_of(data, 5)` is v5 exactly (what v6 added says
    `new_in`): the Spaces R3 study replays on it against the v5 lock's
    scores (`docs/audits/evidence/spaces_r3/locked_v5_scores.json`) and
    reproduces with part F's code. A memory shown on
    another's line (a joined follow-up, a replaced value) stands by that
    line's margin; a list question's margin describes no hit (siblings, the
    summary limit) and the edge test leaves it out. A new dataset version's
    pgvector floors start unverified, the local scores labelled `local`,
    until `lock-pgvector` names a CI run.

