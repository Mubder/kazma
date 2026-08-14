"""Document generation pipeline — 5 deterministic steps.

Replaces the free-form agent loop for document tasks (PDF/DOCX/XLSX
reproduction, creation, conversion). The audit showed the model burning
100 iterations writing Python scripts instead of calling generate_pdf.

Steps:
  1. READ: read the source (file/document/attachment)
  2. EXTRACT: one LLM call to structure content into markdown
  3. WRITE: file_write the markdown to disk
  4. GENERATE: generate_pdf/generate_docx with markdown_path
  5. DELIVER: send_file if a delivery target was detected
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kazma_core.agent.intent_router import IntentCategory, TaskIntent
from kazma_core.agent.pipeline_registry import (
    Pipeline,
    PipelineBudget,
    get_registry,
)

__all__ = ["document_pipeline", "register"]

logger = logging.getLogger(__name__)


async def document_pipeline(intent: TaskIntent, state: dict[str, Any], **ctx: Any) -> str:
    """Execute the document generation workflow.

    Args:
        intent: The classified TaskIntent (category=document).
        state: The supervisor state (messages, thread_id, etc.).
        **ctx: Additional context (llm, tool_executor).

    Returns:
        Human-readable result string (success message or error).
    """
    params = intent.parameters
    source_path = params.get("source_path") or params.get("source_hint") or ""
    output_format = params.get("format", "pdf").lower()
    deliver_to = params.get("deliver_to", "")
    messages = state.get("messages", [])

    llm = ctx.get("llm")
    steps_log: list[str] = []

    # ── Step 1: READ the source ─────────────────────────────────────
    source_content = ""

    # If no explicit source path, look for one in the conversation history.
    # "reproduce this PDF" in an ongoing session refers to a file that was
    # uploaded or mentioned earlier — search recent messages for file paths
    # and attachment references.
    if not source_path and messages:
        source_path = _find_source_in_history(messages, state)

    if source_path:
        try:
            import asyncio as _aio

            p = Path(source_path)
            if not p.is_absolute():
                # Try workspace-relative, then attachments dir, then as-is
                from kazma_core.workspace.binding import resolve_active_root

                root = await _aio.to_thread(resolve_active_root)
                candidates = [
                    root / p,
                    Path("kazma-data/attachments") / p.name,
                    Path("kazma-data/documents") / p.name,
                    p,
                ]
                p = next((c for c in candidates if c.is_file()), candidates[0])
            if await _aio.to_thread(p.is_file):
                if p.suffix.lower() == ".pdf":
                    source_content = await _aio.to_thread(_read_pdf, p)
                else:
                    source_content = await _aio.to_thread(
                        p.read_text, encoding="utf-8", errors="replace"
                    )
                steps_log.append(f"✓ Read source: {p.name} ({len(source_content)} chars)")
            else:
                steps_log.append(f"⚠ Source not found: {source_path} (resolved: {p})")
        except Exception as exc:
            steps_log.append(f"⚠ Source read failed: {exc}")

    if not source_content:
        # Do NOT fall back to conversation text as document content — that
        # produces a garbage PDF containing the user's instruction text.
        # Instead, return an actionable error.
        _att_dir = Path("kazma-data/attachments")
        _pdfs = sorted(
            [f.name for f in _att_dir.glob("*.pdf")],
            key=lambda f: (_att_dir / f).stat().st_mtime,
            reverse=True,
        )[:3]
        _hint = f" Available PDFs: {', '.join(_pdfs)}" if _pdfs else ""
        return _format_result(
            "Error", steps_log,
            f"No source document found. Please attach a file or specify a path"
            f" (e.g., 'reproduce kazma-data/attachments/Calender.pdf').{_hint}",
        )

    # ── Step 2: EXTRACT/STRUCTURE ───────────────────────────────────
    # LLM structuring is EXPENSIVE for large content: asking the model to
    # "preserve all" 12K chars means re-emitting 12K chars in its output,
    # which hits max_tokens (8192) and gets truncated. For structured
    # content (calendars, tables, reports), _basic_structure() is
    # sufficient and instant.
    #
    # LLM extraction is reserved for UNSTRUCTURED PROSE only, with a
    # capped input size to keep the output manageable.
    structured_md = source_content
    _needs_llm = len(source_content) > 500 and _looks_like_prose(source_content)

    if llm is not None and _needs_llm:
        try:
            import asyncio as _aio

            # Cap input to 6K chars — the model outputs a STRUCTURED
            # SUMMARY, not the full content (which would hit max_tokens)
            extract_prompt = (
                "Organize this content into markdown with headings (# ## ###), "
                "bullet points, and tables. You may condense repetitive patterns "
                "into templates — do NOT try to reproduce every word. Output "
                "should be under 3000 words. Keep the original language.\n\n"
                f"{source_content[:6000]}"
            )
            resp = await _aio.wait_for(
                llm.chat(
                    [{"role": "user", "content": extract_prompt}],
                    tools=None,
                    max_tokens=6000,
                ),
                timeout=180.0,
            )
            content = (getattr(resp, "content", "") or "").strip()
            if content and not content.startswith("Error"):
                structured_md = content
                steps_log.append(f"✓ LLM structured content ({len(structured_md)} chars)")
            else:
                steps_log.append("⚠ LLM returned empty — applying basic structuring")
                structured_md = _basic_structure(source_content)
        except TimeoutError:
            steps_log.append("⚠ LLM extraction timed out — applying basic structuring")
            structured_md = _basic_structure(source_content)
        except Exception as exc:
            _exc_name = type(exc).__name__
            _exc_msg = str(exc)[:100] or "(no message)"
            steps_log.append(f"⚠ LLM extraction error ({_exc_name}: {_exc_msg}) — basic structuring")
            structured_md = _basic_structure(source_content)
    else:
        # Default path: deterministic structuring (instant, no LLM cost,
        # handles dates/titles/lists/Arabic — perfect for calendars)
        structured_md = _basic_structure(source_content)
        if _needs_llm:
            steps_log.append("✓ Basic structuring applied (LLM path skipped)")
        else:
            steps_log.append(f"✓ Basic structuring applied ({len(structured_md)} chars)")

    # ── Step 3: WRITE the markdown file ─────────────────────────────
    md_filename = f"document_output_{int(__import__('time').time())}.md"
    try:
        import asyncio as _aio

        from kazma_core.tools.file_write import file_write

        write_result = await _aio.wait_for(
            file_write(md_filename, structured_md), timeout=30.0
        )
        if "Error" in write_result:
            return _format_result("Error", steps_log, f"file_write failed: {write_result}")
        steps_log.append(f"✓ Wrote markdown: {md_filename}")
    except Exception as exc:
        return _format_result("Error", steps_log, f"file_write error: {exc}")

    # ── Step 4: GENERATE the document ───────────────────────────────
    title = params.get("title") or _derive_title(source_content, output_format)
    try:
        import asyncio as _aio

        if output_format in ("docx", "word"):
            from kazma_skills.native.document_generator.tools import generate_docx

            gen_result = await _aio.wait_for(
                generate_docx(title, markdown_path=md_filename), timeout=120.0
            )
        elif output_format in ("xlsx", "excel", "spreadsheet"):
            from kazma_skills.native.document_generator.tools import generate_xlsx

            gen_result = await _aio.wait_for(
                generate_xlsx(
                    [{"name": "Sheet1", "rows": [["Content"], [structured_md[:500]]]}],
                    filename=title,
                ),
                timeout=120.0,
            )
        else:
            from kazma_skills.native.document_generator.tools import generate_pdf

            gen_result = await _aio.wait_for(
                generate_pdf(title, markdown_path=md_filename), timeout=120.0
            )

        if "Error" in gen_result:
            return _format_result("Error", steps_log, f"Generation failed: {gen_result}")
        steps_log.append(f"✓ Generated {output_format.upper()}: {gen_result[:200]}")
    except ImportError:
        return _format_result(
            "Error", steps_log,
            "Document generator not available. Install: pip install -e kazma-skills",
        )
    except Exception as exc:
        return _format_result("Error", steps_log, f"Generation error: {exc}")

    # ── Step 5: DELIVER (optional) ──────────────────────────────────
    if deliver_to:
        try:
            import asyncio as _aio

            # Extract the output path from the generation result
            output_path = ""
            if "Saved to:" in gen_result:
                output_path = gen_result.split("Saved to:")[-1].strip()

            if output_path:
                from kazma_core.tools.send_message import (
                    get_current_delivery_target,
                    send_file_message,
                )

                target = get_current_delivery_target()
                if not target and deliver_to:
                    # No active session target (e.g., chatting from Web UI)
                    # — resolve the configured chat ID for the platform
                    # (same fallback as the send_file tool, with the
                    # string-as-list normalization fix)
                    target = f"{deliver_to}:{_resolve_platform_chat_id(deliver_to)}"

                if target and not target.endswith(":"):
                    send_result = await _aio.wait_for(
                        send_file_message(
                            target_id=target,
                            text=f"📎 {title}",
                            file_path=output_path,
                        ),
                        timeout=60.0,
                    )
                    steps_log.append(f"✓ Delivered via {deliver_to}: {send_result[:100]}")
                elif target.endswith(":"):
                    steps_log.append(
                        f"⚠ Could not resolve {deliver_to} chat ID — "
                        "file saved locally. Set connectors.{deliver_to}.swarm_chat_id "
                        "in Settings, or ask from the {deliver_to} chat directly."
                    )
                else:
                    steps_log.append("⚠ No delivery target — file saved locally")
            else:
                steps_log.append("⚠ Could not extract output path for delivery")
        except Exception as exc:
            steps_log.append(f"⚠ Delivery failed: {exc} — file saved locally")
    else:
        steps_log.append("✓ File saved (no delivery requested)")

    return _format_result("Success", steps_log, gen_result[:500])


_DATE_LINE_RE = None  # compiled lazily


import re as _re

_PROSE_RE = None


def _looks_like_prose(text: str) -> bool:
    """True when the content is unstructured prose (needs LLM structuring).

    False for already-structured content: calendars, tables, lists with
    dates, form-like data. These are handled perfectly by _basic_structure().
    """
    global _PROSE_RE
    if _PROSE_RE is None:
        _PROSE_RE = _re.compile(
            r"^(?:"
            r"(?:Aug|Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun|Jul)\s+\d{1,2}"
            r"|\d{1,2}[/-]\d{1,2}"
            r"|- |• |\* |\d+\.\s"
            r"|(?:السبت|الأحد|الاثنين|الثلاثاء|الأربعاء|الخميس|الجمعة)"
            r"|(?:أغسطس|سبتمبر|أكتوبر|نوفمبر|ديسمبر)"
            r"|#{1,3}\s"
            r"|\|.*\|"  # table row
            r")",
            _re.MULTILINE | _re.IGNORECASE,
        )

    lines = text.strip().split("\n")
    if not lines:
        return False

    # Count structured lines (dates, bullets, tables, headings)
    structured = sum(1 for line in lines if line.strip() and _PROSE_RE.match(line.strip()))
    total = sum(1 for line in lines if line.strip())

    if total == 0:
        return False

    # If >30% of lines look structured, it's NOT prose — use basic structuring
    return (structured / total) < 0.30


_CONTENT_TYPE_RE = None


def _basic_structure(text: str) -> str:
    """Add basic markdown structure to flat text when the LLM is unavailable.

    Detects date lines, content-type lines (Reel/Carousel/Story), platform
    lines, hashtags, and list items — groups them into per-entry sections
    so the PDF generator produces structured output.
    """
    import re as _re

    global _DATE_LINE_RE, _CONTENT_TYPE_RE
    if _DATE_LINE_RE is None:
        _DATE_LINE_RE = _re.compile(
            r"^(?:"
            r"(?:Aug|Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun|Jul)\s+\d{1,2}"
            r"|\d{1,2}\s+(?:Aug|Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun|Jul)"
            r"|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?"
            r"|(?:السبت|الأحد|الاثنين|الثلاثاء|الأربعاء|الخميس|الجمعة)\s+\d{1,2}"
            r"|(?:أغسطس|سبتمبر|أكتوبر)\s+\d{1,2}"
            r")"
            r"(?:\s|,|$)",
            _re.IGNORECASE,
        )
        _CONTENT_TYPE_RE = _re.compile(
            r"^(Reel|Carousel|Story|Stories|Static|Post|Video|Live|"
            r"ريل|كاروسيل|ستوري|منشور|فيديو)",
            _re.IGNORECASE,
        )

    lines = text.split("\n")
    out: list[str] = []
    current_entry: list[str] = []
    entry_count = 0

    def _flush_entry():
        nonlocal current_entry, entry_count
        if current_entry:
            entry_count += 1
            # Extract date and type for the heading if available
            date_val = next((c.replace("**Date:** ", "") for c in current_entry if c.startswith("**Date:**")), "")
            type_val = next((c.replace("**Type:** ", "") for c in current_entry if c.startswith("**Type:**")), "")
            heading = f"## Entry {entry_count}"
            if date_val and type_val:
                heading = f"## {date_val} — {type_val}"
            elif date_val:
                heading = f"## {date_val}"
            out.append(heading)
            for item in current_entry:
                out.append(item)
            out.append("")
            current_entry = []

    for line in lines:
        stripped = line.strip()

        if not stripped:
            if current_entry:
                current_entry.append("")
            continue

        # Date lines → new entry section
        if _DATE_LINE_RE.match(stripped):
            _flush_entry()
            current_entry.append(f"**Date:** {stripped}")
            continue

        # Content type → entry metadata
        if _CONTENT_TYPE_RE.match(stripped):
            if current_entry and not any("**Type:**" in c for c in current_entry):
                current_entry.append(f"**Type:** {stripped}")
            else:
                _flush_entry()
                current_entry.append(f"**Type:** {stripped}")
            continue

        # Platform lines
        if stripped in ("Instagram", "TikTok", "Instagram + TikTok", "Instagram & TikTok", ".."):
            if current_entry and not any("**Platform:**" in c for c in current_entry):
                current_entry.append(f"**Platform:** {stripped}")
            else:
                current_entry.append(stripped)
            continue

        # Hashtags (lines with multiple #)
        if stripped.count("#") >= 2 or (stripped.startswith("#") and len(stripped) > 3):
            current_entry.append(f"**Hashtags:** {stripped}")
            continue

        # Status
        if "progress" in stripped.lower() or "✏" in stripped or "in progress" in stripped.lower():
            current_entry.append(f"**Status:** {stripped}")
            continue

        # List items → bullets
        if stripped.startswith(("-", "•", "*")) or (
            len(stripped) > 2 and stripped[0].isdigit() and stripped[1] == "."
        ):
            current_entry.append(f"- {stripped.lstrip('-•* ').lstrip('0123456789. ')}")
            continue

        # Regular content
        current_entry.append(stripped)

    _flush_entry()

    result = "\n".join(out)

    # Add a top-level heading if none exists
    if not result.startswith("#"):
        first_line = text.strip().split("\n")[0][:80]
        result = f"# {first_line}\n\n{result}"

    return result


_FILE_REF_RE = None


def _find_source_in_history(messages: list[dict], state: dict) -> str:
    """Find a source file path from the conversation history.

    When the user says 'reproduce this PDF' in an ongoing session, the
    referenced file was uploaded or mentioned in an earlier message. Search:
    1. Attachment stubs in messages ([Attached: <path>])
    2. Explicit file paths in user messages
    3. Active attachments from the state
    4. Most recent PDF in the attachments directory
    """
    import re as _re

    global _FILE_REF_RE
    if _FILE_REF_RE is None:
        # Matches: file paths with extensions, [Attached: path], attachment stubs
        _FILE_REF_RE = _re.compile(
            r"(?:\[Attached:\s*([^\]]+\.\w{2,5})\])"
            r"|(?:path[=:\s]+([\w\-./\\]+\.\w{2,5}))"
            r"|(?:file[=:\s]+([\w\-./\\]+\.\w{2,5}))"
            r"|(?:([\w\-./\\]+\.(?:pdf|docx?|xlsx?|pptx?|csv)))",
            _re.IGNORECASE,
        )

    # 1. Scan messages from most recent to oldest for file references
    for msg in reversed(messages):
        content = msg.get("content", "")
        if not isinstance(content, str):
            continue
        # Only look at user messages and attachment stubs
        if msg.get("role") not in ("user", "assistant"):
            continue
        matches = _FILE_REF_RE.findall(content)
        for match in matches:
            for group in match:
                if group:
                    path = group.strip()
                    # Verify it looks like a real file path (has extension)
                    if Path(path).suffix:
                        return path

    # 2. Check active attachments from the state
    for att in state.get("active_attachments") or []:
        path = att.get("path") or att.get("filename") or ""
        if path:
            return path

    # 3. Most recent PDF in the attachments directory
    att_dir = Path("kazma-data/attachments")
    if att_dir.is_dir():
        pdfs = sorted(
            [f for f in att_dir.iterdir() if f.suffix.lower() == ".pdf"],
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if pdfs:
            return str(pdfs[0])

    return ""


def _resolve_platform_chat_id(platform: str) -> str:
    """Resolve the configured chat ID for a delivery platform.

    Checks ConfigStore for connectors.<platform>.swarm_chat_id, then
    connectors.<platform>.allowed_users (normalizing string→list — the
    send_file tool had the same string-as-list bug).
    """
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        chat_id = store.get(f"connectors.{platform}.swarm_chat_id")
        if chat_id:
            return str(chat_id).strip()
        allowed = store.get(f"connectors.{platform}.allowed_users")
        if isinstance(allowed, str):
            candidates = [u.strip() for u in allowed.replace(",", " ").split() if u.strip()]
        elif isinstance(allowed, list):
            candidates = [str(u).strip() for u in allowed if str(u).strip()]
        else:
            candidates = []
        if candidates:
            return candidates[0]
    except Exception as exc:
        logger.debug("[document_pipeline] chat ID resolution failed: %s", exc)
    return ""


def _read_pdf(path: Path) -> str:
    """Read PDF text content with proper Arabic handling.

    Raw fitz.get_text() returns Arabic in VISUAL order (reversed, isolated
    forms) and reads table PDFs column-by-column. The documents service
    handles Arabic correctly and produces better structured output.
    """
    # Primary: the documents service (proper Arabic + table handling)
    try:
        from kazma_core.documents.service import DocumentService

        result = DocumentService().read_transient_sync(
            path, approved_path=path, max_chars=50000, fence=False
        )
        text = result.as_tool_output()
        if text and len(text) > 50:
            return text
    except Exception as exc:
        logger.debug("[document_pipeline] documents service failed: %s", exc)

    # Fallback: fitz with Arabic bidi correction
    try:
        import fitz

        doc = fitz.open(str(path))
        text_parts = []
        for page in doc:
            page_text = page.get_text()
            # fitz returns Arabic in visual order (reversed). Apply bidi
            # to convert back to logical order so downstream processing
            # (reshaping, rendering) works correctly.
            try:
                from bidi.algorithm import get_display

                page_text = get_display(page_text, base_dir="R")
            except ImportError:
                pass  # python-bidi not installed — use raw text
            text_parts.append(page_text)
        doc.close()
        return "\n\n".join(text_parts)
    except Exception as exc:
        logger.warning("[document_pipeline] PDF read failed: %s", exc)
        return ""


def _derive_title(content: str, fmt: str) -> str:
    """Derive a document title from the content."""
    for line in content.split("\n"):
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()[:80]
        if len(line) > 10:
            return line[:60]
    return f"Generated Document ({fmt.upper()})"


def _format_result(status: str, steps: list[str], detail: str) -> str:
    """Format the pipeline result for the user."""
    lines = [f"📄 Document Pipeline — {status}", ""]
    for s in steps:
        lines.append(f"  {s}")
    lines.append("")
    if detail:
        lines.append(f"Result: {detail}")
    return "\n".join(lines)


def register() -> None:
    """Register the document pipeline with the pipeline registry."""
    registry = get_registry()
    registry.register(
        Pipeline(
            name="document",
            description="Generate PDF/DOCX/XLSX documents via a deterministic 5-step workflow",
            handler=document_pipeline,
            category=IntentCategory.DOCUMENT,
            budget=PipelineBudget(
                max_tokens=15_000,
                max_steps=5,
                max_llm_calls=1,
                timeout_seconds=120.0,
            ),
        )
    )
