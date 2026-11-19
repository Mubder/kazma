"""Attachment → LLM message-content builder.

Shared by the gateway path (``agent_handler/store.py``) and the Web SSE
path (``kazma_ui/sse_chat.py``) so both transports produce identical
OpenAI-compatible multimodal content from an :class:`Attachment` list.

Policy (see roadmap Phase 1.2):

* **Images** (PNG/JPEG/WEBP/GIF, ≤ ``MAX_INLINE_BYTES``) are inlined as
  base64 ``data:`` URIs into the ``image_url`` content block so the LLM
  sees them immediately. This mirrors the proven pattern in
  ``kazma_core/tools/vision_analyze.py:_build_vision_messages``.
* **Documents / large media / over the inline cap** are persisted to
  ``kazma-data/attachments/`` and represented in the prompt as a text
  stub pointing the agent at the file via ``file_read``. This keeps the
  prompt size bounded while still making the bytes reachable.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kazma_core.agent.attached_text import file_label
from kazma_core.documents.errors import DocumentParseError
from kazma_core.documents.registry import get_parser_registry
from kazma_core.documents.service import DocumentService
from kazma_core.english_count import count_noun

if TYPE_CHECKING:
    from kazma_gateway.gateway import Attachment

logger = logging.getLogger(__name__)

# Inlining images larger than this as base64 bloats the prompt. The vision
# tool uses a 20 MB cap; we are more conservative for chat to avoid context
# blow-up. Larger images are persisted and referenced as files.
MAX_INLINE_BYTES = 8 * 1024 * 1024  # 8 MB

# MIME types safe to inline as vision input to OpenAI-compatible providers.
# Matches vision_analyze.py's accepted set.
_INLINE_IMAGE_MIMES = frozenset(
    {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}
)

# Where over-cap / non-image attachments are persisted so the agent can
# open them with file_read. Relative to CWD, matching tools/image_gen.py.
from kazma_core.paths import data_dir as _data_dir

#: Resolved via paths so KAZMA_DATA_DIR is honoured.
ATTACHMENT_DIR = _data_dir() / "attachments"

# Model-emitted paths in chat text that we may auto-attach (telegram send).
# Untrusted: the regex is not a permission check. Containment is.
_AUTO_ATTACH_RE = re.compile(
    r"(?:kazma-data/documents/|reports/|data/)[^\s\"'\(\)\[\]`]+\.(?:pdf|docx|html)",
    re.IGNORECASE,
)


def auto_attach_roots() -> tuple[Path, Path]:
    """Allowlisted directories for telegram auto-attach (audit H-4)."""
    from kazma_core.paths import data_dir

    d = data_dir()
    return (d / "documents", d / "exports")


def resolve_auto_attach_path(
    file_path_str: str,
    *,
    roots: tuple[Path, ...] | list[Path] | None = None,
) -> Path | None:
    """Return a contained file path or None.

    Rejects ``..`` before resolve, then requires ``is_relative_to`` an
    allowlisted root after ``resolve()`` (symlink-aware).
    """
    raw = str(file_path_str or "").strip()
    if not raw:
        return None
    if ".." in raw.replace("\\", "/"):
        return None
    try:
        fpath = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not fpath.is_file():
        return None
    allow = tuple(Path(r).resolve() for r in (roots or auto_attach_roots()))
    for root in allow:
        try:
            if fpath.is_relative_to(root):
                return fpath
        except (OSError, ValueError):
            continue
    return None


def find_auto_attach_paths(
    text: str,
    *,
    roots: tuple[Path, ...] | list[Path] | None = None,
) -> list[Path]:
    """Extract contained auto-attach files mentioned in *text*."""
    found: list[Path] = []
    seen: set[str] = set()
    for match in _AUTO_ATTACH_RE.findall(text or ""):
        fpath = resolve_auto_attach_path(match, roots=roots)
        if fpath is None:
            continue
        key = str(fpath)
        if key in seen:
            continue
        seen.add(key)
        found.append(fpath)
    return found


_MAX_ATTACHMENT_REDIRECTS = 3
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENT_COUNT = 10
MAX_TOTAL_ATTACHMENT_BYTES = 50 * 1024 * 1024


#: One deadline for the whole download: name lookups, every redirect, the body.
_ATTACHMENT_FETCH_TIMEOUT_S = 30.0


async def _fetch_attachment(url: str) -> bytes:
    """Download a remote attachment on the pinned path (AGENTS.md §32).

    The URL is the sender's (a Discord attachment, an embed image -- which can
    be the ``og:image`` of any page the sender linked), so it goes through
    ``web_acquire.http.fetch_public``: every hop of the redirect chain checked
    public, the connection pinned to the address that was checked, the peer
    checked before the body is read, the body bounded. Until 2026-11-15 a
    plain client resolved the name again after the check, so a name answering
    a public address and then an internal one made Kazma read the internal
    one into the chat.
    """
    from kazma_core.web_acquire.http import fetch_public

    got = await fetch_public(
        url,
        max_bytes=MAX_ATTACHMENT_BYTES,
        timeout=_ATTACHMENT_FETCH_TIMEOUT_S,
        max_redirects=_MAX_ATTACHMENT_REDIRECTS,
    )
    status = got.response.status_code
    if status >= 400:
        raise ValueError(f"attachment download answered HTTP {status}")
    try:
        declared = int(got.response.headers.get("content-length", "") or 0)
    except ValueError:
        declared = 0
    if got.truncated or declared > MAX_ATTACHMENT_BYTES:
        raise ValueError("attachment exceeds remote fetch size limit")
    return got.response.content


def _fetch_attachment_url(url: str) -> bytes:
    """Blocking :func:`_fetch_attachment`, for :func:`build_user_content`.

    ``build_user_content`` runs in a worker thread (both callers hand it to
    ``asyncio.to_thread``), where no event loop runs; the download runs on a
    loop of its own there. Called where a loop runs, it refuses rather than
    block that loop.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_fetch_attachment(url))
    raise RuntimeError(
        "attachment download blocks: run build_user_content in a worker thread"
    )


