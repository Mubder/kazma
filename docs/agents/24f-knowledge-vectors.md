<!-- AGENTS.md, piece 24f-knowledge-vectors. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**F. Knowledge Library meaning search** (`memory/vector_store_global.py`,
`stores/knowledge_index.py`). Off from 2026-07-31 to 2026-09-26: the client
module was deleted and the import's `except` said "chromadb not installed".
- **One Chroma client per directory** for the process (`_shared_client`),
  telemetry off (Chroma's default posts usage events to PostHog). A failed
  start names its real cause and is retried after 5 minutes, not on every
  search.
- **Collections compare by angle** (`hnsw:space: cosine`) **and every vector
  is made unit length** before it is stored or searched. Chroma's default is
  squared L2, which every earlier collection has; with unit vectors it ranks
  the same, and `query` reports the true cosine for either (`_similarity`).
  It used to report `1 - L2` as a cosine. No embedding function: every write
  and search passes its own vectors, made from at most 1,500 characters
  (`_EMBED_MAX_CHARS`; keyword search reads the whole chunk). Measured on the
  live CPU: 64 chunks of median length embed in 3.4 s, the 32 longest took
  113 s at a 4,000-character cap.
- **The "knowledge vector repair" sweep** (15 min, up to 5 min a pass,
  LAST in the cycle so the memory sweeps never wait on it;
  `KnowledgeIndex.backfill_vectors`) makes each library's vectors match its
  active chunks both ways: embeds the missing, **shortest first across all
  libraries** (most of every library is searchable early, and a batch of
  like lengths wastes nothing on padding), and removes those of retired
  chunks (they took search slots and joined to nothing). Ingest still embeds
  on write; the sweep covers whatever the store missed.
- **`delete_library` opens the collection before dropping it** -- only a
  cached one was dropped, so a library deleted after a restart kept its
  vectors.
- **Vectors live at `KAZMA_VECTOR_PATH`, else beside the Knowledge
  database** (`data_dir()/vector_memory` for the install's), so a test's
  temporary store never writes into the install's. **They are not backed
  up** (`universal._rebuilt_dirs`; `chroma.sqlite3` is a `rebuilt` store):
  a file copy of a live Chroma database can be torn, and the sweep rebuilds
  them from the chunks, which are.
- **The Knowledge page shows each library's coverage** ("Meaning search:
  N of M", or "Keyword search only"), from `KnowledgeIndex.health` through
  `/api/kb/libraries`. Nothing showed it before, which is how the outage
  lasted two months. A library's `chunk_count` is COUNTED by every library
  SELECT (`_CHUNK_COUNT_SQL`): the column is a cache several writers keep,
  one missed, and the page listed 365 chunks for a library holding 366.
- **An embedding-model switch** (2026-09-27): a vector records the model
  that made it (`model` in its metadata; every write builds it in
  `_chunk_vector_metadata`) and the repair re-embeds another model's. Tags
  are read in Python (`VectorStore.models`): Chroma's `$ne` also matches a
  vector WITHOUT the key, and every vector written before the tag has none
  (it was made by the model of the day). A collection holding vectors of
  another size is rebuilt before the first write (`VectorStore._fit`, from
  the size it actually holds): Chroma keeps its first size and refuses any
  other. The memory Rebuild (`reembed.rebuild_embeddings`) used to finish
  with `shutil.rmtree(data_dir/vector_memory)` -- the V1 chat index once,
  the Knowledge Library's live store since 2026-09-26; it no longer touches
  it. Nor does `scripts/reembed.py` (2026-10-14): it kept its own copy of the
  rebuild, step 7 included, and now runs `rebuild_embeddings`. No function
  deletes a folder tree and names the store
  (`tests/test_reembed_keeps_knowledge_vectors.py`, the old step as the
  negative control).
- **A vector's tag names the embedder INSTANCE** (`embedder_identity`: class,
  model, size, endpoint digest; Knowledge audit KB-E03, 2026-10-14), never the
  configured name: a Settings change waiting for a restart, or a remote model
  that fell back to the local one, tagged vectors with a model that had not
  made them. Search filters on the active identity (`_raw_layers`), an
  untagged vector is re-made, and a query against a collection of another
  size waits for the sweep (one INFO). Health is the sweep's recorded
  coverage (`knowledge_vector_status`, states unknown/ok/rebuilding/
  unavailable/failed): listing libraries opens no collection and loads no
  model (KB-S15).
- **The sweep is isolated per library and observed** (`run_repair_sweep`,
  KB-E11): a failing library is recorded and the others repaired, then the
  pass raises for the runner's WARNING; the first unavailable pass and the
  recovery are WARNINGs; three bad passes raise the ops alert
  `knowledge.meaning_search` (6 h cooldown); `/metrics` carries
  `kazma_kb_vector_*`, readiness `knowledge_vectors` (never critical,
  `topology: single_install`), the daily digest a "Needs attention" line.
  The keyword index's coverage is checked each pass and rebuilt with a
  WARNING; an FTS storage error is a WARNING and `keyword_index_ok: false`,
  a query syntax error stays DEBUG. `KB_FOLD_VERSION` is stored in
  `knowledge_meta`; a new value rebuilds the index at open (a fingerprint
  test fails when `fold_for_search` changes without it).
- **One `KnowledgeIndex` per store** (`knowledge_index_for`, shared with
  Documents; KB-E12), and a collection another handle dropped is re-opened
  by name and the call retried once (`VectorStore._reopen_if_missing`). Only
  `knowledge_index_for` constructs an index in product code (gate in
  `tests/test_knowledge_vectors.py`).
- **Chroma's local store, kept honest** (`memory/chroma_local.py`, KB-Q01,
  2026-10-15; chromadb 1.5.9, the newest release that day, has every defect
  below):
  - *Dropped vectors leave the disk.* `delete_collection` removes the catalog
    rows and keeps the segment folder (~40 MB per 10k chunks): 17 folders in a
    4-minute soak. `remove_orphan_segment_folders` removes the UUID-named
    folders no row of `chroma.sqlite3`'s `segments` owns (folders listed
    BEFORE the catalog is read; an unreadable catalog removes nothing; a
    non-UUID folder is never touched) -- at client start, before any
    collection opens (`_shared_client`), after every drop
    (`VectorStore._drop_collection`, the ONE drop path: gate in
    `tests/test_knowledge_vector_lifecycle.py`), and at the end of each
    repair pass (`release_dropped_vectors`). On Windows a folder whose index
    this process loaded is held open until the client is closed: the pass
    then closes it -- a `_Gate` per client: every Chroma call of a
    `VectorStore` holds it shared (an embedding runs before it, so a model
    call never holds up a close), the close exclusive, so it waits for calls
    in flight and holds new ones -- removes the folders and bumps the
    client's generation; every store re-opens on its next call. `atexit`
    does the same.
  - *No index is evicted.* Chroma's HNSW pool is split over 64 shards
    (`POOL_SHARDS`), sized by default as `max_file_handles // 5` (102 on
    Windows: one or two a shard), and writes an index to disk only every
    `sync_threshold` (1,000) records; two collections in one full shard
    evict each other after the process replayed their logs. An evicted index
    under 1,000 records failed every read ("Nothing found on disk", 5 of 20
    short soaks); one of 1,000+ reloaded from its last disk write WITHOUT
    the records since -- no error, the search just missed them -- until the
    collection's next write (reproduced:
    `tests/test_knowledge_vector_text_and_pool.py`, a pool of 64 drops 150
    of 1,180 records). `chroma_local.start_system` starts Chroma's system
    for the directory with Kazma's pool before `PersistentClient` finds it
    (`hnsw_cache_size` is read only at `system.start()`): `index_pool_size`
    = 64 x (`library_ceiling()` + `SPARE_COLLECTIONS`), so a shard holds
    every collection a process can load, whichever shards they hash to.
    `LIBRARY_CEILING` is 500 libraries, lowered when the process cannot keep
    every index's 4 files open (`FILES_PER_INDEX`; Windows: ucrtbase streams
    raised to 8,192 -- lowered to 40, a process loads 9 indexes; msvcrt is
    only what Chroma's default pool reads; POSIX: soft NOFILE to 10,240
    under the hard limit). `KnowledgeStore.create_library` counts every
    library (archived too) in its `BEGIN IMMEDIATE` and raises
    `KnowledgeLibraryLimit` (a `KnowledgeStorageFull`, so every surface that
    refuses at the quota refuses it); `ensure_capacity(library_id)` asks
    before a crawl or page fetch; the Web API answers 409
    `kb_library_limit` (EN/AR). A read that still finds an index lost
    (`VectorStore._hnsw_read`) deletes the id `kazma:reload-index`: the push
    makes Chroma rebuild it from its write log before the read is asked
    again (WARNING, `kazma_kb_vector_index_reloads_total`).
    `hnsw:sync_threshold` does not fix it (minimum 2, and a persist rewrites
    the whole index).
  - *A log is replayed on one thread* (2026-10-21,
    `chroma_local.serial_index_inserts`, called by `_shared_client` before
    Chroma's pool first runs). The first read of a collection after a
    client starts (every boot, every client the repair pass reopens)
    applies its write log to the index Chroma loads from disk, on rayon
    worker threads, and that parallel insert races in hnswlib: an access
    violation in its graph search, always at one address, the Knowledge
    soak's crash (2026-10-17/19, three runs). Measured with
    `scripts/chroma_replay_race.py`: 6 crashes in about 28,200 client
    starts with Chroma's own pool, 0 in 28,000 with `RAYON_NUM_THREADS=1`;
    a crashed store replayed again never crashes (a race, not bad data).
    Price: 20,000 vectors at 1,024 dimensions insert in 60 s instead of
    8 s, a new client's replay of 999 records 6 s instead of 0.7 s. An
    operator's other value is kept and named once (WARNING). Every Chroma
    client in product code and scripts is built after the call; the drill
    alone measures Chroma's own pool. Gate:
    `tests/test_chroma_serial_inserts.py` (a real replay adds one thread,
    Chroma's own pool as the negative control; a planted client without
    the call fails the gate). Re-run the drill after a chromadb upgrade.
  - *No text beside a vector.* An older build stored each chunk's first
    2,000 characters as the Chroma document and its title, heading, address
    and hash as metadata -- read by nothing -- and SQLite keeps a deleted
    row's bytes in free pages and in pages' free space (`secure_delete` off,
    Chroma's own connection): a dropped 3,000-row collection's words had
    9,429 copies in the file. Chroma keeps a document in
    `embedding_metadata`, its trigram full-text index and its write log
    (until the next 1,000-record disk write). Every write keeps only
    `KEPT_METADATA` (`model`, `library_id`, `document_id`, `tenant_id`,
    `source`) and no document (gate: no `documents=` in a Chroma write in
    product code). A collection holding anything else (`holds_text`: a
    read-only catalog query on the metadata segment AND the write log,
    once per collection and process) is rebuilt by the repair pass
    (`VectorStore.rebuild_collection`, which replaced `rebuild_graph`: copy
    to `<name>.rebuild` with vectors and kept keys, counts compared, old
    dropped, copy renamed; nothing embedded again, 12 s for 10k at 1,024
    dimensions). Note: an upsert without documents KEEPS a record's old
    document and merges its metadata, so only the rebuild clears it.
  - *Deleted text leaves the catalog.* A deletion of rows that may hold
    text (`_note_deleting`: drops, `delete`, `delete_many`, unless the
    collection was found text-free) writes `vector_memory/
    kazma_compaction_due`; at the end of each repair pass `compact_vectors`
    takes the client's gate exclusive (calls in flight finish, new ones wait,
    the client stays open) and `compact_catalog` runs FTS5 `optimize` on
    Chroma's full-text index (rebuilt when the merge kept deleted words:
    `fts5_purge.purge_deleted_rows`, G below), then `VACUUM` (Chroma's own `chroma utils
    vacuum`), and clears the marker. 10k chunks: 41.0 -> 3.2 MB in 0.3 s; a
    text-free catalog 0.13 s. A failure keeps the marker (WARNING);
    `kazma_kb_vector_compaction_pending`.
  - *A search filters only when it must.* A collection holds one library,
    so `KnowledgeIndex._model_filter` asks Chroma no filter while
    `VectorStore.only_model(identity)` holds (one `$ne` read per collection
    and process; a write of another tag ends it; a mixed collection is
    re-asked after 60 s), else `{"model": identity}`. Chroma's metadata
    filter cost more than the search: 10k vectors, p50 40 ms with the
    library+model filter vs 4 ms without; 100k, 353 vs 6 ms (p95 442 vs 10),
    recall the same either way.
  - *The index is built for recall* (`_HNSW`: 48 neighbours, construction
    400, search 256 for a small library). Chroma's defaults (16/100/100)
    returned 31-39 of 40 planted answers at 10k chunks while exact search
    ranked all 40 first; these return 40/40 in EN, AR and mixed in 11 of 12
    harness measurements (39 in one: Chroma builds in parallel) for ~1 ms
    more a query. An older collection's graph is rebuilt by the repair pass
    from its own vectors (`VectorStore.rebuild_collection`).
  - *The search widens as the library grows* (`vector_store_global.search_breadth`,
    2026-10-15): one candidate for every 12 vectors, rounded up to the next
    of `_BREADTH_GRID` (256 below 3,072 vectors ... 12,288 from 98,304,
    24,576 from 196,608, 49,152 from 393,216: never under n/12, at most 1.5
    times it). `scripts/knowledge_soak.py breadth` (two corpora x EN/AR/mixed,
    240 planted questions a size, exact rank 1 each): at a fixed 256,
    239/218/210/203 of 240 found at 10k/50k/100k/200k; the least breadth that
    found all was at most n/12 at every size (512, 4,096, 6,144, 16,384, all
    Arabic); the rule's breadth found 240/240 at each, p50 ~3x 256's at 100k (the
    guide's "Meaning search at scale"). Chroma (1.5.9, measured) applies a
    breadth when it LOADS an index, every read loads it (a `count()` and a
    `get` too), and a loaded index keeps its breadth while the client lives
    (Kazma's pool never evicts): so the size comes from the catalog
    (`chroma_local.collection_size`, loads nothing) and `VectorStore._open`
    sets the breadth before anything reads the collection (`_fit_breadth`);
    the repair pass (`fit_search_breadth`) sets it for a collection that grew
    while loaded and then has every index loaded again
    (`reload_vector_indexes`: the client closed between calls, as
    `release_dropped_vectors` does); a breadth is kept until the size falls
    a fifth below its step (`_BREADTH_KEEP_SHARE`); a rebuilt collection is
    made with its size's breadth. Never set a breadth only by `modify` on a
    loaded collection and call it applied. Gate:
    `tests/test_knowledge_search_breadth.py` (the fixed breadth, a size read
    with `count()` and no reload as negative controls; recall on the real
    chromadb).

