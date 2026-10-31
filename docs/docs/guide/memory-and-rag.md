---
id: memory-and-rag
title: Memory & RAG
sidebar_label: Memory & RAG
description: Kazma V2 cognitive memory — beliefs, episodes, KB inject, /memory admin, ego-graph anchoring, optional Neo4j/Postgres adapters
---

> This page is published at [kazma.ai/docs/memory-and-rag/](https://kazma.ai/docs/memory-and-rag/) ([Arabic](https://kazma.ai/ar/docs/memory-and-rag/)).

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
| Inject Knowledge into chat | `merge_knowledge_into_chat` (default on; library text is injected into the turn, never saved into chat memory) |
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
on a persona's life -- 1,120 chat turns and 69 facts inside unrelated chat,
89 questions (single facts, paraphrases, things only the assistant said,
updates, codes, Arabic, overviews, questions with no answer) -- and 40 more
held out: written after the last tuning and never used to choose a
threshold, so their scores say how far the tuning carries. CI replays the
real model's recorded vectors and holds every score to a ratchet
(`tests/test_memory_benchmark.py`); `lock` raises it. A score can fall only
in a reviewed trade (`lock --trade "why"`: no score falls by more than 0.05,
one rises by 0.05 or more, abstention never falls), written into the
thresholds file with its reason. The report for this dataset is under
"Benchmark report" below.

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
the index) — not keyword-only. On the retrieval benchmark (dataset v5) it
finds nearly the same answers as local recall: hit rate 0.895 against 0.925,
MRR 0.83 against 0.84, precision 0.62 against 0.64; the gap is one
stopword-heavy question its keyword search misses. It reads only memories
recall may show, ranks the mirror's keyword matches by how many of your
words each holds, and leaves out anything this install forgot, even when the
shared copy still holds it; the 15-minute mirror sync then repairs that copy.
Explicit Qdrant in Settings is never overridden.

Recall waits at most `memory.v2.recall_deadline_s` (default 5 seconds) for
the question's meaning vector and asks the embedder once per question. When
the embedder is slow or failing, recall answers from keywords, the memory
search tool says its search was incomplete (never "nothing remembered"),
and the embedder rests for 30 seconds, then longer, before it is asked again;
memory health shows it as degraded meanwhile.
Kill-switch: `KAZMA_PGVECTOR=0`.

### Post-turn

Every finished turn is handed to memory once, by the closer every transport
runs (`kazma_ui.turn_runtime.close_turn` → `consolidator.remember_turn`) —
web, Telegram, Slack and Discord alike. Until 2026-09-26 only the gateway
handler did it, so no web chat turn was remembered from 2026-08-08; a
15-minute **turn reconcile** now writes an episode for every chat-store
turn that has none, with the turn's own time, which recovered those turns and
catches any future gap. The turns it writes then get their facts like a live
turn (the heuristic pass at once, the LLM pass queued, 60 turns a pass).

**Facts follow the order they were said in** (2026-09-27). A fact carries
the time it was stated (`valid_from`), apart from the time it was written
(`ingested_at`). A single-valued statement OLDER than the current fact -- a
reconciled turn, a turn that waited in the queue, the LLM pass arriving
after a later turn -- is kept as history in its place on the timeline and
never replaces the current fact. Before, whichever statement was written
last won.

**A list stays a list** (2026-10-19). When you keep several notes of one
kind ("user noted ..."), a new one is added to them, even when the fact pass
labels it as a fact with one value. Before, one such note could end all the
others: on the live install one note replaced one and closed 57. Notes closed
that way can be reopened. Size it first (this only reads, and lists entries
by id):

```bash
python scripts/memory_reopen_lists.py size
```

Then, when Kazma is idle, run `apply --db <path to memory_state.db> --yes`.
The file is copied to `memory_state.db.pre_reopen` first, and each reopened
entry is written to the memory audit log.

