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
| Tenant mode | Retired: memory is one memory per install today, and per space once Spaces is on. An install that stored `per_platform` or `per_user` keeps it until its owner presses **Use one memory** |
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

> **The model is pinned (2026-11):** Kazma loads, downloads and checks
> `BAAI/bge-m3` at one commit (`5617a9f61b028005a4858fdac845db406aefb181`),
> never at the repository's `main` branch, so a change pushed to the model's
> repository does not reach an install. It is the commit every install's cache
> already holds, so nothing downloads again. A model you configure yourself
> follows its repository's main branch unless you give a revision
> (`memory.embedding.revision`, or `KAZMA_EMBED_REVISION`).

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
5. **Dates.** Every conversation memory is shown with the day it was said
   ("[12 Jan 2026] User: ..."), every fact with when it began: the start
   you named ("we moved last month", worked out from the day you said it),
   else the month you said it ("[since May 2026] user lives in London").
   Something you did or that happened to you is shown on its day ("[on 30
   May 2026] user attended a pottery exhibition", "[in Apr 2026]"), and
   something ahead as planned ("[planned for 3 Jun 2026]", then "[was
   planned for ...]"). The same thing done on two days is two entries, the
   latest shown first; a question about a period, or for all of a kind,
   shows each thing once before the other times it happened, and counts the
   ones it has no room for ("and 3 more"). Text you pasted, quoted or asked
   Kazma to translate or correct is not taken as something you did; where it
   is not clear whose words they are, it is kept as Kazma's reading. Kazma
   works the day out from what you said, so on a fact you stated yourself it
   is marked as Kazma's reading; forgetting the message that gave it takes
   the day back. A fact that holds one value is shown with the value it replaced
   ("before that: Paris (Jan 2026 to May 2026)"). Dates are in the time
   zone set for scheduled tasks. A value you asked Kazma to forget is never
   shown as "before that".
