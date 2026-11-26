<!-- AGENTS.md, piece 15k-mirrors. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**K. The Postgres memory mirror stays whole (2026-09-27, `memory/state_backend.py`).**
Found on live that day: 116 turns still "working" in the mirror (the
working-turn promotion never told it), 17 facts that never reached it, and 53
rows only the mirror held -- test data written before the database shield and
memories removed here since -- which recall's top-up could bring back into
an answer ("User prefers dark mode").
- **Memory's copies in Postgres say whose rows they are** (S4-01 step 0b,
  2026-11-26, §70 (2)). A mirror write, a mirror delete and a vector write or
  delete run in their row's own space where Kazma does the work itself (a
  sweep, the forget convergence; `row_work.row_statements`), and in the bound
  space (or the declared one) where a request or a turn does; a row of
  another space there keeps that door and is said as `cross`, a finding of
  its own in step 1's report (0b review M1, L3). The mirror's pushes are
  grouped by space, one transaction each. **A delete says whether the row is
  gone** (`delete_belief(id, tenant_id=)`, pgvector's `delete`): it runs in
  the row's space; once Spaces is enforced it names the tenant
  (`COALESCE(NULLIF(tenant_id, ''), 'default')`, decision M6), and with
  Spaces off it is by id, as before (review L1). Gone is deleted now, or no
  row of that id left. Not gone -- False, and a WARNING once per id, DEBUG
  after -- is a row of that id kept under another space, or a door of
  another space (`row_work.door_hides`: once armed its "nothing there" says
  nothing). Then pgvector's forget journal keeps the vector queued; the
  mirror's own callers (`macro_sleep`, graph-clear) do not retry, and the
  outbox the `beliefs_mirror_ad` trigger fills, which deletes by id in the
  sync's install door, finishes it (H5: "deleted" was answered whatever the
  statement did, pgvector dropped its tenant, and a forgotten fact could stay
  for good). Callers that know the fact's space pass it
  (`unmirror_belief_to_state(id, tenant)`). The schema is made on a
  connection of its own, as the install's (`_ensure()`, `_ensure_table()`):
  on the caller's, its commits put the caller's statement in a second
  transaction. The probes, the sync, the comparison, the drift summary, the
  forget convergence and the passes that read every space's chats
  (`run_rehydrate_pass`, `run_turn_reconcile_pass`, `run_text_fill_pass`: the
  sweeps and the memory restore, a request, call them) are the install's
  (`install_statements`). `fetch_*` take `strict` like `search_*`, and a space
  refusal raises from all four and from `search_state_*` whatever *strict*
  says (never "nothing found"). Gate: `tests/test_memory_rows_in_their_space.py`
  (each fails on the code before; a real Postgres keeps another space's fact).
- **Every tier or text change reaches the mirror.** `remirror_episodes` is the
  one call after a local change (macro_sleep's moves, the working-turn
  promotion, forget, retire). Every `UPDATE episodes SET tier/text` in product
  code is declared with its path (`tests/test_memory_mirror_sync.py`, like
  the delete gate). `remirror_*_by_id` work on any connection (they used to
  need `row_factory = Row` and raised on the promotion's tuples).
- **The mirror sync** (maintenance entry "memory mirror sync",
  `sync_state_mirror`): compares the whole mirror with the local store -- id,
  tier and an md5 of the texts; for a fact, current or not and the value --
  and pushes what differs, newest first, at most 5,000 rows a pass
  (`_SYNC_LIMIT`; 500 while every push had its own connection), episodes in
  batches. A comparison still pushing with more behind is not stamped, so the
  next pass compares again (`catching_up`). A row only
  the mirror holds is counted (memory health `mirror`) and left alone, never
  deleted or copied back. Skipped with no mirror only: it runs when Postgres
  is the primary store too (2026-10-13, Memory audit M06) -- recall reads the
  mirror there, and a forget whose mirror write failed stayed whole in it for
  good. Digests match Postgres's own (real-Postgres test, Arabic, emoji,
  NULLs); NUL bytes are dropped on every mirror write.
- **A pass pushes what changed; the full comparison runs daily** (2026-10-14,
  Memory audit M31). Triggers (`schema_v2._ensure_mirror_outbox`) queue each
  episode or fact written, or changed in a column the mirror holds, in
  `memory_mirror_outbox` -- in the change's own transaction -- and a deleted
  fact (its mirror copy goes). The pass drains the queue (each row once, as
  it is now; a failed push retried 5 times, then left to the comparison; a
  row another region owns dequeued and counted, `outbox_conflicts`). The full
  comparison above runs once in `memory.v2.mirror_verify_hours` (24, 1-168;
  stamp `memory.v2.mirror_full_verify`, its findings kept in
  `memory.v2.mirror_verify_result`, both runtime). No mirror configured: the
  queue is emptied; configured but down: kept. A new mirrored column needs a
  trigger column -- `test_every_column_the_mirror_holds_queues_its_row` reads
  the upserts' SQL. Pushes go through a pool (`PostgresStateBackend._connect`,
  psycopg_pool, §44's `check=`) and in batches (`mirror_episode_rows`, 500 a
  batch, one region read per batch; `remirror_episodes` uses it and takes
  what it pushed off the queue, entries queued before its read only). Every
  row on its own connection, the first macro-sleep pass at 10x the live size
  took 336 s; batched, 7 s. **A mirror that went back is compared at once**
  (`_mirror_went_back`, 2026-10-14): a pass asks the mirror
  (`present_ids`) for the newest local rows it was already sent, and one
  missing (a `pg_restore` of an older dump) forces the full comparison, at
  most once an hour. The outbox had been drained for those rows, so recall
  on the primary path missed every memory since the dump until the daily
  comparison. Gates: `tests/test_memory_restore_drill_pg.py` (real Postgres,
  `pg_dump`/`pg_restore`; the incremental pass alone as the negative
  control), `tests/test_memory_mirror_incremental.py`. Measured limits (recall is the ceiling: 8 s a
  question at 170,000 memories) are in the guide, from the opt-in
  `tests/test_memory_scale_bench.py`. Gates:
  `tests/test_memory_mirror_incremental.py` (each with its negative control:
  a full pass reads both digests, a backend without batches pushes per row,
  the pool's connection count against a connection each, real Postgres).
- **Postgres-primary recall reads only what this install still remembers**
  (2026-10-13, M06). The mirror's keyword search and id fetch read only
  `RECALLABLE_TIERS`; the keyword channel orders by how many of the
  question's words a row holds (`words_held`), then by time -- it took the
  newest rows holding ANY word, so an older memory only its words would find
  fell out of the slice (the slice gate in `test_memory_nothing_lost.py`
  covers `state_backend.py`). Recall drops every row whose local copy is a
  tombstone or whose turn the forget ledger names (`recall._locally_forgotten`),
  whoever wrote it. Measured on the retrieval benchmark with the mirror as
  primary and the local vector index on the same vectors (the
  Postgres-primary keyword path, not pgvector): hit rate, MRR and precision
  equal to local (2026-11-21, dataset v8; `PRIMARY_DELTA`).
  The pgvector path is the other measurement (§15G, "pgvector in CI"). Gate:
  `tests/test_memory_pg_primary.py` (real Postgres: a forget with the mirror
  write failing, left out of recall, repaired by one pass; each with its
  negative control). The mirror tables are in `KAZMA_PG_TABLES` (§21A).
- **The primary path takes the local path's question-shape steps**
  (2026-11-21; CI's Postgres job was red from part F on, MRR 0.6889 against
  local 0.7133). After ranking, `_primary_episodes_as_local` and
  `_primary_beliefs_as_local` apply what `_recall_episodes` and
  `_recall_beliefs` do: the chat's own turns left out, the question's time
  (a period's turns and facts, or only them), the same text once, a list's
  compact lines and siblings, follow-ups joined to their turn. Candidates:
  a period's matches and turns from the mirror (`_primary_period_rows`;
  `search_state_episodes(between=)`, `state_episodes_between`) and a fact's
  earlier values from this install's database (`_primary_belief_rows`: the
  mirror keeps no chain of replaced values; a shared space's choice of whose
  facts too). A new step after ranking on the local path is added to both.
  Gates: the Postgres benchmark (without the steps as the negative control:
  CI's numbers) and, with no Postgres, `tests/test_memory_primary_parity.py`
  (a stand-in mirror over the same rows: every time, list, follow-up and
  roll-up question injects what local recall injects).
- **The top-up reads only other installs' rows.** Mirror writes carry the
  writer's install id (`metadata.install`, `<data dir>/install_id`);
  `written_elsewhere` admits a remote-only row only when another install
  wrote it. A row this install wrote and no longer holds was removed here;
  untagged rows (mirrored before the tag) count as this install's.
- **Region conflicts are counted, never pushed** (multi-region only). Under
  `origin_wins` a row another region wrote keeps its value quietly
  (`kept_by_origin`); under `fail_closed` the same refusal is reported
  (`region_conflicts`, a WARNING each pass, ops alert `memory.region_conflict`).
  The sync reads the writer's region (`row_regions`, a pattern read -- one
  malformed metadata row must not fail the query) and never spends the push
  budget on a refused row. The two policies behaved the same until
  2026-09-27. `tests/test_memory_mirror_sync.py`.