- Mirror working/recall episode (the question and ITS answer; the episode's
  turn number is the conversation's turn index). Recall shows the model
  both sides ("User: ... / Assistant: ...", `memory/episode_text.py`,
  2026-09-27) -- it used to show the question alone, so a recalled "what did
  you suggest?" came back without the suggestion. The meaning vector is made
  from the question (the answer was tried and measured: it cost precision
  and found nothing more); keyword search and word coverage read the answer
  too.
- "Remember this" (or تذكر، لا تنسى، احفظ، سجل عندك ...) puts the turn straight
  in the recall tier, in English or Arabic. Small talk -- a greeting or thanks
  with a short reply -- is kept but never shown as history, and a greeting
  looks nothing up.
- The graph walk that connects facts ("works at Acme" + "Acme is in Lisbon")
  reads the facts around the question's entities, however many other facts
  there are; hybrid vector search merges the remote index with the local
  store, so a memory not yet copied to the remote index is still found.  
- Heuristic (+ optional LLM queue) belief extraction → `mutate_belief`, stated at the turn's time. A `user_explicit` functional belief **cannot** be superseded by `llm_inferred` / `system_tool` (commitment source-trust gate in `_mutate_functional`; independent of `authorize_effect`), nor cut short by an earlier lower-trust statement.  
- Hygiene rejects stack/version subjects (e.g. `kazma_v2_4_0` mistaken for product version)  
- Dual-write: optional Postgres state mirror + Neo4j edge upsert  
- **Ego-graph anchor** — every non-hub subject that does not already reach `user` gets `user → related_to → <subject>` at write time (payload leaves **and** floating entity clusters). Payload objects (`fully_clean`, paths) are **not** minted as concept entities (that mint used to skip the hub edge). Idempotent backfill on the 6h sweep.  
- Invalidate / supersede / graph-clear **tombstone the Postgres mirror** and best-effort **delete Neo4j edges** (mass clear uses `clear_tenant_edges`).  

A turn whose memory could not be written, or whose fact pass found the model unavailable, is not counted as remembered: it goes back to the memory queue and is tried again later, waiting longer each time (30 seconds, then 2, 8 and 32 minutes, at most an hour). A task that fails every attempt stays in the Memory page's task queue, marks memory health as degraded and sends an operations alert until you retry or clear it. Memory writes have queue slots of their own, so a backup never holds up a turn.

### Schedulers (`worker_bootstrap`)

**All loops start from `start_memory_worker()`** — add a new one there or it never runs (the gap that once left backups inert). Current boot list is **eight** loops.

| Cadence | Work |
|---------|------|
| ~6h | `macro_sleep` (rule-based tier moves + archival, below) + ego-anchor backfill + FTS drift COUNT (`*_docsize` vs base; rebuild on mismatch) |
| **~6h** (not 24h) | `native_backup` + JSONL/GraphML/episodes/merges/audit export + `native_pg_backup` + mirror-drift warning. Universal backup **checks** PG dump freshness; it does not dump twice. |
| ~24h | `global_reconsolidation` (dedupe + re-embed; **partitioned** for large corpora; recomputes entity counts) |
| ~15m | commitment GC (TTL + soul-pending), HITL-gate TTL sweep, **memory vector repair** (missing, wrong-size or old-model vectors re-encoded in place, ~20 s a pass, no model load when there is nothing to do), **memory recovery** (below), **turn reconcile** (chat-store turns without an episode, then their facts) and **knowledge vector repair** (Knowledge Library chunks without a meaning vector, shortest first, up to 5 min a pass, last in the cycle) — one sweep runner, no extra loop |
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

Export writes `kazma_beliefs_latest.jsonl`, GraphML, plus episodes, `beliefs_archive`, `entity_merges` (+ archive), and `memory_audit_log` (per-tenant filenames when not `default`), each table whole and as plain text: vectors are left out (they are rebuilt), other binary values are base64. A piece that cannot be written fails the export task, which the memory queue retries. Native `.db` backups remain the restore SoT: each is one consistent copy, taken in a single step while Kazma keeps writing.

Memory health shows how long the oldest queued task has waited, how long the oldest running one has run, when each kind of task last succeeded, and each maintenance sweep's last outcome. A sweep that fails three passes in a row, a task the queue refuses, and a task that fails every attempt are reported, not just logged.

The Postgres mirror is kept whole by a sync pass on the 15-minute maintenance cadence. Every memory or fact written here, or changed in something the mirror holds (a tier move, a forgotten memory, an ended fact), is queued in the same transaction as the change, and the pass pushes the queued rows as they are now, in batches over pooled connections. Once a day (`memory.v2.mirror_verify_hours`, 1-168) it also compares every row with the mirror and pushes whatever differs, newest first, up to 5,000 rows a pass; while it is still pushing with more behind, the next pass compares again rather than a day later. A pass also asks the mirror for the newest rows it was already sent, and compares in full at once when one is missing: a restore of the shared database takes rows back that no pass would push again (at most once an hour). A push that keeps failing is retried five times and then left to that comparison. A row only the mirror holds is counted in memory health and left alone. Recall tops up a thin answer only with memories another install wrote; a memory this install removed never comes back through the mirror.

What the agent stores with its memory tools (`memory_store`, linking two entities) records the chat and turn it was said in: forgetting that turn or chat forgets those notes and facts too, and in a chat kept out of memory the tools store nothing. Compacting a long conversation no longer stores its summary as a memory; the turns themselves are remembered.

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
- **Never written into memory** — Knowledge Library text is shown to the model for the turn and never stored as a memory of the conversation. Copies earlier builds made are emptied by the maintenance pass (the library keeps the text).

Deep dive: [Knowledge Library](./knowledge-library.md).

---

## Neo4j (optional dual-write)

- **SoT for beliefs remains SQLite** (bi-temporal, scrub, Dashboard filters).  
- **Dashboard topology paints from SQLite** when Neo4j is configured (types + bi-temporal).  
- Neo4j receives dual-writes on mutate; **Sync beliefs → Neo4j** backfills existing rows.  
- Soft-invalidate / supersede / **graph-clear** best-effort **deletes** Neo4j edges (tenant-scoped). You should not need a manual Sync after Clear graph.

### Operator setup

1. Run Neo4j (e.g. `docker compose -f deploy/docker-compose.neo4j.yml up -d`).  
2. Install the driver in Kazma's Python: the `neo4j` extra (`uv pip install -e ".[neo4j]"`, or Settings → Packages). Without it the graph stays in SQLite, and Kazma says so once at boot and Settings shows `driver_missing`.  
3. Settings → Memory → Graph store **Neo4j**, URL `bolt://localhost:7687`, password, **Save**.  
4. **Test Neo4j** (masked `***` passwords do not overwrite the vault secret).  
5. **Sync beliefs → Neo4j**.  

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

## Forgetting, and chats kept out of memory

You decide what Kazma keeps:

- **Forget one memory.** The Memory page lists what Kazma remembers of your
  conversations ("Memories of conversations"), newest first, a page at a
  time ("1–30 of 412", **Older** and **Newer**), with a search box that finds
  the memories holding every word you type. **Forget** removes one, from
  that list or from a search result on the same page (the memory search and
  "Search both stores"); weekly summaries page and forget the same way. Kazma
  keeps nothing it said -- the question, the answer, the search vector --
  and the facts it learned from that part of the conversation lose their
  value, their vector and their edit history, wherever a copy was kept: the
  fact archive, the record of changes to facts, a turn still waiting to be
  processed, the search index. The chat itself stays as it is.
- **Ask in chat.** "Forget my codeword" ends the fact and forgets the part of
  the conversation it came from, as **Forget** on the Memory page does --
  otherwise recall would find that turn and the next turn would learn the
  fact again. When the fact's value names one thing (one word of six
  characters or more with a digit, `-` or `_`: a codeword, an order or phone
  number), every memory that repeats it is forgotten too, Kazma's own earlier
  answers quoting it included; a plain word such as a city is not swept.
  "Forget everything that mentions BLUE-HERON-42" forgets every memory of a
  conversation and every current fact that holds that text (four characters
  at least; Latin letters in any case), not only the few a memory search
  shows, and removes every lesson Kazma wrote for itself that holds it. Each
  of these asks for your approval first, and the message in which you ask to
  forget is not remembered either. A fact that had already ended keeps its
  value in the fact history.
