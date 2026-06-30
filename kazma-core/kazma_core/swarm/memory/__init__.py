"""Kazma 4-Layer Memory Architecture.

Exports:
    Layer 1 — VectorStore (ChromaDB global semantic)
    Layer 2 — KnowledgeGraph (NetworkX structural) — TODO
    Layer 3 — FTS5LexicalStore (SQLite FTS5) — TODO
    Layer 4 — SQLiteVectorStore (sqlite-vec local)
    get_encoder — shared sentence-transformers singleton
"""

from kazma_core.swarm.memory.vector import VectorStore, get_encoder
from kazma_core.swarm.memory.sqlite_vec import SQLiteVectorStore

__all__ = [
    "VectorStore",
    "SQLiteVectorStore",
    "get_encoder",
]
