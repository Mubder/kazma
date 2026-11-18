"""Each space's document files are kept in its own folder (Spaces S1-12).

So one space can be exported or erased alone. A space's folder
(``storage.space_folder``) is the storage root for the owner's space (an
install's documents do not move) and ``spaces/<id>`` for any other.

**Before Spaces is enforced nothing changes** (``storage.writes_folder``):
every tenant's files are written to the root, where they always were -- the
older multi-user mode's tenants and a retired memory mode's too -- so a
backup has the shape it had before Spaces and an older build (a revert)
reads every file. Once enforced, each space writes to its own folder and
``documents.space_files`` copies the files kept in the root into their
space's folder, journaled and safe to interrupt. **The root keeps its copy**
in this release: garbage collection never removes a root file a live row of
any tenant names, so a revert still reads every document.

Negative controls: a store that writes another tenant's files to its folder
before Spaces is enforced leaves an older build nothing to read; one folder
for every tenant once enforced shares a file between two spaces; the first
version's collector, which let the root forget a copied tenant, loses the
root copy an older build reads.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from kazma_core.documents import space_files
from kazma_core.documents import storage as storage_module
from kazma_core.documents.backup import perform_document_backup, verify_document_backup
from kazma_core.documents.space_files import copy_into_space_folders
from kazma_core.documents.storage import (
    SPACE_FILES_JOURNAL,
    ContentAddressedStorage,
    blob_rel,
    legacy_blob_rel,
    legacy_manifest_rel,
    manifest_rel,
    space_folder,
    writes_folder,
)
from kazma_core.spaces import scope

from tests._documents_seed import seed_document, seeded_service

OWNER = "default"
OTHER = "acme"


@pytest.fixture
def svc(tmp_path):
    service = seeded_service(tmp_path / "store")
    try:
        yield service
    finally:
        service.close()


@pytest.fixture
def enforced(monkeypatch):
    """Spaces enforced (the in-memory switch ``spaces_enforced`` reads)."""
    monkeypatch.setattr(scope, "_enforced", True)


def _enforce(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _older_build_reads(root: Path, seeded) -> bool:
    """What an older build (a revert) can read: the root's own tree only."""
    return ((root / legacy_blob_rel("originals", seeded.sha256)).is_file()
            and (root / legacy_manifest_rel(seeded.document_id, seeded.version_id)).is_file())


# ── the folder of a space ───────────────────────────────────────────────────


def test_the_owners_space_is_the_root_and_others_have_a_folder(monkeypatch):
    assert space_folder(OWNER) == ""
    assert space_folder("acme") == "spaces/acme"
    # A tenant an older install named freely never becomes a path of its own.
    odd = space_folder("Acme Corp/../x")
    assert odd.startswith("spaces/_t") and "/" not in odd.removeprefix("spaces/")
    with pytest.raises(ValueError):
        space_folder("")
    sha = _sha(b"x")
    assert blob_rel(OWNER, "originals", sha) == f"originals/sha256/{sha[:2]}/{sha[2:4]}/{sha}"
    assert blob_rel(OTHER, "originals", sha) == f"spaces/acme/originals/sha256/{sha[:2]}/{sha[2:4]}/{sha}"
    # Where new files go: the root for everyone until Spaces is enforced.
    assert writes_folder(OTHER) == "" and writes_folder(OWNER) == ""
    _enforce(monkeypatch)
    assert writes_folder(OTHER) == "spaces/acme" and writes_folder(OWNER) == ""


# ── before Spaces is enforced: nothing changes ─────────────────────────────


