<!-- AGENTS.md, piece 02-platform-isolation. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 2. Platform Isolation (`kazma-gateway/kazma_gateway/agent_handler/`)
- The LangGraph state NEVER contains `chat_id`, `user_id`, or `message_id`
  as keys of its own.
- These live in `SessionStore` and are restored via `_build_target_id()` on reply
- **One declared exception, the `_gateway` routing block** (2026-11, FA-151):
  `agent_handler/store.py` and `send_message.web_gateway_block` write
  `thread_id`, `display_name`, `platform` and `delivery_target` -- the
  platform-prefixed address a reminder is booked to (§16B) and an expired
  approval is answered at (§30E2) -- and nothing else. A fork's block keeps
  none of the three platform ids even from an older checkpoint
  (`time_travel._SNAPSHOT_PLATFORM_IDS`). Gate:
  `tests/test_gateway_routing_block.py` (a planted `chat_id` key as the
  negative control).
- Breaking this leaks platform IDs into the graph and corrupts sessions
- `SQLiteSessionStore._ensure_db` publishes its connection only after pragmas,
  table creation and migrations finish. Publishing before those awaits lets
  another request's fast path query a missing table. Failed/cancelled setup
  closes the unpublished connection; `close` shares the initialization lock.
  Gate: `TestConcurrentSerialization.test_concurrent_first_use_waits_for_schema`
  (old initializer fails deterministically), plus setup failure/close cases.