- **Don't remember a chat.** In the chat list, a chat's menu has
  **Memory...**: turn it off and Kazma remembers nothing new from that chat
  and forgets what it already remembered from it. In Telegram, Discord or
  Slack, send `/memory off` (and `/memory on` to start again). `/memory`
  says whether the chat is remembered.
- **Deleting a chat keeps what Kazma learned from it**, unless you tick
  **Also forget what Kazma learned from this chat** in the delete dialog:
  then the chat's memories and facts are forgotten before it is deleted.
- **Notes Kazma saves with its memory tools belong to the chat** they were
  saved in: forgetting that chat (or that turn) forgets them too, and in a
  chat you keep out of memory Kazma saves nothing.
- **Take it with you.** **Export** on the Memory page downloads what Kazma
  remembers as JSON: your About me, facts (current, past and archived, with
  the chat they came from), every change made to a fact, memories of
  conversations, weekly summaries, entities and their merges, the groupings
  you made on the graph, the tool procedures Kazma learned, and when things
  were forgotten (never what). The file's `not_included` part names what it
  leaves out and why: work still queued, the safety layer's approval
  records, search indexes rebuilt from the rest.

A forgotten memory stays forgotten: nothing that rebuilds memory from your
chat history brings it back.

**Rewinding a chat rewinds what Kazma remembers of it.** `/undo`, `/edit`,
restoring an earlier point in Time Travel and aborting a turn take the
rewound turns out of memory as well -- the memory of each and the facts it
taught -- so the old answer cannot come back as something Kazma "remembers".
A fork starts a new chat and leaves the original's memory as it is.