def _persist_attachment(attachment: Attachment) -> str:
    """Persist attachment bytes to disk and return the absolute file path.

    Assumes ``attachment.data`` is populated. The name keeps the sender's
    file name (the model reads it) and adds the content's hash (2026-11,
    FA-146): the same file sent again is the file already kept, never
    another copy; two different files of one name stay two files.
    """
    from kazma_core.atomic_files import atomic_write_bytes

    from kazma_core.spaces.files import space_folder

    # The bound space's own folder (Spaces S1-19); ATTACHMENT_DIR for the
    # owner's space and for an install that does not enforce Spaces.
    folder = space_folder("attachments", install=ATTACHMENT_DIR)
    data = attachment.data or b""
    digest = hashlib.sha256(data).hexdigest()
    # The name as a stub shows it (no bracket, line break or control
    # character): the path is read back from the stub.
    base = file_label(attachment.filename) if attachment.filename else f"{attachment.kind}_{digest[:8]}"
    dest = folder / f"{Path(base).stem}_{digest[:12]}{Path(base).suffix}"
    if not dest.is_file():
        atomic_write_bytes(dest, data)
    return str(dest.resolve())


def _scanned_note(total: int, scanned: tuple[int, ...]) -> str:
    """What the model is told about pages that have no text layer."""

    from kazma_core.english_count import count_noun

    which = "This is a scanned document" if len(scanned) == total else "Some pages are scanned"
    pages = count_noun(total, "page")
    count = f"{len(scanned)} of {pages}" if len(scanned) != total else f"all {pages}"
    return (
        f"{which}: {count} have no text layer, so their text needs OCR "
        f"(about 15-45 s a page). ocr_document reads a few pages per call; "
        f"document_import reads the whole file in the background, and "
        f"document_status shows how many pages are done."
    )


