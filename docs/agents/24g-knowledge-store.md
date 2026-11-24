<!-- AGENTS.md, piece 24g-knowledge-store. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
**G. The Knowledge store and its surfaces** (Knowledge audit 2026-10-13,
tracker `docs/plans/KNOWLEDGE_INDUSTRIAL_HARDENING_GOAL.md`).
- **Own file** `knowledge.db` (`paths.knowledge_db`, a `bundle` store; KB-E10):
  a quota on the store's own pages in use (1 GiB, refused before fetch with
  `KnowledgeStorageFull`, reads unaffected), `auto_vacuum=INCREMENTAL` and the
  maintenance pass's `compact()` (never `VACUUM`: it renumbers the rowids the
  FTS is keyed by). `settings.db`'s old tables move in on the first open
  (`copy_legacy_knowledge`, explicit columns, FTS rebuilt, a non-http seed
  blanked) and a bundle in the older layout is staged the same way
  (`importer._stage_legacy_knowledge`; no Knowledge DDL in `migration/`,
  KB-E01). Every connection sets the pragmas, `foreign_keys` included, and
  `close()` takes the store lock (KB-E13).
- **One publication path** (`KnowledgeStore.publish_page`, KB-E02): a page's
  retire and insert, FTS included, in one `BEGIN IMMEDIATE`; a deleted
  library refuses (`KnowledgeLibraryGone`), a failed page fails and the crawl
  ends `partial`. Chunk ids carry the position (`make_chunk_id`, KB-E05). The
  FTS is keyed by rowid and deleted by `rowid AND chunk_id` (KB-E06); library
  deletes run in 500-row batches with the lock released between them.
- **A deleted library's words leave the index file** (KB-Q01, 2026-10-15):
  FTS5 keeps a deleted row's words in its older segments until a merge
  reaches them -- every planted word of a deleted 10k library was still in
  `knowledge_chunks_fts_data`. `delete_library` ends with
  `purge_search_index`: an FTS5 `merge` of every segment (memory's M01
  purge, bounded: `_PURGE_STEP_PAGES` = 1,000 pages a transaction, the lock
  released between steps). A replaced page or document version keeps its old
  words until FTS5's own merges (unsearchable; a full rewrite per page would
  cost the whole index each time). `tests/test_knowledge_store.py`.
  **A merge is not proof** (the multi-day soak, 2026-10-15: 8 words of the
  library deleted at cycle 76 left after the purge). FTS5 drops a delete
  marker -- which carries the word -- only when the merge writes the
  index's OLDEST segment, judged by place (the only segment on the
  structure's last level); every full merge adds two levels and a larger
  new segment pulls older ones down, and with every segment on one level
  below the top `merge -N` and `optimize` merge it one level up and keep
  every marker. So the purge's last transaction reads the structure record
  (`db/fts5_purge.drop_deleted_rows`; an unreadable record counts as
  unchecked) and rebuilds the index (FTS5 `rebuild`) unless it is one
  segment on the last level. Every FTS5 `optimize`/`merge` in product code
  goes through `fts5_purge` or ends with that check -- memory's Forget
  purge and the vector catalog's compaction too (gate:
  `tests/test_fts5_purge.py`, the plain `optimize` as negative control).
  **The purge outlives the process:** each delete batch writes
  `knowledge_meta.keyword_purge_due` (a counter) in its own transaction;
  the purge clears it only if unchanged; every store open and the repair
  pass (`finish_pending_purge`) finish a purge a killed delete left, and a
  delete that finds no chunk still purges when it is set. Gate: a real
  child killed after a batch, after the library row, between two merge
  steps and inside the rebuild (`tests/test_knowledge_store.py`, the store
  without the mark as negative control). The soak's check reads whole words
  from the leaf pages and leaves out words a live row holds
  (`knowledge_soak._segment_residue`): its old test of a word's last 4-6
  letters anywhere in the blobs hit 6 of 20,000 random Arabic tokens.
