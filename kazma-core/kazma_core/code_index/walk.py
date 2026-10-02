"""Workspace file walk for the code index — skip venvs, git, caches."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from kazma_core.workspace.project_files import GENERATED_DIRS as SKIP_DIRS
from kazma_core.workspace.project_files import iter_project_files
from kazma_core.workspace.project_files import skipped_dir as should_skip_dir

__all__ = [
    "INDEX_EXTS", "MAX_FILES", "MAX_FILE_BYTES", "SKIP_DIRS",
    "iter_source_files", "lang_for_path", "should_skip_dir",
]

INDEX_EXTS: frozenset[str] = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".cs", ".rb", ".php", ".swift", ".vue", ".svelte",
    ".toml", ".yaml", ".yml", ".json", ".sql",
})

MAX_FILE_BYTES = 500_000
MAX_FILES = 4000


def iter_source_files(root: Path, *, limit: int = MAX_FILES) -> Iterator[Path]:
    """Yield source files under *root*, skipping junk directories."""
    root = root.resolve()
    n = 0
    for p in iter_project_files(root):
        if n >= limit:
            return
        if p.suffix.lower() not in INDEX_EXTS:
            continue
        try:
            if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
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
