"""Reading a document-store backup leaves no ``-wal`` or ``-shm`` beside it.

Audit 2026-10-26 AUD-116 (measured here first: the finding was marked
"needs runtime verification"). ``sqlite3.backup()`` keeps the source's WAL
journal mode in the copy's header, and opening such a copy ``?mode=ro`` --
as the backup's reference read and its verify did -- creates
``documents.db-wal`` and ``documents.db-shm`` in the backup folder: files its
checksums never named, left in every backup. ``immutable=1`` reads without
them. The negative control is the old open: the files appear.
"""

from __future__ import annotations

import sqlite3

from kazma_core.documents.backup import _read_references, verify_document_backup
from kazma_core.memory.backup import backup_one


def _wal_snapshot(tmp_path):
    src = tmp_path / "live.db"
    conn = sqlite3.connect(src)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE documents (id TEXT, tenant_id TEXT, deleted_at TEXT)")
        conn.execute(
            "CREATE TABLE document_versions (id TEXT, tenant_id TEXT, document_id TEXT, "
            "source_blob_id TEXT, source_sha256 TEXT)"
        )
        conn.execute("CREATE TABLE document_blobs (id TEXT, tenant_id TEXT, sha256 TEXT, storage_kind TEXT)")
        conn.execute(
            "CREATE TABLE document_artifacts (id TEXT, tenant_id TEXT, document_id TEXT, blob_id TEXT)"
        )
        conn.commit()
        out = tmp_path / "backup"
        out.mkdir()
        assert backup_one(src, out / "documents.db")
    finally:
        conn.close()
    header = (out / "documents.db").read_bytes()[18:20]
    assert header == b"\x02\x02", "the snapshot is not in WAL mode; the test proves nothing"
    return out


def _sidecars(folder):
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("documents.db-"))


def test_reading_a_snapshot_creates_no_sidecar_files(tmp_path):
    out = _wal_snapshot(tmp_path)
    _read_references(out / "documents.db")
    assert _sidecars(out) == []


def test_verifying_a_backup_creates_no_sidecar_files(tmp_path):
    out = _wal_snapshot(tmp_path)
    verify_document_backup(backup_dir=out, checksums={})
    assert _sidecars(out) == []


def test_the_old_read_only_open_creates_them(tmp_path):
    """Negative control: the instrument sees the sidecars ``mode=ro`` makes."""
    out = _wal_snapshot(tmp_path)
    conn = sqlite3.connect((out / "documents.db").resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("SELECT count(*) FROM documents").fetchone()
    finally:
        conn.close()
    assert _sidecars(out) == ["documents.db-shm", "documents.db-wal"]
