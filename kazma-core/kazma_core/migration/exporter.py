"""Bundle exporter — read a live Kazma install, write a portable .zip bundle.

Entry point: :func:`export_bundle`. Reads the source installation through
Kazma's backend-agnostic data-access layer (so it works whether the source
is SQLite or Postgres) and writes a :mod:`kazma_core.migration.bundle` zip.

Strategy (v1, "SQLite-portable"):

  * **Config**: read via ``ConfigStore.export_yaml()`` (handles either backend,
    resolves vault refs to their ``cfg:`` pointers, NOT plaintext). The
    matching ``vault.db`` travels separately + encrypted.
  * **SQLite data files** (snapshots, memory_state, memory_ops, cron,
    sessions, swarm_tasks, sandbox_emails, research_sessions, pipeline_logs,
    knowledge_graph, workspaces): copied via the WAL-safe online-backup API
    (:func:`kazma_core.memory.backup._backup_one`). These are the portable
    SQLite files, copied as-is regardless of source backend.
  * **Postgres shared-state** (settings, chat_sessions, checkpoints): when
    the source backend is Postgres, these live in Postgres, NOT SQLite. v1
    reads them through their store APIs and writes SQLite copies into the
    bundle so the target can ingest them uniformly. (When the source is
    already SQLite, the file copy above already captures them.)
  * **Assets** (attachments, documents, exports, images, fonts): copied
    verbatim — they contain no embedded absolute paths.
  * **meta.env**: the source's ``KAZMA_VAULT_KEY`` + ``KAZMA_PUBLIC_URL``
    (the key is what makes vault.db decrypt on the target; public url is
    OAuth-relevant).

The manifest records per-file sha256, the vault-key fingerprint, table row
counts, the source workspace root + data dir (for path translation), and
the detected source backend.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path
from typing import Any, Callable

from kazma_core.migration.bundle import (
    BUNDLE_VERSION,
    Manifest,
    render_meta_env,
    sha256_file,
    vault_key_fingerprint,
)
from kazma_core.migration.path_rewrite import PathMap

logger = logging.getLogger(__name__)

__all__ = ["export_bundle"]

# SQLite data files to copy as-is when they exist on disk (WAL-safe copy).
# Path is the kazma_core.paths resolver function name; archive name is the
# filename to use inside the bundle's data/ dir.
_DATA_DBS: list[tuple[str, str]] = [
    ("snapshots_db", "snapshots.db"),
    ("primary_memory_db", "memory_state.db"),
    ("memory_ops_db", "memory_ops.db"),
    ("swarm_tasks_db", "swarm_tasks.db"),
    ("checkpoints_db", "checkpoints.db"),
    ("knowledge_graph_db", "knowledge_graph.db"),
    ("vault_db_path", "vault.db"),
]

# SQLite data files resolved by a plain data_dir() join (no dedicated resolver).
_DATA_DIR_DBS = (
    "chat_sessions.db",
    "cron.db",
    "sessions.db",
    "sandbox_emails.db",
    "research_sessions.db",
    "pipeline_logs.db",
)

# workspaces.db is actually a *table* inside settings.db (per
# stores/workspaces.py). We export it as its own file for path-rewrite
# clarity, by copying settings.db into workspaces.db in the bundle — BUT
# settings.db also holds the ConfigStore settings table. To keep config
# and workspaces path-rewrite concerns separate, we emit a dedicated
# workspaces.db containing ONLY the workspaces table.
_WORKSPACES_BUNDLE_NAME = "workspaces.db"
_SETTINGS_BUNDLE_NAME = "settings.db"

# Binary asset subdirs under kazma-data/ (copied verbatim, no path rewrite).
_ASSET_DIRS = ("attachments", "documents", "exports", "images", "fonts")


def export_bundle(
    out_path: str | Path,
    *,
    include_assets: bool = True,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Export the current Kazma installation into a portable bundle.

    Args:
        out_path: destination .zip path (created/overwritten).
        include_assets: include binary asset subdirs (set False for a lean
            config+data-only bundle).
        progress: optional callback receiving human-readable step messages.

    Returns:
        The Path to the written bundle.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _log = lambda msg: (logger.info("[migrate:export] %s", msg), progress(msg) if progress else None)  # noqa: E731

    from kazma_core import paths
    from kazma_core.config_store import get_config_store
    from kazma_core.db.backend import get_backend, is_postgres

    data_dir = paths.data_dir()
    source_backend = "postgres" if is_postgres() else "sqlite"
    manifest = Manifest(
        bundle_version=BUNDLE_VERSION,
        source_os=f"{platform.system()} {platform.release()}",
        source_backend=source_backend,
    )

    # ── Build a staging dir, then zip it at the end ────────────────────
    staging = data_dir / f".migrate-export-{int(time.time())}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    (staging / "data").mkdir(parents=True)

    # 1. config.yaml — full settings (vault refs preserved, not plaintext).
    _log("Exporting config (ConfigStore.export_yaml)…")
    try:
        config_yaml = get_config_store().export_yaml()
        (staging / "config.yaml").write_text(config_yaml, encoding="utf-8")
    except Exception as exc:
        logger.warning("[migrate:export] config export failed: %s", exc)

    # 2. workspaces.db — dedicated file holding ONLY the workspaces table,
    #    so path-rewrite has one clean target.
    _log("Exporting workspaces…")
    try:
        _export_workspaces(staging / "data" / _WORKSPACES_BUNDLE_NAME)
        manifest.source_workspace_root = _detect_active_workspace_root()
    except Exception as exc:
        logger.warning("[migrate:export] workspaces export failed: %s", exc)

    # 2b. settings.db — dedicated file holding ONLY the Knowledge Library
    #     tables (knowledge_libraries + knowledge_chunks), so the importer's
    #     _merge_kb_into_settings can restore the user's KB. NOT the whole
    #     settings.db (the importer treats settings.db as merge-only; swapping
    #     it whole would clobber the target's config/workspaces).
    _log("Exporting Knowledge Library…")
    try:
        _export_kb_settings(staging / "data" / _SETTINGS_BUNDLE_NAME)
    except Exception as exc:
        logger.warning("[migrate:export] Knowledge Library export failed: %s", exc)

    manifest.source_data_dir = str(data_dir)

    # 3. SQLite data files — WAL-safe online copy.
    for resolver_name, arc_name in _DATA_DBS:
        try:
            src = Path(getattr(paths, resolver_name)())
        except Exception:
            continue
        if not src.exists():
            continue
        _log(f"Copying {src.name}…")
        if _safe_copy(src, staging / "data" / arc_name):
            manifest.table_counts[arc_name] = _count_tables(src)

    # 4. Data-dir DBs (no dedicated resolver).
    for name in _DATA_DIR_DBS:
        src = data_dir / name
        if src.exists():
            _log(f"Copying {name}…")
            if _safe_copy(src, staging / "data" / name):
                manifest.table_counts[name] = _count_tables(src)

    # 5. Postgres shared-state → SQLite for portability (v1).
    #    settings.db / chat_sessions.db / checkpoints may live in Postgres.
    if is_postgres():
        _log("Source is Postgres — dumping shared-state tables to SQLite…")
        _dump_postgres_shared_state(staging, manifest, _log)

    # 6. Assets — verbatim copy (no embedded paths).
    if include_assets:
        for sub in _ASSET_DIRS:
            src = data_dir / sub
            if src.exists() and any(src.iterdir()):
                _log(f"Copying assets/{sub}/…")
                shutil.copytree(src, staging / "assets" / sub, dirs_exist_ok=True)

    # 6b. Document store — documents.db (consistent snapshot) + the immutable
    #     content-addressed tree (quarantine/originals/artifacts/manifests).
    #     The tree is content-addressed (no embedded paths), so it is copied
    #     verbatim and restored to the target's document-store root on import.
    _log("Exporting document store…")
    try:
        _export_document_store(staging, manifest, _log)
    except Exception as exc:  # noqa: BLE001 - document store is optional
        logger.warning("[migrate:export] document store export failed: %s", exc)

    # 7. meta.env — vault key (so vault.db decrypts) + public url.
    meta = {
        "KAZMA_VAULT_KEY": (os.environ.get("KAZMA_VAULT_KEY") or "").strip(),
        "KAZMA_PUBLIC_URL": (os.environ.get("KAZMA_PUBLIC_URL") or "").strip(),
        "KAZMA_DB_BACKEND_SOURCE": source_backend,
    }
    (staging / "meta.env").write_text(render_meta_env(meta), encoding="utf-8")
    manifest.vault_key_fingerprint = vault_key_fingerprint(meta["KAZMA_VAULT_KEY"])

    # 8. pathmap.json — source root/data dir for import-time translation.
    pm = PathMap()
    if manifest.source_workspace_root:
        pm.add(manifest.source_workspace_root, manifest.source_workspace_root)  # target filled on import
    (staging / "pathmap.json").write_text(pm.to_json(), encoding="utf-8")

    # 9. manifest.json — hash every staged file, then write the manifest last.
    _log("Hashing files for manifest…")
    for fpath in staging.rglob("*"):
        if fpath.is_file():
            rel = fpath.relative_to(staging).as_posix()
            manifest.file_hashes[rel] = sha256_file(fpath)
    (staging / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")

    # 10. Zip the staging dir → final bundle.
    _log(f"Compressing bundle → {out_path.name}…")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for fpath in sorted(staging.rglob("*")):
            if fpath.is_file():
                zf.write(fpath, fpath.relative_to(staging).as_posix())

    # Re-hash manifest.json after writing it (it wasn't in file_hashes yet).
    # Append-correction: write manifest hashes inclusively by re-zipping is
    # wasteful; instead, include manifest's own hash via a second pass only
    # if strict integrity demands it. For v1 the importer verifies every
    # *data* file's hash; manifest self-hash is a known small gap.

    shutil.rmtree(staging, ignore_errors=True)
    _log(f"Bundle written: {out_path} ({out_path.stat().st_size // 1024} KB)")
    return out_path


# ── Helpers ───────────────────────────────────────────────────────────────


def _safe_copy(src: Path, dest: Path) -> bool:
    """WAL-safe online copy of a SQLite DB (reuses the memory backup primitive)."""
    from kazma_core.memory.backup import _backup_one

    dest.parent.mkdir(parents=True, exist_ok=True)
    return _backup_one(src, dest)


def _count_tables(db_path: Path) -> dict[str, int]:
    """Row counts per table in a SQLite DB (for the manifest's verify report)."""
    counts: dict[str, int] = {}
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            for (name,) in rows:
                try:
                    counts[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                except sqlite3.OperationalError:
                    counts[name] = -1
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        logger.warning("[migrate:export] count failed for %s: %s", db_path.name, exc)
    return counts


def _export_workspaces(dest: Path) -> None:
    """Write a SQLite file containing only the ``workspaces`` table.

    Reads via WorkspaceStore (backend-agnostic) so it works under Postgres too.
    """
    from kazma_core.stores.workspaces import WorkspaceStore

    store = WorkspaceStore()
    workspaces = store.list_workspaces()
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    conn = sqlite3.connect(str(dest))
    try:
        conn.execute(
            """
            CREATE TABLE workspaces (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL,
                created_at TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 0,
                repo_url TEXT, owner TEXT, repo TEXT, default_branch TEXT, is_github INTEGER
            )
            """
        )
        for w in workspaces:
            conn.execute(
                "INSERT INTO workspaces "
                "(id,name,root_path,created_at,is_active,repo_url,owner,repo,default_branch,is_github) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    w["id"], w["name"], w["root_path"], w["created_at"],
                    1 if w.get("is_active") else 0,
                    w.get("repo_url"), w.get("owner"), w.get("repo"),
                    w.get("default_branch"), 1 if w.get("is_github") else (None if w.get("is_github") is None else 0),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _detect_active_workspace_root() -> str:
    """The active workspace's root_path (empty if none)."""
    try:
        from kazma_core.stores.workspaces import get_workspace_store

        active = get_workspace_store().get_active_workspace()
        return (active or {}).get("root_path", "") or ""
    except Exception:
        return ""


def _export_kb_settings(dest: Path) -> int:
    """Write a SQLite file containing ONLY the Knowledge Library tables.

    The KB (``knowledge_libraries`` + ``knowledge_chunks``) lives in SQLite
    ``settings.db`` regardless of backend — it is NOT in Postgres. Previously
    the exporter shipped no ``settings.db`` at all, so the importer's
    ``_merge_kb_into_settings`` never ran and the entire Knowledge Library
    (libraries + chunk embeddings + FTS index) was silently lost on every
    migration (audit finding).

    This emits a dedicated KB-only ``settings.db`` (created via ATTACH +
    create-as-select, so it survives any ALTER-TABLE migrations the live
    schema has accumulated). The importer recreates the canonical schema and
    copies by explicit column name, so the copy's column set just needs to be
    a superset of those columns. Crucially this is NOT the whole settings.db
    — restoring that would clobber the target's config/workspaces tables.
    """
    import sqlite3
    from kazma_core import paths as _paths

    try:
        live = _paths.settings_db()
    except Exception:
        return 0
    if not live or not Path(live).exists():
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    copied = 0
    conn = sqlite3.connect(str(dest))
    try:
        conn.execute("ATTACH DATABASE ? AS src", (live,))
        for tbl in ("knowledge_libraries", "knowledge_chunks"):
            try:
                exists = conn.execute(
                    "SELECT name FROM src.sqlite_master WHERE type='table' AND name=?",
                    (tbl,),
                ).fetchone()
                if not exists:
                    continue
                conn.execute(f"CREATE TABLE {tbl} AS SELECT * FROM src.{tbl}")
                copied += 1
            except sqlite3.OperationalError as exc:
                logger.debug("[migrate:export] KB table %s copy skipped: %s", tbl, exc)
        conn.commit()
    finally:
        try:
            conn.execute("DETACH DATABASE src")
        except Exception:  # noqa: BLE001
            pass
        conn.close()
    return copied


def _export_document_store(
    staging: Path, manifest: Manifest, _log: Callable[[str], None]
) -> None:
    """Copy documents.db (consistent snapshot) + the content tree into the bundle.

    documents.db is snapshotted FIRST (read lock → point-in-time), then the
    referenced content-addressed files are copied — every blob a snapshot row
    references already exists on disk (files precede rows), so the pair is
    consistent. The tree carries no embedded paths, so it needs no rewrite.
    """
    from kazma_core.documents.config import get_document_config

    root = Path(get_document_config().storage_root)
    db_src = root / "documents.db"
    if not db_src.exists():
        _log("  (no documents.db — skipping document store)")
        return

    dest_root = staging / "document-store"
    dest_root.mkdir(parents=True, exist_ok=True)
    # 1) Consistent DB snapshot into the bundle's data/ dir.
    db_dest = staging / "data" / "documents.db"
    if _safe_copy(db_src, db_dest):
        manifest.table_counts["documents.db"] = _count_tables(db_src)

    # 2) Copy referenced content + manifests verbatim (hashed by the manifest
    #    pass, so integrity is verified on import).
    try:
        from kazma_core.documents.backup import _blob_rel_path, _read_references

        refs, versions = _read_references(db_dest if db_dest.exists() else db_src)
        blob_count = 0
        missing_blobs: list[str] = []
        for sha, kind in refs:
            rel = _blob_rel_path(kind, sha)
            src = root / rel
            if not src.is_file():
                missing_blobs.append(str(rel).replace("\\", "/"))
                continue
            dst = dest_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            blob_count += 1
        if missing_blobs:
            sample = ", ".join(missing_blobs[:3])
            raise RuntimeError(
                "document store is incomplete; referenced blob(s) are missing: "
                f"{sample}"
            )
        manifest_count = 0
        for doc_id, ver_id in versions:
            src = root / "manifests" / doc_id / f"{ver_id}.json"
            if not src.is_file():
                continue
            dst = dest_root / "manifests" / doc_id / f"{ver_id}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            manifest_count += 1
        manifest.table_counts["_document_store"] = {
            "blobs": blob_count,
            "manifests": manifest_count,
        }
        _log(f"  document store: {blob_count} blob(s), {manifest_count} manifest(s)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[migrate:export] document content copy failed: %s", exc)
        raise


def _dump_postgres_shared_state(
    staging: Path,
    manifest: Manifest,
    _log: Callable[[str], None],
) -> None:
    """Dump the Postgres DB into the bundle as a custom-format dump file.

    v2: replaces the v1 no-op stub. When the source backend is Postgres, the
    shared-state tables (kazma_settings, kazma_chat_sessions, checkpoints,
    checkpoint_blobs, checkpoint_writes, kazma_swarm_tasks, etc.) live in
    Postgres, NOT in the on-disk SQLite files. This runs ``pg_dump -Fc`` and
    writes ``data/postgres.dump`` into the bundle so the importer can
    ``pg_restore`` it into a target Postgres.

    Best-effort at the bundle level: a failure here logs + continues, because
    the SQLite files (vault, memory, snapshots) are still valuable on their
    own. The importer will warn that the Postgres dump is missing.
    """
    try:
        from kazma_core.db.backend import get_database_url
        from kazma_core.migration.pg_bridge import PgToolNotFound, dump_database

        dsn = get_database_url() or ""
        if not dsn:
            _log("  (no KAZMA_DATABASE_URL — skipping Postgres dump)")
            return
        dest = staging / "data" / "postgres.dump"
        _log(f"  pg_dump → data/postgres.dump (this may take a minute for large DBs)…")
        written = dump_database(dsn, dest, progress=lambda p: _log(f"    {p}"))
        manifest.table_counts["_postgres_dump"] = {
            "bytes": written.stat().st_size,
            "mb": round(written.stat().st_size / (1024 * 1024), 1),
        }
        _log(f"  Postgres dump: {written.stat().st_size // (1024*1024)} MB")
    except PgToolNotFound as exc:
        # Tool not available — degrade gracefully, the SQLite files still ship.
        _log(f"  ⚠ pg_dump unavailable: {exc}")
        _log("    Postgres tables will NOT be in the bundle (SQLite files still included).")
        manifest.table_counts["_postgres_dump"] = {"skipped": "pg_dump not found"}
    except Exception as exc:
        logger.warning("[migrate:export] Postgres dump failed: %s", exc)
        _log(f"  ⚠ Postgres dump failed: {exc}")
        manifest.table_counts["_postgres_dump"] = {"error": str(exc)[:100]}
