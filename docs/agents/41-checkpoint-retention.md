<!-- AGENTS.md, piece 41-checkpoint-retention. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 41. Every chat's step history is bounded, on both backends (`kazma_core/checkpoint_retention.py`)

LangGraph writes a checkpoint per superstep (audit M-G1). Live on 2026-09-27
the Postgres checkpoint tables held 2.9 GB of a 3.0 GB database (one chat
3,969 checkpoints): retention skipped Postgres as "ops-owned". On SQLite it
named `checkpoint_writes` (the Postgres table), so a pruned checkpoint's
`writes` stayed, and its idle rule read `metadata.ts`, which LangGraph never
writes.

- **One policy:** every chat (thread + namespace) keeps its newest 200
  checkpoints; one idle for `checkpoints.retention_days` (Settings -> System,
  default 30) keeps 10; `0` keeps everything. `KAZMA_CHECKPOINT_RETENTION_DAYS`
  wins when set (it was an on/off switch whose value meant nothing). A
  checkpoint's age comes from its uuid6 id. A chat written in the last ten
  minutes is left for the next pass.
- **Postgres blobs are shared** by every checkpoint that did not change the
  channel. One goes only when no kept checkpoint names its version AND a kept
  checkpoint names a NEWER version of the channel: the saver writes a new
  checkpoint's blobs before the checkpoint (autocommit), and that blob is
  newer than every named one. One transaction per chat; a chat that fails is
  rolled back and named in a WARNING, the others still pruned.
- **Runs on the 15-minute maintenance cadence** ("checkpoint retention",
  §15B). It was a daily loop that logged failures at DEBUG.
- **No graph state may use a LangGraph `DeltaChannel`** until the prune is
  made delta-aware: a delta value is rebuilt by walking back to a snapshot
  checkpoint, and LangGraph warns a keep-newest prune empties it silently.
- Gate: `tests/test_checkpoint_retention.py` -- real graphs on the product's
  savers (state and every kept checkpoint unchanged, the next turn works),
  orphaned writes (old shape as the negative control), the setting and the
  API, and on a real Postgres the in-flight blob (the rule removed as the
  negative control would delete it).

