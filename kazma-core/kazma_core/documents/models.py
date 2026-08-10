"""Canonical contracts for the document intelligence platform."""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar, cast

__all__ = [
    "ArtifactId",
    "BlobId",
    "BlockType",
    "BoundingBox",
    "DocumentBlock",
    "DocumentIR",
    "DocumentId",
    "DocumentJobState",
    "DocumentPage",
    "DocumentProvenance",
    "DocumentResult",
    "JobId",
    "JobState",
    "Provenance",
    "SCHEMA_VERSION",
    "VersionId",
    "new_artifact_id",
    "new_blob_id",
    "new_document_id",
    "new_job_id",
    "new_version_id",
]

SCHEMA_VERSION = "1.0"
JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
T = TypeVar("T")


class _OpaqueUuid(str):
    """Canonical UUID string with a domain-specific runtime type."""

    def __new__(cls, value: str | uuid.UUID) -> _OpaqueUuid:
        text = str(value)
        try:
            parsed = uuid.UUID(text)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {cls.__name__}: {value!r}") from exc
        canonical = str(parsed)
        if text != canonical:
            raise ValueError(f"{cls.__name__} must be a canonical lowercase UUID")
        return str.__new__(cls, canonical)

    @classmethod
    def new(cls) -> _OpaqueUuid:
        """Create a new UUID value while preserving the concrete ID type."""
        return cls(uuid.uuid4())


class DocumentId(_OpaqueUuid):
    """Opaque logical-document identifier."""


class VersionId(_OpaqueUuid):
    """Opaque immutable-version identifier."""


class BlobId(_OpaqueUuid):
    """Opaque tenant blob-metadata identifier."""


class ArtifactId(_OpaqueUuid):
    """Opaque derived-artifact identifier."""


class JobId(_OpaqueUuid):
    """Opaque document-job identifier reserved for later phases."""


def new_document_id() -> DocumentId:
    return DocumentId(uuid.uuid4())


def new_version_id() -> VersionId:
    return VersionId(uuid.uuid4())


def new_blob_id() -> BlobId:
    return BlobId(uuid.uuid4())


def new_artifact_id() -> ArtifactId:
    return ArtifactId(uuid.uuid4())


def new_job_id() -> JobId:
    return JobId(uuid.uuid4())


class DocumentJobState(StrEnum):
    """Canonical durable document-processing lifecycle."""

    RECEIVED = "received"
    QUARANTINED = "quarantined"
    VALIDATING = "validating"
    REJECTED = "rejected"
    READY_TO_PARSE = "ready_to_parse"
    PARSING = "parsing"
    OCR_REQUIRED = "ocr_required"
    OCR_RUNNING = "ocr_running"
    NORMALIZING = "normalizing"
    INDEXING = "indexing"
    VERIFYING = "verifying"
    READY = "ready"
    RETRY_WAIT = "retry_wait"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"


JobState = DocumentJobState


class BlockType(StrEnum):
    """Semantic block types emitted by document parsers."""

    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TEXT = "text"
    LIST_ITEM = "list_item"
    TABLE = "table"
    IMAGE = "image"
    CAPTION = "caption"
    FORMULA = "formula"
    CODE = "code"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"
    UNKNOWN = "unknown"


def _validate_json(value: Any, path: str = "value") -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, (list, tuple)):
        return [_validate_json(item, f"{path}[]") for item in value]
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings")
            result[key] = _validate_json(item, f"{path}.{key}")
        return result
    raise TypeError(f"{path} is not JSON serializable: {type(value).__name__}")


def _metadata(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], _validate_json(value, "metadata"))


