"""Each space's document files in its own folder: the copy for an older
install (Spaces S1-12).

Until Spaces every tenant's blobs and manifests were in one tree at the
storage root. Now each space's are in its own folder (``storage.space_folder``)
so one space can be exported or erased alone. The owner's space IS the root,
so a single-account install moves nothing. An install of the older
multi-user mode can hold another tenant's files in the root; this module
copies them into their space's folder, once, with no step for the owner to
run:

* **Journaled.** ``<storage root>/.space-files.json`` says which tenants are
  done (``moved``) and which one is being copied (``copying``), and is written
  (atomically) before each tenant's copy starts and after it ends.
* **Safe to interrupt.** A copy is content-addressed, checked against its
  digest and renamed into place (``SpaceStorage.put_stream``); a manifest is
  copied byte for byte the same way. A stopped run starts its tenant again and
  skips every file already in place.
* **Nothing is read from a folder that is not ready.** Until a tenant is
  ``moved``, its reads fall back to the root (``SpaceStorage.locate_blob``)
  and garbage collection keeps its root copies; once it is, the root copies
  no one else references are collected like any other.

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
    legacy_blob_rel,
    legacy_manifest_rel,
    space_folder,
)

logger = logging.getLogger(__name__)

__all__ = ["copy_into_space_folders"]

#: Every reference counts, however long ago its document was deleted: a file
#: copied that no one needs is collected from its folder later.
_ALL_TIME = "1970-01-01T00:00:00+00:00"


def _marks(repository: Any) -> dict[str, Any]:
    return repository.gc_mark(tombstone_cutoff=_ALL_TIME, rejected_cutoff=_ALL_TIME, dead_cutoff=_ALL_TIME)


def _tenants_to_copy(storage: ContentAddressedStorage, marks: dict[str, Any]) -> list[str]:
    """The tenants of another space with references, not yet in their folder."""
    tenants: set[str] = set()
    for key in ("keep_source_by_tenant", "keep_artifacts_by_tenant", "keep_versions_by_tenant"):
        tenants.update(marks.get(key) or ())
    moved = storage.moved_tenants()
    return sorted(t for t in tenants if space_folder(t) and t not in moved)


def _write_journal(storage: ContentAddressedStorage, *, moved: set[str], copying: str | None) -> None:
    import json

    atomic_write_text(
        storage.journal_path(),
        json.dumps({"version": 1, "moved": sorted(moved), "copying": copying}, indent=2, sort_keys=True),
    )


def _copy_blob(storage: ContentAddressedStorage, tenant: str, kind: str, sha: str) -> bool:
    """Copy one blob into the tenant's folder; True when it is there now (or
    was never in the root to copy)."""
    space = storage.for_space(tenant)
    if space.blob_path(kind=kind, sha256=sha).is_file():
        return True
    legacy = storage.root / legacy_blob_rel(kind, sha)
    if not legacy.is_file():
        return True  # nothing of it in the root: nothing to copy
    try:
        with legacy.open("rb") as handle:
            space.put_stream(handle, kind=kind, max_bytes=max(1, legacy.stat().st_size),
                             expected_sha256=sha)
    except BlobChecksumError:
        logger.warning(
            "[documents.space_files] a %s file of space %s in the root does not match its "
            "digest; it is left there and the space keeps reading the root", kind, tenant,
        )
        return False
    return True


def _copy_manifest(storage: ContentAddressedStorage, tenant: str, document_id: str, version_id: str) -> None:
    space = storage.for_space(tenant)
    target = space.manifest_path(document_id=document_id, version_id=version_id)
    if target.is_file():
        return
    legacy = storage.root / legacy_manifest_rel(document_id, version_id)
    if not legacy.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=".manifest-", dir=target.parent)
    os.close(descriptor)
    try:
        shutil.copyfile(legacy, temp_name)
        with open(temp_name, "rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def copy_into_space_folders(
    storage: ContentAddressedStorage, repository: Any, *, stop: threading.Event | None = None,
) -> dict[str, Any]:
    """Copy every tenant of another space whose files are in the root into
    its folder. Returns ``{"moved": [...], "left": [...]}``: the tenants done
    by this run, and those left for the next (a stop, or a file that does not
    match its digest)."""
    marks = _marks(repository)
    pending = _tenants_to_copy(storage, marks)
    report: dict[str, Any] = {"moved": [], "left": []}
    if not pending:
        return report
    moved = set(_read_journal(storage.journal_path()).get("moved") or ())
    for index, tenant in enumerate(pending):
        if stop is not None and stop.is_set():
            report["left"].extend(pending[index:])
            break
        _write_journal(storage, moved=moved, copying=tenant)
        steps = [
            *((kind, sha) for sha in sorted(marks.get("keep_source_by_tenant", {}).get(tenant, ()))
              for kind in ("quarantine", "originals")),
            *(("artifacts", sha) for sha in sorted(marks.get("keep_artifacts_by_tenant", {}).get(tenant, ()))),
            *(("manifest", pair) for pair in sorted(marks.get("keep_versions_by_tenant", {}).get(tenant, ()))),
        ]
        whole = True
        for kind, what in steps:
            if stop is not None and stop.is_set():
                whole = False  # the next start goes on from here
                break
            if kind == "manifest":
                _copy_manifest(storage, tenant, *what)
            else:
                whole = _copy_blob(storage, tenant, kind, what) and whole
        if not whole:
            report["left"].append(tenant)
            continue
        moved.add(tenant)
        _write_journal(storage, moved=moved, copying=None)
        storage.note_moved(tenant)
        report["moved"].append(tenant)
    if report["moved"]:
        logger.info("[documents.space_files] copied the files of %d space(s) into their folders",
                    len(report["moved"]))
    return report
