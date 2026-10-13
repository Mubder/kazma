"""Garbage collection never deletes what a running backup's snapshot needs.

2026-10-13 (F06). The coordinated Postgres backup reads which blobs and
manifests its snapshot references, pins them, then copies them. A blob
unreferenced since the snapshot (and past the GC grace period) would have
been deletable in between. The pin is re-read immediately before every
deletion. Negative control: the same orphan without a pin is deleted. A pin
left by a backup that died stops protecting after a day.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from kazma_core.documents import admission
from kazma_core.documents.backup_pg import backup_pins
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.ingestion import DocumentIngestionService


def _orphan(root, kind: str = "originals"):
    sha = hashlib.sha256(os.urandom(16)).hexdigest()
    path = root / kind / "sha256" / sha[:2] / sha[2:4] / sha
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"orphaned bytes")
    old = time.time() - 30 * 24 * 3600
    os.utime(path, (old, old))
    return f"{kind}/sha256/{sha[:2]}/{sha[2:4]}/{sha}", path


def _pin(root, paths, *, age: float = 0.0):
    pin = root / ".backup-pins" / "document-pg-test.json"
    pin.parent.mkdir(parents=True, exist_ok=True)
    pin.write_text(json.dumps({"paths": paths}), encoding="utf-8")
    if age:
        stamp = time.time() - age
        os.utime(pin, (stamp, stamp))
    return pin


def _service(root):
    return DocumentIngestionService(config=DocumentConfig(storage_root=root, security_malware_scan="off"))


def test_a_pinned_orphan_survives_garbage_collection(tmp_path):
    svc = _service(tmp_path)
    try:
        rel, path = _orphan(tmp_path)
        _pin(tmp_path, [rel])
        svc.run_maintenance(dry_run=False)
        assert path.exists()
    finally:
        svc.close()


def test_an_unpinned_orphan_is_deleted(tmp_path):
    # Negative control: the orphan really is what GC removes.
    svc = _service(tmp_path)
    try:
        _rel, path = _orphan(tmp_path)
        svc.run_maintenance(dry_run=False)
        assert not path.exists()
    finally:
        svc.close()


def test_a_pin_left_by_a_dead_backup_expires(tmp_path):
    svc = _service(tmp_path)
    try:
        rel, path = _orphan(tmp_path)
        _pin(tmp_path, [rel], age=2 * 24 * 3600)
        assert backup_pins(tmp_path) == set()
        svc.run_maintenance(dry_run=False)
        assert not path.exists()
    finally:
        svc.close()


def test_a_pinned_manifest_survives(tmp_path):
    svc = _service(tmp_path)
    try:
        manifest = tmp_path / "manifests" / "doc-gone" / "ver-gone.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}", encoding="utf-8")
        old = time.time() - 30 * 24 * 3600
        os.utime(manifest, (old, old))
        _pin(tmp_path, ["manifests/doc-gone/ver-gone.json"])
        svc.run_maintenance(dry_run=False)
        assert manifest.exists()
        (tmp_path / ".backup-pins" / "document-pg-test.json").unlink()
        svc.run_maintenance(dry_run=False)
        assert not manifest.exists()
    finally:
        svc.close()


def test_a_restored_reservation_owner_is_dead():
    # Restored from a backup: its publisher wrote to another database.
    assert admission.owner_state(json.dumps({"restored": "document-pg-1"})) == "dead"
    assert admission.owner_state(json.dumps({"install": "x", "host": "elsewhere", "pid": 1, "started": "1"})) == "unknown"
