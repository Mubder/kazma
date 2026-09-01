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
import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from kazma_core.documents.errors import DocumentParseError
from kazma_core.documents.registry import get_parser_registry
from kazma_core.documents.service import DocumentService

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
ATTACHMENT_DIR = Path("kazma-data/attachments")

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


def _fetch_attachment_url(url: str) -> bytes:
    """Fetch a remote attachment after SSRF-validating every redirect target.

    Routes through the shared scraping client factory (proxy provider +
    rotating UA pool when configured) so platform file downloads follow the
    same egress stack as the rest of Kazma — previously a bare httpx.Client
    was the one web-egress path outside it.
    """
    from kazma_core.security.ssrf import validate_url

    try:
        from kazma_core.proxy.client import get_scraping_client_sync
    except Exception:
        # Proxy module unavailable — degrade to a plain client (SSRF loop
        # below still applies).
        import httpx

        _client_factory = lambda: httpx.Client(timeout=30.0, follow_redirects=False)  # noqa: E731
    else:
        def _client_factory():
            return get_scraping_client_sync(
                follow_redirects=False, timeout=30.0, rotate_ua=True
            )

    current_url = url
    with _client_factory() as client:
        for _ in range(_MAX_ATTACHMENT_REDIRECTS + 1):
            validate_url(current_url, block_unresolved=True)
            with client.stream("GET", current_url) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("attachment redirect has no location")
                    current_url = urljoin(current_url, location)
                    continue

                response.raise_for_status()
                declared = response.headers.get("content-length")
                if declared:
                    try:
                        if int(declared) > MAX_ATTACHMENT_BYTES:
                            raise ValueError("attachment exceeds remote fetch size limit")
                    except ValueError as exc:
                        if "exceeds" in str(exc):
                            raise

                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_ATTACHMENT_BYTES:
                        raise ValueError("attachment exceeds remote fetch size limit")
                    chunks.append(chunk)
                return b"".join(chunks)

    raise ValueError("attachment URL exceeded redirect limit")


def _persist_attachment(attachment: Attachment) -> str:
    """Persist attachment bytes to disk and return the absolute file path.

    Assumes ``attachment.data`` is populated. The filename is uniqueified
    to avoid collisions across concurrent turns.
    """
    ATTACHMENT_DIR.mkdir(parents=True, exist_ok=True)
    base = attachment.filename or f"{attachment.kind}_{uuid.uuid4().hex[:8]}"
    # Disambiguate while preserving extension.
    dest = ATTACHMENT_DIR / f"{Path(base).stem}_{uuid.uuid4().hex[:6]}{Path(base).suffix}"
    dest.write_bytes(attachment.data or b"")
    return str(dest.resolve())


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
        result = DocumentService().read_transient_sync(
            p,
            approved_path=p,
            max_chars=max_chars,
            fence=False,
        )
        return result.as_tool_output()
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
                f"[Attachment limit reached: only {MAX_ATTACHMENT_COUNT} files are processed per turn]"
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
                    f"[Attached: {att.filename or att.url} — fetch failed]"
                )
                continue

        if data is not None:
            if total_attachment_bytes + len(data) > MAX_TOTAL_ATTACHMENT_BYTES:
                logger.warning(
                    "[attachments] aggregate attachment byte limit exceeded at %s",
                    att.filename or att.url,
                )
                text_parts.append(
                    f"[Attached: {att.filename or att.url or att.kind} — skipped: "
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
                                f"[Attached: {att.filename or path} ({att.mime}) "
                                f"— parsed contents:\n{fenced_excerpt}]"
                            )
                            text_parts.append(
                                f"[Full document at: {path} — use read_document for complete content]"
                            )
                        else:
                            text_parts.append(
                                f"[Attached: {att.filename or path} ({att.mime}) "
                                f"— use read_document to open: {path}]"
                            )
                    else:
                        text_parts.append(
                            f"[Attached: {att.filename or path} ({att.mime}) "
                            f"— use file_read to open: {path}]"
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[attachments] persist failed: %s", exc)
                    text_parts.append(
                        f"[Attached: {att.filename} — save failed: {exc}]"
                    )
            else:
                # No data and no fetchable URL — record what we know.
                label = att.filename or att.url or att.kind
                text_parts.append(f"[Attached: {label} ({att.mime}) — unavailable]")

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
