# Architecture Change: Tantivy Removal → SQLite FTS5

## Overview

This document describes the architectural change made to the Kazma project in January 2025 (Lead Architect decision), transitioning from Tantivy-based search to SQLite FTS5 with Arabic tokenization.

## Date of Change

- **Initial Commit**: 12e7876 (feat: Replace Tantivy with SQLite FTS5 + Arabic tokenization)
- **Date**: 2025-01-XX
- **Reason**: Optimize for edge deployment, remove external build dependencies (Rust/maturin)

## What Was Removed

### 1. Tantivy Dependencies
- **File**: `pyproject.toml`
- **Removed**: `[project.optional-dependencies.tantivy]` section including `tantivy-py>=0.10.0,<0.11.0`
- **Reason**: tantivy-py requires Rust/maturin build toolchain, complex for edge deployment

### 2. Tantivy Backend Module
- **File**: `kazma-memory/kazma_memory/tantivy_backend.py`
- **Removed**: Entire module (~200+ lines)
- **Contained**: `TantivySearchBackend`, `IndexStats`, `Memory`, `SearchResult` classes
- **Reason**: Replaced with SQLite FTS5 implementation

### 3. Migration Module
- **File**: `kazma-memory/kazma_memory/migration.py`
- **Removed**: Entire module (~200+ lines)
- **Contained**: `SQLiteToTantivyMigration`, `MigrationResult`, `VerificationResult` classes
- **Reason**: No longer needed - no migration from/to Tantivy

### 4. Benchmark Module
- **File**: `kazma-memory/kazma_memory/benchmark.py`
- **Removed**: Entire module (~400+ lines)
- **Contained**: `SearchBenchmark`, `BenchmarkResult`, `BenchmarkReport` classes
- **Reason**: Tantivy-specific benchmarking, no longer relevant

### 5. Report Store Module
- **File**: `kazma-memory/kazma_memory/report_store.py`
- **Removed**: Entire module (~100+ lines)
- **Contained**: `ReportStore`, `ReportStoreError` classes
- **Reason**: Used by migration/benchmarking, no longer needed

### 6. Test Files
- **File**: `tests/test_tantivy_backend.py`
- **Removed**: Entire test file (~200+ lines)
- **Contained**: Comprehensive tests for Tantivy backend
- **Reason**: Tantivy backend removed

- **File**: `tests/test_migration.py`
- **Removed**: Entire test file (~300+ lines)
- **Contained**: Tests for SQLite to Tantivy migration
- **Reason**: Migration module removed

### 7. CI Workflow Configuration
- **File**: `.github/workflows/ci.yml`
- **Changed**: Removed `--no-extra tantivy` flag from `uv sync --all-extras`
- **Reason**: tantivy extra no longer exists in pyproject.toml

## What Was Added

### 1. Enhanced SQLite Backend
- **File**: `kazma-memory/kazma_memory/search_backend.py`
- **Added**: FTS5 virtual table with automatic triggers
- **Added**: Arabic tokenization bridge
- **Added**: Hybrid BM25 + vector search querying
- **Simplified**: Removed `SearchBackendRouter` class (now single `SearchBackend`)

### 2. Generic Arabic Tokenizer
- **File**: `kazma-memory/kazma_memory/arabic_tokenizer.py`
- **Changed**: Renamed `ArabicTantivyTokenizer` to `ArabicTokenizer`
- **Added**: Backward compatibility wrapper `ArabicTantivyTokenizer`
- **Maintained**: All Arabic text processing functionality

### 3. SQLite Search Tests
- **File**: `tests/test_sqlite_search_backend.py`
- **Added**: 20 comprehensive tests for new SQLite-only backend
- **Tests**: Arabic tokenizer, FTS5 search, hybrid querying, edge deployment

### 4. Updated Core Systems
- **File**: `kazma-core/kazma_core/agent.py`
- **Changed**: Removed Tantivy import, use `SearchBackend` directly
- **File**: `kazma-memory/kazma_memory/__init__.py`
- **Simplified**: Export only `SearchBackend`, `SQLiteMemoryBackend`, `ArabicTokenizer`
- **File**: `setup.sh`
- **Updated**: Removed Tantivy installation instructions

## Technical Details

