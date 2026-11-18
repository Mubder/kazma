"""Verified content-addressed storage for document source and derived bytes.

**Kept per space** (Spaces S1-12). Every byte of a document -- its quarantined
and promoted original, its artifacts, its parsed manifest -- lives in its
space's own folder, so one space can be exported or erased alone. A store is
asked as ``storage.for_space(tenant_id)``; there is no call that reads or
writes content without naming a space. The folder is :func:`space_folder`:

* the owner's space (``default``) is the storage root itself, as before
  Spaces: an install's documents do not move (plan section 7, "a mapping,
  not a move");
* any other space is ``spaces/<space id>/`` under the root (a tenant that is
  not a space id, from an older install, gets ``spaces/_t<hash>/``).

An install of the older multi-user mode can hold another tenant's files in
the root, where every tenant's lived before. They are copied into their
space's folder once, journaled and safe to interrupt
(``documents.space_files``); until a tenant's copy is done its reads fall
back to the root, and garbage collection keeps them there.
"""

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
    "BlobLengthError",
    "ContentAddressedStorage",
    "SPACES_DIR",
    "SPACE_FILES_JOURNAL",
    "SpaceStorage",
    "StoredBlob",
    "blob_rel",
    "bounded_stream_digest",
    "manifest_rel",
    "space_folder",
    "StorageQuotaExceeded",
    "StorageQuotaExceededError",
]

#: The folder under the storage root that holds every space but the owner's.
SPACES_DIR = "spaces"

#: The record of which tenants' files are in their space's folder
#: (``documents.space_files``), at the storage root.
SPACE_FILES_JOURNAL = ".space-files.json"


def space_folder(tenant_id: str) -> str:
    """The folder of a space's content, relative to the storage root, with
    ``/`` separators: ``""`` for the owner's space, ``spaces/<id>`` for any
    other. The one answer every reader and writer of document files asks
    (storage, garbage collection, backups, the migration export)."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID, valid_space_id

    tenant = str(tenant_id or "").strip()
    if not tenant:
        raise ValueError("a document's files need its space")
    if tenant == OWNER_SPACE_ID:
        return ""
    if valid_space_id(tenant):
        return f"{SPACES_DIR}/{tenant}"
    # A tenant an older install named freely: never a path of its own.
    return f"{SPACES_DIR}/_t{hashlib.sha256(tenant.encode('utf-8')).hexdigest()[:32]}"


def _join(folder: str, rel: str) -> str:
    return f"{folder}/{rel}" if folder else rel


def blob_rel(tenant_id: str, kind: str, sha256: str) -> str:
    """A blob's path relative to the storage root (``/`` separators)."""
    digest = _validate_sha256(sha256)
    return _join(space_folder(tenant_id), f"{_validate_kind(kind)}/sha256/{digest[:2]}/{digest[2:4]}/{digest}")


def _path_part(value: Any) -> str:
    """An id read from the catalog, as one path segment. Backups and the
    migration export take ids as the catalog holds them (they never
    validated their shape); a separator or a parent step is refused."""
    text = str(value)
    if not text or "/" in text or "\\" in text or text in {".", ".."}:
        raise ValueError(f"not an id: {text!r}")
    return text


def manifest_rel(tenant_id: str, document_id: Any, version_id: Any) -> str:
    """A version manifest's path relative to the storage root."""
    return _join(space_folder(tenant_id),
                 f"manifests/{_path_part(document_id)}/{_path_part(version_id)}.json")


def legacy_blob_rel(kind: str, sha256: str) -> str:
    """Where every tenant's blob lived before Spaces: the root's own tree."""
    digest = _validate_sha256(sha256)
    return f"{_validate_kind(kind)}/sha256/{digest[:2]}/{digest[2:4]}/{digest}"


def legacy_manifest_rel(document_id: Any, version_id: Any) -> str:
    return f"manifests/{_path_part(document_id)}/{_path_part(version_id)}.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KINDS = frozenset({"quarantine", "originals", "artifacts"})
_COPY_CHUNK_SIZE = 1024 * 1024


class BlobTooLargeError(ValueError):
    """Raised before an over-limit stream can be promoted."""


class BlobChecksumError(IOError):
    """Raised when supplied or persisted bytes do not match their checksum."""


class BlobLengthError(ValueError):
    """A declared stream length must match before immutable publication."""


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


def bounded_stream_digest(
    source: BinaryIO | Iterable[bytes], *, max_bytes: int
) -> tuple[str, int]:
    """Hash a replay without publishing bytes or trusting a declared length."""
    digest = hashlib.sha256()
    size = 0
    for chunk in _chunks(source):
        size += len(chunk)
        if size > max_bytes:
            raise BlobTooLargeError("Document exceeds the configured intake size limit")
        digest.update(chunk)
    return digest.hexdigest(), size


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


