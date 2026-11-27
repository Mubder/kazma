<!-- AGENTS.md, piece 15j-summaries. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**J. Weekly topic summaries (C2, 2026-09-27, `memory/topic_summaries.py`).**
Recall injects five turns and five facts; one topic on live ran to 100+
turns a week (ShipX 107 and 115, the fitness app's naming 108). Once a week
has ended (a day's grace), its turns are grouped by topic and the model
writes one summary per topic, on the durable queue (`topic_summaries`),
queued by the maintenance sweep with at most two weeks in flight.
- **Grouping, measured on live:** a chat with at least
  `memory.v2.summaries_min_turns` (4) turns in the week is one topic, under
  either of its keys (`chat_history.chat_ids`). Grouping every turn by
  meaning mixed chats: the question-first vectors of follow-ups ("recheck it
  again", "schedule them") say how the user talks, not what about, and an
  audit chat, a reminders chat and an X-posts chat became one group. The rest
  -- short chats, legacy single turns, the agent's notes (`memory_store`) --
  is grouped by meaning: average linkage, merging while the average
  similarity clears the tenant's own bar (the 90th percentile of pairs of
  its memories: 0.525 on live, the same from 200 to 1,676 memories; a
  week's own percentile split weeks of few topics). A question with fewer
  than two content words follows the previous turn of its chat. A V1
  migration copy of a turn memory also holds (`legacy-*`, "User: ...
  Assistant: ...": 181 of 269 on live) is left out -- the first run wrote
  three July topics twice. One rule decides what a copy is, here and in the
  live cleanup: `legacy_tables.legacy_copies`, the question AND the start of
  the answer (the migration cut answers at ~300 characters, "…"); the same
  question with another answer is another occasion. `forget.retire_copy`
  empties a copy without a ledger row and keeps its facts -- the original
  stays remembered, and the kept id stops the legacy restore bringing it
  back (`tests/test_memory_legacy_copies.py`).
- **Versioned:** a week done by an older `_VERSION` of these rules is
  summarized again, its summaries retired first; a forgotten summary stays a
  tombstone and still keeps its topic unwritten.
- **Derived, never a source:** `memory_summary_sources` names the turns.
  `forget.forget_episode` calls `on_turns_forgotten` before it commits: the
  summary is emptied at once (`rebuild`) and written again without the turn
  (`topic_summary_rebuild`), or retired below the minimum. A summary the
  user forgets is a tombstone its week never writes again (`_covered`: a
  topic mostly made of another summary's turns is that summary, even after a
  late turn joins it). The model's text passes `prompt_fence.filter_injection`
  and a credential mask. **The store is a compare-and-set** (`_store`,
  Memory audit M05, 2026-10-13): the model call takes up to a minute per
  topic, so inside one write transaction the source turns are read again --
  a text written from a turn forgotten meanwhile is never stored (the turns
  left, if still a topic, wait as `rebuild`) -- and a summary written again
  must still be `rebuild` (a forgotten one stays a tombstone). Until then a
  forget during the weekly run landed in an ACTIVE summary, and a rebuild
  overwrote a summary the user had just forgotten. A rebuild's store also
  cuts the summary's sources to the turns it was written from (2026-10-14:
  the forgotten turn's source row stayed, and `forget.enforce_ledger` sent
  the rebuilt summary back at every check, a model call each time). Gate:
  `tests/test_memory_summary_store_cas.py` (both witnesses inverted; every
  statement that sets a summary active names the status it replaces; the
  old store as negative control; the stale source row as the rebuild's).
- **Local only:** summaries live in the local memory database, like the
  graph, and are not mirrored to the Postgres state backend; they are
  rebuilt from turns. The Memory page's "Weekly summaries" panel lists and
  forgets them; memory health counts them.
- **Recall reads them (C2b)** on every path (the Postgres-primary one from
  the local database): every active summary of the tenant, ranked on
  evidence with the summary thresholds (`recall._SUMMARY_*`), at most two,
  shown AFTER the history ("## Weekly Summaries", week and title first, cut
  at 900 characters). The background is the MEDIAN of the question's
  similarity with every summary (`_summary_background`): bge-m3 puts a
  paragraph 0.14-0.19 below a turn, so the episodes' background sank true
  summaries below zero, and the episodes' "ranks 4-15" window fills with a
  topic summarized every week -- at 97 live summaries "list me all my
  subscription resets" (ten reset summaries) found none. Against the median
  every "catch me up" question of 23 real ones scored 0.235-0.439 and every
  unrelated one at most 0.170; floor 0.21. Benchmark v4 carries such a topic
  (eleven weekly training summaries): the ranks window hid the race result,
  the median finds it. Shown above the history, summaries pushed answering
  turns down (MRR 0.875 -> 0.868), so they come after it. Only `active` is
  ever recalled -- forgotten, `rebuild` (a forgotten turn's) and retired
  rows never are (`tests/test_memory_summary_recall.py`).
- Gate: `tests/test_memory_topic_summaries.py` (grouping identical to
  scipy's average linkage; each rule with a negative control).
- **Roll-ups above the weeks: a month, a project** (2026-11, the memory
  framework audit's F13, part G; `memory/topic_rollups.py`). A roll-up is a
  row of `memory_summaries` with its `level` (`week`, `month`, `project`),
  so recall, the page's list, Forget, the export and the space walk treat it
  as any summary. A month: its weekly summaries (a week is the month of its
  Monday), once the month has ended (a day's grace) and settled, at least
  `MIN_PARTS`. A project: weekly summaries that are one topic over at least
  `PROJECT_MIN_WEEKS` weeks, by average linkage at the tenant's own bar (the
  90th percentile of its summaries' pairs, never below `PROJECT_BAR_FLOOR`;
  measured on the benchmark: the training topic's 11 weeks are one group
  from 0.60 to 0.70). It names its sources twice: the weekly summaries in
  `memory_summary_parts` and their turns in `memory_summary_sources`. So a
  turn forgotten empties it (`on_turns_forgotten`, through the turns), and
  a weekly summary forgotten, retired or emptied empties it
  (`empty_rollups_of`, from `forget_summary`, `_retire`, `_retire_older`).
  The sweep (`queue_due_work` -> `plan_rollups`, at most six a pass) marks it
  `rebuild` with its parts; the queue's `topic_summary_rebuild` task writes
  it (`rebuild_summary` -> `rebuild_rollup`: one model call, the weekly
  summaries fenced), waiting while a part waits itself, retired below the
  minimum or when the model skips it. The store is a compare-and-set: every
  part still active and unchanged since it was read, the roll-up still
  waiting. A forgotten roll-up is a tombstone its month or project never
  writes again. Recall shows it under its own label (`rollup_label`:
  "October 2026", "Project, Sep 2026 to Nov 2026") and the block's header
  becomes "## Summaries"; a question about what was said in a period keeps
  the summaries of that period (`_summaries_in_time`, as part F keeps turns).
  A project is ranked as a weekly summary is, by its newest day
  (`period_end`), against the weekly summaries' background (a roll-up
  restates its weeks). A month's roll-up is a candidate only for a question
  whose period it overlaps (`_overlaps`): as a topic it held every topic of
  its month and was shown for "Where do I live?". A question that names
  nothing but talk and its time (`question_shape.asks_what_was_said`:
  "What did we talk about back in June?", "شنو تكلمنا عنه في يونيو؟") is
  answered by the period's month roll-ups by the share of it each holds
  (`_months_said_in`; by meaning June measured 0.08 against the floor of
  0.21), recorded in the benchmark's trace against a cut of nothing. A
  summary's stretch is taken from its key in the operator's zone
  (`topic_rollups.stretch_of`, recall's `_spans`; a stretch without a key is
  read at midday), never as stored: a month a server wrote in its own local
  time, compared as stored, answered a June question with July as well in
  UTC and May in Kiritimati (W6 step 5's follow-up review, L-a; gate: the
  June question in EN and AR in four zones, the stored stretch as control).
  Weeks and months are named and filed from their keys too (`rollup_label`,
  `_week_month`, `_month_settled`; a gate reads every place that dates a
  stored stretch from source).
  Gates: `tests/test_memory_rollups.py` (a forget empties a roll-up built
  from it: through a turn and through a weekly summary; a roll-up that names
  no source, and a store without the re-read, as controls),
  `tests/test_memory_benchmark_rollups.py` (the dataset's roll-ups are the
  ones the planner makes; the `rollup` category with and without them;
  months ranked as topics, and the month questions by meaning alone, as
  negative controls). Dataset v7 (the `rollup` category, every v6 vector
  unchanged) was locked with `--rebaseline`: every v6 question scores the
  same or better on the v7 store; overall MRR and precision are lower only
  by the new category's (a summary is shown after the turns and facts).

