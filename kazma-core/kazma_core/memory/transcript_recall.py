"""Transcript recall fallback — search past chat sessions when memory is empty.

Born from the 2026-08-27 "green names" incident: facts that live ONLY in old
chat transcripts (a naming shortlist, a decision table) are invisible to V2
recall, so the supervisor burned 21 iterations hand-writing SQL against
``chat_sessions.db`` and grepping export files (plus a YOLO approval) just to
answer "what did we decide before?".

This module gives the supervisor a first-class, read-only fallback: when V2
recall returns nothing, search past WEB chat sessions (title + message text)
and return ranked hits with snippets. The supervisor injects them as a fenced
untrusted block next to the memory block — zero extra iterations, no danger
tools, no permissions.

Design notes:
  * Opens ``kazma-data/chat_sessions.db`` READ-ONLY by path — kazma-core must
    not import kazma-ui's SessionManager (layering); the schema is the
    documented ``sessions`` table (tenant_id, session_id, messages, title…).
  * Best-effort and NEVER raises — a missing DB, a locked file, or a schema
    drift returns ``[]`` and the turn proceeds exactly as before.
  * Kill-switch: ``KAZMA_TRANSCRIPT_RECALL=0`` env or ConfigStore
    ``memory.transcript_fallback=false`` (live-read, no restart).
  * Transcript text is UNTRUSTED conversation data — callers must inject it
    via :func:`format_transcript_block` (prompt-fenced), never raw.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "format_transcript_block",
    "search_transcripts",
    "transcript_fallback_enabled",
]

_MAX_HITS = 3
_CANDIDATE_SCAN = 400  # recent rows pulled for python-side scoring
_SNIPPET_CHARS = 320
_MIN_TERM = 3

# Latin words/digits + Arabic letter runs. Terms shorter than 3 chars are noise.
_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}|[\u0600-\u06FF]{3,}|\d[\d/]{2,}")

_STOPWORDS = {
    "the", "and", "for", "you", "your", "our", "with", "that", "this",
    "what", "when", "where", "which", "list", "every", "all", "from",
    "about", "into", "have", "has", "was", "were", "are", "not", "but",
    "can", "get", "give", "show", "find", "need", "want", "please",
}


def transcript_fallback_enabled() -> bool:
    """Live-read kill-switch. Env wins, then ConfigStore
    ``memory.transcript_fallback`` (default ON). Never raises."""
    env = (os.getenv("KAZMA_TRANSCRIPT_RECALL") or "").strip().lower()
    if env in ("0", "false", "off"):
        return False
    if env in ("1", "true", "on"):
        return True
    try:
        from kazma_core.config_store import get_config_store

        val = get_config_store().get("memory.transcript_fallback")
        if isinstance(val, bool):
            return val
        if isinstance(val, str) and val.strip().lower() in ("0", "false", "off"):
            return False
    except Exception:
        pass
    return True


def _terms(query: str, max_terms: int = 6) -> list[str]:
    """Most-specific query terms: longest first (proper nouns like a brand
    name carry the signal), stopwords dropped, case-insensitive."""
    seen: dict[str, str] = {}
    for m in _TERM_RE.finditer(str(query or "")):
        raw = m.group(0)
        low = raw.lower()
        if low in _STOPWORDS:
            continue
        seen.setdefault(low, raw)
    ordered = sorted(seen.values(), key=len, reverse=True)
    return ordered[:max_terms]


def _snippet(text: str, terms: list[str]) -> str:
    """Window around the first term occurrence; JSON-unescape for display."""
    hay = text.lower()
    pos = -1
    for t in terms:
        pos = hay.find(t.lower())
        if pos >= 0:
            break
    if pos < 0:
        return text[:_SNIPPET_CHARS].strip()
    start = max(0, pos - _SNIPPET_CHARS // 2)
    end = min(len(text), pos + _SNIPPET_CHARS)
    out = text[start:end]
    for esc, real in (("\\n", "\n"), ("\\t", "\t"), ('\\"', '"')):
        out = out.replace(esc, real)
    return ("…" if start > 0 else "") + out.strip() + ("…" if end < len(text) else "")


def search_transcripts(
    query: str,
    *,
    tenant_id: str = "default",
    exclude_session_id: str | None = None,
    db_path: str | Path | None = None,
    limit: int = _MAX_HITS,
) -> list[dict[str, Any]]:
    """Rank past chat sessions by query-term overlap. Never raises.

    Returns up to ``limit`` hits: {session_id, thread_id, title, created_at,
    score, snippet}. Empty list when the store is missing/locked/empty or no
    term matches.
    """
    try:
        terms = _terms(query)
        if not terms:
            return []
        if db_path is None:
            from kazma_core.paths import data_dir

            db_path = data_dir() / "chat_sessions.db"
        path = Path(db_path)
        if not path.exists():
            return []
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3.0)
        try:
            conn.row_factory = sqlite3.Row
            like = " OR ".join(
                ["title LIKE ? OR messages LIKE ?"] * len(terms)
            )
            params: list[Any] = []
            for t in terms:
                pat = f"%{t}%"
                params.extend([pat, pat])
            rows = conn.execute(
                "SELECT session_id, thread_id, title, created_at, messages "
                f"FROM sessions WHERE ({like}) "
                "ORDER BY created_at DESC LIMIT ?",
                (*params, _CANDIDATE_SCAN),
            ).fetchall()
        finally:
            conn.close()

        hits: list[dict[str, Any]] = []
        for r in rows:
            sid = str(r["session_id"])
            if exclude_session_id and sid == exclude_session_id:
                continue
            title = str(r["title"])
            messages = str(r["messages"])
            low_title, low_msgs = title.lower(), messages.lower()
            score = 0
            matched: list[str] = []
            for t in terms:
                tl = t.lower()
                if tl in low_title:
                    score += 5  # title matches are the strongest signal
                n = low_msgs.count(tl)
                if n:
                    score += min(n, 4)
                if tl in low_title or n:
                    matched.append(t)
            if not matched:
                continue
            hits.append(
                {
                    "session_id": sid,
                    "thread_id": str(r["thread_id"]),
                    "title": title or "(untitled session)",
                    "created_at": str(r["created_at"]),
                    "score": score,
                    "matched": matched,
                    "snippet": _snippet(messages if len(messages) < 200000 else title, matched),
                }
            )
        hits.sort(key=lambda h: h["score"], reverse=True)
        return hits[: max(1, limit)]
    except Exception:
        logger.debug("[transcript-recall] search failed — returning no hits", exc_info=True)
        return []


def format_transcript_block(hits: list[dict[str, Any]]) -> str:
    """Prompt-fenced untrusted block for the supervisor context. Empty string
    when there is nothing to inject. Transcript text is conversation data —
    never instructions — so it MUST ride inside the kazma:data fence."""
    if not hits:
        return ""
    from kazma_core.safety.prompt_fence import format_untrusted_block

    lines: list[str] = [
        "Past-session transcript matches (memory had nothing for this query —",
        "these are excerpts from earlier chats, newest-relevance first):",
        "",
    ]
    for i, h in enumerate(hits, 1):
        when = str(h.get("created_at") or "")
        lines.append(
            f"{i}. \"{str(h.get('title') or '(untitled)')}\""
            f" (saved {when or 'unknown date'}, matched: {', '.join(h.get('matched') or [])})"
        )
        snip = str(h.get("snippet") or "").strip()
        if snip:
            lines.append(f"   > {snip}")
    lines.append("")
    lines.append(
        "If a hit is relevant, cite what it says; open the session only if the"
        " excerpt is insufficient."
    )
    return format_untrusted_block("\n".join(lines), source="chat_history")
