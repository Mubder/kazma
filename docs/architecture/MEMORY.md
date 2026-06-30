# Kazma 4-Layer Memory Architecture

## Overview

Kazma's memory system is a **co-processing cascade** — four independent backends
queried in parallel and blended into a single high-density context payload via
Reciprocal Rank Fusion (RRF).

```
┌─────────────────────────────────────────────────────────────┐
│                  UnifiedMemoryAdapter                       │
│                   query(text, tags)                         │
├──────────┬──────────┬──────────────┬────────────────────────┤
│ Layer 1  │ Layer 2  │   Layer 3    │      Layer 4           │
│ ChromaDB │ NetworkX │  FTS5 + BM25 │    sqlite-vec           │
│ (global) │ (graph)  │  (lexical)   │    (local)              │
│ semantic │ struct.  │  keyword     │    local-embedding      │
└──────────┴──────────┴──────────────┴────────────────────────┘
                           │
                    Reciprocal Rank Fusion
                           │
                    Top-N blended results
```

## Layer Descriptions

### Layer 1 — ChromaDB Global Vector (Semantic)
- **Backend:** ChromaDB with `all-MiniLM-L6-v2` (384-dim embeddings)
- **Purpose:** Cross-worker semantic similarity. Stores task outputs, worker Souls, and knowledge artifacts in `kazma_global` collection.
- **Query:** Cosine similarity via `collection.query(query_embeddings=[...])`
- **Indexing:** Called after pipeline stages complete and when worker Souls mutate.
- **File:** `swarm/memory/vector.py` — `VectorStore`

### Layer 2 — NetworkX Knowledge Graph (Structural)
- **Backend:** NetworkX `MultiDiGraph` persisted as JSON
- **Purpose:** Entity-relationship tracking. Maps code dependencies, worker lineage, task→output chains, and handoff records.
- **Query:** `query_related(id, depth)` returns upstream/downstream neighbors. `query_by_type("function")` returns all entities of a type.
- **Indexing:** `add_entity(id, type, props)` + `add_relation(src, dst, type)`
- **File:** `swarm/memory/graph.py` — `KnowledgeGraph`

### Layer 3 — SQLite FTS5 (Lexical)
- **Backend:** SQLite FTS5 with BM25 ranking + custom Arabic tokenizer
- **Purpose:** Exact and fuzzy keyword matching. Language-aware tokenization for Arabic (hamza normalization, stop-word filtering) and English.
- **Query:** `SELECT memory_id, bm25(memories_fts) FROM memories_fts WHERE MATCH ?`
- **Indexing:** Triggers on `memories` table → FTS5 sync (already built)
- **File:** `swarm/memory/fts5.py` — `FTS5LexicalStore` (wraps existing `SQLiteMemoryBackend`)

### Layer 4 — sqlite-vec Local Vector (Embedding)
- **Backend:** sqlite-vec virtual tables in `kazma-data/vector.db`
- **Purpose:** Zero-dependency local embeddings. Per-worker collections so each worker has its own vector store without requiring ChromaDB to be installed.
- **Query:** `SELECT id, vec_distance_cosine(embedding, ?) FROM worker_vectors ORDER BY distance LIMIT ?`
- **Indexing:** INSERT into `worker_vectors` table with blob embedding
- **File:** `swarm/memory/sqlite_vec.py` — `SQLiteVectorStore`

## Query Flow

```
UnifiedMemoryAdapter.query("fix auth bug", tags=["code", "security"])
│
├─► Layer 1: ChromaDB.query(["fix auth bug"])
│     └─→ [(id_1, 0.92), (id_2, 0.87), (id_3, 0.81)]
│
├─► Layer 2: KnowledgeGraph.query_related("auth_module")
│     └─→ [(entity_a, 1.0), (entity_b, 0.8)]
│
├─► Layer 3: FTS5LexicalStore.search("fix auth bug")
│     └─→ [(id_3, 15.2), (id_5, 12.1)]
│
├─► Layer 4: SQLiteVectorStore.query("fix auth bug")
│     └─→ [(id_1, 0.89), (id_4, 0.76)]
│
└─► RRF Blending
      k = 60 (smoothing constant)
      score = Σ 1/(k + rank_in_layer) across all 4 layers
      └─→ [(id_1, 0.048), (id_3, 0.044), (id_2, 0.039), ...]
      └─→ top-10 after dedup + trim
```

## Reciprocal Rank Fusion (RRF)

```
For each result r in layer L:
    rrf_score += 1 / (k + rank_of_r_in_L)

k = 60 (standard smoothing constant)
rank = 1-based position in that layer's sorted results

Final ranking: sort by rrf_score descending, take top-N
De-duplication: if two results share the same content hash, keep the higher score
```

## Singleton Encoder Pattern

Both `VectorStore` (ChromaDB) and `SQLiteVectorStore` (sqlite-vec) share a
**single** `SentenceTransformer` instance loaded once at module level. This
prevents double-loading the 90MB model into RAM.

```python
# swarm/memory/vector.py
_encoder: SentenceTransformer | None = None

def get_encoder(model_name: str = "all-MiniLM-L6-v2") -> SentenceTransformer | None:
    global _encoder
    if _encoder is None:
        try:
            from sentence_transformers import SentenceTransformer
            _encoder = SentenceTransformer(model_name)
        except ImportError:
            return None
    return _encoder
```

## Graceful Degradation

Every backend wraps initialization and queries in try/except:

| Layer | If fails | Fallback |
|:---|:---|:---|
| L1 ChromaDB | Not installed / connection error | Returns `[]`, logs warning |
| L2 NetworkX | JSON file corrupted | Returns `[]`, reinitializes empty graph |
| L3 FTS5 | SQLite error | Returns `[]`, falls back to LIKE queries |
| L4 sqlite-vec | Extension not loaded | Returns `[]`, RRF skips layer |
| Encoder | sentence-transformers not installed | All vector layers return `[]` |

The adapter's `health()` method reports per-layer status:
```python
{"chromadb": True, "graph": True, "fts5": True, "sqlite_vec": False}
```

The adapter's `health()` method reports per-layer status:
```python
{"chromadb": True, "graph": True, "fts5": True, "sqlite_vec": False}
```

## Atomic Async Indexing

All indexing operations execute asynchronously to avoid blocking the pipeline:

```python
async def index(self, text: str, metadata: dict) -> None:
    await asyncio.gather(
        self._index_chromadb(text, metadata),   # Layer 1
        self._index_graph(text, metadata),       # Layer 2
        self._index_fts5(text, metadata),        # Layer 3
        self._index_sqlite_vec(text, metadata),  # Layer 4
        return_exceptions=True,                  # never crash on one failure
    )
```

## File Map

| File | Layer | Class |
|:---|:---|:---|
| `swarm/memory/vector.py` | L1 | `VectorStore` |
| `swarm/memory/graph.py` | L2 | `KnowledgeGraph` |
| `swarm/memory/fts5.py` | L3 | `FTS5LexicalStore` |
| `swarm/memory/sqlite_vec.py` | L4 | `SQLiteVectorStore` |
| `swarm/memory/adapter.py` | — | `UnifiedMemoryAdapter` |
