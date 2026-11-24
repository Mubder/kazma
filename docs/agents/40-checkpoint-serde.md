<!-- AGENTS.md, piece 40-checkpoint-serde. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 40. One checkpoint serializer, strict (`kazma_core/checkpoint_serde.py`)

LangGraph's `JsonPlusSerializer` rebuilds typed values from a checkpoint by
calling the class the checkpoint names, and its default is PERMISSIVE (any
class, with a warning). Given an allowlist it is strict: LangGraph's safe
types plus `KAZMA_MSGPACK_TYPES`, anything else back as raw data.

- **Every saver takes `serde=kazma_checkpoint_serde()`** — the gateway's
  CheckpointManager and per-tenant savers, the Postgres saver, KazmaAgent's
  own savers, the shared SQLite saver (`checkpoints_shared` no longer takes a
  serializer: it used to keep whichever its FIRST caller passed, so the
  process's posture depended on boot order), and the CLI's MemorySaver.
- **Every Enum the graph-state modules define is on the list**
  (`kazma_core.agent.state`, `kazma_core.agent.intent.types`). `TaskStatus`
  and `RouteKind` were not: on SQLite they came back as plain strings with
  a warning (Postgres stores `str` inline, so the live install never showed
  it). A new state enum goes on the list.
- Gate: `tests/test_checkpoint_serde.py` — every saver construction in the
  product source, the one construction site, every state enum round-tripped
  as itself, and a class off the list never constructed (negative control:
  LangGraph's permissive mode builds it).

