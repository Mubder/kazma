"""Verified content-addressed storage for document source and derived bytes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Protocol

from .models import DocumentId, VersionId

logger = logging.getLogger(__name__)

__all__ = [
    "BlobChecksumError",
    "BlobTooLargeError",
    "ContentAddressedStorage",
    "StoredBlob",
    "StorageQuotaExceeded",
    "StorageQuotaExceededError",
]

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KINDS = frozenset({"quarantine", "originals", "artifacts"})
_COPY_CHUNK_SIZE = 1024 * 1024


class BlobTooLargeError(ValueError):
    """Raised before an over-limit stream can be promoted."""


class BlobChecksumError(IOError):
    """Raised when supplied or persisted bytes do not match their checksum."""


class StorageQuotaExceededError(ValueError):
    """Raised when a new tenant reference would exceed its configured quota."""


StorageQuotaExceeded = StorageQuotaExceededError


class _QuotaRepository(Protocol):
    def tenant_referenced_blob_bytes(self, *, tenant_id: str) -> int: ...

    def tenant_references_sha256(
        self, *, tenant_id: str, sha256: str, storage_kind: str | None = None
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class StoredBlob:
    """Result of a verified content-addressed write."""

    sha256: str
    byte_size: int
    kind: str
    path: Path
    reused: bool


def _validate_kind(kind: str) -> str:
    if kind not in _KINDS:
        raise ValueError(f"invalid blob kind: {kind!r}")
    return kind


def _validate_sha256(value: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError("sha256 must contain exactly 64 lowercase hexadecimal characters")
    return value


def _chunks(source: BinaryIO | Iterable[bytes]) -> Iterator[bytes]:
    read = getattr(source, "read", None)
    if callable(read):
        while True:
            chunk = read(_COPY_CHUNK_SIZE)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise TypeError("binary stream read() must return bytes")
            yield chunk
        return
    for chunk in source:
        if not isinstance(chunk, bytes):
            raise TypeError("input chunks must be bytes")
        if chunk:
            yield chunk


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _sync_directory(path: Path) -> None:
    """Persist a rename's directory entry on platforms that support it."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ContentAddressedStorage:
    """Immutable sha256-addressed blobs plus atomic per-version manifests."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def blob_path(self, *, kind: str, sha256: str) -> Path:
        """Return the canonical path after strict kind/hash validation."""
        normalized_kind = _validate_kind(kind)
        digest = _validate_sha256(sha256)
        return self.root / normalized_kind / "sha256" / digest[:2] / digest[2:4] / digest

    def put_stream(
        self,
        source: BinaryIO | Iterable[bytes],
        *,
        kind: str,
        max_bytes: int,
        expected_sha256: str | None = None,
        tenant_id: str | None = None,
        repository: _QuotaRepository | None = None,
        tenant_quota_bytes: int | None = None,
    ) -> StoredBlob:
        """Stream, limit, hash, verify, and atomically promote immutable content."""
        normalized_kind = _validate_kind(kind)
        if isinstance(max_bytes, bool) or int(max_bytes) <= 0:
            raise ValueError("max_bytes must be a positive integer")
        byte_limit = int(max_bytes)
        expected = _validate_sha256(expected_sha256) if expected_sha256 is not None else None
        quota_enabled = repository is not None or tenant_id is not None or tenant_quota_bytes is not None
        if quota_enabled and (
            repository is None
            or tenant_id is None
            or not str(tenant_id).strip()
            or tenant_quota_bytes is None
            or isinstance(tenant_quota_bytes, bool)
            or int(tenant_quota_bytes) <= 0
        ):
            raise ValueError(
                "repository, non-empty tenant_id, and positive tenant_quota_bytes "
                "must be supplied together"
            )

        staging_dir = self.root / normalized_kind / "sha256"
        staging_dir.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(prefix=".incoming-", dir=staging_dir)
            temp_path = Path(temp_name)
            digest = hashlib.sha256()
            total = 0
            with os.fdopen(descriptor, "wb") as handle:
                for chunk in _chunks(source):
                    total += len(chunk)
                    if total > byte_limit:
                        raise BlobTooLargeError(
                            f"document stream exceeds the {byte_limit}-byte intake limit"
                        )
                    handle.write(chunk)
                    digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())

            computed = digest.hexdigest()
            if expected is not None and computed != expected:
                raise BlobChecksumError(
                    f"checksum mismatch: expected {expected}, computed {computed}"
                )
            verified_digest, verified_size = _file_digest(temp_path)
            if verified_digest != computed or verified_size != total:
                raise BlobChecksumError("staged blob failed post-write checksum verification")

            if repository is not None and tenant_id is not None and tenant_quota_bytes is not None:
                referenced = repository.tenant_referenced_blob_bytes(tenant_id=tenant_id)
                already_referenced = repository.tenant_references_sha256(
                    tenant_id=tenant_id,
                    sha256=computed,
                    storage_kind=normalized_kind,
                )
                prospective = referenced if already_referenced else referenced + total
                if prospective > int(tenant_quota_bytes):
                    raise StorageQuotaExceeded(
                        f"tenant blob quota exceeded: {prospective} > {tenant_quota_bytes}"
                    )

            target = self.blob_path(kind=normalized_kind, sha256=computed)
            target.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                if target.exists():
                    existing_digest, existing_size = _file_digest(target)
                    if existing_digest != computed or existing_size != total:
                        raise BlobChecksumError(
                            f"existing immutable blob is corrupt: {target}"
                        )
                    # GC uses mtime as an additional grace-period backstop.
                    # Refresh it when immutable content is deduplicated so a
                    # newly published reference cannot look stale.
                    os.utime(target, None)
                    temp_path.unlink()
                    temp_path = None
                    return StoredBlob(computed, total, normalized_kind, target, True)

                os.replace(temp_path, target)
                temp_path = None
                _sync_directory(target.parent)
                promoted_digest, promoted_size = _file_digest(target)
                if promoted_digest != computed or promoted_size != total:
                    try:
                        target.unlink()
                    except OSError:
                        logger.exception(
                            "[documents.storage] Failed to remove corrupt promoted blob %s",
                            target,
                        )
                    raise BlobChecksumError("promoted blob failed checksum verification")
            return StoredBlob(computed, total, normalized_kind, target, False)
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "[documents.storage] Failed to clean staging file %s",
                        temp_path,
                        exc_info=True,
                    )

    def verify_blob(self, *, kind: str, sha256: str) -> bool:
        """Verify that an addressed blob exists and matches its path checksum."""
        target = self.blob_path(kind=kind, sha256=sha256)
        if not target.is_file():
            return False
        actual, _ = _file_digest(target)
        return actual == sha256

    def write_manifest(
        self,
        *,
        document_id: DocumentId,
        version_id: VersionId,
        manifest: Mapping[str, Any],
    ) -> Path:
        """Atomically write deterministic JSON for one document version."""
        doc_id = DocumentId(document_id)
        ver_id = VersionId(version_id)
        try:
            payload = json.dumps(
                dict(manifest),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("manifest must be a JSON-serializable object") from exc

        manifest_dir = self.root / "manifests" / str(doc_id)
        manifest_dir.mkdir(parents=True, exist_ok=True)
        target = manifest_dir / f"{ver_id}.json"
        temp_path: Path | None = None
        try:
            descriptor, temp_name = tempfile.mkstemp(prefix=".manifest-", dir=manifest_dir)
            temp_path = Path(temp_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            with self._lock:
                os.replace(temp_path, target)
                temp_path = None
                _sync_directory(target.parent)
            return target
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def read_manifest(
        self,
        *,
        document_id: DocumentId,
        version_id: VersionId,
    ) -> dict[str, Any] | None:
        """Read a validated manifest path, returning ``None`` when absent."""
        doc_id = DocumentId(document_id)
        ver_id = VersionId(version_id)
        target = self.root / "manifests" / str(doc_id) / f"{ver_id}.json"
        if not target.is_file():
            return None
        value = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("stored manifest is not a JSON object")
        return value
