"""A deleted document's missing file must not fail a migrate export.

A blob still referenced by a live document stays required.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kazma_core.documents.backup import _read_references
from kazma_core.migration.bundle import Manifest
from kazma_core.migration.exporter import _export_document_store

_LIVE = "ab" * 32
_DEAD = "cd" * 32
_SHARED = "ef" * 32


def _store(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    db = root / "documents.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            title TEXT,
            deleted_at TEXT
        );
        CREATE TABLE document_blobs (
            id TEXT PRIMARY KEY,
            sha256 TEXT,
            storage_kind TEXT
        );
        CREATE TABLE document_versions (
            id TEXT PRIMARY KEY,
            document_id TEXT,
            source_blob_id TEXT
        );
        CREATE TABLE document_artifacts (
            id TEXT PRIMARY KEY,
            document_id TEXT,
            blob_id TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO documents (id, title, deleted_at) VALUES (?, ?, ?)",
        [
            ("live-doc", "live", None),
            ("dead-doc", "deleted", "2026-08-11T00:00:00Z"),
        ],
    )
    conn.executemany(
        "INSERT INTO document_blobs (id, sha256, storage_kind) VALUES (?, ?, ?)",
        [
            ("blob-live", _LIVE, "quarantine"),
            ("blob-dead", _DEAD, "quarantine"),
            ("blob-shared", _SHARED, "originals"),
        ],
    )
    conn.executemany(
        "INSERT INTO document_versions (id, document_id, source_blob_id) VALUES (?, ?, ?)",
        [
            ("ver-live", "live-doc", "blob-live"),
            ("ver-dead", "dead-doc", "blob-dead"),
            ("ver-dead-shared", "dead-doc", "blob-shared"),
            ("ver-live-shared", "live-doc", "blob-shared"),
        ],
    )
    conn.commit()
    conn.close()
    return db


def _touch(root: Path, kind: str, sha: str, payload: bytes) -> None:
    path = root / kind / "sha256" / sha[:2] / sha[2:4] / sha
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_read_references_skips_deleted_documents_only(tmp_path: Path) -> None:
    db = _store(tmp_path / "store")
    refs, versions = _read_references(db)
    shas = {sha for sha, _kind in refs}
    assert _LIVE in shas
    assert _SHARED in shas
    assert _DEAD not in shas
    assert ("dead-doc", "ver-dead") not in versions
    assert ("live-doc", "ver-live") in versions


def test_export_ignores_a_missing_deleted_blob_and_rejects_a_missing_live_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "store"
    _store(root)
    _touch(root, "quarantine", _LIVE, b"live-bytes")
    _touch(root, "originals", _SHARED, b"shared-bytes")
    # The deleted document's file is gone on purpose.

    class _Cfg:
        storage_root = root

    monkeypatch.setattr(
        "kazma_core.documents.config.get_document_config",
        lambda: _Cfg(),
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    manifest = Manifest()
    _export_document_store(staging, manifest, lambda _msg: None)
    copied = manifest.table_counts["_document_store"]
    assert copied["blobs"] == 2
    assert "error" not in copied

    live_file = root / "quarantine" / "sha256" / _LIVE[:2] / _LIVE[2:4] / _LIVE
    live_file.unlink()
    staging2 = tmp_path / "staging-live-missing"
    staging2.mkdir()
    manifest2 = Manifest()
    with pytest.raises(RuntimeError, match="document store is incomplete"):
        _export_document_store(staging2, manifest2, lambda _msg: None)