6. **The question's time** (since 2026-11-21). In a question about the past,
   "yesterday", "last week", "last month", "two weeks ago", "in August",
   "in 2025", "أمس", "الشهر الماضي", "قبل أسبوعين" and "في يوليو" are read as
   a period, in the same zone, and are not counted as words a memory must
   hold. A question about what was said then ("what did I tell you about
   my race in August?") is answered only from what was said in that
   period. A question about what was true then ("where did I live in
   June?") is answered from the facts that held then, an earlier value
   included, with the turns said then first; something you did counts on
   its own day ("what did I buy last month?"). "At first", "originally",
   "before we moved", "في البداية" and "قبل ما" show a fact's earlier values
   right before the current one, each with the months it held. A question
   about a day ahead ("what's on March 22?") is left as it is. Since 28
   November 2026 a question can also name three weeks ago, this year, the
   start of a month, a counted stretch through today, or the Gulf "آخر كم
   يوم". A month or a year that says when it was said wins over a stretch
   that only names the topic. The questions and the periods are
   [below](#question-time).
7. **Follow-ups.** A short message that goes on from the one before it
   ("And her sister?", "Yes, book it for Saturday", "ووين يشتغل؟") is
   searched together with your message before it, with no model involved.
   A stored turn that only goes on from the one before it is shown on that
   turn's line ("-> then: ..."), never on a line of its own: stored as
   typed, its meaning says nothing of what it answers.
8. **What the chat already shows.** The chat's own turns that the model
   reads in the conversation are not recalled into the block a second
   time; older turns of the chat that no longer fit are.
9. **Counting and listing.** A question that asks for every memory of a
   kind ("how many long runs did I do?", "list all my subscriptions", "كم
   مرة", "عطني كل خططي") gets up to 12 facts and 12 conversations (a list whose
   entries were kept under several names, "plans to" and "intends to", as one), shown shorter, and a rule
   telling the model these are only the best matches. The `memory_list`
   tool (a read tool: no approval) lists every memory that holds some words
   or comes from a period, newest first, 30 a page, each with its id: the
   current facts (for a period, the values that held then and what
   happened in it) and the conversations. An Arabic word is found in any usual spelling, with or
   without its article ("قهوه" finds "القهوة"), as an English one is in any case, singular or plural.
   It reads only the space it runs in, never a forgotten
   memory, and its text reaches the model as data.
10. **Ids.** Every recalled conversation ends with its id ("(memory
    e_...)"), with the ids of any turns joined to it, so `memory_read` can
    read it whole.
11. **Numbers.** A one-digit number counts as a word when recall ranks
    ("week 4" is not "week 14").
12. **An incomplete search says so.** When recall could not search by
    meaning, or could not search at all, the turn gets a note saying so and
    that an empty block does not mean nothing is remembered. On
    Postgres-primary a mirror search that fails is a failed recall, never
    "nothing found". The TUI's memory search and
    `GET /api/memory/graph/search` (its `degraded` field) say it too.
13. Fence: `format_untrusted_block(..., source="memory_v2_recall")`.

### Questions and the period they read {#question-time}

These are questions from `tests/test_memory_time_words.py`, read on
25 September 2026 at noon UTC, the clock that file uses. Each Arabic
question reads the same period as the English one beside it. A period
written as "up to" a date stops at the start of that date. A stretch
"through the moment asked" includes that noon.

| English | Arabic | Period |
|---|---|---|
| What did I do three weeks ago? | شنو سويت قبل ثلاث أسابيع؟ (Gulf); ماذا فعلت قبل ثلاثة أسابيع؟ (MSA); شنو سويت قبل حوالي ثلاث أسابيع؟ | 31 August 2026 up to 9 September 2026 |
| What did we talk about this year? | شنو تكلمنا عنه السنة هذي؟ (Gulf); شنو تكلمنا عنه هذه السنة؟ and شنو تكلمنا عنه هذا العام؟ (MSA). The same reading: السنة هذه، العام هذا، هالعام | 1 January 2026 through the moment asked on 25 September 2026. A question about what was said |
| What did we talk about at the start of August? | شنو تكلمنا عنه بداية أغسطس؟; شنو تكلمنا عنه بداية شهر أغسطس؟; شنو تكلمنا عنه في بداية شهر أغسطس؟ | The whole of August 2026 (31 days). "Start" and "بداية" also ask for a fact's earlier values |
| What did we talk about in the past 10 days? | شنو تكلمنا عنه آخر عشر أيام؟ (Gulf); شنو تكلمنا عنه في الأيام العشرة الماضية؟ (MSA) | 15 September 2026 through the moment asked. Today is included |
| What did we talk about in the last 3 weeks? | شنو تكلمنا عنه آخر ثلاث أسابيع؟; آخر ثلاثة أسابيع؟; في الأسابيع الثلاثة الماضية؟; ثلاث أسابيع اللي فاتت؟ | 4 September 2026 through the moment asked |
| What did I tell you in the last few days? | وش قلت لي آخر كم يوم؟ and وش قلت لي هالكم يوم؟ (Gulf); ماذا قلت لك في الكم يوم اللي فاتت؟ | The last 3 days: 22 September 2026 through the moment asked. The same period as "the last 3 days" |
| What did I say in the last 3 days of August? | شنو قلت لك آخر ٣ أيام من أغسطس؟; آخر ثلاث أيام من أغسطس؟; في الأيام الثلاثة الماضية من أغسطس؟ | 29 August 2026 up to 1 September 2026 |
| What did I tell you in the last few days of August? | شنو قلت لك آخر كم يوم من أغسطس؟ | The same three days: 29 August 2026 up to 1 September 2026 |
| What did I tell you in the last 5 days of last month? | وش قلت لك آخر خمس أيام من الشهر الماضي؟; وش قلت لك آخر خمس أيام من الشهر اللي فات؟ | 27 August 2026 up to 1 September 2026. On this clock last month is August. Not the whole month |
| What did I tell you in the last few days of last month? | وش قلت لك آخر كم يوم من الشهر الماضي؟ | 29 August 2026 up to 1 September 2026 |
| وين كنت ساكن من شهر أغسطس؟ | (the question is Arabic) | August 2026, 1 August up to 1 September. "من" is the month, not "one month ago". شنو تكلمنا عنه آخر شهر أغسطس؟ is that same August |
| What did I say in August about the last 3 days? | شنو قلت لك في أغسطس عن آخر ٣ أيام؟ (Gulf); ماذا قلت لك في أغسطس عن آخر ٣ أيام؟ and ماذا ذكرت في شهر أغسطس عن آخر ٣ أيام؟ (MSA) | August 2026, 1 August up to 1 September. The same period as "What did I say in August?" The stretch is the topic, so the month is not a word a memory must hold |
| What did I say in August 2024 about the last 3 days? | شنو قلت لك في أغسطس 2024 عن آخر ٣ أيام؟; شنو قلت لك في أغسطس ٢٠٢٤ عن آخر ٣ أيام؟ | August 2024, the same period as asking about August 2024 on its own |
| What did I tell you in 2025 about the last 3 days? | شنو قلت لك في عام 2025 عن آخر ٣ أيام؟ | All of 2025, 1 January 2025 up to 1 January 2026. The same period as "What did I buy in 2025?" |
| What did I tell you in the last 3 days about my trip in August? | وش قلت لك آخر ثلاث أيام عن رحلتي في أغسطس؟ | The last 3 days, 22 September 2026 through the moment asked. August stays a word to search for. The same when the month comes as the topic: "in the last 3 days from August" and وش قلت لك من أغسطس عن آخر ثلاث أيام؟ |
| What did I tell you in the last 3 days about August 2025? | وش قلت لك آخر ثلاث أيام عن أغسطس 2025؟ | The last 3 days. 2025 stays a search word |
| What did I tell you in the past 10 days about June? | وش قلت لك آخر عشر أيام عن يونيو؟ | The past 10 days. June stays a search word |
| What did I tell you last week about the last 3 days of August? | وش قلت لك الأسبوع الماضي عن آخر ٣ أيام من أغسطس؟ | The same period as "What did I tell you last week?" The August days are the topic |
| What did I say yesterday about the past 10 days of rain? | شنو قلت أمس عن آخر عشر أيام من المطر؟ | Yesterday: 24 September 2026 up to 25 September 2026 |
| What did I tell you in the last 5 days of last week? | وش قلت لك آخر خمس أيام من الأسبوع الماضي؟ | The same period as last week. "Of last week" says when it was said. "Of last month" is the one tie that closes the stretch on that month |

These are not a period: "What did I do with the last 3 emails?", "What did
we do on weekends?", "What did I do in my pastime?", "شنو سويت قبل ثلاث
صفحات؟", "شنو تكلمنا عنه في السنة الدراسية؟", "What did I tell you in the
last few weeks of school?", "وش قلت لك آخر كم أسبوع من المدرسة؟", "كم يوم
فات من آخر مرة قلت لك عن السفر؟", "How many days since I told you about the
trip?". "Few" tied to a noun that is not a time reads no period. A number
still does: "What did I do in the last 3 weeks of school?" is the last 3
weeks, 4 September 2026 through the moment asked, and so is وش قلت آخر
ثلاث أسابيع من المدرسة؟. "قبل كم يوم" in a message is the last few days.
"كم يوم فات" asks how long, and is not a stretch.

What did not help, measured on the benchmark's v6 `dense` category (thirty
Arabic wedding turns, forty near-identical ride logs): reading the
question's background from the whole store (the median, or a higher
quantile) instead of its nearest memories, and weighing a question's rare
words more. The background is one number per question, so it decides which
memories clear the floor, never their order, and these questions were lost
on order; no setting kept every earlier answer. The three Gulf Arabic
paraphrases still missed need a reranker (decision 14 of the memory plan).

When memory has nothing for a question, the **past-chats fallback** searches
earlier chats; a chat must hold more than half of the question's content
words.

**Quality is measured.** `python scripts/memory_bench.py run` scores recall
on a persona's life -- 1,187 chat turns and 69 facts inside unrelated chat
that says each of its turns once (dataset v8),
127 questions (single facts, paraphrases, things only the assistant said,
updates, codes, Arabic, overviews, questions with no answer, and since
dataset v6 one topic of many turns, follow-ups, counts and lists, and
periods) -- and a second set of 51: written after the last tuning, and no threshold is swept
on them. They are a second check, not an unseen test: two decisions were
taken by looking at them (the report below), and they are gated like every
other score (renamed from "held out" on 2026-11-20, benchmark audit R8; a
set nobody has looked at is a sealed one, scored only on request). CI replays the
real model's recorded vectors and holds every score to a ratchet
(`tests/test_memory_benchmark.py`); `lock` raises it. A score can fall only
in a reviewed trade (`lock --trade "why"`: no score falls by more than 0.05,
one rises by 0.05 or more, abstention never falls), written into the
thresholds file with its reason. A hit that holds only by the rounding of
the recorded vectors stops the lock: `lock` replays the questions ten times
with every recorded number moved inside its rounding, and a hit that flips
is refused, naming its margin, unless the question is named on the edge
list (`lock --edge QUESTION "why"`); no threshold moves to save one
question. One question is on it: "what is it that I do for a living?",
whose answer clears the fact floor by 0.0004 (`tests/test_memory_benchmark_edges.py`).
The report for this dataset is under "Benchmark report" below.

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
the index) — not keyword-only. pgvector may answer from its HNSW index,
which is approximate (the local search compares every row). CI asks the
retrieval benchmark's questions on both Postgres paths: the mirror's keyword
search with the local vector index (held within 0.01 hit rate, 0.01 MRR and
0.02 precision of local recall), and pgvector, measured with its own search
and with every row compared, to settle which Kazma should use. A run made by
hand on 2026-10-14 scored hit rate 0.895 against 0.925; the question it lost
stands at the edge of the recorded vectors' rounding (benchmark audit F5),
which is the likelier cause, not the keyword search it was put down to. It reads only memories
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

### Long memories {#long-memories}

A memory of a conversation keeps all of its text: the whole message and the
whole answer (since 2026-11-19). Until then every writer kept the first
4,000 characters of each, 2,000 of a summary and 8,000 of a swarm result, so
a long paste or a long report was remembered by its start only. The one
bound left is a safety ceiling of 2 MiB per field, the most the web chat
accepts in one request; nothing a person sends or a model answers reaches
it, and a text past it keeps its start with a warning in the log.

- **Searching.** Keyword search covers all of a long memory: the full-text
  index holds every word. Meaning search covers its start: a memory's
  meaning vector is made from its first 4,000 characters, as it always was,
  so a question about something deep inside a long memory is found by its
  words, not its meaning. Searching the whole of a long memory by meaning
  (a vector per stretch of it) is a later step.
- **Showing.** The memory block a turn gets shows each memory bounded, by
  rank: the best one up to 1,500 characters, every other one up to 500,
  each cut around the stretch that holds the question's words (read the
  way search reads them, in English and Arabic), so the part that answers
  is the part shown. When that stretch is deep inside a memory, the
  memory's first sentence is shown before it (a memory often names who or
  what it is about first). When the block's budget would leave a memory out, the
  others give way first (down to 250 characters each), then the best (down
  to 500). A follow-up joined to its turn and another member's turn keep
  their own line. When it leaves part out, the memory ends with how many
  characters are not shown and `memory_read("<id>")`, in the memory's
  language. The
  `memory_search` tool and the Memory page's probe show up to 1,500
  characters of one memory the same way.