**What Forget cannot reach.** Copies made before you forgot keep it until
they are replaced: the memory backups (ten, one every six hours), Kazma's
full backups (seven), the offsite backups (up to twelve monthly), the
memory export written every six hours, the copy kept before an embedding
rebuild, the copies the operator's repair scripts keep before they change the
database (`memory_state.db.pre_entity_key`, `memory_state.db.pre_relabel`,
`memory_state.db.pre_reopen`, until removed), and space the database has
freed but not yet reused. Kazma's lessons for itself are reached only by
"forget everything that mentions ..."; lessons written before 2026-10-19
could quote the message they came from. The
Forget dialog says so too.

## About me

**Settings → Memory → About me** is a short text (up to 2,000 characters)
that you write: your name, your work, where you are, how you like to be
answered. Kazma reads it at the start of every reply, in every chat. It is
never filled in by Kazma, and what you say later in a conversation wins over
it. Clear the text to remove it. On an install with several users, only an
administrator can change it: Kazma takes it into account for everyone who
uses that tenant.

## One fact, one name

When Kazma learns a fact it names it (`timezone`, `works_at` ...). The same
fact could come back under another name -- `timezone` and `timezone_is`,
`daily_tweet_cap` and `tweet_daily_cap` -- and then an old value and a new one
were both current. A fact is now written under the name Kazma already uses for
it when the words are the same, so a new value replaces the old one; the daily
memory maintenance retires facts already stored twice (keeping the latest).
Where your own words and something Kazma inferred disagree, both are kept for
you to settle.

What you say about yourself outranks what Kazma works out or stores on its
own. A fact Kazma saved with its memory tools without you asking -- from a
page it read, an email, its own reading of the chat -- never stands in the
way of you correcting it: say "I live in Lisbon now" and that is what Kazma
remembers. When you ask Kazma to remember something ("remember that ...",
"تذكر أن ..."), what it stores in your words counts as yours. Text that reads
like instructions to Kazma is refused; anything a page or an email slips past
that check is still never treated as your own words.

Facts Kazma's memory tools stored before 2026-10-13 were all labelled as
yours. They can be relabelled as Kazma's own. Size it first (this only reads,
and lists facts by id, never by what they say):

```bash
python scripts/memory_relabel_provenance.py size
```

Then, when Kazma is idle, `apply --db <path to memory_state.db> --yes`: the
file is copied to `memory_state.db.pre_relabel` first. A fact changes only
when what the memory recorded shows a memory tool wrote it -- a note the tool
saved names it, or the fact names the chat turn the tool ran in -- and keeps
your label when you edited it on the Memory page, acted on it there, or the
turn asked Kazma to remember those words. Facts nothing shows the origin of
(the Memory page's own links, the one-off import from the earlier memory
system) are left as they are. Each change is written to the memory audit log; running it again
changes nothing.