- **A coverage record names the vector store it measured** (KB-Q01): the
  record is in knowledge.db (backed up), the vectors are not, and a restored
  database reported "ok, 10,000 of 10,000" before the first pass with no
  vector there. `knowledge_vector_status.store_id` is the id the sweep read
  from `vector_memory/kazma_vector_store_id` (`chroma_local.vector_store_id`,
  written at the directory's first client start); `KnowledgeIndex.health`
  shows an ok/rebuilding record of another store, or of none (a row from
  before the column), as `unknown` -- read from that file, no client, no
  model (KB-S15). `tests/test_knowledge_vectors.py`.
- **Delete goes through `knowledge_lifecycle.delete_library`** (KB-E08): 409
  while a crawl owns the library, the documents it holds unindexed through
  the Documents service first, then vectors, chunks and the fetched pages
  (`<data dir>/knowledge_provenance/<library>`, a `bundle` entry).
- **Model boundary**: the supervisor builds the turn's ONE Knowledge block
  (`_knowledge_inject_for_turn`, off the loop; KB-S08), stripping an earlier
  turn's note at iteration 0; the transports inject nothing. The "Memory
  used" record lists exactly the injected chunks (KB-S02), even with recall
  suppressed. `knowledge_search` keeps its scaffolding outside the fence and
  the page's text inside (KB-S01); so does the turn note since 2026-10-15
  (`KnowledgeInject.block` fenced, then `.footer` -- the library footer and
  the instruction to cite it -- after the fence: inside, the fence told the
  model to obey none of it; the note still opens with the fence, its
  `turn_notes` kind). Federated search ranks each store on its
  own budget. Stored URLs are http(s) only (`validate_web_url`, every
  writer), rendered through `safeHref` (KB-S09).
- **A search shows only what is about the question** (KQ01, 2026-10-15,
  `knowledge_index._judge`): the tool, `/kb search`, the page's test search
  and auto-inject all rank on memory's EVIDENCE rule -- meaning similarity
  above the question's background (meaning ranks 4-15) plus content-word
  coverage (`memory/query_terms`) -- with a floor (0.08), a gap (0.15) and
  memory's words-only rule when no vector judges; `score` is that evidence
  and `strength` "weak" is shown as possibly related. The fusion ranked by
  position and always showed something: five hits and three injected chunks
  for every question nothing answers. A keyword-only candidate is measured
  by its stored vector (`VectorStore.similarities`); the question is
  embedded once per search. A document-scoped search is not judged.
  **An identifier the question names wins** (`_sole_exact_match`,
  2026-10-16): the one candidate holding every content word of the
  question, when one of them is in no other candidate, leads with the best
  kept evidence. It reorders what is kept, never keeps more. The KB-Q01 72 h
  soak failed at cycle 1,757 on "Which port does zqfmbfjvdv listen on?": five
  look-alikes ("zqtkjhghfc listens on port 5997") were nearer by meaning, word
  coverage weighs 0.12, and the answer -- the keyword index's first hit --
  came 6th. Benchmark scores unchanged. Gate:
  `tests/test_knowledge_exact_identifier.py` (the soak's measured scores; the
  judge without the rule as control; the kept set identical with and without).
- **Retrieval quality is measured and ratcheted** (KQ01):
  `stores/knowledge_benchmark.py` publishes a versioned EN/AR/mixed corpus
  (`knowledge_benchmark_data/dataset.json`, built by
  `scripts/knowledge_bench.py build`) into a PRIVATE store through the
  product's publication path and asks every question through
  `search_all_sync` (the tool) and `build_knowledge_inject(store=)` (the turn
  note), with meaning (`hybrid`) and without (`keyword`), tuning and held-out
  questions apart. CI replays bge-m3's recorded vectors
  (`tests/fixtures/knowledge_bench/vectors.npz`) through an exact in-process
  client registered for the private vector folder only
  (`vector_store_global.directory_client`; `run --chroma` gave the same
  scores on chromadb 1.5.9, `run --real` on the live model). The scores are a
  ratchet stamped with the dataset version (`thresholds.json`; `lock` raises
  and refuses to lower, `lock --rebaseline` for a new version only). Gate:
  `tests/test_knowledge_benchmark.py` (the pre-KQ01 fusion order and a search
  that shows nothing as negative controls). Measured numbers: the guide's
  "Benchmark report".
- **A crawl seeded on a private network is refused at admission**
  (2026-10-15, `knowledge_ingest._admit_public_seed`, last check of
  `_prepare_ingest_site`, before the journal row; every caller creates the
  library only after it returns): the check acquisition makes
  (`dns_worker.validate_public_url`, never `to_thread(validate_url)`),
  `security_blocked` for a private, loopback or link-local address,
  `connect_error` (transient) for a name that resolves to nothing
  (`ssrf.UnresolvedHostError`). Until then the seed was admitted, the crawl
  refused it before discovery and the library stayed empty with no refusal
  shown. Gate: `tests/test_knowledge_crawl_admission.py` (every surface --
  Web crawl and refresh, `/kb crawl|refresh|add`, both agent tools, a page
  ingest -- and `STARTERS`, the class gate: a new crawl starter must be
  declared and admitted before its library exists; the old admission as the
  negative control), `tests/e2e/test_knowledge_outcomes_browser.py` (EN/AR).
- **Surfaces**: `/api/kb/*` refuses with stable codes (`KB_ERROR_CODES`,
  `knowledge.err.<code>` EN+AR; KB-S05) and reads bodies under 64 KiB with
  bounded models (KB-S13); every KB surface refuses an install with platform
  users (`kb_refusal`, the shared single-operator rule; KB-S04); `/kb
  add|crawl|refresh|delete` are handled in the gateway, admin-only through
  `changes_global_config`, and report the journal's outcome (KB-S07).
  `window.kazmaSave` rejects `{ok: false}` and every Settings save does too
  (KB-S10).
- Gates: `tests/test_knowledge_publication.py`, `test_knowledge_crawl_journal.py`,
  `test_knowledge_vectors.py`, `test_knowledge_model_boundary.py`,
  `test_kb_api_contract.py`, `test_kb_gateway_commands.py`,
  `test_knowledge_migration.py`, `test_knowledge_docs_claims.py`, each with
  its negative control.
- Gate: `tests/test_knowledge_meaning_search.py` (a fake chromadb in CI, the
  real one where the `rag` extra is installed).
- KB-Q01's harness is `scripts/knowledge_soak.py` (`scale`: ingest, search,
  idle-sweep, restore re-embed and delete measured per size and language on
  named hardware; `soak`: crawls, refreshes, documents, deletes, model
  switches, crashes, a restart every cycle, checked from the stores), held by
  `tests/test_knowledge_soak_harness.py` (its runs need the real chromadb;
  the negative controls run everywhere). It runs strict since the KB-Q01
  fixes: a dropped collection's folder, a deleted library's words in the
  keyword index, a restored coverage record claiming lost vectors and a
  collection a process cannot read are violations (`--tolerate-*` exists
  only to measure an older build); an index Chroma lost and the product
  rebuilt is a counted finding.