- **Reading one whole.** `memory_read` (a read tool: no approval) reads one
  memory by its id, 8,000 characters a page, with where the next page
  starts; it reads only memories of the space it runs in, and its text
  reaches the model as data, never as instructions. On the Memory page,
  "The whole memory" under a memory opens it, a part at a time.
- **What was cut comes back.** The maintenance sweep "memory text fill"
  finds memories written before 2026-11-19 whose question or answer is
  exactly 4,000 characters long and fills in the rest from the chat they
  came from, when the chat store still holds it: only when the memory holds
  exactly that chat text's start and its id is that turn's, never a
  forgotten memory or a turn you kept out of memory, never from another
  space's chat. Its vector does not change (it is made from the start).
  Memory health counts what was filled, what was already whole, and what
  cannot be filled (the chat is gone; swarm results and weekly summaries
  have no chat to fill them from).
- **Matching the old rows.** Code that finds a turn's memory by its text
  (turn reconcile, the forget ledger, the weekly summaries' copy check)
  compares the first 4,000 characters, so a memory written before still
  counts as the same turn and is never written a second time.
- **Model inputs stay bounded.** The deep pass reads the first 1,500
  characters of a message and 800 of the answer; a weekly summary reads 400
  and 600 of each turn. With a turn the deep pass is shown up to 30 facts
  Kazma holds (4,000 characters at most, as many in Arabic as in English) so
  that a new one can replace an old one: the facts of the people and things
  the turn names (and the turn before, for "she" and "it"), yours that it
  speaks of by word or by meaning, your home, employer, name and role, and the
  ones you told last.

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
- Neo4j receives dual-writes on mutate, and soft-invalidate / supersede / **graph-clear** delete edges at once (tenant-scoped). Both are best effort.  
- **The copy converges by itself** (since 2026-11-13). The maintenance pass "memory graph sync" runs every 15 minutes: it brings across every fact changed since the last pass, and once a day compares every current fact with every edge. A fact written, superseded or forgotten while Neo4j was down reaches it at the next pass; a forgotten value leaves with its node. An install that had memories before Neo4j was added is filled at the first pass. A Neo4j recreated empty or restored from an older backup is compared in full at once, and so is the copy after a memory restore. A pass makes at most 2,000 writes (removals first, then the newest facts); a larger set takes a few passes.  
- **Sync beliefs → Neo4j** runs the same pass now, with the full comparison.  
- **Only this install's edges change.** Every edge Kazma writes carries the fact's id and this install's id. An edge without a fact id, or written by another install sharing the Neo4j, is never changed or removed.  
- **Neo4j down is waited out:** changes wait in a queue (up to 100,000, then the next pass compares everything instead), Kazma says once that Neo4j does not answer and once that it is back. Memory health shows the copy: in sync, behind (with how much), unreachable, or not configured.
- **Nodes are found by an index** (since 2026-11-13). Each node carries a key, the SHA-256 of its name, under a uniqueness constraint on the tenant and the key; 500 edges written at once over 40,000 nodes take about 0.1 s instead of 8.5 s, and two writers of one new name make one node. An index on the name itself cannot be used: Neo4j refuses a value longer than about 8 KB, and a fact's value can be 16 KB. The pass gives existing nodes their keys (40,000 in about 5.5 s); until every node has one, writes find nodes by name. Memory health says whether the copy is keyed.  

### Operator setup

`kazma setup install` does all of this on Windows and Linux (the Neo4j memory
graph part, in Express and Full since 2026-11-13): it installs the driver,
keeps a generated `KAZMA_NEO4J_PASSWORD` in the install's `.env`, starts Neo4j
on Kazma's container engine (`deploy/docker-compose.neo4j.yml`, project
`kazma-graph`) and points Kazma at it (`KAZMA_NEO4J_URL`,
`KAZMA_NEO4J_DEFAULT=1`). By hand:

1. Run Neo4j: `docker compose -f deploy/docker-compose.neo4j.yml --env-file .env -p kazma-graph up -d kazma-neo4j` (with `KAZMA_NEO4J_PASSWORD` set in `.env`).  
2. Install the driver in Kazma's Python: the `neo4j` extra (`kazma setup install --with graph_neo4j`, or Settings → Packages). Without it the graph stays in SQLite, and Kazma says so once at boot and Settings shows `driver_missing`.  
3. Settings → Memory → Graph store **Neo4j**, URL `bolt://localhost:7687`, password, **Save**.  
4. **Test Neo4j** (masked `***` passwords do not overwrite the vault secret).  
5. **Sync beliefs → Neo4j** fills the copy now; without it, the next maintenance pass does (within 15 minutes).  

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
  the memories holding every word you type, an Arabic word in any usual
  spelling. **Forget** removes one, from
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
full backups (seven), the offsite backups (up to twelve monthly), the backup
copy in a folder you chose (Settings, Backup: the newest copies kept there),
the memory export written every six hours, Kazma's Postgres dumps (three,
which hold the memory mirror's tables when memory has a Postgres copy),
Postgres's archived write-ahead log and base backups and their offsite
copies, Neo4j's own transaction logs when Neo4j holds the memory graph (two
days by default), migration bundles until you delete them, the copy kept
before an embedding rebuild, the copies the operator's repair scripts keep
before they change the database (`memory_state.db.pre_entity_key`,
`memory_state.db.pre_relabel`, `memory_state.db.pre_reopen`, until removed; a
migration never carries them), and space the database has freed but not yet
reused. Restoring a memory backup (the Memory page's Restore) does not bring
a forget back: every forget is journaled in a file of its own and applied
again to the restored copy. A restore of the whole data folder from an older
full backup takes that journal back to its time too. The Forget dialog says
so too.

**A forgotten fact keeps nothing you could read.** Its subject, its name and
its value are emptied together (since 2026-11-21; before then the subject and
name stayed, and a forgotten "Sara was diagnosed with ..." still read
"sara, diagnosed_with"). What stays is an id, when, and the chat and turn it
came from.

**"Forget everything that mentions ..."** reads every place memory keeps
text: conversations, facts (current, ended or archived), weekly summaries,
entities and their groupings, changes waiting for you, work still queued and
Kazma's lessons for itself. It matches in any case and folds Arabic spelling
the way search does (ة and ه, أ and ا, with or without vowel marks). "About
me" is your own text: Kazma tells you when it holds the words and never
edits it. Forgetting a turn or a chat also takes back the lessons it taught.

## Bring your history {#bring-your-history}

Kazma can remember what you talked about with ChatGPT or Claude before you
had it. Ask the service for your data (ChatGPT: **Settings → Data controls →
Export data**; Claude: **Settings → Privacy → Export data**); it emails you a
`.zip`. Then, on the **Memory** page, **Bring your history**: choose the zip
(or the conversations.json file inside it, if the zip is large), **Read it** to
see what it holds, then **Import**. Or in a terminal:

```bash
kazma import ~/Downloads/chatgpt-export.zip
```

Each question and its answer become one memory, dated when you asked it, so
recall finds them by meaning and words, and an old turn is history, never
news. Each conversation is a memory chat of its own; the Memory page lists
the turns, and Forget forgets one like any memory. Only what you and the
assistant wrote is taken: not ChatGPT's tool calls or their output, hidden
instructions, branches you left, or images. Importing the same export again
skips what is there, so an import that was stopped goes on where it ended.
No facts are extracted (that would call the model for every turn); the
weekly summaries cover the imported weeks over the next hours, as for every
turn.

## About me

**Settings → Memory → About me** is a short text (up to 2,000 characters)
that you write: your name, your work, where you are, how you like to be
answered. Kazma reads it at the start of every reply, in every chat. It is
never filled in by Kazma, and what you say later in a conversation wins over
it. Clear the text to remove it. On an install with several users, only an
administrator can change it: Kazma takes it into account for everyone who
uses that tenant.

**Suggested lines.** The Memory page's card "Suggested for About me" offers
lines made from facts you told Kazma yourself -- "I live in Porto.", "أسكن في
الكويت." -- in the language you said them in. Nothing is added to About me
until you press **Add to About me**; **Dismiss** keeps a line from coming
back. Kazma never suggests what it only read into a conversation, what a tool
said, or what someone else said about you, and never a line already in About
me. In a shared space each member is offered lines from their own facts, for
their own About me. Kazma keeps only which fact you answered and how, never
its value.

## Changes to what you told Kazma {#changes-to-what-you-told-kazma}

A fact you told Kazma yourself ("I live in Paris") changes only in ways you
can see and take back. The Memory page's card **Changes to what you told
Kazma** lists them:

- **Changed from what you said.** You later said it plainly ("I live in
  Rotterdam now"): Kazma keeps the new value and shows the change, with
  **Undo**, which brings the earlier value back (every earlier value, when
  it replaced a list such as two employers; one you have said again since
  is already there and is not added twice).
- **Is this a change? Kazma kept the earlier fact.** A message Kazma reads
  as a correction only with its model ("We moved to Rotterdam last month",
  "No, Lisbon", "I'm visiting Dubai next week") does not replace your fact
  on its own: the model may have filed it under the wrong fact. Kazma keeps
  the earlier one and asks: **Yes, change it** writes the new value as
  yours (and the change can still be undone), **No, keep it** leaves
  everything as it was. A message about someone else ("My sister moved to
  Rotterdam"), a denial ("I never lived in Paris") or a question ("Should I
  move to Rotterdam?") asks nothing. The same holds when you ask Kazma to
  remember something and its memory tool would replace one of your facts:
  "I'm visiting Dubai next week, remember that for the itinerary" asks
  before it touches your home.
- **Has this ended? Kazma kept the fact.** You said one of your facts
  stopped being true ("I don't live in Paris anymore", "بطلت أستخدم فيم"),
  and only the model read it. Kazma keeps the fact and asks: **Yes, it
  ended** ends it (shown as **Ended from what you said**, "No longer:
  Paris", with **Undo**), **No, keep it** leaves it. A fact Kazma or its
  model wrote ends at once. Someone else's ending ("My sister doesn't live
  in Paris anymore") or a question asks nothing.
- **Kazma's reading of your message.** The first time only the model reads
  one of your facts (your home, your name, the name you go by, your
  employer, your role), Kazma keeps it as its own reading, not as yours,
  and does not ask: anything you say later replaces it. **This is right**
  makes it yours; **Not me** takes it out, and Kazma never files that value
  again (your own words still can). When a fact like this is recalled into a
  conversation, it starts with "(Kazma's reading)" ("(قراءة Kazma)" for an
  Arabic fact), so the model knows you did not say it; so does any fact a
  tool or the model wrote, and a fact whose source is not recorded (an
  older one, or another install's) starts with "(source unknown)". Only
  your own words go untagged. Each recalled fact is one line, whatever its
  value holds.

Kazma also tells you, once, in its next reply in the chat the change came
from: what it changed, and at most one question a turn, never about the
same fact twice in a day. The rest wait here. It cannot apply a waiting
change itself; you do, by saying it in your own words or with the buttons.
In Telegram, Discord, Slack and WhatsApp, `/memory changes` lists them with
a short id, and `/memory undo`, `/memory confirm` or `/memory dismiss` with
that id decides one. Through the API, an operator's API token may confirm
one too: it can already edit your facts directly. If a reply fails, the note
is not used up; the next reply tells it.

A question you leave unanswered is let go after 30 days. Forgetting the
fact, the conversation or the words a change holds forgets the change too,
and the changes are in the memory export. Nothing here calls a model.

## What Kazma knows about you {#what-kazma-knows-about-you}

The Memory page's **What Kazma knows about you** (`/memory/you`) lists the
facts Kazma holds about you, and only you: in a shared space, your own
entry, never another member's. Each shows:

- **where it came from**: you said it, Kazma's reading of a conversation,
  Kazma noted it with a memory tool, or a tool reported it;
- **since when** it holds, and the start you named for it ("since 2024"),
  if you did; for something you did, the day it happened, and for something
  ahead the day it is planned for;
- **what it was before**, under "Before", each earlier value with when it
  held.

**Correct** puts your words in its place: the fact becomes yours (as if
you had told Kazma), the earlier value stays in its history, and the
change is under [Changes to what you told
Kazma](#changes-to-what-you-told-kazma) with Undo. For a list (the
languages you speak), the entry you correct is ended and the new value
added; the others stay. **No longer true** ends the fact, also with Undo.
Nothing here calls a model.

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

### Months and projects {#months-and-projects}

Above the weeks, Kazma writes two kinds of roll-up from the weekly summaries:

- **A month**: once a month has ended (a day later) and its weeks are
  written, one roll-up of what that month was about, when it has at least
  two weekly summaries. A week belongs to the month its Monday is in.
- **A project**: when weekly summaries of three or more weeks are one topic
  (a training plan, a product you are building), one roll-up of the whole
  story. "One topic" is measured on the summaries' meaning, against your own
  memories (on the retrieval benchmark, eleven weeks of half marathon
  training came out as one project, and each kind of everyday question as
  another).

Each roll-up names the weekly summaries it was written from and the
conversation turns they stand for. Forget a turn, or a weekly summary, and
every roll-up built from it is emptied at once and written again without it
(or dropped when too little is left); forget the roll-up itself and it is
never written again. Writing one is one model call, in the background, at
most six a pass. Recall shows a roll-up labelled with its month or its span
("October 2026", "Project, Sep 2026 to Nov 2026"):

- A project roll-up is ranked like a weekly summary, by what the question is
  about ("How did my half marathon training go?").
- A month's roll-up answers only a question about that month. It holds every
  topic of its month, so as a topic it matched every question a little (on
  the benchmark, "Where do I live?" was shown May's).
- A question that asks only what was said in a period ("What did we talk
  about back in June?", "شنو تكلمنا عنه في يونيو؟") is answered by that
  period's month roll-up. It names no topic, so by meaning nothing stood out
  for it.

The Memory page names each of these from the period it stored, in the
page's language. A week and a month come from that key. A project comes
from the week keys of its first and last part. A project with no week
part is named from the months of the stretch it stored, read at midday.
The page does not read the stored instant in the browser's time zone: a
June written in Kuwait stays June on a browser set to UTC. Reading the
instant there had shown May.

They are listed, and can be forgotten, in the Memory page's summaries list.

## Memory admin UI (`/memory`)

Single operator surface for **topology + entities + beliefs + hygiene**
(`memory.html` + `memory_console.js` + `memory.js` + `memory_api.py`).

Every change made here leaves a trail: a log line and an audit row naming
the action, who made it, the tenant and the ids, never a value. The daily
digest counts the memories forgotten from this page, and `/metrics` counts
the changes requested per action (`kazma_memory_operator_actions_total`).

### Layout

Reworked 2026-11-18 so a person can tell what each part is and does. From
top to bottom:

1. **Status** -- whether memory is on and working, one sentence, with a link
   to Settings -> Memory. When something needs attention, a warning above
   the map opens **Health and maintenance**.
2. **Memory map** -- what Kazma knows as dots (things: people, places,
   projects, ideas) joined by lines (facts). The toolbar finds a dot, zooms
   (**-**, **+**, **Show all**), shows conversations on the map, reloads it
   and exports it (picture, drawing, data, GraphML). A legend under it names
   only the marks the map is using. Two folds below it: **Connect or join
   two things** (fixing the map by hand) and **Filter the map** (by kind of
   thing, kind of fact, or as it was on a day). A map that could not be
   loaded says so; it is never shown as an empty memory.
3. **What Kazma knows** -- the facts (click one for where it came from, show
   it on the map, or remove it) and **Changes to what you told Kazma**.
4. **Conversations Kazma remembers** -- memories and weekly summaries, each
   with **Forget**, the memory search, and the history import. A week, a
   month and a project are named from the period stored, not from the
   browser's time zone ([Months and projects](#months-and-projects)).
5. **Clean up memory** (folded) -- the tabs **Facts**, **Things**,
   **Suggested joins** and **Tidy up**. Joining two things and running a
   tidy-up ask first and name what will happen.
6. **Health and maintenance** (folded) -- memory's parts and checks, the
   background queue, backups (a restore asks first and only takes a backup
   of the memory database).

The map draws flat, in the theme's colours (light and dark): a label under
each dot, the most connected first where two would overlap, Arabic names
right to left; a line's relation is written on it when its dot is chosen.
It moves only while its layout settles or a dot is dragged, and fits itself
in view once settled. On a phone, two fingers pinch to zoom.

### Operator capabilities (2026-08 overhaul)

| Capability | Where | Notes |
|------------|-------|-------|
| **Pagination + real counts** | every list in Clean up memory | "Showing 1–150 of 3,412" + **Load more**. The graph reports `total_nodes`/`truncated` and shows an amber banner when capped — including **connections hidden by slicing**. No more silent 200-row cap. |
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
- UI: Clean up memory → Facts → **Edit** (guided prompts for object → predicate → subject).

### Other entity ops (unchanged contract)

| Action | Route |
|--------|--------|
| List / filter | `GET /api/memory/v2/entities` |
| Merge shells | `POST /api/memory/v2/entities/merge` |
| Link two entities | `POST /api/memory/v2/entities/link` |
| Delete empty shell | `DELETE /api/memory/v2/entities/{id}` (not protected entities). Copies `entity_merges` rows to `entity_merges_archive` first (FK still drops live ledger rows). |
| Purge empty entities | Clean up memory → Tidy up, `POST /api/memory/v2/hygiene/run` with `purge_empty_entities`: removes only shells with no live fact and nothing but their own name, the rule that removes them automatically. Merge redirects stay: they send a merged name to its target, and the Entities list shows them as "merged into". The page's **Empty** count is this list. |
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

## How memory is watched {#how-memory-is-watched}

Every turn's recall is recorded: which memories were shown, whether recall
came back empty, could not search by meaning, or failed, and how long it
took. No text is kept, and a record goes after 30 days. Two things are read
from it: whether the answer used what was shown (a fact's value in the
answer, or a remembered turn sharing words with the answer the question did
not hold), and whether you corrected a shown fact within two days (you
replaced or forgot it). Memory health shows the last week, with what was
added and replaced; the daily digest has a "Memory:" line.

Three problems page once each, and once more when they are over:

| Page | When |
|------|------|
| The model's reading of turns is failing | Three deep passes failed since the last that worked, the first ten minutes back or more |
| Recall cannot search by meaning | Most of the last twenty recalls degraded or failed |
| Recall is slow | The slowest twentieth of the last twenty over 8 seconds |

While the first is open, a deep pass given up on does not page again.

**The quick recall check** (Memory page; it was "Run golden eval") writes
each case's memories among twenty unrelated ones and passes only when the
answer shows and none of the unrelated ones does. Measured with the
embedding model on 2026-11-21 it passed 2 of its 6 cases: recall also showed
three to five unrelated conversations, marked "possibly related". In a store
that small, recall's sense of "what unrelated memories reach" was the
average of everything, so the nearest unrelated conversations always looked
related. A small store's conversations are now judged as a full store would
judge them, and the check passes 6 of 6. A store of only a few memories,
all of them about the question, keeps them all: the rule waits until a
store holds more than a handful. The benchmark page has a small-store run
of its own.

**Search past a size.** Below 20,000 memories, search by meaning compares
every memory exactly. Past it, a short code of each vector picks the
nearest candidates first (eight times what was asked, at least 400; a
memory with no code yet is always one), and every candidate is compared
exactly; the retrieval benchmark answers every question the same with this
on. The first narrowed search after a start, and every 50th, also compares
every memory, and the full answer is used when they differ (memory health,
`narrowing`).

**The memory sweeps.** Each memory sweep of the 15-minute maintenance pass
has a time budget, and one over it is said in the log. A sweep whose work
appears only when memories change waits while nothing it reads has changed
since a pass found nothing to do; forgetting, turn reconcile and the copies
in Postgres and Neo4j keep cursors of their own.

## Scale (trigger only)

| Need | Tracking |
|------|----------|
| Full Postgres-primary recall | [#76](https://github.com/Mubder/kazma/issues/76) — adapter shipped; enable only after `reconcile_memory_mirror.py` is clean |
| Multi-region + conflicts | [#77](https://github.com/Mubder/kazma/issues/77) |
| Hosted embed-only fleet | [#78](https://github.com/Mubder/kazma/issues/78) |

Postgres dual-mirror and sparse ILIKE assist already exist as **optional** foundations. Tombstones propagate, and the 15-minute mirror sync pushes what changed (and, once a day, whatever the mirror is missing or holds stale; a row only the mirror holds is counted, never deleted).

### Against a public benchmark: LongMemEval (2026-11-21)

[LongMemEval](https://github.com/xiaowu0162/LongMemEval) (MIT) asks
questions of a user's own chat history: about 48 conversations, the one or
few holding the answer among unrelated ones. Kazma is measured on a fixed,
stratified sample of 100 of its 500 questions
(`tests/fixtures/longmemeval/sample.json`; `scripts/longmemeval_bench.py`).
From 22 Nov the questions are split in two
(`tests/fixtures/longmemeval/split.json`): 200 to choose settings on (the
sample and 100 more), and 300 kept for reporting: no setting is chosen on
them, and every look at those 300 is logged with its reason
(`tests/fixtures/longmemeval/report_looks.json`).
Each history is written into a private memory the product's way, one memory
per round (the user's message and the reply), dated, and the question is
asked through the real `recall()`. No facts are extracted (a model call per
round). The data is downloaded into a folder outside the repository and
never committed; the result files hold question ids and scores only.

**Finding it** (no model call): the round holding the answer is among the
memories shown for 91.5 % of the answerable questions, a memory from the
right conversation for 97.9 %, every answering conversation for 83.0 %;
precision 0.84; recall took 26 ms (median). None of the six questions with
no answer came back empty.

**Answering from it** (deepseek-flash as reader and judge, LongMemEval's own
prompts; the share the judge called correct):

| | Kazma's recall | Read whole | History cut to fit | Oracle |
|---|---|---|---|---|
| All questions | 0.47 | 0.77 | 0.72 | 0.94 |
| Tokens sent a question | 1,017 | 2,214 | 96,356 | 6,306 |

"Kazma's recall" is the block as a chat turn's model is shown it. It shows
each memory with 200 characters of the question and 300 of the reply, and
points the model at `memory_read` for the rest; a one-shot reader cannot call it.
For 44 of the 51 answerable questions it missed, the round holding the
answer was shown, cut. "Read whole" gives the same recalled rounds whole,
as `memory_read` would: above the history cut to fit, at a fortieth of its
tokens. That history was cut at 440,000 characters from its oldest
conversations, which left out sessions in every dev question and an answer
session in 33 of these 100: it is not a full-context score. Since 22 Nov
the harness sends the whole history uncut (the reader's window holds the
longest, 115,818 tokens); the full-context score is measured on dev with
the plan's next runs. Asked through Kazma's own chat turn, which calls
`memory_read` itself (`--conditions kazma_turn`, built 22 Nov), is the next
measurement.

**The block's bounds, measured** (`scripts/recall_block_bounds.py`, the
same reader and judge; built into the product since, as the last row: see
"Showing" above):

| How each recalled conversation is shown | Answered | Block (characters, median) |
|---|---|---|
| Before: 200 of the question, 300 of the reply | 0.48 | 3,926 |
| Twice those bounds | 0.58 | 4,392 |
| Each as wide as the block's budget allows | 0.59 | 4,979 |
| Every one whole, no budget | 0.59 | 5,221 |
| The best one at up to 1,500 characters around the question's words, the others at 500 | 0.74 | 5,344 |

Showing more of every conversation stops helping at 0.59, and a budget of
4,000 tokens instead of 1,500 adds nothing. What helps is giving the best
conversation room, around the words the question asks about, and keeping
the others short (500 characters, 250 when the 1,500-token budget needs it;
no line is ever left out). Questions that count across conversations
("how many") gain most when their list lines are handled the same way.
On the benchmark's own store nothing changes: its conversations are all
shorter than the bounds before.

**In English and in Arabic**: 40 of the sample's questions and the 84
conversations holding their answers, in one memory, in English and
translated into Arabic (the user's messages in Gulf Arabic, the replies in
Modern Standard Arabic, by deepseek-flash; kept with the data). The round
holding the answer is shown for 85.0 % in English and 87.5 % in Arabic, a
memory from the right conversation for 97.5 % in both.

Spend (deepseek-flash, 2026-11-21): the end-to-end run 800 calls,
10,684,738 prompt and 36,646 completion tokens; the translation 392 calls,
338,099 prompt and 420,616 completion tokens.

**On the dev split, beside plain retrieval at the same size** (200
questions, 22 Nov, no model): what Kazma's recall shows, against the rounds
nearest the question by bge-m3 alone (the same vectors, with none of
recall's background, word coverage or gap), read whole, at Kazma's own size
and at ten. Tokens are counted as the product measures count the block.

| Method | Memories | Tokens (median) | The answer's round shown | Every answering conversation |
|---|---|---|---|---|
| Kazma's recall | 4.5 | 1,080 | 90.2 % | 82.0 % |
| bge-m3, within Kazma's median block | 1.8 | 797 | 70.1 % | 42.3 % |
| bge-m3, as many rounds as Kazma | 4.5 | 2,310 | 89.7 % | 77.3 % |
| bge-m3, ten rounds | 10 | 5,148 | 94.8 % | 91.8 % |

At the same size Kazma finds more; ten whole rounds find more than Kazma,
at almost five times its tokens (BM25 finds less than bge-m3 at every size;
the benchmarks page has both). The 300 report questions, looked at once for
these starting numbers, say the same: Kazma shows the answer's round for
92.4 % and every answering conversation for 82.6 %; bge-m3's rounds within
its block 70.3 % and 38.4 %, as many rounds 88.0 % and 81.2 %, ten rounds
97.5 % and 90.6 %. On the questions with no answer (6 on
dev, 24 on report) Kazma still shows memories: it does not yet say
nothing. For the questions
whose answer is a word or a phrase, the answer is in the rounds shown for
45 %, and in the block after its bounds for 30 %.

The end-to-end harness now reads at the reasoning Kazma's chat runs (low),
judges every dev answer twice (deepseek-flash and deepseek-v4-pro), answers
every question twice to count the answers that flip on their own, and asks
again any call the API refused (the account's balance ran out during the
first dev runs; a refused judge call had been kept as a "no").

### What memory costs an ordinary turn (2026-11-22)

Before an outside score counts, a memory change is held to what it does to
an ordinary turn (`scripts/memory_product_measures.py`, kept in
`tests/fixtures/memory_bench/product_measures.json` and on the benchmarks
page). On the retrieval benchmark's store, with its recorded vectors:

| | English (137 questions) | Arabic (46) |
|---|---|---|
| Answer shown (questions with one) | 95.7 % | 90.5 % |
| Precision of what is shown | 47.7 % | 42.8 % |
| Questions with no answer that show nothing | 0 of 22 | 0 of 4 |
| Tokens the recall block adds (median / 95th / largest) | 711 / 1,035 / 1,341 | 590 / 823 / 1,091 |
| The same, the store four times as large | 736 / 1,211 / 1,343 | 598 / 848 / 1,085 |

Recall took 96 ms (median) on the store and 244 ms at four times its size;
the embedding model 76 ms a question; Kazma's own share of the time to the
first token 0.115 s. The block's tokens are held to the record by a test: a
change that makes the block bigger records its new numbers in the same
commit.

### Memory in real use, in the browser (2026-11-22)

`scripts/memory_real_use.py` runs a private Kazma from the checkout (its
own data folder, nothing of an install on the machine) with the real model
and embedding model, and uses it through the browser, by the sidebar, in
English and in Arabic: it tells two facts, asks for one in a new chat, asks
for it in other words, changes it, forgets the other on the Memory page,
and asks about something never said. A model judges each answer with
LongMemEval's prompts. It is run by hand at every stage of the memory work;
its results are in `docs/audits/evidence/memory_real_use/`.

The first run passed every step in both languages, and showed three things
to improve: a fact told once is often kept twice (the conversation, and a
note the model writes itself), so forgetting it took two Forgets; telling
Kazma about a move made the model ask to retire the old fact, which needs
an approval; and an Arabic answer read "my sister" as Kazma's own.

### Benchmark report (dataset v8, the unrelated chat without repeats, 2026-11-21)

Until v7 the "unrelated chat" around the persona was 300 conversations
drawn with repeats from 74 hand-written turns: 1,060 turns, each said about
fourteen times (the benchmark audit's F10). Recall
judges a memory against the question's background, the meaning ranks 4 to
15, and on that pool those ranks were copies; its thresholds were chosen
there. Dataset v8 says each turn once: the 74 hand-written turns and 977
turns a model wrote for the pool (`scripts/memory_pool_generate.py`,
deepseek-flash: questions and requests about the world in English and Gulf
Arabic, none saying anything about the person asking, 346 calls, 61,020
prompt and 244,633 completion tokens), 300 conversations, 1,051 turns. Its
weekly summaries follow, and so do the month and project roll-ups the
planner makes from them, written by the product's own roll-up call (18
calls). v7's pool stays in the dataset file for the versions before it, so
v5 to v7 replay exactly.

| | v7 (pool of copies) | v8 (each turn once) | v8 at four times the pool |
|---|---|---|---|
| Hit rate | 0.918 | 0.901 | 0.897 |
| MRR | 0.754 | 0.749 | 0.736 |
| Precision | 0.581 | 0.487 | 0.438 |
| Questions with no answer shown nothing | 0.278 | 0.000 | 0.056 |
| Second set hit rate, MRR, precision | 0.863, 0.772, 0.621 | 0.863, 0.757, 0.404 | 0.842, 0.731, 0.360 |

Answers are found as before; what changed is what comes with them. On a
pool of different conversations, the unrelated turns that share a
question's topic clear the floor ("Why do cats purr?" for "Do I have any
cats?", the week's money questions for "Which bank do I use?"), where v7's
copies had filled the background ranks and kept them out. No unanswerable
tuning question comes back empty. The thresholds were chosen on the copies,
and were chosen again on v8 (below): nothing moved. The
scale run (`benchmark.run_scale_benchmark`, 4,774 memories, the scale
pool's 900 conversations said once as well) shows the scores hold as the
unrelated chat grows four times: hit rate and MRR within 0.02, precision
0.05 lower. Its scores have a ratchet of their own (`scale` in the lock).
q601 stays on the edge list (its fact clears the floor by 0.0004, as on
v7).

**The thresholds, chosen again on v8.** Every floor, gap and word weight
of conversations, facts and summaries was swept on half the questions
(92, divided by category and script), with the other half kept back to
judge the choice, on the four stores the benchmark builds: its own, four
times its unrelated chat, a small store per question, and a shared space
of several people. The rule, in the script with how it came to be: no
answer lost on any store, then more unanswerable questions empty, then
precision and MRR. A
one-question difference never decides, and a value moves only where the
values next to it lose nothing either. Nothing moved. Raising the
conversations' floor loses answers at four times the store; narrowing
their gap loses them in small stores. On the benchmark's own store alone,
a higher floor and a narrower gap would have raised precision from 0.49
to 0.61 (0.44 to 0.56 on the judging half) without losing an answer there,
and lost five on the other stores (four on the judging half). The lower
precision of v8 is not the thresholds' to buy back. The judging half is
not a sealed set: every question was read once while finding where the
first choice lost answers; the final choice moved nothing
(`scripts/memory_threshold_sweep.py`; every configuration measured is in
`docs/audits/evidence/memory_thresholds_v8/`).

On 22 Nov the sweep ran once more the way the memory plan sets it: chosen
on the benchmark's first set alone (131 questions), with the second set
(52) kept apart to judge. Nothing moved, and no answer was lost or gained
on either set. Only a run logged with its reason in
`docs/audits/evidence/memory_thresholds_v8/choices.json` may set the
thresholds; this is the first.

### Benchmark report (dataset v7, part G, 2026-11-21)

Dataset v7 adds the roll-ups the planner makes from the benchmark's weekly
summaries (four months and nine projects; their texts are written in the
shape the model writes, and their vectors recorded with bge-m3; every v6
vector is unchanged byte for byte) and the `rollup` category: a project's
whole story and a month's talk, in English and Arabic (four tuning
questions, one in the second set).

| | v6 lock (127 questions) | v7 (131 questions) |
|---|---|---|
| Hit rate | 0.901 | 0.918 |
| MRR | 0.764 | 0.754 |
| Precision | 0.586 | 0.581 |
| `rollup` hit rate, MRR, precision | -- | 1.00, 0.47, 0.44 |
| Second set hit rate, MRR, precision | 0.853, 0.782, 0.623 | 0.863, 0.772, 0.621 |

Every v6 question scores the same on the v7 store as on v6, except three
that improved (a list question now found, a catch-me-up question's answer
ranked higher, another's precision up). The lower overall MRR and precision
are the new category's: a summary is shown after the conversations and
facts, so a roll-up that answers ranks below them. Negative controls: on the
store without roll-ups the two month questions miss; with months ranked as
topics, May's roll-up is shown for "Where do I live?" and months for four
more questions they do not answer.

### Benchmark report (dataset v6, part F, 2026-11-21)

Dataset v6 added 76 chat turns (thirty Arabic wedding turns, forty
near-identical ride logs, three stored follow-ups) and four categories:
`dense` (one topic of many turns), `follow_up`, `list` (a count or a list)
and `temporal` (a period, what was true before). Every memory of v5 kept
its id and text. Recall before part F against recall after it, on the same
v6 dataset and the same recorded vectors (local path, tuning questions):

| | Before part F | After part F |
|---|---|---|
| Questions | 127 | 127 |
| Hit rate | 0.815 | 0.901 |
| MRR | 0.698 | 0.764 |
| Precision | 0.541 | 0.586 |
| `dense` hit rate (14) | 0.71 | 0.79 |
| `follow_up` hit rate (9) | 0.22 | 1.00 |
| `list` hit rate (6) | 0.33 | 0.67 |
| `temporal` hit rate (9), right order | 0.78, 0.50 | 0.89, 0.83 |
| Abstention | 0.28 | 0.28 |

Every category of v5 scores the same before and after part F on v6 (its
lower precision against the v5 lock comes from the new memories, measured
with the code before part F too). Two scores fell inside part F's own
categories: `list` precision (0.668 to 0.634: a list shows more lines) and
the second set's `follow_up` MRR (0.67 to 0.50, while its hit rate rose
from 0.67 to 1.0: the message before a follow-up is searched with it, and
its own memory can rank first). Still missed: three Gulf Arabic paraphrases
in `dense`, the race result asked as "my running last week" (bge-m3 puts
the two far apart), and two `list` questions (seven of nine long runs; a
book named in Arabic asked for in English).

### Benchmark report (dataset v5, 2026-10-14)

Recall on the retrieval benchmark, the tuning questions and the second
set, on both paths (vectors replayed from bge-m3). The second set was
called "held out" when this was written; the two decisions below were
taken by looking at it, which is why it is the second set now and a
sealed set is the held-out one (benchmark audit R8). The Postgres-primary
columns are a run made by hand with pgvector's own (approximate) search;
since 2026-11-20 CI's pgvector job measures it, approximate and exact, and
the lock's floors are locked from a named run (benchmark audit R7):

| | Local (SQLite) tuning | Local second set | Postgres-primary tuning | Postgres-primary second set |
|---|---|---|---|---|
| Questions | 89 | 40 | 89 | 40 |
| Hit rate (every piece of the answer shown) | 0.925 | 0.891 | 0.895 | 0.891 |
| MRR (how high the first answer ranks) | 0.844 | 0.845 | 0.830 | 0.845 |
| Precision (share of what is shown that answers) | 0.644 | 0.646 | 0.621 | 0.630 |
| Abstention (nothing shown when nothing answers) | 0.28 (5 of 18) | 0.00 (0 of 8) | 0.28 | 0.00 |
| Questions across languages (second set, `mixed`) | | hit 1.0, MRR 0.90 | | hit 1.0, MRR 0.90 |

- **The ranking carries over:** the second set's hit rate, MRR and precision are
  within a few points of the tuning set's, on both paths, and a question in
  Arabic about something said in English (or the reverse, or both in one
  sentence) is answered as well as any other.
- **Abstention does not.** Recall shows something for most questions nothing
  answers: 13 of 18 tuning questions and every one of the 8 in the second set, which
  were written to come close to a real memory (another person's dentist, a
  car's colour). The target is 0.6 on both sets (`TARGETS` in
  `scripts/memory_bench.py`); it is a goal recorded beside the floors, not a
  gate, and abstention is the one score no trade may lower.
- **The thresholds cannot buy it** (swept 2026-10-14: the memory floor 0.04
  to 0.10 against the fact floor 0.06 to 0.12, 16 settings). Every setting
  that shows nothing for a second-set question loses answers elsewhere past the
  trade rule's 0.05 -- Arabic hit rate 0.25, "noise" 0.33, overviews 0.5 --
  and the one setting inside the rule raises tuning abstention only (0.28 to
  0.39) and leaves the second set at 0: a choice fitted to the tuning
  questions. What these questions share with their near-miss memories is the
  person or thing (Noor, Maya, the car); telling "Noor's dentist" from
  "Noor's school" needs a signal of what is asked about, not a higher floor.
  Most of what is shown for them is marked "(possibly related)" to the
  model.
- **A signal of what is asked about, measured, not adopted** (2026-10-15):
  a question naming a word no memory holds ("colour", "salary",
  "cholesterol", "anniversary") asks about something unremembered, so only
  strong matches are shown for it. It reaches the target -- abstention 0.28
  to 0.67 on the tuning set, 0 to 0.50 (4 of 8) in the second set -- but the true
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
  known, second set 1 of 8) or Arabic answers (at half); the one setting inside
  the rule (two thirds known) leaves the second set's abstention at 0.

Extraction (`python scripts/memory_bench.py extraction`): 30 single turns
in English, Arabic and both, scored on what a reader would keep from each.
A fact counts as **found** when its whole value is kept about the right
person, whatever the pass called it, unless the name turns it around
("dislikes", "former"); **same name** is the share of those kept under a
name on the list (a fact under two names is two facts to Kazma, so the name
matters too); **right** is the share of kept facts that hold a value the
turn gives, about the right person, and are not on the turn's list of what
must not be kept ("my sister is a pilot" never makes the user a pilot). The
name is scored apart, so "right" does not say the name fits. Until 2026-11 a
fact counted only under a listed name, so a fact kept under another name was
counted "not found" and "wrong" at once.

| Heuristic pass | Found | Same name | Right | Turns with nothing to keep that kept nothing |
|---|---|---|---|---|
| English (12 turns) | 0.46 | 1.0 | 1.0 | 3 of 3 |
| Arabic (10 turns) | 0.64 | 1.0 | 1.0 | 2 of 2 |
| Mixed (8 turns) | 0.60 | 1.0 | 1.0 | 1 of 1 |

The heuristic pass is the quick first look at every turn. Until 2026-10-14 it
had English patterns only and kept nothing from Arabic (0.00, mixed 0.20);
it now reads the same kinds of fact in Arabic and in Arabic mixed with
English -- name, where you live (or moved to), where you work, a favourite,
a preference, an allergy (`memory/arabic_facts.py`). Its rules come from the
grammar, so it keeps nothing from a negation ("ما أحب"), an instruction to
Kazma ("استخدم البحث"), a question, someone else ("أخوي ساكن في دبي", "أختي
انتقلت إلى روتردام", and "عندي أخت اسمها نورة وانتقلت إلى روتردام", where it
is Noura who moved) or a field of work ("أشتغل في المبيعات", like "I work in
sales"). Once a sentence names someone else, a statement with no "أنا" after it
is read as about them: the quick pass would rather miss a fact than file
another person's under you. A new sentence starts fresh, except for someone
you introduced by name ("عندي أخت اسمها نورة. انتقلت إلى روتردام" is still
Noura), and what describes you is still you ("أنا مدير مشاريع وساكن في دبي",
"عندي بنت وساكن في الكويت", "قبل سنتين انتقلت إلى دبي"). It reads only your
own words: not an attached document, a forwarded message or a pasted mail, a
pasted chat (the lines of a WhatsApp export, the header a Telegram, Discord
or Slack copy puts over a message and the message under it, "Dana: My name is
Dana", "dana_92: ...", "the customer: ..."), a mail you quote ("On ... Dana
wrote:" and what follows it to the next blank line), a quote, what someone
else said or thinks ("my sister said: ...", to the end of that sentence; "He
said, I live in Lyon"; "Ali told me I work at Initech"; "Ali thinks I ...";
"He was like, I ..."; "According to Ali, ..."; "أختي تقول أنا ساكنة في
دبي"), a text of someone else's ("Dana's bio:", "Her message was:"), an
example or a part to play ("For example: ...", "Pretend you are me and say:
..."), a text you asked Kazma to work on or about ("Translate to French: My
name is Pierre" does not make your name Pierre, nor does "Is this sentence
correct: I work at Initech"), or a question ("How do I say my name is Pierre
in French?"). A greeting before a colon is yours ("Good morning: I live in
Oslo"). Inside a request to remember, a quote is yours ("remember this: "I
prefer dark mode""), unless it quotes someone ("remember that Dana wrote "I
live in Oslo"") or you name the text as pasted ("Remember this email: ...":
kept, at the trust of something you pasted, not of your own words). "Don't
remember this" asks nothing. "Correct: my name is Ahmad" is your own words
and counts, and so is what you add after the text you asked about
("Summarize https://x.y and my name is Sam", "Can you review my text?" then
"Also my name is Sam" on a line of its own). Only the first 2,000 characters
of a message are read this way, up to their last blank line. The end of a
longer message (its last paragraph) is read only to ask you: a correction
there that the model's deep pass reads becomes a question, never your word.
The deep pass reads the first 1,500 characters of a message and 800 of the
answer (the memory itself keeps all of both: "Long memories" above). A fact only
the model's deep pass reads is never your own word by itself: whether the
model's name fits your words ("I'm visiting Dubai next week" read as your
home) only you can say. It stays the model's inference, and a fact you state
yourself always wins over it. When it disagrees with a fact you gave and is
in your own words ("We moved to Rotterdam last month" beside your Paris),
Kazma asks you (below); when you never gave that fact, it shows as Kazma's
reading on the Memory page and asks nothing. Another name for the same fact
("home city", "resides in") counts as the same fact.
"My sister moved to Rotterdam" is never asked about, whoever the model files
it under. What it misses --
relatives, jobs, dates -- is the model's deep pass to find. That pass is scored from the configured model's recorded
replies (`scripts/memory_extraction_record.py --out <fixture>`, run with an
install's own Python, one model call per turn); a turn with no recorded reply
is an error, never a guess, and a recording of another prompt is refused.
Recorded 2026-10-15 with DeepSeek's `deepseek-flash`, one asking a turn:

| Model's pass | Found | Same name | Right | Turns with nothing to keep that kept nothing |
|---|---|---|---|---|
| English (12 turns) | 1.0 | 0.77 | 1.0 | 3 of 3 |
| Arabic (10 turns) | 1.0 | 0.91 | 1.0 | 2 of 2 |
| Mixed (8 turns) | 1.0 | 0.70 | 0.92 | 1 of 1 |

The model kept every fact asked for. What varies is the name it gives a
fact, and the one fact it should not have kept: "Call me Abu Faisal يا
Kazma" also gave "the assistant's name is Kazma". One asking a turn is a
small sample: asked again, the same model answered differently on three of
six turns, so these numbers can move by a few facts with nothing changed.

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
It fails when a size goes over the limits it names. The table is that
bench, measured on 14 October 2026. It is not the later bench below, which
has not published times.

### Scale and speed {#scale-and-speed}

`scripts/memory_scale_speed.py` times recall with no paid model call. It
fills a private store at 1, 10 and 100 times the episode count in
`tests/fixtures/memory_bench/live_lengths.json` (the count and the length
percentiles only; the text is synthetic, and the bench opens no install).
Facts are not assumed one per episode: every 25th turn is a short
preference, English and Arabic alternating. Recall is the product's, local
only, on a fixed set of English and Arabic questions. Each size embeds its
own questions. One untimed recall runs first and is reported as the warm-up.
`size` estimates the fill from a sample and does not time recall. `run`
refuses 100 times when that estimate is two hours or more. Timed runs have
not been published: they need a quiet machine.

```bash
python scripts/memory_scale_speed.py size
python scripts/memory_scale_speed.py run --factors 1,10 --out REPORT.json
```

`scripts/longmemeval_bench.py size` counts LongMemEval-M before a run:
rounds, tokens and the distinct texts to embed, one question at a time,
and estimates that embedding from a sample. It does not retrieve and it
does not answer. The data file stays outside the repository. Pass it with
`--data`. There is no default path.

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
