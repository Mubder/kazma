"""Live lexical search: ripgrep when present, Python walk otherwise."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from kazma_core.code_index.walk import SKIP_DIRS, iter_source_files

__all__ = ["rg_available", "search_text"]


def rg_available() -> bool:
    return shutil.which("rg") is not None


def search_text(
    root: Path,
    pattern: str,
    *,
    glob: str = "",
    limit: int = 20,
) -> list[str]:
    """Return ``path:line: snippet`` hits. Never raises."""
    pattern = (pattern or "").strip()
    if not pattern or len(pattern) > 200:
        return []
    root = root.resolve()
    rg = shutil.which("rg")
    if rg:
        hits = _rg(rg, root, pattern, glob=glob, limit=limit)
        if hits is not None:
            return _without_kazma_stores(root, hits)
    return _without_kazma_stores(root, _python_grep(root, pattern, glob=glob, limit=limit))


_HIT_PATH = re.compile(r"^(.*?):(\d+):")


def _without_kazma_stores(root: Path, hits: list[str]) -> list[str]:
    """Drop hits in Kazma's own stores and secret files (``.env``,
    ``restic.pass``): ``--hidden`` reads dot files, and the workspace can be
    the install folder (audit 2026-11, FA-001)."""
    from kazma_core.store_registry import is_kazma_store

    kept: list[str] = []
    for hit in hits:
        match = _HIT_PATH.match(hit)
        if match:
            where = Path(match.group(1))
            if is_kazma_store(where if where.is_absolute() else root / where):
                continue
        kept.append(hit)
    return kept


def _rg(
    rg: str,
    root: Path,
    pattern: str,
    *,
    glob: str,
    limit: int,
) -> list[str] | None:
    cmd: list[str] = [
        rg,
        "-n",
        "--no-heading",
        "-S",
        "-F",  # literal — codebase_search is not a regex REPL
        "-m",
        str(max(1, limit)),
        "--max-filesize",
        "500K",
        "--hidden",
    ]
    for name in SKIP_DIRS:
        cmd.extend(["-g", f"!{name}"])
    if glob:
        cmd.extend(["-g", glob])
    cmd.extend(["--", pattern, str(root)])
    try:
        proc = subprocess.run(  # noqa: S603 — rg argv, no shell
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    return lines[:limit]


def _python_grep(
    root: Path,
    pattern: str,
    *,
    glob: str,
    limit: int,
) -> list[str]:
    try:
        regex = re.compile(re.escape(pattern))
    except re.error:
        return []
    suffix = ""
    if glob.startswith("*.") and glob.count("*") == 1 and "/" not in glob:
        suffix = glob[1:]  # ".py"
    out: list[str] = []
    for path in iter_source_files(root):
        if suffix and path.suffix.lower() != suffix.lower():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _rel(root, path)
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                out.append(f"{rel}:{i}: {line.strip()}")
                if len(out) >= limit:
                    return out
    return out


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)
