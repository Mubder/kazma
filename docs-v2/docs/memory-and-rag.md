# Memory & RAG

> A precise, source-referenced map of Kazma's memory subsystems — including honest status notes on what is actually wired versus what older docs describe.

---

## 1. The three memory subsystems (read this first)

Kazma contains **three distinct, non-integrated** memory subsystems. Historically the docs conflated them into one "4-layer pipeline." This section separates them honestly:

| Subsystem | Backing | Embedding | Used by chat agent? | Code |
|---|---|---|---|---|
| **A. VectorMemory** (RAG tools) | ChromaDB PersistentClient | `all-MiniLM-L6-v2` (384-d) | Only when the LLM calls `memory_search` / `memory_store` | `kazma_core/memory/vector_store.py` |
| **B. SQLiteMemoryBackend** (agent `self.memory`) | SQLite + FTS5 (`porter unicode61`) | none | **Not queried during chat retrieval** | `kazma_memory/search_backend.py` |
| **C. UnifiedMemoryAdapter** (the "4-layer") | ChromaDB + NetworkX + FTS5 + sqlite-vec, RRF-blended | `all-MiniLM-L6-v2` | **No** — only `self_improvement.py` + `phonebook.py` | `kazma_core/swarm/memory/adapter.py` |

> **Bottom line:** in the default chat path, the *only* memory retrieval that happens is a single ChromaDB query behind an **opt-in LLM tool**. There is no automatic injection of retrieved context into the prompt, and there is no short-term→permanent consolidation process.

---

## 2. Subsystem A — VectorMemory (the RAG tools)

This is what the `memory_search` / `memory_store` tools actually use.

### 2.1 Initialization

`kazma-ui/kazma_ui/app.py:444-462`:

```python
vector_memory = VectorMemory(
    path=KAZMA_VECTOR_PATH,          # default ~/.kazma/vector_memory
    collection_name=KAZMA_VECTOR_COLLECTION,  # default "agent_memory"
    model_name=KAZMA_VECTOR_MODEL,   # default "all-MiniLM-L6-v2"
)
set_vector_memory(vector_memory)
```

`VectorMemory` (`memory/vector_store.py`):

- Storage: `chromadb.PersistentClient(path=...)` (line 57).
- Embedding: `SentenceTransformerEmbeddingFunction` bound at collection creation (lines 58-63) — **all embeddings run locally, no external API calls** (module docstring line 5).
- Collection default: `agent_memory` (line 42).
- Dimension: **384** (`all-MiniLM-L6-v2`). Note: `kazma.yaml` `storage.vector_dim: 1536` does **not** match and is not enforced here.

### 2.2 Retrieval (tool-based, not automatic)

Retrieval happens **only** when the LLM chooses to call `memory_search` (`agent/tool_registry.py:591-605`):

```python
async def memory_search(query: str, limit: int = 5) -> str:
    mem = get_vector_memory()
    results = mem.search(query=query, n_results=limit)
    return json.dumps(results, ...)
```

`VectorMemory.search()` (`vector_store.py:136-180`) is a single ChromaDB call:

```python
self._collection.query(query_texts=[query], n_results=..., where=tenant_filter)
```

The result returns to the LLM as a normal tool observation in the ReAct loop. **There is no FTS step, no re-ranking, and no automatic injection.**

### 2.3 Storage (tool-based)

Storage happens only when the LLM calls `memory_store` (`tool_registry.py:607-624`): `mem.add(text, metadata)` → `VectorMemory.add()` → `self._collection.add(documents, metas, ids)`.

> **No chunking strategy exists.** Each `add()` is one document. Greps for `chunk`, `text_splitter`, `chunk_size`, `overlap` found no document-splitting implementation.

### 2.4 Shared embedding singleton

`swarm/memory/vector.py:27-46` exposes `get_encoder()` which loads `SentenceTransformer("all-MiniLM-L6-v2")` once. Layer 4 (`sqlite_vec.py:141`) reuses this singleton.

---

## 3. Subsystem B — SQLiteMemoryBackend (FTS5)

`kazma_memory/search_backend.py` — a hybrid FTS5 + (intended) vector backend.

### 3.1 Schema

- DB: `kazma-data/memory.db` (line 32), PRAGMAs `journal_mode=WAL`, `synchronous=NORMAL` (lines 52-53).
- `memories` table (lines 63-75): `id, content, content_arabic, metadata, timestamp, source, relevance, embedding BLOB, tenant_id` (tenant auto-migrated, lines 78-81).
- FTS5 table `memories_fts` (lines 86-113) kept in sync by `AFTER INSERT/DELETE/UPDATE` triggers, with columns `memory_id, content, content_arabic`.

### 3.2 Search

