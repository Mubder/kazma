"""Sole public orchestration boundary for document parser execution and reads."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from kazma_core.safety.prompt_fence import format_untrusted_block

from .config import DocumentConfig
from .cancellation import run_cancellable
from .errors import (
    DocumentFormatError,
    DocumentOcrError,
    DocumentOcrUnavailableError,
    DocumentParseError,
    DocumentSandboxError,
    infrastructure_errors,
)
from .knowledge import DocumentIndexResult, DocumentKnowledgeAdapter, DocumentSearchResult
from .models import DocumentIR, DocumentResult
from .mutation import get_mutation_registry
from .ocr import OcrHealth, get_ocr_health
from .ocr.neural_models import model_directory
from .operations import DocumentOperations, OperationScope
from .parsers.common import sha256_path
from .policy import DocumentPolicy, policy_operation
from .registry import ParserRegistry, get_parser_registry
from .renderers import RendererRegistry, get_renderer_registry
from .repository import DocumentAccessError, DocumentRepository
from .sandbox import SandboxRequest, run_isolated_subprocess
from .sniff import SniffResult
from .storage import ContentAddressedStorage
from .worker_container import (
    CONTAINER,
    isolation_report,
    parser_container_request,
    process_profile_refusal,
    run_container_worker,
    worker_isolation_mode,
)

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


#: One page's OCR time before any page of the document has been read: the
#: neural engine on one core read the pages of a live Arabic scan in 14-45 s
#: (2026-10-22).
_FIRST_PAGE_SECONDS = 45.0
#: A worker run's fixed cost: the worker starting, the models loading, the
#: document parsed again.
_RUN_OVERHEAD_SECONDS = 10.0
#: At most this many pages in one run, however fast they go.
_MAX_BATCH_PAGES = 12
#: A run is sized to this share of its time limit: room for a slow page.
_BATCH_SHARE = 0.6


@dataclass(frozen=True, slots=True)
class OcrPass:
    """What one :meth:`DocumentService.ocr_in_batches` pass read.

    ``document`` holds every page, the ones OCR read in this pass included;
    ``remaining`` names the pages it did not reach (a caller's time budget
    ran out), for the next pass.
    """

    document: DocumentIR
    read: tuple[int, ...]
    failed: tuple[int, ...]
    remaining: tuple[int, ...]
    seconds: float


def _page_ranges(numbers: tuple[int, ...] | list[int]) -> str:
    """1, 2, 3, 5 -> "1-3, 5"."""

    ordered = sorted(set(int(n) for n in numbers))
    parts: list[str] = []
    start = prev = None
    for n in ordered:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            parts.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = n
    if start is not None:
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ", ".join(parts)


#: Seconds of a tool call's limit kept for its answer once OCR stops: the
#: reply to build, a page that ran longer than the ones before it.
_OCR_MARGIN_SECONDS = 25.0


async def tool_ocr_budget() -> float:
    """How long a tool may OCR: the tool's own time limit, less a margin.

    A chat tool call is ended at ``agent.tool_timeout_seconds`` (120 s by
    default). OCR is about 15-45 s a page on one core, so a call that tried
    the whole document, or six pages, ended with nothing (2026-10-22); it
    now reads what fits and names the rest.
    """

    from kazma_core.agent.graph_helpers import _resolve_tool_timeout

    timeout = await run_cancellable(_resolve_tool_timeout)
    if not timeout or timeout <= 0:
        return 600.0
    return max(20.0, float(timeout) - _OCR_MARGIN_SECONDS)


def ocr_continuation_note(
    *, read: tuple[int, ...], remaining: tuple[int, ...], total_pages: int,
) -> str:
    """What a tool tells the model when OCR did not reach every page.

    Its next step is named: a few more pages per call, or the whole document
    as a background job. Without it the model asked for six pages at once,
    then three, then two, each call timing out (2026-10-22).
    """

    if not remaining:
        return ""
    head = (f"OCR read pages {_page_ranges(read)} of {total_pages} in this call. "
            if read else "OCR read no page in this call. ")
    nxt = list(remaining[:3])
    return (
        f"\n\n{head}Pages {_page_ranges(remaining)} are not read yet "
        f"(OCR takes about 15-45 s a page here). To continue, call ocr_document "
        f"with pages={nxt} -- a few pages a call. To read the whole document, "
        f"use document_import with this file: it runs in the background with no "
        f"per-call time limit, and document_status shows how many pages are done."
    )


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
        policy: DocumentPolicy | None = None,
    ) -> None:
        self._policy = policy or DocumentPolicy(config)
        self.registry = registry or get_parser_registry()
        self.knowledge_adapter = knowledge_adapter
        self.renderer_registry = renderer_registry or get_renderer_registry()
        self.mutation_registry = mutation_registry or get_mutation_registry()
        self.repository = repository
        self.operations = DocumentOperations(
            config=self._policy.initial,
            policy=self._policy,
            validator=self._parse_isolated,
            storage=storage,
            repository=repository,
            renderer_registry=self.renderer_registry,
            mutation_registry=self.mutation_registry,
        )

    @property
    def config(self) -> DocumentConfig:
        return self._policy.current

    @policy_operation
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
        return await run_cancellable(
            self.operations.generate,
            target_format=target_format,
            payload=payload,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            template=template,
            template_version=template_version,
        )

    @policy_operation
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
        keep_source_style: bool = False,
    ) -> DocumentResult[Any]:
        """Convert a caller-approved source through an isolated renderer.

        An HTML source comes out in Kazma's house style unless
        *keep_source_style* (``renderer_worker._house_style_html``)."""

        try:
            source = self._validate_approved_path(path, approved_path)
            sniffed = await run_cancellable(self.inspect_path, source)
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
        return await run_cancellable(
            self._convert_isolated,
            source,
            target_format=target_format,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            approved_assets=assets,
            keep_source_style=keep_source_style,
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
        keep_source_style: bool = False,
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
                keep_source_style=keep_source_style,
            )
        except DocumentParseError as exc:
            return self._operation_error(exc.code, exc.safe_message)
        except Exception as exc:
            return self._operation_error(
                "document_operation_failed",
                f"Document conversion failed safely ({type(exc).__name__})",
            )

    @policy_operation
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

        prepared = await run_cancellable(self._prepare_pdf_request,
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
        return await run_cancellable(
            self.operations.pdf_operation,
            "pdf:merge",
            sources,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
        )

    @policy_operation
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
        prepared = await run_cancellable(self._prepare_pdf_request,
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
        return await run_cancellable(
            self.operations.pdf_operation,
            "pdf:split",
            source,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            parameters={"start_page": start_page, "end_page": end_page},
        )

    @policy_operation
    async def pdf_info(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        tenant_id: str,
        workspace_id: str,
        actor_id: str,
    ) -> DocumentResult[Any]:
        prepared = await run_cancellable(self._prepare_pdf_request,
            (path,), (approved_path,), tenant_id, workspace_id, actor_id
        )
        if isinstance(prepared, DocumentResult):
            return prepared
        sources, scope = prepared
        source = sources[0]
        return await run_cancellable(self.operations.pdf_info, source, scope=scope)

    @policy_operation
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
        prepared = await run_cancellable(self._prepare_pdf_request,
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
        return await run_cancellable(
            self.operations.pdf_operation,
            "pdf:fill-form",
            source,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            parameters={"fields": fields},
        )

    @policy_operation
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

        prepared = await run_cancellable(self._prepare_pdf_request,
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
        return await run_cancellable(
            self.operations.pdf_operation,
            "pdf:redact",
            source,
            scope=scope,
            output_name=output_name,
            export_dir=export_dir,
            parameters={"terms": terms},
        )

    @policy_operation
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
        return await run_cancellable(
            self._parse_isolated,
            source,
            force_ocr=force_ocr,
            ocr_language=ocr_language,
            ocr_pages=ocr_pages,
        )

    @policy_operation
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

    @policy_operation
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

    @policy_operation
    async def parse_transient(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        force_ocr: bool = False,
        ocr_language: str | None = None,
        ocr_pages: tuple[int, ...] | None = None,
        allow_ocr: bool = True,
    ) -> DocumentIR:
        """Compatibility parse after a caller has approved the resolved path."""

        source = self._validate_approved_path(path, approved_path)
        return await run_cancellable(
            self._parse_isolated,
            source,
            force_ocr=force_ocr,
            ocr_language=ocr_language,
            ocr_pages=ocr_pages,
            allow_ocr=allow_ocr,
        )

    @policy_operation
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

    def pages_needing_ocr(self, document: DocumentIR) -> tuple[int, ...]:
        """Pages whose text layer is too thin to read: OCR's own rule.

        Only a PDF or an image has pages OCR can read; an empty page of a
        word-processing file is just empty.
        """

        from .quality import assess_document_quality

        mime = str(document.metadata.get("source_mime", ""))
        if mime != "application/pdf" and not mime.startswith("image/"):
            return ()
        return tuple(
            quality.page_number
            for quality in assess_document_quality(
                document, min_text_chars=self.config.ocr_min_text_chars_per_page)
            if quality.needs_ocr
        )

    @policy_operation
    def text_layer_sync(self, path: str | Path, *, approved_path: str | Path) -> DocumentIR:
        """The document as its text layer has it: no OCR, however long it is.

        A chat attachment's excerpt was read with OCR before the model saw
        the message: a 54-page scan spent the worker's whole five minutes on
        it and gave nothing (2026-10-22).
        """

        source = self._validate_approved_path(path, approved_path)
        return self._parse_isolated(source, allow_ocr=False)

    @policy_operation
    async def read_with_ocr_budget(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        budget_seconds: float,
        page: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        block: str | int | None = None,
        offset: int = 0,
        max_chars: int = 20_000,
        fence: bool = True,
        language: str | None = "auto",
    ) -> tuple[DocumentReadResult, str]:
        """Read a document; OCR its scanned pages in range within the budget.

        The text layer first (fast, whatever the length); then OCR for the
        requested pages that have none, as many as fit in *budget_seconds*.
        Returns the read and a note for the caller naming the pages OCR did
        not reach (empty when there are none). A tool call used to OCR the
        whole document in one run and time out with nothing.
        """

        source = self._validate_approved_path(path, approved_path)
        started = time.monotonic()
        document = await run_cancellable(self._parse_isolated, source, allow_ocr=False)
        note = ""
        if self.config.ocr_enabled and document.pages:
            first = page if page is not None else (page_start or 1)
            last = page if page is not None else (page_end or len(document.pages))
            scanned = tuple(n for n in self.pages_needing_ocr(document) if first <= n <= last)
            if scanned:
                left = budget_seconds - (time.monotonic() - started)
                result = await run_cancellable(
                    self.ocr_in_batches, source, force=True, language=language,
                    pages=scanned, budget_seconds=left,
                )
                document = result.document
                note = ocr_continuation_note(
                    read=result.read, remaining=result.remaining,
                    total_pages=len(document.pages))
        return self.read_ir(
            document, page=page, page_start=page_start, page_end=page_end,
            block=block, offset=offset, max_chars=max_chars, fence=fence,
        ), note

    @policy_operation
    async def ocr_transient_within(
        self,
        path: str | Path,
        *,
        approved_path: str | Path,
        budget_seconds: float,
        language: str | None = "auto",
        pages: tuple[int, ...] | None = None,
        force: bool = True,
    ) -> OcrPass:
        """OCR as many of *pages* as fit in *budget_seconds*, in page order.

        For a tool call, which its caller ends at a fixed time: the rest are
        named in ``remaining`` instead of the call timing out with nothing
        (2026-10-22: every chat OCR of a 54-page scan past page 2 did).
        """

        source = self._validate_approved_path(path, approved_path)
        return await run_cancellable(
            self.ocr_in_batches, source, force=force, language=language,
            pages=pages, budget_seconds=budget_seconds,
        )

    def ocr_in_batches(
        self,
        source: Path,
        *,
        force: bool,
        language: str | None = None,
        pages: tuple[int, ...] | None = None,
        budget_seconds: float | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> OcrPass:
        """Parse *source* with OCR read a few pages per worker run.

        One worker run had to OCR every page within its time limit: a
        54-page scan needs ~30 minutes on one core and never finished
        (2026-10-22). The first run parses the document and OCRs as many
        pages as fit (``max_ocr_pages``); the pages it defers are read by
        further runs, each sized from the seconds a page has taken so far,
        and merged in. A document that needs no OCR, or few pages of it, is
        one run, as before. Without *budget_seconds* every page is read
        (the durable job, whose lease the worker pool renews); with it, the
        pass stops when the next page would not fit and names the rest.
        """

        started = time.monotonic()
        limit = float(self.config.worker_timeout_seconds)
        per_page: float | None = None

        def left() -> float | None:
            if budget_seconds is None:
                return None
            return budget_seconds - (time.monotonic() - started)

        def fits(seconds: float) -> int:
            page = per_page or _FIRST_PAGE_SECONDS
            return max(1, min(_MAX_BATCH_PAGES,
                              int((seconds * _BATCH_SHARE - _RUN_OVERHEAD_SECONDS) // page)))

        remaining_time = left()
        run_limit = limit if remaining_time is None else min(limit, remaining_time)
        first = time.monotonic()
        document = self._parse_isolated(
            source, force_ocr=force, ocr_language=language, ocr_pages=pages,
            timeout_seconds=None if budget_seconds is None else run_limit,
            max_ocr_pages=1 if budget_seconds is not None else fits(run_limit),
        )
        summary = dict(document.metadata.get("ocr") or {})
        read = [int(n) for n in summary.get("completed_pages") or []]
        failed = [int(n) for n in summary.get("failed_pages") or []]
        todo = [int(n) for n in summary.get("deferred_pages") or []]
        total = len(read) + len(failed) + len(todo)
        first_done = len(read) + len(failed)
        if read:
            per_page = (time.monotonic() - first) / len(read)
        if on_progress is not None and total:
            on_progress(first_done, total)
        by_number = {page.page_number: page for page in document.pages}
        warnings = [str(item) for item in document.metadata.get("warnings") or []]
        while todo:
            if check_cancelled is not None:
                check_cancelled()
            remaining_time = left()
            if remaining_time is not None:
                if remaining_time < (per_page or _FIRST_PAGE_SECONDS) + _RUN_OVERHEAD_SECONDS:
                    break
                run_limit = min(limit, remaining_time)
            else:
                run_limit = limit
            batch = tuple(todo[: fits(run_limit)])
            run_started = time.monotonic()
            try:
                run = self._parse_isolated(
                    source, force_ocr=True, ocr_language=language, ocr_pages=batch,
                    timeout_seconds=None if budget_seconds is None else run_limit,
                )
            except DocumentSandboxError as exc:
                if exc.code != "parser_timeout":
                    raise
                if len(batch) > 1:
                    # Slower than measured: one page a run from here.
                    per_page = run_limit
                    continue
                if budget_seconds is not None:
                    break
                raise
            per_page = (time.monotonic() - run_started) / len(batch)
            run_summary = dict(run.metadata.get("ocr") or {})
            run_pages = {page.page_number: page for page in run.pages}
            for number in batch:
                if number in run_pages:
                    by_number[number] = run_pages[number]
            read.extend(int(n) for n in run_summary.get("completed_pages") or [])
            failed.extend(int(n) for n in run_summary.get("failed_pages") or [])
            warnings.extend(str(item) for item in run.metadata.get("warnings") or [])
            todo = todo[len(batch):]
            if on_progress is not None:
                on_progress(len(read) + len(failed), total)
        if len(read) + len(failed) > first_done or todo:
            status = "completed" if read and not failed and not todo else (
                "partial" if read else "failed")
            summary.update({
                "status": status,
                "completed_pages": sorted(read),
                "failed_pages": sorted(failed),
                "selected_pages": sorted(set(read) | set(failed) | set(todo)),
            })
            summary.pop("deferred_pages", None)
            if todo:
                summary["deferred_pages"] = list(todo)
            document = replace(
                document,
                pages=tuple(by_number[page.page_number] for page in document.pages),
                metadata={
                    **dict(document.metadata),
                    "warnings": list(dict.fromkeys(warnings)),
                    "ocr": summary,
                },
            )
        return OcrPass(
            document=document,
            read=tuple(sorted(read)),
            failed=tuple(sorted(failed)),
            remaining=tuple(todo),
            seconds=time.monotonic() - started,
        )

    @policy_operation
    def parse_ingested_blob(
        self,
        source: str | Path,
        *,
        force_ocr: bool = False,
        ocr_language: str | None = None,
        ocr_pages: tuple[int, ...] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        check_cancelled: Callable[[], None] | None = None,
    ) -> DocumentIR:
        """Parse a durable-ingestion staged blob through the isolated worker.

        This is the sync entry the :class:`DocumentIngestionService` stage
        handlers call. The path is a coordinator-materialized copy of an
        immutable CAS ``originals`` blob (never a remote-client path), so no
        approval handshake is required — it is the single parsing path. A
        document that needs OCR is read a few pages per worker run
        (:meth:`ocr_in_batches`), *on_progress* told as pages are read.
        """

        if not (self.config.ocr_enabled or force_ocr):
            return self._parse_isolated(
                Path(source),
                force_ocr=force_ocr,
                ocr_language=ocr_language,
                ocr_pages=ocr_pages,
            )
        return self.ocr_in_batches(
            Path(source), force=force_ocr, language=ocr_language, pages=ocr_pages,
            on_progress=on_progress, check_cancelled=check_cancelled,
        ).document

    @policy_operation
    def ocr_health(self) -> OcrHealth:
        """Return live engine/language/rasterizer/image readiness."""

        return get_ocr_health(self.config.ocr_languages, engine=self.config.ocr_engine)

    @policy_operation
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
            "policy": self._policy.status(),
            "worker_isolation": isolation_report(),
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

    @policy_operation
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
        (``deleted_at`` set). Physical content is reclaimed later by GC. A
        library the document cannot be taken out of stops the delete
        (``document_unindex_failed``), and so does a missing knowledge
        adapter while the store records the document as indexed. The whole
        sequence holds the document's publication lock.
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
        # The tombstone needs write access, so the unindex before it does too
        # (audit 2026-10-26 AUD-061): checked only for read here, a read-only
        # grantee took the document out of every library and was refused
        # only at the tombstone, leaving it unsearchable but not deleted.
        if not self.repository.has_access(
            tenant_id=tenant_id, document_id=record.id, actor_id=actor_id,
            permission="write",
        ):
            return DocumentResult(
                ok=False,
                code="document_access_denied",
                message="Document is unavailable or you lack permission to delete it",
                document_id=document_id,
            )
        # One publication lock from reading the libraries to the tombstone:
        # an index published in between would have stayed searchable after
        # the delete (Q04 defensive check C14, 2026-10-15). The lock is
        # re-entrant, so the unindex calls inside take it again.
        lock = (
            self.knowledge_adapter.publication_lock(tenant_id, record.id)
            if self.knowledge_adapter is not None
            else contextlib.nullcontext()
        )
        with lock:
            return self._unindex_then_tombstone(
                record, tenant_id=tenant_id, actor_id=actor_id, reason=reason
            )

    def _unindex_then_tombstone(
        self, record: Any, *, tenant_id: str, actor_id: str, reason: str
    ) -> DocumentResult[dict[str, Any]]:
        """Take the document out of every library that holds it, then
        tombstone it; a library it cannot leave stops the delete."""
        if self.knowledge_adapter is None:
            # No adapter (Knowledge failed to start) is not "nothing to
            # unindex": the store records the libraries the document is
            # searchable in, and tombstoning it there would leave it
            # searchable until a later start's reconcile.
            unreadable = False
            try:
                indexed = tuple(
                    self.repository.list_indexed_libraries(
                        tenant_id=tenant_id, document_id=record.id
                    )
                )
            except infrastructure_errors():
                logger.warning(
                    "[documents] delete %s: its libraries could not be read",
                    record.id,
                    exc_info=True,
                )
                indexed, unreadable = (), True
            if indexed or unreadable:
                return DocumentResult(
                    ok=False,
                    code="document_unindex_failed",
                    message="The Knowledge Library is unavailable and this document "
                    "may be searchable there; nothing was deleted. Try again once it is back.",
                    data={"libraries": list(indexed)},
                    document_id=record.id,
                )
        libraries: list[str] = []
        if self.knowledge_adapter is not None:
            # A deleted document must not stay searchable: when it cannot be
            # taken out of a library, nothing is deleted and the owner is told
            # which library, so a retry can finish the job. This used to carry
            # on silently and archive a document chat could still quote.
            adapter = self.knowledge_adapter
            try:
                held = adapter.libraries_holding(
                    tenant_id=tenant_id, document_id=record.id
                )
            except Exception:
                logger.warning(
                    "[documents] delete %s: its libraries could not be read",
                    record.id,
                    exc_info=True,
                )
                return DocumentResult(
                    ok=False,
                    code="document_unindex_failed",
                    message="Could not check which libraries hold this document; "
                    "nothing was deleted. Try again.",
                    document_id=record.id,
                )
            stuck: list[str] = []
            for library_id in held:
                try:
                    result = adapter.unindex_document(
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        library_id=library_id,
                        document_id=record.id,
                    )
                except Exception:
                    logger.warning(
                        "[documents] delete %s: removal from library %s failed",
                        record.id,
                        library_id,
                        exc_info=True,
                    )
                    stuck.append(library_id)
                    continue
                if result.ok:
                    libraries.append(library_id)
                else:
                    logger.warning(
                        "[documents] delete %s: removal from library %s refused (%s)",
                        record.id,
                        library_id,
                        result.code,
                    )
                    stuck.append(library_id)
            if stuck:
                return DocumentResult(
                    ok=False,
                    code="document_unindex_failed",
                    message="Could not remove this document from "
                    f"{', '.join(stuck)}; nothing was deleted. Try again.",
                    data={"libraries": stuck},
                    document_id=record.id,
                )
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

    @policy_operation
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
        allow_ocr: bool = True,
    ) -> DocumentReadResult:
        document = await self.parse_transient(
            path, approved_path=approved_path, allow_ocr=allow_ocr)
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

    @policy_operation
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
        allow_ocr: bool = True,
    ) -> DocumentReadResult:
        """Synchronous adapter that still executes parser libraries out of process."""

        source = self._validate_approved_path(path, approved_path)
        document = self._parse_isolated(source, allow_ocr=allow_ocr)
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
                "logical_document": f"Section {item.page_number}",
                "segment": f"Segment {item.page_number} (source page unknown)",
            }.get(kind, f"Page {item.page_number}")
            if document.metadata.get("salvaged"):
                label = f"Segment {item.page_number} (source page unknown)"
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
            sniffed = self.inspect_path(source)
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

    @policy_operation
    def _parse_isolated(
        self,
        source: Path,
        *,
        force_ocr: bool = False,
        ocr_language: str | None = None,
        ocr_pages: tuple[int, ...] | None = None,
        allow_ocr: bool = True,
        timeout_seconds: float | None = None,
        max_ocr_pages: int | None = None,
    ) -> DocumentIR:
        response, source_sha = self._run_parser_worker(
            source, operation="parse", force_ocr=force_ocr,
            ocr_language=ocr_language, ocr_pages=ocr_pages,
            allow_ocr=allow_ocr, timeout_seconds=timeout_seconds,
            max_ocr_pages=max_ocr_pages,
        )
        document = self._validate_response(response, source_sha)
        from .extract_salvage import maybe_salvage_extract

        return maybe_salvage_extract(source, document, config=self.config, allow_local=False)

    @policy_operation
    def inspect_path(self, path: str | Path) -> SniffResult:
        """Validate untrusted structure only inside the bounded parser worker."""
        source = self._validate_path(path)
        response, source_sha = self._run_parser_worker(source, operation="inspect")
        self._validate_response_status(response)
        if set(response) != {"protocol_version", "ok", "code", "message", "source_sha256", "sniff"}:
            raise DocumentSandboxError("Document inspection response schema mismatch")
        if response["source_sha256"] != source_sha:
            raise DocumentSandboxError("Document inspection source checksum mismatch")
        sniff = response["sniff"]
        if not isinstance(sniff, dict) or set(sniff) != {"mime_type", "extension", "container"}:
            raise DocumentSandboxError("Document inspection result is invalid")
        if not isinstance(sniff["mime_type"], str) or sniff["extension"] != source.suffix.lower():
            raise DocumentSandboxError("Document inspection type is invalid")
        if sniff["container"] not in {None, "ooxml", "ole"}:
            raise DocumentSandboxError("Document inspection container is invalid")
        result = SniffResult(**sniff)
        self.registry.resolve(mime_type=result.mime_type, extension=result.extension)
        return result

    def _run_parser_worker(
        self, source: Path, *, operation: str, force_ocr: bool = False,
        ocr_language: str | None = None, ocr_pages: tuple[int, ...] | None = None,
        allow_ocr: bool = True, timeout_seconds: float | None = None,
        max_ocr_pages: int | None = None,
    ) -> tuple[Any, str]:
        # A run may be given less time than the policy's limit (an OCR batch
        # sized to its caller's budget); the worker reads the same figure
        # for its own OCR deadline, so both end together.
        config = self.config
        if timeout_seconds is not None:
            config = replace(config, worker_timeout_seconds=max(
                1, min(int(timeout_seconds), int(config.worker_timeout_seconds))))
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
            "operation": operation,
            "config": {
                key: str(value) if isinstance(value, Path) else value
                for key, value in asdict(config).items()
            },
            "ocr": {
                "force": force_ocr,
                "disable": not allow_ocr,
                "max_pages": max_ocr_pages,
                "language": ocr_language,
                "pages": list(ocr_pages) if ocr_pages is not None else None,
                # The neural engine's models: the worker's environment is
                # scrubbed, so it is told where they are (a container run
                # reads the image's own copy instead).
                "models": str(model_directory()),
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
            timeout_seconds=config.worker_timeout_seconds,
            stdout_limit_bytes=4_096,
            stderr_limit_bytes=65_536,
            memory_limit_bytes=self.config.worker_memory_mb * 1024 * 1024,
            cpu_limit_seconds=config.worker_timeout_seconds,
                cpu_cores=1 if os.name == "nt" else None,
                process_limit=32 if os.name == "nt" else None,
            env={
                # OpenBLAS (numpy / scipy) and OpenMP allocate per-thread
                # buffers at import time.  With the default thread count on
                # many-core machines these can exceed the Job Object memory
                # limit before the process even reaches the try/except in
                # the parser worker, causing an uncatchable exit(1).
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "KAZMA_DOCLING": os.getenv("KAZMA_DOCLING", "1"),
            },
        )
        try:
            if worker_isolation_mode() == CONTAINER:
                # Every untrusted parse in this profile runs in an attested
                # container behind the broker: no network, no shared storage.
                container_request, staged = parser_container_request(request, source_abs)
                result = run_container_worker(
                    worker="parser",
                    request=container_request,
                    inputs=staged,
                    run_dir=run_dir,
                    timeout_seconds=config.worker_timeout_seconds,
                    max_output_bytes=self.config.worker_result_max_bytes + 1024 * 1024,
                )
            else:
                refusal = process_profile_refusal()
                if refusal is not None:
                    raise refusal
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
                raise DocumentSandboxError(
                    "Document parser produced no result", code="parser_result_missing"
                )
            if result_path.stat().st_size > self.config.worker_result_max_bytes:
                raise DocumentSandboxError(
                    "Document parser result exceeds the configured limit",
                    code="parser_output_limit",
                )
            response = json.loads(result_path.read_text(encoding="utf-8"))
            return response, source_sha
        except json.JSONDecodeError as exc:
            raise DocumentSandboxError("Document parser returned invalid JSON") from exc
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    @staticmethod
    def _validate_response_status(response: Any) -> None:
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

    @staticmethod
    def _validate_response(response: Any, source_sha: str) -> DocumentIR:
        DocumentService._validate_response_status(response)
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