class SpaceStorage:
    """One space's immutable sha256-addressed blobs and atomic per-version
    manifests, in its own folder (:func:`space_folder`). Made by
    :meth:`ContentAddressedStorage.for_space` only."""

    def __init__(self, install: ContentAddressedStorage, tenant_id: str) -> None:
        self.tenant_id = str(tenant_id).strip()
        folder = space_folder(self.tenant_id)
        self.install = install
        self.root = install.root / folder if folder else install.root
        self._lock = install._lock

    def blob_path(self, *, kind: str, sha256: str) -> Path:
        """The space's own path of a blob (where it is written)."""
        normalized_kind = _validate_kind(kind)
        digest = _validate_sha256(sha256)
        return self.root / normalized_kind / "sha256" / digest[:2] / digest[2:4] / digest

    def locate_blob(self, *, kind: str, sha256: str) -> Path:
        """Where to read a blob: the space's own path, or, for a tenant whose
        files an older install kept in the root and that are not copied yet,
        the root's path (``documents.space_files``)."""
        own = self.blob_path(kind=kind, sha256=sha256)
        if own.is_file() or not self.install.reads_root_for(self.tenant_id):
            return own
        legacy = self.install.root / legacy_blob_rel(kind, sha256)
        return legacy if legacy.is_file() else own

    def manifest_path(self, *, document_id: DocumentId, version_id: VersionId) -> Path:
        return self.root / "manifests" / str(DocumentId(document_id)) / f"{VersionId(version_id)}.json"

    def locate_manifest(self, *, document_id: DocumentId, version_id: VersionId) -> Path:
        """Where to read a manifest (as :meth:`locate_blob`)."""
        own = self.manifest_path(document_id=document_id, version_id=version_id)
        if own.is_file() or not self.install.reads_root_for(self.tenant_id):
            return own
        legacy = self.install.root / legacy_manifest_rel(document_id, version_id)
        return legacy if legacy.is_file() else own

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
        expected_byte_size: int | None = None,
    ) -> StoredBlob:
        """Stream, limit, hash, verify, and atomically promote immutable content."""
        normalized_kind = _validate_kind(kind)
        if isinstance(max_bytes, bool) or int(max_bytes) <= 0:
            raise ValueError("max_bytes must be a positive integer")
        byte_limit = int(max_bytes)
        if expected_byte_size is not None and (
            isinstance(expected_byte_size, bool) or not isinstance(expected_byte_size, int)
            or not 0 <= expected_byte_size <= byte_limit
        ):
            raise ValueError("expected_byte_size must be within the intake limit")
        expected = _validate_sha256(expected_sha256) if expected_sha256 is not None else None
        if tenant_id is not None and str(tenant_id).strip() != self.tenant_id:
            # A space's store writes its own space's files only.
            raise ValueError("tenant_id names another space than this store's")
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
                    if expected_byte_size is not None and total > expected_byte_size:
                        raise BlobLengthError("Document length does not match the declared length")
                    handle.write(chunk)
                    digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())

            if expected_byte_size is not None and total != expected_byte_size:
                raise BlobLengthError("Document length does not match the declared length")

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
                        # The blob's absolute path is the operator's to find
                        # (logged); the upload's answer names its digest only.
                        logger.error("[documents] immutable blob is corrupt: %s", target)
                        raise BlobChecksumError(
                            f"existing immutable blob is corrupt (sha256 {computed[:16]})"
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
        target = self.locate_blob(kind=kind, sha256=sha256)
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
        target = self.locate_manifest(document_id=document_id, version_id=version_id)
        if not target.is_file():
            return None
        value = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("stored manifest is not a JSON object")
        return value


class ContentAddressedStorage:
    """The install's document content store: a root folder holding each
    space's own (:class:`SpaceStorage`, :func:`space_folder`).

    Nothing here reads or writes a space's content: ask :meth:`for_space`.
    It knows which tenants an older install kept in the root and has not
    copied into their space's folder yet (``documents.space_files``).
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._moved: frozenset[str] | None = None

    def for_space(self, tenant_id: str) -> SpaceStorage:
        """The store of one space (a document's tenant)."""
        return SpaceStorage(self, tenant_id)

    def journal_path(self) -> Path:
        return self.root / SPACE_FILES_JOURNAL

    def moved_tenants(self) -> frozenset[str]:
        """Tenants whose files are in their space's folder: read from the
        journal once, then kept current by :meth:`note_moved`."""
        moved = self._moved
        if moved is None:
            moved = frozenset(_read_journal(self.journal_path()).get("moved") or ())
            self._moved = moved
        return moved

    def note_moved(self, tenant_id: str) -> None:
        """This process saw *tenant_id*'s files copied into its folder."""
        self._moved = self.moved_tenants() | {str(tenant_id)}

    def reads_root_for(self, tenant_id: str) -> bool:
        """True while a tenant of another space may still have files where an
        older install kept every tenant's: the root's own tree."""
        if not space_folder(tenant_id):
            return False  # the owner's space IS the root
        return str(tenant_id) not in self.moved_tenants()


def _read_journal(path: Path) -> dict[str, Any]:
    """The space-files journal; an absent or unreadable one is empty (every
    tenant of another space then reads the root as well: the safe side)."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("[documents.storage] the space-files journal could not be read", exc_info=True)
        return {}
    return value if isinstance(value, dict) else {}
