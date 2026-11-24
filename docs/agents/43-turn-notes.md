<!-- AGENTS.md, piece 43-turn-notes. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 43. A turn's notes are replaced every turn, never stacked (`kazma_core/turn_notes.py`)

A turn's system notes describe THAT turn: the environment block, the
language lock, the recall block, the task ledger, the "latest message" pin,
Knowledge Library and IDE context, the working-memory anchor, one-shot notices.
`SupervisorState.messages` has no reducer, so the checkpoint saves them with
their turn and the next turn's history holds them again. Measured 2026-09-29
through the real web route: the fourth turn of a chat sent the model sixteen
system notes (four "LATEST USER MESSAGE PRIORITY", four task ledgers, three
recall blocks) and the environment block of the workspace the user had
switched AWAY from -- `build_turn_messages` deduped notes by their first 80
characters, which every note of one kind shares, so each later turn's fresh
block was dropped. Stale notes cost tokens on every call, moved the "stable"
prompt-cache prefix every turn, and contradicted the current turn (an old
clarify-only lock, a notice to repeat verbatim).

- **`TURN_NOTE_KINDS` names every kind by how its producer begins the note**
  -- the only mark a note already in a checkpoint carries.
  `build_turn_messages` (SSE, WebSocket, voice and the gateway all use it)
  starts a new turn from the history WITHOUT any turn note; each producer
  adds this turn's copy, one per kind. The 80-character dedupe remains only
  for notes that are not turn notes (the base prompt). Not turn notes, never
  stripped: the base prompt and personality, "About me", `[CONTEXT SUMMARY]`,
  the abort marker.
- **A new per-turn note needs a kind.** `tests/test_turn_notes.py` builds
  every producer's note and checks it is recognised (inline notes by their
  literal in the producing module), fails on a registered opening nothing
  writes, and fails on a kind with no producer -- each with a negative
  control. A note that must persist must not begin with a registered opening.
- **A retired kind is still stripped, and nothing writes it**
  (`turn_notes.RETIRED_TURN_NOTE_KINDS`). The procedural store's tool
  tallies ("file_read (C=0.99, n=812)") are no longer added before a reply
  (2026-11, the memory framework audit's F13, decision 13): a tally is not a
  procedure. Reading the code for the change showed the injection had never
  worked: it read rows by column name on a connection with no row factory,
  raised `TypeError` and was skipped at DEBUG, every turn, after opening the
  memory database. The tallies stay for health and the Memory page's tally
  browser (`match_procedural_dags`). Gates:
  `tests/test_tool_tallies_not_injected.py` (no read of the store in a real
  turn; the old block as the control), `tests/test_turn_notes.py`
  (`test_nothing_writes_a_retired_kind`, a planted writer as the control).
- **Approval scope facts belong to their tool batch** (2026-10-09).
  `APPROVAL SCOPE (runtime decision facts):` is registered as `approval_scope`.
  Fresh user turns strip that instruction while retaining assistant/tool
  history; approval resumes keep the checkpoint unchanged. An unregistered
  denial note made the live Slack model claim a later request was denied
  without creating a new gate. Gate: `tests/test_turn_notes.py` covers both
  turn rebuild paths, approval/denial history, and the old unregistered note
  as a negative control.
- **The IDE chat's open file is a fenced turn note** (`source="ide_context"`,
  cut at 2,000 characters). It was glued to the question, so memory, the
  chat store, the chat's title, the language lock and the Knowledge lookup
  all took "The user has this file open in the IDE: ..." as the user's words.
- **The gateway rebuilds a turn on its history through
  `_rebuild_turn_messages`**, which keeps the turn's own notes (the §25
  continue context was dropped whenever the chat had history) and its
  message's parts (a photo for a vision model reached it as the printed list,
  base64 and all).
- **Prompt cache:** every turn note except the environment block is dynamic
  (`prompt_cache.is_dynamic_system`), so the cacheable prefix holds only what
  stays the same from turn to turn.
- **The base system prompt is sent on EVERY call, first** (2026-10-23,
  `graph_helpers._ensure_base_prompt`, marked `[KAZMA_BASE_PROMPT]`, in place
  of any copy a checkpoint holds), with the day's date in the operator's zone
  and the day's cultural context (`cultural_context_enrichment.
  daily_prompt_note`, a loop-stall helper; it changes once a day). The
  supervisor added it only when a call held no system message, so with any
  turn note -- every web and chat-app turn -- the model ran without Kazma's
  identity, product knowledge, dialect directive or date, and dated things
  from old notes. Never gate the base prompt on "no system message", and
  never build a date into the boot-time prompt. Gate:
  `tests/test_base_prompt_every_call.py` (the old rule as control).
- **A system message is judged by how it opens, never by a phrase in its
  text** (`turn_notes.opens_with`, 2026-11-15). The language lock's replace
  step removed every system message holding "LANGUAGE LOCK", and the base
  prompt holds those words ("A LANGUAGE LOCK system message for this turn is
  absolute"): from 2026-10-23 the first model call of every turn, on every
  surface, went without the base prompt (found measuring a turn's tokens;
  the 10-23 gate's base prompt named no note). Every filter that replaces,
  removes or looks for a note asks `opens_with`; the prompt cache's packer
  keeps the identity notes (`turn_notes.IDENTITY_OPENINGS`: base prompt,
  personality, "About me") in the cached prefix whatever they name. Gate:
  `tests/test_messages_judged_by_how_they_open.py` (a base prompt naming
  every note opening and cache marker, through the real graph in English
  and Arabic with a tool round; no `<phrase> in <message>.get("content")`
  in product code outside `DECLARED`; the old rule as the control).
- Gates: `tests/test_turn_notes.py`; `tests/e2e/test_turn_notes_route.py`
  (four turns through the real route, a workspace switch, IDE context; CI
  lifecycle job).

