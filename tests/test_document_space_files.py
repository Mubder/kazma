"""Each space's document files are kept in its own folder (Spaces S1-12).

So one space can be exported or erased alone: every byte of a document -- its
original, its artifacts, its parsed manifest -- is under its space's folder
(``storage.space_folder``): the storage root for the owner's space (an
install's documents do not move), ``spaces/<id>`` for any other.

An install of the older multi-user mode kept another tenant's files in the
root. ``documents.space_files`` copies them into their space's folder once,
journaled and safe to interrupt; until then reads fall back to the root and
garbage collection keeps the root copies.

Negative controls: one folder for every tenant (before Spaces) shares a file
between two spaces; a collector that took a tenant as copied before it was
loses that tenant's only copy.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from kazma_core.documents import storage as storage_module
from kazma_core.documents.backup import perform_document_backup, verify_document_backup
from kazma_core.documents.space_files import copy_into_space_folders
from kazma_core.documents.storage import (
    SPACE_FILES_JOURNAL,
    ContentAddressedStorage,
    blob_rel,
    manifest_rel,
    space_folder,
)

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


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── the folder of a space ───────────────────────────────────────────────────


def test_the_owners_space_is_the_root_and_others_have_a_folder():
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


def test_two_spaces_never_share_a_file(tmp_path):
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


def test_control_one_folder_for_every_tenant_shares_the_file(tmp_path, monkeypatch):
    # Negative control: before Spaces every tenant's files were the root's.
    monkeypatch.setattr(storage_module, "space_folder", lambda _tenant: "")
    store = ContentAddressedStorage(tmp_path)
    store.for_space(OWNER).put_stream(io.BytesIO(b"same bytes"), kind="originals", max_bytes=100)
    theirs = store.for_space(OTHER).put_stream(io.BytesIO(b"same bytes"), kind="originals", max_bytes=100)
    assert theirs.reused, "one file was shared by two spaces"


def test_a_document_of_each_space_is_under_its_folder(svc):
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


def test_the_owners_documents_do_not_move(svc):
    """A single-account install: its files stay where they are, and the copy
    for an older install does nothing."""
    mine = seed_document(svc, tenant_id=OWNER, owner="owner", marker="stays")
    before = sorted(p.relative_to(svc.storage.root) for p in svc.storage.root.rglob("*") if p.is_file())
    report = copy_into_space_folders(svc.storage, svc.repository)
    after = sorted(p.relative_to(svc.storage.root) for p in svc.storage.root.rglob("*") if p.is_file())
    assert report == {"moved": [], "left": []}
    assert after == before
    assert not (svc.storage.root / "spaces").exists()
    assert not (svc.storage.root / SPACE_FILES_JOURNAL).exists()
    assert "stays" in svc.get_content(tenant_id=OWNER, actor_id="owner", document_id=mine.document_id)["text"]


# ── an older install: another tenant's files in the root ───────────────────


def _older_install(svc, marker: str = "older"):
    """A document of tenant OTHER whose files an older install kept in the
    root, where every tenant's were."""
    seeded = seed_document(svc, tenant_id=OTHER, owner="layla", marker=marker)
    root = svc.storage.root
    for rel_of in (lambda t: blob_rel(t, "originals", seeded.sha256),
                   lambda t: manifest_rel(t, seeded.document_id, seeded.version_id)):
        target = root / rel_of(OWNER)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(root / rel_of(OTHER), target)
    shutil.rmtree(root / "spaces")
    return seeded


def test_an_older_tenants_files_are_read_from_the_root_until_copied(svc):
    old = _older_install(svc, "fromroot")
    space = svc.storage.for_space(OTHER)
    located = space.locate_blob(kind="originals", sha256=old.sha256)
    assert located == svc.storage.root / blob_rel(OWNER, "originals", old.sha256)
    assert "fromroot" in svc.get_content(tenant_id=OTHER, actor_id="layla", document_id=old.document_id)["text"]


def test_the_copy_is_journaled_and_the_tenant_then_reads_its_folder(svc):
    old = _older_install(svc, "copied")
    report = copy_into_space_folders(svc.storage, svc.repository)
    assert report["moved"] == [OTHER] and not report["left"]
    root = svc.storage.root
    assert (root / blob_rel(OTHER, "originals", old.sha256)).is_file()
    assert (root / manifest_rel(OTHER, old.document_id, old.version_id)).is_file()
    journal = json.loads((root / SPACE_FILES_JOURNAL).read_text(encoding="utf-8"))
    assert journal["moved"] == [OTHER] and journal["copying"] is None
    assert not svc.storage.reads_root_for(OTHER)
    # A fresh process reads the journal.
    assert not ContentAddressedStorage(root).reads_root_for(OTHER)
    assert "copied" in svc.get_content(tenant_id=OTHER, actor_id="layla", document_id=old.document_id)["text"]
    assert copy_into_space_folders(svc.storage, svc.repository) == {"moved": [], "left": []}


def test_an_interrupted_copy_goes_on_where_it_stopped(svc, monkeypatch):
    from kazma_core.documents import space_files

    old = _older_install(svc, "interrupted")
    root = svc.storage.root
    real_copy = space_files._copy_manifest

    def dies(*_args, **_kwargs):
        raise OSError("the process stopped")

    monkeypatch.setattr(space_files, "_copy_manifest", dies)
    with pytest.raises(OSError):
        copy_into_space_folders(svc.storage, svc.repository)
    journal = json.loads((root / SPACE_FILES_JOURNAL).read_text(encoding="utf-8"))
    assert journal["copying"] == OTHER and OTHER not in journal["moved"], "the step was recorded first"
    assert svc.storage.reads_root_for(OTHER), "a half-copied space still reads the root"
    copied_blob = root / blob_rel(OTHER, "originals", old.sha256)
    assert copied_blob.is_file()
    stamp = copied_blob.stat().st_mtime_ns

    monkeypatch.setattr(space_files, "_copy_manifest", real_copy)
    fresh = ContentAddressedStorage(root)
    assert copy_into_space_folders(fresh, svc.repository)["moved"] == [OTHER]
    assert copied_blob.stat().st_mtime_ns == stamp, "a file already in place is not copied again"
    assert (root / manifest_rel(OTHER, old.document_id, old.version_id)).is_file()