def test_before_spaces_every_tenants_files_stay_in_the_root(svc):
    """A single-account install, the older multi-user mode, a retired memory
    mode: every file where it always was, nothing copied, no journal."""
    mine = seed_document(svc, tenant_id=OWNER, owner="owner", marker="stays")
    theirs = seed_document(svc, tenant_id="telegram", owner="operator", marker="chat upload")
    root = svc.storage.root
    before = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    report = copy_into_space_folders(svc.storage, svc.repository)
    after = sorted(p.relative_to(root) for p in root.rglob("*") if p.is_file())
    assert report == {"moved": [], "left": [], "skipped": 0}
    assert after == before
    assert not (root / "spaces").exists()
    assert not (root / SPACE_FILES_JOURNAL).exists()
    assert _older_build_reads(root, mine) and _older_build_reads(root, theirs)
    assert "chat upload" in svc.get_content(
        tenant_id="telegram", actor_id="operator", document_id=theirs.document_id)["text"]


def test_control_a_store_that_writes_spaces_folders_early_leaves_a_revert_nothing(svc, monkeypatch):
    # Negative control: the first version wrote another tenant's files to its
    # folder whatever Spaces said; an older build reads the root only.
    monkeypatch.setattr(storage_module, "writes_folder", storage_module.space_folder)
    theirs = seed_document(svc, tenant_id="telegram", owner="operator", marker="early")
    assert not _older_build_reads(svc.storage.root, theirs)


def test_before_spaces_a_backup_has_the_shape_it_always_had(svc, tmp_path):
    """An older build verifies it: every file at its root path."""
    mine = seed_document(svc, tenant_id=OWNER, owner="owner", marker="owners backup")
    theirs = seed_document(svc, tenant_id=OTHER, owner="layla", marker="others backup")
    report = perform_document_backup(storage_root=svc.storage.root, dest_dir=tmp_path / "backups")
    assert report["ok"], report
    keys = set(report["checksums"])
    for seeded in (mine, theirs):
        assert legacy_blob_rel("originals", seeded.sha256) in keys
        assert legacy_manifest_rel(seeded.document_id, seeded.version_id) in keys
    assert not any(key.startswith("spaces/") for key in keys)


# ── Spaces enforced: each space in its folder ──────────────────────────────


def test_two_spaces_never_share_a_file(tmp_path, enforced):
    store = ContentAddressedStorage(tmp_path)
    mine = store.for_space(OWNER).put_stream(io.BytesIO(b"same bytes"), kind="originals", max_bytes=100)
    theirs = store.for_space(OTHER).put_stream(io.BytesIO(b"same bytes"), kind="originals", max_bytes=100)
    assert mine.path != theirs.path and not theirs.reused
    assert mine.path.parent.parts[-4:] == ("originals", "sha256", mine.sha256[:2], mine.sha256[2:4])
    assert theirs.path.is_relative_to(tmp_path / "spaces" / OTHER)
    # A space's store writes its own space only.
    with pytest.raises(ValueError, match="another space"):
        store.for_space(OTHER).put_stream(io.BytesIO(b"x"), kind="originals", max_bytes=10,
                                          tenant_id=OWNER, repository=object(), tenant_quota_bytes=10)


def test_control_one_folder_for_every_tenant_shares_the_file(tmp_path, monkeypatch, enforced):
    # Negative control: before Spaces every tenant's files were the root's.
    monkeypatch.setattr(storage_module, "writes_folder", lambda _tenant: "")
    store = ContentAddressedStorage(tmp_path)
    store.for_space(OWNER).put_stream(io.BytesIO(b"same bytes"), kind="originals", max_bytes=100)
    theirs = store.for_space(OTHER).put_stream(io.BytesIO(b"same bytes"), kind="originals", max_bytes=100)
    assert theirs.reused, "one file was shared by two spaces"


