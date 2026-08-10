"""Immutable metadata returned for generated and mutated document artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .models import ArtifactId, DocumentId, JobId, JsonValue, VersionId, _metadata

__all__ = ["ArtifactManifest", "DocumentArtifact"]


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    """Complete provenance for one verified immutable artifact."""

    artifact_id: ArtifactId
    operation: str
    input_sha256: tuple[str, ...]
    renderer: str
    renderer_version: str
    output_mime_type: str
    output_extension: str
    output_size: int
    output_sha256: str
    created_at: str
    template: str | None = None
    template_version: str | None = None
    document_id: DocumentId | None = None
    version_id: VersionId | None = None
    job_id: JobId | None = None
    warnings: tuple[str, ...] = ()
    provenance: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", ArtifactId(self.artifact_id))
        object.__setattr__(self, "input_sha256", tuple(self.input_sha256))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "provenance", _freeze(_metadata(self.provenance)))
        if self.document_id is not None:
            object.__setattr__(self, "document_id", DocumentId(self.document_id))
        if self.version_id is not None:
            object.__setattr__(self, "version_id", VersionId(self.version_id))
        if self.job_id is not None:
            object.__setattr__(self, "job_id", JobId(self.job_id))

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": str(self.artifact_id),
            "operation": self.operation,
            "input_sha256": list(self.input_sha256),
            "renderer": self.renderer,
            "renderer_version": self.renderer_version,
            "template": self.template,
            "template_version": self.template_version,
            "warnings": list(self.warnings),
            "output": {
                "mime_type": self.output_mime_type,
                "extension": self.output_extension,
                "size": self.output_size,
                "sha256": self.output_sha256,
            },
            "created_at": self.created_at,
            "document_id": str(self.document_id) if self.document_id else None,
            "version_id": str(self.version_id) if self.version_id else None,
            "job_id": str(self.job_id) if self.job_id else None,
            "provenance": _thaw(self.provenance),
        }


@dataclass(frozen=True, slots=True)
class DocumentArtifact:
    """A verified artifact with authoritative and optional export locations."""

    manifest: ArtifactManifest
    storage_path: Path
    export_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        value = self.manifest.to_dict()
        value["storage_path"] = str(self.storage_path)
        value["export_path"] = str(self.export_path) if self.export_path else None
        return value
