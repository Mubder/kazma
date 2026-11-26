<!-- AGENTS.md, piece 15g-recall. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**G. Recall injects what the question is about -- and nothing when nothing is
(Stage 2, 2026-09-26, plan §5 R1/R2/R4/R7, S4/S5/W3).** Recall used to
rank-fuse its channels and always return its top five: on live, 77 of 77
recalls injected exactly "5 beliefs, 5 episodes", and on the retrieval
benchmark no unanswerable question got an empty result and three facts in
four injected were noise.
- **One ranking, every path:** `recall._rank_by_evidence` scores every
  candidate -- local and Postgres-primary, episodes and facts -- on its
  meaning similarity ABOVE the question's background (the mean of meaning
  ranks 4-15, blended with a 0.5 prior below five samples: bge-m3 puts
  almost everything at 0.45-0.65, so an absolute cosine cannot tell "about
  this" from "near this") plus the share of the question's content words it
  holds. Below the floor nothing is injected; behind the best by more than
  the gap, nothing either; below "strong" it is shown as "(possibly
  related)". Thresholds per kind (`_evidence_rules`), each from a sweep on
  the benchmark (the comments say what each end cost). With no comparable
  vector for any candidate, words decide alone: at least half the question
  (`_WORDS_ONLY_FLOOR`). A keyword-only candidate is judged by meaning too:
  its stored vector locally, `VectorBackend.similarities` on the
  Postgres-primary path (every backend implements it). Never add a path
  that ranks by position again -- `tests/test_memory_relevance.py` replaces
  the ranker with rank fusion and every path must break.
- **Content words** (`memory/query_terms.py`): EN + Gulf/MSA stopwords,
  Arabic folded (`documents.arabic.fold_for_search`) with the article and
  one-letter prefixes handled, light plurals, whole-word prefix matching
  (`mentions`: "out" is not in "about"), an underscore separates words (as
  in FTS5). The FTS query, the mirror searches, the past-chats fallback and
  coverage all use it. A words-only search needs half the question: the
  mirror top-up (which also never re-adds a row the local database holds --
  local recall already judged it) and the past-chats fallback (more than
  half), which now actually runs, whenever recall comes back empty.
- **Facts:** candidates are content-word matches, the nearest facts by
  meaning, the facts extracted from the turns episode recall found
  (provenance: `source_session`/`source_turn`, never a substring bridge),
  and the graph walk's head. The access-count "rotation" penalty is gone: a
  fact asked about often is not demoted for it (R4). Display de-slugs the
  subject ("platform team owns ...").
- **The model sees the question AND the answer (R3, 2026-09-27).**
  `memory/episode_text.py` is the one home of an episode's texts:
  `display_text` ("User: ... / Assistant: ..." when both sides exist; one
  side is shown as it is -- a swarm result must not read as something the
  user said) is what the injected block and the tool/probe outputs show
  (`metadata["display"]`, read through `recall.shown_text`); `match_text`
  (summary, else question, else answer) stays what recall compares,
  de-duplicates and seeds the graph walk by -- used for those, the display
  changed which memories were kept and precision fell; `embed_text` keeps
  the vector question-first. Embedding the answer too was built and
  measured, and not adopted: with every threshold re-swept, precision fell
  in four benchmark categories (fact 0.71 -> 0.65, multi 0.89 -> 0.78, noise
  0.61 -> 0.57, paraphrase 0.66 -> 0.65) and answer-only questions gained
  nothing (their words are in the answer, which keyword search and coverage
  read). Gate: `tests/test_memory_episode_text.py::test_every_episode_text_comes_from_one_place`
  (no "summary first" choice outside that module; ids and dedupe keys take
  the question first and are exempt).
- **"Remember this" in either language (W4).** `memory/remember_request.py`
  is the one detector: English phrases as before, Arabic (MSA and Gulf:
  تذكر، لا تنسى، احفظ، سجل عندك، خلك فاكر، حط في بالك ...) compared folded
  and as whole words with an optional و/ف prefix -- "تذكرة" (a ticket) is
  not "تذكر". It promotes the turn to the recall tier (`episode_row`) and is
  a durable cue (`is_filler_turn`: "تذكر هذا" is eight characters).
