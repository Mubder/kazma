"""Build and refresh the per-workspace symbol index."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from kazma_core.code_index.store import (
    connect,
    drop_file,
    listed_files,
    stats,
    upsert_file,
)
from kazma_core.code_index.symbols import extract_symbols
from kazma_core.code_index.walk import INDEX_EXTS, iter_source_files, lang_for_path

logger = logging.getLogger(__name__)

__all__ = ["code_index_enabled", "ensure_index", "notify_file_changed", "status"]


def code_index_enabled() -> bool:
    raw = (os.environ.get("KAZMA_CODE_INDEX") or "").strip().lower()
    return raw not in ("0", "false", "off", "no")


def _root() -> Path | None:
    try:
        from kazma_core.workspace.binding import resolve_active_root

        return resolve_active_root().resolve()
    except Exception:
        return None


def _rel(root: Path, path: Path) -> str | None:
    try:
        return str(path.resolve().relative_to(root)).replace("\\", "/")
    except ValueError:
        return None


def ensure_index(root: Path | None = None) -> dict[str, int]:
    """Stat-walk the workspace and refresh stale/missing files. Never raises."""
    if not code_index_enabled():
        return {"files": 0, "symbols": 0, "updated": 0}
    root = (root or _root())
    if root is None or not root.is_dir():
        return {"files": 0, "symbols": 0, "updated": 0}
    conn = connect(root)
    updated = 0
    try:
        known = listed_files(conn)
        seen: set[str] = set()
        for path in iter_source_files(root):
            rel = _rel(root, path)
            if not rel:
                continue
            seen.add(rel)
            try:
                st = path.stat()
            except OSError:
                continue
            prev = known.get(rel)
            if prev and prev[0] == st.st_mtime and prev[1] == st.st_size:
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            syms = extract_symbols(path, source)
            upsert_file(
                conn,
                rel,
                mtime=st.st_mtime,
                size=st.st_size,
                symbols=syms,
                lang=lang_for_path(path),
            )
            updated += 1
        for rel in set(known) - seen:
            drop_file(conn, rel)
        conn.commit()
        out = stats(conn)
        out["updated"] = updated
        return out
    except Exception:
        logger.debug("[code_index] ensure_index failed", exc_info=True)
        return {"files": 0, "symbols": 0, "updated": 0}
    finally:
        conn.close()


def notify_file_changed(path: str | Path, *, deleted: bool = False) -> None:
    """Best-effort incremental update after a workspace write/delete."""
    if not code_index_enabled():
        return
    try:
        root = _root()
        if root is None:
            return
        p = Path(path).expanduser().resolve()
        rel = _rel(root, p)
        if not rel:
            return
        conn = connect(root)
        try:
            if deleted or not p.is_file():
                drop_file(conn, rel)
            elif p.suffix.lower() in INDEX_EXTS:
                st = p.stat()
                source = p.read_text(encoding="utf-8", errors="replace")
                upsert_file(
                    conn,
                    rel,
                    mtime=st.st_mtime,
                    size=st.st_size,
                    symbols=extract_symbols(p, source),
                    lang=lang_for_path(p),
                )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("[code_index] notify_file_changed failed", exc_info=True)


def status(root: Path | None = None) -> dict[str, object]:
    from kazma_core.code_index.ripgrep import rg_available
    from kazma_core.code_index.symbols import tree_sitter_available

    enabled = code_index_enabled()
    info: dict[str, object] = {
        "enabled": enabled,
        "ripgrep": rg_available(),
        "tree_sitter": tree_sitter_available(),
        "files": 0,
        "symbols": 0,
    }
    if not enabled:
        return info
    root = root or _root()
    if root is None:
        return info
    try:
        conn = connect(root)
        try:
            info.update(stats(conn))
        finally:
            conn.close()
    except Exception:
        pass
    return info
