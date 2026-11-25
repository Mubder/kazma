<!-- AGENTS.md, piece 57-weak-spot-roadmap. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 57. The weak-spot roadmap of 2026-11 (`docs/plans/FUNCTIONAL_AUDIT_2026-11_REMEDIATION.md`)

The audit's findings clustered into twelve themes; each theme is a standard
held by a class gate, beyond the single findings. Themes 3, 8 and 9 were met
by Stages 1-3; theme 10 (GitHub Actions billing) is the owner's, and theme 7
is the reasoning setting below plus the tool count per turn (open).

- **How much the model reasons before a chat answer is a setting** (theme 7,
  2026-11-03, the owner's choice: `low`). `agent.chat_reasoning`
  (`agent/chat_reasoning.py`; Settings -> System; `default` / `off` / `low` /
  `high` / `max`) reaches every model call of a chat turn -- the supervisor's,
  its failover, the empty-answer nudge, the respond synthesis -- as
  `chat_reasoning_kwargs(client)`: only where the client takes it
  (`LLMProvider.can_turn_off_reasoning`: DeepSeek's own API, `thinking`
  for off, `reasoning_effort` for the rest, `llm_provider.REASONING_EFFORTS`;
  Qwen's on Model Studio, `enable_thinking: false` for off alone -- no
  effort level is documented for Qwen, so one is sent as nothing; the
  memory plan's finding 12, 2026-11-22), so a failover to a provider with a
  `chat` of its own receives nothing. A model that refuses the field is
  asked again without it (the blocking path's reasoning-switch fallback,
  every field of `llm_provider._REASONING_FIELDS`; the stream's 4xx falls
  back to it). X drafting, research and the intent classifier's refinement
  (`intent/classify._refine_acts_llm`: a thinking model spent its whole 4 s
  reasoning) keep `reasoning="off"`. Settings' card says what the active
  provider takes (`chat_reasoning.levels_taken`: every level, Off only, or
  nothing; the API's `takes`). Gates: `tests/test_chat_reasoning.py`
  (each level on the wire, the refusal, the stream, a real turn through the
  supervisor graph with the call before the setting as the negative
  control, every `invoke_llm_chat` call in the chat turn's modules carrying
  the level), `tests/test_qwen_reasoning_off.py` (Qwen's switch on the wire,
  the classifier in English and Arabic, today's classifier as the control).
- **Qwen caches a background call's instructions explicitly** (2026-11-23,
  the memory plan's W4 pilot; its review, 2026-11-24). Model Studio caches a
  prefix on its own when it chooses (20 % of the input price);
  `llm_provider._with_qwen_cache_marker`, applied in `_chat_payload` (the
  blocking and the streaming body) for a Model Studio address
  (`_is_qwen_api`), marks only a call of the deep pass's shape -- one system
  and one user message as the caller made it, no tools -- on a measured
  model family (`_QWEN_CACHE_MODELS`), off with `KAZMA_PROMPT_CACHE=0`: its
  system message of at least `_QWEN_CACHE_MIN_CHARS` characters is sent as
  one text part with `cache_control` (cached five minutes, renewed on each hit; Model Studio's
  page gives a hit at typically 10 % of the input price and a first write
  at 125 %, qwen3.8-flash's own ratios by region). Measured through Kazma's
  own provider on qwen3.8-flash and qwen3.7-plus with the deep pass's
  prompt: 1,614 of about 1,850 prompt tokens served from the cache from the
  second call on. `cost_from_usage` counts the write's 125 %
  (`cache_creation_input_tokens`). Until the review every call of every Qwen
  model was marked, a chat's changing system message included. Gate:
  `tests/test_qwen_context_cache.py` (a host that only looks like Model
  Studio's as the control).

- **A result that says whether it worked is read where it is returned**
  (theme 1). `tests/test_failure_results_are_used.py` covers the vault, the
  chat store, the swarm task store and the updater too; a call that returns
  nothing worth reading is in `NOT_AN_ANSWER`. Fixed when it widened: a
  pipeline checkpoint's approve, reject and timeout wrote their task with
  the answer dropped, as did the step journal (`SwarmEngine._journal_step`)
  and a Research session's archive (now 409 `not_recorded`). Gate:
  `tests/test_refused_task_writes_are_said.py`.
- **Recovery is drilled with the real thing, in CI** (theme 2). The job
  `Recovery drills` runs, with a skip failing it: a universal backup sent to
  a real restic repository, restored by `restore.restore_files` into a
  scratch folder, and recalled (`tests/test_drill_restic_restore_recall.py`;
  the generation before the memory as the control); a throwaway Postgres
  stopped under a real settings store; the memory restore drill on
  Postgres; and the REAL guard reloading the REAL `serve.py` in a private
  install with a web turn streaming and an OCR job running
  (`tests/test_drill_graceful_reload.py`, `KAZMA_TEST_RELOAD_DRILL=1`; a hard
  kill as the control: the job's next attempt). The drills found four
  defects, each with its own gate:
  - a SQLite install's restore ended FAILED for want of a Postgres dump its
    manifest says it never had (`restore._postgres_not_used`);
  - the tab that sent a turn reads it through the journal-attached tail,
    which returned the moment the stop began, before the producer's
    `done(stopped_for_shutdown)`: FA-262's frame never reached the page.
    The tail passes on what is queued and sends the same frame itself
    (`_streaming._end_attach_for_shutdown`, one payload
    `_shutdown_done_payload`); a paused turn still ends quietly. Gate:
    `tests/test_attached_stream_told_of_restart.py` (the early return as the
    control);
  - a stopping document worker set its stage's event, but the sandbox polls
    the `documents.cancellation` signal, which only `run_cancellable` bound:
    the OCR process ran on to its last page after the job was given back.
    Every stage now runs under `cancellation.call_with_cancel`. Gate:
    `tests/test_document_stage_stop_ends_its_worker.py` (a real sandboxed
    process; the unbound call as the control);
  - the server did not end after its own shutdown: uvicorn re-raised the
    stop signal through the C runtime's default action, and interpreter exit
    joins executor threads. `serve.py` calls
    `process_exit.prepare_server_exit` (SIGBREAK/SIGTERM come back as
    KeyboardInterrupt; a stop listener, `shutdown.add_stop_listener`, arms a
    deadline at the stop's budget plus `EXIT_MARGIN_S`) and leaves through
    `process_exit.exit_after_shutdown`, which runs the `atexit` handlers
    first, bounded (the procedural recorder's drain, the vector store's
    release: a bare `os._exit` skips them). Gate:
    `tests/test_server_exit_after_stop.py` (real processes; each piece
    removed as its control, a bare `os._exit` among them). `serve.py` reads `KAZMA_PORT` now (documented,
    never read: the drill could not run beside a live server). The console
    log handler escapes what stdout cannot encode
    (`logging_config._console_never_raises`;
    `tests/test_console_log_never_raises.py`).
  A Knowledge crawl in flight is not drilled: its seed must be public, so a
  drill would need the internet; its reload is held by
  `tests/test_crawl_auto_resume.py`.
- **Every setting has a reader, the TUI's too** (theme 4).
  `tests/test_settings_keys_have_readers.py` reads the TUI's writers
  (`TUI_WRITERS`); its dead toggles are gone and its chat reads the system
  prompt the web reads (`agent.system_prompt` first).
- **Forgetting a turn takes back the lessons it taught** (theme 5).
  `self_improvement.forget_lessons_from(chat_keys, turn)`, called by
  `forget._forget_turn` (after its commit, before the journal entry is
  marked applied) and by `forget_chat`; a lesson another turn also taught
  stays. Gate: `tests/test_forget_takes_back_lessons.py` (the forget without
  the call as the control).
- **Arabic and English as equals** (theme 6). `tests/test_paired_languages.py`
  runs each path that decides by language once per language, through the
  real function, and compares the answers; every product module with Arabic
  in its code is paired there or declared with why (`DECLARED`). Its first
  run found: Arabic calendar words unparsed ("بكرة الساعة 9 الصبح" asked
  when; `relative_time._RE_CALENDAR_AR`, and "the day after tomorrow" in
  English); Gulf document requests unrecognised and a bare "مستند" never
  matching (`intent.heuristics`: the verbs, the formats' Arabic names, a
  named format beating the generic word); plan approval words that did not
  mirror (`plan_mode._APPROVE_RE`); and fixed replies in Arabic whatever the
  user wrote -- the supervisor's budget message and four chat-app replies
  -- now `language_lock.in_their_language`.
- **A dependency advisory reaches the owner the day it is published**
  (theme 11). The maintenance sweep "dependency advisories"
  (`security/advisory_watch.advisory_sweep`) asks OSV about the installed
  versions once a day (`audit_installed`; an hour's retry after a failure,
  which raises nothing) and raises `security.advisories_new` once per new
  advisory, naming the package, the fixed version and the update command;
  reviewed advisories stay quiet. What is still open is kept in the RUNTIME
  key `system.security.advisories` (`open_advisories`), and each stored one
  is held against the version installed NOW (`advisory_watch._still_open`):
  read as stored, the drill VM's five advisories stayed on the Dashboard after
  `kazma update` had installed the fixes, under "It closes when the installed
  version is past the fix" (2026-11-07; `tests/test_advisories_close_when_fixed.py`).
- **An unmet requirement stays in front of the owner** (theme 12). The boot
  check records its result (`install_requirements.REQUIREMENTS_KEY`,
  `last_requirements_check`; a check that finds none clears it), and the
  daily digest lists each unmet requirement and open advisory under "Needs
  attention" until it is gone. Gate for 11 and 12:
  `tests/test_dependency_notices_reach_the_owner.py`.

