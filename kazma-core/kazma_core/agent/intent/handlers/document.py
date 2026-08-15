"""Document generate handler — all mutations via tool_executor.execute().

Implements §13 of KAZMA_INTENT_ENGINE.md (Phase 1). The handler NEVER
calls file_write / generate_pdf / send_file directly — every mutation goes
through execute() so HITL + commitment stay wired. Delivery uses the
registered send_file tool (execute path), which resolves the active-chat
target and honors the outbound gate.

HITL rule: if the graph interrupt cannot fire from inside a handler
(because we're in supervisor_node, not tool_worker), the handler MUST
escalate (return HandlerResult(escalate=True)) so the supervisor loop
performs the write with the HITL card. Only auto-execute when HITL is
off, YOLO is on, or the approval ContextVar is already true.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from kazma_core.agent.intent.types import (
    ActKind,
    HandlerResult,
    ResolvedFile,
    TurnDecision,
)

__all__ = ["run_document", "register"]

logger = logging.getLogger(__name__)


def _can_auto_execute(tool_executor: Any) -> bool:
    """Check whether we're in a context where execute() is safe.

    Safe when: HITL is off, the mutating tools are not approval-gated, or
    the graph HITL gate ContextVar is set (we're inside tool_worker_node).
    Otherwise escalate so the supervisor loop performs the writes with the
    HITL card. YOLO is intentionally not checked here — it requires a
    thread_id that supervisor_node does not carry; the loop handles YOLO
    via tool_worker_node instead.
    """
    try:
        from kazma_core.safety.hitl import get_hitl_config

        cfg = get_hitl_config()
        if not cfg.get("enabled"):
            return True
        danger = cfg.get("require_approval_for") or []
        if "file_write" not in danger and "generate_pdf" not in danger:
            return True

        # Check ContextVars (set by graph tool_worker_node)
        try:
            from kazma_core.agent.tool_registry import _graph_hitl_gate_ctx, _hitl_approved_ctx

            if _graph_hitl_gate_ctx.get(None) or _hitl_approved_ctx.get(None):
                return True
        except Exception:
            pass

        return False
    except Exception:
        return False


async def run_document(decision: TurnDecision, state: dict[str, Any], **ctx: Any) -> HandlerResult:
    """Execute the document generation workflow via tool_executor.

    All mutations go through execute() so HITL/commitment stay wired.
    """
    tool_executor = ctx.get("tool_executor")
    llm = ctx.get("llm")

    if tool_executor is None or not hasattr(tool_executor, "execute"):
        return HandlerResult(ok=False, escalate=True, message="no tool_executor")

    # HITL check: if we can't safely execute (graph interrupt can't fire
    # from supervisor_node), escalate so the loop performs the writes
    if not _can_auto_execute(tool_executor):
        return HandlerResult(
            ok=False,
            escalate=True,
            message="hitl_required",
        )

    primary = decision.primary
    if primary is None or primary.kind != ActKind.DOCUMENT_GENERATE:
        return HandlerResult(ok=False, escalate=True, message="not_document_act")

    fmt = primary.slots.get("format", "pdf").lower()
    deliver_to = primary.slots.get("deliver_to", "")
    entities = decision.entities

    steps_log: list[str] = []

    # ── Step 1: READ source ─────────────────────────────────────────
    source_content = ""
    source_file: ResolvedFile | None = entities.files[0] if entities.files else None

    if source_file:
        p = Path(source_file.path)
        import asyncio as _aio

        if p.suffix.lower() == ".pdf":
            source_content = await _aio.to_thread(_read_pdf_raw, p)
        else:
            try:
                source_content = await _aio.to_thread(
                    p.read_text, encoding="utf-8", errors="replace"
                )
            except Exception as exc:
                return HandlerResult(ok=False, escalate=True, message=f"read failed: {exc}")
        if source_content:
            steps_log.append(f"Read source: {p.name} ({len(source_content)} chars)")
    elif primary.slots.get("inline_content"):
        # From-scratch: use the user's message as content
        user_text = ""
        for m in reversed(state.get("messages", [])):
            if m.get("role") == "user" and isinstance(m.get("content"), str):
                user_text = m["content"]
                break
        if user_text:
            source_content = user_text
            steps_log.append(f"Inline content ({len(source_content)} chars)")

    if not source_content:
        return HandlerResult(
            ok=False,
            escalate=True,
            message="No source document found. Attach a file or provide content.",
        )

    # ── Step 2: STRUCTURE ────────────────────────────────────────────
    structured_md = _basic_structure(source_content)
    steps_log.append(f"Structured ({len(structured_md)} chars)")

    # Optional LLM enhancement (preserve, not condense)
    if llm is not None and len(source_content) > 500:
        try:
            import asyncio

            prompt = (
                "Organize this content into markdown with headings and lists. "
                "PRESERVE all information and the original language. "
                "Do NOT condense or summarize.\n\n"
                f"{source_content[:6000]}"
            )
            resp = await asyncio.wait_for(
                llm.chat([{"role": "user", "content": prompt}], tools=None),
                timeout=180.0,
            )
            content = (getattr(resp, "content", "") or "").strip()
            if content and not content.startswith("Error"):
                structured_md = content
                steps_log.append(f"LLM structured ({len(content)} chars)")
        except Exception as exc:
            logger.debug("[intent_document] LLM structuring failed: %s", exc)
            # Keep basic structuring

    # ── Step 3: WRITE markdown (via execute) ─────────────────────────
    md_name = f"document_output_{int(time.time())}.md"
    out = await tool_executor.execute("file_write", {"path": md_name, "content": structured_md})
    if out.get("is_error") or str(out.get("content", "")).startswith("Error:"):
        return HandlerResult(ok=False, escalate=True, message=f"file_write failed: {out.get('content', '')[:200]}")
    steps_log.append(f"Wrote markdown: {md_name}")

    # ── Step 4: GENERATE (via execute) ───────────────────────────────
    title = primary.slots.get("title") or _derive_title(source_content)
    gen_tool = f"generate_{fmt}"

    if fmt == "xlsx":
        return HandlerResult(
            ok=False,
            escalate=True,
            message="XLSX generation requires structured rows — cannot generate from flat markdown. Use constrain mode.",
        )
    if fmt == "pptx":
        pptx_check = await tool_executor.execute("generate_pptx", {"title": title, "markdown_path": md_name})
        if pptx_check.get("is_error"):
            return HandlerResult(
                ok=False,
                escalate=True,
                message="PPTX generation not available. Cannot silently generate PDF instead.",
            )
        gen_tool = "generate_pptx"

    out = await tool_executor.execute(gen_tool, {"title": title, "markdown_path": md_name})
    gen_content = str(out.get("content", ""))
    if out.get("is_error") or gen_content.startswith("Error:"):
        return HandlerResult(ok=False, escalate=True, message=f"{gen_tool} failed: {gen_content[:200]}")
    steps_log.append(f"Generated {fmt.upper()}: {gen_content[:150]}")

    # ── Step 5: QUALITY gate ─────────────────────────────────────────
    output_path = ""
    if "Saved to:" in gen_content:
        output_path = gen_content.split("Saved to:")[-1].strip()

    if not output_path:
        return HandlerResult(ok=False, escalate=True, message="No output path in generation result")

    op = Path(output_path)
    if not op.is_file() or op.stat().st_size < 200:
        return HandlerResult(ok=False, escalate=True, message=f"Quality gate failed: {output_path} missing or <200 bytes")

    if op.suffix.lower() == ".pdf":
        try:
            import fitz

            doc = fitz.open(str(op))
            page_count = len(doc)
            # §21 Phase 4: Arabic isolated-form scan — check for rendering
            # issues (isolated/presentation forms indicate bidi failure)
            _arabic_issue = False
            if page_count > 0:
                sample_text = doc[0].get_text()[:500]
                doc.close()
                # Check for isolated Arabic forms (U+FE70-FEFF presentation
                # forms that indicate the text wasn't properly shaped)
                isolated_count = sum(
                    1 for c in sample_text if "\uFE70" <= c <= "\uFEFF"
                )
                if isolated_count > 10:  # >10 isolated forms = rendering issue
                    _arabic_issue = True
                    logger.warning(
                        "[intent_document] Arabic isolated forms detected (%d) — "
                        "possible bidi rendering issue",
                        isolated_count,
                    )
            else:
                doc.close()
            if page_count < 1:
                return HandlerResult(ok=False, escalate=True, message="Quality gate: PDF has 0 pages")
            # Arabic issue doesn't fail the gate (the renderer may handle it
            # correctly on re-render) but we log it for Phase 4 retry logic
        except Exception:
            pass  # fitz unavailable — size check is sufficient

    steps_log.append(f"Quality gate passed ({op.stat().st_size} bytes)")

    # ── Step 6: DELIVER (optional) ───────────────────────────────────
    # F8: route delivery through the send_file tool (execute path) so the
    # HITL/commitment wiring applies — never call send_file_message directly.
    if deliver_to:
        target = _resolve_delivery_target(deliver_to)
        if target:
            try:
                send_out = await tool_executor.execute(
                    "send_file",
                    {"file_path": output_path, "caption": f"📎 {title}"},
                )
                if send_out.get("is_error") or str(send_out.get("content", "")).startswith("Error:"):
                    steps_log.append(f"Delivery failed: {str(send_out.get('content', ''))[:120]} — file saved locally")
                else:
                    steps_log.append(f"Delivered via {deliver_to}: {str(send_out.get('content', ''))[:100]}")
            except Exception as exc:
                steps_log.append(f"Delivery failed: {exc} — file saved locally")
        else:
            steps_log.append(f"No {deliver_to} chat ID — file saved locally")
    else:
        steps_log.append("File saved")

    result_msg = "📄 Document generated\n" + "\n".join(f"  ✓ {s}" for s in steps_log)
    result_msg += f"\n  Result: {gen_content[:200]}"

    return HandlerResult(
        ok=True,
        message=result_msg,
        artifacts={"output_path": output_path, "format": fmt},
    )


def _read_pdf_raw(path: Path) -> str:
    """Read PDF text — raw fitz, NO get_display (§13).

    The renderer (documents/rich_render.py) already handles Arabic shaping.
    """
    try:
        import fitz

        doc = fitz.open(str(path))
        parts = [page.get_text() for page in doc]
        doc.close()
        return "\n\n".join(parts)
    except Exception:
        try:
            from kazma_core.documents.service import DocumentService

            result = DocumentService().read_transient_sync(
                path, approved_path=path, max_chars=50000, fence=False
            )
            return result.as_tool_output()
        except Exception:
            return ""


def _basic_structure(text: str) -> str:
    """Deterministic markdown structuring for calendar/structured content."""
    import re

    date_re = re.compile(
        r"^(?:"
        r"(?:Aug|Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun|Jul)\s+\d{1,2}"
        r"|\d{1,2}\s+(?:Aug|Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr|May|Jun|Jul)"
        r"|\d{1,2}[/-]\d{1,2}"
        r")",
        re.IGNORECASE,
    )
    type_re = re.compile(
        r"^(Reel|Carousel|Story|Stories|Static|Post|Video)",
        re.IGNORECASE,
    )

    lines = text.split("\n")
    out: list[str] = []
    entry: list[str] = []
    count = 0

    def flush():
        nonlocal entry, count
        if entry:
            count += 1
            date = next((c.replace("**Date:** ", "") for c in entry if c.startswith("**Date:**")), "")
            typ = next((c.replace("**Type:** ", "") for c in entry if c.startswith("**Type:**")), "")
            heading = f"## {date} — {typ}" if date and typ else f"## Entry {count}"
            out.append(heading)
            out.extend(entry)
            out.append("")
            entry = []

    for line in lines:
        s = line.strip()
        if not s:
            if entry:
                entry.append("")
            continue
        if date_re.match(s):
            flush()
            entry.append(f"**Date:** {s}")
        elif type_re.match(s):
            if entry and not any("**Type:**" in c for c in entry):
                entry.append(f"**Type:** {s}")
            else:
                flush()
                entry.append(f"**Type:** {s}")
        elif s in ("Instagram", "TikTok", "Instagram + TikTok"):
            if not any("**Platform:**" in c for c in entry):
                entry.append(f"**Platform:** {s}")
        elif s.startswith("#") or s.count("#") >= 2:
            entry.append(f"**Hashtags:** {s}")
        elif "progress" in s.lower() or "✏" in s:
            entry.append(f"**Status:** {s}")
        else:
            entry.append(s)

    flush()
    result = "\n".join(out)
    if not result.startswith("#"):
        first = text.strip().split("\n")[0][:80]
        result = f"# {first}\n\n{result}"
    return result


def _derive_title(content: str) -> str:
    for line in content.split("\n"):
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:80]
        if len(s) > 10:
            return s[:60]
    return "Generated Document"


def _resolve_delivery_target(platform: str) -> str:
    """Resolve delivery chat ID from ConfigStore."""
    try:
        from kazma_core.tools.send_message import get_current_delivery_target

        target = get_current_delivery_target()
        if target and not target.endswith(":"):
            return target
    except Exception:
        pass
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        chat_id = store.get(f"connectors.{platform}.swarm_chat_id")
        if chat_id:
            return f"{platform}:{chat_id}"
        allowed = store.get(f"connectors.{platform}.allowed_users")
        if isinstance(allowed, str):
            candidates = [u.strip() for u in allowed.replace(",", " ").split() if u.strip()]
        elif isinstance(allowed, list):
            candidates = [str(u).strip() for u in allowed if str(u).strip()]
        else:
            candidates = []
        if candidates:
            return f"{platform}:{candidates[0]}"
    except Exception:
        pass
    return ""


def register() -> None:
    """Register the document handler with the intent registry."""
    from kazma_core.agent.intent.registry import IntentHandler, get_registry

    get_registry().register(IntentHandler(
        name="document_generate",
        act="document_generate",
        required_slots=("format",),
        uses_execute=True,
        mutating=True,
        timeout_seconds=180.0,
        run=run_document,
    ))
