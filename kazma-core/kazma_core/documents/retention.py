"""Retention policy and crash-safe garbage collection for the document store.

Phase 9 retention. The **database is the authority**: a physical blob file or
manifest is deleted only when no live database reference requires it and the
file is older than the safety grace period. The collector is a *mark/sweep*:

* **Mark** — a read snapshot of the live reference sets (source shas, artifact
  shas, version ids, referenced blob-row ids) computed from the metadata DB.
  This is the authoritative "mark"; it is re-derivable, so a crash mid-sweep
  is harmless — re-running recomputes and continues.
* **Sweep** — walk the content-addressed tree kind-by-kind and delete only
  files whose content is not in the live set and whose mtime is older than the
  grace period. Deletions are bounded per run (``gc_max_deletions_per_run``)
  and each is an atomic ``unlink`` of a single regular file.

Safety invariants:

* Never deletes a referenced blob, a current version's content, or an artifact
  still referenced by a live document (content-addressed dedup preserved — a
  sha shared by any retained version/artifact is kept).
* Never recursively deletes directories; only unlinks individual regular files.
* Refuses to follow or delete through a symlink/junction, and refuses any file
  whose real path escapes the store root (traversal/junction hardening).
* ``dry_run`` reports exactly what *would* be deleted without touching disk.

Retention windows (live-read from the ConfigStore via :class:`DocumentConfig`):
expired tombstones, post-promotion quarantine copies, terminally-failed
(rejected/dead-letter) versions, unreferenced/orphan blobs + manifests, and
audit rows.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import DocumentConfig, get_document_config

logger = logging.getLogger(__name__)

__all__ = [
    "DocumentGarbageCollector",
    "GcReport",
    "start_document_maintenance_loop",
]

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_KINDS = ("quarantine", "originals", "artifacts")


@dataclass(slots=True)
class GcReport:
    """Truthful outcome of a garbage-collection run (partial failures included)."""

    dry_run: bool = False
    started_at: str = ""
    finished_at: str = ""
    deleted_blobs: int = 0
    deleted_manifests: int = 0
    deleted_blob_rows: int = 0
    deleted_staging: int = 0
    pruned_audit: int = 0
    reclaimed_bytes: int = 0
    budget: int = 0
    budget_exhausted: bool = False
    refused_symlinks: int = 0
    errors: list[str] = field(default_factory=list)
    by_kind: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "deleted_blobs": self.deleted_blobs,
            "deleted_manifests": self.deleted_manifests,
            "deleted_blob_rows": self.deleted_blob_rows,
            "deleted_staging": self.deleted_staging,
            "pruned_audit": self.pruned_audit,
            "reclaimed_bytes": self.reclaimed_bytes,
            "budget": self.budget,
            "budget_exhausted": self.budget_exhausted,
            "refused_symlinks": self.refused_symlinks,
            "errors": self.errors,
            "by_kind": self.by_kind,
        }


class DocumentGarbageCollector:
    """Reference-driven, symlink-safe collector over the content store."""

    def __init__(
        self,
        *,
        repository: Any,
        storage: Any,
        audit: Any = None,
        config: DocumentConfig | None = None,
    ) -> None:
        self._repo = repository
        self._storage = storage
        self._audit = audit
        self._config = config
        self._root = Path(storage.root).resolve()

    # ── Public entry ─────────────────────────────────────────────────────

    def collect(self, *, dry_run: bool = False) -> GcReport:
        """Run one bounded mark/sweep pass and return a truthful report."""
        cfg = self._config or get_document_config()
        now = datetime.now(UTC)
        report = GcReport(
            dry_run=dry_run,
            started_at=now.isoformat(),
            budget=int(cfg.gc_max_deletions_per_run),
        )
        try:
            marks = self._mark(cfg, now)
        except Exception as exc:  # noqa: BLE001 - report, don't fabricate success
            report.errors.append(f"mark_failed:{type(exc).__name__}")
            report.finished_at = datetime.now(UTC).isoformat()
            logger.warning("[documents.retention] mark phase failed", exc_info=True)
            return report

        budget = int(cfg.gc_max_deletions_per_run)
        grace = timedelta(seconds=int(cfg.gc_grace_seconds))
        quarantine_window = timedelta(days=int(cfg.retention_quarantine_days))

        # Sweep physical blob files kind-by-kind.
        for kind in _KINDS:
            if budget <= 0:
                report.budget_exhausted = True
                break
            budget = self._sweep_kind(
                kind,
                marks=marks,
                now=now,
                grace=grace,
                quarantine_window=quarantine_window,
                budget=budget,
                report=report,
                dry_run=dry_run,
            )

        # Sweep manifests.
        if budget > 0:
            budget = self._sweep_manifests(
                marks=marks, now=now, grace=grace, budget=budget, report=report, dry_run=dry_run
            )
        else:
            report.budget_exhausted = True

        # Prune orphan blob rows (metadata only; physical files handled above).
        if budget > 0 and not dry_run:
            try:
                budget = self._prune_blob_rows(marks=marks, now=now, grace=grace, budget=budget, report=report)
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"blob_rows:{type(exc).__name__}")
        elif budget > 0 and dry_run:
            report.deleted_blob_rows = self._count_orphan_blob_rows(marks=marks, now=now, grace=grace)

        # Prune audit rows past retention.
        if self._audit is not None and int(cfg.retention_audit_days) > 0:
            cutoff = (now - timedelta(days=int(cfg.retention_audit_days))).isoformat()
            try:
                if dry_run:
                    report.pruned_audit = 0
                else:
                    report.pruned_audit = self._audit.prune_older_than(
                        cutoff_iso=cutoff, max_rows=int(cfg.gc_max_deletions_per_run)
                    )
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"audit_prune:{type(exc).__name__}")

        report.finished_at = datetime.now(UTC).isoformat()
        return report

    # ── Mark phase ───────────────────────────────────────────────────────

    def _mark(self, cfg: DocumentConfig, now: datetime) -> dict[str, Any]:
        """Compute the authoritative live reference sets from the DB."""
        tombstone_cutoff = (now - timedelta(days=int(cfg.retention_tombstone_days))).isoformat()
        rejected_cutoff = (now - timedelta(days=int(cfg.retention_rejected_days))).isoformat()
        dead_cutoff = (now - timedelta(days=int(cfg.retention_dead_letter_days))).isoformat()
        if hasattr(self._repo, "gc_mark"):
            return self._repo.gc_mark(
                tombstone_cutoff=tombstone_cutoff,
                rejected_cutoff=rejected_cutoff,
                dead_cutoff=dead_cutoff,
            )

        conn = self._repo._conn  # noqa: SLF001 - shared read connection
        lock = self._repo._lock  # noqa: SLF001
        with lock:
            has_jobs = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='document_jobs'"
                ).fetchone()
                is not None
            )
            if has_jobs:
                keep_source = {
                    row["source_sha256"]
                    for row in conn.execute(
                        """
                        SELECT DISTINCT v.source_sha256 AS source_sha256
                        FROM document_versions v
                        JOIN documents d
                          ON d.id = v.document_id AND d.tenant_id = v.tenant_id
                        WHERE (d.deleted_at IS NULL OR d.deleted_at >= ?)
                          AND NOT (
                            v.id <> COALESCE(d.current_version_id, '')
                            AND EXISTS (
                              SELECT 1 FROM document_jobs j
                              WHERE j.tenant_id = v.tenant_id AND j.version_id = v.id
                            )
                            AND NOT EXISTS (
                              SELECT 1 FROM document_jobs j
                              WHERE j.tenant_id = v.tenant_id AND j.version_id = v.id
                                AND (
                                  j.state NOT IN ('rejected', 'dead_letter', 'cancelled')
                                  OR (j.state = 'rejected' AND j.updated_at >= ?)
                                  OR (j.state = 'dead_letter' AND j.updated_at >= ?)
                                )
                            )
                          )
                        """,
                        (tombstone_cutoff, rejected_cutoff, dead_cutoff),
                    ).fetchall()
                }
            else:
                # No durable-job table (metadata-only store) — keep every
                # source referenced by a non-expired-tombstone version.
                keep_source = {
                    row["source_sha256"]
                    for row in conn.execute(
                        """
                        SELECT DISTINCT v.source_sha256 AS source_sha256
                        FROM document_versions v
                        JOIN documents d
                          ON d.id = v.document_id AND d.tenant_id = v.tenant_id
                        WHERE (d.deleted_at IS NULL OR d.deleted_at >= ?)
                        """,
                        (tombstone_cutoff,),
                    ).fetchall()
                }
            keep_artifacts = {
                row["sha256"]
                for row in conn.execute(
                    """
                    SELECT DISTINCT b.sha256 AS sha256
                    FROM document_artifacts a
                    JOIN document_blobs b
                      ON b.id = a.blob_id AND b.tenant_id = a.tenant_id
                    JOIN documents d
                      ON d.id = a.document_id AND d.tenant_id = a.tenant_id
                    WHERE (d.deleted_at IS NULL OR d.deleted_at >= ?)
                    """,
                    (tombstone_cutoff,),
                ).fetchall()
            }
            keep_versions = {
                (row["document_id"], row["id"])
                for row in conn.execute(
                    """
                    SELECT v.id AS id, v.document_id AS document_id
                    FROM document_versions v
                    JOIN documents d
                      ON d.id = v.document_id AND d.tenant_id = v.tenant_id
                    WHERE (d.deleted_at IS NULL OR d.deleted_at >= ?)
                    """,
                    (tombstone_cutoff,),
                ).fetchall()
            }
            referenced_blob_ids = {
                row["blob_id"]
                for row in conn.execute(
                    """
                    SELECT source_blob_id AS blob_id FROM document_versions
                    UNION
                    SELECT blob_id AS blob_id FROM document_artifacts
                    """
                ).fetchall()
            }
        return {
            "keep_source": keep_source,
            "keep_artifacts": keep_artifacts,
            "keep_versions": keep_versions,
            "referenced_blob_ids": referenced_blob_ids,
        }

    # ── Sweep helpers ────────────────────────────────────────────────────

    def _sweep_kind(
        self,
        kind: str,
        *,
        marks: dict[str, Any],
        now: datetime,
        grace: timedelta,
        quarantine_window: timedelta,
        budget: int,
        report: GcReport,
        dry_run: bool,
    ) -> int:
        base = self._root / kind / "sha256"
        if not base.exists():
            return budget
        grace_cutoff = now - grace
        quarantine_cutoff = now - quarantine_window
        keep_source: set[str] = marks["keep_source"]
        keep_artifacts: set[str] = marks["keep_artifacts"]

        for path, sha, mtime, is_staging in self._iter_files(base, report):
            if budget <= 0:
                report.budget_exhausted = True
                break
            if is_staging:
                # Interrupted .incoming-* staging temp — remove if stale.
                if mtime < grace_cutoff:
                    if self._delete_file(path, report, dry_run):
                        report.deleted_staging += 1
                        budget -= 1
                continue
            if sha is None:
                continue

            deletable = False
            if kind == "artifacts":
                deletable = sha not in keep_artifacts and mtime < grace_cutoff
            elif kind == "originals":
                deletable = sha not in keep_source and mtime < grace_cutoff
            elif kind == "quarantine":
                if sha not in keep_source and mtime < grace_cutoff:
                    deletable = True
                elif self._originals_exists(sha) and mtime < quarantine_cutoff:
                    # Redundant untrusted copy after successful promotion.
                    deletable = True

            if not deletable:
                continue
            if not dry_run and self._is_live_reference(kind=kind, sha=sha):
                # Close the mark/sweep TOCTOU window: ingestion may have
                # published a reference after the mark snapshot.
                continue
            size = 0
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if self._delete_file(path, report, dry_run):
                report.deleted_blobs += 1
                report.reclaimed_bytes += size
                report.by_kind[kind] = report.by_kind.get(kind, 0) + 1
                budget -= 1
        return budget

    def _is_live_reference(self, *, kind: str, sha: str) -> bool:
        """Recheck authoritative references immediately before unlink."""
        if hasattr(self._repo, "gc_is_live_reference"):
            return bool(self._repo.gc_is_live_reference(kind=kind, sha=sha))

        conn = self._repo._conn  # noqa: SLF001
        lock = self._repo._lock  # noqa: SLF001
        with lock:
            if kind == "artifacts":
                row = conn.execute(
                    """
                    SELECT 1
                    FROM document_artifacts a
                    JOIN document_blobs b
                      ON b.id = a.blob_id AND b.tenant_id = a.tenant_id
                    JOIN documents d
                      ON d.id = a.document_id AND d.tenant_id = a.tenant_id
                    WHERE b.sha256 = ? AND b.storage_kind = 'artifacts'
                      AND d.deleted_at IS NULL
                    LIMIT 1
                    """,
                    (sha,),
                ).fetchone()
                return row is not None
            if kind == "originals":
                row = conn.execute(
                    """
                    SELECT 1
                    FROM document_versions v
                    JOIN document_blobs b
                      ON b.id = v.source_blob_id AND b.tenant_id = v.tenant_id
                    JOIN documents d
                      ON d.id = v.document_id AND d.tenant_id = v.tenant_id
                    WHERE b.sha256 = ? AND d.deleted_at IS NULL
                    LIMIT 1
                    """,
                    (sha,),
                ).fetchone()
                return row is not None
        return False

    def _sweep_manifests(
        self,
        *,
        marks: dict[str, Any],
        now: datetime,
        grace: timedelta,
        budget: int,
        report: GcReport,
        dry_run: bool,
    ) -> int:
        base = self._root / "manifests"
        if not base.exists():
            return budget
        grace_cutoff = now - grace
        keep_versions: set[tuple[str, str]] = marks["keep_versions"]
        try:
            doc_dirs = [d for d in os.scandir(base) if d.is_dir(follow_symlinks=False)]
        except OSError:
            return budget
        for doc_entry in doc_dirs:
            if budget <= 0:
                report.budget_exhausted = True
                break
            doc_id = doc_entry.name
            try:
                files = list(os.scandir(doc_entry.path))
            except OSError:
                continue
            for entry in files:
                if budget <= 0:
                    report.budget_exhausted = True
                    break
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    if entry.is_symlink():
                        report.refused_symlinks += 1
                    continue
                if not entry.name.endswith(".json"):
                    continue
                version_id = entry.name[:-5]
                if (doc_id, version_id) in keep_versions:
                    continue
                path = Path(entry.path)
                try:
                    mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
                except OSError:
                    continue
                if mtime >= grace_cutoff:
                    continue
                if self._delete_file(path, report, dry_run):
                    report.deleted_manifests += 1
                    budget -= 1
        return budget

    def _iter_files(self, base: Path, report: GcReport):
        """Yield (path, sha_or_None, mtime, is_staging) for regular files only.

        Walks the ``{kind}/sha256`` tree without following symlinks. A symlink
        or a file whose real path escapes the store root is refused (never
        followed, never deleted).
        """
        root_real = os.path.realpath(self._root)
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            # Do not descend into symlinked directories (junction hardening).
            pruned = []
            for d in list(dirnames):
                full = os.path.join(dirpath, d)
                if os.path.islink(full):
                    report.refused_symlinks += 1
                    pruned.append(d)
            for d in pruned:
                dirnames.remove(d)
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    if os.path.islink(full):
                        report.refused_symlinks += 1
                        continue
                    real = os.path.realpath(full)
                    if not (real == root_real or real.startswith(root_real + os.sep)):
                        report.refused_symlinks += 1
                        continue
                    st = os.stat(full, follow_symlinks=False)
                    mtime = datetime.fromtimestamp(st.st_mtime, tz=UTC)
                except OSError:
                    continue
                is_staging = name.startswith(".incoming-") or name.startswith(".")
                sha = name if _SHA_RE.fullmatch(name) else None
                if sha is None and not is_staging:
                    # Unexpected file that isn't a sha blob or a staging temp.
                    continue
                yield Path(full), sha, mtime, is_staging

    def _originals_exists(self, sha: str) -> bool:
        try:
            return self._storage.verify_blob(kind="originals", sha256=sha)
        except Exception:  # noqa: BLE001
            # Fall back to a path check without hashing on any error.
            try:
                return self._storage.blob_path(kind="originals", sha256=sha).is_file()
            except Exception:  # noqa: BLE001
                return False

    def _delete_file(self, path: Path, report: GcReport, dry_run: bool) -> bool:
        if dry_run:
            return True
        try:
            if path.is_symlink():
                report.refused_symlinks += 1
                return False
            path.unlink()
            return True
        except OSError as exc:
            report.errors.append(f"unlink:{type(exc).__name__}")
            logger.debug("[documents.retention] unlink failed for a blob", exc_info=True)
            return False

    def _count_orphan_blob_rows(
        self, *, marks: dict[str, Any], now: datetime, grace: timedelta
    ) -> int:
        cutoff = (now - grace).isoformat()
        referenced = marks["referenced_blob_ids"]
        if hasattr(self._repo, "gc_old_unreferenced_blob_ids"):
            return len(
                self._repo.gc_old_unreferenced_blob_ids(
                    cutoff_iso=cutoff, referenced=referenced, limit=10_000
                )
            )
        conn = self._repo._conn  # noqa: SLF001
        lock = self._repo._lock  # noqa: SLF001
        with lock:
            rows = conn.execute(
                "SELECT id FROM document_blobs WHERE created_at < ?", (cutoff,)
            ).fetchall()
        return sum(1 for r in rows if r["id"] not in referenced)

    def _prune_blob_rows(
        self, *, marks: dict[str, Any], now: datetime, grace: timedelta, budget: int, report: GcReport
    ) -> int:
        cutoff = (now - grace).isoformat()
        referenced = marks["referenced_blob_ids"]
        if hasattr(self._repo, "gc_old_unreferenced_blob_ids") and hasattr(
            self._repo, "gc_delete_blob_ids"
        ):
            to_delete = self._repo.gc_old_unreferenced_blob_ids(
                cutoff_iso=cutoff, referenced=referenced, limit=budget
            )
            n = int(self._repo.gc_delete_blob_ids(to_delete))
            report.deleted_blob_rows += n
            return max(0, budget - n)
        conn = self._repo._conn  # noqa: SLF001
        lock = self._repo._lock  # noqa: SLF001
        with lock:
            rows = conn.execute(
                "SELECT id FROM document_blobs WHERE created_at < ? ORDER BY created_at ASC",
                (cutoff,),
            ).fetchall()
            to_delete = [r["id"] for r in rows if r["id"] not in referenced][:budget]
            for blob_id in to_delete:
                try:
                    conn.execute("DELETE FROM document_blobs WHERE id = ?", (blob_id,))
                    report.deleted_blob_rows += 1
                    budget -= 1
                except Exception:  # noqa: BLE001
                    report.errors.append("blob_row_delete")
        return budget


# ── Scheduled maintenance lifecycle loop (cancellable) ───────────────────

_MAINTENANCE_FIRST_DELAY_SECONDS = 180


def start_document_maintenance_loop(
    *,
    first_delay_seconds: int = _MAINTENANCE_FIRST_DELAY_SECONDS,
):
    """Start the periodic document GC loop (fire-and-forget asyncio task).

    Reads ``documents.gc.*`` live from the ConfigStore each run, so Settings
    changes apply without a restart. Respects the global shutdown flag and is
    cancellable. Never raises — a failed run is logged and the loop continues.
    Returns the created :class:`asyncio.Task`.
    """
    import asyncio

    async def _loop() -> None:
        await asyncio.sleep(max(0, first_delay_seconds))
        while True:
            try:
                from kazma_core.shutdown import is_shutting_down

                if is_shutting_down():
                    logger.info("[documents.retention] maintenance loop exiting (shutdown)")
                    return
                cfg = get_document_config()
                from .config import get_document_rollout

                rollout = get_document_rollout()
                if rollout.enabled and cfg.gc_enabled and cfg.gc_auto_maintain:
                    run = asyncio.create_task(asyncio.to_thread(_run_once))
                    try:
                        await asyncio.shield(run)
                    except asyncio.CancelledError:
                        # A thread cannot be cancelled safely. Wait for the
                        # bounded pass before allowing repository shutdown.
                        await run
                        raise
                else:
                    logger.debug(
                        "[documents.retention] auto-maintain or durable writes disabled; skipping"
                    )
                interval_hours = max(1, int(cfg.gc_interval_hours))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - loop must survive any failure
                logger.warning("[documents.retention] maintenance iteration failed: %s", exc)
                interval_hours = 6
            await asyncio.sleep(interval_hours * 3600)

    return asyncio.create_task(_loop())


def _run_once() -> None:
    """Run a single automatic GC pass via the shared ingestion coordinator."""
    try:
        from .ingestion import get_ingestion_service

        service = get_ingestion_service()
        report = service.run_maintenance(dry_run=False)
        logger.info(
            "[documents.retention] gc: blobs=%d manifests=%d rows=%d audit=%d reclaimed=%dB",
            report.get("deleted_blobs", 0),
            report.get("deleted_manifests", 0),
            report.get("deleted_blob_rows", 0),
            report.get("pruned_audit", 0),
            report.get("reclaimed_bytes", 0),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[documents.retention] automatic gc failed: %s", exc)
