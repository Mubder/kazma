---
id: memory-and-rag
title: Memory & RAG
sidebar_label: Memory & RAG
description: Kazma V2 cognitive memory — beliefs, episodes, KB inject, /memory admin, ego-graph anchoring, optional Neo4j/Postgres adapters
---

> **Live SoT (2026-09).** V2 is the **only** chat memory stack. Invariants: [`AGENTS.md`](https://github.com/Mubder/kazma/blob/main/AGENTS.md) §15, §20 (source-trust), §29 (context integrity / shift).
>
> - **Personal memory** — bi-temporal beliefs, 4-tier episodes, PPR, FTS5 + dense (sqlite-vec on one node; **pgvector** when Postgres is on), durable queue.
> - **Knowledge Library** — separate store; **product merge** via labeled inject + federated search (not one table).
> - **Scale adapters** — pgvector auto-selects from the Postgres DSN; Qdrant if you set it; Neo4j dual-write; Postgres state mirror / primary.
> - **V1 4-layer RRF** (Chroma / L1–L4 concepts) was **removed**. Do not resurrect it in docs or UI copy. Earlier notes referencing `UnifiedMemoryAdapter` / `VectorMemory` are obsolete.

Operator checklist: [Memory best path](./memory-best-path.md).  
Scale backlog: [`docs/plans/MEMORY_REMAINING.md`](https://github.com/Mubder/kazma/blob/main/docs/plans/MEMORY_REMAINING.md).  
System audit (2026-08-24, findings closed): [`AUDIT_MEMORY_SYSTEM_2026-08-24.md`](https://github.com/Mubder/kazma/blob/main/docs/audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md).

---

## Architecture (chat path)

```text
User turn
  → recall()  (beliefs + episodes FTS/dense + PPR + session bias)
  → optional federated KB hits  (if merge_knowledge_into_chat)
  → format_untrusted_block  (<kazma:data untrusted>)
  → LLM
  → post-turn: mirror episode, heuristic beliefs, enqueue micro_consolidation
```

### Two SQLite DBs (load-bearing split)

| Database | Role |
|----------|------|
| `memory_state.db` | Hot: beliefs, episodes, entities, `entity_merges` (+ archive), procedural DAGs |
| `memory_ops.db` | Cold: task queue, audit log |

Do **not** merge these — background consolidation must not WAL-contend with chat reads.

### Stores stay separate (product excellence)

| Store | Role | Default |
|-------|------|---------|
| V2 cognitive | Who I am / what we said | SQLite SoT on one node |
| Knowledge Library | Docs + citations | Separate KB indexes |
| Neo4j | Optional dual-write of belief triples | Off unless configured |
| Postgres state | Dual-mirror or `state.role=primary` | Off unless configured |
| Dense vectors | sqlite-vec (one node) / **pgvector** (Postgres DSN) / Qdrant | Auto pgvector when you leave one node |

**Physical one-table merge of KB + beliefs is wontfix** ([#79](https://github.com/Mubder/kazma/issues/79)). Chat unifies via inject; stores do not.

---

## Settings (where everything lives)

**Settings → Memory** (`/settings?tab=memory`) — isolation, Knowledge + chat toggles, backends (vector / state / graph), Neo4j Test/Sync, **and embedder** (merged into this tab).

| Control | Effect |
|---------|--------|
| Tenant mode | `shared` / `per_platform` / `per_user` |
| Inject Knowledge into chat | `merge_knowledge_into_chat` (default on) |
| Promote KB hits to episodes | `promote_kb_to_episodes` (tagged soft-copy) |
| Graph store | `sqlite` (default) or `neo4j` dual-write |
| Vector / embedder | sqlite-vec on one node; **pgvector** when `KAZMA_DATABASE_URL` / state URL is set (`KAZMA_PGVECTOR=0` to keep local) |

Legacy deep-links: `?tab=embedder` → Memory (scroll to embedder); `?tab=connectors` → LLM Providers → Platform Connectors.

> **Embedder download guard (2026-08-19):** a deliberately configured
> `local` embedder may download its model on first use, but FALLBACK
> embedders (unknown provider / broken remote config) never do — they
> check the local HuggingFace cache and degrade to no embeddings with an
> actionable warning instead of stalling the process on a live ~2GB
> `bge-m3` download. Force-allow with `KAZMA_EMBED_ALLOW_DOWNLOAD=1`.

---

## Recall & post-turn

### `recall()`

Recall injects what the question is about, and nothing when nothing is
(since 2026-09-26). It used to fuse its channels by rank and always return
its top five: every turn got five facts and five conversation memories,
related or not.

1. **Candidates.** Conversation memories: the question's content-word
   matches (FTS5) and its 40 nearest memories by meaning, over **every tier,
   archived included**. Facts: content-word matches, the nearest current
   facts by meaning, the facts extracted from the turns just found, and the
   belief graph's walk.
2. **Evidence.** Each candidate is scored on how far its meaning similarity
   rises above what unrelated memories reach for this same question, plus
   the share of the question's content words it holds. Below a floor
   nothing is injected; far behind the best, nothing either; a thin match
   is shown to the model as "(possibly related)". Conversation memories and
   facts have their own thresholds, measured on the retrieval benchmark.
   When no candidate has a vector to judge by (no embedder), a memory must
   hold at least half of the question's content words.
3. **Tie-breakers.** Among close candidates the newer one comes first (and,
   for a fact, the one that matters more); an archived memory comes just
   behind an equally matching active one; a memory from the current session
   comes first; the same text is shown once.
4. **Content words** (`memory/query_terms.py`): English and Gulf/MSA
   stopwords dropped, Arabic folded with the article and one-letter prefixes
   handled, light plurals, whole words only ("out" is not in "about").
5. Fence: `format_untrusted_block(..., source="memory_v2_recall")`.

When memory has nothing for a question, the **past-chats fallback** searches
earlier chats; a chat must hold more than half of the question's content
words.

**Quality is measured.** `python scripts/memory_bench.py run` scores recall
on a persona's life -- 1,109 chat turns and 72 facts inside unrelated chat,
76 questions (single facts, paraphrases, things only the assistant said,
updates, codes, Arabic, questions with no answer). CI replays the real
model's recorded vectors and holds every score to a ratchet
(`tests/test_memory_benchmark.py`); `lock` raises it, and it cannot be
lowered.

**Meaning search is exact over every memory** (`vector_engine.py`, since
2026-09-26): every episode and every current fact whose vector comes from
the current model is scored in SQL (`vec_distance_cosine`; about 1 ms per
1,000 rows), with a NumPy fallback. It used to compare an unordered slice of
episodes (the newest 60 of 300 were never searched) and the 400 "most
important" facts, and fact meaning search ran only when keywords came up
short.

**Dense engine:** sqlite-vec while you stay on one SQLite node. When the
process already has a Postgres DSN, recall uses **pgvector** (hybrid
dual-write, or remote-first if `KAZMA_MEMORY_STATE_ROLE=primary`) — if that
Postgres ships the extension. `postgres:16-alpine` does not; Kazma then
stays on sqlite-vec, says so once at boot, and Settings → Memory shows why
([Postgres & SaaS](../ops/postgres-and-saas) has the states and the fix).
Postgres-primary ranks the mirror's keyword matches and the index's
nearest memories on the same evidence (a keyword match's meaning comes from
the index) — not keyword-only. Explicit Qdrant in Settings is never overridden.
Kill-switch: `KAZMA_PGVECTOR=0`.

### Post-turn

Every finished turn is handed to memory once, by the closer every transport
runs (`kazma_ui.turn_runtime.close_turn` → `consolidator.remember_turn`) —
web, Telegram, Slack and Discord alike. Until 2026-09-26 only the gateway
handler did it, so no web chat turn was remembered from 2026-08-08; a
15-minute **turn reconcile** now writes an episode for every chat-store
turn that has none (with the turn's own time; episodes only, since replaying
old statements as facts could overwrite newer ones), which recovered those
turns and catches any future gap.

- Mirror working/recall episode (the question and ITS answer; the episode's
  turn number is the conversation's turn index)  
- Heuristic (+ optional LLM queue) belief extraction → `mutate_belief`. A `user_explicit` functional belief **cannot** be superseded by `llm_inferred` / `system_tool` (commitment source-trust gate in `_mutate_functional`; independent of `authorize_effect`).  
- Hygiene rejects stack/version subjects (e.g. `kazma_v2_4_0` mistaken for product version)  
- Dual-write: optional Postgres state mirror + Neo4j edge upsert  
- **Ego-graph anchor** — every non-hub subject that does not already reach `user` gets `user → related_to → <subject>` at write time (payload leaves **and** floating entity clusters). Payload objects (`fully_clean`, paths) are **not** minted as concept entities (that mint used to skip the hub edge). Idempotent backfill on the 6h sweep.  
- Invalidate / supersede / graph-clear **tombstone the Postgres mirror** and best-effort **delete Neo4j edges** (mass clear uses `clear_tenant_edges`).  

### Schedulers (`worker_bootstrap`)

**All loops start from `start_memory_worker()`** — add a new one there or it never runs (the gap that once left backups inert). Current boot list is **eight** loops.

| Cadence | Work |
|---------|------|
| ~6h | `macro_sleep` (rule-based tier moves + archival, below) + ego-anchor backfill + FTS drift COUNT (`*_docsize` vs base; rebuild on mismatch) |
| **~6h** (not 24h) | `native_backup` + JSONL/GraphML/episodes/merges/audit export + `native_pg_backup` + mirror-drift warning. Universal backup **checks** PG dump freshness; it does not dump twice. |
| ~24h | `global_reconsolidation` (dedupe + re-embed; **partitioned** for large corpora; recomputes entity counts) |
| ~15m | commitment GC (TTL + soul-pending), HITL-gate TTL sweep, **memory vector repair** (missing, wrong-size or old-model vectors re-encoded in place, ~20 s a pass, no model load when there is nothing to do), **memory recovery** (below) and **turn reconcile** (chat-store turns without an episode) — one sweep runner, no extra loop |
| (also from this boot) | session purge, daily digest, weekly firing ledger, restore drill |

Huge corpus: subject-hash partitions + chained queue tasks (see `global_reconsolidation.py`).

### Episode lifecycle and archival (`macro_sleep.py`)

Every move is a rule on TTLs, importance and use — there is no decay score
(`compute_retention` was removed on 2026-09-23: nothing read it, and its decay
rates were per-second).

| Move | Rule |
|------|------|
| working → episodic | after `working_ttl_hours` (24) |
| episodic → recall | importance ≥ `promote_to_recall_min_importance` (3) **and** access ≥ `promote_to_recall_min_access` (2) |
| recall → episodic | not recalled for `recall_demote_idle_days` (30) |
| → archived | stale on **both** clocks — created more than the TTL ago **and** not recalled within it — and below the promote floor. A memory still being recalled is never archived. |

**Archiving is cold storage, not deletion** (since 2026-09-26). The tier
changes and an empty summary gets a one-line stub (start of the question —
answer); the question, the answer and the vector all stay, and a static gate
fails the build if any statement sets episode text to NULL. Recall still
reaches an archived memory (`memory.v2.archived_recall_weight`, default
0.98 — just behind an active memory that matches as well), and a
memory recalled again returns to the episodic tier at the next sweep. Tier
moves reach the optional Postgres state mirror; a remote vector index keeps
the archived vector, re-tagged.

**Recovering what the old rule erased.** Until 2026-09-26 archiving *did*
null the text (the live install had lost 76 memories). The 15-minute
recovery pass (`memory/rehydrate.py`) restores them from whatever still holds
the text: an earlier backup of `memory_state.db`, the chat store and its save
spool, LangGraph checkpoint history, the `noted` belief or `memory_store`
tool call a saved note left, the knowledge library. A text is restored only
when it is provably the one erased: a backup must be a copy of the same row
whose text reproduces the stub; any other source must reproduce the
episode's id (a SHA-256 of session, turn and text) *and* the stub. Sources
must agree, existing text is never overwritten, and a source that cannot be
read leaves the row for the next pass. Memory health on the Dashboard shows
what is restored, pending, or kept only as its stub ("Every memory
findable").

**Why this matters:** every ordinary chat turn is written at importance 1, so it
can never be promoted. Until 2026-09-23 archival tested creation age only and
the stub fallback used `COALESCE` on an empty string, so every chat memory
became an empty shell on day 30 however often it was recalled. The live
install had 341 such shells; all were restored from backups.

Export writes `kazma_beliefs_latest.jsonl`, GraphML, plus episodes, `beliefs_archive`, `entity_merges` (+ archive), and `memory_audit_log` (per-tenant filenames when not `default`). Native `.db` backups remain the restore SoT.

Postgres mirror drift (dead facts still live in `kazma_beliefs`): the backup handler logs a warning. Reconcile with:

```bash
python scripts/reconcile_memory_mirror.py --dry-run
python scripts/reconcile_memory_mirror.py
```

Do **not** set `KAZMA_MEMORY_STATE_ROLE=primary` until that check is clean — cutover would otherwise resurrect tombstoned facts.

---

## Knowledge Library + chat (product merge)

- **Inject** — supervisor can add labeled KB observation blocks next to V2 memory.  
- **Federated search** — `POST /api/memory/v2/federated-search` and Dashboard “Search all knowledge” (`MEM` / `KB` chips).  
- **Promote** — optional soft-copy of top KB hits into episodes (not belief SPO without provenance).

Deep dive: [Knowledge Library](./knowledge-library.md).

---

## Neo4j (optional dual-write)

- **SoT for beliefs remains SQLite** (bi-temporal, scrub, Dashboard filters).  
- **Dashboard topology paints from SQLite** when Neo4j is configured (types + bi-temporal).  
- Neo4j receives dual-writes on mutate; **Sync beliefs → Neo4j** backfills existing rows.  
- Soft-invalidate / supersede / **graph-clear** best-effort **deletes** Neo4j edges (tenant-scoped). You should not need a manual Sync after Clear graph.

### Operator setup

1. Run Neo4j (e.g. `docker compose -f deploy/docker-compose.neo4j.yml up -d`).  
2. Settings → Memory → Graph store **Neo4j**, URL `bolt://localhost:7687`, password, **Save**.  
3. **Test Neo4j** (masked `***` passwords do not overwrite the vault secret).  
4. **Sync beliefs → Neo4j**.  

### Env install default (fail-open)

```bash
export KAZMA_NEO4J_DEFAULT=1
export KAZMA_NEO4J_PASSWORD=...
# optional: KAZMA_NEO4J_URL=bolt://localhost:7687
```

If unset, default graph provider stays **sqlite**. Server down → topology falls back to SQLite.

---

## Dashboard

| Panel | Role |
|-------|------|
| Memory & Governance | Component health (V2 stack, Neo4j, KB inject, packages) |
| V2 Cognitive Engine | KPIs, probe/federated, queue, topology canvas |
| Topology | SQLite paint; accent UI; path-from-query; episode overlay; PNG/SVG |

---

## Memory admin UI (`/memory`)

Single operator surface for **topology + entities + beliefs + hygiene**
(`memory.html` + `memory_console.js` + `memory.js` + `memory_api.py`).

### Layout

1. **Graph & health** (top) — V2 belief canvas, KPIs, probe, backups.  
2. **Ops tabs** — Entities, Beliefs, Pending merges, Hygiene.

### Operator capabilities (2026-08 overhaul)

| Capability | Where | Notes |
|------------|-------|-------|
| **Pagination + real counts** | every list tab | "Showing 1–150 of 3,412" + **Load more**. The graph reports `total_nodes`/`truncated` and shows an amber banner when capped — including **connections hidden by slicing**. No more silent 200-row cap. |
| **Diacritic-insensitive search** | Entities, Beliefs search boxes | Routes through `beliefs_fts` / `entities_fts` (FTS5) — `francais` matches `Français`, aliases are searchable. Falls back to `LIKE` if FTS is unavailable. |
| **"Why recalled"** | belief drawer | Click a belief → see `recalled N× · last <date> · via <method> · from <episode>`, plus a **Probe from this belief →** button. Endpoint: `GET /beliefs/{id}/recall-trail`. |
| **Undo** | action toast | Invalidate-batch, link, edit, delete-entity return a receipt + a 60s single-use undo token; the toast has an **[Undo]** button. Merge shows a "N beliefs rewired" receipt (not undoable — restore from backup). |
| **Single belief edit** | belief row **Edit** | One modal form (subject/predicate/object), not a multi-step prompt. |
| **Single ops bar** | graph Ops bar | The duplicate Link/merge slots card was removed — graph Ops bar is the one source of truth; row **Src**/**Tgt** buttons + Shift-click sync to it. |
| **Multi-tenant** | env flag | `KAZMA_MEMORY_ENFORCE_TENANT=1` scopes reads, id-keyed mutations, undo tokens, and **graph-clear** by the request-scoped tenant. Off by default (single-tenant `default`). There is no all-tenants wipe. Note: `entities.id` is a global PK, not per-tenant. |
| **Group / Ungroup** | graph inspect | View-only clustering (`POST/DELETE /api/memory/v2/graph/groups*`). Does not mutate beliefs. The 30s canvas poll uses groups already on `GET /api/memory/v2/graph` (no extra fetch). |

**Performance:** entity `belief_count` / `graph_degree` are materialized columns
(maintained on every write, self-healing from the `-1` sentinel), so the page
reads precomputed counts instead of running O(entities×beliefs) correlated
subqueries on every load — a ~10× page-open speedup at scale.

### Graph invariants (canvas SoT)

| Rule | Why |
|------|-----|
| **Unique node ids** in the payload | Canvas `_v2gIds[id]` is last-write-wins; duplicate ids orphan one node (the old “two shipx” bug). |
| **Entity wins over virtual fact** | When a belief object text equals an entity id (`user → has_project → shipx`), emit **one** real entity node — never a second `isVirtual` twin with the same id. |
| **No dangling links** | Link source/target must both survive filters (`entity_type`, limit). |
| **Hub is always `id=user`** | Center “You” styling; display name comes from `entities.user.name` (e.g. **Mubder**). |
| **Payload subjects attach to the hub** | A fact whose object is a literal (not another entity) still gets `user → related_to → <subject>` so the concept is not a floating component. |

### Display rename (not id rewrite)

- **API:** `POST /api/memory/v2/entities/{id}/rename` body `{ "name": "ShipX" }`.  
- Canonical **id** stays stable so belief subjects/objects keep linking.  
- Previous labels go into `aliases_json` (resolution still finds old nicknames).  
- UI: graph inspect **Rename**, Entities table **Rename**.  
- Canvas soft-updates labels when only names change (does not reset layout).

### Self / hub identity (User → Mubder)

Backfill and extractors often create a **person shell** (`ent_<hash>` named
`User`) separate from the synthetic hub `user`. Those are the same *operator
identity* for the UI:

| Concept | Behavior |
|---------|----------|
| `memory/self_hub.py` | Detects self labels (`user` / `you` / aliases) and person shells |
| Rename self shell → brand | Also upserts `entities.id=user` with that display name |
| List row | `is_self: true`, `graph_id: "user"` — click focuses the hub |
| Graph paint | Collapses self ids onto one hub node; label = hub display name |

So renaming **User → Mubder** on `ent_…` makes the canvas hub show **Mubder**,
not a hardcoded “You”.

### List ↔ graph bridge

| From | To |
|------|-----|
| Click entity / belief row | Select + zoom on canvas (`_v2gSelectEntity` / `_v2gSelectBelief`) |
| **Double-click** graph node | Highlight matching list row + scroll ops (`kazma:memory-graph-select`) |
| Single-click / drag node | Select + inspect only — **does not** jump the page (free explore) |
| Drag node then release | Node is **pinned** at that position (survives refresh / filter retune) |
| Inspect **In list** | Explicit jump to list (same as double-click) |
| Merge / link / invalidate / rename | Refresh graph payload; pinned positions restored from client cache |

### Belief operator edit

- **API:** `PATCH /api/memory/v2/beliefs/{id}` with any of
  `subject` / `predicate` / `object` / `predicate_type`.  
- Active beliefs only (not invalidated/superseded).  
- Sets `extraction_method=user_explicit`; clears embedding when object text
  changes (FTS triggers keep search in sync).  
- UI: Beliefs tab → **Edit** (guided prompts for object → predicate → subject).

### Other entity ops (unchanged contract)

| Action | Route |
|--------|--------|
| List / filter | `GET /api/memory/v2/entities` |
| Merge shells | `POST /api/memory/v2/entities/merge` |
| Link two entities | `POST /api/memory/v2/entities/link` |
| Delete empty shell | `DELETE /api/memory/v2/entities/{id}` (not protected hub ids). Copies `entity_merges` rows to `entity_merges_archive` first (FK still drops live ledger rows). |
| Invalidate belief | `POST /api/memory/v2/beliefs/{id}/invalidate` (+ batch) |

---

## Key modules

| Module | Purpose |
|--------|---------|
| `memory/recall.py` | Unified recall |
| `memory/belief_mutation.py` / `belief_extractor.py` | Write path + fence/hygiene; INSERT OR IGNORE rowcount + rollback |
| `memory/hygiene.py` | Blocked subjects, FTS self-heal, invalidate + graph delete |
| `memory/ego_anchor.py` | Hub edges for payload-object leaf subjects |
| `memory/fts_health.py` | Periodic FTS `*_docsize` COUNT vs base + rebuild |
| `memory/entity_counts.py` | Single SoT for belief_count / graph_degree SQL |
| `memory/self_hub.py` | Hub display name + self person-shell collapse |
| `memory/graph_backend.py` | SQLite default + Neo4j dual-write + tenant edge clear |
| `memory/backends.py` | Vector / state / graph factory; pgvector auto-select; env Neo4j defaults |
| `memory/federated_search.py` | Memory + KB labeled search |
| `memory/vector_engine.py` | Exact meaning search over every episode and fact; `RECALLABLE_TIERS` |
| `memory/reembed.py` | Rebuild + the 15-minute vector repair |
| `memory/rehydrate.py` | Recovery of memories the pre-2026-09-26 archive rule erased |
| `memory/chat_history.py` | The one reader of chat history (chat store + spool, checkpoints) |
| `memory/turn_reconcile.py` | Every chat-store turn gets its episode |
| `memory/global_reconsolidation.py` | Dedup + re-embed (partitioned; repair via `reembed`) |
| `memory/worker_bootstrap.py` | Queue handlers + **eight** schedulers (single boot entry) |
| `memory/v2_health.py` / `health.py` | Health APIs for Dashboard / Packages |
| `kazma_ui/memory_api.py` | `/memory` admin routes (rename, edit, merge, hygiene) |
| `kazma_ui/static/js/memory_console.js` | V2 canvas + inspect rename |
| `kazma_ui/static/js/memory.js` | Entities/beliefs list + list↔graph bridge |

---

## Scale (trigger only)

| Need | Tracking |
|------|----------|
| Full Postgres-primary recall | [#76](https://github.com/Mubder/kazma/issues/76) — adapter shipped; enable only after `reconcile_memory_mirror.py` is clean |
| Multi-region + conflicts | [#77](https://github.com/Mubder/kazma/issues/77) |
| Hosted embed-only fleet | [#78](https://github.com/Mubder/kazma/issues/78) |

Postgres dual-mirror and sparse ILIKE assist already exist as **optional** foundations. Tombstones now propagate; nightly drift warns if the mirror still holds dead facts.

---

## Transcript recall fallback (2026-08)

Facts that live only in **past chat transcripts** (a naming shortlist, a
decision table) are invisible to V2 recall — recall reads beliefs/episodes,
not raw session text. Until 2026-08-27 the agent compensated by hand-writing
SQL against `chat_sessions.db` (one task burned 21 iterations and a YOLO
gate to answer "what did we decide before?").

Now, when V2 recall returns **nothing** for a query, the supervisor
automatically searches past web chat sessions (title + message text;
longest/most-specific terms win, title matches rank highest, Arabic
supported) and injects the top hits as a **prompt-fenced untrusted block**
(`chat_history` source) next to the memory block — zero extra iterations, no
danger tools, no permissions. Suppressed-recall turns skip it too.

- Read-only, through `memory/chat_history.py`: the chat store the web UI
  writes (Postgres `kazma_chat_sessions` or SQLite `chat_sessions.db`) plus
  its save spool, the current tenant only, every session ranked in SQL.
  Until 2026-09-26 it opened only the SQLite file — on a Postgres install a
  leftover from July — and ranked the 400 most recent matches of any
  tenant. A missing store is a silent no-op.
- Kill-switch: `KAZMA_TRANSCRIPT_RECALL=0` env or ConfigStore
  `memory.transcript_fallback=false` (live-read, default ON).
- Module: `kazma_core/memory/transcript_recall.py`; wired in
  `graph_supervisor.py` (log line: `[Supervisor] transcript fallback: N
  past-session hit(s)`).
- Tip for durable facts: after the agent finds such data, tell it to
  *remember it permanently* — an explicit user store becomes a high-trust
  belief that recall surfaces first, no fallback needed.

## Related

- [Memory best path](./memory-best-path.md) — operator checklist  
- [Architecture](./architecture.md) · [Knowledge Library](./knowledge-library.md) · [Commitment Layer](./commitment-layer.md)  
- [Diagnosis map](../ops/diagnosis-map.md)  
- Plan: `docs/plans/MEMORY_REMAINING.md`  
- Audit: `docs/audits/AUDIT_MEMORY_SYSTEM_2026-08-24.md` (M-01..M-17 closed)  
- Invariants: [`AGENTS.md`](https://github.com/Mubder/kazma/blob/main/AGENTS.md) §15 (schedulers), §20 (source-trust), §29 (trim / shift / scratchpad)
