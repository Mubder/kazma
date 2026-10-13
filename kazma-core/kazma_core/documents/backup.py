"""Consistent native backup of the document store (DB + content tree).

Phase 9 backup. The document store is a SQLite metadata DB (``documents.db``)
plus an immutable content-addressed tree (``quarantine/`` / ``originals/`` /
``artifacts/`` / ``manifests/``). A naive copy risks a **torn snapshot** where
the DB references a blob the copy missed.

Consistency strategy (no publication quiesce required):

1. Snapshot ``documents.db`` first via ``sqlite3.backup()`` (holds a read
   lock → a consistent point-in-time). Blob *files* are always written before
   their DB *rows* (``put_stream`` → ``register_blob``), so every blob the
   snapshot references already exists on disk at snapshot time.
2. Copy the referenced content **after** the DB snapshot. Referenced blobs are
   immutable and GC never removes a referenced blob, so each is still present.
   The copy is a superset (extra newer blobs are harmless).
3. **Verify**: re-open the snapshot DB, enumerate every referenced
   ``(sha256, storage_kind)`` and manifest ``(document, version)``, and confirm
   each is present in the backup with a matching checksum. The report lists any
   missing item truthfully (there should be none).

Backups land in :func:`kazma_core.paths.backups_dir` under a timestamped
``document-store-<ts>/`` directory with a ``manifest.json`` of file counts +
checksums.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "installed_document_backends",
    "perform_document_backup",
    "promoted_versions_from_postgres",
    "verify_document_backup",
]

_KIND_DIRS = ("quarantine", "originals", "artifacts")
_COPY_CHUNK = 1 << 20


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_COPY_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _blob_rel_path(kind: str, sha: str) -> Path:
    return Path(kind) / "sha256" / sha[:2] / sha[2:4] / sha


def installed_document_backends() -> tuple[str, str]:
    """``(metadata, jobs)``: where the document store serves from.

    Each is ``"sqlite"`` or ``"postgres"``. The running service's own
    repositories when there is one (a fallback is what serves), else the
    configured backends -- a CLI process (``kazma migrate``) has no service,
    and judging by the service alone let it treat a Postgres store as an
    empty local one (DOC-A7, 2026-10-13).
    """
    from .ingestion import peek_ingestion_service
    from .jobs_pg import PostgresDocumentJobRepository, document_jobs_backend, document_metadata_backend

    installed = peek_ingestion_service()
    if installed is not None:
        metadata = "sqlite" if getattr(installed.repository, "backend_name", "sqlite") == "sqlite" else "postgres"
        jobs = "postgres" if isinstance(installed.jobs, PostgresDocumentJobRepository) else "sqlite"
        return metadata, jobs
    from kazma_core.db.backend import get_database_url

    if not get_database_url():
        return "sqlite", "sqlite"
    return document_metadata_backend(), document_jobs_backend()


#: The one shape no backup can hold: metadata on Postgres, jobs on SQLite (a
#: fallback). The coordinated backup's snapshot cannot include a SQLite queue.
UNSUPPORTED_SHAPE = (
    "document_backup_backend_unsupported: document metadata is on Postgres but jobs are "
    "on SQLite (a fallback); no backup can hold both at one point. Restore Postgres for "
    "the job queue (or set KAZMA_DOCUMENTS_JOBS_BACKEND to match) and back up again"
)


def promoted_versions_from_postgres(dsn: str) -> set[str]:
    """Version ids whose source left quarantine, read from the Postgres queue.

    For SQLite metadata beside Postgres jobs (DOC-A6): the snapshot decides
    whether a source is needed from ``quarantine`` or ``originals`` by the
    job events, which are not in ``documents.db`` then. Promotions only ever
    accumulate, so reading them after the snapshot is a safe superset.
    """
    import psycopg

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT j.version_id FROM document_jobs j
               JOIN document_job_events e ON e.job_id = j.id AND e.tenant_id = j.tenant_id
               WHERE e.to_state IN ('ready_to_parse', 'ocr_required')"""
        )
        return {str(row[0]) for row in cur.fetchall()}


