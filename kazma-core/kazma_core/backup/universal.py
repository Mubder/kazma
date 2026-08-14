"""Universal backup — back up EVERYTHING in kazma-data + Postgres.

The one-shot backup that "never leaves anything behind": every SQLite DB
(WAL-safe via the Online Backup API), every asset/file/dir, and a Postgres
dump when configured. Triggered by the 24h auto loop (via the
``native_backup`` handler) and manually from the Backup UI / API.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["perform_universal_backup", "list_universal_backups", "latest_universal_backup"]

# Directories/patterns to EXCLUDE from the backup (never copy these).
_EXCLUDE_DIRS = frozenset({"backups", "__pycache__", ".git", "node_modules", ".tmp"})
_EXCLUDE_SUFFIXES = (".pyc", "-wal", "-shm", "-journal", ".tmp")
_DEFAULT_RETENTION = 7


def _data_dir() -> Path:
    from kazma_core.paths import data_dir

    return Path(data_dir())


def _universal_dir() -> Path:
    return _data_dir() / "backups" / "universal"


def _should_exclude(name: str, is_dir: bool = False) -> bool:
    if name in _EXCLUDE_DIRS:
        return True
    return any(name.endswith(suf) for suf in _EXCLUDE_SUFFIXES)


def _backup_one_db(src: Path, dest: Path) -> bool:
    """WAL-safe copy of a single SQLite database via the Online Backup API."""
    try:
        with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dest)) as d:
            s.backup(d, pages=100, sleep=0.01)
        return True
    except Exception as exc:
        logger.warning("[universal-backup] DB copy failed %s: %s", src.name, exc)
        return False


def _copy_assets(src: Path, dest: Path) -> list[dict[str, Any]]:
    """Copy all non-DB files and directories (recursive) excluding backups."""
    copied: list[dict[str, Any]] = []
    for item in sorted(src.iterdir()):
        if _should_exclude(item.name, is_dir=item.is_dir()):
            continue
        # Skip *.db files — they're handled by _backup_dbs.
        if item.is_file() and item.suffix == ".db":
            continue
        rel = item.name
        target = dest / rel
        try:
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(*_EXCLUDE_DIRS,
                                                              *_EXCLUDE_SUFFIXES))
                # Count files inside
                count = sum(1 for _ in target.rglob("*") if _.is_file())
                copied.append({"path": rel, "type": "dir", "files": count})
            else:
                shutil.copy2(item, target)
                copied.append({"path": rel, "type": "file", "size": item.stat().st_size})
        except Exception as exc:
            logger.warning("[universal-backup] asset copy failed %s: %s", rel, exc)
            copied.append({"path": rel, "type": "error", "error": str(exc)[:200]})
    return copied


def _backup_postgres(dest_dir: Path) -> dict[str, Any] | None:
    """Delegate to pg_backup for a Postgres dump, then copy into the universal folder."""
    try:
        from kazma_core.db.pg_backup import pg_backup_enabled, perform_pg_backup

        if not pg_backup_enabled():
            return None
        dump_path = perform_pg_backup()
        if dump_path is None:
            return {"ok": False, "error": "pg_dump produced no file"}
        pg_dest = dest_dir / "postgres.dump"
        shutil.copy2(dump_path, pg_dest)
        return {"ok": True, "path": "postgres.dump", "size": pg_dest.stat().st_size}
    except Exception as exc:
        logger.warning("[universal-backup] Postgres dump failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}


def _prune(retention: int) -> int:
    """Delete oldest universal backups beyond retention. Returns count deleted."""
    base = _universal_dir()
    backups = sorted(base.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    deleted = 0
    for stale in backups[retention:]:
        try:
            shutil.rmtree(stale)
            deleted += 1
        except Exception:
            logger.debug("[universal-backup] could not prune %s", stale.name, exc_info=True)
    return deleted


def perform_universal_backup(*, retention: int = _DEFAULT_RETENTION) -> dict[str, Any]:
    """Back up EVERYTHING: all kazma-data SQLite DBs + assets + Postgres dump.

    Creates ``kazma-data/backups/universal/<timestamp>/`` containing:
    - ``dbs/`` — all ``*.db`` files (WAL-safe via sqlite3.backup API)
    - ``assets/`` — all non-DB files/dirs (attachments, document-store,
      workspace, exports, etc.)
    - ``postgres.dump`` — Postgres shared-state tables (when configured)
    - ``manifest.json`` — itemised listing with sizes

    Returns a summary dict. Never raises — the 24h loop depends on this.
    """
    started = time.time()
    ts = int(time.time())
    data = _data_dir()
    dest = _universal_dir() / str(ts)
    (dest / "dbs").mkdir(parents=True, exist_ok=True)
    (dest / "assets").mkdir(parents=True, exist_ok=True)

    # 1. All SQLite databases (WAL-safe).
    db_results: list[dict[str, Any]] = []
    db_files = sorted(data.rglob("*.db"))
    # Exclude any .db inside backups/ or excluded dirs.
    db_files = [f for f in db_files if not any(p in _EXCLUDE_DIRS for p in f.parts)]
    db_ok = db_fail = 0
    for db in db_files:
        rel = db.relative_to(data)
        db_dest = dest / "dbs" / rel
        db_dest.parent.mkdir(parents=True, exist_ok=True)
        if _backup_one_db(db, db_dest):
            db_ok += 1
            db_results.append({"path": str(rel), "size": db_dest.stat().st_size})
        else:
            db_fail += 1
            db_results.append({"path": str(rel), "error": "backup failed"})

    # 2. All non-DB assets (attachments, document-store, workspace, etc.).
    asset_results = _copy_assets(data, dest / "assets")

    # 3. Postgres dump (if configured).
    pg_result = _backup_postgres(dest)

    # 4. Manifest.
    elapsed = round(time.time() - started, 1)
    manifest: dict[str, Any] = {
        "timestamp": ts,
        "version": 1,
        "elapsed_seconds": elapsed,
        "databases": {"ok": db_ok, "failed": db_fail, "items": db_results},
        "assets": asset_results,
        "postgres": pg_result,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # 5. Prune old backups.
    pruned = _prune(max(1, retention))

    total_size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    size_mb = round(total_size / (1024 * 1024), 1)
    summary = {
        "ok": True,
        "backup_dir": str(dest),
        "timestamp": ts,
        "databases_ok": db_ok,
        "databases_failed": db_fail,
        "assets_copied": len(asset_results),
        "postgres": pg_result.get("ok") if pg_result else None,
        "total_size_mb": size_mb,
        "elapsed_seconds": elapsed,
        "pruned": pruned,
    }
    logger.info(
        "[universal-backup] complete: %d DBs, %d asset groups, %.1f MB, %.1fs, pruned %d",
        db_ok, len(asset_results), size_mb, elapsed, pruned,
    )
    return summary


def list_universal_backups() -> list[dict[str, Any]]:
    """List all universal backups (newest first) for the UI."""
    base = _universal_dir()
    if not base.is_dir():
        return []
    backups: list[dict[str, Any]] = []
    for d in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        manifest_path = d / "manifest.json"
        entry: dict[str, Any] = {
            "dir": d.name,
            "timestamp": int(d.name) if d.name.isdigit() else 0,
            "size_mb": round(
                sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / (1024 * 1024), 1
            ),
        }
        if manifest_path.is_file():
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
                entry["databases_ok"] = m.get("databases", {}).get("ok", 0)
                entry["databases_failed"] = m.get("databases", {}).get("failed", 0)
                entry["assets"] = len(m.get("assets", []))
                entry["postgres"] = (m.get("postgres") or {}).get("ok", False)
                entry["elapsed"] = m.get("elapsed_seconds", 0)
            except Exception:
                pass
        backups.append(entry)
    return backups


def latest_universal_backup() -> dict[str, Any] | None:
    """Return the newest universal backup entry, or None."""
    items = list_universal_backups()
    return items[0] if items else None
