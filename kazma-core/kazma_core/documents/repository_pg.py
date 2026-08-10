"""Postgres multi-replica document **metadata** repository.

Mirrors the public surface of :class:`~kazma_core.documents.repository.DocumentRepository`
so ingestion/API can run with shared state across replicas when
``KAZMA_DOCUMENTS_METADATA_BACKEND=postgres`` (or ``auto`` with Postgres jobs).

Uses the shared ``get_pool()`` connection pool (same as job claims). Content
blobs remain on the content-addressed filesystem; only relational metadata
moves to Postgres.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

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
from .repository import (
    ArtifactRecord,
    BlobRecord,
    DocumentAccessError,
    DocumentChunkRecord,
    DocumentRecord,
    TombstoneRecord,
    VersionRecord,
    _ACL_PERMISSIONS,
    _BLOB_KINDS,
    _SHA256_RE,
    _decode_json,
    _json_object,
    _required,
    _sha256,
)

logger = logging.getLogger(__name__)

__all__ = [
    "PostgresDocumentRepository",
    "resolve_document_repository",
    "document_metadata_backend_name",
]

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    current_version_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    deleted_at TIMESTAMPTZ,
    PRIMARY KEY (id),
    UNIQUE (id, tenant_id)
);
CREATE TABLE IF NOT EXISTS document_blobs (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    storage_kind TEXT NOT NULL CHECK (storage_kind IN ('quarantine', 'originals', 'artifacts')),
    created_at TIMESTAMPTZ NOT NULL,
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
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, document_id, version_number),
    UNIQUE (tenant_id, document_id, source_sha256)
);
CREATE TABLE IF NOT EXISTS document_artifacts (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    blob_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (id, tenant_id),
    UNIQUE (tenant_id, version_id, artifact_type, blob_id)
);
CREATE TABLE IF NOT EXISTS document_acl (
    tenant_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    principal_id TEXT NOT NULL,
    permission TEXT NOT NULL CHECK (permission IN ('read', 'write', 'owner')),
    granted_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, document_id, principal_id)
);
CREATE TABLE IF NOT EXISTS document_tombstones (
    id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    deleted_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (id),
    UNIQUE (tenant_id, document_id)
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
    index_status TEXT NOT NULL CHECK (index_status IN ('indexed', 'failed', 'tombstoned')),
    active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, library_id, id),
    UNIQUE (tenant_id, library_id, version_id, chunk_index)
);
CREATE TABLE IF NOT EXISTS document_audit_events (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    actor_id TEXT,
    workspace_id TEXT,
    document_id TEXT,
    version_id TEXT,
    job_id TEXT,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_pg_documents_tenant_updated ON documents(tenant_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_pg_versions_tenant_document ON document_versions(tenant_id, document_id, version_number);
CREATE INDEX IF NOT EXISTS idx_pg_blobs_tenant_sha ON document_blobs(tenant_id, sha256);
CREATE INDEX IF NOT EXISTS idx_pg_audit_tenant_id ON document_audit_events(tenant_id, id DESC);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _as_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:  # noqa: BLE001
        return {k: row[k] for k in row.keys()}  # type: ignore[index]


def _document(row: Any) -> DocumentRecord:
    r = _as_dict(row)
    return DocumentRecord(
        id=DocumentId(r["id"]),
        tenant_id=r["tenant_id"],
        owner_id=r["owner_id"],
        title=r["title"],
        current_version_id=(
            VersionId(r["current_version_id"]) if r.get("current_version_id") else None
        ),
        metadata=_decode_json(r.get("metadata_json") or "{}"),
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
        deleted_at=str(r["deleted_at"]) if r.get("deleted_at") else None,
    )


def _blob(row: Any) -> BlobRecord:
    r = _as_dict(row)
    return BlobRecord(
        id=BlobId(r["id"]),
        tenant_id=r["tenant_id"],
        sha256=r["sha256"],
        byte_size=int(r["byte_size"]),
        storage_kind=r["storage_kind"],
        created_at=str(r["created_at"]),
    )


def _version(row: Any) -> VersionRecord:
    r = _as_dict(row)
    return VersionRecord(
        id=VersionId(r["id"]),
        tenant_id=r["tenant_id"],
        document_id=DocumentId(r["document_id"]),
        version_number=int(r["version_number"]),
        source_blob_id=BlobId(r["source_blob_id"]),
        source_sha256=r["source_sha256"],
        original_filename=r["original_filename"],
        mime_type=r["mime_type"],
        metadata=_decode_json(r.get("metadata_json") or "{}"),
        created_at=str(r["created_at"]),
    )


def _artifact(row: Any) -> ArtifactRecord:
    r = _as_dict(row)
    return ArtifactRecord(
        id=ArtifactId(r["id"]),
        tenant_id=r["tenant_id"],
        document_id=DocumentId(r["document_id"]),
        version_id=VersionId(r["version_id"]),
        blob_id=BlobId(r["blob_id"]),
        artifact_type=r["artifact_type"],
        metadata=_decode_json(r.get("metadata_json") or "{}"),
        created_at=str(r["created_at"]),
    )


def _chunk(row: Any) -> DocumentChunkRecord:
    r = _as_dict(row)
    return DocumentChunkRecord(
        id=r["id"],
        tenant_id=r["tenant_id"],
        library_id=r["library_id"],
        document_id=DocumentId(r["document_id"]),
        version_id=VersionId(r["version_id"]),
        source_url=r["source_url"],
        source_sha256=r["source_sha256"],
        chunk_index=int(r["chunk_index"]),
        chunk_hash=r["chunk_hash"],
        page_start=int(r["page_start"]),
        page_end=int(r["page_end"]),
        block_ids=tuple(json.loads(r["block_ids_json"])),
        block_hashes=tuple(json.loads(r["block_hashes_json"])),
        coordinates=tuple(json.loads(r["coordinates_json"])),
        provenance=dict(json.loads(r["provenance_json"])),
        metadata=dict(json.loads(r["metadata_json"])),
        index_status=r["index_status"],
        active=bool(r["active"]),
        created_at=str(r["created_at"]),
        updated_at=str(r["updated_at"]),
    )


class PostgresDocumentRepository:
    """Postgres-backed document metadata (multi-replica safe)."""

    backend_name = "postgres"
    multi_replica = True

    def __init__(self, pool: Any, *, tenant_quota_bytes: int) -> None:
        if isinstance(tenant_quota_bytes, bool) or int(tenant_quota_bytes) <= 0:
            raise ValueError("tenant_quota_bytes must be a positive integer")
        self._pool = pool
        self._tenant_quota_bytes = int(tenant_quota_bytes)
        self._lock = threading.RLock()
        # Compatibility for DocumentJobRepository SQLite fallback (unused when PG jobs).
        self._conn = None  # type: ignore[assignment]
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_PG_SCHEMA)
            conn.commit()

    def close(self) -> None:
        """No-op — pool is process-owned."""

    def _require_access(
        self, cur: Any, tenant: str, document_id: DocumentId, actor: str, permission: str
    ) -> None:
        cur.execute(
            """
            SELECT owner_id FROM documents
            WHERE tenant_id = %s AND id = %s AND deleted_at IS NULL
            """,
            (tenant, str(document_id)),
        )
        row = cur.fetchone()
        if row is None:
            raise DocumentAccessError("document is unavailable")
        if _as_dict(row).get("owner_id") == actor:
            return
        if permission == "owner":
            raise DocumentAccessError("actor is not the document owner")
        cur.execute(
            """
            SELECT permission FROM document_acl
            WHERE tenant_id = %s AND document_id = %s AND principal_id = %s
            """,
            (tenant, str(document_id), actor),
        )
        acl = cur.fetchone()
        perm = _as_dict(acl).get("permission") if acl is not None else None
        if permission == "read" and perm in {"read", "write", "owner"}:
            return
        if permission == "write" and perm in {"write", "owner"}:
            return
        raise DocumentAccessError("document is unavailable or access denied")

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
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO documents
                        (id, tenant_id, owner_id, title, metadata_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
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
                cur.execute(
                    "SELECT * FROM documents WHERE tenant_id = %s AND id = %s",
                    (tenant, str(identifier)),
                )
                row = cur.fetchone()
            conn.commit()
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
        suffix = "" if include_deleted else " AND deleted_at IS NULL"
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM documents WHERE tenant_id = %s AND id = %s{suffix}",
                    (tenant, str(identifier)),
                )
                row = cur.fetchone()
                if row is not None and actor_id is not None:
                    self._require_access(cur, tenant, identifier, actor_id, "read")
            conn.commit()
        return _document(row) if row is not None else None

    def list_documents(
        self,
        *,
        tenant_id: str,
        owner_id: str | None = None,
        include_deleted: bool = False,
    ) -> list[DocumentRecord]:
        tenant = _required(tenant_id, "tenant_id")
        clauses = ["tenant_id = %s"]
        params: list[Any] = [tenant]
        if owner_id is not None:
            clauses.append("owner_id = %s")
            params.append(_required(owner_id, "owner_id"))
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM documents WHERE {' AND '.join(clauses)} "
                    f"ORDER BY created_at, id",
                    params,
                )
                rows = cur.fetchall()
            conn.commit()
        return [_document(row) for row in rows]

    def assert_owner(
        self, *, tenant_id: str, document_id: DocumentId, actor_id: str
    ) -> None:
        tenant = _required(tenant_id, "tenant_id")
        identifier = DocumentId(document_id)
        actor = _required(actor_id, "actor_id")
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM documents
                    WHERE tenant_id = %s AND id = %s AND owner_id = %s
                      AND deleted_at IS NULL
                    """,
                    (tenant, str(identifier), actor),
                )
                row = cur.fetchone()
            conn.commit()
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
        try:
            with self._pool.connection() as conn:
                with conn.cursor() as cur:
                    self._require_access(
                        cur,
                        _required(tenant_id, "tenant_id"),
                        DocumentId(document_id),
                        _required(actor_id, "actor_id"),
                        permission,
                    )
                conn.commit()
            return True
        except DocumentAccessError:
            return False

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
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                self._require_access(cur, tenant, identifier, actor, "owner")
                cur.execute(
                    """
                    INSERT INTO document_acl
                        (tenant_id, document_id, principal_id, permission, granted_by, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, document_id, principal_id)
                    DO UPDATE SET permission = EXCLUDED.permission,
                                  granted_by = EXCLUDED.granted_by,
                                  created_at = EXCLUDED.created_at
                    """,
                    (tenant, str(identifier), principal, permission, actor, _now()),
                )
            conn.commit()

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
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM document_blobs
                    WHERE tenant_id = %s AND sha256 = %s AND storage_kind = %s
                    """,
                    (tenant, digest, storage_kind),
                )
                existing = cur.fetchone()
                if existing is not None:
                    if int(_as_dict(existing)["byte_size"]) != size:
                        raise ValueError("existing blob metadata has a conflicting byte size")
                    conn.commit()
                    return _blob(existing)
                cur.execute(
                    """
                    INSERT INTO document_blobs
                        (id, tenant_id, sha256, byte_size, storage_kind, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (str(identifier), tenant, digest, size, storage_kind, _now()),
                )
                cur.execute(
                    "SELECT * FROM document_blobs WHERE tenant_id = %s AND id = %s",
                    (tenant, str(identifier)),
                )
                row = cur.fetchone()
            conn.commit()
        return _blob(row)

    def get_blob(self, *, tenant_id: str, blob_id: BlobId) -> BlobRecord | None:
        tenant = _required(tenant_id, "tenant_id")
        identifier = BlobId(blob_id)
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM document_blobs WHERE tenant_id = %s AND id = %s",
                    (tenant, str(identifier)),
                )
                row = cur.fetchone()
            conn.commit()
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
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    self._require_access(cur, tenant, doc_id, actor, "write")
                    cur.execute(
                        "SELECT * FROM document_blobs WHERE tenant_id = %s AND id = %s",
                        (tenant, str(blob_id)),
                    )
                    blob_row = cur.fetchone()
                    if blob_row is None or _as_dict(blob_row)["sha256"] != digest:
                        raise ValueError(
                            "source blob is unavailable or its checksum does not match"
                        )
                    cur.execute(
                        """
                        SELECT * FROM document_versions
                        WHERE tenant_id = %s AND document_id = %s AND source_sha256 = %s
                        """,
                        (tenant, str(doc_id), digest),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        conn.commit()
                        return _version(existing)
                    cur.execute(
                        """
                        SELECT COALESCE(MAX(version_number), 0) + 1 AS next_version
                        FROM document_versions
                        WHERE tenant_id = %s AND document_id = %s
                        """,
                        (tenant, str(doc_id)),
                    )
                    number = int(_as_dict(cur.fetchone())["next_version"])
                    now = _now()
                    cur.execute(
                        """
                        INSERT INTO document_versions
                            (id, tenant_id, document_id, version_number, source_blob_id,
                             source_sha256, original_filename, mime_type, metadata_json, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    cur.execute(
                        """
                        UPDATE documents SET current_version_id = %s, updated_at = %s
                        WHERE tenant_id = %s AND id = %s
                        """,
                        (str(identifier), now, tenant, str(doc_id)),
                    )
                    cur.execute(
                        "SELECT * FROM document_versions WHERE tenant_id = %s AND id = %s",
                        (tenant, str(identifier)),
                    )
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
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
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM document_versions WHERE tenant_id = %s AND id = %s",
                    (tenant, str(identifier)),
                )
                row = cur.fetchone()
                if row is not None and actor_id is not None:
                    self._require_access(
                        cur,
                        tenant,
                        DocumentId(_as_dict(row)["document_id"]),
                        actor_id,
                        "read",
                    )
            conn.commit()
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
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                if actor_id is not None:
                    self._require_access(cur, tenant, identifier, actor_id, "read")
                cur.execute(
                    """
                    SELECT * FROM document_versions
                    WHERE tenant_id = %s AND document_id = %s
                    ORDER BY version_number
                    """,
                    (tenant, str(identifier)),
                )
                rows = cur.fetchall()
            conn.commit()
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
        tenant = _required(tenant_id, "tenant_id")
        library = _required(library_id, "library_id")
        document = DocumentId(document_id)
        version = VersionId(version_id)
        if not chunks:
            raise ValueError("at least one chunk is required")
        now = _now()
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT source_sha256 FROM document_versions
                           WHERE tenant_id = %s AND document_id = %s AND id = %s""",
                        (tenant, str(document), str(version)),
                    )
                    version_row = cur.fetchone()
                    if version_row is None:
                        raise DocumentAccessError(
                            "document version is unavailable in this tenant"
                        )
                    src_sha = _as_dict(version_row)["source_sha256"]
                    cur.execute(
                        """UPDATE document_chunks SET active = FALSE, updated_at = %s
                           WHERE tenant_id = %s AND library_id = %s AND document_id = %s""",
                        (now, tenant, library, str(document)),
                    )
                    cur.execute(
                        """DELETE FROM document_chunks
                           WHERE tenant_id = %s AND library_id = %s AND version_id = %s""",
                        (tenant, library, str(version)),
                    )
                    for item in chunks:
                        if (
                            str(item.document_id) != str(document)
                            or str(item.version_id) != str(version)
                            or item.source_sha256 != src_sha
                        ):
                            raise ValueError(
                                "chunk provenance does not match the stored version"
                            )
                        provenance = {
                            "parser": item.parser,
                            "parser_version": item.parser_version,
                        }
                        cur.execute(
                            """INSERT INTO document_chunks
                               (id, tenant_id, library_id, document_id, version_id,
                                source_url, source_sha256, chunk_index, chunk_hash,
                                page_start, page_end, block_ids_json, block_hashes_json,
                                coordinates_json, provenance_json, metadata_json,
                                index_status, active, created_at, updated_at)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                       'indexed', TRUE, %s, %s)""",
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
                conn.commit()
            except Exception:
                conn.rollback()
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
        clauses = ["tenant_id = %s", "library_id = %s", "document_id = %s"]
        params: list[Any] = [tenant, library, str(document)]
        if version_id is not None:
            clauses.append("version_id = %s")
            params.append(str(VersionId(version_id)))
        if not include_inactive:
            clauses.extend(["active = TRUE", "index_status = 'indexed'"])
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT * FROM document_chunks
                        WHERE {' AND '.join(clauses)}
                        ORDER BY version_id, chunk_index""",
                    params,
                )
                rows = cur.fetchall()
            conn.commit()
        return [_chunk(row) for row in rows]

    def tombstone_document_chunks(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        library_id: str | None = None,
    ) -> int:
        tenant = _required(tenant_id, "tenant_id")
        document = DocumentId(document_id)
        library_clause = ""
        params: list[Any] = [_now(), tenant, str(document)]
        if library_id is not None:
            library_clause = " AND library_id = %s"
            params.append(_required(library_id, "library_id"))
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""UPDATE document_chunks
                       SET active = FALSE, index_status = 'tombstoned', updated_at = %s
                       WHERE tenant_id = %s AND document_id = %s
                         AND index_status != 'tombstoned'{library_clause}""",
                    params,
                )
                count = cur.rowcount
            conn.commit()
        return int(count or 0)

    def list_indexed_libraries(
        self, *, tenant_id: str, document_id: DocumentId
    ) -> tuple[str, ...]:
        tenant = _required(tenant_id, "tenant_id")
        document = DocumentId(document_id)
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT DISTINCT library_id FROM document_chunks
                       WHERE tenant_id = %s AND document_id = %s
                         AND active = TRUE AND index_status = 'indexed'
                       ORDER BY library_id""",
                    (tenant, str(document)),
                )
                rows = cur.fetchall()
            conn.commit()
        return tuple(_as_dict(row)["library_id"] for row in rows)

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
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    self._require_access(cur, tenant, doc_id, actor, "write")
                    cur.execute(
                        """
                        SELECT 1 FROM document_versions
                        WHERE tenant_id = %s AND id = %s AND document_id = %s
                        """,
                        (tenant, str(ver_id), str(doc_id)),
                    )
                    version_row = cur.fetchone()
                    cur.execute(
                        "SELECT 1 FROM document_blobs WHERE tenant_id = %s AND id = %s",
                        (tenant, str(blob_identifier)),
                    )
                    blob_row = cur.fetchone()
                    if version_row is None or blob_row is None:
                        raise ValueError("version or blob is unavailable in this tenant")
                    cur.execute(
                        """
                        SELECT * FROM document_artifacts
                        WHERE tenant_id = %s AND version_id = %s
                          AND artifact_type = %s AND blob_id = %s
                        """,
                        (tenant, str(ver_id), kind, str(blob_identifier)),
                    )
                    existing = cur.fetchone()
                    if existing is not None:
                        conn.commit()
                        return _artifact(existing)
                    cur.execute(
                        """
                        INSERT INTO document_artifacts
                            (id, tenant_id, document_id, version_id, blob_id,
                             artifact_type, metadata_json, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
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
                    cur.execute(
                        "SELECT * FROM document_artifacts WHERE tenant_id = %s AND id = %s",
                        (tenant, str(identifier)),
                    )
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
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
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM document_artifacts WHERE tenant_id = %s AND id = %s",
                    (tenant, str(identifier)),
                )
                row = cur.fetchone()
                if row is not None and actor_id is not None:
                    self._require_access(
                        cur,
                        tenant,
                        DocumentId(_as_dict(row)["document_id"]),
                        actor_id,
                        "read",
                    )
            conn.commit()
        return _artifact(row) if row is not None else None

    def list_artifacts(
        self,
        *,
        tenant_id: str,
        document_id: DocumentId,
        version_id: VersionId | None = None,
    ) -> list[ArtifactRecord]:
        tenant = _required(tenant_id, "tenant_id")
        identifier = DocumentId(document_id)
        clauses = ["tenant_id = %s", "document_id = %s"]
        params: list[Any] = [tenant, str(identifier)]
        if version_id is not None:
            clauses.append("version_id = %s")
            params.append(str(VersionId(version_id)))
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT * FROM document_artifacts
                    WHERE {' AND '.join(clauses)}
                    ORDER BY created_at DESC, id DESC
                    """,
                    params,
                )
                rows = cur.fetchall()
            conn.commit()
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
        with self._pool.connection() as conn:
            try:
                with conn.cursor() as cur:
                    # Write access (owner or write ACL) — same as SQLite path.
                    self._require_access(cur, tenant, identifier, actor, "write")
                    cur.execute(
                        """
                        UPDATE documents SET deleted_at = %s, updated_at = %s
                        WHERE tenant_id = %s AND id = %s AND deleted_at IS NULL
                        """,
                        (now, now, tenant, str(identifier)),
                    )
                    cur.execute(
                        """
                        INSERT INTO document_tombstones
                            (id, tenant_id, document_id, deleted_by, reason, deleted_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, document_id) DO NOTHING
                        """,
                        (
                            tombstone_id,
                            tenant,
                            str(identifier),
                            actor,
                            normalized_reason,
                            now,
                        ),
                    )
                    cur.execute(
                        """UPDATE document_chunks
                           SET active = FALSE, index_status = 'tombstoned', updated_at = %s
                           WHERE tenant_id = %s AND document_id = %s""",
                        (now, tenant, str(identifier)),
                    )
                    cur.execute(
                        """
                        SELECT * FROM document_tombstones
                        WHERE tenant_id = %s AND document_id = %s
                        """,
                        (tenant, str(identifier)),
                    )
                    row = cur.fetchone()
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        r = _as_dict(row)
        return TombstoneRecord(
            id=r["id"],
            tenant_id=r["tenant_id"],
            document_id=DocumentId(r["document_id"]),
            deleted_by=r["deleted_by"],
            reason=r["reason"],
            deleted_at=str(r["deleted_at"]),
        )

    def tenant_referenced_blob_bytes(self, *, tenant_id: str) -> int:
        tenant = _required(tenant_id, "tenant_id")
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COALESCE(SUM(byte_size), 0) AS total
                    FROM (
                        SELECT b.sha256, b.storage_kind, MAX(b.byte_size) AS byte_size
                        FROM document_blobs b
                        JOIN (
                            SELECT source_blob_id AS blob_id
                            FROM document_versions WHERE tenant_id = %s
                            UNION
                            SELECT blob_id
                            FROM document_artifacts WHERE tenant_id = %s
                        ) refs ON refs.blob_id = b.id
                        WHERE b.tenant_id = %s
                        GROUP BY b.sha256, b.storage_kind
                    ) t
                    """,
                    (tenant, tenant, tenant),
                )
                row = cur.fetchone()
            conn.commit()
        return int(_as_dict(row).get("total") or 0)

    def tenant_references_sha256(
        self,
        *,
        tenant_id: str,
        sha256: str,
        storage_kind: str | None = None,
    ) -> bool:
        tenant = _required(tenant_id, "tenant_id")
        digest = _sha256(sha256)
        if storage_kind is not None and storage_kind not in _BLOB_KINDS:
            raise ValueError(f"invalid storage kind: {storage_kind!r}")
        # Branch instead of ``(%s IS NULL OR col = %s)`` — Postgres/psycopg3
        # cannot infer the type of an untyped NULL bind (IndeterminateDatatype).
        kind_clause = ""
        params: list[Any] = [tenant, digest]
        if storage_kind is not None:
            kind_clause = " AND b.storage_kind = %s"
            params.append(storage_kind)
        params.extend([tenant, tenant])
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT 1
                    FROM document_blobs b
                    WHERE b.tenant_id = %s AND b.sha256 = %s
                      {kind_clause}
                      AND b.id IN (
                        SELECT source_blob_id FROM document_versions WHERE tenant_id = %s
                        UNION
                        SELECT blob_id FROM document_artifacts WHERE tenant_id = %s
                      )
                    LIMIT 1
                    """,
                    params,
                )
                row = cur.fetchone()
            conn.commit()
        return row is not None


def document_metadata_backend_name() -> str:
    from .jobs_pg import document_metadata_backend

    return document_metadata_backend()


def resolve_document_repository(
    sqlite_repo: Any,
    *,
    tenant_quota_bytes: int | None = None,
) -> Any:
    """Return Postgres metadata repo when configured, else *sqlite_repo*."""

    if document_metadata_backend_name() != "postgres":
        return sqlite_repo
    try:
        from kazma_core.db.pg_helpers import get_pool

        quota = tenant_quota_bytes
        if quota is None:
            from .config import get_document_config

            quota = get_document_config().quota_tenant_bytes
        pool = get_pool()
        repo = PostgresDocumentRepository(pool, tenant_quota_bytes=int(quota))
        logger.info("[documents.metadata] using Postgres multi-replica metadata repository")
        return repo
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[documents.metadata] Postgres metadata repo unavailable (%s); "
            "falling back to SQLite",
            exc,
        )
        return sqlite_repo
