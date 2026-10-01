"""Workspace file walk for the code index — skip venvs, git, caches."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from kazma_core.workspace.project_files import GENERATED_DIRS as SKIP_DIRS

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


def _walked(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for name in filenames:
            yield Path(dirpath) / name


def _candidates(root: Path) -> Iterator[Path]:
    """The project's own files when git knows *root*, else the pruned walk.

    The walk entered every folder not on the skip list, a repository's
    ignored ones included: on the live install (the workspace is the install
    folder) a 4.7 GB clone sorted before Kazma's own packages and could take
    the whole 4,000-file budget (2026-10-02).
    """
    from kazma_core.workspace import project_files

    listed = project_files.git_project_files(root)
    if listed is None:
        yield from _walked(root)
        return
    for p in sorted(listed):
        try:
            parts = p.relative_to(root).parts
        except ValueError:
            continue
        if not any(should_skip_dir(part) for part in parts[:-1]):
            yield p


def iter_source_files(root: Path, *, limit: int = MAX_FILES) -> Iterator[Path]:
    """Yield source files under *root*, skipping junk directories."""
    root = root.resolve()
    n = 0
    for p in _candidates(root):
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
