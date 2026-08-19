"""Sole public orchestration boundary for document parser execution and reads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kazma_core.safety.prompt_fence import format_untrusted_block

from .config import DocumentConfig, get_document_config
from .errors import (
    DocumentFormatError,
    DocumentOcrError,
    DocumentOcrUnavailableError,
    DocumentParseError,
    DocumentSandboxError,
)
from .knowledge import DocumentIndexResult, DocumentKnowledgeAdapter, DocumentSearchResult
from .models import DocumentIR, DocumentResult
from .mutation import get_mutation_registry
from .ocr import OcrHealth, get_ocr_health
from .operations import DocumentOperations, OperationScope
from .parsers.common import sha256_path
from .registry import ParserRegistry, get_parser_registry
from .renderers import RendererRegistry, get_renderer_registry
from .repository import DocumentAccessError, DocumentRepository
from .sandbox import SandboxRequest, run_isolated_subprocess
from .sniff import sniff_document
from .storage import ContentAddressedStorage

__all__ = ["DocumentReadResult", "DocumentService"]

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = 1
_WORKER_RESPONSE_KEYS = frozenset(
    {
        "protocol_version",
        "ok",
        "code",
        "message",
        "source_sha256",
        "ir_sha256",
        "ir",
    }
)


def _resolve_python_executable(repo_root: Path) -> str:
    """Return the best Python executable for parser subprocess bootstrapping.

    ``sys.executable`` points to the interpreter that launched the *current*
    process.  When Kazma runs outside its ``.venv`` (e.g. under a Copilot CLI
    that uses a system-wide Python), the system Python may lack the framework
    dependencies that the parser worker needs at import time and will crash
    during module discovery without writing ``result.json``.

    Priority:
    1. ``sys.executable`` if the process is already inside the venv.
    2. ``.venv\\Scripts\\python.exe`` relative to *repo_root* (Windows).
    3. ``.venv/bin/python`` relative to *repo_root* (POSIX).
    4. Fall back to ``sys.executable`` (existing behaviour).
    """
    # Already inside the venv — no override needed.
    if sys.prefix != sys.base_prefix:
        return sys.executable

    # Check the project-local venv.
    for candidate in (
        repo_root / ".venv" / "Scripts" / "python.exe",
        repo_root / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            logger.debug(
                "[documents.service] Resolved parser python to %s", candidate
            )
            return str(candidate)

    return sys.executable


@dataclass(frozen=True, slots=True)
class DocumentReadResult:
    text: str
    continuation: dict[str, int | bool | None]
    document: DocumentIR
    fenced: bool

    def as_tool_output(self) -> str:
        if not self.continuation["has_more"]:
            return self.text
        return (
            f"{self.text}\n"
            f"[Document continuation: next_offset={self.continuation['next_offset']}, "
            f"total_chars={self.continuation['total_chars']}]"
        )


class DocumentService:
    """Validate, isolate, parse, and page document content."""

    def __init__(
        self,
        *,
        config: DocumentConfig | None = None,
        registry: ParserRegistry | None = None,
        knowledge_adapter: DocumentKnowledgeAdapter | None = None,
        renderer_registry: RendererRegistry | None = None,
        mutation_registry: RendererRegistry | None = None,
        storage: ContentAddressedStorage | None = None,
        repository: DocumentRepository | None = None,
    ) -> None:
        self.config = config or get_document_config()
        self.registry = registry or get_parser_registry()
        self.knowledge_adapter = knowledge_adapter
        self.renderer_registry = renderer_registry or get_renderer_registry()
        self.mutation_registry = mutation_registry or get_mutation_registry()
        self.repository = repository
        self.operations = DocumentOperations(
            config=self.config,
            validator=self._parse_isolated,
            storage=storage,
            repository=repository,
            renderer_registry=self.renderer_registry,
            mutation_registry=self.mutation_registry,
        )

    async def generate(
        self,
        target_format: str,
        payload: dict[str, Any],
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        output_name: str | None = None,
        export_dir: str | Path | None = None,
        template: str | None = None,
        template_version: str | None = None,
        document_id: Any = None,
        version_id: Any = None,
        job_id: Any = None,
    ) -> DocumentResult[Any]:
        """Generate and atomically promote a verified document artifact."""

        try:
            scope = OperationScope(
                tenant_id,
                workspace_id,
                actor_id,
                document_id=document_id,
                version_id=version_id,
                job_id=job_id,
            )
        except (TypeError, ValueError) as exc:
            return self._operation_error("invalid_document_scope", str(exc))
        return await asyncio.to_thread(
            self.operations.generate,
            target_format=target_format,
            payload=payload,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            template=template,
            template_version=template_version,
        )

    async def convert(
        self,
        path: str | Path,
        target_format: str,
        *,
        approved_path: str | Path,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        output_name: str | None = None,
        export_dir: str | Path | None = None,
        approved_assets: tuple[str | Path, ...] = (),
        document_id: Any = None,
        version_id: Any = None,
        job_id: Any = None,
    ) -> DocumentResult[Any]:
        """Convert a caller-approved source through an isolated renderer."""

        try:
            source = self._validate_approved_path(path, approved_path)
            sniffed = sniff_document(source, self.config)
            self.registry.resolve(
                mime_type=sniffed.mime_type,
                extension=sniffed.extension,
            )
            assets = tuple(self._validate_path(item) for item in approved_assets)
            if any(not asset.is_relative_to(source.parent) for asset in assets):
                raise DocumentFormatError(
                    "Approved render assets must be workspace-local to the source document"
                )
            scope = OperationScope(
                tenant_id,
                workspace_id,
                actor_id,
                document_id=document_id,
                version_id=version_id,
                job_id=job_id,
            )
        except DocumentParseError as exc:
            return self._operation_error(exc.code, exc.safe_message)
        except (OSError, TypeError, ValueError) as exc:
            return self._operation_error("invalid_document_request", str(exc))
        return await asyncio.to_thread(
            self._convert_isolated,
            source,
            target_format=target_format,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            approved_assets=assets,
        )

    def _convert_isolated(
        self,
        source: Path,
        *,
        target_format: str,
        scope: OperationScope,
        output_name: str | None,
        export_dir: str | Path | None,
        approved_assets: tuple[Path, ...],
    ) -> DocumentResult[Any]:
        try:
            source_sha = sha256_path(source)
            self._parse_isolated(source)
            if sha256_path(source) != source_sha:
                return self._operation_error(
                    "document_changed", "Document changed during conversion preflight"
                )
            return self.operations.convert(
                source,
                target_format=target_format,
                scope=scope,
                output_name=output_name,
                export_dir=export_dir,
                approved_assets=approved_assets,
            )
        except DocumentParseError as exc:
            return self._operation_error(exc.code, exc.safe_message)
        except Exception as exc:
            return self._operation_error(
                "document_operation_failed",
                f"Document conversion failed safely ({type(exc).__name__})",
            )

    async def pdf_merge(
        self,
        paths: tuple[str | Path, ...],
        *,
        approved_paths: tuple[str | Path, ...],
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        output_name: str | None = None,
        export_dir: str | Path | None = None,
        document_id: Any = None,
        version_id: Any = None,
        job_id: Any = None,
    ) -> DocumentResult[Any]:
        """Merge bounded, approved PDFs in an isolated mutation worker."""

        prepared = self._prepare_pdf_request(
            paths,
            approved_paths,
            tenant_id,
            workspace_id,
            actor_id,
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
        )
        if isinstance(prepared, DocumentResult):
            return prepared
        sources, scope = prepared
        return await asyncio.to_thread(
            self.operations.pdf_operation,
            "pdf:merge",
            sources,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
        )

    async def pdf_split(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        start_page: int = 1,
        end_page: int = 0,
        output_name: str | None = None,
        export_dir: str | Path | None = None,
        document_id: Any = None,
        version_id: Any = None,
        job_id: Any = None,
    ) -> DocumentResult[Any]:
        prepared = self._prepare_pdf_request(
            (path,),
            (approved_path,),
            tenant_id,
            workspace_id,
            actor_id,
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
        )
        if isinstance(prepared, DocumentResult):
            return prepared
        source, scope = prepared
        return await asyncio.to_thread(
            self.operations.pdf_operation,
            "pdf:split",
            source,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            parameters={"start_page": start_page, "end_page": end_page},
        )

    async def pdf_info(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
    ) -> DocumentResult[Any]:
        prepared = self._prepare_pdf_request(
            (path,), (approved_path,), tenant_id, workspace_id, actor_id
        )
        if isinstance(prepared, DocumentResult):
            return prepared
        sources, scope = prepared
        source = sources[0]
        return await asyncio.to_thread(self.operations.pdf_info, source, scope=scope)

    async def pdf_fill_form(
        self,
        path: str | Path,
        fields: dict[str, str],
        *,
        approved_path: str | Path,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        output_name: str | None = None,
        export_dir: str | Path | None = None,
        document_id: Any = None,
        version_id: Any = None,
        job_id: Any = None,
    ) -> DocumentResult[Any]:
        prepared = self._prepare_pdf_request(
            (path,),
            (approved_path,),
            tenant_id,
            workspace_id,
            actor_id,
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
        )
        if isinstance(prepared, DocumentResult):
            return prepared
        source, scope = prepared
        return await asyncio.to_thread(
            self.operations.pdf_operation,
            "pdf:fill-form",
            source,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            parameters={"fields": fields},
        )

    async def redact(
        self,
        path: str | Path,
        terms: list[str],
        *,
        approved_path: str | Path,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        output_name: str | None = None,
        export_dir: str | Path | None = None,
        document_id: Any = None,
        version_id: Any = None,
        job_id: Any = None,
    ) -> DocumentResult[Any]:
        """Physically redact and independently verify a flattened PDF, or refuse."""

        prepared = self._prepare_pdf_request(
            (path,),
            (approved_path,),
            tenant_id,
            workspace_id,
            actor_id,
            document_id=document_id,
            version_id=version_id,
            job_id=job_id,
        )
        if isinstance(prepared, DocumentResult):
            return prepared
        source, scope = prepared
        return await asyncio.to_thread(
            self.operations.pdf_operation,
            "pdf:redact",
            source,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            parameters={"terms": terms},
        )

    async def parse_path(
        self,
        path: str | Path,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        force_ocr: bool = False,
        ocr_language: str | None = None,
        ocr_pages: tuple[int, ...] | None = None,
    ) -> DocumentIR:
        """Parse for a durable workflow with explicit authorization scope."""

        if not all(str(value).strip() for value in (tenant_id, workspace_id, actor_id)):
            raise ValueError("tenant_id, workspace_id, and actor_id are required")
        source = self._validate_path(path)
        return await asyncio.to_thread(
            self._parse_isolated,
            source,
            force_ocr=force_ocr,
            ocr_language=ocr_language,
            ocr_pages=ocr_pages,
        )

    async def ocr_path(
        self,
        path: str | Path,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        language: str | None = "auto",
        pages: tuple[int, ...] | None = None,
    ) -> DocumentIR:
        """Force OCR through the isolated parser worker."""

        return await self.parse_path(
            path,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
            force_ocr=True,
            ocr_language=language,
            ocr_pages=pages,
        )

    async def read_path(
        self,
        path: str | Path,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        page: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        block: str | int | None = None,
        offset: int = 0,
        max_chars: int = 20_000,
        fence: bool = True,
    ) -> DocumentReadResult:
        document = await self.parse_path(
            path,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=actor_id,
        )
        return self.read_ir(
            document,
            page=page,
            page_start=page_start,
            page_end=page_end,
            block=block,
            offset=offset,
            max_chars=max_chars,
            fence=fence,
        )

    async def parse_transient(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        force_ocr: bool = False,
        ocr_language: str | None = None,
        ocr_pages: tuple[int, ...] | None = None,
    ) -> DocumentIR:
        """Compatibility parse after a caller has approved the resolved path."""

        source = self._validate_approved_path(path, approved_path)
        return await asyncio.to_thread(
            self._parse_isolated,
            source,
            force_ocr=force_ocr,
            ocr_language=ocr_language,
            ocr_pages=ocr_pages,
        )

    async def ocr_transient(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        language: str | None = "auto",
        pages: tuple[int, ...] | None = None,
    ) -> DocumentIR:
        """Compatibility force-OCR after a caller approves the workspace path."""

        return await self.parse_transient(
            path,
            approved_path=approved_path,
            force_ocr=True,
            ocr_language=language,
            ocr_pages=pages,
        )

    def parse_ingested_blob(
        self,
        source: str | Path,
        *,
        force_ocr: bool = False,
        ocr_language: str | None = None,
        ocr_pages: tuple[int, ...] | None = None,
    ) -> DocumentIR:
        """Parse a durable-ingestion staged blob through the isolated worker.

        This is the sync entry the :class:`DocumentIngestionService` stage
        handlers call. The path is a coordinator-materialized copy of an
        immutable CAS ``originals`` blob (never a remote-client path), so no
        approval handshake is required — it is the single parsing path.
        """

        return self._parse_isolated(
            Path(source),
            force_ocr=force_ocr,
            ocr_language=ocr_language,
            ocr_pages=ocr_pages,
        )

    def ocr_health(self) -> OcrHealth:
        """Return live engine/language/rasterizer/image readiness."""

        return get_ocr_health(self.config.ocr_languages)

    def health(self) -> dict[str, object]:
        """Data contract for a later ``/api/documents/health`` endpoint."""

        return {
            "parsers": [
                {
                    "parser_id": item.parser_id,
                    "parser_version": item.parser_version,
                    "readiness": item.readiness.value,
                    "reason": item.reason,
                    "features": list(item.features),
                    "mime_types": list(item.mime_types),
                    "extensions": list(item.extensions),
                }
                for item in self.registry.capabilities()
            ],
            "ocr": self.ocr_health().to_dict(),
            "renderers": [
                {
                    "renderer_id": item.renderer_id,
                    "renderer_version": item.renderer_version,
                    "readiness": item.readiness.value,
                    "reason": item.reason,
                    "operations": list(item.operations),
                    "formats": list(item.formats),
                    "features": list(item.features),
                    "dependencies": dict(item.dependencies),
                    "system_binaries": dict(item.system_binaries),
                }
                for item in self.renderer_registry.capabilities()
            ],
            "mutators": [
                {
                    "renderer_id": item.renderer_id,
                    "renderer_version": item.renderer_version,
                    "readiness": item.readiness.value,
                    "reason": item.reason,
                    "operations": list(item.operations),
                    "features": list(item.features),
                    "dependencies": dict(item.dependencies),
                }
                for item in self.mutation_registry.capabilities()
            ],
        }

    def index_document_ir(
        self,
        document: DocumentIR,
        *,
        tenant_id: str,
        actor_id: str,
        library_id: str,
    ) -> DocumentResult[DocumentIndexResult]:
        """Publish canonical IR with explicit authenticated scope."""
        return self._knowledge().index_document_ir(
            document,
            tenant_id=tenant_id,
            actor_id=actor_id,
            library_id=library_id,
        )

    def unindex_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        library_id: str,
        document_id: Any,
    ) -> DocumentResult[dict[str, Any]]:
        return self._knowledge().unindex_document(
            tenant_id=tenant_id,
            actor_id=actor_id,
            library_id=library_id,
            document_id=document_id,
        )

    async def search_document(
        self,
        query: str,
        *,
        tenant_id: str,
        actor_id: str,
        library_id: str,
        document_id: Any,
        top_k: int = 5,
    ) -> DocumentResult[DocumentSearchResult]:
        return await self._knowledge().search_document(
            query,
            tenant_id=tenant_id,
            actor_id=actor_id,
            library_id=library_id,
            document_id=document_id,
            top_k=top_k,
        )

    async def search_library(
        self,
        query: str,
        *,
        tenant_id: str,
        library_id: str,
        top_k: int = 5,
        actor_id: str | None = None,
    ) -> DocumentResult[DocumentSearchResult]:
        return await self._knowledge().search_library(
            query,
            tenant_id=tenant_id,
            library_id=library_id,
            top_k=top_k,
            actor_id=actor_id,
        )

    def delete_document(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        document_id: Any,
        reason: str,
    ) -> DocumentResult[dict[str, Any]]:
        """Unindex active libraries (when knowledge is wired), then tombstone.

        Soft-delete / archive: the document leaves the library list
        (``deleted_at`` set). Physical content is reclaimed later by GC.
        Knowledge adapter is best-effort — tombstone still succeeds without it.
        """
        if self.repository is None:
            return DocumentResult(
                ok=False,
                code="repository_unavailable",
                message="Document repository is unavailable",
                document_id=document_id,
            )
        try:
            record = self.repository.get_document(
                tenant_id=tenant_id,
                document_id=document_id,
                actor_id=actor_id,
            )
        except DocumentAccessError:
            return DocumentResult(
                ok=False,
                code="document_access_denied",
                message="Document is unavailable or you lack permission to delete it",
                document_id=document_id,
            )
        if record is None:
            return DocumentResult(
                ok=False,
                code="document_access_denied",
                message="Document is unavailable",
                document_id=document_id,
            )
        libraries: list[str] = []
        if self.knowledge_adapter is not None:
            adapter = self.knowledge_adapter
            try:
                libraries = list(
                    sorted(
                        set(
                            adapter.repository.list_indexed_libraries(
                                tenant_id=tenant_id,
                                document_id=record.id,
                            )
                        )
                        | set(
                            adapter.store.list_document_libraries(
                                tenant_id=tenant_id,
                                document_id=str(record.id),
                            )
                        )
                    )
                )
            except Exception:
                libraries = []
            for library_id in libraries:
                try:
                    result = adapter.unindex_document(
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        library_id=library_id,
                        document_id=record.id,
                    )
                    if not result.ok:
                        # Soft-delete should still proceed; log via message.
                        libraries = [lid for lid in libraries if lid != library_id]
                except Exception:
                    continue
        try:
            self.repository.tombstone_document(
                tenant_id=tenant_id,
                document_id=record.id,
                actor_id=actor_id,
                reason=reason,
            )
        except DocumentAccessError:
            return DocumentResult(
                ok=False,
                code="document_access_denied",
                message="Document is unavailable or you lack permission to delete it",
                document_id=record.id,
            )
        except Exception as exc:
            return DocumentResult(
                ok=False,
                code="document_delete_failed",
                message=f"Document delete failed ({type(exc).__name__})",
                document_id=record.id,
            )
        return DocumentResult(
            ok=True,
            code="document_deleted",
            message="Document was unindexed and tombstoned"
            if libraries
            else "Document was archived (soft-deleted)",
            data={"libraries": libraries},
            document_id=record.id,
        )

    def _knowledge(self) -> DocumentKnowledgeAdapter:
        if self.knowledge_adapter is None:
            raise RuntimeError(
                "Document knowledge operations require an explicitly scoped adapter"
            )
        return self.knowledge_adapter

    async def read_transient(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        page: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        block: str | int | None = None,
        offset: int = 0,
        max_chars: int = 20_000,
        fence: bool = True,
    ) -> DocumentReadResult:
        document = await self.parse_transient(path, approved_path=approved_path)
        return self.read_ir(
            document,
            page=page,
            page_start=page_start,
            page_end=page_end,
            block=block,
            offset=offset,
            max_chars=max_chars,
            fence=fence,
        )

    def read_transient_sync(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        page: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        block: str | int | None = None,
        offset: int = 0,
        max_chars: int = 20_000,
        fence: bool = True,
    ) -> DocumentReadResult:
        """Synchronous adapter that still executes parser libraries out of process."""

        source = self._validate_approved_path(path, approved_path)
        document = self._parse_isolated(source)
        return self.read_ir(
            document,
            page=page,
            page_start=page_start,
            page_end=page_end,
            block=block,
            offset=offset,
            max_chars=max_chars,
            fence=fence,
        )

    @staticmethod
    def read_ir(
        document: DocumentIR,
        *,
        page: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        block: str | int | None = None,
        offset: int = 0,
        max_chars: int = 20_000,
        fence: bool = True,
    ) -> DocumentReadResult:
        if page is not None and (page_start is not None or page_end is not None):
            raise ValueError("page cannot be combined with page_start/page_end")
        if isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if isinstance(max_chars, bool) or max_chars <= 0:
            raise ValueError("max_chars must be a positive integer")
        first = page if page is not None else (page_start or 1)
        last = page if page is not None else (page_end or len(document.pages))
        if not document.pages:
            # Degenerate parse output — a hostile/malformed file a parser
            # tolerantly read as ZERO pages. This is document content, not a
            # caller argument error: the raw ValueError this used to raise
            # leaked through the typed boundary and failed certification on
            # every platform (deep-audit 2026-08-19 CI triage, round 7).
            raise DocumentFormatError("Document contains no readable pages")
        if first < 1 or last < first:
            raise ValueError("page selector is invalid")
        selected = [
            item for item in document.pages if first <= item.page_number <= last
        ]
        if not selected:
            raise DocumentFormatError("Requested document page does not exist")
        selected_block_ids: set[str] | None = None
        if block is not None:
            flattened = [
                candidate
                for item in selected
                for candidate in item.blocks
            ]
            if isinstance(block, int):
                matched = [flattened[block - 1]] if 1 <= block <= len(flattened) else []
            else:
                matched = [candidate for candidate in flattened if candidate.block_id == block]
            if not matched:
                raise DocumentFormatError("Requested document block does not exist")
            selected_block_ids = {candidate.block_id for candidate in matched}
        rendered: list[str] = []
        for item in selected:
            blocks = list(item.blocks)
            if selected_block_ids is not None:
                blocks = [
                    candidate
                    for candidate in blocks
                    if candidate.block_id in selected_block_ids
                ]
                if not blocks:
                    continue
            kind = str(item.metadata.get("kind", "page"))
            label = {
                "sheet": f"Sheet {item.metadata.get('sheet_name', item.page_number)}",
                "slide": f"Slide {item.page_number}",
            }.get(kind, f"Page {item.page_number}")
            rendered.append(f"--- {label} ---")
            rendered.extend(candidate.text for candidate in blocks if candidate.text)
        full_text = "\n".join(rendered)
        window = full_text[offset : offset + max_chars]
        next_offset = offset + len(window) if offset + len(window) < len(full_text) else None
        output = format_untrusted_block(window, source="document") if fence else window
        return DocumentReadResult(
            text=output,
            continuation={
                "offset": offset,
                "next_offset": next_offset,
                "has_more": next_offset is not None,
                "returned_chars": len(window),
                "total_chars": len(full_text),
            },
            document=document,
            fenced=fence,
        )

    @staticmethod
    def _validate_path(path: str | Path) -> Path:
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise DocumentFormatError("Document path is not a file")
        return source

    def _validate_approved_path(
        self, path: str | Path, approved_path: str | Path
    ) -> Path:
        source = self._validate_path(path)
        approved = Path(approved_path).expanduser().resolve(strict=True)
        if source != approved:
            raise DocumentFormatError("Resolved document path was not approved by the caller")
        return source

    def _approved_pdf_sources(
        self,
        paths: tuple[str | Path, ...],
        approved_paths: tuple[str | Path, ...],
    ) -> tuple[Path, ...]:
        if not paths or len(paths) != len(approved_paths):
            raise DocumentFormatError("Every PDF input requires an approved resolved path")
        if len(paths) > self.config.intake_max_files:
            raise DocumentFormatError("Too many PDF inputs")
        sources = tuple(
            self._validate_approved_path(path, approved)
            for path, approved in zip(paths, approved_paths, strict=True)
        )
        aggregate = sum(source.stat().st_size for source in sources)
        if aggregate > self.config.intake_max_bytes:
            raise DocumentFormatError("PDF inputs exceed the aggregate size limit")
        for source in sources:
            sniffed = sniff_document(source, self.config)
            if sniffed.mime_type != "application/pdf" or sniffed.extension != ".pdf":
                raise DocumentFormatError("PDF operation input is not a valid PDF")
            self.registry.resolve(
                mime_type=sniffed.mime_type,
                extension=sniffed.extension,
            )
        return sources

    def _prepare_pdf_request(
        self,
        paths: tuple[str | Path, ...],
        approved_paths: tuple[str | Path, ...],
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
        *,
        document_id: Any = None,
        version_id: Any = None,
        job_id: Any = None,
    ) -> tuple[tuple[Path, ...], OperationScope] | DocumentResult[Any]:
        try:
            return (
                self._approved_pdf_sources(paths, approved_paths),
                OperationScope(
                    tenant_id,
                    workspace_id,
                    actor_id,
                    document_id=document_id,
                    version_id=version_id,
                    job_id=job_id,
                ),
            )
        except DocumentParseError as exc:
            return self._operation_error(exc.code, exc.safe_message)
        except (OSError, TypeError, ValueError) as exc:
            return self._operation_error("invalid_document_request", str(exc))

    @staticmethod
    def _operation_error(code: str, message: str) -> DocumentResult[Any]:
        return DocumentResult(ok=False, code=code, message=message)

    def _parse_isolated(
        self,
        source: Path,
        *,
        force_ocr: bool = False,
        ocr_language: str | None = None,
        ocr_pages: tuple[int, ...] | None = None,
    ) -> DocumentIR:
        sniffed = sniff_document(source, self.config)
        self.registry.resolve(
            mime_type=sniffed.mime_type,
            extension=sniffed.extension,
        )
        source_sha = sha256_path(source)
        root = self.config.storage_root / "parser-runs"
        root.mkdir(parents=True, exist_ok=True)
        run_dir = root / f"parse-{uuid.uuid4().hex}"
        run_dir.mkdir(parents=False, exist_ok=False)
        request_path = run_dir / "request.json"
        result_path = run_dir / "result.json"
        # Absolute paths: the sandbox sets cwd=run_dir, so relative request/
        # result paths would resolve incorrectly and fail to open request.json.
        source_abs = source.resolve()
        request_abs = request_path.resolve()
        result_abs = result_path.resolve()
        request = {
            "protocol_version": _PROTOCOL_VERSION,
            "source_path": str(source_abs),
            "source_sha256": source_sha,
            "mime_type": sniffed.mime_type,
            "extension": sniffed.extension,
            "config": {
                key: str(value) if isinstance(value, Path) else value
                for key, value in asdict(self.config).items()
            },
            "ocr": {
                "force": force_ocr,
                "language": ocr_language,
                "pages": list(ocr_pages) if ocr_pages is not None else None,
            },
        }
        request_path.write_text(
            json.dumps(request, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        repo_root = Path(__file__).resolve().parents[3]
        core_root = repo_root / "kazma-core"
        python_exe = _resolve_python_executable(repo_root)

        bootstrap = (
            "import runpy,sys;"
            f"sys.path.insert(0,{str(core_root)!r});"
            "runpy.run_module('kazma_core.documents.parser_worker',run_name='__main__')"
        )
        sandbox_request = SandboxRequest(
            command=(
                python_exe,
                "-I",
                "-c",
                bootstrap,
                str(request_abs),
                str(result_abs),
            ),
            work_dir=run_dir,
            timeout_seconds=self.config.worker_timeout_seconds,
            stdout_limit_bytes=4_096,
            stderr_limit_bytes=65_536,
            memory_limit_bytes=self.config.worker_memory_mb * 1024 * 1024,
            cpu_limit_seconds=self.config.worker_timeout_seconds,
            env={
                # OpenBLAS (numpy / scipy) and OpenMP allocate per-thread
                # buffers at import time.  With the default thread count on
                # many-core machines these can exceed the Job Object memory
                # limit before the process even reaches the try/except in
                # the parser worker, causing an uncatchable exit(1).
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
            },
        )
        try:
            result = run_isolated_subprocess(sandbox_request)
            if result.resource_limit_degraded_reason:
                logger.warning(
                    "[documents.service] Parser resource limits were partially "
                    "degraded for %s: %s",
                    source.name,
                    result.resource_limit_degraded_reason,
                )
            if result.timed_out:
                raise DocumentSandboxError(
                    "Document parser exceeded its time limit",
                    code="parser_timeout",
                )
            if result.output_limit_exceeded:
                raise DocumentSandboxError(
                    "Document parser exceeded its subprocess output limit",
                    code="parser_output_limit",
                )
            if not result_path.is_file():
                _debug_stderr = result.stderr[:500] if result.stderr else b"<empty>"
                _debug_stdout = result.stdout[:500] if result.stdout else b"<empty>"
                logger.error(
                    "[documents.service] Parser produced no result.json "
                    "(returncode=%s, timed_out=%s, output_limit=%s, "
                    "stderr=%s, stdout=%s, run_dir=%s)",
                    result.returncode,
                    result.timed_out,
                    result.output_limit_exceeded,
                    _debug_stderr,
                    _debug_stdout,
                    run_dir,
                )
                raise DocumentSandboxError("Document parser produced no result")
            if result_path.stat().st_size > self.config.worker_result_max_bytes:
                raise DocumentSandboxError(
                    "Document parser result exceeds the configured limit",
                    code="parser_output_limit",
                )
            response = json.loads(result_path.read_text(encoding="utf-8"))
            return self._validate_response(response, source_sha)
        except json.JSONDecodeError as exc:
            raise DocumentSandboxError("Document parser returned invalid JSON") from exc
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @staticmethod
    def _validate_response(response: Any, source_sha: str) -> DocumentIR:
        if not isinstance(response, dict):
            raise DocumentSandboxError("Document parser response is not an object")
        if response.get("protocol_version") != _PROTOCOL_VERSION:
            raise DocumentSandboxError("Document parser protocol version mismatch")
        if not isinstance(response.get("ok"), bool):
            raise DocumentSandboxError("Document parser response has invalid status")
        if not response["ok"]:
            code = response.get("code")
            message = response.get("message")
            if not isinstance(code, str) or not isinstance(message, str):
                raise DocumentSandboxError("Document parser error response is invalid")
            if code == "ocr_unavailable":
                raise DocumentOcrUnavailableError(message)
            if code.startswith("ocr_") or code == "ocr_failed":
                raise DocumentOcrError(message, code=code)
            raise DocumentParseError(message, code=code)
        if set(response) != _WORKER_RESPONSE_KEYS:
            raise DocumentSandboxError("Document parser response schema mismatch")
        if response.get("source_sha256") != source_sha:
            raise DocumentSandboxError("Document parser source checksum mismatch")
        ir_value = response.get("ir")
        if not isinstance(ir_value, dict):
            raise DocumentSandboxError("Document parser IR is missing")
        document = DocumentIR.from_dict(ir_value)
        actual_ir_sha = hashlib.sha256(document.to_json().encode("utf-8")).hexdigest()
        if response.get("ir_sha256") != actual_ir_sha:
            raise DocumentSandboxError("Document parser result checksum mismatch")
        if document.metadata.get("source_sha256") != source_sha:
            raise DocumentSandboxError("Document IR source checksum mismatch")
        return document
