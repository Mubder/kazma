"""Atomic parent-side orchestration for isolated rendering and PDF mutation."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .artifacts import ArtifactManifest, DocumentArtifact
from .config import DocumentConfig
from .errors import DocumentParseError, DocumentSandboxError
from .models import (
    ArtifactId,
    DocumentId,
    DocumentResult,
    JobId,
    VersionId,
    new_artifact_id,
)
from .mutation import get_mutation_registry
from .parsers.common import sha256_path
from .renderers import RendererCapability, RendererRegistry, get_renderer_registry
from .repository import DocumentRepository
from .resources import validate_restricted_render_resources
from .sandbox import SandboxRequest, run_isolated_subprocess
from .storage import ContentAddressedStorage

_PROTOCOL_VERSION = 1
_EXTENSIONS = {
    "markdown": "md",
    "html": "html",
    "pdf": "pdf",
    "docx": "docx",
    "xlsx": "xlsx",
    "pptx": "pptx",
}
_MIME_TYPES = {
    "md": "text/markdown",
    "html": "text/html",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_SLUG = re.compile(r"[^a-zA-Z0-9\u0600-\u06ff._-]+")


@dataclass(frozen=True, slots=True)
class OperationScope:
    tenant_id: str
    workspace_id: str
    actor_id: str
    document_id: DocumentId | None = None
    version_id: VersionId | None = None
    job_id: JobId | None = None

    def __post_init__(self) -> None:
        if not all(
            str(value).strip() for value in (self.tenant_id, self.workspace_id, self.actor_id)
        ):
            raise ValueError("tenant_id, workspace_id, and actor_id are required")
        if (self.document_id is None) != (self.version_id is None):
            raise ValueError("document_id and version_id must be supplied together")


class DocumentOperations:
    """Executes worker protocols and promotes only fully verified artifacts."""

    def __init__(
        self,
        *,
        config: DocumentConfig,
        validator: Callable[[Path], object],
        storage: ContentAddressedStorage | None = None,
        repository: DocumentRepository | None = None,
        renderer_registry: RendererRegistry | None = None,
        mutation_registry: RendererRegistry | None = None,
    ) -> None:
        self.config = config
        self.validator = validator
        self.storage = storage or ContentAddressedStorage(config.storage_root)
        self.repository = repository
        self.renderers = renderer_registry or get_renderer_registry()
        self.mutations = mutation_registry or get_mutation_registry()

    @staticmethod
    def _error(code: str, message: str, *, retryable: bool = False) -> DocumentResult[Any]:
        return DocumentResult(
            ok=False,
            code=code,
            message=message,
            retryable=retryable,
        )

    @staticmethod
    def _capability(
        registry: RendererRegistry, operation: str
    ) -> RendererCapability | DocumentResult[Any]:
        try:
            capability = registry.resolve(operation)
        except ValueError:
            return DocumentOperations._error(
                "unsupported_document_operation", "The requested document operation is unsupported"
            )
        if not capability.available:
            reason = capability.reason or f"{capability.renderer_id} is unavailable"
            if capability.renderer_id == "libreoffice" or "soffice" in reason.lower():
                reason = (
                    f"{reason}. Install LibreOffice for high-fidelity Office→PDF, "
                    "or ensure a pure-Python fallback (reportlab + python-docx) is installed."
                )
            return DocumentOperations._error(
                "document_engine_unavailable",
                reason,
            )
        return capability

    def generate(
        self,
        *,
        target_format: str,
        payload: Mapping[str, Any],
        scope: OperationScope,
        output_name: str | None,
        export_dir: str | Path | None,
        template: str | None = None,
        template_version: str | None = None,
        approved_assets: Sequence[Path] = (),
    ) -> DocumentResult[DocumentArtifact]:
        target = target_format.lower().lstrip(".")
        operation = f"generate:{target}"
        capability = self._capability(self.renderers, operation)
        if isinstance(capability, DocumentResult):
            return capability
        render_payload = dict(payload)
        if template is not None:
            render_payload["_template"] = template
            render_payload["_template_version"] = template_version or "1"
        return self._artifact_operation(
            module="kazma_core.documents.renderer_worker",
            operation=operation,
            capability=capability,
            extension=_EXTENSIONS.get(target, target),
            payload=render_payload,
            sources=(),
            scope=scope,
            output_name=output_name or str(payload.get("title", "document")),
            export_dir=export_dir,
            template=template,
            template_version=template_version,
            approved_assets=approved_assets,
        )

    def convert(
        self,
        source: Path,
        *,
        target_format: str,
        scope: OperationScope,
        output_name: str | None,
        export_dir: str | Path | None,
        approved_assets: Sequence[Path] = (),
    ) -> DocumentResult[DocumentArtifact]:
        source_format = source.suffix.lower().lstrip(".")
        if source_format == "md":
            source_format = "markdown"
        target = target_format.lower().lstrip(".")
        operation = f"convert:{source_format}:{target}"
        if source_format in {"html", "htm", "markdown"} and target in {"pdf", "html", "docx"}:
            try:
                text = source.read_text(encoding="utf-8")
                validate_restricted_render_resources(
                    text,
                    approved_asset_names=frozenset(path.name for path in approved_assets),
                )
            except DocumentParseError as exc:
                return self._error(exc.code, exc.safe_message)
            except (OSError, UnicodeError):
                return self._error(
                    "invalid_document_encoding",
                    "Document conversion source must be valid UTF-8 text",
                )
        try:
            candidates = self.renderers.resolve_all(operation)
        except ValueError:
            return self._error(
                "unsupported_document_operation",
                "The requested document operation is unsupported",
            )
        ready = [item for item in candidates if item.available]
        if not ready:
            first = candidates[0]
            reason = first.reason or f"{first.renderer_id} is unavailable"
            return self._error("document_engine_unavailable", reason)

        last: DocumentResult[Any] | None = None
        for capability in ready:
            result = self._artifact_operation(
                module="kazma_core.documents.renderer_worker",
                operation=operation,
                capability=capability,
                extension=_EXTENSIONS.get(target, target),
                payload={},
                sources=(source,),
                scope=scope,
                output_name=output_name or source.stem,
                export_dir=export_dir,
                approved_assets=approved_assets,
            )
            if result.ok:
                return result
            last = result
            # LibreOffice may probe "ready" yet fail at convert time on Windows;
            # fall through to pure-Python engines (e.g. reportlab-office).
            if capability.renderer_id != "libreoffice":
                return result
        return last or self._error(
            "document_engine_unavailable", "No document conversion engine succeeded"
        )

    def pdf_info(
        self, source: Path, *, scope: OperationScope
    ) -> DocumentResult[dict[str, Any]]:
        del scope
        capability = self._capability(self.mutations, "pdf:info")
        if isinstance(capability, DocumentResult):
            return capability
        run_dir = self._run_dir("pdf-info")
        try:
            source_sha = sha256_path(source)
            request = self._mutation_request(
                "pdf:info", capability, (source,), (source_sha,)
            )
            response = self._run_worker(
                run_dir, "kazma_core.documents.mutation_worker", request
            )
            error = self._response_error(response)
            if error:
                return error
            if response.get("source_sha256") != [source_sha]:
                return self._error(
                    "worker_checksum_mismatch", "PDF worker source checksum mismatch"
                )
            report = response.get("report")
            if not isinstance(report, dict):
                return self._error("invalid_worker_response", "PDF worker report is invalid")
            return DocumentResult(
                ok=True,
                code="pdf_info",
                message="PDF inspected",
                data=report,
            )
        except DocumentParseError as exc:
            return self._error(exc.code, exc.safe_message, retryable=exc.retryable)
        except Exception as exc:
            return self._error(
                "document_worker_failed",
                f"PDF inspection failed safely ({type(exc).__name__})",
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    def pdf_operation(
        self,
        operation: str,
        sources: Sequence[Path],
        *,
        scope: OperationScope,
        output_name: str | None,
        export_dir: str | Path | None,
        parameters: Mapping[str, Any] | None = None,
    ) -> DocumentResult[DocumentArtifact]:
        capability = self._capability(self.mutations, operation)
        if isinstance(capability, DocumentResult):
            return capability
        return self._artifact_operation(
            module="kazma_core.documents.mutation_worker",
            operation=operation,
            capability=capability,
            extension="pdf",
            payload=dict(parameters or {}),
            sources=tuple(sources),
            scope=scope,
            output_name=output_name or operation.replace(":", "-"),
            export_dir=export_dir,
        )

    def _artifact_operation(
        self,
        *,
        module: str,
        operation: str,
        capability: RendererCapability,
        extension: str,
        payload: dict[str, Any],
        sources: tuple[Path, ...],
        scope: OperationScope,
        output_name: str,
        export_dir: str | Path | None,
        template: str | None = None,
        template_version: str | None = None,
        approved_assets: Sequence[Path] = (),
    ) -> DocumentResult[DocumentArtifact]:
        artifact_id = new_artifact_id()
        run_dir = self._run_dir(operation.replace(":", "-"))
        export: Path | None = None
        try:
            source_hashes = tuple(sha256_path(path) for path in sources)
            assets_dir = run_dir / "assets"
            assets_dir.mkdir()
            asset_records: list[dict[str, str]] = []
            seen_assets: set[str] = set()
            for source in approved_assets:
                path = source.resolve(strict=True)
                if not path.is_file():
                    raise ValueError("approved render asset is not a file")
                if path.name in seen_assets:
                    raise ValueError("approved render asset names must be unique")
                seen_assets.add(path.name)
                destination = assets_dir / path.name
                shutil.copyfile(path, destination)
                asset_records.append(
                    {"name": destination.name, "sha256": sha256_path(destination)}
                )
            request: dict[str, Any] = {
                "protocol_version": _PROTOCOL_VERSION,
                "operation": operation,
                "renderer": capability.renderer_id,
                "renderer_version": capability.renderer_version,
                "output_name": f"output.{extension}",
                "payload": payload,
                # Absolute path: worker sandbox cwd is run_dir.
                "source_path": str(sources[0].resolve()) if len(sources) == 1 else None,
                "source_sha256": source_hashes[0] if len(source_hashes) == 1 else None,
                "approved_assets": asset_records,
                "library_timeout_seconds": min(self.config.worker_timeout_seconds, 120),
                "max_output_bytes": self.config.max_expanded_bytes,
                "limits": {
                    "max_sheets": self.config.max_sheets,
                    "max_slides": self.config.max_slides,
                    "max_rows_per_sheet": self.config.max_rows_per_sheet,
                    "max_cells": self.config.max_cells,
                    "max_images": self.config.max_images,
                },
            }
            if module.endswith("mutation_worker"):
                request = self._mutation_request(
                    operation, capability, sources, source_hashes
                )
                request.update(payload)
            response = self._run_worker(run_dir, module, request)
            error = self._response_error(response)
            if error:
                return error
            output = run_dir / str(response.get("output_name", ""))
            expected_output = run_dir / f"output.{extension}"
            if output != expected_output or not output.is_file():
                return self._error("invalid_worker_response", "Worker output path is invalid")
            response_warnings = response.get("warnings")
            if (
                response.get("renderer") != capability.renderer_id
                or response.get("renderer_version") != capability.renderer_version
                or not isinstance(response_warnings, list)
                or any(not isinstance(item, str) for item in response_warnings)
                or isinstance(response.get("output_size"), bool)
                or not isinstance(response.get("output_size"), int)
            ):
                return self._error(
                    "invalid_worker_response", "Worker output metadata is invalid"
                )
            actual_sha = sha256_path(output)
            actual_size = output.stat().st_size
            expected_worker_sources: str | list[str] | None = (
                list(source_hashes)
                if module.endswith("mutation_worker")
                else (source_hashes[0] if len(source_hashes) == 1 else None)
            )
            if (
                response.get("output_sha256") != actual_sha
                or response.get("output_size") != actual_size
                or response.get("source_sha256") != expected_worker_sources
                or response.get("output_extension") != extension
                or response.get("output_mime_type") != _MIME_TYPES.get(extension)
                or actual_size > self.config.max_expanded_bytes
            ):
                return self._error(
                    "worker_checksum_mismatch", "Worker output verification failed"
                )
            if any(sha256_path(path) != digest for path, digest in zip(sources, source_hashes)):
                return self._error("document_changed", "A source changed during processing")
            roundtrip = self.validator(output)
            self._validate_roundtrip_content(operation, payload, roundtrip)
            warnings = tuple(response_warnings)
            report = response.get("report")
            provenance: dict[str, Any] = {
                "operation": operation,
                "workspace_id": scope.workspace_id,
            }
            if isinstance(report, dict):
                provenance["report"] = report
            manifest = ArtifactManifest(
                artifact_id=artifact_id,
                operation=operation,
                input_sha256=source_hashes,
                renderer=capability.renderer_id,
                renderer_version=capability.renderer_version,
                template=template,
                template_version=template_version,
                warnings=warnings,
                output_mime_type=str(response["output_mime_type"]),
                output_extension=f".{extension}",
                output_size=actual_size,
                output_sha256=actual_sha,
                created_at=datetime.now(UTC).isoformat(timespec="seconds"),
                document_id=scope.document_id,
                version_id=scope.version_id,
                job_id=scope.job_id,
                provenance=provenance,
            )
            if (
                scope.document_id is not None
                and scope.version_id is not None
                and self.repository is None
            ):
                return self._error(
                    "repository_unavailable",
                    "Durable artifact scope requires a document repository",
                )
            with output.open("rb") as handle:
                stored = self.storage.put_stream(
                    handle,
                    kind="artifacts",
                    max_bytes=self.config.max_expanded_bytes,
                    expected_sha256=actual_sha,
                )
            export = (
                self._export_atomic(
                    stored.path,
                    Path(export_dir),
                    output_name,
                    extension,
                    manifest.artifact_id,
                )
                if export_dir is not None
                else None
            )
            persisted_artifact_id: ArtifactId | None = None
            if scope.document_id is not None and scope.version_id is not None:
                assert self.repository is not None
                blob = self.repository.register_blob(
                    tenant_id=scope.tenant_id,
                    sha256=stored.sha256,
                    byte_size=stored.byte_size,
                    storage_kind="artifacts",
                )
                record = self.repository.create_artifact(
                    tenant_id=scope.tenant_id,
                    document_id=scope.document_id,
                    version_id=scope.version_id,
                    actor_id=scope.actor_id,
                    blob_id=blob.id,
                    artifact_type=operation,
                    metadata=manifest.to_dict(),
                    artifact_id=artifact_id,
                )
                persisted_artifact_id = record.id
                if record.id != manifest.artifact_id:
                    manifest = replace(manifest, artifact_id=record.id)
            artifact = DocumentArtifact(manifest, stored.path, export)
            return DocumentResult(
                ok=True,
                code="artifact_ready",
                message="Document artifact generated and verified",
                data=artifact,
                artifact_id=persisted_artifact_id or artifact_id,
                document_id=scope.document_id,
                version_id=scope.version_id,
                job_id=scope.job_id,
                warnings=warnings,
            )
        except DocumentParseError as exc:
            if export is not None:
                export.unlink(missing_ok=True)
            return self._error(exc.code, exc.safe_message, retryable=exc.retryable)
        except Exception as exc:
            if export is not None:
                export.unlink(missing_ok=True)
            return self._error(
                "document_operation_failed",
                f"Document operation failed safely ({type(exc).__name__})",
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    def _mutation_request(
        self,
        operation: str,
        capability: RendererCapability,
        sources: Sequence[Path],
        hashes: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "protocol_version": _PROTOCOL_VERSION,
            "operation": operation,
            "renderer": capability.renderer_id,
            "renderer_version": capability.renderer_version,
            "output_name": "output.pdf",
            "sources": [
                {"path": str(path.resolve()), "sha256": digest}
                for path, digest in zip(sources, hashes, strict=True)
            ],
            "max_files": self.config.intake_max_files,
            "max_aggregate_bytes": self.config.intake_max_bytes,
            "max_pages": self.config.max_pages,
            "max_output_bytes": self.config.max_expanded_bytes,
        }

    def _run_worker(
        self, run_dir: Path, module: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        request_path = run_dir / "request.json"
        result_path = run_dir / "result.json"
        request_payload = json.dumps(
            request, ensure_ascii=False, allow_nan=False, sort_keys=True
        ).encode("utf-8")
        if len(request_payload) > self.config.intake_max_bytes:
            raise DocumentSandboxError(
                "Document worker request exceeds the configured input limit",
                code="document_limit_exceeded",
            )
        with request_path.open("xb") as handle:
            handle.write(request_payload)
            handle.flush()
            os.fsync(handle.fileno())
        core_root = Path(__file__).resolve().parents[2]
        bootstrap = (
            "import runpy,sys;"
            f"sys.path.insert(0,{str(core_root)!r});"
            f"runpy.run_module({module!r},run_name='__main__')"
        )
        result = run_isolated_subprocess(
            SandboxRequest(
                command=(
                    sys.executable,
                    "-I",
                    "-c",
                    bootstrap,
                    # Absolute paths: sandbox cwd is run_dir.
                    str(request_path.resolve()),
                    str(result_path.resolve()),
                ),
                work_dir=run_dir,
                timeout_seconds=self.config.worker_timeout_seconds,
                stdout_limit_bytes=4_096,
                stderr_limit_bytes=65_536,
                memory_limit_bytes=self.config.worker_memory_mb * 1024 * 1024,
                cpu_limit_seconds=self.config.worker_timeout_seconds,
            )
        )
        if result.timed_out:
            raise DocumentSandboxError(
                "Document worker exceeded its time limit", code="document_worker_timeout"
            )
        if result.output_limit_exceeded:
            raise DocumentSandboxError(
                "Document worker exceeded its output limit", code="document_worker_output_limit"
            )
        if not result_path.is_file():
            raise DocumentSandboxError("Document worker produced no result")
        if result_path.stat().st_size > self.config.worker_result_max_bytes:
            raise DocumentSandboxError(
                "Document worker result exceeded its limit",
                code="document_worker_output_limit",
            )
        try:
            value = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DocumentSandboxError("Document worker returned invalid JSON") from exc
        if not isinstance(value, dict) or value.get("protocol_version") != _PROTOCOL_VERSION:
            raise DocumentSandboxError("Document worker protocol mismatch")
        if value.get("ok") is False:
            expected = {"protocol_version", "ok", "code", "message"}
        elif module.endswith("mutation_worker") and request.get("operation") == "pdf:info":
            expected = {
                "protocol_version",
                "ok",
                "code",
                "message",
                "source_sha256",
                "report",
            }
        else:
            expected = {
                "protocol_version",
                "ok",
                "code",
                "message",
                "renderer",
                "renderer_version",
                "source_sha256",
                "output_name",
                "output_extension",
                "output_mime_type",
                "output_size",
                "output_sha256",
                "warnings",
            }
            if module.endswith("mutation_worker"):
                expected.add("report")
        if set(value) != expected:
            raise DocumentSandboxError("Document worker response schema mismatch")
        return value

    @staticmethod
    def _response_error(response: Mapping[str, Any]) -> DocumentResult[Any] | None:
        if not isinstance(response.get("ok"), bool):
            return DocumentOperations._error(
                "invalid_worker_response", "Document worker returned an invalid status"
            )
        if response["ok"]:
            return None
        code = response.get("code")
        message = response.get("message")
        if not isinstance(code, str) or not isinstance(message, str):
            return DocumentOperations._error(
                "invalid_worker_response", "Document worker error response is invalid"
            )
        return DocumentOperations._error(code, message)

    def _run_dir(self, prefix: str) -> Path:
        root = self.config.storage_root / "operation-runs"
        root.mkdir(parents=True, exist_ok=True)
        run_dir = root / f"{prefix}-{uuid.uuid4().hex}"
        run_dir.mkdir()
        try:
            run_dir.chmod(0o700)
        except OSError:
            pass
        return run_dir

    @staticmethod
    def _validate_roundtrip_content(
        operation: str, payload: Mapping[str, Any], document: object
    ) -> None:
        """Require a generated content sentinel to survive a parser round trip."""

        if not operation.startswith("generate:"):
            return
        probe = ""
        if operation == "generate:xlsx":
            sheets = payload.get("sheets")
            if isinstance(sheets, list):
                for sheet in sheets:
                    if not isinstance(sheet, Mapping):
                        continue
                    rows = sheet.get("rows")
                    if not isinstance(rows, list):
                        continue
                    probe = next(
                        (
                            str(cell)
                            for row in rows
                            if isinstance(row, list)
                            for cell in row
                            if str(cell).strip()
                        ),
                        "",
                    )
                    if probe:
                        break
        else:
            probe = str(payload.get("title", "")).strip()
        if not probe:
            return
        pages = getattr(document, "pages", ())
        text = "\n".join(
            str(getattr(block, "text", ""))
            for page in pages
            for block in getattr(page, "blocks", ())
        )
        def normalize(value: str) -> str:
            return " ".join(value.casefold().split())

        if normalize(probe) not in normalize(text):
            raise DocumentSandboxError(
                "Generated document failed round-trip content verification",
                code="invalid_output_content",
            )

    @staticmethod
    def _export_atomic(
        source: Path,
        directory: Path,
        requested_name: str,
        extension: str,
        artifact_id: ArtifactId,
    ) -> Path:
        directory = directory.expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        stem = _SLUG.sub("-", Path(requested_name).stem).strip(".-_")[:80] or "document"
        target = directory / f"{stem}-{artifact_id}-{uuid.uuid4().hex}.{extension}"
        temporary = directory / f".{target.name}.{uuid.uuid4().hex}.writing"
        try:
            with source.open("rb") as reader, temporary.open("xb") as writer:
                shutil.copyfileobj(reader, writer, length=1024 * 1024)
                writer.flush()
                os.fsync(writer.fileno())
            os.replace(temporary, target)
            try:
                descriptor = os.open(
                    directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
            except OSError:
                pass
            else:
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return target
        finally:
            temporary.unlink(missing_ok=True)