### FTS5 Implementation
```sql
-- Main memories table with Arabic-processed content
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    content_arabic TEXT,  -- Arabic-tokenized version
    metadata TEXT DEFAULT '{}',
    timestamp INTEGER DEFAULT 0,
    source TEXT DEFAULT '',
    relevance REAL DEFAULT 1.0,
    embedding BLOB
);

-- FTS5 virtual table with automatic triggers
CREATE VIRTUAL TABLE memories_fts
USING fts5(content, content_arabic, content_rowid=rowid);

-- Triggers keep FTS5 in sync
CREATE TRIGGER memories_ai AFTER INSERT ON memories ...
CREATE TRIGGER memories_ad AFTER DELETE ON memories ...
CREATE TRIGGER memories_au AFTER UPDATE ON memories ...
```

### Arabic Processing Pipeline
```
Original Text
    ↓
ArabicTokenizer.normalize()
    ↓ Unicode normalization, diacritics removal
ArabicTokenizer.tokenize()
    ↓ Stop words removal, stemming
content_arabic
    ↓ Indexed in FTS5
```

### Benefits of Change

1. **Zero External Dependencies**: No Rust/maturin required
2. **Simpler Deployment**: Just `uv sync`, no special extras
3. **Edge-Ready**: Works on any system with SQLite
4. **Reproducible**: Same behavior everywhere
5. **Fast Performance**: FTS5 BM25 ranking + sqlite-vec vector search
6. **Proper Arabic Support**: Kuwaiti dialect + MSA tokenization

## Migration Notes

### For Developers
- If you see code referencing `TantivySearchBackend`, it needs to be replaced with `SearchBackend`
- If you see code referencing `SearchBackendRouter`, it should be replaced with `SearchBackend`
- The memory backend now automatically handles Arabic tokenization during indexing

### For Deployments
- No special Tantivy installation needed
- Setup script (`setup.sh`) now installs all dependencies automatically
- Database schema includes FTS5 tables (automatically created on first run)

## Test Status

- **Total Tests**: 1125 (1105 original + 20 new SQLite search tests)
- **Pass Rate**: 100%
- **Coverage**: Full SQLite search backend tested

## Related Commits

1. `12e7876` - feat: Replace Tantivy with SQLite FTS5 + Arabic tokenization (main architectural change)
2. `8b7fedd` - docs: Update BUG_FIX_TASK.md with SQLite FTS5 architecture changes
3. `08f88fe` - fix: Remove tantivy exclusion from CI workflow
4. `a2922b5` - fix: Resolve CI lint errors (unused import)
5. `3fc297d` - fix: Separate direct imports from from-imports for ruff I001
6. `de3202c` - fix: Move pytest before from-imports to satisfy ruff I001
7. `d352660` - fix: Group pathlib with stdlib imports for ruff I001

## Architectural Decision Context

**Decision Made By**: Lead Architect
**Decision Type**: Override of external dependencies for keyword search
**Rationale**: Kazma must remain lightweight and optimized for edge deployment
**Constraint**: Zero external build dependencies (no Rust, no maturin)
**Solution**: Leverage existing SQLite with FTS5 + enhanced Arabic tokenization

## Files to Avoid

Do not attempt to add back:
- `kazma-memory/kazma_memory/tantivy_backend.py`
- `kazma-memory/kazma_memory/migration.py`
- `kazma-memory/kazma_memory/benchmark.py`
- `kazma-memory/kazma_memory/report_store.py`
- `tests/test_tantivy_backend.py`
- `tests/test_migration.py`

These were intentionally removed as part of the architectural decision.

## Current Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Kazma Agent Memory                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────────┐      ┌──────────────────────────┐   │
│  │ SearchBackend    │◄────►│  SQLiteMemoryBackend    │   │
│  │ (Simple Router)  │      │  (FTS5 + sqlite-vec)     │   │
│  └──────────────────┘      └──────────────────────────┘   │
│         │                        │                         │
│         └─────────────────────────────────────────────┘    │
│                    │                                       │
│                    ▼                                       │
│           ArabicTokenizer                                  │
│           (Kuwaiti + MSA)                                 │
└─────────────────────────────────────────────────────────────┘
```

**Key Principle**: SQLite-only, edge-optimized, zero external dependencies for search.