def _try_parse_attachment(path: str, *, max_chars: int = 2000) -> str:
    """Best-effort synchronous parse of a document attachment for prompt injection.

    Called by build_user_content when a document (PDF/DOCX/XLSX/PPTX/CSV) is
    uploaded — extracts the first ``max_chars`` of text so the agent sees the
    content immediately in the prompt. Returns an error string on failure.

    This is synchronous (not async) because build_user_content is sync and
    runs in the graph-builder thread before the LLM call.
    """
    try:
        p = Path(path).expanduser().resolve(strict=True)
        # The text layer only. A scanned PDF was OCR'd here before the model
        # saw the message: a 54-page scan spent the worker's whole five
        # minutes on it and gave nothing (2026-10-22). The model is told what
        # the file is and which tools read it.
        service = DocumentService()
        document = service.text_layer_sync(p, approved_path=p)
        scanned = service.pages_needing_ocr(document)
        total = len(document.pages)
        if scanned and len(scanned) == total:
            return _scanned_note(total, scanned)
        excerpt = service.read_ir(document, max_chars=max_chars, fence=False).as_tool_output()
        if scanned:
            excerpt += "\n" + _scanned_note(total, scanned)
        return excerpt
    except DocumentParseError as exc:
        return f"Error: {exc.safe_message}"
    except Exception as exc:
        logger.debug(
            "[attachments] document service failed: %s", type(exc).__name__
        )
        return f"Error: document parser failed ({type(exc).__name__})"


def _resolve_active_model_vision_capable() -> bool:
    """Best-effort: is the active model vision-capable?

    Returns ``True`` (fail-open, inline images as before) on any error so
    that an inability to read the registry never silently drops images.
    """
    try:
        from kazma_core.model_registry import get_model_registry
        from kazma_core.vision_capability import active_model_is_vision_capable

        return active_model_is_vision_capable(get_model_registry())
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.debug(
            "[attachments] could not resolve active model vision capability: %s",
            exc,
        )
        return True  # fail-open


def _stub_name(att: Attachment, path: str) -> str:
    """The name a stub shows: the sender's, cleaned (``file_label``: a name
    holding "]" ended the stub early and its tail read as the user's words),
    else the kept file's path."""
    return file_label(att.filename) if att.filename else path