## Names in any language

A person, a company or a place Kazma learns about keeps its name in the
language you used: "شركة الخليج", "東京タワー" and "Gulf Company" are three
different things to it. Until 2026-10-13 a name with no Latin letters was
filed under one shared id, so two Arabic facts about different things could
replace each other, and a later Arabic name could be stored as the word
"entity". Names that differ only by vowel marks or the form of a letter
("أحمد", "أَحْمَد", "احمد") are recognised as one name.

Facts the old rule filed under "entity" can be repaired. Size it first (this
only reads):

```bash
python scripts/memory_entity_repair.py size
```

Then repair a copy of `memory_state.db` (`apply --db <copy> --yes
--no-queue`) to see the result, and, when Kazma is idle, the install's own
database (`apply --db <path> --yes`). Each collapsed fact is set aside --
kept, no longer used -- and the turn it came from is read again by Kazma's
memory worker, which files its facts under their real names. A fact whose
turn is gone stays set aside; nothing is guessed.

On an install with several users, each user's people, places and things are
their own: two users can each have a "Kazma" or a "Noor", and each has their
own "You" at the centre of their graph. Until 2026-10-15 a name was one entry
for the whole install, held by whoever used it first. A memory database made
before then keeps that shared entry until it is changed over; size it first
(this only reads), then, when Kazma is idle, apply it:

```bash
python scripts/memory_entity_key.py size
python scripts/memory_entity_key.py apply --db <path to memory_state.db> --yes
```

The file is copied to `memory_state.db.pre_entity_key` first; every entry
keeps its name, and running it again changes nothing. Memory health shows
which form the database has (`entity_key`). A single-user install loses
nothing by waiting: it has one user.

## Weekly summaries

A topic you work on for weeks can fill a hundred conversation turns a week,
and a question about it brings back only the few turns that match best. So
once a week is over (a day later), Kazma writes a short summary of each topic
from that week: what it was about, what was decided or done, and what was
left open.

- **A topic** is a chat with at least 4 turns that week. Turns from short
  chats, and the notes Kazma saved for you, are grouped by what they are
  about.
- **Where to see them:** the Memory page, "Weekly summaries". Forget one and
  Kazma never writes it again; the conversations it was written from stay.
- **Forgetting a conversation turn** also removes it from every summary: the
  summary is emptied at once and written again without it -- also when you
  forget it while that summary is being written.
- **Settings** (`memory.v2`): `summaries_enabled` (on), `summaries_min_turns`
  (4), `summaries_max_per_week` (24 topics, largest first). One model call per
  topic.
- **When they are used:** a question about a topic brings back its week's
  summary (at most two), shown to the model after the conversation turns
  that match. A summary has to be clearly about the question: on the
  author's install every "where are we with ..." question found its topic,
  and no unrelated question brought one in.
- Summaries are included in the memory export. They are kept in the memory
  database on this install and rebuilt from the conversations; they are not
  copied to the Postgres memory mirror.

## Memory admin UI (`/memory`)

Single operator surface for **topology + entities + beliefs + hygiene**
(`memory.html` + `memory_console.js` + `memory.js` + `memory_api.py`).

