"""Tenant-isolated SQLite metadata repository for document intelligence."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

from .models import (
    ArtifactId,
    BlobId,
    DocumentId,
    JsonValue,
    VersionId,
    new_artifact_id,
    new_blob_id,
    new_document_id,
    new_version_id,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ArtifactRecord",
    "BlobRecord",
    "DocumentAccessError",
    "DocumentRecord",
    "DocumentChunkRecord",
    "DocumentRepository",
    "TombstoneRecord",
    "VersionRecord",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BLOB_KINDS = frozenset({"quarantine", "originals", "artifacts"})
_ACL_PERMISSIONS = frozenset({"read", "write", "owner"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    current_version_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    PRIMARY KEY (id),
    UNIQUE (id, tenant_id)
);

CREATE TABLE IF NOT EXISTS document_blobs (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    storage_kind TEXT NOT NULL CHECK (storage_kind IN ('quarantine', 'originals', 'artifacts')),
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, sha256, storage_kind)
);

CREATE TABLE IF NOT EXISTS document_versions (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    source_blob_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, document_id, version_number),
    UNIQUE (tenant_id, document_id, source_sha256),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents(id, tenant_id),
    FOREIGN KEY (source_blob_id, tenant_id) REFERENCES document_blobs(id, tenant_id)
);

CREATE TABLE IF NOT EXISTS document_artifacts (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, version_id, artifact_type, blob_id),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents(id, tenant_id),
    FOREIGN KEY (version_id, tenant_id) REFERENCES document_versions(id, tenant_id),
    FOREIGN KEY (blob_id, tenant_id) REFERENCES document_blobs(id, tenant_id)
);

CREATE TABLE IF NOT EXISTS document_acl (
    tenant_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    permission TEXT NOT NULL CHECK (permission IN ('read', 'write', 'owner')),
    granted_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, document_id, principal_id),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents(id, tenant_id)
);

CREATE TABLE IF NOT EXISTS document_tombstones (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    deleted_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, document_id),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents(id, tenant_id)
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    library_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_hash TEXT NOT NULL,
    page_start INTEGER NOT NULL,
    page_end INTEGER NOT NULL,
    block_ids_json TEXT NOT NULL,
    block_hashes_json TEXT NOT NULL,
    coordinates_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    index_status TEXT NOT NULL CHECK (
        index_status IN ('indexed', 'failed', 'tombstoned')
    ),
    active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, library_id, id),
    UNIQUE (tenant_id, library_id, version_id, chunk_index),
    FOREIGN KEY (document_id, tenant_id) REFERENCES documents(id, tenant_id),
    FOREIGN KEY (version_id, tenant_id) REFERENCES document_versions(id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_updated
    ON documents(tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_versions_tenant_document
    ON document_versions(tenant_id, document_id, version_number);
CREATE INDEX IF NOT EXISTS idx_blobs_tenant_sha
    ON document_blobs(tenant_id, sha256);
CREATE INDEX IF NOT EXISTS idx_artifacts_tenant_version
    ON document_artifacts(tenant_id, version_id);
CREATE INDEX IF NOT EXISTS idx_acl_tenant_principal
    ON document_acl(tenant_id, principal_id);
CREATE INDEX IF NOT EXISTS idx_document_chunks_scope
    ON document_chunks(tenant_id, library_id, document_id, active);
"""


class DocumentAccessError(PermissionError):
    """Raised when a tenant or principal cannot access a document resource."""


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    id: DocumentId
    tenant_id: str
    owner_id: str
    title: str
    current_version_id: VersionId | None
    metadata: dict[str, JsonValue]
    created_at: str
    updated_at: str
    deleted_at: str | None


@dataclass(frozen=True, slots=True)
class BlobRecord:
    id: BlobId
    tenant_id: str
    sha256: str
    byte_size: int
    storage_kind: str
    created_at: str