def build_user_content(
    text: str,
    attachments: list[Attachment] | None,
    vision_capable: bool | None = None,
) -> str | list[dict[str, Any]]:
    """Build the OpenAI ``content`` for a user turn.

    Returns a plain string when there are no inlinable attachments (the
    fast path every plain-text message takes), or a multimodal
    ``content`` list (text + image_url blocks) when images are present.

    Non-inlinable attachments (documents, audio, over-cap images) are
    persisted and folded into the text portion as ``[Attached: <path>]``
    stubs so the agent can fetch them with ``file_read``.

    ``vision_capable`` controls whether inlinable images are actually
    inlined as ``image_url`` blocks. When the active model is text-only
    (e.g. DeepSeek) passing ``vision_capable=False`` forces images down
    the persist-and-stub path so the provider never receives an
    ``image_url`` part it would reject with a 400. When ``None`` (default),
    the active model is looked up best-effort via
    :func:`kazma_core.vision_capability.active_model_is_vision_capable`;
    on any error, behavior defaults to inlining (fail-open).
    """
    if not attachments:
        return text

    # Resolve the active model's vision capability if the caller didn't.
    if vision_capable is None:
        vision_capable = _resolve_active_model_vision_capable()

    blocks: list[dict[str, Any]] = []
    text_parts: list[str] = [text] if text else []
    saw_image = False

    total_attachment_bytes = 0
    for index, att in enumerate(attachments):
        if index >= MAX_ATTACHMENT_COUNT:
            logger.warning(
                "[attachments] dropped attachments beyond count limit (%d)",
                MAX_ATTACHMENT_COUNT,
            )
            text_parts.append(
                f"[Attachment limit reached: Kazma reads at most {count_noun(MAX_ATTACHMENT_COUNT, 'file')} per turn]"
            )
            break
        data = att.data
        # Fetch on demand if only a URL was provided.
        if data is None and att.url:
            try:
                data = _fetch_attachment_url(att.url)
            except Exception as exc:  # noqa: BLE001 — network is best-effort
                logger.warning(
                    "[attachments] failed to fetch %s: %s", att.url, exc
                )
                text_parts.append(
                    f"[Attached: {file_label(att.filename or att.url)} — fetch failed]"
                )
                continue

        if data is not None:
            if total_attachment_bytes + len(data) > MAX_TOTAL_ATTACHMENT_BYTES:
                logger.warning(
                    "[attachments] aggregate attachment byte limit exceeded at %s",
                    att.filename or att.url,
                )
                text_parts.append(
                    f"[Attached: {file_label(att.filename or att.url or att.kind)} — skipped: "
                    "aggregate attachment size limit exceeded]"
                )
                continue
            total_attachment_bytes += len(data)

        is_inlinable_image = (
            att.kind == "image"
            and att.mime in _INLINE_IMAGE_MIMES
            and data is not None
            and len(data) <= MAX_INLINE_BYTES
        )
        # A text-only model cannot consume an image_url content part —
        # force the image down the persist-and-stub path (file_read) so
        # the provider receives plain text and never 400s on `image_url`.
        if is_inlinable_image and not vision_capable:
            is_inlinable_image = False
            logger.info(
                "[attachments] active model is text-only — downgrading "
                "image %s to file reference (no image_url)",
                att.filename or att.url,
            )

        if is_inlinable_image and data is not None:
            data_uri = (
                f"data:{att.mime};base64,{base64.b64encode(data).decode('ascii')}"
            )
            blocks.append(
                {"type": "image_url", "image_url": {"url": data_uri}}
            )
            saw_image = True
        else:
            # Persist and reference. Non-image or oversized attachments
            # stay out of the prompt payload themselves.
            if data is not None:
                try:
                    path = _persist_attachment(att)
                    # ── Auto-parse document attachments ──────────────
                    # For document types (PDF, DOCX, XLSX, PPTX, CSV), extract
                    # text immediately so the agent sees the content in the
                    # prompt — not just a "use file_read" stub that would
                    # fail on binary formats. The file is also persisted so
                    # the agent can use read_document for the full content.
                    from pathlib import Path as _Path

                    _suffix = _Path(path).suffix.lower()
                    capability = get_parser_registry().capability_for_extension(_suffix)
                    if capability is not None and capability.available:
                        excerpt = _try_parse_attachment(path, max_chars=2000)
                        if excerpt and not excerpt.startswith("Error"):
                            from kazma_core.safety.prompt_fence import (
                                format_untrusted_block,
                            )

                            fenced_excerpt = format_untrusted_block(
                                excerpt,
                                source="document_attachment",
                            )
                            text_parts.append(
                                f"[Attached: {_stub_name(att, path)} ({file_label(att.mime)}) "
                                f"— parsed contents:\n{fenced_excerpt}]"
                            )
                            text_parts.append(
                                f"[Full document at: {path} — use read_document for complete content]"
                            )
                        else:
                            text_parts.append(
                                f"[Attached: {_stub_name(att, path)} ({file_label(att.mime)}) "
                                f"— use read_document to open: {path}]"
                            )
                    else:
                        text_parts.append(
                            f"[Attached: {_stub_name(att, path)} ({file_label(att.mime)}) "
                            f"— use file_read to open: {path}]"
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[attachments] persist failed: %s", exc)
                    text_parts.append(
                        f"[Attached: {file_label(att.filename)} — save failed: {file_label(exc)}]"
                    )
            else:
                # No data and no fetchable URL — record what we know.
                label = file_label(att.filename or att.url or att.kind)
                text_parts.append(f"[Attached: {label} ({file_label(att.mime)}) — unavailable]")

    if not saw_image:
        # No vision input — keep content a plain string for token efficiency
        # and broad provider compatibility.
        return "\n".join(text_parts)

    # Multimodal: text block first (the user's caption/intent), then images.
    combined_text = "\n".join(text_parts)
    content: list[dict[str, Any]] = []
    if combined_text.strip():
        content.append({"type": "text", "text": combined_text})
    content.extend(blocks)
    return content