**K2. The Neo4j copy of the graph converges (2026-11-13, `memory/graph_sync.py`).**
`kazma setup install` sets Neo4j up by default since that day, and the copy
was kept by the dual-write alone (best effort, after the commit): a fact
written while Neo4j was down never reached it, an install with memories
before Neo4j was added had an empty one (the Settings button pushed the
first 1,000 current facts and removed nothing), a fact invalidated or
forgotten while it was down kept its edge -- and its value node -- for good,
and Neo4j down at a process's first fact made it use SQLite until a restart.
- **The sweep** "memory graph sync" (after the mirror sync,
  `run_graph_sync`), as the mirror's: triggers queue every fact written,
  changed in a column its edge depends on (`schema_v2.GRAPH_EDGE_COLUMNS`) or
  deleted, in `memory_graph_outbox` (`schema_v2._ensure_graph_outbox`), and
  each pass brings those facts' edges in step; a full comparison (every
  current fact against every edge of each tenant, Neo4j read first) runs
  daily, at once for a copy never compared or another Neo4j, one that went
  back (the newest facts it was sent are missing: recreated, restored),
  after a memory restore (`graph_sync.mark_full_due`, from
  `restore_memory_backup`) and while one is catching up. At most 2,000 writes
  and 120 s a pass, removals first, then the newest facts; a fact is read
  again just before its edge is written (a forget in between is not pushed
  back). Findings are the runtime setting `memory.v2.graph_sync`.
