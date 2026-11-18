"""Each space's document files in its own folder: the copy for an older
install (Spaces S1-12).

Until Spaces is enforced every tenant's blobs and manifests are written to
the storage root, as before Spaces (``storage.writes_folder``): the older
multi-user mode's tenants and a retired memory mode's (``telegram``) too.
Once it is enforced each space writes to its own folder
(``storage.space_folder``), so one space can be exported or erased alone, and
this module copies the files an older install kept in the root into their
space's folder, with no step for the owner to run:

* **Only once Spaces is enforced.** Before, nothing is copied: there is one
  space, the owner's, and it is the root.
* **Copied, never moved.** The root keeps its copy of every file in this
  release (garbage collection keeps every root file a live row of any tenant
  names), so an older build, which reads the root only, still reads every
  document after a revert. Removing the root copies is a later step, after
  S1-33.
* **Journaled.** ``<storage root>/.space-files.json`` says which tenants are
  done (``moved``) and which one is being copied (``copying``), written
  (atomically) before a tenant's copy starts and after it ends.
* **Safe to interrupt, and checked at every start.** A blob is copied
  content-addressed, checked against its digest and renamed into place
  (``SpaceStorage.put_stream``); a manifest byte for byte the same way. Every
  start looks at every tenant again and copies only what its folder lacks, so
  a stopped run goes on where it stopped, and a file an older build wrote to
  the root during a revert is copied too.
* **One bad file never stops the rest.** An id that cannot name a path, a
  file that does not match its digest or cannot be read is a WARNING naming
  its tenant and kind; the copy goes on with the next file. Reads look in the
  root as well as the space's folder (``storage.blob_rels``), so nothing
  waits for this.

Blocking: runs in a thread at the service's start (``start_workers``).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from kazma_core.atomic_files import atomic_write_text

from .storage import (
    BlobChecksumError,
    ContentAddressedStorage,
    _read_journal,
    blob_rel,
    legacy_blob_rel,
    legacy_manifest_rel,
    manifest_rel,
    space_folder,
)

logger = logging.getLogger(__name__)

__all__ = ["copy_into_space_folders"]

#: Every reference counts, however long ago its document was deleted: a file
#: copied that no one needs is collected from its folder later.
_ALL_TIME = "1970-01-01T00:00:00+00:00"


def _marks(repository: Any) -> dict[str, Any]:
    return repository.gc_mark(tombstone_cutoff=_ALL_TIME, rejected_cutoff=_ALL_TIME, dead_cutoff=_ALL_TIME)


def _other_tenants(marks: dict[str, Any], report: dict[str, Any]) -> list[str]:
    """The tenants of another space than the owner's with references. A
    tenant with no name (a row an older build wrote badly) has no folder: it
    is skipped with a WARNING, its files stay in the root."""
    tenants: set[str] = set()
    for key in ("keep_source_by_tenant", "keep_artifacts_by_tenant", "keep_versions_by_tenant"):
        tenants.update(marks.get(key) or ())
    others: list[str] = []
    for tenant in sorted(tenants):
        try:
            if space_folder(tenant):
                others.append(tenant)
        except ValueError:
            report["skipped"] += 1
            logger.warning("[documents.space_files] documents with no space name are left in the root")
    return others


def _steps(marks: dict[str, Any], tenant: str) -> list[tuple[str, Any]]:
    return [
        *((kind, sha) for sha in sorted(marks.get("keep_source_by_tenant", {}).get(tenant, ()))
          for kind in ("quarantine", "originals")),
        *(("artifacts", sha) for sha in sorted(marks.get("keep_artifacts_by_tenant", {}).get(tenant, ()))),
        *(("manifest", pair) for pair in sorted(marks.get("keep_versions_by_tenant", {}).get(tenant, ()))),
    ]


def _paths(storage: ContentAddressedStorage, tenant: str, kind: str, what: Any) -> tuple[Path, Path]:
    """(the file's place in the space's folder, its place in the root);
    ``ValueError`` for an id that cannot name a path."""
    if kind == "manifest":
        document_id, version_id = what
        return (storage.root / manifest_rel(tenant, document_id, version_id),
                storage.root / legacy_manifest_rel(document_id, version_id))
    return storage.root / blob_rel(tenant, kind, what), storage.root / legacy_blob_rel(kind, what)


def _write_journal(storage: ContentAddressedStorage, *, moved: set[str], copying: str | None) -> None:
    import json

    atomic_write_text(
        storage.journal_path(),
        json.dumps({"version": 1, "moved": sorted(moved), "copying": copying}, indent=2, sort_keys=True),
    )


def _copy_blob(storage: ContentAddressedStorage, tenant: str, kind: str, sha: str, source: Path) -> None:
    """Copy one blob into the tenant's folder, checked against its digest
    (``BlobChecksumError`` when it does not match)."""
    space = storage.for_space(tenant)
    with source.open("rb") as handle:
        space.put_stream(handle, kind=kind, max_bytes=max(1, source.stat().st_size), expected_sha256=sha)


def _copy_manifest(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=".manifest-", dir=target.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(source, temp_name)
        with open(temp_name, "rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _to_copy(storage: ContentAddressedStorage, tenant: str, steps: list[tuple[str, Any]],
             report: dict[str, Any]) -> list[tuple[str, Any, Path, Path]]:
    """The steps whose file is in the root and not in the space's folder. An
    id that cannot name a path is skipped with a WARNING (nothing could ever
    copy it, and it is never read by that path either)."""
    pending: list[tuple[str, Any, Path, Path]] = []
    for kind, what in steps:
        try:
            own, root = _paths(storage, tenant, kind, what)
        except ValueError:
            report["skipped"] += 1
            logger.warning("[documents.space_files] a %s of space %s has an id that names no file; "
                           "it is skipped", kind, tenant)
            continue
        if own.is_file() or not root.is_file():
            continue  # in its folder already, or nothing of it in the root to copy
        pending.append((kind, what, own, root))
    return pending


def copy_into_space_folders(
    storage: ContentAddressedStorage, repository: Any, *, stop: threading.Event | None = None,
) -> dict[str, Any]:
    """Copy the root files of every tenant of another space into its folder,
    once Spaces is enforced. Returns ``{"moved": [...], "left": [...],
    "skipped": n}``: the tenants whose folder holds every file now, those left
    for the next start (a stop, a file that does not match its digest or could
    not be read), and the references whose id names no file."""
    from kazma_core.spaces.scope import spaces_enforced

    report: dict[str, Any] = {"moved": [], "left": [], "skipped": 0}
    if not spaces_enforced():
        return report  # one space, the owner's: every file is in the root
    marks = _marks(repository)
    tenants = _other_tenants(marks, report)
    if not tenants:
        return report
    moved = set(_read_journal(storage.journal_path()).get("moved") or ())
    journal_changed = False
    for index, tenant in enumerate(tenants):
        if stop is not None and stop.is_set():
            report["left"].extend(tenants[index:])
            break
        pending = _to_copy(storage, tenant, _steps(marks, tenant), report)
        if not pending:
            if tenant not in moved:
                moved.add(tenant)
                journal_changed = True
                storage.note_moved(tenant)
                report["moved"].append(tenant)
            continue
        _write_journal(storage, moved=moved - {tenant}, copying=tenant)
        whole = True
        for kind, what, own, root in pending:
            if stop is not None and stop.is_set():
                whole = False  # the next start goes on from here
                break
            try:
                if kind == "manifest":
                    _copy_manifest(root, own)
                else:
                    _copy_blob(storage, tenant, kind, what, root)
            except BlobChecksumError:
                whole = False
                logger.warning("[documents.space_files] a %s file of space %s in the root does not "
                               "match its digest; it is left there and read from there", kind, tenant)
            except OSError as exc:
                whole = False
                logger.warning("[documents.space_files] a %s file of space %s could not be copied "
                               "(%s); the next start tries again", kind, tenant, type(exc).__name__)
        if not whole:
            moved.discard(tenant)
            _write_journal(storage, moved=moved, copying=None)
            report["left"].append(tenant)
            continue
        moved.add(tenant)
        _write_journal(storage, moved=moved, copying=None)
        journal_changed = False
        storage.note_moved(tenant)
        report["moved"].append(tenant)
    if journal_changed:
        _write_journal(storage, moved=moved, copying=None)
    if report["moved"]:
        logger.info("[documents.space_files] the files of %d space(s) are in their folders",
                    len(report["moved"]))
    return report
