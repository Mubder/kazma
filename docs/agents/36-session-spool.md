<!-- AGENTS.md, piece 36-session-spool. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 36. A chat save the database refuses is spooled, never held only in memory

Live 2026-09-24: a NUL in one tool result made Postgres refuse every save of
one chat ("A reply was produced but NOT saved to the transcript"). The reply
existed only in `SessionManager`'s cache, and the restart that picked up the
fix discarded a finished answer. A cache eviction or `_refresh_from_db` (the
Web UI calls it for every gateway session) would have done the same without
a restart. The NUL is fixed (`pg_helpers.json_dumps`, §35 gate); this section
is the class.

- **`SessionManager._write_durably` is the only way in.** It calls
  `_upsert_db`; if the primary store raises for ANY reason, the whole session
  goes to `kazma_ui/session_spool.py` (SQLite `chat_sessions_spool.db` next to
  the sessions DB, SQLite even under Postgres so it does not share the
  primary's failure mode) and the save counts as durable. It raises only
  when both refuse, and then the callers' "NOT saved" alert fires.
- **Every read overlays the spool** in `_session_from_row`, and spool-only
  sessions are served by `_load_one_from_db` / `list_all` /
  `get_by_thread_id`. The next accepted save of the session clears its entry;
  boot (`_drain_spool`) retries the rest. Delete / clear / evict discard it,
  or a deleted chat returns.
- **Gate:** `test_chat_saves_go_through_the_spool` — `_upsert_db` is called
  only by `_write_durably` and `_drain_spool`, and nothing outside
  `session_manager.py` writes `kazma_chat_sessions`. Behaviour:
  `tests/test_session_spool.py` (its genuine-refusal test runs in the CI
  Postgres job). The migration bundle carries the spool file.
- **Not covered:** a hard crash between the answer finishing and the save
  call. Streaming persists incrementally, so that window is the tail of one
  reply, and the answer is still in the checkpoint.