def test_a_document_of_each_space_is_under_its_folder(svc, enforced):
    mine = seed_document(svc, tenant_id=OWNER, owner="owner", marker="mine")
    theirs = seed_document(svc, tenant_id=OTHER, owner="layla", marker="theirs")
    root = svc.storage.root
    assert (root / blob_rel(OWNER, "originals", mine.sha256)).is_file()
    assert (root / manifest_rel(OWNER, mine.document_id, mine.version_id)).is_file()
    assert (root / blob_rel(OTHER, "originals", theirs.sha256)).is_file()
    assert (root / manifest_rel(OTHER, theirs.document_id, theirs.version_id)).is_file()
    # No content file of the other space is outside its folder (the catalog
    # is rows, kept apart by tenant).
    content = {"originals", "quarantine", "artifacts", "manifests"}
    outside = [p for p in root.rglob("*") if p.is_file() and content & set(p.relative_to(root).parts)
               and b"theirs" in p.read_bytes() and not p.is_relative_to(root / "spaces" / OTHER)]
    assert not outside, outside
    assert svc.get_content(tenant_id=OTHER, actor_id="layla", document_id=theirs.document_id)["text"]


# ── files written before Spaces was enforced ───────────────────────────────


def test_files_from_before_spaces_are_read_from_the_root(svc, monkeypatch):
    old = seed_document(svc, tenant_id=OTHER, owner="layla", marker="fromroot")
    _enforce(monkeypatch)
    located = svc.storage.for_space(OTHER).locate_blob(kind="originals", sha256=old.sha256)
    assert located == svc.storage.root / legacy_blob_rel("originals", old.sha256)
    assert "fromroot" in svc.get_content(tenant_id=OTHER, actor_id="layla", document_id=old.document_id)["text"]


def test_the_copy_is_journaled_and_the_root_keeps_its_copy(svc, monkeypatch):
    old = seed_document(svc, tenant_id=OTHER, owner="layla", marker="copied")
    _enforce(monkeypatch)
    report = copy_into_space_folders(svc.storage, svc.repository)
    assert report["moved"] == [OTHER] and not report["left"]
    root = svc.storage.root
    assert (root / blob_rel(OTHER, "originals", old.sha256)).is_file()
    assert (root / manifest_rel(OTHER, old.document_id, old.version_id)).is_file()
    assert _older_build_reads(root, old), "copied, never moved: a revert still reads the root"
    journal = json.loads((root / SPACE_FILES_JOURNAL).read_text(encoding="utf-8"))
    assert journal["moved"] == [OTHER] and journal["copying"] is None
    assert ContentAddressedStorage(root).moved_tenants() == frozenset({OTHER})
    located = svc.storage.for_space(OTHER).locate_blob(kind="originals", sha256=old.sha256)
    assert located == root / blob_rel(OTHER, "originals", old.sha256), "the space's folder first"
    assert "copied" in svc.get_content(tenant_id=OTHER, actor_id="layla", document_id=old.document_id)["text"]
    # Nothing left to copy: the next start writes nothing.
    stamp = (root / SPACE_FILES_JOURNAL).stat().st_mtime_ns
    assert copy_into_space_folders(svc.storage, svc.repository) == {"moved": [], "left": [], "skipped": 0}
    assert (root / SPACE_FILES_JOURNAL).stat().st_mtime_ns == stamp


def test_a_file_an_older_build_wrote_during_a_revert_is_copied_at_the_next_start(svc, monkeypatch):
    seed_document(svc, tenant_id=OTHER, owner="layla", marker="first")
    _enforce(monkeypatch)
    assert copy_into_space_folders(svc.storage, svc.repository)["moved"] == [OTHER]
    # The revert: an older build writes the space's new document to the root.
    monkeypatch.setattr(scope, "_enforced", False)
    later = seed_document(svc, tenant_id=OTHER, owner="layla", marker="during the revert")
    assert not (svc.storage.root / blob_rel(OTHER, "originals", later.sha256)).exists()
    _enforce(monkeypatch)
    copy_into_space_folders(svc.storage, svc.repository)
    assert (svc.storage.root / blob_rel(OTHER, "originals", later.sha256)).is_file()
    assert (svc.storage.root / manifest_rel(OTHER, later.document_id, later.version_id)).is_file()


class _Killed(BaseException):
    """The process stopping in the middle of a copy."""


