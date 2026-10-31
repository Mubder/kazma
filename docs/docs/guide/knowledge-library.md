---
id: knowledge-library
title: Knowledge Library
sidebar_label: Knowledge Library
description: Ingest whole documentation sites once, then have the agent reason over the corpus with cited sources.
---

# Knowledge Library

> This page is published at [kazma.ai/docs/knowledge-base-and-rag/](https://kazma.ai/docs/knowledge-base-and-rag/) ([Arabic](https://kazma.ai/ar/docs/knowledge-base-and-rag/)).

A **Knowledge Library** is a named, managed corpus of ingested documentation — for example, the Meta WhatsApp Cloud API. You point Kazma at a doc root once; it crawls the tree, chunks it by heading, embeds it, and indexes it. The agent then **reasons over** the corpus and **cites sources** when you ask questions.

**Operator tour (re-index, inject, Settings toggles):** [Recent features](./recent-features#3-knowledge-library-hardening).

This is RAG (Retrieval-Augmented Generation) over a curated, updatable corpus — not live scraping, and not fine-tuning. Library text reaches a turn as fenced, untrusted data; it is never written into chat memory.

> **Arabic brand:** product name is **Kazma** / **كاظمه** (or **كاظمة**). Never **كازما**.

## When to use it

| You want | What to do |
|----------|------------|
| The agent to know a specific API/docset deeply | Ingest once, then ask questions — it cites the source URL + section |
| Authoritative answers with sources | Each `knowledge_search` hit carries its source URL and section |
| Update when docs change | Refresh the library; unchanged pages are skipped |
| Access from several places | The same library works from the Web page, Telegram, Discord and Slack (`/kb`), and the agent's tools |

The Knowledge Library works on a **single-operator install**. Libraries, chunks and crawl jobs are not separated per user, so an install with platform users (or `KAZMA_MULTI_USER`) refuses every Knowledge surface — the Web API answers `409 kb_single_operator`, the page shows a banner, `/kb` and the agent's tools reply with the reason — until per-user libraries are built.

If you instead want the agent to *search the live web* per question, see [Web research](./web-research.md) — that's a different feature (ephemeral results, no indexed corpus).

### Document Intelligence → library bridge {#document-intelligence--library-bridge}

You can also **publish a processed local document** (upload / `document_import`)
into a Knowledge library after it reaches `ready`:

| Path | How |
|------|-----|
| Web | Documents page → pick a library → **Add to library**; the page lists the libraries a document is in, each with a remove button (`POST /api/documents/{document_id}/unindex`) |
| API | `POST /api/documents/{document_id}/index` with library id |
| Agent | tool `document_index(document_id, library_id)` |
| Search | `document_search` / `POST /api/documents/search` — hits are **fenced** as untrusted data |

Auto-index after parse is **off by default** (explicit index only). Crawl-based
KB ingestion (this page) and document-platform indexing share library ids but
are different intake pipelines. Deleting a document takes it out of every
library that holds it first; a library it cannot leave stops the delete, so a
retry can finish it. The document store records what it indexed where: after a
restore, each worker start re-publishes every version the store records as
indexed but its library no longer holds, and reports a missing library or
manifest instead of inventing one (`scripts/document_pg_backup.py index` runs
the same check by hand). A document the store records as **deleted** that a
restored library still holds is taken out of the library at that check, with a
WARNING and the ops alert `documents.index_drift`. Deleting a library tells the
Documents platform too: every document it holds is unindexed from it first. See
[Document Intelligence](./document-intelligence.md).

## How ingestion works

When you give Kazma a seed URL (e.g. `https://developers.facebook.com/docs/whatsapp/cloud-api`):

1. **Admission.** The seed must be an http or https URL (no `javascript:`, `data:` or other scheme is ever stored), the library store must be below its quota, and no other crawl of the same library may be running. A crawl seed on a private network is refused before anything is created: the address check acquisition makes of every page (each address the host resolves to) is made of the seed at admission, and a private, loopback or link-local address is refused as `security_blocked`, a name that resolves to nothing as `connect_error` (worth retrying). A refused crawl creates nothing: a library is created only once its first page was fetched (single page) or its crawl was admitted.
2. **Discovery (sitemap-first).** Kazma reads `robots.txt` for `Sitemap:` directives, then tries `/sitemap.xml`, `/sitemap_index.xml`, `/docs/sitemap.xml`. The resulting URLs are filtered to the seed's scope, so the crawl stays inside the doc subtree. Fallback: a breadth-first link walk over the rendered page.
3. **Fetch.** Each page goes through the shared acquisition ladder. By default acquisition is **local only**: HTTP first, then local Chromium for pages that need JavaScript. Hosted readers (Firecrawl, Jina) are used only when the operator allows remote acquisition (see [Web research](./web-research.md)). Tabbed pages get a full-DOM pass that reads hidden panels too. A page that redirects is stored once, under the address it was served from.
4. **Chunk.** Markdown is split on `#`/`##`/`###`/`####` headings with a section breadcrumb (`"Messages > Send Text Message"`). **Fenced code blocks are atomic** — never split. A chunk's id names its position on the page, so a page that repeats a section keeps every copy.
5. **Publish.** A page is published in **one transaction**: its old chunks and keyword-index rows go and its new ones arrive together, so a search sees the old page or the new one, never a mix. A page that cannot be written is counted as failed and the crawl ends **partial**, never "completed".
6. **Embed.** After the page is committed, its chunks are embedded (local `BAAI/bge-m3` by default, or any OpenAI-compatible `/embeddings`) into the library's own vector collection. A chunk the vector store missed is embedded by the maintenance pass.

Discovery + fetch caps: `KAZMA_KB_MAX_PAGES` (default 200, hard cap 1000), `KAZMA_KB_MAX_DEPTH` (default 10), `KAZMA_KB_DELAY_MS` (default 300), `KAZMA_KB_SCOPE_MODE` (`tree` | `prefix` | `domain` | `exact`, default **`tree`**).

### Re-index and refresh

| Case | Behavior |
|------|----------|
| Page **unchanged** (same chunks in the same order) | Skipped: nothing written, counted as unchanged |
| Page **changed** or shrank | Replaced in one transaction (old or new, never a mix) |
| URL **missing** from a later discovery | **Kept.** A sparse, capped or failed discovery never deletes indexed pages |
| A page that **fails** | Counted as failed; the crawl ends partial and says so |

Agent tool `knowledge_ingest_site` is capped lower (default 5, at most 15 pages) than Web crawls — use `/knowledge` or `/kb crawl` for large trees.

### Crawl jobs

Every site crawl and refresh is a job in the **crawl journal** (`web_acquire.crawl_jobs`), the one source of crawl state: the Web page, `/kb status` and `kazma acquisition status` all read it.

- **One row per job.** A page update writes only its own job, so a large finished crawl never makes the next one fail with "journal is busy". Each job is bounded (4 MiB) and the journal as a whole (16 MiB, at most 24 jobs, at most 4 running). Finished jobs are evicted oldest first, by count **and** by bytes, so a new crawl always has room; a running job is never evicted. `kazma acquisition purge-finished` removes this install's finished jobs by hand.
- **One crawl per library.** A second crawl of a library that is being crawled is refused (`library_busy`); deleting a library while it is crawled is refused too (`crawl_owns_library`).
- **Owners are proven, never guessed.** A running job names its install, host, process and a lease the crawl renews (5 minutes). After a restart, a job whose process is gone on this host is marked interrupted; a job owned by another host is taken over only once its lease has lapsed, and the old owner's next write is refused. Jobs are this install's own (`crawl_journal_scope: this_install` in `kazma acquisition status`): the Knowledge Library is single-install.
- **Resume is explicit.** An interrupted job resumes from the page, through `POST /api/kb/jobs/{job_id}/resume` (the one resume path). Pages whose outcome was uncertain are fetched again; completed pages are not.

### Recall + inject (one hybrid stack)

Chat does **not** merge the Knowledge Library into the memory schema. Product path:

1. **Hybrid candidates** — per-library vector search + FTS5 BM25 (`KnowledgeIndex.search_all_sync`); the question is embedded once, and a chunk found by its words alone is measured by its stored vector too.
2. **Relevance** — A search shows only what is about the question. Every candidate is judged on evidence, the rule chat memory's recall uses: its meaning similarity above the question's background (what unrelated chunks reach for it) plus the share of the question's words it holds, with Arabic folded. Below a floor nothing is shown, and a hit far behind the best is dropped; with no vector to judge by, a chunk must hold at least half of the question's words. Hits come best first by that evidence, and a weak one is shown as "possibly related". One exception: when a question names something only one chunk holds (a service, a code, a version) and that chunk holds every word of the question, it comes first, with the best kept hit's evidence. Meaning search is weak at exact names: the Knowledge soak asked "Which port does zqfmbfjvdv listen on?" of a library holding five look-alike lines, and the answer came sixth. This changes the order of what is shown, never what is shown. The tool, `/kb search`, the page's test search and auto-inject all use it; a search scoped to one document does not. See [Retrieval quality](#retrieval-quality) for what it measures.
3. **Federated search** — `POST /api/memory/v2/federated-search` returns memory hits and Knowledge hits side by side. Each store keeps its own ranking and its own budget (`limit_memory`, `limit_kb`); `include_memory` / `include_knowledge` decide whether a store is searched at all.
4. **One injection per turn** — the supervisor builds the turn's Knowledge block, once, at the turn's first model call (`agent/graph_supervisor.py`). The chat transports (Web, voice, chat apps) no longer inject their own copy.
5. **Fence** — what the pages wrote goes through `format_untrusted_block(..., source="knowledge")`: a page that writes a fence tag has it redacted. Kazma's own lines -- how many chunks, which library, and the instruction to cite it -- follow the fence, outside it.
6. **The turn's record** — the turn's **Memory used** row lists exactly the chunks that were injected.
7. **Kill switches** — `KAZMA_KB_AUTO_INJECT=0`, and `memory.v2.merge_knowledge_into_chat` (Settings → Memory).

**Auto-inject** only includes **non-archived** libraries with the toggle on.

**Smart search (optional):** set `KAZMA_KB_SMART_SEARCH=1` (or ConfigStore
`knowledge.smart_search=true`) to also retrieve from **active libraries with
chunks** when the user message looks technical (API/docs/how-to), even if
per-library auto-inject is off.

## Use it

### From the Web UI

Open **`/knowledge`** → "Add a library" → enter an ID (e.g. `shipx_whatsapp_api`), a name, and the seed URL → choose:

- **Ingest single page** — one URL, now. The reply says whether the page was stored, unchanged or refused, and why.
- **Crawl whole doc tree** — a background job; watch live progress (discovered / fetched / ingested / failed). A crawl with failed pages ends **Finished with failures**, never "Done".

Per library you can: **Test search** (try a query in-page), **Refresh** (re-crawl from seed), **Browse** chunks, **Archive**, **Delete**. A refusal is shown in the page's language from its stable code (`knowledge.err.<code>`).

### From chat (Telegram / Discord / Slack)

The gateway answers `/kb` itself, with no model call. Commands that change a library are for the gateway's admins only.

| Command | Admin only | Does |
|---------|------------|------|
| `/kb` | no | List libraries + help |
| `/kb add <id> <url>` | yes | Ingest ONE page now; replies with what was stored |
| `/kb crawl <id> <url> [N]` | yes | Crawl the doc tree in the background; replies with the journal's outcome when it ends |
| `/kb refresh <id>` | yes | Re-crawl a library from its seed URL |
| `/kb search <id> <query>` | no | Direct search (no model call) |
| `/kb status <id>` | no | The library's latest crawl, from the crawl journal |
| `/kb delete <id>` | yes | Delete a library and everything derived from it |

Example:

```
/kb crawl shipx_whatsapp_api https://developers.facebook.com/docs/whatsapp/cloud-api
/kb status shipx_whatsapp_api
```

Then just ask your question normally. The agent decides when to consult the library via the `knowledge_search` tool. The tool reads at most 20 hits a call.

## Auto-inject (the "just knows" behaviour)

By default the agent calls `knowledge_search` when *it* decides the question needs library context. If you'd rather have relevant chunks **folded into every turn automatically**, flip the **auto-inject** toggle on a library (Web page checkbox, or `PATCH /api/kb/libraries/{id}`).

When auto-inject is on, the chunks about the user's latest message (at most top-k, and none when nothing in the libraries is about it) are retrieved and added to the turn — **fenced as untrusted data** (`<kazma:data source="knowledge" untrusted="true">`), so a malicious doc page can't smuggle instructions. The supervisor is the one place that builds this block, once per turn; a turn rebuilt from history drops an earlier turn's block first.

**Kill switch:** `KAZMA_KB_AUTO_INJECT=0` disables the whole subsystem at runtime (checked live, per turn). Per-library opt-in is still required with the kill switch on, so behaviour is strictly opt-in.

Tunable: `KAZMA_KB_AUTO_INJECT_TOP_K` (default 3, max 10) controls how many chunks per turn.

## Citation footer

Every answer derived from Knowledge Library data carries a visible footer so you can tell where the information came from:

> 📚 This data is from Knowledge "shipx_whatsapp_api".

This applies to both the `knowledge_search` tool path (explicit) and the auto-inject path (implicit). The footer names the specific library (or libraries, if the search spanned multiple). It is a prompt-level directive: the model is instructed to append it verbatim. In the tool's output Kazma's own lines (the hit number, score, library and the footer) sit outside the fence and what the page wrote (its URL, title, section and text) inside it. The auto-inject block is the same: the pages' text inside the fence, the footer and the instruction to cite it after the fence (inside, the fence would tell the model to obey none of it).

## Archive and delete

Libraries can be **archived** — hidden without deleting their chunks.

- An archived library leaves the Active list, auto-inject and every search across libraries. An explicit search **by its id** (the tool's `library`, `/kb search`, the page's test search) still reads it.
- **Restore** brings it back; archive is reversible.

**Delete** is permanent and goes through one path for every surface (`kazma-core/kazma_core/stores/knowledge_lifecycle.py`): it is refused while a crawl of the library runs, it unindexes every document the library holds through the Documents platform (and is refused while that platform cannot be told), then removes the library's vectors, its chunks and keyword-index rows (in bounded batches) and the fetched pages kept with it (`<data dir>/knowledge_provenance/<library>`). Its words then leave the keyword index's files too (see Storage), and its vector folder leaves the disk (see Meaning vectors). The vector catalog never held its text; text an older build stored there leaves the file at the next maintenance pass.

## Storage

The Knowledge Library has its own database file, **`knowledge.db`** in the data directory, declared in the store registry: backups and `kazma migrate` carry it. An install that kept its libraries in `settings.db` has them moved to `knowledge.db` on the first open, through the store's own schema, and the old tables are dropped; a migration bundle written before the move is staged the same way on import.

- **Quota.** The store's own pages are limited to 1 GiB. At the quota a new ingest or crawl is refused before anything is fetched (`kb_storage_full`, HTTP 507); reads keep working. The quota counts pages in use, so deleting a library makes room for new ingest at once; the maintenance pass gives the freed space back to the file system once at least 64 MiB, or a fifth of the file, is free (`PRAGMA incremental_vacuum`; never `VACUUM`, which renumbers the rows the keyword index is keyed by).
- **Keyword index.** The FTS5 index is keyed by row id, so writing or deleting a chunk costs the same whatever the index size. The maintenance pass checks that it matches the chunks and rebuilds it when it does not, with a WARNING. The Arabic folding rules carry a version number, stored with the index (a test fails when the rules change and the number does not); a new number makes the next open rebuild the index. A broken index is reported in each library's health (`keyword_index_ok`) and at WARNING; a query the index cannot parse returns no hits quietly. FTS5 records a deleted row as a delete marker and keeps the row's words in its older segments until a merge reaches them: unsearchable, but on disk. After a library delete the index is therefore merged into one segment (`KnowledgeStore.purge_search_index`, in steps of 1,000 pages, each its own transaction with the store lock released between them), which leaves none of the library's words in the file. A merge alone is not enough: FTS5 drops a deleted row's words only when the merge writes the index's oldest segment, which it judges by the segment's place, and when every segment sits on one level below the top it merges them one level up and keeps the words. So the purge checks the shape the merge left -- one segment on the last level -- and otherwise **rebuilds the index from its own rows** in one transaction. Each batch of a delete records the purge it owes in the same transaction, so **a process stopped before the purge ends leaves it to the next open of the store or the next maintenance pass**, which finish it. Memory's Forget and the vector catalog's compaction use the same check. A replaced page or document version keeps its old words in the segments until FTS5's own merges reach them; they are never searchable.
- **Fetched pages.** The text of each fetched page is kept under `<data dir>/knowledge_provenance/<library>/` (at most 5,000 files a library) and removed with the library.
- **Libraries.** An install holds at most 500 libraries, archived ones included. A new library past that is refused before anything is fetched (`kb_library_limit`, HTTP 409, in English and Arabic on the page); delete one to add another. Each library is one vector collection, and the ceiling is what Kazma sizes Chroma's index pool for (see Meaning vectors). A process that cannot open enough files gets a lower ceiling: a loaded index holds 4 files open, so at the default of 512 it is 92.

## Meaning vectors

**Every chunk gets its meaning vector.** A chunk is embedded once its page is committed. A 15-minute maintenance pass (`KnowledgeIndex.backfill_vectors`, up to 5 minutes each, last in the maintenance cycle) makes each library's vectors match its chunks: it embeds any chunk without a vector of the active embedder — the shortest first, across all libraries — and removes the vectors of retired chunks.

- **Each vector names the embedder that made it**: its class, model, size and a digest of its endpoint, read off the running embedder, never from configuration. A model changed in Settings takes effect at the next restart; a remote model that fell back to the local one makes vectors tagged as the local one. Searches compare only vectors of the active embedder, and the pass re-embeds the others; a collection whose vectors have another size is rebuilt first, and until then a query waits quietly (one INFO line).
- **One library cannot stop the others.** A library whose collection fails is recorded as failed and the rest are repaired; the pass then raises so the maintenance runner logs it at WARNING.
- **An outage is seen.** The first pass that finds the vector store unavailable logs a WARNING, and so does the recovery; three bad passes in a row raise the ops alert `knowledge.meaning_search` (every 6 hours while it lasts). `/metrics` carries `kazma_kb_vector_*` and `kazma_kb_keyword_index_ok`, readiness carries a `knowledge_vectors` line (never critical: a library still answers by keyword), and the daily digest lists a degraded pass under "Needs attention".
- **Health is what was measured, on this vector store.** Each library on the Knowledge page shows "Meaning search: N of M" — chunks whose vector the active embedder made — with a state: not checked yet, rebuilding, unavailable or failed. Listing the libraries opens no collection and loads no model; the numbers are the last pass's. The record names the vector store it was measured on (an id kept in `vector_memory/kazma_vector_store_id`): knowledge.db is backed up and the vectors are not, so a database restored onto a machine without them shows "not checked yet" until the first pass has measured the vectors that are there — never the coverage of vectors it lost.
- **One index per store.** The Knowledge Library and the Documents platform share the store's one index, and a collection another handle dropped is re-opened by name on the next call.

Vectors are compared by angle (cosine), each made unit length before it is stored or searched. A vector is made from the first 1,500 characters of its chunk; keyword search reads the whole chunk. Only the vector is stored in the vector store, never the chunk's text. They live in `KAZMA_VECTOR_PATH` if set, else in `vector_memory/` beside the Knowledge database. Deleting a library drops its collection. Backups leave the folder out — a copy of a live Chroma database can be torn — and the maintenance pass rebuilds it after a restore or a migration. Chroma's telemetry is off.

What Kazma does around Chroma's local store (chromadb 1.5.9, measured by `scripts/knowledge_soak.py`):

- **A dropped collection's vectors leave the disk.** Chroma's `delete_collection` removes the catalog rows and keeps the collection's folder (about 40 MB per 10,000 chunks at 1,024 dimensions). Kazma removes every folder no collection in `chroma.sqlite3` owns: when a client starts, before any collection opens; right after a drop, when nothing holds the folder; and at the end of each maintenance pass. On Windows a folder whose index this process loaded stays open until the process lets go of it, so the pass then closes the vector client (after the calls in flight, holding new ones for that moment), removes the folders and lets every library re-open on its next call; the process's exit does the same. `/metrics` carries `kazma_kb_vector_orphan_folders` (folders another process still holds).
- **No index is evicted.** Chroma keeps each loaded index in a pool split over 64 shards, sized by default from the open-file limit (102 entries on Windows: one or two a shard), and writes an index to disk only every 1,000 records. Two libraries in one full shard evict each other. An evicted index of fewer than 1,000 records was never written, so every read of it fails until a restart ("Nothing found on disk"); an evicted index of 1,000 or more reloads from its last write without the records added since, and searches miss them with no error until the library's next write. Kazma starts Chroma with its own pool: each shard holds every library there may be (the 500-library ceiling, plus a few spares), so no shard can overflow, whichever shards the libraries land in. It raises the open-file limit first (Windows: the C runtime's stream limit to 8,192; Linux: 10,240 when the hard limit allows), since every loaded index keeps its 4 files open. A read that still finds an index lost makes Chroma rebuild it from its own write log, then asks again (a WARNING, and `kazma_kb_vector_index_reloads_total`).
- **A library's log is replayed on one thread.** The first read of a library after Chroma starts (at every boot, and whenever the maintenance pass reopens the vector client) applies the library's write log to the index Chroma loads from disk. Chroma does that on its own worker threads, and that parallel insert has a race that crashes the whole process (an access violation inside the index, always at one address). The Knowledge soak hit it three times. On 2026-10-21 `scripts/chroma_replay_race.py` measured 6 crashes in about 28,200 client starts with Chroma's own threads, and none in 28,000 with one thread; replaying a crashed store again never crashed, so it is a race, not bad data. Kazma sets `RAYON_NUM_THREADS=1` before Chroma starts (a value you set yourself is kept, with a warning in the log). The price, measured at 1,024 dimensions: 20,000 vectors insert in 60 s instead of 8 s, and the first read of a library after a boot replays up to 999 pending records in about 6 s instead of 0.7 s. The real embedding model makes about 18 chunks a second, so ingest speed is still set by the model. Run the drill again after a chromadb upgrade.
- **No text is stored beside a vector.** A vector is stored with the tag of the embedder that made it and the ids of its library and document, and nothing else: the chunk itself, its title, heading and address stay in `knowledge.db`, which every hit is read from. Builds before 2026-10-15 also stored a 2,000-character preview of the chunk and its title, heading and address, which nothing read, and SQLite keeps a deleted row's bytes in the file's free space: a deleted library's words stayed readable in `chroma.sqlite3` (9,429 copies of a dropped 3,000-row collection's words in one probe). The maintenance pass rebuilds such a collection from the vectors it holds, without the text (nothing is embedded again: 12 s for 10,000 chunks).
- **Deleted text leaves the vector catalog.** A deletion of rows that held text (a library delete, a replaced page, a rebuilt collection) marks the catalog (`vector_memory/kazma_compaction_due`), and the end of the next maintenance pass rewrites it: Chroma's full-text index is merged, then the file is rebuilt from its live rows (`VACUUM`, as Chroma's own `chroma utils vacuum` does). The pass holds new vector calls only while it runs and keeps the client open. At 10,000 chunks the catalog went from 41.0 MB to 3.2 MB in 0.3 s; a catalog with no text takes 0.13 s. `/metrics` carries `kazma_kb_vector_compaction_pending`.
- **A search asks no metadata filter when it needs none.** A collection holds one library, so a search filters on the embedder's tag only while vectors of another embedder are still there (after a model switch, until the pass has re-made them), checked once per collection and process. Chroma's metadata filter costs more than the search: at 10,000 vectors a query took 40 ms with the library and embedder filter and 4 ms without (p95 123 ms and 5 ms); at 100,000, 353 ms and 6 ms (p95 442 ms and 10 ms). The filter changed no result: 40 of 40 planted answers at 10,000 either way, 32 of 40 at 100,000 either way (with the search breadth of 256 every library had then: the breadth now grows with the library, two points down).
- **The index is built for recall.** New collections are made with 48 neighbours a vector, a construction breadth of 400 and a search breadth of 256 (Chroma's defaults are 16, 100 and 100). On the soak corpus at 10,000 chunks the defaults returned 31-39 of 40 planted answers while an exact search ranked every one first; these settings returned 40 of 40 in English, Arabic and mixed text in 11 of 12 measurements, and 39 in the other (measured before 2026-10-21, when Chroma still built an index on parallel threads, so two builds differed a little: see "A library's log is replayed on one thread" above). 256 is a small library's search breadth; a larger library is searched wider (next point). For a collection made before, the maintenance pass rebuilds its graph from the vectors it holds — nothing is embedded again.
- **The search widens as a library grows.** The search breadth is how many candidates the index keeps while it walks its graph toward a question. At a fixed 256, meaning search found fewer planted answers the larger the library — 239 of 240 at 10,000 chunks, 218 at 50,000, 210 at 100,000, 203 at 200,000 — while an exact search ranked every one first; the least breadth that found them all grew with the library, to about one candidate for every 12 chunks (measurements below). Each library's collection is searched with a search breadth of 256 below 3,072 vectors, 384 from 3,072, 512 from 4,608, 768 from 6,144, 1,024 from 9,216, 1,536 from 12,288, 2,048 from 18,432, 3,072 from 24,576, 4,096 from 36,864, 6,144 from 49,152, 8,192 from 73,728, 12,288 from 98,304, 16,384 from 147,456, 24,576 from 196,608, 32,768 from 294,912, 49,152 from 393,216: one candidate for every 12 vectors, rounded up to the next of these breadths. The size is read from Chroma's own catalog and the breadth set when the collection is opened, before anything reads it: Chroma applies a breadth when it loads a collection's index, any read loads it (a count too), and a loaded index keeps its breadth while the vector client lives. A library that grows past a step while its index is loaded gets the new breadth at the next maintenance pass, which then has the indexes loaded again (it closes the vector client between calls, as for dropped folders; every library loads its index at its next search). A library keeps its breadth until it shrinks a fifth below its step, so one that hovers at a step does not change it on every pass. A rebuilt collection is made with its size's breadth.

### Meaning search at scale

Measured with `scripts/knowledge_soak.py breadth` on 2026-10-15 (chromadb 1.5.9; a 24-thread Windows workstation that was running other soaks at the same time, so the search times are high and compare only within a row). One library per size and language, its vectors built through the product's vector store from the scale corpus (hash-embedded chunks of 1,024 dimensions, Kazma's graph of 48 neighbours and construction breadth 400) with 40 planted questions in English, Arabic and mixed text; two corpora (seeds 20261015 and 7), so 240 questions a size. An exact search ranks every planted answer first. Found: the answer is among the 20 candidates a library search's meaning layer asks for. Each breadth is set before the index loads, in a fresh process, on the same graph.

