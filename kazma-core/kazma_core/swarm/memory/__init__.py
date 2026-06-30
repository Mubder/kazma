"""Kazma 4-Layer Memory Architecture.

Exports:
    Layer 1 — VectorStore (ChromaDB global semantic)
    Layer 2 — KnowledgeGraph (NetworkX structural)
    Layer 3 — FTS5LexicalStore (SQLite FTS5 + BM25)
    Layer 4 — SQLiteVectorStore (sqlite-vec local)
    Adapter — UnifiedMemoryAdapter (RRF blending)
    get_encoder — shared sentence-transformers singleton
"""

from kazma_core.swarm.memory.vector import VectorStore, get_encoder
from kazma_core.swarm.memory.graph import KnowledgeGraph
from kazma_core.swarm.memory.fts5 import FTS5LexicalStore
from kazma_core.swarm.memory.sqlite_vec import SQLiteVectorStore
from kazma_core.swarm.memory.adapter import MemoryHit, UnifiedMemoryAdapter

__all__ = [
    "VectorStore",
    "KnowledgeGraph",
    "FTS5LexicalStore",
    "SQLiteVectorStore",
    "UnifiedMemoryAdapter",
    "MemoryHit",
    "get_encoder",
]