def test_an_interrupted_copy_goes_on_where_it_stopped(svc, monkeypatch):
    old = seed_document(svc, tenant_id=OTHER, owner="layla", marker="interrupted")
    _enforce(monkeypatch)
    root = svc.storage.root
    real_copy = space_files._copy_manifest

    def dies(*_args, **_kwargs):
        raise _Killed

    monkeypatch.setattr(space_files, "_copy_manifest", dies)
    with pytest.raises(_Killed):
        copy_into_space_folders(svc.storage, svc.repository)
    journal = json.loads((root / SPACE_FILES_JOURNAL).read_text(encoding="utf-8"))
    assert journal["copying"] == OTHER and OTHER not in journal["moved"], "the step was recorded first"
    copied_blob = root / blob_rel(OTHER, "originals", old.sha256)
    assert copied_blob.is_file()
    stamp = copied_blob.stat().st_mtime_ns
    # Half copied, the space still reads every file.
    assert "interrupted" in svc.get_content(tenant_id=OTHER, actor_id="layla", document_id=old.document_id)["text"]

    monkeypatch.setattr(space_files, "_copy_manifest", real_copy)
    fresh = ContentAddressedStorage(root)
    assert copy_into_space_folders(fresh, svc.repository)["moved"] == [OTHER]
    assert copied_blob.stat().st_mtime_ns == stamp, "a file already in place is not copied again"
    assert (root / manifest_rel(OTHER, old.document_id, old.version_id)).is_file()


def test_a_stop_leaves_the_tenant_for_the_next_start(svc, monkeypatch):
    seed_document(svc, tenant_id=OTHER, owner="layla", marker="stopped")
    _enforce(monkeypatch)
    stop = threading.Event()
    stop.set()
    report = copy_into_space_folders(svc.storage, svc.repository, stop=stop)
    assert report == {"moved": [], "left": [OTHER], "skipped": 0}


def test_a_file_that_does_not_match_its_digest_is_left_in_the_root(svc, monkeypatch):
    old = seed_document(svc, tenant_id=OTHER, owner="layla", marker="corrupt")
    _enforce(monkeypatch)
    legacy = svc.storage.root / legacy_blob_rel("originals", old.sha256)
    legacy.write_bytes(b"not what the catalog says")
    report = copy_into_space_folders(svc.storage, svc.repository)
    assert report["left"] == [OTHER]
    assert not (svc.storage.root / blob_rel(OTHER, "originals", old.sha256)).exists()
    assert svc.storage.for_space(OTHER).locate_blob(kind="originals", sha256=old.sha256) == legacy


def test_one_bad_file_is_a_warning_and_the_rest_is_copied(svc, monkeypatch, caplog):
    """An id that names no file, a tenant with no name, a file that cannot be
    read: each a WARNING, never an ERROR at every start, and the copy goes on."""
    good = seed_document(svc, tenant_id=OTHER, owner="layla", marker="good")
    _enforce(monkeypatch)
    real_marks = space_files._marks

    def with_bad_rows(repository):
        marks = real_marks(repository)
        marks["keep_versions_by_tenant"].setdefault(OTHER, set()).add(("../escape", "v"))
        marks["keep_source_by_tenant"][""] = {"0" * 64}
        return marks

    monkeypatch.setattr(space_files, "_marks", with_bad_rows)
    real_blob = space_files._copy_blob
    unreadable = {"done": False}

    def first_fails(storage, tenant, kind, sha, source):
        if not unreadable["done"]:
            unreadable["done"] = True
            raise PermissionError("locked")
        return real_blob(storage, tenant, kind, sha, source)

    monkeypatch.setattr(space_files, "_copy_blob", first_fails)
    with caplog.at_level(logging.WARNING, logger="kazma_core.documents.space_files"):
        report = copy_into_space_folders(svc.storage, svc.repository)
    assert report["skipped"] == 2 and report["left"] == [OTHER]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 3
    # The file after the one that failed was copied all the same.
    assert (svc.storage.root / manifest_rel(OTHER, good.document_id, good.version_id)).is_file()
    assert not (svc.storage.root / blob_rel(OTHER, "originals", good.sha256)).exists()
    assert "good" in svc.get_content(tenant_id=OTHER, actor_id="layla", document_id=good.document_id)["text"]
    # The next start copies the file that failed.
    monkeypatch.setattr(space_files, "_copy_blob", real_blob)
    assert copy_into_space_folders(svc.storage, svc.repository)["moved"] == [OTHER]
    assert (svc.storage.root / blob_rel(OTHER, "originals", good.sha256)).is_file()


