"""Coordinated backup and restore of a Postgres-backed document store.

2026-10-13 (F06). Native document backup was SQLite-only: on Postgres
metadata it refused, and the Postgres dump and the content files were copied
by separate jobs at different times, so nothing proved that the files a dump
references were in the backup beside it.

One point in time for both halves:

1. A ``REPEATABLE READ`` transaction exports its snapshot
   (``pg_export_snapshot``) and, inside that snapshot, reads which content
   blobs and manifests the document tables reference.
2. Those blobs are PINNED (``<storage root>/.backup-pins``): garbage
   collection re-checks the pins before it deletes, so a blob the snapshot
   references survives until it is copied even if it was unreferenced since.
3. ``pg_dump --snapshot`` dumps the document tables AS OF that snapshot
   while the exporting transaction stays open.
4. The referenced blobs and manifests are copied with checksums; the
   snapshot's row counts, time and WAL position are recorded.

Verification re-derives nothing from the live store: the backup's own
manifest names every required object and checksum. A restore goes into an
EMPTY target database and storage root, re-checks every table's row count
against the snapshot, and prepares the restored state for a new process:
leases held by the dead source expire at once, and intake reservations the
source never finished are marked as restored so the recovery rolls them
back (their publisher can never resume against this database).

RPO is the time between the snapshot and the previous successful backup
(the schedule); the backup records the snapshot time so the loss window is
known exactly. RTO is measured by the restore drill
(``tests/test_document_pg_backup_restore.py``), not asserted here.

The knowledge index (library chunks in the settings store, vectors in
Chroma) is a separate store and is NOT in this backup: a restored document
that was indexed keeps its ``document_chunks`` provenance, and the service
re-publishes it from its own manifest at the next start
(``DocumentIngestionService.reconcile_index``); the restore report lists
them under ``reindex_required``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DOCUMENT_PG_TABLES",
    "backup_pins",
    "perform_pg_document_backup",
    "restore_pg_document_backup",
    "verify_pg_document_backup",
]

FORMAT = "kazma-document-pg-backup/1"
#: Every Postgres table the document store owns (a subset of KAZMA_PG_TABLES).
DOCUMENT_PG_TABLES = (
    "documents",
    "document_blobs",
    "document_versions",
    "document_artifacts",
    "document_acl",
    "document_tombstones",
    "document_chunks",
    "document_audit_events",
    "document_jobs",
    "document_job_events",
    "document_intake_reservations",
    "document_intake_mutex",
)
_PIN_DIR = ".backup-pins"
#: A pin older than this was left by a backup that died; it protects nothing.
_PIN_MAX_AGE_SECONDS = 24 * 3600
_CHUNK = 1 << 20
_ACTIVE_STATES = ("validating", "parsing", "ocr_running", "normalizing", "indexing", "verifying")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _blob_rel(kind: str, sha: str) -> str:
    return f"{kind}/sha256/{sha[:2]}/{sha[2:4]}/{sha}"


# ── garbage-collection pins ────────────────────────────────────────────────


def backup_pins(storage_root: str | Path, *, now: float | None = None) -> set[str]:
    """Relative paths (blobs and manifests) a running backup still needs.

    Garbage collection asks this immediately before each deletion.
    """
    base = Path(storage_root) / _PIN_DIR
    if not base.is_dir():
        return set()
    cutoff = (now if now is not None else time.time()) - _PIN_MAX_AGE_SECONDS
    pinned: set[str] = set()
    for entry in base.glob("*.json"):
        try:
            if entry.stat().st_mtime < cutoff:
                continue
            pinned.update(json.loads(entry.read_text(encoding="utf-8")).get("paths", []))
        except (OSError, ValueError, AttributeError):
            continue  # unreadable pin: the backup's own verification still decides
    return pinned


def _write_pin(storage_root: Path, backup_id: str, paths: list[str]) -> Path:
    base = storage_root / _PIN_DIR
    base.mkdir(parents=True, exist_ok=True)
    pin = base / f"{backup_id}.json"
    temp = pin.with_suffix(".tmp")
    temp.write_text(json.dumps({"paths": sorted(paths)}), encoding="utf-8")
    temp.replace(pin)
    return pin


# ── backup ─────────────────────────────────────────────────────────────────


def _snapshot_references(cur: Any) -> dict[str, Any]:
    """What the snapshot's live documents need, read inside the snapshot."""
    cur.execute(
        """SELECT DISTINCT v.source_sha256 AS sha
           FROM document_versions v
           JOIN documents d ON d.id = v.document_id AND d.tenant_id = v.tenant_id
           JOIN document_jobs j ON j.version_id = v.id AND j.tenant_id = v.tenant_id
           JOIN document_job_events e ON e.job_id = j.id AND e.tenant_id = j.tenant_id
           WHERE d.deleted_at IS NULL AND e.to_state IN ('ready_to_parse', 'ocr_required')"""
    )
    promoted = {row["sha"] for row in cur.fetchall()}
    cur.execute(
        """SELECT DISTINCT b.sha256 AS sha, b.storage_kind AS kind, 'source' AS origin
           FROM document_blobs b
           JOIN document_versions v ON v.source_blob_id = b.id AND v.tenant_id = b.tenant_id
           JOIN documents d ON d.id = v.document_id AND d.tenant_id = v.tenant_id
           WHERE d.deleted_at IS NULL
           UNION
           SELECT DISTINCT b.sha256 AS sha, b.storage_kind AS kind, 'artifact' AS origin
           FROM document_blobs b
           JOIN document_artifacts a ON a.blob_id = b.id AND a.tenant_id = b.tenant_id
           JOIN documents d ON d.id = a.document_id AND d.tenant_id = a.tenant_id
           WHERE d.deleted_at IS NULL"""
    )
    blobs: set[str] = set()
    for row in cur.fetchall():
        kind = row["kind"]
        # Intake keeps quarantine provenance on the row; once validated the
        # execution needs ``originals`` (the SQLite backup's rule too).
        if row["origin"] == "source" and row["sha"] in promoted:
            kind = "originals"
        blobs.add(_blob_rel(kind, row["sha"]))
    cur.execute(
        """SELECT v.document_id AS doc, v.id AS ver FROM document_versions v
           JOIN documents d ON d.id = v.document_id AND d.tenant_id = v.tenant_id
           WHERE d.deleted_at IS NULL"""
    )
    manifests = {f"manifests/{row['doc']}/{row['ver']}.json" for row in cur.fetchall()}
    cur.execute(
        """SELECT DISTINCT j.document_id AS doc, j.version_id AS ver FROM document_jobs j
           JOIN documents d ON d.id = j.document_id AND d.tenant_id = j.tenant_id
           WHERE j.state = 'ready' AND d.deleted_at IS NULL"""
    )
    required_manifests = {f"manifests/{row['doc']}/{row['ver']}.json" for row in cur.fetchall()}
    return {"blobs": blobs, "manifests": manifests, "required_manifests": required_manifests}