def _perform_installed_pg_backup(dest_dir: str | Path | None, *, retention: int) -> dict[str, Any]:
    """The installed Postgres store, through the coordinated backup (F06)."""
    from kazma_core.db.backend import get_database_url

    from .backup_pg import perform_pg_document_backup, verify_pg_document_backup
    from .config import get_document_storage_root

    dsn = get_database_url()
    if not dsn:
        return {"ok": False, "error": "document metadata is on Postgres but no database URL is set"}
    if dest_dir is None:
        from kazma_core.paths import backups_dir

        dest_dir = backups_dir()
    report = perform_pg_document_backup(dsn=dsn, storage_root=get_document_storage_root(), dest_dir=dest_dir)
    if report.get("ok"):
        verified = sorted(
            (p for p in Path(dest_dir).glob("document-pg-*")
             if p.is_dir() and verify_pg_document_backup(p)["ok"]),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in verified[max(1, int(retention)):]:
            shutil.rmtree(stale, ignore_errors=True)
    return report


def perform_document_backup(
    *,
    storage_root: str | Path | None = None,
    dest_dir: str | Path | None = None,
    retention: int = 5,
) -> dict[str, Any]:
    """Back up ``documents.db`` + referenced content to a timestamped dir.

    Returns a report dict (paths, counts, checksums, verification result).
    Best-effort: returns ``{"ok": False, ...}`` on failure rather than raising.
    """
    out: Path | None = None
    try:
        promoted: set[str] | None = None
        jobs_backend = "sqlite"
        if storage_root is None:
            # The path follows where the CATALOG is (DOC-A6, 2026-10-13): the
            # jobs backend alone sent a SQLite catalog beside a Postgres queue
            # to the coordinated backup, which read a catalog that was not
            # there (or copied stale leftover tables).
            metadata, jobs = installed_document_backends()
            if metadata == "postgres":
                if jobs != "postgres":
                    return {"ok": False, "error": UNSUPPORTED_SHAPE}
                return _perform_installed_pg_backup(dest_dir, retention=retention)
            if jobs == "postgres":
                from kazma_core.db.backend import get_database_url

                from .config import get_document_storage_root

                dsn = get_database_url()
                if not dsn:
                    return {"ok": False, "error": "document jobs are on Postgres but no database URL is set"}
                storage_root = get_document_storage_root()
                jobs_backend = "postgres"
            else:
                from .config import get_document_storage_root

                storage_root = get_document_storage_root()
        root = Path(storage_root)
        db_src = root / "documents.db"
        if not db_src.exists():
            return {"ok": True, "skipped": "no documents.db yet"}

        if dest_dir is None:
            from kazma_core.paths import backups_dir

            dest_dir = backups_dir()
        ts = int(time.time())
        name = f"document-store-{ts}-{uuid.uuid4().hex[:12]}"
        # Written under a partial name and renamed at the end: a failed run
        # leaves no folder that looks like a backup.
        out = Path(dest_dir) / f".{name}.partial"
        out.mkdir(parents=True, exist_ok=False)

        # 1) Consistent DB snapshot FIRST (read lock → point-in-time).
        from kazma_core.memory.backup import backup_one

        db_dest = out / "documents.db"
        if not backup_one(db_src, db_dest):
            shutil.rmtree(out, ignore_errors=True)
            return {"ok": False, "error": "documents.db snapshot failed"}
        if jobs_backend == "postgres":
            # After the snapshot: promotions only accumulate (a superset).
            promoted = promoted_versions_from_postgres(dsn)

        # 2) Copy content referenced by the snapshot (superset-safe).
        refs, versions = _read_references(db_dest, promoted_versions=promoted)
        checksums: dict[str, str] = {"documents.db": _sha256_file(db_dest)}
        copied = 0
        for sha, kind in refs:
            rel = _blob_rel_path(kind, sha)
            rel_key = str(rel).replace("\\", "/")
            checksums[rel_key] = sha
            src = root / rel
            if not src.is_file():
                continue
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
                copied += 1

        manifests_copied = 0
        for doc_id, ver_id in versions:
            src = root / "manifests" / doc_id / f"{ver_id}.json"
            if not src.is_file():
                continue
            dst = out / "manifests" / doc_id / f"{ver_id}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            checksums[str(dst.relative_to(out)).replace("\\", "/")] = _sha256_file(dst)
            manifests_copied += 1

        # 3) Verify + write manifest.
        verification = verify_document_backup(
            backup_dir=out, checksums=checksums, promoted_versions=promoted,
        )
        manifest = {
            "created_epoch": ts,
            "referenced_blobs": len(refs),
            "copied_blobs": copied,
            "manifests": manifests_copied,
            "checksums": checksums,
            "verification": verification,
            # Where the job queue was: with "postgres" it is not in this
            # snapshot but in the whole-database dump (pg_shared); the
            # promotions it held are kept so the backup verifies later.
            "jobs_backend": jobs_backend,
            "promoted_versions": sorted(promoted) if promoted is not None else None,
        }
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        final = out.with_name(name)
        out.rename(final)
        out = final
        # Never prune a known-good recovery point to retain a failed backup.
        if verification["ok"]:
            _prune_old(Path(dest_dir), retention=retention)
        logger.info(
            "[documents.backup] backed up documents.db + %d blob(s) → %s (verified=%s)",
            copied,
            out.name,
            verification["ok"],
        )
        return {"ok": bool(verification["ok"]), "path": str(out), **manifest}
    except Exception as exc:  # noqa: BLE001 - backup must never crash the worker
        logger.warning("[documents.backup] backup failed: %s", exc, exc_info=True)
        if out is not None and out.name.endswith(".partial"):
            shutil.rmtree(out, ignore_errors=True)
        return {"ok": False, "error": str(exc)[:200]}


def _read_references(
    db_path: Path, *, promoted_versions: set[str] | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return blobs and versions still owned by a document that is not deleted.

    A soft-deleted document can keep its version row after garbage collection
    has removed the file. That missing file must not fail a backup or a
    migrate. A blob still referenced by any live document stays required,
    including when a deleted document points at the same bytes.

    ``promoted_versions``: the versions whose source left quarantine, when
    the job queue is elsewhere (Postgres); else the snapshot's own events.
    """
    refs: list[tuple[str, str]] = []
    versions: list[tuple[str, str]] = []
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        # Inspection must not mutate the snapshot whose bytes were hashed.
        conn.execute("PRAGMA busy_timeout=5000")
        promoted: set[str] = set()
        if promoted_versions is not None:
            promoted = {row[1] for row in conn.execute(
                """SELECT v.id, v.source_sha256 FROM document_versions v
                   JOIN documents d ON d.id=v.document_id AND d.tenant_id=v.tenant_id
                   WHERE d.deleted_at IS NULL"""
            ) if str(row[0]) in promoted_versions}
        elif conn.execute("SELECT 1 FROM sqlite_master WHERE name='document_job_events'").fetchone():
            promoted = {row[0] for row in conn.execute(
                """SELECT DISTINCT v.source_sha256 FROM document_versions v
                   JOIN documents d ON d.id=v.document_id AND d.tenant_id=v.tenant_id
                   JOIN document_jobs j ON j.version_id=v.id AND j.tenant_id=v.tenant_id
                   JOIN document_job_events e ON e.job_id=j.id AND e.tenant_id=j.tenant_id
                   WHERE d.deleted_at IS NULL AND e.to_state IN ('ready_to_parse','ocr_required')"""
            )}
        for row in conn.execute(
            """
            SELECT DISTINCT b.sha256, b.storage_kind, 'source' AS origin
            FROM document_blobs b JOIN document_versions v
              ON v.source_blob_id=b.id AND v.tenant_id=b.tenant_id
            JOIN documents d ON d.id=v.document_id AND d.tenant_id=v.tenant_id
            WHERE d.deleted_at IS NULL
            UNION
            SELECT DISTINCT b.sha256, b.storage_kind, 'artifact' AS origin
            FROM document_blobs b JOIN document_artifacts a
              ON a.blob_id=b.id AND a.tenant_id=b.tenant_id
            JOIN documents d ON d.id=a.document_id AND d.tenant_id=a.tenant_id
            WHERE d.deleted_at IS NULL
            """
        ):
            kind = row["storage_kind"]
            # Intake metadata retains its original quarantine provenance.
            # The durable validated event proves that execution now requires
            # originals, while GC may legitimately remove quarantine copies.
            if row["origin"] == "source" and row["sha256"] in promoted:
                kind = "originals"
            refs.append((row["sha256"], kind))
        for row in conn.execute(
            """
            SELECT v.id AS id, v.document_id AS document_id
            FROM document_versions v
            JOIN documents d ON d.id = v.document_id
            WHERE d.deleted_at IS NULL
            """
        ):
            versions.append((row["document_id"], row["id"]))
    finally:
        conn.close()
    return sorted(set(refs)), versions


def verify_document_backup(
    *, backup_dir: str | Path, checksums: dict[str, str] | None = None,
    promoted_versions: set[str] | None = None,
) -> dict[str, Any]:
    """Verify every referenced blob is present in the backup with right checksum.

    If ``checksums`` is not supplied it is read from the backup's
    ``manifest.json``, with the promotions a Postgres queue held at backup
    time (``promoted_versions``, DOC-A6). Returns ``{"ok": bool, "checked":
    int, "missing": [...], "corrupt": [...]}``.
    """
    out = Path(backup_dir).resolve()
    invalid_inventory: list[str] = []
    if checksums is None:
        manifest_path = out / "manifest.json"
        if manifest_path.is_file():
            try:
                stored = json.loads(manifest_path.read_text(encoding="utf-8"))
                checksums = stored["checksums"]
                if promoted_versions is None and stored.get("promoted_versions") is not None:
                    promoted_versions = {str(v) for v in stored["promoted_versions"]}
            except (ValueError, OSError, KeyError, TypeError):
                checksums = {}
                invalid_inventory.append("invalid_backup_manifest")
        else:
            checksums = {}
            invalid_inventory.append("missing_backup_manifest")
    if not isinstance(checksums, dict) or not checksums:
        checksums = {}
        invalid_inventory.append("empty_backup_inventory")
    missing: list[str] = []
    corrupt: list[str] = []
    if "documents.db" not in checksums:
        invalid_inventory.append("metadata_not_checksummed")
    # Independently derive required objects from the DB snapshot. An omitted
    # inventory entry must not make a referenced object optional.
    db_path = out / "documents.db"
    if db_path.is_file():
        try:
            refs, _versions = _read_references(db_path, promoted_versions=promoted_versions)
            required = {str(_blob_rel_path(kind, sha)).replace("\\", "/") for sha, kind in refs}
            conn = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
            try:
                has_jobs = conn.execute("SELECT 1 FROM sqlite_master WHERE name='document_jobs'").fetchone()
                if has_jobs:
                    for doc, ver in conn.execute(
                        """SELECT DISTINCT j.document_id, j.version_id
                           FROM document_jobs j JOIN documents d
                             ON d.id=j.document_id AND d.tenant_id=j.tenant_id
                           WHERE j.state='ready' AND d.deleted_at IS NULL"""
                    ):
                        required.add(f"manifests/{doc}/{ver}.json")
            finally:
                conn.close()
            for rel in required:
                if rel not in checksums:
                    invalid_inventory.append("required_object_not_checksummed:" + rel)
                if not (out / rel).is_file():
                    missing.append(rel)
        except (sqlite3.Error, OSError, ValueError):
            invalid_inventory.append("metadata_snapshot_unreadable")
    else:
        missing.append("documents.db")
    for rel, expected in checksums.items():
        if not isinstance(rel, str) or not isinstance(expected, str) or len(expected) != 64:
            invalid_inventory.append("invalid_checksum_entry")
            continue
        path = (out / rel).resolve()
        if not path.is_relative_to(out):
            invalid_inventory.append("unsafe_inventory_path")
            continue
        if not path.is_file():
            missing.append(rel)
            continue
        try:
            matches = _sha256_file(path) == expected
        except OSError:
            matches = False
        if not matches:
            corrupt.append(rel)
    return {
        "ok": not missing and not corrupt and not invalid_inventory,
        "checked": len(checksums),
        "missing": sorted(set(missing))[:50],
        "corrupt": corrupt[:50],
        "invalid_inventory": invalid_inventory[:50],
    }


def _prune_old(dest_dir: Path, *, retention: int) -> int:
    """Retain verified recovery points; failed directories never displace them."""
    parent = dest_dir.resolve()
    dirs = sorted(
        (p for p in dest_dir.glob("document-store-*")
         if p.is_dir() and p.resolve().parent == parent
         and verify_document_backup(backup_dir=p)["ok"]),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    deleted = 0
    for stale in dirs[max(1, int(retention)):]:
        try:
            shutil.rmtree(stale)
            deleted += 1
        except OSError:
            logger.warning("[documents.backup] could not prune %s", stale.name, exc_info=True)
    return deleted