async def test_the_start_up_copy_never_logs_an_error(svc, monkeypatch, caplog):
    """The service's own start-up task: a bad row is a WARNING (an ERROR at
    every start would trip the deploy's log check)."""
    seed_document(svc, tenant_id=OTHER, owner="layla", marker="at start")
    _enforce(monkeypatch)
    real_marks = space_files._marks

    def with_a_bad_row(repository):
        marks = real_marks(repository)
        marks["keep_versions_by_tenant"].setdefault(OTHER, set()).add(("a/b", "v"))
        return marks

    monkeypatch.setattr(space_files, "_marks", with_a_bad_row)
    with caplog.at_level(logging.WARNING):
        await svc._copy_space_files_once()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], caplog.records
    assert OTHER in svc.storage.moved_tenants()


# ── garbage collection ─────────────────────────────────────────────────────


def _collect(svc):
    from kazma_core.documents.retention import DocumentGarbageCollector

    config = replace(svc.config, gc_grace_seconds=0, retention_quarantine_days=0)
    time.sleep(0.02)
    return DocumentGarbageCollector(repository=svc.repository, storage=svc.storage,
                                    audit=svc.audit, config=config).collect(dry_run=False)


def _age(path: Path) -> None:
    old = time.time() - 3600
    os.utime(path, (old, old))


def test_gc_keeps_the_root_copy_after_the_copy(svc, monkeypatch):
    """Rollback safety: a copied space's root files stay, so a revert reads
    every document."""
    old = seed_document(svc, tenant_id=OTHER, owner="layla", marker="gcroot")
    _enforce(monkeypatch)
    copy_into_space_folders(svc.storage, svc.repository)
    legacy = svc.storage.root / legacy_blob_rel("originals", old.sha256)
    _age(legacy)
    report = _collect(svc)
    assert legacy.is_file(), f"the root copy a revert reads was collected: {report.to_dict()}"
    assert _older_build_reads(svc.storage.root, old)


def test_control_the_first_collector_loses_the_root_copy(svc, monkeypatch):
    # Negative control: the first version's root kept only the owner's
    # space and the tenants not yet copied.
    from kazma_core.documents.retention import DocumentGarbageCollector

    old = seed_document(svc, tenant_id=OTHER, owner="layla", marker="lost")
    _enforce(monkeypatch)
    copy_into_space_folders(svc.storage, svc.repository)
    real_folders = DocumentGarbageCollector._folders

    def first_rule(self, marks, report):
        folders = real_folders(self, marks, report)
        owners = (marks["keep_source_by_tenant"].get(OWNER, set()),
                  marks["keep_artifacts_by_tenant"].get(OWNER, set()),
                  marks["keep_versions_by_tenant"].get(OWNER, set()))
        root = replace(folders[0], keep_source=set(owners[0]), keep_artifacts=set(owners[1]),
                       keep_versions=set(owners[2]), tenants=(OWNER,))
        return [root, *folders[1:]]

    monkeypatch.setattr(DocumentGarbageCollector, "_folders", first_rule)
    legacy = svc.storage.root / legacy_blob_rel("originals", old.sha256)
    _age(legacy)
    _collect(svc)
    assert not legacy.exists(), "the control should lose the root copy"