- **Write-path details** (2026-10-13, Memory audit M26): English "remember"
  phrases match whole words, and one right after "do/did you" is a question
  ("Can you denote that" matched "note that" and became a recall-tier
  memory); `dual_write.mirror_belief` (no caller, past the trust gate, the
  ledger and supersede) is deleted; a single-valued fact's current value is
  the newest, and extra current rows are closed at its start; the deep pass
  marks its episode (`metadata.deep_pass`, `_DEEP_PASS_VERSION`) and a
  second task for it does nothing; the embedder singleton is built once
  under a lock. Gate: `tests/test_memory_write_path_items.py`.
- **A single value stays single whatever the writer labels it** (2026-11,
  the memory framework audit's F7, `belief_mutation._classify_predicate`,
  `_survivor`). Kazma's own class (`kazma_classifies`) wins over the caller's
  label: the agent's memory tools label a fact "set" by default, and an
  agent's `lives_in Tokyo` labelled a list stood beside the user's own
  London, past the trust gate. The class is read from the name's words
  (`belief_mutation._kazma_name`, as `predicates` compares names): "live_in",
  "in_lives" or "is_name" labelled a list slipped past an exact-name class.
  Of several current rows of a single-valued
  fact, the newest the user said stays (else the newest), and a newer row
  from a lower source closes where it began. On names a person holds several
  of (`_LISTABLE`: works_at, active_project, current_role) a list labelled by
  the user's own words stays a list, and stays whole until a write that may
  replace the user's value replaces all of it; every other single-valued name
  holds one value whoever labels it. **A plural name is a list**
  (`_names_a_list`, the security review of F1's second half, 2026-11-18):
  "names", "favorite_foods", "colors_favorite" put Kazma's one-value thing in
  the plural and are written as a set; "lives_in" and "works_at" keep their
  verb's "s", and "the_name" or "is_name" is still `name_is`. Gate:
  `tests/test_memory_kazma_class_wins.py` (the label first, keeping the
  newest, the exact-name class, no listable names and no plural rule as
  negative controls).
- **A list stays a list** (2026-10-19, `belief_mutation._established_kind`).
  The extractor labels each fact's type; a single-valued label on a predicate
  the subject keeps as a list (current `set` rows) is written as one more
  entry, and the "extra current rows" close never takes a list entry --
  unless Kazma itself classifies the predicate (`kazma_classifies`: the
  functional and state lists, `favorite_*`, the rotating current facts).
  Live, `user noted "test codeword is ..."` labelled functional superseded one
  of the owner's notes and closed 57 more. `scripts/memory_reopen_lists.py`
  (`memory/list_reopen.py`; `size`, then `apply --db --yes`, copy first)
  reopens entries closed that way, one audit row each. **"Forget" in chat
  forgets the turn too**: `memory_invalidate(forget_source_turn=true)` runs
  `forget.forget_belief_source` -- the fact's source turn through
  `forget_episode(by="user")`, as the Memory page's Forget -- or recall found
  the turn and the next turn learned the fact again. A value that names one
  thing (`forget._distinctive_value`: one token of 6+ characters with a
  digit, `-` or `_` -- a codeword, an order or phone number) is forgotten in
  every memory of the tenant that holds it: Kazma's own later answers quoting
  it kept it, and a new chat recalled one. A plain word is never swept.
  **"Forget everything that mentions X"** is `memory_forget_mentions(text)`
  (`forget.forget_mentions`, danger tier, 4+ characters): everything of the
  tenant holding the text -- memories, facts current, ended or archived (in
  their subject, predicate or value), weekly summaries, entities it names
  with their merges and groupings, changes, legacy rows, queued turns --
  matched by `memory/mention_match.py` (both sides folded as search folds:
  case, Arabic letter forms and marks; an id also by its slug; Kazma's own
  names for the person and itself never a mention). "About me" is searched
  and named (`about_me_holds_it`), never edited. Journaled (kind
  `mentions`: ids only, an entity's id hashed). `memory_search`
  ranks the best few matches, so forgetting its hits one by one left 5 of 7
  (live). It also removes every self-improvement lesson holding the text
  (`self_improvement.forget_text`, the Soul and its history: a lesson quoted
  the codeword into every prompt), and a forget keeps its own turn out of
  memory (`forget.keep_turn_out`, the ledger row the writer asks first: the
  request "Forget every memory that mentions X" was written as a memory of
  X) -- **only when the user's own words ask to forget**
  (`memory/forget_request.is_forget_request`, 2026-11-22, the memory plan's
  finding 16: English and Arabic, a polite question too, read through
  `own_words(questions=True)`, so never a "forget" in a quote, someone
  else's words or a text to translate). A model that retires an old memory
  on its own in a turn that states a new fact ("I moved to Porto",
  "انتقلت إلى بورتو") keeps that turn: it is an update, and the quick pass
  writes the new fact (live, the move was kept out and no new home was
  written). Gate: `tests/test_forget_only_when_asked.py` (a real supervisor
  graph and `close_turn`, EN/AR; every forget keeping its turn out as the
  negative control). A lesson never quotes the task (it appended "Task context: '<the
  user's message>'"). The memory tool also takes a
  memory id (`e_...`, from `memory_search`) and forgets that memory, and an id
  it cannot find is refused before anything is done (`RefusedBeforeEffect`):
  the model passed memory ids the first day, and "not found" was recorded as
  an unknown effect that failed the turn. Gate:
  `tests/test_memory_list_stays_a_list.py` (the old write and invalidating
  alone as negative controls).
- **Small talk is kept, never recalled (W5).** `episode_text.is_small_talk`:
  the whole message is small talk by its words, never by length ("Pixel's
  age?" is short and a question). A greeting, thanks or goodbye with a reply
  under 1,000 characters (live greeting replies ran to 531, median 141); an
  acknowledgement ("ok", "yes", "تمام") only with a reply under 200 -- it is
  often the go-ahead for a report. Every episode candidate path of recall
  (local, Postgres-primary, mirror top-up) leaves it out; the row stays. A
  small-talk MESSAGE looks nothing up: `recall` returns empty at once and
  `search_transcripts` (the past-chats fallback an empty recall runs) too.
  **Extraction's filler check follows the same rule** (2026-11, the memory
  framework audit's F8): `belief_extractor.is_filler_turn` asks
  `episode_text.is_small_talk_words` after the durable cues. A message of
  twelve characters or fewer was filler by its length, and "I'm diabetic",
  "Call me Sam", "No, Lisbon" and "عندي سكري" reached neither pass. **Small
  talk is read by whole phrases** (the security review of F1's second half,
  2026-11-18): a message is nothing but `_SMALL_TALK_PHRASES` (longest
  first), the words that may follow one (`_SMALL_TALK_TAILS`: "thanks
  man", "شكرا حبيبي"), laughter and pauses -- never every WORD small talk,
  which made "I am a man", "I am Sure", "I'm Kazma" and "no it will do"
  filler. The phrases include the replies a person answers and goes ahead
  with ("نعم", "لا", "ايوه", "كمل", "يلا", "خلاص", "ان شاء الله", "go ahead",
  "yes please", "sounds good", "I'm good"). A message past `_FILLER_MAX`
  (400 characters) is never filler, and the cues read no further. A filler
  turn queues no deep pass (one model call): the
  post-turn path and turn reconcile ask `is_filler_turn` before queueing
  `micro_consolidation`. Gate:
  `tests/test_memory_short_statements.py` (the length rule and the
  word-by-word rule as negative controls), the write scenarios' `short` category, and
  `tests/test_filler_queues_no_deep_pass.py` (tasks counted through the real
  post-turn function; every function queueing a deep pass asks, or is
  declared; the path without the check as the control).
- **The graph walk reads the seeds' neighbourhood (R5).** It loaded the
  tenant's most important facts (800 at the defaults) and walked those: a
  fact below the cut was out of reach however close to the question.
  `recall._belief_neighbourhood` reads the facts around the seeds hop by hop
  (one round past the walk's radius, so the edges between its outermost
  nodes are there); the seeds come from every entity name. Same graph as
  before whenever the old cut held everything.
- **Recall has a deadline and embeds the question once** (2026-10-13, Memory
  audit M16). `recall()` wraps the body (`_recall`) with a per-recall memo
  (`_QUERY_MEMO`): each text is embedded at most once, a failure included
  (a failed embedding was tried by every channel, four times a recall), in a
  bounded daemon thread (`_embed_within`, two in flight at most) waited on
  until `memory.v2.recall_deadline_s` (default 5 s). Past it, or with the
  embedder failing, recall answers from keywords and sets
  `RecallResult.degraded`; `memory_search` then says the search was
  incomplete, never "No relevant memories found.". Both embedders carry a
  circuit breaker (`embedder._EmbedderBreaker`: 30 s doubling to ten
  minutes, no call and no log while open, one log line per opening) shown in
  memory health (`embedder`, `status_detail: embedder_unavailable`). Gate:
  `tests/test_memory_recall_deadline.py` (negative controls: the body
  without its memo or deadline, a breaker that never opens).
- **Recall reads the question's text, bounded** (2026-10-13, Memory audits
  M17, M25). The supervisor's latest user text is
  `turn_input.extract_latest_user_text(messages, image_marker=False)`: the
  text parts only. It was `str(content)` -- on a photo turn the Python repr of
  the parts, base64 included (recall embedded 200,125 characters and built a
  6,029-term keyword query; the language lock and the router read it too).
  `recall()` reads at most `_QUESTION_MAX_CHARS` (2,000: the start and the
  end of a long paste), the keyword query and the graph walk's seeds at most
  `_QUESTION_MAX_TERMS` (32) words, each once. Gate:
  `tests/test_memory_question_bounds.py` (a photo turn through the real
  supervisor node; recall at 2,000 words within twice 20 words on a seeded
  store; the bounds lifted as the negative control).
- **Hybrid vector search merges (R8).** `HybridVectorBackend.search`
  returned the remote index's hits alone whenever it had any, so a memory
  not yet in the remote index was invisible to meaning search. Both are
  searched and merged by id; the local store's score wins (it is the source
  of truth).
- **The block carries the dates Kazma holds** (2026-11-20, framework audit
  F2 step 1, part C2). `recall` ends with `recall._date_hits`: each turn
  gets its day (`said_on`, from the candidate's `at`), each fact the month
  it became true (`since`, its `valid_from`), and a functional or state
  fact the value it replaced with its months (`before`, read through
  `supersedes_id` in the fact's tenant; a forgotten value, `object = ''`,
  is never shown). In the operator's zone (`_memory_zone`:
  `resolve_cron_zone`). `_dated_line` puts them BEFORE the text ("[since
  Oct 2026] user lives in London; before that: Paris (May 2026 to Oct
  2026)"), after the reading tag, one line. No model, no new storage; a
  read that fails leaves the dates out with a WARNING. Gate:
  `tests/test_memory_recall_dates.py` (recall without the dating step as
  the negative control). **A changed value names the conversations that
  said it** (2026-11-23, the memory plan's W4): on a fact line with
  `before`, each value is followed by "(memory e_...)" of the turn that
  stated it (`recall._stated_in`: the fact's `source_session` /
  `source_turn`, in the asker's tenant, a turn recall could still show), so
  the model can open it with `memory_read`; an unchanged fact names none.
  Gate: `tests/test_memory_changed_value_sources.py` (EN/AR; the line as it
  was as the control). **A change waiting for the user's yes is shown
  beside its fact** (2026-11-22, the memory plan's finding 17): the same
  step reads the newest open pending change on each shown fact
  (`recall._waiting_changes`, `changes.PENDING`, state open: a decided or
  expired change is not shown, another tenant's never) and `_dated_line`
  ends the fact's line, inside the fence, with `WAITING_CHANGE` and the
  value ("user lives in Lisbon; changed, not confirmed yet, to: Porto") or
  `WAITING_ENDED`; a recall rule outside the fence says to give both
  values and ask. A bare move is put to the user as a question (finding
  15), and recall showed only the held home: the real-use suite run twice
  after finding 16 answered "Where do I live now?" with the old home in 3
  of 4 language-runs. A store from before the change log shows its facts
  as before. Gate: `tests/test_memory_waiting_change_in_recall.py` (the
  real quick pass, `recall` and block, EN/AR; recall reading no change as
  the negative control).
- **A fact is found by a question in either language** (2026-11-22, the
  memory plan's finding 10). A fact's words are Kazma's English name for it
  ("user lives in بورتو"): an English question matched the name ("live",
  worth the whole fact floor) and an Arabic one no word of it, so "وين أسكن
  الحين؟" found the user's home once in four real-use runs. The words each
  fact kind is asked with, in both languages and one sense only, live
  beside `fact_names.KINDS` (`ASKED_WITH`; a favourite's kind words from
  `arabic_facts.favourite_kind_words`; `NOT_ASKED_BY_WORDS` names a fact
  meaning alone decides, with why): recall's word coverage reads them beside
  the fact's own words (`recall._belief_candidate`, `asked_words`), and the
  fact lane's keyword search (local and Postgres-primary) is asked for the
  kinds a question names (`_belief_search_words`, `kinds_asked`). The Arabic
  words for "now" (الحين, الآن ...) are not content words, as English "now"
  is not (`time_words`, part F's non-content handling); "these days",
  "هالأيام" stay content in both. The fact's vector is unchanged (W3
  measures what this table gives). Measured: the gate 36/42 -> 42/42 (every
  loss an Arabic question); Kazma's benchmark hit rate and MRR unchanged,
  precision 0.4872 -> 0.4828 (q1008 "كم صرفنا لحد الحين؟" shows one more
  turn: its answer held "الحين", a word it no longer counts); the speakers
  set and the small store unchanged. Gate: `tests/test_memory_fact_languages.py`
  (`benchmark.run_fact_languages`: five facts told by the real quick pass in
  each language, stores of one and five, asked in both, bge-m3's recorded
  vectors, `scripts/memory_bench.py fact-languages-vectors`; a word in
  another sense shows none; every predicate a quick pass writes has words in
  both languages or a reason; today's rules as the negative control, which
  lose Arabic questions only). **City and home ask where the user lives
  only when said of the user** (2026-11-22, the main session's decision
  after the real-use suite's "أي مدينة صارت بيتي هالفترة؟" found no fact):
  `fact_names.ASKED_AS_MINE`, `kinds_said_of_the_user`. English says it in
  the phrase ("my city", "my home", "home for me", "I call home"), Arabic in
  the word ("مدينتي", "بيتي", "ديرتي" after a question word or a verb of
  becoming). The word must be the thing asked, in both languages: never a
  word describing another ("my home Wi-Fi", "عنوان بيتي"), a place
  something is in ("in my home", "في بيتي") or another kind of city ("my
  favourite city", "مدينتي المفضلة"). "Which city is the capital", "the home
  page", "مدينة الملاهي" and "بيت الشعر" ask nothing. Such a question reads
  those words as "live" in the fact lane (`asked_as_mine`, for its kinds
  and its word coverage), and the asker's own fact of that kind is kept
  whatever its evidence (`recall._named_facts`, `_rank_by_evidence`'s
  `named`, both fact lanes; `_ASKER_ENTRY` is `user` or the member's
  entry). Words alone were not enough: bge-m3 puts "What is my city?" below
  an unrelated question's background for "user lives in Porto", and a
  fact's coverage weighs 0.06. A named fact clears no cut, so the
  benchmark's edge margin counts it as `benchmark.NAMED_MARGIN`. Measured:
  the 28 city/home rows of the fact-languages set 3/28 -> 28/28 (words alone
  7/28), the precision rows show no fact; Kazma's benchmark, the speakers
  set and the small store do not move (a first version showed the home for
  the small store's abstain question q805, "What is my home Wi-Fi network
  called?": hence the rule that the word is the thing asked).
- **A fact told as a turn, asked three ways** (2026-11-26, W6 step 3).
  `benchmark_kinds.run_fact_languages_kinds` is `run_fact_languages`' table
  grown by 22 kinds a model names. Each fact is TOLD as a turn and filed as the
  product files it: the quick pass, then a scripted deep-pass reply through
  `_apply_beliefs_to_v2`, so `users_names` and `name_to_file` are in it. It is
  told in English and in Arabic, in stores of 1, 5 and 30, and asked in English,
  Gulf and MSA in the first person and with the thing asked ("Which bike did I
  buy?", "شنو الدراجة اللي شريتها؟"). Each kind also has a question in another
  sense ("What can I buy here?", "شنو أشتري هدية؟"). Vectors are recorded apart
  (`fact_languages_kinds_vectors.npz`, `scripts/memory_bench.py
  fact-languages-kinds-vectors`).
  - Lost before, of 132 a way and form (first person / with the thing): EN 0/3,
    Gulf 2/16, MSA 0/9; as first built 0/1, 0/3, 0/2. With no table (meaning
    alone): 13/4, 67/35, 53/21.
  - **After the review of step 3** (23 rows a way now: a pet held by its name
    alone; other-sense rows asked in the stores of five and thirty, and rows
    that hold the added words bare): lost of 138 a way, step 3 as built EN 1/2,
    Gulf 0/3, MSA 0/2; now 0/1, 0/2, 0/1.
  - What is left: a store of one fact (no background) and "at the gym", which
    the fact does not hold.
  - Shown in another sense, by way and store (5 / 30; of 68, 64, 62): step 3 as
    built EN 36/48, Gulf 21/42, MSA 22/39; now 32/44, 17/38, 20/37; meaning
    alone 31/43, 16/34, 18/35. The table still adds 1 to 4 a way and store:
    its words for the kinds asked before this step, and a few of this step's
    by meaning ("When did the king get married?", and "أي حيوان أليف يعيش
    أكثر؟" in the Arabic-told store of five).
  - Added only where a question was measured lost:
    - a doctor's nobody's nouns ("طبيب", "دكتور", as English has "doctor"), and
      "أراجع" only beside them ("أي طبيب أراجع؟"; "متى أراجع البنك؟" asks none);
    - "أسوق" (drive), "زرت"/"زرتها" (visited, a list kind now), "قطوتي"/"قطتي",
      "my cat", "my dog" (pets), "متزوج" (married), and "أهتم" only after "أي"
      ("بأي فن أهتم؟"; "كيف أهتم بصحتي؟" asks none);
    - not "drive", "visit", "حيواني" or a bare "أهتم"/"أراجع": each has another
      common sense (the review, L1); "my X" is read from the nouns a kind is asked
      alone by, never its verbs ("my drive" is no car, M1);
    - "حالتي الاجتماعية" and "my marital status" as phrases (`ASKED_AS_MINE`);
    - "Can I eat ...?" / "أقدر آكل ...؟" at a question's head with a food after
      it (diet), the food from one list in both languages
      (`fact_names.DIET_FOODS`, the delta review N3). "Can I eat here / this /
      at home?", "أقدر آكل الحين / هذا / بالبيت؟", "What can I eat for
      dinner?" ask none, and "أتناول" is out ("أتناول البنادول", M2);
    - "عدد" as asking (L-f, "عدد كل الأماكن اللي زرتها"), and "places" / "الأماكن"
      as words that only ask.
  - Kazma's benchmark (183), the small store (164) and the speakers set (105):
    no hit, rank or precision moved (cdbcfb65e against this step, and against
    its review's fixes). Two margins did, for the better: q001 "What is my
    dog's name?" and q009 "What car do I drive?" are found by the named path
    now (0.08 and 0.15 to 1.0). The Spaces R3 study's q103 (pets) shows one
    memory fewer (re-recorded).
  - **The delta review of the fixes** (N1-N5): "أهتم" ends the question and
    "which" is of a thing ("بأي طريقة أهتم بأمي؟", "اي وقت اهتم؟" ask none);
    "my pet" is asked alone again, and "حيواني" beside "الأليف" is the user's
    pet ("ما اسم حيواني الأليف؟"; "my pet peeve" and "my pet project" ask none;
    the `also` rows of the told run); the car is asked by what or which ("Where
    did I drive?" asks none); a doctor asked ends the question ("What doctor do
    I see for a cold?", "لأي دكتور أروح للأسنان؟" ask none). Shown in another
    sense (5 / 30; of 78, 70, 68, with the review's added rows): before EN
    38/51, Gulf 23/44, MSA 26/43; now 37/51, 18/41, 22/40. Nothing lost. The
    gate: a question asks a kind in English exactly when its Gulf and MSA twins
    do (`test_the_three_ways_ask_alike`, the diet's pairs read from the food
    list), with the forms before as its controls.
  - Left, written down (the delta review, N4): "Did I visit Rome?" and "Do I
    drive a Toyota?" ask the keyword index nothing in English, while "زرت
    روما؟" and "أسوق تويوتا؟" do: "visit" and "drive" left the table for their
    other senses (L1). The told run's English questions of both kinds ("Which
    mosque did I visit?", "Which Toyota do I drive?") are found; a keyword twin
    would need the question's wording, not its words.
  - Gates: `tests/test_memory_fact_kinds.py`. The step's numbers are held there,
    and the table before step 3, and as step 3 first built it, are the negative
    controls. Every kind the tables ask has a told row, questions in all three
    ways and both forms, and a question in another sense; one helper is both the
    gate's check and its control's (a planted kind).
- **Every kind of fact, asked in either language** (2026-11-25, W6 step 2).
  `ASKED_WITH` knew seven kinds, by Kazma's own names alone; the deep pass names
  a fact freely ("intends_to", "has_occupation"), in English, so an English
  question shares a word with it and an Arabic one none. `fact_names.fact_kind`
  reads a kind from a name's words (`LISTS`, `_FEW`, a role's and an employer's
  other names) and `asked_words` gives the kind's words; new kinds have Arabic
  words that say whose it is ("خططي", "شريت"), never nobody's ("خطة المبنى",
  "كم هدف سجل؟"). A question of nothing but one kind, said of the user ("شنو
  شريت؟", "Who is my doctor?"), keeps the asker's facts of it whatever their
  evidence (`_kinds_it_is_all_of`, through `recall._named_facts`); beside another
  word the question is of that one (`ASKED_ALONE`: "وش رقم سيارتي؟" asks no car).
  "Do for a living" and what one may not eat are phrases (`ASKED_AS_MINE`).
  Only kinds a question was measured to miss are in the table. Measured
  (`memory/benchmark_kinds.py`: 23 kinds told in each language, stores of 1, 5
  and 35, 138 questions a way): lost English 11, Gulf 59, MSA 55; now 0, 0, 0,
  and the questions that use a kind's word otherwise show what they did (5, 2, 3
  of 12, by meaning). Twenty kinds asked once in other words, not tuned on:
  lost 8, 11, 6 of 40 before and 6, 10, 5 after; with the few words that run
  showed missing, 6, 5, 5. Kazma's benchmark: every one of its 183 questions as
  it was. The fact's vector is unchanged. Gate:
  `tests/test_memory_fact_kinds.py` (the table before and the words alone as
  negative controls, `scripts/memory_bench.py fact-kinds-vectors`).
- **A list is read by its kind** (2026-11-25, W6 step 2). "List all my plans"
  showed the entries of ONE name: 66 of 76 in English, 52 in Gulf, 59 in MSA (four lists told in each language, each
  asked two ways); now all.
  `recall._list_siblings` reads every name of the kind (`fact_names.LISTS`:
  plans, goals, interests, bought, attended, habits), each entry under the name
  it was told by; nothing is renamed or merged. Only where the names say ONE
  list: "uses_service" and "uses_tool", "owns" and "has_item", "went_to" were
  read as one and put eight tools into a question about delivery services. On
  the pilots' stores (no model asked) 1 of 50 blocks and 8 of 200 change, and no
  word of an answer is lost. **The name found comes first** (the review of step
  2): the kind's other names come after its own entries, and only when the
  question is of nothing but the kind or they hold a word of it
  (`recall._asks_the_kind`, `fact_names.words_beside_the_kind`): "List all the
  books I plan to read" showed 4 of 8 books among the other plans, "How many
  concerts did I attend?" 7 wrong of 12. Measured (`benchmark_kinds.NARROW`,
  4 lists in each language, 36 questions, real bge-m3): own entries of 68 a way
  5f9bd053c 68/67/67, b80d43c15 60/59/58, now 68/67/67; the kind's other names
  shown 9/5/5, 55/44/44, now 12/6/6 (after the own ones; "how many" as before).
  Gate: `test_a_list_of_some_of_a_kind_shows_its_own_first`.
- **A pattern that reads folded text is written folded** (2026-11-25): the list
  pattern held "قائمة" and "لستة" and the follow-up's "متى", spellings the fold
  takes away, so "عطني قائمة الخطط" was no list and "ومتى؟" no follow-up while their
  English twins were; "إلى مارس" read no time (`time_words`' "الى"). Gate:
  `tests/test_arabic_patterns_folded.py`: every Arabic word of a regular
  expression -- compiled or not, joined, from a table, an f-string, a class's
  attribute -- and of a comparison with folded text, in every module that folds;
  a word the fold changes stands beside its folded spelling or is declared with
  why (a pattern asked of the text as written). A table named beside a folded
  word (`x in _ORG_HEADS`, `.get`, `__contains__`), an alias of `re` and
  `functools.partial` are read too; the delta check found six more live ones that
  way ("مؤسسة" and "هيئة" filed no employer; "متى أسكن في دبي؟" filed a home;
  `own_words`' "ترى", "سؤال", "معنى"; `pasted_text`'s "نهائي", "الرئيس"),
  each now written beside its folded spelling. 25 planted ways of writing one
  and the live ones are the control. Not read: a pattern built in another
  module, `content_terms` and `str.translate` tables.
- **A list's question word in an entry's name** (the delta check, N5):
  `recall._named_by` reads the fact's name's words beside its value, so "List all
  the trips I am planning" puts `planning_trip` entries before other plans.
- **One fact, one predicate name (W6, `memory/predicates.py`).** The
  extractor names predicates freely; under two names a single-valued fact is
  never superseded (live 2026-09-27: "daily_tweet_cap" 16 and
  "tweet_daily_cap" 8 both current). Names that are the SAME WORDS (order,
  filler words and a plural "s" aside) are one name: `mutate_belief` writes a
  new fact under the name its subject already uses for those words and type
  (`canonical_predicate`, inside the mutation lock, so the trust gate still
  decides), and reconsolidation retires what is stored twice
  (`merge_same_word_predicates`: latest statement among equal sources; where
  the user's word and an inference disagree, both stay -- the extractor labels
  the user's own later words "inferred", so either choice is wrong). Never by
  meaning: bge-m3 puts "grok_next_reset" / "grok_personal_next_reset" (two
  accounts) at 0.94, above most true pairs. Names a word apart are the
  extractor's to avoid: the deep pass is given the names in use
  (`predicate_vocabulary`) and told to reuse them, and to skip one run's
  status and internal ids. Measured and gated in
  `tests/test_memory_predicate_names.py`.