def test_a_stop_leaves_the_tenant_for_the_next_start(svc):
    _older_install(svc, "stopped")
    stop = threading.Event()
    stop.set()
    report = copy_into_space_folders(svc.storage, svc.repository, stop=stop)
    assert report == {"moved": [], "left": [OTHER]}
    assert svc.storage.reads_root_for(OTHER)


def test_a_file_that_does_not_match_its_digest_is_left_in_the_root(svc):
    old = _older_install(svc, "corrupt")
    legacy = svc.storage.root / blob_rel(OWNER, "originals", old.sha256)
    legacy.write_bytes(b"not what the catalog says")
    report = copy_into_space_folders(svc.storage, svc.repository)
    assert report["left"] == [OTHER]
    assert not (svc.storage.root / blob_rel(OTHER, "originals", old.sha256)).exists()
    assert svc.storage.reads_root_for(OTHER)


# ── garbage collection per folder ──────────────────────────────────────────


def _collect(svc):
    from kazma_core.documents.retention import DocumentGarbageCollector

    config = replace(svc.config, gc_grace_seconds=0, retention_quarantine_days=0)
    time.sleep(0.02)
    return DocumentGarbageCollector(repository=svc.repository, storage=svc.storage,
                                    audit=svc.audit, config=config).collect(dry_run=False)


def _age(path: Path) -> None:
    old = time.time() - 3600
    os.utime(path, (old, old))


def test_gc_keeps_an_uncopied_tenants_root_files_then_collects_them(svc):
    old = _older_install(svc, "gcroot")
    legacy = svc.storage.root / blob_rel(OWNER, "originals", old.sha256)
    _age(legacy)
    report = _collect(svc)
    assert legacy.is_file(), f"the only copy of an uncopied tenant's file was collected: {report.to_dict()}"
    copy_into_space_folders(svc.storage, svc.repository)
    _age(legacy)
    _collect(svc)
    assert not legacy.exists(), "the root copy nobody in the owner's space references stays"
    assert "gcroot" in svc.get_content(tenant_id=OTHER, actor_id="layla", document_id=old.document_id)["text"]


def test_control_a_collector_that_took_the_tenant_as_copied_loses_its_file(svc, monkeypatch):
    # Negative control: the root's keep set without the uncopied tenant.
    old = _older_install(svc, "lost")
    legacy = svc.storage.root / blob_rel(OWNER, "originals", old.sha256)
    _age(legacy)
    monkeypatch.setattr(svc.storage, "moved_tenants", lambda: frozenset({OTHER}))
    _collect(svc)
    assert not legacy.exists(), "the control should lose the file"


def test_gc_never_keeps_a_file_for_another_spaces_reference(svc):
    """The same bytes in two folders, referenced in one: the other's copy goes."""
    referenced = seed_document(svc, tenant_id=OTHER, owner="layla", marker="shared bytes")
    stray = svc.storage.for_space(OWNER).put_stream(io.BytesIO(b"shared bytes"), kind="originals",
                                                    max_bytes=100)
    assert stray.sha256 == referenced.sha256
    # Start-up records the space as copied (its files are in its folder);
    # until then the root keeps its references, the safe side.
    assert copy_into_space_folders(svc.storage, svc.repository)["moved"] == [OTHER]
    _age(stray.path)
    _age(svc.storage.root / blob_rel(OTHER, "originals", referenced.sha256))
    _collect(svc)
    assert not stray.path.exists(), "the owner's folder kept a file only another space references"
    assert (svc.storage.root / blob_rel(OTHER, "originals", referenced.sha256)).is_file()


def test_an_erased_spaces_folder_is_collected(svc):
    gone = svc.storage.for_space("erased").put_stream(io.BytesIO(b"left behind"), kind="originals",
                                                      max_bytes=100)
    manifest = svc.storage.for_space("erased").write_manifest(
        document_id="11111111-1111-4111-8111-111111111111",
        version_id="22222222-2222-4222-8222-222222222222", manifest={"ir": {}},
    )
    _age(gone.path)
    _age(manifest)
    _collect(svc)
    assert not gone.path.exists() and not manifest.exists()


# ── backups hold each space under its folder ───────────────────────────────


def test_a_backup_holds_each_space_under_its_folder_and_restores(svc, tmp_path):
    mine = seed_document(svc, tenant_id=OWNER, owner="owner", marker="owners backup")
    old = _older_install(svc, "others backup")  # still in the root: read from there
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


def test_a_backup_from_before_spaces_still_verifies(svc, tmp_path):
    """A backup an older build made holds another tenant's files in the root."""
    old = seed_document(svc, tenant_id=OTHER, owner="layla", marker="older backup")
    report = perform_document_backup(storage_root=svc.storage.root, dest_dir=tmp_path / "backups")
    out = Path(report["path"])
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for rel_of in (lambda t: blob_rel(t, "originals", old.sha256),
                   lambda t: manifest_rel(t, old.document_id, old.version_id)):
        new, legacy = rel_of(OTHER), rel_of(OWNER)
        (out / legacy).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(out / new, out / legacy)
        manifest["checksums"][legacy] = manifest["checksums"].pop(new)
    shutil.rmtree(out / "spaces")
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert verify_document_backup(backup_dir=out)["ok"]