def _table_counts(cur: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in DOCUMENT_PG_TABLES:
        cur.execute("SELECT to_regclass(%s) AS reg", (table,))
        if cur.fetchone()["reg"] is None:
            continue
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608 - fixed allowlist
        counts[table] = int(cur.fetchone()["n"])
    return counts


def perform_pg_document_backup(
    *,
    dsn: str,
    storage_root: str | Path,
    dest_dir: str | Path,
    dump: Callable[..., Path] | None = None,
) -> dict[str, Any]:
    """Back up the Postgres document tables and their content as one point.

    Returns a report; ``ok`` is the backup's own verification. Never raises
    for an operational failure: the report says what failed.
    """
    import psycopg
    from psycopg.rows import dict_row

    from kazma_core.db.pg_helpers import store_errors
    from kazma_core.migration.pg_bridge import dump_database

    dump = dump or dump_database
    root = Path(storage_root)
    backup_id = f"document-pg-{int(time.time())}-{uuid.uuid4().hex[:12]}"
    final = Path(dest_dir) / backup_id
    # Written under a partial name and renamed at the end (DOC-A6): a run that
    # failed before or while connecting left an empty folder named like a backup.
    out = Path(dest_dir) / f".{backup_id}.partial"
    started = time.monotonic()
    pin: Path | None = None
    try:
        out.mkdir(parents=True)
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute("SELECT pg_export_snapshot() AS id, now() AS at, pg_current_wal_lsn()::text AS lsn")
                snapshot = cur.fetchone()
                refs = _snapshot_references(cur)
                counts = _table_counts(cur)
            needed = sorted(refs["blobs"] | refs["manifests"])
            pin = _write_pin(root, backup_id, needed)
            # The exporting transaction stays open while pg_dump imports it.
            dump_path = dump(
                dsn, out / "documents.pgdump", tables=list(counts), snapshot=str(snapshot["id"])
            )
            conn.rollback()
        checksums: dict[str, str] = {"documents.pgdump": _sha256_file(dump_path)}
        missing: list[str] = []
        for rel in needed:
            src = root / rel
            if not src.is_file():
                if rel in refs["blobs"] or rel in refs["required_manifests"]:
                    missing.append(rel)
                continue
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            checksums[rel] = _sha256_file(dst)
        manifest = {
            "format": FORMAT,
            "backup_id": backup_id,
            "snapshot_at": snapshot["at"].isoformat(),
            "snapshot_lsn": snapshot["lsn"],
            "finished_at_epoch": time.time(),
            "duration_seconds": round(time.monotonic() - started, 3),
            "table_counts": counts,
            "required": sorted(refs["blobs"] | refs["required_manifests"]),
            "checksums": checksums,
            "missing_at_backup": missing,
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        verification = verify_pg_document_backup(out)
        manifest["verification"] = verification
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        out.rename(final)
        out = final
        logger.info(
            "[documents.backup_pg] %s: %d table(s), %d object(s), verified=%s",
            backup_id, len(counts), len(checksums) - 1, verification["ok"],
        )
        return {"ok": bool(verification["ok"]), "path": str(out), **manifest}
    except store_errors() as exc:  # a backup reports, never crashes its scheduler
        logger.warning("[documents.backup_pg] backup failed: %s", type(exc).__name__, exc_info=True)
        if out != final:
            shutil.rmtree(out, ignore_errors=True)
        return {"ok": False, "path": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    finally:
        if pin is not None:
            pin.unlink(missing_ok=True)


def verify_pg_document_backup(backup_dir: str | Path) -> dict[str, Any]:
    """Every required object present, every checksum matching, a real dump."""
    out = Path(backup_dir).resolve()
    problems: list[str] = []
    try:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "problems": ["manifest_unreadable"], "missing": [], "corrupt": []}
    if manifest.get("format") != FORMAT:
        problems.append("unknown_format")
    checksums = manifest.get("checksums") or {}
    if "documents.pgdump" not in checksums:
        problems.append("dump_not_checksummed")
    missing = [rel for rel in manifest.get("required", []) if rel not in checksums]
    missing += list(manifest.get("missing_at_backup") or [])
    corrupt: list[str] = []
    for rel, expected in checksums.items():
        path = (out / rel).resolve()
        if not path.is_relative_to(out):
            problems.append("unsafe_inventory_path")
            continue
        if not path.is_file():
            missing.append(rel)
            continue
        if _sha256_file(path) != expected:
            corrupt.append(rel)
    dump = out / "documents.pgdump"
    if dump.is_file():
        with dump.open("rb") as handle:
            if handle.read(5) != b"PGDMP":
                problems.append("dump_not_custom_format")
    return {
        "ok": not problems and not missing and not corrupt,
        "problems": problems,
        "missing": sorted(set(missing))[:50],
        "corrupt": corrupt[:50],
    }


# ── restore ────────────────────────────────────────────────────────────────


def restore_pg_document_backup(
    backup_dir: str | Path,
    *,
    target_dsn: str,
    storage_root: str | Path,
    restore: Callable[..., int] | None = None,
    replace_document_tables: bool = False,
) -> dict[str, Any]:
    """Restore into an EMPTY database and storage root; raise on any failure.

    Refuses a non-empty target: a restore never merges into live data.

    ``replace_document_tables`` (DOC-A10, 2026-10-13) is the drilled order for
    restoring BOTH Postgres backups into one database: the whole-database dump
    (``pg_shared``) first -- it brings Kazma's other tables, and document
    tables older than this backup's content -- then this restore, which drops
    those document tables in one transaction and puts its own copy in. Only
    after the backup has verified and the storage root was found empty, so a
    live store (whose root is never empty) is never touched.
    """
    import psycopg
    from psycopg.rows import dict_row

    from kazma_core.migration.pg_bridge import restore_database

    restore = restore or restore_database
    source = Path(backup_dir)
    verification = verify_pg_document_backup(source)
    if not verification["ok"]:
        raise RuntimeError(f"document backup failed verification: {verification}")
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    root = Path(storage_root)
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("restore target storage root is not empty")
    replaced: list[str] = []
    if replace_document_tables:
        with psycopg.connect(target_dsn, row_factory=dict_row) as conn:
            cur = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(%s)",
                (list(DOCUMENT_PG_TABLES),),
            )
            replaced = sorted(row["table_name"] for row in cur.fetchall())
            if replaced:
                # Names come from DOCUMENT_PG_TABLES (a constant), never input.
                conn.execute("DROP TABLE IF EXISTS " + ", ".join(replaced) + " CASCADE")  # noqa: S608
            conn.commit()
        logger.info("[documents.backup_pg] replaced %d older document table(s) before the restore", len(replaced))
    with psycopg.connect(target_dsn, row_factory=dict_row) as conn:
        cur = conn.execute(
            "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (list(DOCUMENT_PG_TABLES),),
        )
        if int(cur.fetchone()["n"]):
            raise RuntimeError("restore target database already has document tables")
    timings: dict[str, float] = {}
    started = time.monotonic()
    restore(source / "documents.pgdump", target_dsn, strict=True)
    timings["database_seconds"] = round(time.monotonic() - started, 3)
    files_started = time.monotonic()
    root.mkdir(parents=True, exist_ok=True)
    for rel in manifest["checksums"]:
        if rel == "documents.pgdump":
            continue
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, dst)
    timings["files_seconds"] = round(time.monotonic() - files_started, 3)
    with psycopg.connect(target_dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            restored_counts = _table_counts(cur)
            if restored_counts != manifest["table_counts"]:
                raise RuntimeError(
                    f"restored row counts differ from the snapshot: {restored_counts} != {manifest['table_counts']}"
                )
            # The source process is gone for good: its leases end now, and its
            # unfinished intakes are rolled back by the recovery at start.
            active = ",".join(f"'{state}'" for state in _ACTIVE_STATES)
            cur.execute(
                f"UPDATE document_jobs SET lease_expires_at = now() - interval '1 second' "  # noqa: S608
                f"WHERE lease_owner IS NOT NULL AND state IN ({active})"
            )
            leases = cur.rowcount
            reservations = 0
            cur.execute("SELECT to_regclass('document_intake_reservations') AS reg")
            if cur.fetchone()["reg"] is not None:
                cur.execute(
                    "UPDATE document_intake_reservations SET owner = %s WHERE active = 1",
                    (json.dumps({"restored": manifest["backup_id"]}, sort_keys=True),),
                )
                reservations = cur.rowcount
            reindex: list[dict[str, str]] = []
            if "document_chunks" in restored_counts:
                cur.execute("SELECT DISTINCT tenant_id, document_id, version_id FROM document_chunks")
                reindex = [{k: str(v) for k, v in row.items()} for row in cur.fetchall()]
        conn.commit()
    timings["total_seconds"] = round(time.monotonic() - started, 3)
    return {
        "ok": True,
        "backup_id": manifest["backup_id"],
        "snapshot_at": manifest["snapshot_at"],
        "table_counts": restored_counts,
        "expired_leases": leases,
        "restored_reservations": reservations,
        "reindex_required": reindex,
        "replaced_document_tables": replaced,
        "timings": timings,
    }