- **Only this install's edges change.** Every write carries the fact id and
  the install's id (the edge's install tag, `graph_backend.this_install`); an
  edge with no fact id, or another install's tag, is never written or
  removed (the dev checkout and live share one Neo4j at 127.0.0.1:7687), and
  an untagged edge of a fact this install never held is counted and left.
  The dual-write's upsert takes the same rule, and graph-clear deletes only
  this install's. Entity nodes left with no edge go. **Each install's facts
  have edges of their own** (2026-11-13): the write matches by install tag
  (`MERGE (a)-[r:TYPE {install: $inst}]->(b)`, `_UPSERT_EDGE`; a writer with
  no install id `_UPSERT_EDGE_UNTAGGED`), and another install's edge, or one
  with no fact id, at a key this install wants is counted (`held_elsewhere`)
  beside this install's own. Matched by type alone, the second install's
  write landed on the first's edge, so its fact left the graph when the
  first install forgot its own (until a daily comparison). A forget by id
  reads the subject's edges (`_DELETE_BY_ID_AT_KEY`, an index seek; by name
  until keyed), this install's only, never every edge of the database; the
  subject-object delete keeps to this install's too (`_THIS_INSTALLS_EDGE`).
  Gates: `test_real_server_two_installs_share_one_copy`,
  `test_real_server_a_forget_by_id_seeks_its_subject_and_keeps_to_this_install`
  and the type-only write as the control (`tests/test_memory_graph_sync.py`).
