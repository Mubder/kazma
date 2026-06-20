"""Kazma Memory — sqlite-vec schemas, retrieval, provenance tagging.

This module provides high-performance search capabilities using Tantivy
(Rust-based engine) for massive, multi-million object agent memories
with sub-millisecond query latency.
"""
from .arabic_tokenizer import ArabicTantivyTokenizer
from .benchmark import BenchmarkReport, BenchmarkResult, SearchBenchmark
from .migration import MigrationResult, SQLiteToTantivyMigration, VerificationResult
from .report_store import ReportStore, ReportStoreError
from .search_backend import SearchBackendRouter, SQLiteMemoryBackend
from .tantivy_backend import (
    IndexStats,
    Memory,
    SearchResult,
    TantivySearchBackend,
)

__all__ = [
    # Tantivy Backend
    "TantivySearchBackend",
    "Memory",
    "SearchResult",
    "IndexStats",
    
    # Arabic Tokenizer
    "ArabicTantivyTokenizer",
    
    # Migration
    "SQLiteToTantivyMigration",
    "MigrationResult",
    "VerificationResult",
    
    # Benchmark
    "SearchBenchmark",
    "BenchmarkResult",
    "BenchmarkReport",
    
    # Search Backend Router
    "SearchBackendRouter",
    "SQLiteMemoryBackend",
    
    # Existing
    "ReportStore",
    "ReportStoreError",
]
