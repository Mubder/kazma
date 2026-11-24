<!-- AGENTS.md, piece 11-self-improvement. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 11. Self-Improvement Soul Store + Prompt Fence (`kazma-core/kazma_core/skills/self_improvement.py`)

The self-improvement engine persists "Soul deltas" (LLM-generated system-prompt
refinements derived from untrusted conversation/tool output) and re-injects
them into every future system prompt. Two invariants must hold:

**A. Storage is ConfigStore-backed, NOT a free-standing JSON file.**
- The supervisor/main-agent Soul lives in `get_config_store()` under key
  `self_improvement.agent_evolution` (a dict `{"agents": {<id>: {soul, history}}}`).
- `_load_agent_evolution` (a read) and `_update_soul` (every change) go
  through ConfigStore — do NOT reintroduce a direct `path.write_text` write
  (the old `agent_evolution.json` was non-atomic and corruptible on
  crash/concurrency).
- Every change is one `atomic_update` with the read inside the updater
  (across processes); `_agent_evo_lock` orders this process's own changes
  (and the legacy migration). Both are required (S1-10 review, 2026-11-18:
  a read before the write lost another process's lesson).
- A one-time migration (`_migrate_legacy_evolution_if_present`) moves any
  pre-existing `agent_evolution.json` into ConfigStore and renames it
  `.migrated`. Leave this in place.
- Swarm *worker* deltas live on `WorkerRegistry` (`WorkerEntry.system_prompt`),
  not here.

**A2. A turn teaches only what memory would keep** (2026-11, FA-212).
`close_turn` is the one hand-off (`learn_from_finished_turn`, beside the
memory hand-off): never a failed turn, a turn or chat the forget ledger keeps
out, a refiner that could not answer (no template line), or a lesson longer
than `_MAX_DELTA_CHARS` (refused, never cut). The cap keeps whole lessons
(`_lessons` / `_render`). Gate: `tests/test_lessons_only_from_kept_turns.py`.

**A3. A turn teaches only on a signal, and a lesson is merged** (2026-11,
the memory framework audit's F13, decision 12). Every finished turn used to
cost one more model call, and the Soul's twelve slots let twelve routine
turns push out a real correction. `learn_from_finished_turn` now reads the
turn first, with no model call (`skills/lesson_signal.read_signal`): a
**correction** of the previous answer, a **recovered failure** (a tool that
failed, then a later call of the same turn that worked, read from the
state's `tool_results`) or the user's **feedback** ("from now on...", "too
long", "المرة الجاية...", "اختصر ردودك"), from the user's own words
(`memory.own_words`, quoted spans out), English and Arabic read alike (the
Arabic phrases folded as the text is); no signal, no refiner. The chat
refiner (`analyze_and_apply_chat_turn`, `_refiner_prompt`) is shown the
turn and the lessons the space's Soul holds, each in a data fence, and
answers one line: NOTHING, SAME n, REPLACE n or NEW (`_read_refiner_reply`;
anything else teaches nothing). A lesson is merged: it takes the place of
the one it replaces and of any that says the same (`_same_lesson`: folded
equal, or content words overlapping 80 %), and its history entry names
where those came from (`also_from`), so forgetting any of those turns takes
the merged lesson back (`forget_lessons_from`). The refiner searches no
memory any more. A swarm worker's refiner may answer NOTHING too. Gates:
`tests/test_lessons_on_a_signal.py` (twelve routine turns keep a
correction; the old rule, a lesson from every turn, loses it to the twelve
slots: the control), `tests/test_paired_languages.py` (the signal pairs).

**B. Every injected Soul delta MUST go through the prompt fence.**
- `kazma_core/safety/prompt_fence.py` provides `is_override_delta()` (rejects
  injection markers like "ignore prior instructions") and
  `format_untrusted_block()` (wraps content in a `<kazma:data untrusted>`
  fence telling the model the text is observation data, NOT instructions).
- Deltas are checked at creation time (`_analyze_success`/`_analyze_failure`)
  AND at apply time (`_auto_apply`/`apply_agent_mutation`) — defense-in-depth.
  Never inject a delta via the old `"Apply these refinements to your behaviour:"`
  framing; always use `format_untrusted_block(evo, source="self_improvement")`.
- **One place adds the main agent's Soul: the supervisor**, at the start of
  every turn on every surface and every install
  (`graph_helpers._ensure_space_lessons`: every other `self_improvement` note
  the call holds is dropped, one current fenced copy added), and it cuts a
  Soul baked into a base prompt (`self_improvement.without_baked_soul`).
  Nothing bakes one (`agent_runner` did, read once at start: stale after the
  first new lesson), and the transports (`sse_chat/`, gateway `graph.py`,
  `voice_turn.py`) add none: the model saw it twice (S1-10 review,
  2026-11-18). The swarm worker (`worker.py`) fences its own. Gate:
  `tests/test_lessons_per_space.py` (one copy through the real supervisor
  graph; the old base prompt with the cut taken out as the control).
- Kill-switch `KAZMA_SELF_IMPROVEMENT=0` is checked live (not just at init) on
  both the chat/supervisor path and the swarm worker path.