@dataclass(frozen=True, slots=True)
class VersionRecord:
    id: VersionId
    tenant_id: str
    document_id: DocumentId
    version_number: int
    source_blob_id: BlobId
    source_sha256: str
    original_filename: str
    mime_type: str
    metadata: dict[str, JsonValue]
    created_at: str


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: ArtifactId
    tenant_id: str
    document_id: DocumentId
    version_id: VersionId
    blob_id: BlobId
    artifact_type: str
    metadata: dict[str, JsonValue]
    created_at: str


@dataclass(frozen=True, slots=True)
class TombstoneRecord:
    id: str
    tenant_id: str
    document_id: DocumentId
    deleted_by: str
    reason: str
    deleted_at: str


@dataclass(frozen=True, slots=True)
class DocumentChunkRecord:
    id: str
    tenant_id: str
    library_id: str
    document_id: DocumentId
    version_id: VersionId
    source_url: str
    source_sha256: str
    chunk_index: int
    chunk_hash: str
    page_start: int
    page_end: int
    block_ids: tuple[str, ...]
    block_hashes: tuple[str, ...]
    coordinates: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]
    metadata: dict[str, Any]
    index_status: str
    active: bool
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _required(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _sha256(value: str) -> str:
    normalized = str(value).strip()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("sha256 must contain exactly 64 lowercase hexadecimal characters")
    return normalized


def _json_object(value: Mapping[str, Any] | None) -> str:
    candidate = dict(value or {})
    try:
        return json.dumps(
            candidate,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be a JSON-serializable object") from exc


def _decode_json(value: str) -> dict[str, JsonValue]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("stored metadata is not an object")
    return decoded


def _document(row: sqlite3.Row) -> DocumentRecord:
    return DocumentRecord(
        id=DocumentId(row["id"]),
        tenant_id=row["tenant_id"],
        owner_id=row["owner_id"],
        title=row["title"],
        current_version_id=(
            VersionId(row["current_version_id"]) if row["current_version_id"] else None
        ),
        metadata=_decode_json(row["metadata_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
    )


def _blob(row: sqlite3.Row) -> BlobRecord:
    return BlobRecord(
        id=BlobId(row["id"]),
        tenant_id=row["tenant_id"],
        sha256=row["sha256"],
        byte_size=int(row["byte_size"]),
        storage_kind=row["storage_kind"],
        created_at=row["created_at"],
    )


def _version(row: sqlite3.Row) -> VersionRecord:
    return VersionRecord(
        id=VersionId(row["id"]),
        tenant_id=row["tenant_id"],
        document_id=DocumentId(row["document_id"]),
        version_number=int(row["version_number"]),
        source_blob_id=BlobId(row["source_blob_id"]),
        source_sha256=row["source_sha256"],
        original_filename=row["original_filename"],
        mime_type=row["mime_type"],
        metadata=_decode_json(row["metadata_json"]),
        created_at=row["created_at"],
    )


def _artifact(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        id=ArtifactId(row["id"]),
        tenant_id=row["tenant_id"],
        document_id=DocumentId(row["document_id"]),
        version_id=VersionId(row["version_id"]),
        blob_id=BlobId(row["blob_id"]),
        artifact_type=row["artifact_type"],
        metadata=_decode_json(row["metadata_json"]),
        created_at=row["created_at"],
    )


def _chunk(row: sqlite3.Row) -> DocumentChunkRecord:
    return DocumentChunkRecord(
        id=row["id"],
        tenant_id=row["tenant_id"],
        library_id=row["library_id"],
        document_id=DocumentId(row["document_id"]),
        version_id=VersionId(row["version_id"]),
        source_url=row["source_url"],
        source_sha256=row["source_sha256"],
        chunk_index=int(row["chunk_index"]),
        chunk_hash=row["chunk_hash"],
        page_start=int(row["page_start"]),
        page_end=int(row["page_end"]),
        block_ids=tuple(json.loads(row["block_ids_json"])),
        block_hashes=tuple(json.loads(row["block_hashes_json"])),
        coordinates=tuple(json.loads(row["coordinates_json"])),
        provenance=dict(json.loads(row["provenance_json"])),
        metadata=dict(json.loads(row["metadata_json"])),
        index_status=row["index_status"],
        active=bool(row["active"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class DocumentRepository:
    """Durable Phase 2 metadata store with mandatory tenant constraints."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        tenant_quota_bytes: int | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        if tenant_quota_bytes is None:
            from .config import get_document_config

            tenant_quota_bytes = get_document_config().quota_tenant_bytes
        if isinstance(tenant_quota_bytes, bool) or int(tenant_quota_bytes) <= 0:
            raise ValueError("tenant_quota_bytes must be a positive integer")
        self._tenant_quota_bytes = int(tenant_quota_bytes)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        apply_sqlite_pragmas(self._conn, busy_timeout=5000)
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        """Close this repository's connection."""
        with self._lock:
            self._conn.close()

    def create_document(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        title: str,
        metadata: Mapping[str, Any] | None = None,
        document_id: DocumentId | None = None,
    ) -> DocumentRecord:
        tenant = _required(tenant_id, "tenant_id")
        owner = _required(owner_id, "owner_id")
        normalized_title = _required(title, "title")
        identifier = DocumentId(document_id) if document_id else new_document_id()
        now = _now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO documents
                    (id, tenant_id, owner_id, title, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(identifier),
                    tenant,
                    owner,
                    normalized_title,
                    _json_object(metadata),
                    now,
                    now,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM documents WHERE tenant_id = ? AND id = ?",
                (tenant, str(identifier)),
            ).fetchone()
        if row is None:
            raise RuntimeError("document insert succeeded but record was not found")
        return _document(row)

    def get_document(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        actor_id: str | None = None,
        include_deleted: bool = False,
    ) -> DocumentRecord | None:
        tenant = _required(tenant_id, "tenant_id")
        identifier = DocumentId(document_id)
        with self._lock:
            suffix = "" if include_deleted else " AND deleted_at IS NULL"
            row = self._conn.execute(
                f"SELECT * FROM documents WHERE tenant_id = ? AND id = ?{suffix}",
                (tenant, str(identifier)),
            ).fetchone()
            if row is not None and actor_id is not None:
                self._require_access_locked(tenant, identifier, actor_id, "read")
        return _document(row) if row is not None else None

    def list_documents(
        self,
        *,
        tenant_id: str,
        owner_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[DocumentRecord]:
        tenant = _required(tenant_id, "tenant_id")
        clauses = ["tenant_id = ?"]
        params: list[str] = [tenant]
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(_required(owner_id, "owner_id"))
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM documents WHERE {' AND '.join(clauses)} ORDER BY created_at, id",
                params,
            ).fetchall()
        return [_document(row) for row in rows]

    def assert_owner(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        actor_id: str,
    ) -> None:
        tenant = _required(tenant_id, "tenant_id")
        identifier = DocumentId(document_id)
        actor = _required(actor_id, "actor_id")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM documents
                WHERE tenant_id = ? AND id = ? AND owner_id = ? AND deleted_at IS NULL
                """,
                (tenant, str(identifier), actor),
            ).fetchone()
        if row is None:
            raise DocumentAccessError("document is unavailable or actor is not its owner")

    def has_access(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        actor_id: str,
        permission: str = "read",
    ) -> bool:
        tenant = _required(tenant_id, "tenant_id")
        identifier = DocumentId(document_id)
        actor = _required(actor_id, "actor_id")
        if permission not in _ACL_PERMISSIONS:
            raise ValueError(f"invalid permission: {permission!r}")
        with self._lock:
            try:
                self._require_access_locked(tenant, identifier, actor, permission)
            except DocumentAccessError:
                return False
        return True

    def grant_access(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        actor_id: str,
        principal_id: str,
        permission: str,
    ) -> None:
        tenant = _required(tenant_id, "tenant_id")
        identifier = DocumentId(document_id)
        actor = _required(actor_id, "actor_id")
        principal = _required(principal_id, "principal_id")
        if permission not in _ACL_PERMISSIONS:
            raise ValueError(f"invalid permission: {permission!r}")
        with self._lock:
            self._require_owner_locked(tenant, identifier, actor)
            self._conn.execute(
                """
                INSERT INTO document_acl
                    (tenant_id, document_id, principal_id, permission, granted_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (tenant_id, document_id, principal_id)
                DO UPDATE SET permission = excluded.permission,
                              granted_by = excluded.granted_by,
                              created_at = excluded.created_at
                """,
                (tenant, str(identifier), principal, permission, actor, _now()),
            )

    def register_blob(
        self,
        *,
        tenant_id: str,
        sha256: str,
        byte_size: int,
        storage_kind: str,
        blob_id: BlobId | None = None,
    ) -> BlobRecord:
        tenant = _required(tenant_id, "tenant_id")
        digest = _sha256(sha256)
        if isinstance(byte_size, bool) or int(byte_size) < 0:
            raise ValueError("byte_size must be a non-negative integer")
        size = int(byte_size)
        if storage_kind not in _BLOB_KINDS:
            raise ValueError(f"invalid storage kind: {storage_kind!r}")
        identifier = BlobId(blob_id) if blob_id else new_blob_id()
        with self._lock:
            existing = self._conn.execute(
                """
                SELECT * FROM document_blobs
                WHERE tenant_id = ? AND sha256 = ? AND storage_kind = ?
                """,
                (tenant, digest, storage_kind),
            ).fetchone()
            if existing is not None:
                if int(existing["byte_size"]) != size:
                    raise ValueError("existing blob metadata has a conflicting byte size")
                return _blob(existing)
            self._conn.execute(
                """
                INSERT INTO document_blobs
                    (id, tenant_id, sha256, byte_size, storage_kind, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(identifier), tenant, digest, size, storage_kind, _now()),
            )
            row = self._conn.execute(
                "SELECT * FROM document_blobs WHERE tenant_id = ? AND id = ?",
                (tenant, str(identifier)),
            ).fetchone()
        if row is None:
            raise RuntimeError("blob insert succeeded but record was not found")
        return _blob(row)

    def get_blob(self, *, tenant_id: str, blob_id: BlobId) -> BlobRecord | None:
        tenant = _required(tenant_id, "tenant_id")
        identifier = BlobId(blob_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM document_blobs WHERE tenant_id = ? AND id = ?",
                (tenant, str(identifier)),
            ).fetchone()
        return _blob(row) if row is not None else None

    def create_version(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        actor_id: str,
        source_blob_id: BlobId,
        source_sha256: str,
        original_filename: str,
        mime_type: str,
        metadata: Mapping[str, Any] | None = None,
        version_id: VersionId | None = None,
    ) -> VersionRecord:
        tenant = _required(tenant_id, "tenant_id")
        doc_id = DocumentId(document_id)
        actor = _required(actor_id, "actor_id")
        blob_id = BlobId(source_blob_id)
        digest = _sha256(source_sha256)
        filename = _required(original_filename, "original_filename")
        mime = _required(mime_type, "mime_type")
        identifier = VersionId(version_id) if version_id else new_version_id()
        with self._lock:
            self._require_access_locked(tenant, doc_id, actor, "write")
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                blob_row = self._conn.execute(
                    "SELECT * FROM document_blobs WHERE tenant_id = ? AND id = ?",
                    (tenant, str(blob_id)),
                ).fetchone()
                if blob_row is None or blob_row["sha256"] != digest:
                    raise ValueError("source blob is unavailable or its checksum does not match")
                existing = self._conn.execute(
                    """
                    SELECT * FROM document_versions
                    WHERE tenant_id = ? AND document_id = ? AND source_sha256 = ?
                    """,
                    (tenant, str(doc_id), digest),
                ).fetchone()
                if existing is not None:
                    self._conn.execute("COMMIT")
                    return _version(existing)
                self._require_blob_quota_locked(tenant, blob_id)
                next_row = self._conn.execute(
                    """
                    SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version
                    FROM document_versions
                    WHERE tenant_id = ? AND document_id = ?
                    """,
                    (tenant, str(doc_id)),
                ).fetchone()
                number = int(next_row["next_version"])
                now = _now()
                self._conn.execute(
                    """
                    INSERT INTO document_versions
                        (id, tenant_id, document_id, version_number, source_blob_id,
                         source_sha256, original_filename, mime_type, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(identifier),
                        tenant,
                        str(doc_id),
                        number,
                        str(blob_id),
                        digest,
                        filename,
                        mime,
                        _json_object(metadata),
                        now,
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE documents SET current_version_id = ?, updated_at = ?
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (str(identifier), now, tenant, str(doc_id)),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            row = self._conn.execute(
                "SELECT * FROM document_versions WHERE tenant_id = ? AND id = ?",
                (tenant, str(identifier)),
            ).fetchone()
        if row is None:
            raise RuntimeError("version insert succeeded but record was not found")
        return _version(row)

    def get_version(
        self,
        *,
        tenant_id: str,
        version_id: VersionId,
        actor_id: str | None = None,
    ) -> VersionRecord | None:
        tenant = _required(tenant_id, "tenant_id")
        identifier = VersionId(version_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM document_versions WHERE tenant_id = ? AND id = ?",
                (tenant, str(identifier)),
            ).fetchone()
            if row is not None and actor_id is not None:
                self._require_access_locked(
                    tenant, DocumentId(row["document_id"]), actor_id, "read"
                )
        return _version(row) if row is not None else None

    def list_versions(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        actor_id: str | None = None,
    ) -> list[VersionRecord]:
        tenant = _required(tenant_id, "tenant_id")
        identifier = DocumentId(document_id)
        with self._lock:
            if actor_id is not None:
                self._require_access_locked(tenant, identifier, actor_id, "read")
            rows = self._conn.execute(
                """
                SELECT * FROM document_versions
                WHERE tenant_id = ? AND document_id = ?
                ORDER BY version_number
                """,
                (tenant, str(identifier)),
            ).fetchall()
        return [_version(row) for row in rows]

    def record_indexed_version(
        self,
        *,
        tenant_id: str,
        library_id: str,
        document_id: DocumentId,
        version_id: VersionId,
        chunks: list[Any],
    ) -> list[DocumentChunkRecord]:
        """Atomically record citation metadata and activate one indexed version."""
        tenant = _required(tenant_id, "tenant_id")
        library = _required(library_id, "library_id")
        document = DocumentId(document_id)
        version = VersionId(version_id)
        if not chunks:
            raise ValueError("at least one chunk is required")
        now = _now()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                version_row = self._conn.execute(
                    """SELECT source_sha256 FROM document_versions
                       WHERE tenant_id = ? AND document_id = ? AND id = ?""",
                    (tenant, str(document), str(version)),
                ).fetchone()
                if version_row is None:
                    raise DocumentAccessError("document version is unavailable in this tenant")
                self._conn.execute(
                    """UPDATE document_chunks SET active = 0, updated_at = ?
                       WHERE tenant_id = ? AND library_id = ? AND document_id = ?""",
                    (now, tenant, library, str(document)),
                )
                self._conn.execute(
                    """DELETE FROM document_chunks
                       WHERE tenant_id = ? AND library_id = ? AND version_id = ?""",
                    (tenant, library, str(version)),
                )
                for item in chunks:
                    if (
                        str(item.document_id) != str(document)
                        or str(item.version_id) != str(version)
                        or item.source_sha256 != version_row["source_sha256"]
                    ):
                        raise ValueError("chunk provenance does not match the stored version")
                    provenance = {
                        "parser": item.parser,
                        "parser_version": item.parser_version,
                    }
                    self._conn.execute(
                        """INSERT INTO document_chunks
                           (id, tenant_id, library_id, document_id, version_id,
                            source_url, source_sha256, chunk_index, chunk_hash,
                            page_start, page_end, block_ids_json, block_hashes_json,
                            coordinates_json, provenance_json, metadata_json,
                            index_status, active, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                   'indexed', 1, ?, ?)""",
                        (
                            item.chunk_id,
                            tenant,
                            library,
                            str(document),
                            str(version),
                            item.source_url,
                            item.source_sha256,
                            int(item.chunk_index),
                            item.chunk_hash,
                            int(item.page_start),
                            int(item.page_end),
                            json.dumps(list(item.block_ids), separators=(",", ":")),
                            json.dumps(list(item.block_hashes), separators=(",", ":")),
                            json.dumps(
                                list(item.coordinates),
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            _json_object(provenance),
                            _json_object(item.metadata),
                            now,
                            now,
                        ),
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return self.list_document_chunks(
            tenant_id=tenant,
            library_id=library,
            document_id=document,
            version_id=version,
            include_inactive=True,
        )

    def list_document_chunks(
        self,
        *,
        tenant_id: str,
        library_id: str,
        document_id: DocumentId,
        version_id: VersionId | None = None,
        include_inactive: bool = False,
    ) -> list[DocumentChunkRecord]:
        tenant = _required(tenant_id, "tenant_id")
        library = _required(library_id, "library_id")
        document = DocumentId(document_id)
        clauses = ["tenant_id = ?", "library_id = ?", "document_id = ?"]
        params: list[str] = [tenant, library, str(document)]
        if version_id is not None:
            clauses.append("version_id = ?")
            params.append(str(VersionId(version_id)))
        if not include_inactive:
            clauses.extend(["active = 1", "index_status = 'indexed'"])
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT * FROM document_chunks
                    WHERE {' AND '.join(clauses)}
                    ORDER BY version_id, chunk_index""",
                params,
            ).fetchall()
        return [_chunk(row) for row in rows]

    def tombstone_document_chunks(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        library_id: str | None = None,
    ) -> int:
        """Mark local index metadata unavailable before document blob GC."""
        tenant = _required(tenant_id, "tenant_id")
        document = DocumentId(document_id)
        library_clause = ""
        params: list[Any] = [_now(), tenant, str(document)]
        if library_id is not None:
            library_clause = " AND library_id = ?"
            params.append(_required(library_id, "library_id"))
        with self._lock:
            cursor = self._conn.execute(
                f"""UPDATE document_chunks
                   SET active = 0, index_status = 'tombstoned', updated_at = ?
                   WHERE tenant_id = ? AND document_id = ?
                     AND index_status != 'tombstoned'{library_clause}""",
                params,
            )
        return cursor.rowcount

    def list_indexed_libraries(
        self, *, tenant_id: str, document_id: DocumentId
    ) -> tuple[str, ...]:
        tenant = _required(tenant_id, "tenant_id")
        document = DocumentId(document_id)
        with self._lock:
            rows = self._conn.execute(
                """SELECT DISTINCT library_id FROM document_chunks
                   WHERE tenant_id = ? AND document_id = ?
                     AND active = 1 AND index_status = 'indexed'
                   ORDER BY library_id""",
                (tenant, str(document)),
            ).fetchall()
        return tuple(row["library_id"] for row in rows)

    def create_artifact(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        version_id: VersionId,
        actor_id: str,
        blob_id: BlobId,
        artifact_type: str,
        metadata: Mapping[str, Any] | None = None,
        artifact_id: ArtifactId | None = None,
    ) -> ArtifactRecord:
        tenant = _required(tenant_id, "tenant_id")
        doc_id = DocumentId(document_id)
        ver_id = VersionId(version_id)
        actor = _required(actor_id, "actor_id")
        blob_identifier = BlobId(blob_id)
        kind = _required(artifact_type, "artifact_type")
        identifier = ArtifactId(artifact_id) if artifact_id else new_artifact_id()
        with self._lock:
            self._require_access_locked(tenant, doc_id, actor, "write")
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                version_row = self._conn.execute(
                    """
                    SELECT 1 FROM document_versions
                    WHERE tenant_id = ? AND id = ? AND document_id = ?
                    """,
                    (tenant, str(ver_id), str(doc_id)),
                ).fetchone()
                blob_row = self._conn.execute(
                    "SELECT 1 FROM document_blobs WHERE tenant_id = ? AND id = ?",
                    (tenant, str(blob_identifier)),
                ).fetchone()
                if version_row is None or blob_row is None:
                    raise ValueError("version or blob is unavailable in this tenant")
                existing = self._conn.execute(
                    """
                    SELECT * FROM document_artifacts
                    WHERE tenant_id = ? AND version_id = ? AND artifact_type = ? AND blob_id = ?
                    """,
                    (tenant, str(ver_id), kind, str(blob_identifier)),
                ).fetchone()
                if existing is not None:
                    self._conn.execute("COMMIT")
                    return _artifact(existing)
                self._require_blob_quota_locked(tenant, blob_identifier)
                self._conn.execute(
                    """
                    INSERT INTO document_artifacts
                        (id, tenant_id, document_id, version_id, blob_id,
                         artifact_type, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(identifier),
                        tenant,
                        str(doc_id),
                        str(ver_id),
                        str(blob_identifier),
                        kind,
                        _json_object(metadata),
                        _now(),
                    ),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            row = self._conn.execute(
                "SELECT * FROM document_artifacts WHERE tenant_id = ? AND id = ?",
                (tenant, str(identifier)),
            ).fetchone()
        if row is None:
            raise RuntimeError("artifact insert succeeded but record was not found")
        return _artifact(row)

    def get_artifact(
        self,
        *,
        tenant_id: str,
        artifact_id: ArtifactId,
        actor_id: str | None = None,
    ) -> ArtifactRecord | None:
        tenant = _required(tenant_id, "tenant_id")
        identifier = ArtifactId(artifact_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM document_artifacts WHERE tenant_id = ? AND id = ?",
                (tenant, str(identifier)),
            ).fetchone()
            if row is not None and actor_id is not None:
                self._require_access_locked(
                    tenant, DocumentId(row["document_id"]), actor_id, "read"
                )
        return _artifact(row) if row is not None else None

    def list_artifacts(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        version_id: VersionId | None = None,
    ) -> list[ArtifactRecord]:
        """List a document's artifacts (newest first), optionally by version."""
        tenant = _required(tenant_id, "tenant_id")
        identifier = DocumentId(document_id)
        clauses = ["tenant_id = ?", "document_id = ?"]
        params: list[str] = [tenant, str(identifier)]
        if version_id is not None:
            clauses.append("version_id = ?")
            params.append(str(VersionId(version_id)))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM document_artifacts
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, id DESC
                """,
                params,
            ).fetchall()
        return [_artifact(row) for row in rows]

    def tombstone_document(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        actor_id: str,
        reason: str,
    ) -> TombstoneRecord:
        tenant = _required(tenant_id, "tenant_id")
        identifier = DocumentId(document_id)
        actor = _required(actor_id, "actor_id")
        normalized_reason = _required(reason, "reason")
        tombstone_id = str(new_artifact_id())
        now = _now()
        with self._lock:
            self._require_owner_locked(tenant, identifier, actor)
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    """
                    UPDATE documents SET deleted_at = ?, updated_at = ?
                    WHERE tenant_id = ? AND id = ?
                    """,
                    (now, now, tenant, str(identifier)),
                )
                self._conn.execute(
                    """
                    INSERT INTO document_tombstones
                        (id, tenant_id, document_id, deleted_by, reason, deleted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (tenant_id, document_id) DO NOTHING
                    """,
                    (tombstone_id, tenant, str(identifier), actor, normalized_reason, now),
                )
                self._conn.execute(
                    """UPDATE document_chunks
                       SET active = 0, index_status = 'tombstoned', updated_at = ?
                       WHERE tenant_id = ? AND document_id = ?""",
                    (now, tenant, str(identifier)),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            row = self._conn.execute(
                """
                SELECT * FROM document_tombstones
                WHERE tenant_id = ? AND document_id = ?
                """,
                (tenant, str(identifier)),
            ).fetchone()
        if row is None:
            raise RuntimeError("tombstone insert succeeded but record was not found")
        return TombstoneRecord(
            id=row["id"],
            tenant_id=row["tenant_id"],
            document_id=DocumentId(row["document_id"]),
            deleted_by=row["deleted_by"],
            reason=row["reason"],
            deleted_at=row["deleted_at"],
        )

    def tenant_referenced_blob_bytes(self, *, tenant_id: str) -> int:
        """Return unique bytes referenced by this tenant's versions/artifacts."""
        tenant = _required(tenant_id, "tenant_id")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COALESCE(SUM(byte_size), 0) AS total
                FROM (
                    SELECT b.sha256, b.storage_kind, MAX(b.byte_size) AS byte_size
                    FROM document_blobs b
                    JOIN (
                        SELECT source_blob_id AS blob_id
                        FROM document_versions WHERE tenant_id = ?
                        UNION
                        SELECT blob_id
                        FROM document_artifacts WHERE tenant_id = ?
                    ) refs ON refs.blob_id = b.id
                    WHERE b.tenant_id = ?
                    GROUP BY b.sha256, b.storage_kind
                )
                """,
                (tenant, tenant, tenant),
            ).fetchone()
        return int(row["total"])

    def tenant_references_sha256(
        self,
        *,
        tenant_id: str,
        sha256: str,
        storage_kind: str | None = None,
    ) -> bool:
        """Return whether the tenant already references this physical content."""
        tenant = _required(tenant_id, "tenant_id")
        digest = _sha256(sha256)
        if storage_kind is not None and storage_kind not in _BLOB_KINDS:
            raise ValueError(f"invalid storage kind: {storage_kind!r}")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1
                FROM document_blobs b
                WHERE b.tenant_id = ? AND b.sha256 = ?
                  AND (? IS NULL OR b.storage_kind = ?)
                  AND b.id IN (
                    SELECT source_blob_id FROM document_versions WHERE tenant_id = ?
                    UNION
                    SELECT blob_id FROM document_artifacts WHERE tenant_id = ?
                )
                LIMIT 1
                """,
                (tenant, digest, storage_kind, storage_kind, tenant, tenant),
            ).fetchone()
        return row is not None

    def _require_blob_quota_locked(self, tenant_id: str, blob_id: BlobId) -> None:
        blob_row = self._conn.execute(
            """
            SELECT sha256, storage_kind, byte_size
            FROM document_blobs
            WHERE tenant_id = ? AND id = ?
            """,
            (tenant_id, str(blob_id)),
        ).fetchone()
        if blob_row is None:
            raise ValueError("blob is unavailable in this tenant")
        already_referenced = self.tenant_references_sha256(
            tenant_id=tenant_id,
            sha256=blob_row["sha256"],
            storage_kind=blob_row["storage_kind"],
        )
        if already_referenced:
            return
        prospective = (
            self.tenant_referenced_blob_bytes(tenant_id=tenant_id)
            + int(blob_row["byte_size"])
        )
        if prospective > self._tenant_quota_bytes:
            raise ValueError(
                f"tenant blob quota exceeded: {prospective} > {self._tenant_quota_bytes}"
            )

    def _require_owner_locked(
        self, tenant_id: str, document_id: DocumentId, actor_id: str
    ) -> None:
        row = self._conn.execute(
            """
            SELECT 1 FROM documents
            WHERE tenant_id = ? AND id = ? AND owner_id = ? AND deleted_at IS NULL
            """,
            (tenant_id, str(document_id), actor_id),
        ).fetchone()
        if row is None:
            raise DocumentAccessError("document is unavailable or actor is not its owner")

    def _require_access_locked(
        self,
        tenant_id: str,
        document_id: DocumentId,
        actor_id: str,
        permission: str,
    ) -> None:
        actor = _required(actor_id, "actor_id")
        row = self._conn.execute(
            """
            SELECT d.owner_id, a.permission
            FROM documents d
            LEFT JOIN document_acl a
              ON a.tenant_id = d.tenant_id
             AND a.document_id = d.id
             AND a.principal_id = ?
            WHERE d.tenant_id = ? AND d.id = ? AND d.deleted_at IS NULL
            """,
            (actor, tenant_id, str(document_id)),
        ).fetchone()
        if row is None:
            raise DocumentAccessError("document is unavailable")
        if row["owner_id"] == actor:
            return
        granted = row["permission"]
        allowed = {
            "read": {"read", "write", "owner"},
            "write": {"write", "owner"},
            "owner": {"owner"},
        }[permission]
        if granted not in allowed:
            raise DocumentAccessError("actor does not have the required document permission")