| Chunks | Found at 256 | Least breadth that found all (hardest corpus and language) | Kazma's breadth | Found at Kazma's | Search p50 at 256 | Search p50 at Kazma's | Index load at Kazma's |
|---|---|---|---|---|---|---|---|
| 10,000 | 239 of 240 | 512 | 1,024 | 240 of 240 | 3.3-3.8 ms | 4.5-5.3 ms | 7-9 ms |
| 50,000 | 218 of 240 | 4,096 | 6,144 | 240 of 240 | 7.5-11.7 ms | 23.5-28.4 ms | 32-37 ms |
| 100,000 | 210 of 240 | 6,144 | 12,288 | 240 of 240 | 17.5-20.9 ms | 51.8-59.5 ms | 62-77 ms |
| 200,000 | 203 of 240 | 16,384 | 24,576 | 240 of 240 | 29.4-33.9 ms | 99.9-113.5 ms | 124-145 ms |

What the numbers say:

- **A fixed breadth loses answers as the library grows**, and the hardest answers were Arabic: at every size the Arabic corpus needed the widest search to find all of its answers (its filler vocabulary is the smallest, 115 words against 166 in English). The easiest corpus and language found everything at 512 or less at every size.
- **One candidate for every 12 vectors found every answer at every size measured**; Kazma's breadth is one for every 12, rounded up to its next step (at most 1.5 times it): it found every answer, in every corpus and language, at every size.
- **The cost is search time on large libraries only.** At 10,000 chunks a search takes about 1.3 ms more; at 50,000 2.5 to 3 times as long, at 100,000 about 3 times, at 200,000 about 3.4 times (the p50 columns, measured back to back on the same graphs). A quieter run of the same 100,000-chunk search at 256 took 6 ms (the filter measurement above), a third of the time here. Loading an index again after a step costs its library one slower search: 7-9 ms at 10,000 chunks, 124-145 ms at 200,000, from a warm disk cache.
- Not measured: above 200,000 chunks (the store's quota allows about 480,000), and real embedding models' graphs, which are smoother than hashed words'.

## Retrieval quality

Retrieval is measured on a versioned benchmark (`kazma-core/kazma_core/stores/knowledge_benchmark.py`, built by `scripts/knowledge_bench.py build`): four documentation libraries, 76 sections, published into a private store through the same path an ingest takes.

- A messaging API, in English.
- An object store, in English, whose topics sit beside the API's (keys, rate limits, retries, signed callbacks): the near misses.
- An invoicing product's help centre, in Arabic.
- An operations runbook written the way a Gulf team writes one: Arabic sentences around English terms and commands.

There are 72 tuning questions and 16 held out (never used to choose a threshold), in English, Arabic (Modern Standard and Gulf), both mixed, and across languages (a question in one language about a page in the other). 14 of the tuning questions and 4 held out ask about something no library holds. Every question goes through the tool's search (5 hits) and through auto-inject (3 chunks), with meaning search on (bge-m3) and off.

### Benchmark report (dataset v1, 2026-10-15)

With meaning search:

| | Tool search | Auto-inject | Tool search, held out | Auto-inject, held out |
|---|---|---|---|---|
| Answers found (every piece shown) | 55 of 58 (0.95) | 55 of 58 (0.95) | 11 of 12 (0.92) | 11 of 12 (0.92) |
| MRR (how high the first answer ranks) | 0.94 | 0.94 | 0.92 | 0.92 |
| Precision (share of what is shown that answers) | 0.71 | 0.72 | 0.79 | 0.79 |
| Nothing shown when nothing answers | 7 of 14 (0.50) | 7 of 14 (0.50) | 1 of 4 | 1 of 4 |

Before the relevance rule, on the same corpus, the tool found 51 of 58 answers (MRR 0.80, precision 0.19) and auto-inject 47 of 58 (MRR 0.79, precision 0.30). Both showed something for every one of the 14 questions nothing answers.

Without meaning search (keyword only), both paths find 31 of 58 answers (MRR 0.53, precision 0.47) and leave 11 of the 14 empty; held out, 6 of 12 and 4 of 4. With no rule, the fusion found 46 and left none empty.

What the numbers mean:

- **The rule buys abstention for little.** Half of the questions nothing answers now get nothing, on both paths. Three answers of 58 are lost: a paraphrase, a Gulf-Arabic wording and a cross-language question, each barely above what unrelated chunks reach.
- **What it cannot refuse is a neighbour.** The questions still answered with something ask about what sits next to a library's content: encrypting Orbit objects with your own keys, beside Orbit's key pairs; a plan's price, beside its rate limit. A higher floor loses answers before it refuses those (swept from 0.04 to 0.12).
- **The answer comes first.** The fusion gave every library's best chunk the same score, so another library's near miss tied with the answer. Ranked on evidence, every near miss named in the benchmark ranks below its answer, and 5 of the 6 questions across languages are answered on both paths (the tool found 2 before, auto-inject 1).
- **Words alone cannot tell a paraphrase from a neighbour.** Without meaning search the rule asks for half the question's words, as chat memory's does. What the words cannot place is not guessed.

How the scores are held:

- CI replays the vectors bge-m3 returned for exactly these texts (`tests/fixtures/knowledge_bench/vectors.npz`), served by an exact in-process client. The real chromadb (1.5.9) and the live model give the same scores (`python scripts/knowledge_bench.py run --chroma`, `run --real`).
- The scores are a ratchet stamped with the dataset version (`tests/fixtures/knowledge_bench/thresholds.json`). `tests/test_knowledge_benchmark.py` fails on a drop, and on a rise nobody locked in (`python scripts/knowledge_bench.py lock`). A new dataset version is locked with `lock --rebaseline`, which is refused on the same version.

Not measured: the corpus is small (76 chunks) and written for the purpose, and scale is KB-Q01 (ingest throughput, search latency and repair cost at 10,000 chunks and more).

## The Web API

`/api/kb/*` (`kazma-ui/kazma_ui/kb_api.py`) has 18 routes. Every refusal is a JSON body `{"ok": false, "code": ..., "transient": ..., "error": ...}` with a stable `code`: an acquisition code (`invalid_url`, `robots_disallowed`, `resource_limit`, ...) or one of the Knowledge codes (`kb_single_operator`, `invalid_request`, `request_too_large`, `not_found`, `library_exists`, `url_refused`, `kb_storage_full`, `library_busy`, `crawl_owns_library`, `documents_unavailable`, `page_not_ingested`, `no_seed`, `resume_refused`, `internal_error`). Expected refusals are logged once at INFO or WARNING, never as an ERROR with a traceback. Request bodies are read under 64 KiB and validated: `top_k` 1-20, `query` up to 2,000 characters, name and description up to 500, seed up to 2,048 and http(s) only, a library id slugged to at most 48 characters. A single-page ingest answers `ok` only when the page was stored.

## What a crawl did, page by page

Every page a crawl reaches ends one of four ways:

- **Stored** (or **Unchanged**, when the library already holds the same text).
- **Kept out** by a rule: robots.txt asks crawlers to stay out, the page is outside the crawl's scope, behind a sign-in, no longer on the site (404/410), or larger than Kazma reads (a page is stored whole, up to a million characters; a part is never stored).
- **Failed**: the site had an error, refused the page, the connection failed, the address is on a private network, a bot check stood in the way, or the page had no readable text.

A crawl is **Completed** (green) when nothing failed, **Finished with failures** (yellow) when a page failed or is uncertain, **Stored nothing** (yellow) when every page was kept out, and **Failed** (red) when the crawl itself could not run. The page cap, a link walk stopped at its depth and a very large sitemap read in part are notes on a completed crawl, never failures. The job card lists the pages that were not stored, failures first, each with what to do.

A dropped connection, a server's "try later" (408, 425, 429, 5xx) or a DNS answer that came back private while the line was down is tried again inside the crawl, after 5 and then 30 seconds, at most 20 times a crawl. A crawl a restart cut off resumes once on its own at the next start (within a day); one you stopped waits for **Resume**.

robots.txt is read as RFC 9309 says: a 4xx answer means no rules apply, and a redirect is followed to another host (http://python.org/robots.txt moves to https://www.python.org/robots.txt). KazmaBot names a contact link in its user agent.

### Preview and scope

**Preview** runs the crawl's own discovery (sitemaps, llms.txt, then following links) without storing anything, and says how many pages **This section**, **Whole site** and **Just this page** hold, where the list came from, and a few of each. Following links counts only the scope it walks; the others say "known once crawled". The scope you choose is the library's: its refreshes keep it, and a resumed crawl keeps the scope it started with. **Just this page** follows the page's own redirects within its site. A version segment of the address (the `3` in `docs.python.org/3/tutorial/`) is not a section.

### Measured: the recorded corpus and the daily check

Twenty real sites were crawled once and every response they gave was recorded (`tests/fixtures/crawl_corpus/`). The tests replay them through the whole crawl -- admission, robots.txt with its stored claims, the sitemap and page parsers, retries, chunking, publication and the journal -- with the settings store's vault on, as production has it, and hold each page's outcome to `tests/fixtures/crawl_corpus/expected.json`. Of their 57 pages, every one is stored or kept out by a stated rule; the target is 99% (`crawl_jobs.PAGE_TARGET`). The Dashboard's **Knowledge crawls** card shows the same share over your retained crawls, with the reasons pages were not stored, and `/metrics` carries it (`kazma_kb_crawl_pages`, `kazma_kb_crawl_page_reasons`, `kazma_kb_crawl_page_handled_ratio`).

Once a day Kazma crawls one known page (RFC 2606 by default; **Settings → System → Web acquisition → Daily crawl check**) the same way, into a private store you never see, and alerts you the same day if the page was not stored or does not say what it should. **Check now** runs it at once.

## Crawl progress and completion alerts

The notification bell retains a completion alert linking to the saved Knowledge
crawl. Partial, failed, cancelled and interrupted jobs have distinct alerts.
No observed progress produces a warning; meaningful progress clears it.
**Settings → System → Web acquisition** controls these alerts and thresholds.
Monitoring reads saved local evidence without provider calls or automatic
retries. Reopening the page restores retained job status; explicitly resume an
interrupted job after reviewing uncertain effects.
See [Progress and result alerts](web-research#progress-and-result-alerts).

## Architecture notes (for contributors)

The shared fetch result distinguishes coded failures, observed login screens,
partial content and successful extracts. Content beginning `Error:` is not
treated as a failed request. Partial shared-ladder content is refused before
indexing. The shared knowledge profile preserves hidden panels, code and tables;
bounded Chromium interaction captures each supported lazy tab while it exists.
Link discovery uses that same scoped typed acquisition. Redirect destinations
are checked before dispatch, including browser HTTP redirect hops. Hosted
discovery is refused when the provider cannot enforce crawl scope. Query values
retain page identity. Native deny-proxy controls are not OS network isolation.
Discovery and the page fetch use different extraction profiles, so a page can
be fetched twice in one crawl.

The page shows actual acquisition attempts, bounded remote request reservations
and cached provider balances. Firecrawl balance refresh is explicit and metered,
with five-minute freshness and account scope. Zero means an observed zero;
unavailable/stale balances and per-request charges are not converted to zero.
Request allowances are not financial spending caps. Origin scheduling persists
through the shared settings store. See the
[implementation report](https://github.com/Mubder/kazma/blob/main/docs/audits/WEB_ACQUISITION_IMPLEMENTATION_2026-10-10.md)
for measured qualification, retained failed attempts and platform limits.

| Layer | File | Purpose |
|-------|------|---------|
| Store | `kazma-core/kazma_core/stores/knowledge.py` | `knowledge.db`: libraries, chunks, the FTS5 index, vector status. Source of truth; one publication path per page. |
| Chunker | `kazma-core/kazma_core/stores/knowledge_chunker.py` | Pure-stdlib heading- and code-aware splitter; positional chunk ids. |
| Index | `kazma-core/kazma_core/stores/knowledge_index.py` | Per-library vectors + FTS5, the relevance rule (evidence), the repair pass, health, the auto-inject block. |
| Benchmark | `kazma-core/kazma_core/stores/knowledge_benchmark.py` | The retrieval benchmark: a private store, both paths, scored ([Retrieval quality](#retrieval-quality)). |
| Ingest | `kazma-core/kazma_core/stores/knowledge_ingest.py` | Admission, discovery, fetch, publication, fetched pages. |
| Lifecycle | `kazma-core/kazma_core/stores/knowledge_lifecycle.py` | Library delete, the single-operator rule. |
| Crawl journal | `kazma-core/kazma_core/web_acquire/crawl_jobs.py` | Crawl jobs: one row per job, owners, leases, eviction. |
| Tools | `kazma-core/kazma_core/agent/tool_builtins/knowledge.py` | `knowledge_search` (fenced), list, create, ingest page / site. |
| Gateway | `kazma-gateway/kazma_gateway/agent_handler/commands.py` `_try_kb_command` | `/kb` on all chat platforms. |
| Web API | `kazma-ui/kazma_ui/kb_api.py` | `/api/kb/*` router. |
| Web page | `kazma-ui/kazma_ui/templates/knowledge_base.html` + `static/js/kb.js` | `/knowledge` page. Every stored URL becomes a link only through `safeHref` (http and https). |

There is no Knowledge panel in the TUI.

**Why a separate store (not chat memory)?** The Knowledge Library keeps its own database and its own vector collections (`kazma_kb_<library_id>`), apart from chat memory (`memory_state.db`). Without that separation, crawled text would be recalled as if the user had said it, and library metadata filtering does not fit memory's bi-temporal facts. The Knowledge Library reuses the shared `VectorStore` class (`memory/vector_store_global.py`) and the shared embedder, in its own collections.

## Optional dependencies

Indexed retrieval needs the `rag` extra (`pip install -e ".[rag]"` → `chromadb`, `sentence-transformers`, `sqlite-vec`); JS/tabbed-page fetch needs the `web` extra (`pip install -e ".[web]"` → `playwright`, then `playwright install chromium`). Without them the system degrades: no meaning search (FTS5 only, and the page says so), no JS-page rendering.

## Limits & honest caveats

- **Acquisition defaults to local-only.** Install the `web` extra and Chromium for JavaScript pages. Some bot walls defeat both HTTP and local Chromium; those pages must remain visible failures. Hosted Firecrawl/Jina require `KAZMA_ACQUISITION_MODE=local_first_with_remote_fallback` and their names in `KAZMA_ACQUISITION_REMOTE_PROVIDERS`, plus credentials where required. Stored keys and legacy Jina flags alone cannot authorize hosted fetching. See [web research](./web-research) for the policy and current accounting limitations.
- **Refreshes preserve retained pages.** Missing a URL in discovery, including sparse/capped/failed discovery, does not delete its indexed content. An ingest where every page fails ends in error and keeps the reasons.
- **Robots checks default on.** KB discovery and ingest use bounded raw KazmaBot policies before content, redirects and browser assets. Missing 404/410 differs from failed or malformed checks, which refuse crawling. Explicit `KAZMA_CRAWL_RESPECT_ROBOTS=0` is recorded in acquisition evidence. Hosted readers cannot bypass enforcement; cross-origin control-file redirects are conservatively refused. See [web research](./web-research#robots-policy) for limits and remaining qualification.
- **Chromium binary is separate from the Python package.** `pip install -e ".[web]"` installs the playwright Python bindings; you still need `playwright install chromium` to download the browser binary itself. The job error names this explicitly: *"Chromium binary not installed (run: playwright install chromium)"*.
- **Linux Chromium needs delegated cgroup v2 controls.** The driver and browser share an aggregate memory/CPU/task budget. Missing memory, pids, CPU or group-kill controls cause a visible unavailable error; configure service delegation or `KAZMA_BROWSER_CGROUP_ROOT`. See [web research](./web-research) for limits, watchdog cleanup and remaining qualification.
- A small fraction of heavily-obfuscated SPA doc sites can resist all of the above. Pages that fail are reported in the job so you can add them manually.
- Local embeddings (`BAAI/bge-m3`) are free but slower on CPU; remote (e.g. NVIDIA NIM `nv-embed-v1`) is faster/better but costs money. See `memory.embedding:` in `kazma.yaml` or the **Settings → Embedder** page.
- Crawl is bounded by `KAZMA_KB_MAX_PAGES` (hard cap 1000) to prevent runaway.
- **Library IDs are slugged automatically** to keep them collection-safe (`"Meta WhatsApp Docs"` → `meta_whatsapp_docs`, at most 48 characters).
- **Not measured yet:** throughput, search latency and repair cost at 100,000 chunks and more are open work (`docs/plans/KNOWLEDGE_INDUSTRIAL_HARDENING_GOAL.md`, KB-Q01); meaning search's recall and time in one library are measured to 200,000 chunks (see [Meaning search at scale](#meaning-search-at-scale)). Retrieval quality is measured on a small benchmark: see [Retrieval quality](#retrieval-quality).