`search()` (lines 170-320) runs FTS5 `MATCH` + `bm25(memories_fts)` first, then optionally a vector search if `semantic_search=True` and an `embedding` is provided. Combined score: `-bm25*0.7 + relevance*0.3` (lines 310-318).

### 3.3 Honest status: this backend is not queried in chat

- It is wired as the agent's `self.memory` (`agent_runner.py:220`).
- **No caller in the chat path invokes its `search()`** during retrieval.
- It is therefore effectively dormant for retrieval. It may still be written to by other paths, but it does not feed the chat loop.

---

## 4. Subsystem C — the 4-layer UnifiedMemoryAdapter

The "4-layer memory" referenced in `README.md` and `swarm/memory/__init__.py`. All four layers exist as code under `kazma_core/swarm/memory/`:

| Layer | File | Backing |
|---|---|---|
| 1. Vector | `vector.py` | ChromaDB (`PersistentClient` or in-memory); default collection `kazma_global`. |
| 2. Graph | `graph.py` | `networkx.MultiDiGraph`, JSON-persisted to `kazma-data/knowledge_graph.json`. Tracks code deps, worker lineage, task→output, handoffs. |
| 3. Lexical | `fts5.py` | `FTS5LexicalStore` wrapper. |
| 4. sqlite-vec | `sqlite_vec.py` | vec0 virtual tables (`FLOAT[384]`), `WHERE embedding MATCH ? ORDER BY distance`. DB at `kazma-data/vector.db`. |

### 4.1 RRF blending

`UnifiedMemoryAdapter.query()` (`adapter.py:78`) fans out to all 4 layers in parallel (`asyncio.gather`, line 116) and blends with **Reciprocal Rank Fusion**, `_RRF_K = 60` (line 26): score contribution `1.0 / (_RRF_K + rank)` (line 237). Results are deduped by uid.

### 4.2 Where it is actually used

Only two non-test callers:

- `kazma_core/skills/self_improvement.py:131,194` (`get_adapter()`)
- `kazma_core/swarm/phonebook.py:69` (`get_adapter()`) — injects "PREVIOUS_SUCCESSFUL_STRATEGIES" context before dispatch.

**It is NOT used by** `agent_runner.py`, the gateway chat path, `tool_registry.py`, or `majlis.py`. The default chat agent never touches it.

---

## 5. The Arabic tokenizer

`kazma-memory/kazma_memory/arabic_tokenizer.py` — feeds the FTS5 `content_arabic` column.

### 5.1 Normalization pipeline (`normalize`, lines 132-165)

Applied in order:

1. **Diacritics removal** (`_remove_diacritics`, lines 193-204): regex `[\u064B-\u065F\u0670]` (harakat + superscript alef).
2. **Alef normalization** (`_normalize_alef`, lines 206-218): `أ`, `إ`, `آ` → `ا`.
3. **Teh Marbuta → Heh**: `ة` → `ه` (line 148).
4. **Yeh normalization** (`_normalize_yeh`, lines 220-232): `ئ`, `ؤ`, `إي`, `ى` → `ي`.
5. **Waw/Ya Hamza**: `ؤ` → `و`, `ئ` → `ي` (note: these overlap with rule 4 — known minor conflict).
6. **Tatweel/Kashida removal**: `text.replace("ـ", "")` (line 160). ✅ present.
7. Whitespace collapse.

### 5.2 Stop words (lines 35-102)

~40 hardcoded entries: particles (`في`, `من`, `على`), pronouns (`أنا`/`انا`, `هو`, `هي`), connectors (`و`, `أو`/`او`, `ثم`), plus **Kuwaiti dialect terms** (`يلا`, `شلون`, `عشان`, `مو`, `ليه`, `ماكو`, `فد`). Normalized (hamza-stripped) duplicates were added in a "BUG-023 fix."

### 5.3 Stemmer (`_init_stemmer`, lines 104-130)

Regex-based suffix/prefix stripping (suffixes: `ات`, `ون`, `ين`, `ة`, `ان`, `نا`; prefixes: `ال`, `بـ`, `كـ`). The docstring calls it "Basic stemming" — not a full lemmatizer.

### 5.4 Classes

- `ArabicTokenizer.tokenize()` → returns a **string** (lines 167-191).
- `ArabicTantivyTokenizer.tokenize()` → returns a **list** (lines 243-266), filtering `len(word) > 1`.

---

## 6. Context compaction

When the conversation grows toward the context-window limit, the `ContextAuthority` summarises it. This is the *only* automatic context-management that runs in the chat path.

### 6.1 Components

| Component | File | Role |
|---|---|---|
| `CompactionEngine` | `compaction.py` (287 lines) | The summariser. |
| `ContextAuthority` | `authority.py` (94 lines) | The gatekeeper that decides when to compact. |
| `TokenCounter` | `token_counter.py` | Token counting + threshold. |