Every change made here leaves a trail: a log line and an audit row naming
the action, who made it, the tenant and the ids, never a value. The daily
digest counts the memories forgotten from this page, and `/metrics` counts
the changes requested per action (`kazma_memory_operator_actions_total`).

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
| **Multi-tenant** | env flag + posture | Tenants are enforced with `KAZMA_MEMORY_ENFORCE_TENANT=1`, in production (`KAZMA_PRODUCTION=1`) and with multi-user on. Then every `/api/memory` route reads, counts and changes only the request tenant's rows -- another tenant's id reads as not found -- including health, quality, hygiene, undo tokens and **graph-clear**; the engine's task queue, reconsolidation and golden eval are admin-only. A user bound to no tenant sees the install (single-tenant `default`). There is no all-tenants wipe. Entities are keyed by tenant and id (2026-10-15): each tenant has its own row of a name and its own hub; a database made earlier keeps one shared key until `scripts/memory_entity_key.py apply` changes it over (health: `entity_key`). |
| **Group / Move / Tier / Ungroup** | graph inspect | View-only clustering (`POST/DELETE /api/memory/v2/graph/groups*`). Does not mutate beliefs. **Group under→** on a node that already has a parent moves it (`…/member/{id}/move`: the old parent goes and the nodes below shift with it); **Tier…** sets a grouped node's tier, 0 to 4 (`…/node/{id}/tier`). The 30s canvas poll uses groups already on `GET /api/memory/v2/graph` (no extra fetch). |

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
| Delete empty shell | `DELETE /api/memory/v2/entities/{id}` (not protected entities). Copies `entity_merges` rows to `entity_merges_archive` first (FK still drops live ledger rows). |
| Purge empty entities | Hygiene tab, `POST /api/memory/v2/hygiene/run` with `purge_empty_entities`: removes only shells with no live fact and nothing but their own name, the rule that removes them automatically. Merge redirects stay: they send a merged name to its target, and the Entities list shows them as "merged into". The page's **Empty** count is this list. |
| Invalidate belief | `POST /api/memory/v2/beliefs/{id}/invalidate` (+ batch) |

Protected entities are never deleted, purged or merged away: the hub
(`user`), the agent (`assistant`, `kazma`), the hub's own names (an entity
the hub's aliases list, such as the person entity you were before the hub
took your name) and any entity you mark **Protect**. One of the hub's own
names can still be merged into the hub. Kazma learns the owner's names from
the hub, never from its code (`memory/entity_protection.py`).

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

Postgres dual-mirror and sparse ILIKE assist already exist as **optional** foundations. Tombstones propagate, and the 15-minute mirror sync pushes what changed (and, once a day, whatever the mirror is missing or holds stale; a row only the mirror holds is counted, never deleted).

### Benchmark report (dataset v5, 2026-10-14)

Recall on the retrieval benchmark, the tuning questions and the held-out
ones, on both paths (vectors replayed from bge-m3):

| | Local (SQLite) tuning | Local held out | Postgres-primary tuning | Postgres-primary held out |
|---|---|---|---|---|
| Questions | 89 | 40 | 89 | 40 |
| Hit rate (every piece of the answer shown) | 0.925 | 0.891 | 0.895 | 0.891 |
| MRR (how high the first answer ranks) | 0.844 | 0.845 | 0.830 | 0.845 |
| Precision (share of what is shown that answers) | 0.644 | 0.646 | 0.621 | 0.630 |
| Abstention (nothing shown when nothing answers) | 0.28 (5 of 18) | 0.00 (0 of 8) | 0.28 | 0.00 |
| Questions across languages (held out, `mixed`) | | hit 1.0, MRR 0.90 | | hit 1.0, MRR 0.90 |

- **The ranking carries over:** the held-out hit rate, MRR and precision are
  within a few points of the tuning set's, on both paths, and a question in
  Arabic about something said in English (or the reverse, or both in one
  sentence) is answered as well as any other.
- **Abstention does not.** Recall shows something for most questions nothing
  answers: 13 of 18 tuning questions and every one of the 8 held out, which
  were written to come close to a real memory (another person's dentist, a
  car's colour). The target is 0.6 on both sets (`TARGETS` in
  `scripts/memory_bench.py`); it is a goal recorded beside the floors, not a
  gate, and abstention is the one score no trade may lower.
- **The thresholds cannot buy it** (swept 2026-10-14: the memory floor 0.04
  to 0.10 against the fact floor 0.06 to 0.12, 16 settings). Every setting
  that shows nothing for a held-out question loses answers elsewhere past the
  trade rule's 0.05 -- Arabic hit rate 0.25, "noise" 0.33, overviews 0.5 --
  and the one setting inside the rule raises tuning abstention only (0.28 to
  0.39) and leaves the held-out set at 0: a choice fitted to the tuning
  questions. What these questions share with their near-miss memories is the
  person or thing (Noor, Maya, the car); telling "Noor's dentist" from
  "Noor's school" needs a signal of what is asked about, not a higher floor.
  Most of what is shown for them is marked "(possibly related)" to the
  model.
