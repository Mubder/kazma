"""Workspace codebase index: tree-sitter/regex symbols + live ripgrep."""

from __future__ import annotations

from kazma_core.code_index.indexer import (
    code_index_enabled,
    ensure_index,
    notify_file_changed,
    status,
)
from kazma_core.code_index.search import format_search, search_codebase

__all__ = [
    "code_index_enabled",
    "ensure_index",
    "format_search",
    "notify_file_changed",
    "search_codebase",
    "status",
]