### 6.2 Strategy (`compaction.py:63 compact`)

1. **Checkpoint save** (line 87-92) — *intended*; see status note.
2. **LLM summarise** (line 95) — uses `_SUMMARY_SYSTEM` (lines 20-30): preserve Task Goal, Key Decisions, Tool Results, User Constraints; capped at 2000 tokens, char-truncated at 8000 (line 160).
3. **Retrieve top-5 memories** (line 98) — *intended*; see status note.
4. **Build fresh system message** with summary + memories (`_build_compacted_system`, line 231-259).
5. **Return** new state with `messages = [single system message]`, `context_tokens = 0`.

### 6.3 Threshold

Hardcoded `int(window * 0.8)` (`token_counter.py:23`). With the default `memory.max_context_tokens: 128000`, compaction fires at **102,400 tokens**. This is *deliberately not user-configurable* (`authority.py:5`, `compaction.md:178`); the only tunable is the window itself.

### 6.4 Trigger point

Inside the supervisor node (`graph_builder.py:167`): `compacted_state = await authority.check_and_enforce(state_for_check)` runs **before** the LLM call. If compacted, control returns to `SUPERVISOR` to restart (line 174).

### 6.5 Heuristic fallback

If no LLM is available, `_summarize_heuristic` (line 168) builds a summary from message count + last user message (500 chars) + last 5 tool outputs (200 chars each).

### 6.6 Token counting

`TokenCounter` uses `tiktoken` if installed, else a **chars/4 heuristic** (`token_counter.py:44-46`). `tiktoken` is **not** a declared dependency — the heuristic is the default unless you install it yourself.

---

## 7. Honest status notes (read before relying on memory features)

These are the gaps the audit uncovered. They are documented here so operators don't rely on behavior that isn't actually active.

1. **Short-term→permanent consolidation does NOT exist.** No `consolidat*`, `promote`, `persist.*memory`, or background promotion logic was found. Permanent memory is written **only** when the LLM explicitly calls `memory_store`. Compaction does **not** write discarded messages into `VectorMemory` — it only checkpoints (intended) and summarises.

2. **Compaction's memory retrieval & checkpointing are no-ops in the default wiring.** `agent_runner.py:162-166` calls `create_authority(...)` **without** `memory_store` or `checkpoint_manager` (both default `None`). Consequences:
   - `retrieve_memories()` returns `[]` (`compaction.py:220-221`) — the "retrieve top-5 memories" step is inert.
   - The "save checkpoint before compacting" step is skipped.
   - **Only the LLM summarisation actually runs.** Older docs (`compaction.md`, README) describing memory-enriched, checkpointed compaction describe the *intended* design, not the runtime.

3. **The 4-layer adapter is not wired into chat.** It exists and works, but is only reachable from `self_improvement.py` and `phonebook.py`.

4. **`search_backend.py` sqlite-vec detection is a no-op.** Lines 55-60 run `SELECT sqlite_version()` (always succeeds) and **never call `load_extension`**, so `_vec_available` is unreliable.

5. **`search_backend.py` `_vector_search` uses `distance(embedding, ?)`** (lines 343, 354), which is **not a valid sqlite-vec function** (vec0 tables are queried via `MATCH`/`k`, as correctly done in `swarm/memory/sqlite_vec.py:215-217`). This path would throw at runtime and return `[]` from the `except`. In practice it is unreached because callers don't pass `semantic_search=True` with an embedding.

6. **`sqlite-vec` is not a declared dependency.** It appears in `uv.lock` only as a transitive dependency of `langgraph-checkpoint-sqlite`. It is available at runtime as a side effect, not because Kazma requests it.

7. **Three disjoint memory subsystems** coexist without integration: (A) ChromaDB RAG tools, (B) FTS5 `self.memory` (dormant for retrieval), (C) 4-layer RRF adapter (self-improvement/phonebook only).

8. **The documented "embed → vector → FTS → re-rank → inject" pipeline does not exist as a connected flow.** Retrieval is a single ChromaDB query behind an opt-in LLM tool.

---

## Documentation Audit Notes

- **Premise attribution corrected:** The "4-layer memory" claim is in `README.md:313` and `swarm/memory/__init__.py:1`, **not** in `AGENTS.md`.
- **Wiring gap in `agent_runner.py:162-166`** is the single most important finding for anyone planning to rely on RAG during long conversations. Until `memory_store`/`checkpoint_manager` are passed to `create_authority()`, compaction is summarise-only.
- **`storage.vector_dim: 1536` vs actual 384-d** mismatch is documented in [Configuration](configuration.md).
- **No chunking** means long documents stored via `memory_store` are embedded whole — consider pre-splitting in your skill/tool if you need finer retrieval.