def _json_dumps(value: Mapping[str, Any]) -> str:
    return json.dumps(
        _validate_json(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Axis-aligned page coordinates in parser-native units."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        values = tuple(float(value) for value in (self.x0, self.y0, self.x1, self.y1))
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Bounding-box coordinates must be finite")
        if values[0] > values[2] or values[1] > values[3]:
            raise ValueError("Bounding-box minimums must not exceed maximums")
        for name, value in zip(("x0", "y0", "x1", "y1"), values, strict=True):
            object.__setattr__(self, name, value)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"x0": self.x0, "x1": self.x1, "y0": self.y0, "y1": self.y1}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BoundingBox:
        return cls(*(float(value[key]) for key in ("x0", "y0", "x1", "y1")))


@dataclass(frozen=True, slots=True)
class Provenance:
    """Traceability information for parsed or derived document content."""

    source: str
    parser: str
    parser_version: str | None = None
    source_blob_id: BlobId | None = None
    artifact_ids: tuple[ArtifactId, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("Provenance source must not be empty")
        if not self.parser.strip():
            raise ValueError("Provenance parser must not be empty")
        if self.source_blob_id is not None:
            object.__setattr__(self, "source_blob_id", BlobId(self.source_blob_id))
        object.__setattr__(
            self,
            "artifact_ids",
            tuple(ArtifactId(identifier) for identifier in self.artifact_ids),
        )
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "artifact_ids": [str(item) for item in self.artifact_ids],
            "metadata": dict(self.metadata),
            "parser": self.parser,
            "parser_version": self.parser_version,
            "source": self.source,
            "source_blob_id": str(self.source_blob_id) if self.source_blob_id else None,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Provenance:
        source_blob_id = value.get("source_blob_id")
        artifact_ids = value.get("artifact_ids", [])
        if not isinstance(artifact_ids, list):
            raise TypeError("artifact_ids must be a list")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise TypeError("provenance metadata must be an object")
        return cls(
            source=str(value["source"]),
            parser=str(value["parser"]),
            parser_version=(
                str(value["parser_version"]) if value.get("parser_version") is not None else None
            ),
            source_blob_id=BlobId(str(source_blob_id)) if source_blob_id else None,
            artifact_ids=tuple(ArtifactId(str(item)) for item in artifact_ids),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class DocumentBlock:
    """A semantic unit on a document page."""

    block_id: str
    block_type: BlockType
    text: str = ""
    bounding_box: BoundingBox | None = None
    confidence: float | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.block_id.strip():
            raise ValueError("block_id must not be empty")
        object.__setattr__(self, "block_type", BlockType(self.block_type))
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "block_id": self.block_id,
            "block_type": self.block_type.value,
            "bounding_box": self.bounding_box.to_dict() if self.bounding_box else None,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DocumentBlock:
        box = value.get("bounding_box")
        metadata = value.get("metadata", {})
        if box is not None and not isinstance(box, Mapping):
            raise TypeError("bounding_box must be an object")
        if not isinstance(metadata, Mapping):
            raise TypeError("block metadata must be an object")
        return cls(
            block_id=str(value["block_id"]),
            block_type=BlockType(str(value["block_type"])),
            text=str(value.get("text", "")),
            bounding_box=BoundingBox.from_dict(box) if box is not None else None,
            confidence=float(value["confidence"]) if value.get("confidence") is not None else None,
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class DocumentPage:
    """Ordered blocks and geometry for one 1-based page."""

    page_number: int
    blocks: tuple[DocumentBlock, ...] = ()
    width: float | None = None
    height: float | None = None
    rotation: int = 0
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.page_number < 1:
            raise ValueError("page_number must be at least 1")
        for name in ("width", "height"):
            raw = getattr(self, name)
            if raw is not None:
                value = float(raw)
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(f"page {name} must be finite and positive")
                object.__setattr__(self, name, value)
        if self.rotation not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180, or 270")
        object.__setattr__(self, "blocks", tuple(self.blocks))
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "blocks": [block.to_dict() for block in self.blocks],
            "height": self.height,
            "metadata": dict(self.metadata),
            "page_number": self.page_number,
            "rotation": self.rotation,
            "width": self.width,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DocumentPage:
        blocks = value.get("blocks", [])
        metadata = value.get("metadata", {})
        if not isinstance(blocks, list):
            raise TypeError("page blocks must be a list")
        if not isinstance(metadata, Mapping):
            raise TypeError("page metadata must be an object")
        return cls(
            page_number=int(value["page_number"]),
            blocks=tuple(DocumentBlock.from_dict(item) for item in blocks),
            width=float(value["width"]) if value.get("width") is not None else None,
            height=float(value["height"]) if value.get("height") is not None else None,
            rotation=int(value.get("rotation", 0)),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class DocumentIR:
    """Versioned, transport-neutral intermediate representation."""

    document_id: DocumentId
    version_id: VersionId
    pages: tuple[DocumentPage, ...]
    provenance: Provenance
    schema_version: str = SCHEMA_VERSION
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported DocumentIR schema_version: {self.schema_version!r}")
        object.__setattr__(self, "document_id", DocumentId(self.document_id))
        object.__setattr__(self, "version_id", VersionId(self.version_id))
        object.__setattr__(self, "pages", tuple(self.pages))
        page_numbers = [page.page_number for page in self.pages]
        if page_numbers != sorted(page_numbers) or len(page_numbers) != len(set(page_numbers)):
            raise ValueError("Document pages must have unique ascending page numbers")
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "document_id": str(self.document_id),
            "metadata": dict(self.metadata),
            "pages": [page.to_dict() for page in self.pages],
            "provenance": self.provenance.to_dict(),
            "schema_version": self.schema_version,
            "version_id": str(self.version_id),
        }

    def to_json(self) -> str:
        """Return canonical JSON with stable key ordering and separators."""
        return _json_dumps(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DocumentIR:
        pages = value.get("pages", [])
        provenance = value.get("provenance")
        metadata = value.get("metadata", {})
        if not isinstance(pages, list):
            raise TypeError("pages must be a list")
        if not isinstance(provenance, Mapping):
            raise TypeError("provenance must be an object")
        if not isinstance(metadata, Mapping):
            raise TypeError("document metadata must be an object")
        return cls(
            document_id=DocumentId(str(value["document_id"])),
            version_id=VersionId(str(value["version_id"])),
            pages=tuple(DocumentPage.from_dict(page) for page in pages),
            provenance=Provenance.from_dict(provenance),
            schema_version=str(value.get("schema_version", SCHEMA_VERSION)),
            metadata=metadata,
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> DocumentIR:
        value = json.loads(payload)
        if not isinstance(value, Mapping):
            raise TypeError("DocumentIR JSON must contain an object")
        return cls.from_dict(value)


@dataclass(frozen=True, slots=True)
class DocumentResult(Generic[T]):
    """Typed service boundary result with machine- and human-readable context."""

    ok: bool
    code: str
    message: str
    data: T | None = None
    document_id: DocumentId | None = None
    version_id: VersionId | None = None
    blob_id: BlobId | None = None
    artifact_id: ArtifactId | None = None
    job_id: JobId | None = None
    warnings: tuple[str, ...] = ()
    retryable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool) or not isinstance(self.retryable, bool):
            raise TypeError("ok and retryable must be booleans")
        if not self.code.strip():
            raise ValueError("result code must not be empty")
        for name, identifier_type in (
            ("document_id", DocumentId),
            ("version_id", VersionId),
            ("blob_id", BlobId),
            ("artifact_id", ArtifactId),
            ("job_id", JobId),
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, identifier_type(value))
        object.__setattr__(self, "warnings", tuple(self.warnings))

    def to_dict(self) -> dict[str, JsonValue]:
        data = self.data
        serializer = getattr(data, "to_dict", None)
        if callable(serializer):
            data = serializer()
        return cast(
            dict[str, JsonValue],
            _validate_json(
                {
                    "artifact_id": str(self.artifact_id) if self.artifact_id else None,
                    "blob_id": str(self.blob_id) if self.blob_id else None,
                    "code": self.code,
                    "data": data,
                    "document_id": str(self.document_id) if self.document_id else None,
                    "job_id": str(self.job_id) if self.job_id else None,
                    "message": self.message,
                    "ok": self.ok,
                    "retryable": self.retryable,
                    "version_id": str(self.version_id) if self.version_id else None,
                    "warnings": list(self.warnings),
                }
            ),
        )

    def to_json(self) -> str:
        return _json_dumps(self.to_dict())

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> DocumentResult[JsonValue]:
        warnings = value.get("warnings", [])
        if not isinstance(warnings, list):
            raise TypeError("warnings must be a list")
        return DocumentResult[JsonValue](
            ok=bool(value["ok"]),
            code=str(value["code"]),
            message=str(value["message"]),
            data=_validate_json(value.get("data"), "data"),
            document_id=DocumentId(str(value["document_id"])) if value.get("document_id") else None,
            version_id=VersionId(str(value["version_id"])) if value.get("version_id") else None,
            blob_id=BlobId(str(value["blob_id"])) if value.get("blob_id") else None,
            artifact_id=ArtifactId(str(value["artifact_id"])) if value.get("artifact_id") else None,
            job_id=JobId(str(value["job_id"])) if value.get("job_id") else None,
            warnings=tuple(str(item) for item in warnings),
            retryable=bool(value.get("retryable", False)),
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> DocumentResult[JsonValue]:
        value = json.loads(payload)
        if not isinstance(value, Mapping):
            raise TypeError("DocumentResult JSON must contain an object")
        return cls.from_dict(value)


DocumentProvenance = Provenance
