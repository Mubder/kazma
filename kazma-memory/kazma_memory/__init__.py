"""Kazma Memory — sqlite-vec schemas, retrieval, provenance tagging.

This module provides optimized search capabilities using SQLite FTS5
with Arabic tokenization for lightweight, edge-deployable full-text search.
"""

from .arabic_tokenizer import ArabicTantivyTokenizer, ArabicTokenizer
from .search_backend import SearchBackend, SQLiteMemoryBackend

__all__ = [
    # Search Backend
    "SearchBackend",
    "SQLiteMemoryBackend",
    # Arabic Tokenizer
    "ArabicTokenizer",
    "ArabicTantivyTokenizer",  # Backward compatibility
]
