"""Bring your history: a ChatGPT or Claude export into Kazma's memory (REMAINING 4.2).

Both services hand you a ``.zip`` (ChatGPT: Settings -> Data controls ->
Export data; Claude: Settings -> Privacy -> Export data) holding
``conversations.json``. :func:`read_export` reads it -- the zip, or the JSON
file alone -- and :func:`import_history` writes each question-and-answer
turn into memory as an episode, through the one episode writer
(``dual_write.write_episode``), so every memory rule holds for it: the forget
ledger is asked first, the vector is made once, the turn keeps its own time
(an old turn is history, never "now").

- **Formats.** ChatGPT: each conversation's ``mapping`` walked from its
  ``current_node`` back to the root (the branch the person last saw), only
  the person's and ChatGPT's visible text (no tool calls, tool output,
  hidden instructions or images). Claude: each conversation's
  ``chat_messages``, ``human`` and ``assistant`` text. A question is paired
  with the answer(s) after it.
- **Where it lands.** Each conversation is a memory chat of its own,
  ``import-<chatgpt|claude>-<its id>``; its turns are numbered from 1 and
  marked ``source: history_import`` with the conversation's title. The
  Memory page lists them and Forget forgets them like any memory.
- **Again is harmless.** A turn's id is made from its chat, number and
  question (``dual_write._episode_id``): one already there is skipped before
  anything is embedded.
- **Bounded.** ``conversations.json`` is read at most :data:`MAX_JSON_BYTES`
  (from a zip, the member's declared size is checked first); each side of a
  turn is cut by the writer at 4,000 characters.
- **Nothing else.** No fact is extracted (that pass calls the model);
  recall finds the turns by meaning and words, and the weekly summaries
  cover the imported weeks over the next hours (model calls, two weeks at a
  time), as they do for every turn.

Callers: ``kazma import <file>`` and the Memory page's "Bring your history"
(``kazma_ui/history_import_api.py``). It blocks: never call it on the loop.
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import logging
import re
import sqlite3
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "MAX_JSON_BYTES",
    "SOURCE",
    "History",
    "ImportReport",
    "ImportRefused",
    "import_history",
    "read_export",
]

#: The episode source every imported turn carries (``dual_write``: written
#: after the fact, so it is episodic at once, never the live working buffer).
SOURCE = "history_import"
MAX_JSON_BYTES = 1 << 30
_CHAT_ID = re.compile(r"[^A-Za-z0-9-]")
_TITLE_MAX = 200


class ImportRefused(ValueError):
    """The file is not an export Kazma can read (the message says why)."""


@dataclass(frozen=True)
class _Turn:
    user: str
    assistant: str
    at: float


@dataclass
class _Chat:
    chat_id: str
    title: str
    turns: list[_Turn] = field(default_factory=list)


@dataclass
class History:
    """What an export holds: its source (``chatgpt`` or ``claude``) and chats."""

    source: str
    chats: list[_Chat]

    @property
    def turn_count(self) -> int:
        return sum(len(c.turns) for c in self.chats)

    def span(self) -> tuple[float, float] | None:
        times = [t.at for c in self.chats for t in c.turns if t.at > 0]
        return (min(times), max(times)) if times else None

    def summary(self) -> dict[str, Any]:
        span = self.span()
        return {"source": self.source, "chats": len(self.chats), "turns": self.turn_count,
                "first": span[0] if span else None, "last": span[1] if span else None}


@dataclass
class ImportReport:
    """What :func:`import_history` did, turn by turn."""

    written: int = 0
    already: int = 0
    refused: int = 0
    failed: int = 0
    stopped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


# -- reading ------------------------------------------------------------------------


def _json_bytes(path: Path) -> bytes:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            members = [m for m in archive.infolist()
                       if not m.is_dir() and m.filename.replace("\\", "/").rsplit("/", 1)[-1] == "conversations.json"]
            if not members:
                raise ImportRefused("the zip holds no conversations.json (is it a ChatGPT or Claude export?)")
            member = min(members, key=lambda m: m.filename.count("/"))
            if member.file_size > MAX_JSON_BYTES:
                raise ImportRefused(f"conversations.json is {member.file_size} bytes, more than Kazma reads")
            with archive.open(member) as fh:
                data = fh.read(MAX_JSON_BYTES + 1)
    else:
        with path.open("rb") as fh:
            data = fh.read(MAX_JSON_BYTES + 1)
    if len(data) > MAX_JSON_BYTES:
        raise ImportRefused("conversations.json is larger than Kazma reads")
    return data


def read_export(path: str | Path) -> History:
    """The export at *path* (a zip, or ``conversations.json``). Raises
    :class:`ImportRefused` for anything that is not one."""
    path = Path(path)
    try:
        raw = _json_bytes(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ImportRefused(f"the file could not be read ({type(exc).__name__})") from exc
    try:
        data = json.loads(io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8-sig").read())
    except (ValueError, UnicodeDecodeError) as exc:
        raise ImportRefused("conversations.json is not JSON") from exc
    if not isinstance(data, list):
        raise ImportRefused("conversations.json does not hold a list of conversations")
    sample = next((c for c in data if isinstance(c, dict)), None)
    if sample is None:
        return History("chatgpt", [])
    if "mapping" in sample:
        return History("chatgpt", [c for c in (_chatgpt_chat(x) for x in data if isinstance(x, dict)) if c.turns])
    if "chat_messages" in sample:
        return History("claude", [c for c in (_claude_chat(x) for x in data if isinstance(x, dict)) if c.turns])
    raise ImportRefused("this is not a ChatGPT or Claude export (no mapping or chat_messages)")


def _clean(text: Any) -> str:
    return str(text or "").replace("\x00", "").strip()


def _chat_id(raw: Any) -> str:
    return _CHAT_ID.sub("", str(raw or ""))[:64]


def _pair(messages: list[tuple[str, str, float]], fallback: float) -> list[_Turn]:
    """``(role, text, at)`` in order -> turns: a question with the answers after it."""
    turns: list[_Turn] = []
    question, answers, at = "", [], 0.0
    for role, text, when in messages:
        if role == "user":
            if question or answers:
                turns.append(_Turn(question, "\n\n".join(answers), at or fallback))
            question, answers, at = text, [], when
        else:
            answers.append(text)
            at = at or when
    if question or answers:
        turns.append(_Turn(question, "\n\n".join(answers), at or fallback))
    return [t for t in turns if t.user or t.assistant]


def _epoch(value: Any) -> float:
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    if isinstance(value, str) and value:
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _chatgpt_text(message: dict[str, Any]) -> str:
    content = message.get("content") or {}
    if not isinstance(content, dict) or content.get("content_type") not in ("text", "multimodal_text"):
        return ""
    parts = content.get("parts") or []
    return _clean("\n".join(p for p in parts if isinstance(p, str)))


def _chatgpt_chat(raw: dict[str, Any]) -> _Chat:
    mapping = raw.get("mapping") if isinstance(raw.get("mapping"), dict) else {}
    node = raw.get("current_node")
    path: list[dict[str, Any]] = []
    seen: set[str] = set()
    while isinstance(node, str) and node in mapping and node not in seen:
        seen.add(node)
        entry = mapping[node] if isinstance(mapping[node], dict) else {}
        if isinstance(entry.get("message"), dict):
            path.append(entry["message"])
        node = entry.get("parent")
    messages: list[tuple[str, str, float]] = []
    for message in reversed(path):
        role = str((message.get("author") or {}).get("role") or "")
        meta = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        if role not in ("user", "assistant") or meta.get("is_visually_hidden_from_conversation"):
            continue
        if role == "assistant" and str(message.get("recipient") or "all") != "all":
            continue  # a call to one of ChatGPT's tools, not an answer
        text = _chatgpt_text(message)
        if text:
            messages.append((role, text, _epoch(message.get("create_time"))))
    created = _epoch(raw.get("create_time"))
    chat_id = _chat_id(raw.get("conversation_id") or raw.get("id") or "")
    return _Chat(chat_id, _clean(raw.get("title"))[:_TITLE_MAX], _pair(messages, created))


def _claude_chat(raw: dict[str, Any]) -> _Chat:
    messages: list[tuple[str, str, float]] = []
    for message in raw.get("chat_messages") or []:
        if not isinstance(message, dict):
            continue
        sender = str(message.get("sender") or "")
        role = "user" if sender == "human" else "assistant" if sender == "assistant" else ""
        if not role:
            continue
        text = _clean(message.get("text"))
        if not text:
            blocks = message.get("content") if isinstance(message.get("content"), list) else []
            text = _clean("\n".join(str(b.get("text") or "") for b in blocks
                                    if isinstance(b, dict) and b.get("type") == "text"))
        if text:
            messages.append((role, text, _epoch(message.get("created_at"))))
    return _Chat(_chat_id(raw.get("uuid") or ""), _clean(raw.get("name"))[:_TITLE_MAX],
                 _pair(messages, _epoch(raw.get("created_at"))))


# -- writing -------------------------------------------------------------------------


def _session_of(source: str, chat: _Chat, index: int) -> str:
    return f"import-{source}-{chat.chat_id or f'n{index}'}"


def _existing(ids: list[str]) -> set[str]:
    """Which of *ids* memory already holds (read-only; none when no database yet)."""
    from kazma_core.paths import primary_memory_db

    db = Path(primary_memory_db())
    if not db.exists() or not ids:
        return set()
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=10)
    try:
        held: set[str] = set()
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            rows = conn.execute(f"SELECT id FROM episodes WHERE id IN ({','.join('?' for _ in chunk)})", chunk)
            held.update(r[0] for r in rows)
        return held
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def import_history(history: History, *, tenant_id: str = "default",
                   progress: Callable[[int, int], None] | None = None,
                   should_stop: Callable[[], bool] | None = None) -> ImportReport:
    """Write every turn of *history* into memory; returns what happened.
    *progress* hears (turns done, turns in all); *should_stop* ends the run
    between turns (what was written stays)."""
    from kazma_core.memory.dual_write import episode_row, write_episode

    report = ImportReport()
    total, done = history.turn_count, 0
    for index, chat in enumerate(history.chats, 1):
        session = _session_of(history.source, chat, index)
        meta = {"imported_from": history.source, "chat_title": chat.title}
        rows = [episode_row(session_id=session, turn_number=n, user_text=t.user, assistant_text=t.assistant,
                            source=SOURCE, created_at=t.at or None, metadata=meta)
                for n, t in enumerate(chat.turns, 1)]
        held = _existing([r["id"] for r in rows])
        for n, (turn, row) in enumerate(zip(chat.turns, rows), 1):
            if should_stop is not None and should_stop():
                report.stopped = True
                logger.info("[history_import] stopped after %d of %d turns", done, total)
                return report
            if row["id"] in held:
                report.already += 1
            else:
                status, _eid = write_episode(session_id=session, turn_number=n, user_text=turn.user,
                                             assistant_text=turn.assistant, tenant_id=tenant_id, source=SOURCE,
                                             created_at=turn.at or None, metadata=meta)
                if status == "written":
                    report.written += 1
                elif status == "refused":
                    report.refused += 1
                else:
                    report.failed += 1
            done += 1
            if progress is not None:
                progress(done, total)
    logger.info("[history_import] %s export: %d written, %d already there, %d kept out, %d failed",
                history.source, report.written, report.already, report.refused, report.failed)
    return report
