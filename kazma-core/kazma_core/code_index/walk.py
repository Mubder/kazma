"""Workspace file walk for the code index — skip venvs, git, caches."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

SKIP_DIRS: frozenset[str] = frozenset({
    ".venv", "venv", ".git", "node_modules", "__pycache__",
    ".kazma", "kazma-data", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "build", "dist", ".tox", ".eggs",
    "vector_memory", "site-packages", ".idea", ".vs",
    "target", "coverage", ".next", ".turbo",
})

INDEX_EXTS: frozenset[str] = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".cs", ".rb", ".php", ".swift", ".vue", ".svelte",
    ".toml", ".yaml", ".yml", ".json", ".sql",
})

MAX_FILE_BYTES = 500_000
MAX_FILES = 4000


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".")


def iter_source_files(root: Path, *, limit: int = MAX_FILES) -> Iterator[Path]:
    """Yield source files under *root*, skipping junk directories."""
    root = root.resolve()
    n = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for name in filenames:
            if n >= limit:
                return
            p = Path(dirpath) / name
            if p.suffix.lower() not in INDEX_EXTS:
                continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            n += 1
            yield p


def lang_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".py": "python", ".pyi": "python",
        ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript",
        ".go": "go", ".rs": "rust",
        ".java": "java", ".kt": "kotlin",
        ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
        ".cs": "csharp", ".rb": "ruby", ".php": "php", ".swift": "swift",
        ".vue": "vue", ".svelte": "svelte",
        ".toml": "toml", ".yaml": "yaml", ".yml": "yaml",
        ".json": "json", ".sql": "sql",
    }.get(ext, "text")