- **A signal of what is asked about, measured, not adopted** (2026-10-15):
  a question naming a word no memory holds ("colour", "salary",
  "cholesterol", "anniversary") asks about something unremembered, so only
  strong matches are shown for it. It reaches the target -- abstention 0.28
  to 0.67 on the tuning set, 0 to 0.50 (4 of 8) held out -- but the true
  answers it removes look the same: weak matches of a paraphrase ("who looks
  after my teeth"), an Arabic question whose word carries a suffix the
  memory does not ("قهوتي"), a question naming a category its answer is an
  instance of ("which company is it at"), with the same evidence as the near misses
  (meaning lift 0.01-0.09 against 0.04-0.09). Paraphrase hit rate falls 1.0
  to 0.88, Arabic 1.0 to 0.75, "noise" 1.0 to 0.67, updates 1.0 to 0.86,
  overall MRR 0.84 to 0.77: past the trade rule, in each variant tried on the
  tuning set (summaries left out; only when nothing strong is shown). Asked
  only when most of the question's words are known, it still loses an update
  ("which company is it at now": no memory says "company"; at three fifths
  known, held out 1 of 8) or Arabic answers (at half); the one setting inside
  the rule (two thirds known) leaves held-out abstention at 0.

Extraction (`python scripts/memory_bench.py extraction`): 30 single turns
in English, Arabic and both, scored on what a reader would keep from each.

| Heuristic pass | Recall | Precision | Turns with nothing to keep that kept nothing |
|---|---|---|---|
| English (12 turns) | 0.46 | 1.0 | 3 of 3 |
| Arabic (10 turns) | 0.64 | 1.0 | 2 of 2 |
| Mixed (8 turns) | 0.60 | 1.0 | 1 of 1 |

The heuristic pass is the quick first look at every turn. Until 2026-10-14 it
had English patterns only and kept nothing from Arabic (0.00, mixed 0.20);
it now reads the same kinds of fact in Arabic and in Arabic mixed with
English -- name, where you live (or moved to), where you work, a favourite,
a preference, an allergy (`memory/arabic_facts.py`). Its rules come from the
grammar, so it keeps nothing from a negation ("ما أحب"), an instruction to
Kazma ("استخدم البحث"), a question, someone else ("أخوي ساكن في دبي") or a
field of work ("أشتغل في المبيعات", like "I work in sales"). What it misses --
relatives, jobs, dates -- is the model's deep pass to find. That pass is scored from the configured model's recorded
replies (`scripts/memory_extraction_record.py --out <fixture>`, run with an
install's own Python, one model call per turn); a turn with no recorded reply
is an error, never a guess. Recorded 2026-10-15 with DeepSeek's
`deepseek-flash`:

| Model's pass | Recall | Precision | Turns with nothing to keep that kept nothing |
|---|---|---|---|
| English (12 turns) | 0.77 | 0.79 | 3 of 3 |
| Arabic (10 turns) | 0.91 | 0.91 | 2 of 2 |
| Mixed (8 turns) | 0.70 | 0.58 | 1 of 1 |

The first recording found the deep pass translating what Arabic speakers
said into English slugs ("ليلى" kept as `layla`, "دبي" as `dubai`): the
facts were there, the user's own words were not (Arabic recall 0.09). Its
prompt now keeps every subject and value in the language and script the user
wrote it in; predicates stay English. A slugged value is compared as the
words it stands for (`kuwait_city` is "Kuwait City"), as search reads it.

### Measured limits

Measured on 2026-10-14 at the live size (about 1,700 memories; as many facts
assumed, the live count was not taken) and at 10 and 100 times it. Text
follows a natural word distribution, every memory and fact has a
1024-dimension vector, and the Postgres mirror was a disposable PostgreSQL
16.15 in Docker, limited to 1 CPU and 1 GB. Machine: Intel Core (family 6,
model 183), 32 logical CPUs, 64 GB RAM, Windows 11, Python 3.12.9, SQLite
3.47.1, sqlite-vec 0.1.9, NumPy 2.5.0, the test process at below-normal
priority. Seconds:

| | 1x (1,700) | 10x (17,000) | 100x (170,000) |
|---|---|---|---|
| A whole recall (one question) | 0.08 | 0.67 | 8.2 |
| Exact meaning search, every memory (sqlite-vec) | 0.007 | 0.20 | 1.8 |
| The same without sqlite-vec (NumPy) | 0.012 | 0.27 | 2.7 |
| Keyword search | 0.0002 | 0.003 | 0.03 |
| A first macro-sleep pass, mirror on (moves most rows) | 0.40 | 7.0 | 53 |
| Mirror: a pass with nothing changed | 0.0001 | 0.0001 | 0.0001 |
| Mirror: a pass with 100 changed memories | 0.014 | 0.016 | 0.024 |
| Mirror: the daily full comparison, nothing to push | 0.06 | 0.35 | 4.9 |
| Mirror: 100 single pushes, pooled / a connection each | 0.24 / 2.0 | 0.26 / 2.1 | 0.46 / 2.3 |

What the numbers mean:

- **Recall is the limit.** Meaning search compares the question with every
  memory and every fact (no approximate index), so its cost grows with the
  store: about 0.7 s at 17,000 memories, 8 s at 170,000. Keep a store under
  roughly 40,000 memories and as many facts for recall under two seconds on
  a machine like this one. Past that, an approximate vector index is the
  change to make.
- **The mirror no longer grows with the store.** A pass pushes only what
  changed; only the daily comparison reads everything (5 s at 100x). Before
  this, every 15-minute pass read every text and both digests, and pushed
  each row on a new connection: the first macro-sleep pass at 10x took 336 s
  pushing 16,000 moves one by one, 7 s in batches.
- **Macro-sleep** is linear: one pass reads every memory of a tenant once.

The run is opt-in (`tests/test_memory_scale_bench.py`,
`KAZMA_TEST_MEMORY_SCALE=1,10,100`, with `KAZMA_DATABASE_URL` and
`KAZMA_TEST_ALLOW_REAL_DB=1` for the mirror half on a disposable database).
It fails when a size goes over the limits it names.

### Restore drills (RPO and RTO)

Run on 2026-10-14 on the same machine, at the live size (1,700 chat turns,
half of them before the backup). Each drill uses the product's own steps on
private stores:

| | Local memory (SQLite) | Postgres-primary memory |
|---|---|---|
| The backup | the native memory backup (6-hourly) | the Postgres dump of the mirror tables (6-hourly) |
| The disaster | the memory database restored from it | the shared database restored from the dump |
| Lost (RPO) | nothing a chat turn holds: the 850 turns since the backup were rebuilt from the chat store; an agent's note saved since the backup is lost | nothing: the 850 memories since the dump were pushed back from the local store |
| A forget made after the backup | stays forgotten (the forget journal) | stays forgotten, in the mirror and in recall |
| Time to whole (RTO) | 0.95 s, at the restore | 0.11 s, the next maintenance pass (at most 15 minutes) |

Meaning search for the rebuilt turns follows as the vector repair pass
embeds them (about 20 seconds a pass, every 15 minutes). The drills run in
CI (`tests/test_memory_restore_drill.py`; `tests/test_memory_restore_drill_pg.py`
on a disposable Postgres with `pg_dump`), each with the setting that lost
data as its negative control: before the drills, a restored memory database
lost every turn of a chat changed since the backup (turn reconcile's cursor
lived in the settings store, past them), forgets made since came back for up
to 15 minutes, and a restored mirror waited up to a day.
`KAZMA_TEST_MEMORY_RESTORE_DRILL_TURNS` sets the size.

### The soak

`scripts/memory_soak.py` runs the memory lifecycle on private stores, one
process per cycle (every cycle a restart), killing some cycles partway:
turns live and missed, forgets and chats kept out, the maintenance passes,
backups and restores from older ones. After every cycle it checks that each
chat turn is a memory unless forgotten, that a forget leaves nothing behind,
that both databases are intact and that recall finds what it is asked. A
60-cycle pilot (10 killed, 13 restores) was clean. A 72-hour run passed on
2026-10-20 (commit `de461eae`): 7,050 cycles, 811 of them killed, 405
restores, 1,008 backups, 26,389 turns, 0 violations.

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