- **An outage is waited out:** one WARNING when Neo4j stops answering, one
  INFO when it is back, never a failed sweep; the queue keeps every change
  (100,000 at most, then a full comparison instead); no Neo4j chosen, it is
  emptied. No Neo4j call inside a memory write transaction (§15D2,
  `graph_sync._no_write_open`). The dual-write's SQLite stand-in for an
  unreachable Neo4j expires after a minute (`graph_backend._FALLBACK_RETRY_S`).
- **One implementation:** Settings -> Memory's "Sync beliefs -> Neo4j" runs
  a pass with the full comparison (`graph_sync.sync_now`; the old one-shot
  push is gone). Memory health shows the copy
  (`v2_health` graph entry's `copy`: in sync, behind N, unreachable, not
  configured), from the last pass, never asking Neo4j. The hygiene counts
  had replaced that whole entry since 2026-10-03, and the Memory page said
  "graph: sqlite" over a Neo4j store; they sit beside it now.
- **Nodes are found by key** (2026-11-13). With no index every write's
  `MERGE (:Entity {name, tenant_id})` scanned every Entity node: 500 rows over
  40,000 nodes took 8 s (neo4j 5.26). An index on `name` cannot hold a fact's
  value (Neo4j refuses an indexed value past ~8 KB; a value is up to 4,000
  characters, 16 KB in UTF-8) and a text index is not used by `MERGE`. So
  every node carries `key`, the SHA-256 of its name (`graph_backend.entity_key`,
  sent per row by `graph_backend.edge_row`), under the uniqueness constraint
  `kazma_entity_key` on `(tenant_id, key)` (Community has composite
  uniqueness; its index seeks every lookup, and `MERGE` locks its entry, so
  two writers of a new name make one node). Each pass keys the copy first
  (`graph_sync._key_nodes`): makes the constraint (read first with SHOW: the
  driver logs the notice `IF NOT EXISTS` answers, every pass; keys two nodes
  of a tenant share, written while there was none, are taken off all but the
  one with the most edges), keys every node with a tenant, a string name and
  no key -- never a node with no tenant -- streamed in one read
  (`graph_sync.KEY_STAMPS_PER_PASS` at most), each guarded by its name and
  "no key yet"; a keyless node whose keyed twin exists (an older writer made
  it after the switch) has this install's edges moved to the twin, written
  by key first and then removed, and goes once it has no edge; another
  install's edges stay, and the node is counted `held_elsewhere`. A pass
  that keys every node it can, the constraint there, records the copy
  `keyed` (`graph_backend.KEYS_KEY`, runtime, per Neo4j `target`); writes,
  `edges_at_keys`, `neighbors` and `delete_triple`'s subject-value branch then
  find nodes by key (the backend reads the record at most every 30 s; the
  sync's own process at once). Until then, and after any pass that cannot
  finish (time, a refusal, an outage), it is `transitional`: nodes are found
  by name, which is right in every state -- a new node gets its key when it
  is made, only (setting a found node's key would be refused when its keyed
  twin exists). Of two edges at one key the one between keyed nodes stands
  (`Held.keyed`). Memory health says whether the copy is keyed and how many
  nodes still have no key. Measured on neo4j 5.26 with 40,000 Entity nodes
  (three runs each): 500 rows in one statement 8.4-8.7 s -> 0.07-0.19 s, 500
  dual-writes 7.7-9.0 s -> 0.8-0.9 s; keying the 40,000 took 5.5-5.9 s.
- Gate: `tests/test_memory_graph_sync.py` (an in-memory Neo4j answering the
  backend's statements, the product's writers; each case shows the copy as
  the old code left it first; a real-server case with
  `KAZMA_TEST_NEO4J_URL`, never this machine's 7687 unless
  `KAZMA_TEST_NEO4J_THROWAWAY=1`). For keys: one node per name through
  every phase with writes between, an old writer after the switch converging
  in one pass, a 4,000-character value of 4-byte characters, the plan an
  index seek (EXPLAIN), foreign edges and nodes with no tenant unchanged, two
  concurrent writers of a new name; negative controls: switching before
  every node is keyed duplicates, the keyed write over a plain index (and
  the write before keys) duplicates under concurrency, an index on `name`
  refuses the value. One real-server test drops and remakes
  `kazma_entity_key`.