def test_gc_never_keeps_a_file_in_a_folder_for_another_spaces_reference(svc, enforced):
    """The same bytes in two spaces' folders, referenced in one: the other's
    copy goes; the root keeps every tenant's."""
    referenced = seed_document(svc, tenant_id="beta", owner="layla", marker="shared bytes")
    stray = svc.storage.for_space(OTHER).put_stream(io.BytesIO(b"shared bytes"), kind="originals",
                                                    max_bytes=100)
    assert stray.sha256 == referenced.sha256
    _age(stray.path)
    _age(svc.storage.root / blob_rel("beta", "originals", referenced.sha256))
    _collect(svc)
    assert not stray.path.exists(), "a space's folder kept a file only another space references"
    assert (svc.storage.root / blob_rel("beta", "originals", referenced.sha256)).is_file()


def test_an_erased_spaces_folder_is_collected(svc, enforced):
    gone = svc.storage.for_space("erased").put_stream(io.BytesIO(b"left behind"), kind="originals",
                                                      max_bytes=100)
    manifest = svc.storage.for_space("erased").write_manifest(
        document_id="11111111-1111-4111-8111-111111111111",
        version_id="22222222-2222-4222-8222-222222222222", manifest={"ir": {}},
    )
    assert gone.path.is_relative_to(svc.storage.root / "spaces" / "erased")
    _age(gone.path)
    _age(manifest)
    _collect(svc)
    assert not gone.path.exists() and not manifest.exists()


def test_a_folder_that_cannot_be_listed_is_a_warning(svc, monkeypatch, caplog, enforced):
    seed_document(svc, tenant_id=OTHER, owner="layla", marker="listed")
    real_scandir = os.scandir

    def refuses(path, *args, **kwargs):
        if Path(path).name == "spaces":
            raise PermissionError("denied")
        return real_scandir(path, *args, **kwargs)

    monkeypatch.setattr("kazma_core.documents.retention.os.scandir", refuses)
    with caplog.at_level(logging.WARNING, logger="kazma_core.documents.retention"):
        report = _collect(svc)
    assert "space_folders:PermissionError" in report.errors
    assert any("could not be listed" in r.getMessage() for r in caplog.records)


# ── backups once Spaces is enforced ────────────────────────────────────────


def test_a_backup_holds_each_space_where_it_is_kept_and_restores(svc, tmp_path, monkeypatch):
    mine = seed_document(svc, tenant_id=OWNER, owner="owner", marker="owners backup")
    old = seed_document(svc, tenant_id=OTHER, owner="layla", marker="others backup")
    _enforce(monkeypatch)
    copy_into_space_folders(svc.storage, svc.repository)
    report = perform_document_backup(storage_root=svc.storage.root, dest_dir=tmp_path / "backups")
    assert report["ok"], report
    keys = set(report["checksums"])
    assert blob_rel(OWNER, "originals", mine.sha256) in keys
    assert blob_rel(OTHER, "originals", old.sha256) in keys
    assert manifest_rel(OTHER, old.document_id, old.version_id) in keys

    restored_root = tmp_path / "restored"
    shutil.copytree(report["path"], restored_root)
    restored = seeded_service(restored_root)
    try:
        assert "others backup" in restored.get_content(
            tenant_id=OTHER, actor_id="layla", document_id=old.document_id)["text"]
        assert "owners backup" in restored.get_content(
            tenant_id=OWNER, actor_id="owner", document_id=mine.document_id)["text"]
    finally:
        restored.close()


def test_a_backup_from_before_spaces_verifies_once_spaces_is_enforced(svc, tmp_path, monkeypatch):
    seed_document(svc, tenant_id=OTHER, owner="layla", marker="older backup")
    report = perform_document_backup(storage_root=svc.storage.root, dest_dir=tmp_path / "backups")
    _enforce(monkeypatch)
    assert verify_document_backup(backup_dir=Path(report["path"]))["ok"]
