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
    if source_path:
        try:
            p = Path(source_path)
            if not p.is_absolute():
                from kazma_core.workspace.binding import resolve_active_root

                p = resolve_active_root() / p
            if p.is_file():
                if p.suffix.lower() == ".pdf":
                    source_content = _read_pdf(p)
                else:
                    source_content = p.read_text(encoding="utf-8", errors="replace")
                steps_log.append(f"✓ Read source: {p.name} ({len(source_content)} chars)")
            else:
                steps_log.append(f"⚠ Source not found: {source_path}")
        except Exception as exc:
            steps_log.append(f"⚠ Source read failed: {exc}")

    if not source_content and messages:
        # Fall back to extracting from the conversation context
        user_texts = [
            m.get("content", "")
            for m in messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        source_content = "\n\n".join(user_texts[-3:])  # last 3 user messages
        if source_content:
            steps_log.append(f"✓ Extracted from conversation ({len(source_content)} chars)")

    if not source_content:
        return _format_result(
            "Error", steps_log,
            "No source content found. Please provide a file or describe what to generate.",
        )

    # ── Step 2: EXTRACT/STRUCTURE via one LLM call ──────────────────
    structured_md = source_content
    if llm is not None and len(source_content) > 200:
        try:
            extract_prompt = (
                "Convert the following content into well-structured markdown "
                "with # headings, ## subsections, bullet points, and tables "
                "where appropriate. Preserve ALL information, do not summarize "
                "or omit. Keep the original language (Arabic stays Arabic, "
                "English stays English).\n\n"
                f"---CONTENT START---\n{source_content[:12000]}\n---CONTENT END---"
            )
            resp = await llm.chat(
                [{"role": "user", "content": extract_prompt}], tools=None
            )
            content = (getattr(resp, "content", "") or "").strip()
            if content and not content.startswith("Error"):
                structured_md = content
                steps_log.append(f"✓ LLM structured content ({len(structured_md)} chars)")
            else:
                steps_log.append("⚠ LLM extraction failed — using raw content")
        except Exception as exc:
            steps_log.append(f"⚠ LLM extraction error: {exc} — using raw content")
    else:
        steps_log.append("✓ Content short enough — no LLM structuring needed")

    # ── Step 3: WRITE the markdown file ─────────────────────────────
    md_filename = f"document_output_{int(__import__('time').time())}.md"
    try:
        from kazma_core.tools.file_write import file_write

        write_result = await file_write(md_filename, structured_md)
        if "Error" in write_result:
            return _format_result("Error", steps_log, f"file_write failed: {write_result}")
        steps_log.append(f"✓ Wrote markdown: {md_filename}")
    except Exception as exc:
        return _format_result("Error", steps_log, f"file_write error: {exc}")

    # ── Step 4: GENERATE the document ───────────────────────────────
    title = params.get("title") or _derive_title(source_content, output_format)
    try:
        if output_format in ("docx", "word"):
            from kazma_skills.native.document_generator.tools import generate_docx

            gen_result = await generate_docx(title, markdown_path=md_filename)
        elif output_format in ("xlsx", "excel", "spreadsheet"):
            from kazma_skills.native.document_generator.tools import generate_xlsx

            gen_result = await generate_xlsx(
                [{"name": "Sheet1", "rows": [["Content"], [structured_md[:500]]]}],
                filename=title,
            )
        else:
            from kazma_skills.native.document_generator.tools import generate_pdf

            gen_result = await generate_pdf(title, markdown_path=md_filename)

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
                    target = f"{deliver_to}:"

                if target:
                    send_result = await send_file_message(
                        target_id=target,
                        text=f"📎 {title}",
                        file_path=output_path,
                    )
                    steps_log.append(f"✓ Delivered via {deliver_to}: {send_result[:100]}")
                else:
                    steps_log.append("⚠ No delivery target bound — file saved locally")
            else:
                steps_log.append("⚠ Could not extract output path for delivery")
        except Exception as exc:
            steps_log.append(f"⚠ Delivery failed: {exc} — file saved locally")
    else:
        steps_log.append("✓ File saved (no delivery requested)")

    return _format_result("Success", steps_log, gen_result[:500])


def _read_pdf(path: Path) -> str:
    """Read PDF text content."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(path))
        text_parts = []
        for page in doc:
            text_parts.append(page.get_text())
        doc.close()
        return "\n\n".join(text_parts)
    except ImportError:
        # Fall back to the documents service
        try:
            from kazma_core.documents.service import DocumentService

            result = DocumentService().read_transient_sync(
                path, approved_path=path, max_chars=50000, fence=False
            )
            return result.as_tool_output()
        except Exception:
            return path.read_text(encoding="utf-8", errors="replace")


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
