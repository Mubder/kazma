"""Telegram formatting utilities — shared Markdown-to-HTML conversion for swarm output.

This module provides functions to convert common Markdown syntax to Telegram HTML
so that bold, italic, code blocks, and links render properly inside <blockquote>
elements when sending swarm task results to Telegram groups.
"""

from __future__ import annotations

import html
import re
from typing import Any

# Placeholder tokens so fenced/inline code is not re-parsed as markdown
_CODE_FENCE_RE = re.compile(r"```(?:[^\n`]*)\n?(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)([^_\n]+?)(?<!_)_(?!_)")
_HEADING_MD_RE = re.compile(r"(?m)^(#{1,6})\s+(.+)$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def tg_escape(text: str) -> str:
    """Escape user content for Telegram HTML parse mode."""
    return html.escape(str(text), quote=False)


def md_to_tg_html(text: str) -> str:
    """Convert common Markdown in worker/aggregated output to Telegram HTML.

    Telegram HTML does not understand ``**bold**`` — without conversion those
    markers show literally inside ``<blockquote>``. Escape first so raw HTML
    from the model cannot inject tags, then apply safe replacements.
    """
    if not text:
        return ""

    placeholders: list[str] = []

    def _stash(html_snippet: str) -> str:
        placeholders.append(html_snippet)
        return f"\x00PH{len(placeholders) - 1}\x00"

    # Escape first — all replacements below operate on safe text
    s = tg_escape(text)

    # Fenced code → <pre> (stash so inner * etc. are not styled)
    def _fence(m: re.Match[str]) -> str:
        return _stash(f"<pre>{m.group(1).strip()}</pre>")

    s = _CODE_FENCE_RE.sub(_fence, s)

    def _inline(m: re.Match[str]) -> str:
        return _stash(f"<code>{m.group(1)}</code>")

    s = _INLINE_CODE_RE.sub(_inline, s)

    # Links [text](url) — URL already escaped; keep simple
    def _link(m: re.Match[str]) -> str:
        return _stash(f'<a href="{m.group(2)}">{m.group(1)}</a>')

    s = _LINK_RE.sub(_link, s)

    # ATX headings → bold line
    s = _HEADING_MD_RE.sub(lambda m: f"<b>{m.group(2).strip()}</b>", s)

    # Bold then italic
    s = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", s)
    s = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", s)

    # Restore stashed code/link segments
    for i, snippet in enumerate(placeholders):
        s = s.replace(f"\x00PH{i}\x00", snippet)

    return s


def tg_quote(text: str) -> str:
    """Wrap text in a Telegram HTML blockquote.

    Worker output is usually Markdown; convert to HTML so **bold** renders
    inside the quote instead of showing literal asterisks.
    """
    body = md_to_tg_html(text).strip() or "—"
    return f"<blockquote>{body}</blockquote>"


def tg_heading(label: str) -> str:
    """Bold section heading for Telegram output."""
    return f"<b>{tg_escape(label)}</b>"


# Horizontal rule for section separation
HEADING_RULE = "━━━━━━━━━━━━━━━━━━━━━━━━━━"


def format_swarm_task_result(
    task_id: str,
    status: str,
    aggregated_output: str = "",
    error: str = "",
    duration: float = 0.0,
    tokens: int = 0,
    worker_results: list[dict[str, Any]] | None = None,
) -> str:
    """Format a swarm task result for Telegram HTML output.

    This produces a nicely formatted message with bold headings,
    blockquotes for outputs, and proper HTML escaping.
    """
    if worker_results is None:
        worker_results = []

    status_lower = str(status).lower()
    # TaskResult.status uses completed/failed/timeout (task.py TaskStatus);
    # WorkerResult.status uses success/failed. Normalize both vocabularies.
    if status_lower in ("success", "completed"):
        status_icon = "✅"
        status_text = "SUCCESS"
    elif status_lower in ("partial_success", "partial", "paused"):
        status_icon = "⚠️"
        status_text = "PARTIAL SUCCESS"
    elif status_lower in ("failed", "error"):
        status_icon = "❌"
        status_text = "FAILED"
    elif status_lower == "timeout":
        status_icon = "⏱️"
        status_text = "TIMEOUT"
    elif status_lower == "cancelled":
        status_icon = "🚫"
        status_text = "CANCELLED"
    else:
        status_icon = "❓"
        status_text = status_lower.upper() or "UNKNOWN"

    lines: list[str] = []
    lines.append(f"🚀 {tg_heading('Swarm Task Execution Report')}")
    lines.append(HEADING_RULE)
    if task_id:
        lines.append(f"🆔 {tg_heading('Task ID:')} <code>{tg_escape(task_id)}</code>")
    lines.append(f"📊 {tg_heading('Status:')} {status_icon} {tg_heading(status_text)}")
    if duration > 0:
        dur = f"<code>{duration:.2f}s</code>"
        tok = f" | 🪙 {tg_heading('Tokens:')} <code>{tokens}</code>" if tokens > 0 else ""
        lines.append(f"⏱️ {tg_heading('Duration:')} {dur}{tok}")
    lines.append(HEADING_RULE)

    if error:
        lines.append(f"⚠️ {tg_heading('Error Details:')}")
        lines.append(tg_quote(error))
        lines.append("")

    if aggregated_output:
        lines.append(f"✨ {tg_heading('Final Aggregated Output:')}")
        lines.append(tg_quote(aggregated_output))
        lines.append("")

    if worker_results:
        lines.append(f"👥 {tg_heading('Worker Breakdowns:')}")
        lines.append("")
        for wr in worker_results:
            wr_name = wr.get("worker", "unknown") or "unknown"
            wr_status = wr.get("status", "") or ""
            wr_output = wr.get("output", "") or ""
            wr_error = wr.get("error", "") or ""
            wr_duration = wr.get("duration_seconds", 0.0) or 0.0
            wr_tokens = wr.get("tokens_used", 0) or 0

            wr_status_lower = str(wr_status).lower()
            if wr_status_lower in ("success", "completed"):
                wr_icon = "✅"
            elif wr_status_lower in ("partial_success", "partial", "paused"):
                wr_icon = "⚠️"
            elif wr_status_lower == "timeout":
                wr_icon = "⏱️"
            elif wr_status_lower == "cancelled":
                wr_icon = "🚫"
            else:
                wr_icon = "❌"

            # Plain worker line — no bold/italic (user preference)
            lines.append(
                f"• {tg_escape(wr_name)} ({wr_icon} {wr_status_lower.upper() or 'UNKNOWN'})"
            )
            meta_parts = []
            if wr_duration > 0:
                meta_parts.append(f"<code>{wr_duration:.2f}s</code>")
            if wr_tokens > 0:
                meta_parts.append(f"<code>{wr_tokens}</code> tokens")
            if meta_parts:
                lines.append("⏱️ " + " | ".join(meta_parts))

            # Full worker output — within blockquote
            raw_content = wr_output if wr_output else (wr_error or "no output")
            lines.append(tg_quote(raw_content.strip() or "no output"))
            lines.append("")

    return "\n".join(lines).strip()