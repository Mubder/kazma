<!-- AGENTS.md, piece 59-ui-truth. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 59. What the UI says must be true (resilience plan Part I)

Each with its test (the old rule as the negative control):
- **A row shows done only when its record says it ended** (`chat.js`
  `_endedRows`): on a finished turn a row still "running" is drawn plain
  (`info`), never with a check ("Kazma is thinking…" read as a step that
  finished). `tests/js/test_rows_done_only_when_ended.js`.
- **Two model calls' narrations stay two paragraphs** (`sse_chat/_streaming._paragraph_break`):
  a blank line between them on the live path whatever the text ends with
  (one newline joins in Markdown), and on the resume leg after an approval,
  which added nothing. `tests/test_narration_paragraphs.py`.
- **A finished turn with no answer says so** (`turn_presentation` phase
  `no_answer`, "Ended without an answer", terminal, offers Retry), never
  "Completed". `tests/js/test_turn_without_answer.js`.
- **A new, empty chat offers "Don't remember this chat"** (`#chat-memory-choice`,
  `chat.js` `_watchMemoryChoice`), applied to the chat's id at once, so the
  first turn is kept out too. `tests/e2e/test_new_chat_memory_choice.py` (EN/AR,
  by the sidebar).
- **Every answer-quality row links to its chat or says it was deleted**
  (`chat_deleted`). `tests/js/test_quality_rows_name_their_chat.js`.
- **The Scheduled page's counts come from one place** (`HISTORY_CLASSES`):
  Failed plus Ended is the History tab's count, and each tab's count is the
  rows it lists. `tests/js/test_scheduled_counts.js`.
- **A switched-off provider shows "Off" and nothing else** (`cardState`,
  `checksFor`, `storedDetail`). `tests/js/test_provider_off_state.js`.
- **A file_search query that reads like a file name is matched as a name
  first** (`_named_like`, any extension, whatever the glob).
  `tests/test_file_search_names_first.py`.

**The weekly chaos drill** (plan J, `scripts/chaos_week.py`): the database
stopped, the server killed, Docker stopped, the network cut, the disk filled
to its floor, an operator reload -- each with its expected outcome read from
the target's own answers and the guard's own record (its state file and
`guard.log`), on a private install only (it refuses the live port, a folder
without `.kazma/chaos-ok`, the live `.env`, a target that is not ready, and,
on Windows, a shell that is not elevated). The database step stops what the
install runs: the service `kazma setup postgres` recorded (and the guard must
start it itself), else the container `--db-container` names. Docker is
skipped when Kazma uses no container there. A command the drill could not
run fails its step with the reason. It judged restarts by a `restarts` field
the guard never writes and stopped a `kazma-db` container a service install
lacks (2026-11-06): the drill reads only the state fields and log events the
guard's source writes (`STATE_FIELDS`, `GUARD_EVENTS`; the gate in
`tests/test_chaos_week.py`, the old drill as the negative control). Where it
runs (a VM or a separate PC) is the owner's choice.

