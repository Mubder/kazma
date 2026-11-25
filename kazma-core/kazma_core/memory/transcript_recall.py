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
  * Reads the chat store wherever it is -- Postgres ``kazma_chat_sessions``
    or SQLite ``chat_sessions.db`` -- read-only, through
    :mod:`kazma_core.memory.chat_history` (kazma-core must not import
    kazma-ui's SessionManager). Until 2026-09-26 it opened only the SQLite
    file, which on a Postgres install is a leftover from before the switch.
  * Best-effort and NEVER raises — a missing DB, a locked file, or a schema
    drift returns ``[]`` and the turn proceeds exactly as before.
  * Kill-switch: ``KAZMA_TRANSCRIPT_RECALL=0`` env or ConfigStore
    ``memory.transcript_fallback=false`` (live-read, no restart).
  * Transcript text is UNTRUSTED conversation data — callers must inject it
    via :func:`format_transcript_block` (prompt-fenced), never raw.
  * A session is a hit when it holds MORE THAN HALF of the question's content
    words (``memory/query_terms.py``: stopwords dropped, Arabic folded), each
    as a whole word. It fires whenever memory recall comes back empty, which
    since the relevance floor (2026-09-26) is every question memory has no
    answer for -- and it used to accept one substring ("size" in "sizeable")
    from a list that kept "what's", "is" and "my".
  * Every session that may be one is checked, best ranked first, before the
    cut to the hits wanted; a word is looked for in every spelling that folds
    to it (``documents.arabic.spelling_pattern``) and confirmed with the fold.
  * A ``system`` row is not conversation: ``/compact`` on the web stored the
    model's own prompt, "About me" and recalled memory as such rows until 25
    Nov 2026, and they are not read.
"""

from __future__ import annotations

import functools
import logging
import os
import re
from pathlib import Path
from typing import Any

from kazma_core.spaces.scope import space_named_or_bound

logger = logging.getLogger(__name__)

__all__ = [
    "format_transcript_block",
    "search_transcripts",
    "transcript_fallback_enabled",
]

_MAX_HITS = 3
_SNIPPET_CHARS = 320
#: How far around a match its word is read (a word, its prefixes and marks).
_WORD_REACH = 64


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
    """The question's content words, most specific (longest) first."""
    from kazma_core.memory.query_terms import content_terms

    return sorted(content_terms(str(query or "")), key=len, reverse=True)[:max_terms]


def _spelled(term: str) -> list[str]:
    """The spellings a search for *term* asks for."""
    from kazma_core.memory.query_terms import search_terms

    return search_terms(term) or [term]


@functools.lru_cache(maxsize=128)
def _spelled_anywhere(words: tuple[str, ...]) -> re.Pattern[str]:
    from kazma_core.documents.arabic import spelling_pattern

    return re.compile("|".join(filter(None, map(spelling_pattern, words))) or r"(?!)")


def _where(term: str, text: str) -> int:
    """Where *text* holds *term* as a whole word (any written form), or -1.

    The question is :func:`query_terms.mentions`', which folds what it reads:
    a whole chat is not folded for it (it folds some 9 MB a second), only the
    words its letters stand in.
    """
    from kazma_core.memory.query_terms import mentions

    words = _spelled(term)
    refused: set[str] = set()
    for match in _spelled_anywhere(tuple(words)).finditer(text):
        start, end = match.span()
        near = text[max(0, start - _WORD_REACH): end + 1]
        word = near[_word_start(near, min(start, _WORD_REACH)):]
        if word in refused:
            continue
        if mentions(word, words):
            return start
        refused.add(word)
    return -1


def _word_start(text: str, at: int) -> int:
    """Where the word that holds position *at* begins: after the last space or
    quotation mark before it."""
    return max(text.rfind(sep, 0, at) for sep in (" ", "\n", "\t", '"')) + 1


def _present(terms: list[str], text: str) -> dict[str, int]:
    """The *terms* that *text* holds as whole words, and where."""
    found = {t: _where(t, text) for t in terms}
    return {t: at for t, at in found.items() if at >= 0}


def _snippet(text: str, at: int) -> str:
    """The window of *text* around *at* (its beginning when it is not there)."""
    if at < 0:
        return text[:_SNIPPET_CHARS].strip()
    start = max(0, at - _SNIPPET_CHARS // 2)
    end = min(len(text), at + _SNIPPET_CHARS)
    return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")


_ESCAPED = re.compile(r"\\(?:u00[01][0-9a-f]|.)")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\n", "b": " ", "f": " "}


def _readable(messages: str) -> str:
    """Stored messages with their JSON escapes read: the word after a line
    break ("\\nshoe") begins there, and a snippet shows the break."""
    return _ESCAPED.sub(lambda m: _ESCAPES.get(m[0][1:], m[0][1:] if len(m[0]) == 2 else " "), messages)


def search_transcripts(
    query: str,
    *,
    tenant_id: str | None = None,
    exclude_session_id: str | None = None,
    db_path: str | Path | None = None,
    limit: int = _MAX_HITS,
) -> list[dict[str, Any]]:
    """Rank past chat sessions by query-term overlap. Never raises.

    Every session of the tenant is ranked -- in whichever store holds the
    chats (:func:`kazma_core.memory.chat_history.search_sessions`) -- title
    matches 5, message occurrences up to 4 per term -- and checked in that
    order until *limit* are hits: a hit must hold more than half of the
    content words as whole words, in what the user did not take back. ``db_path``
    searches that SQLite file instead. Returns up to ``limit`` hits:
    {session_id, thread_id, title, created_at, score, matched, snippet}; empty
    when the store is missing or no session is about the question.
    """
    tenant_id = space_named_or_bound(tenant_id)
    try:
        from kazma_core.memory.episode_text import is_small_talk

        # An empty recall runs this fallback: a greeting must not bring
        # back every past chat that said hello (plan W5).
        terms = [] if is_small_talk(query, None) else _terms(query)
        if not terms:
            return []
        from kazma_core.memory.chat_history import search_sessions

        reader = _Reader(terms, tenant_id)
        return search_sessions(
            [_spelled(t) for t in terms],
            tenant_id=tenant_id,
            exclude_session_id=exclude_session_id,
            limit=max(1, limit),
            sqlite_path=Path(db_path) if db_path is not None else None,
            at_least=reader.enough,
            confirm=reader.hit,
        )
    except Exception:
        logger.debug("[transcript-recall] search failed — returning no hits", exc_info=True)
        return []


class _Reader:
    """Reads one ranked chat: a hit, or None."""

    def __init__(self, terms: list[str], tenant_id: str) -> None:
        self.terms = terms
        self.tenant_id = tenant_id
        #: More than half of the question, or the chat is not about it.
        self.enough = len(terms) // 2 + 1
        self._copied: dict[str, float] | None = None

    def hit(self, row: dict[str, Any]) -> dict[str, Any] | None:
        title = str(row.get("title") or "")
        stored = str(row.get("messages") or "")
        # The words first: they refuse most rows, and what is taken out of a
        # chat below can only take words out with it.
        found = self._read(title, stored)
        if found is None:
            return None
        kept = self._kept(str(row.get("session_id") or ""), str(row.get("thread_id") or ""), stored)
        if kept != stored:
            found = self._read(title, kept)
            if found is None:
                return None
        matched, snippet = found
        return {
            "session_id": str(row.get("session_id") or ""),
            "thread_id": str(row.get("thread_id") or ""),
            "title": title or "(untitled session)",
            "created_at": str(row.get("created_at") or ""),
            "score": int(row.get("score") or 0),
            "matched": matched,
            "snippet": snippet,
        }

    def _read(self, title: str, messages: str) -> tuple[list[str], str] | None:
        """(the terms it holds, a snippet), or None when it holds too few."""
        text = _readable(messages)
        in_text = _present(self.terms, text)
        in_title = _present([t for t in self.terms if t not in in_text], title)
        matched = [t for t in self.terms if t in in_text or t in in_title]
        if len(matched) < self.enough or not messages:
            return None
        return matched, _snippet(text, next((in_text[t] for t in self.terms if t in in_text), -1))

    def _kept(self, session_id: str, thread_id: str, messages: str) -> str:
        """The chat's messages a search may read, or ""."""
        from kazma_core.memory.forget import forgotten_in_chat

        # What the user took back stays out (plan U1): a chat they keep out of
        # memory is skipped, a forgotten turn is not read.
        whole, forgotten = forgotten_in_chat(self.tenant_id, (session_id, thread_id))
        if whole:
            return ""
        if forgotten:
            messages = _without_turns(messages, forgotten)
        since = self._copied_here().get(thread_id or session_id)
        if since is not None:
            # A chat copied here from another space (Spaces S2-12): what was
            # said before was said there, so it is not read here.
            messages = _said_here(messages, since)
        return _without_system(messages)

    def _copied_here(self) -> dict[str, float]:
        if self._copied is None:
            from kazma_core.spaces.threads import copied_threads

            self._copied = copied_threads()
        return self._copied


def _without_system(messages: str) -> str:
    """The chat's stored messages without its ``system`` rows."""
    import json

    from kazma_core.memory.chat_history import normalize_message

    if '"system"' not in messages:
        return messages
    try:
        items = json.loads(messages)
    except (TypeError, ValueError):
        return messages
    if not isinstance(items, list):
        return messages
    kept = [item for item in items if normalize_message(item).get("role") != "system"]
    return json.dumps(kept, ensure_ascii=False) if len(kept) != len(items) else messages


def _without_turns(messages: str, forgotten: set[tuple[int, str]]) -> str:
    """The chat's stored messages without the turns the user forgot.

    A turn is left out when its number OR its question matches the ledger:
    a chat edited since may number its turns differently, and hiding one turn
    too many is the side to err on. Messages that cannot be read are all left
    out -- which of them were forgotten cannot be told.
    """
    import json

    from kazma_core.memory.chat_history import normalize_message
    from kazma_core.memory.consolidator import message_text
    from kazma_core.memory.forget import question_sha

    try:
        items = json.loads(messages)
    except (TypeError, ValueError):
        return ""
    if not isinstance(items, list):
        return ""
    numbers = {turn for turn, _sha in forgotten}
    shas = {sha for _turn, sha in forgotten}
    kept: list[Any] = []
    turn = 0
    skipping = False
    for item in items:
        msg = normalize_message(item)
        if msg.get("role") == "user":
            turn += 1
            skipping = turn in numbers or question_sha(message_text(msg.get("content"))) in shas
        if not skipping:
            kept.append(item)
    return json.dumps(kept, ensure_ascii=False)


def _said_here(messages: str, since: float) -> str:
    """The chat's stored messages from the turns asked since *since*, or ""
    when there are none (or they cannot be read)."""
    import json

    from kazma_core.memory.chat_history import said_since

    try:
        items = json.loads(messages)
    except (TypeError, ValueError):
        return ""
    kept = said_since(items, since) if isinstance(items, list) else []
    return json.dumps(kept, ensure_ascii=False) if kept else ""


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
