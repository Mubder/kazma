"""Cross-platform conversation directory.

A *season* is one LangGraph ``thread_id``. Web sidebar rows, gateway
``active_thread.{sender}`` pointers, and SessionStore delivery context
all point at that id. Switching mouths updates the pointer + delivery
target; the checkpointed conversation is unchanged.

Platform IDs (chat_id / channel_id) never enter this module's public
records — only ``platform`` labels and titles.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "SessionEntry",
    "bind_sender_to_thread",
    "canonical_web_session",
    "create_named_session",
    "enrich_summary",
    "find_mouth_thread",
    "format_session_list",
    "infer_origin",
    "list_directory",
    "remember_sender_thread",
    "resolve_session",
    "stamp_last_platform",
]

_LAST_PLATFORM_KEY = "session.last_platform.{tid}"
_MAX_LIST = 40


@dataclass(frozen=True)
class SessionEntry:
    """One row in the operator-wide session directory."""

    session_id: str
    thread_id: str
    title: str
    platform: str
    origin: str
    message_count: int
    updated_at: str
    pinned: bool = False
    archived: bool = False

    @property
    def short_id(self) -> str:
        src = self.thread_id or self.session_id
        return src[-8:] if len(src) >= 8 else src


def infer_origin(session_id: str, title: str = "") -> str:
    """Guess which mouth minted this row (not necessarily last used)."""
    sid = (session_id or "").lower()
    t_low = (title or "").lower()
    if sid.startswith("gw-telegram") or "telegram" in t_low:
        return "telegram"
    if sid.startswith("gw-discord") or "discord" in t_low:
        return "discord"
    if sid.startswith("gw-slack") or "slack" in t_low:
        return "slack"
    if sid.startswith("gw-"):
        return "gateway"
    return "web"


def _last_platform_stored(thread_id: str) -> str:
    if not thread_id:
        return ""
    try:
        from kazma_core.config_store import get_config_store

        v = get_config_store().get(_LAST_PLATFORM_KEY.format(tid=thread_id))
        if v:
            return str(v).strip().lower()
    except Exception:
        logger.debug("[sessions] last_platform read failed", exc_info=True)
    return ""


def remember_sender_thread(sender_id: str, thread_id: str) -> None:
    """Persist which season this mouth is on (ConfigStore)."""
    if not sender_id or not thread_id:
        return
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(
            f"active_thread.{sender_id}",
            thread_id,
            category="session",
        )
    except Exception:
        logger.warning("[sessions] persist active_thread failed", exc_info=True)


def find_mouth_thread(
    sender_id: str,
    *,
    platform: str = "",
    username: str = "",
) -> str | None:
    """Return the season this mouth should continue (no minting).

    Order: ConfigStore pointer → deterministic ``gw-<platform>-<id>`` row
    → newest existing season for this platform+username. Never invents an id.
    """
    if sender_id:
        try:
            from kazma_core.config_store import get_config_store

            persisted = get_config_store().get(f"active_thread.{sender_id}")
            if persisted:
                return str(persisted)
        except Exception:
            logger.debug("[sessions] active_thread read failed", exc_info=True)

    plat = (platform or "").strip().lower()
    tail = ""
    if sender_id and ":" in sender_id:
        _p, tail = sender_id.split(":", 1)
        if not plat:
            plat = _p.strip().lower()
    elif sender_id:
        tail = sender_id
    det = f"gw-{plat}-{tail}" if plat and tail else ""

    try:
        from kazma_ui.session_manager import get_session_manager

        sm = get_session_manager()
        if det:
            sess = sm.get(det) or sm.get_by_thread_id(det)
            if sess is not None:
                return str(sess.thread_id or sess.session_id)
    except Exception:
        logger.debug("[sessions] mouth SM lookup failed", exc_info=True)
    return None


def canonical_web_session(thread_id: str) -> Any | None:
    """The one SessionManager row that should receive this thread's transcript.

    Prefer a named season with more history over an auto-titled twin
    (``Telegram · user``) so take-over cannot write the same turn twice.
    """
    if not thread_id:
        return None
    try:
        from kazma_ui.session_manager import get_session_manager

        store = get_session_manager()
    except Exception:
        return None
    seen: set[str] = set()
    candidates: list[Any] = []
    for sess in (store.get(thread_id), store.get_by_thread_id(thread_id)):
        if sess is None:
            continue
        sid = str(getattr(sess, "session_id", "") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        candidates.append(sess)
    if not candidates:
        return store.get_or_create(thread_id)

    def _score(sess: Any) -> tuple[int, int, int]:
        title = str(getattr(sess, "title", "") or "").strip()
        low = title.lower()
        auto = (not title) or low.startswith("linked ") or " · " in low
        return (
            0 if auto else 1,
            len(getattr(sess, "messages", None) or []),
            1 if sess.session_id == thread_id else 0,
        )

    return max(candidates, key=_score)


def stamp_last_platform(thread_id: str, platform: str) -> None:
    """Record which mouth last spoke on this season (ConfigStore, durable)."""
    if not thread_id or not platform:
        return
    plat = platform.strip().lower()
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(
            _LAST_PLATFORM_KEY.format(tid=thread_id),
            plat,
            category="session",
        )
    except Exception:
        logger.debug("[sessions] last_platform stamp failed", exc_info=True)


def enrich_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """Add origin / last-mouth fields to a ChatSession.to_summary() dict."""
    out = dict(summary)
    sid = str(out.get("session_id") or "")
    tid = str(out.get("thread_id") or sid)
    origin = infer_origin(sid, str(out.get("title") or ""))
    last = _last_platform_stored(tid) or origin
    out["origin"] = origin
    out["last_platform"] = last
    out["platform"] = last
    out["thread_id"] = tid
    out["short_id"] = tid[-8:] if len(tid) >= 8 else tid
    return out


def _entry_from_chat_session(sess: Any) -> SessionEntry:
    sid = str(getattr(sess, "session_id", "") or "")
    tid = str(getattr(sess, "thread_id", "") or sid)
    title = str(getattr(sess, "title", "") or "") or sid[:8]
    origin = infer_origin(sid, title)
    last = _last_platform_stored(tid) or origin
    msgs = getattr(sess, "messages", None) or []
    return SessionEntry(
        session_id=sid,
        thread_id=tid,
        title=title,
        platform=last,
        origin=origin,
        message_count=len(msgs) if isinstance(msgs, list) else int(
            getattr(sess, "message_count", 0) or 0
        ),
        updated_at=str(getattr(sess, "updated_at", "") or getattr(sess, "created_at", "") or ""),
        pinned=bool(getattr(sess, "pinned", False)),
        archived=bool(getattr(sess, "archived", False)),
    )


def list_directory(*, include_archived: bool = False, limit: int = _MAX_LIST) -> list[SessionEntry]:
    """Newest-first operator session list (SessionManager is the SoT)."""
    try:
        from kazma_ui.session_manager import get_session_manager

        store = get_session_manager()
        rows = store.list_all(include_archived=include_archived)
    except Exception:
        logger.debug("[sessions] SessionManager unavailable", exc_info=True)
        return []
    entries = [_entry_from_chat_session(s) for s in rows]
    if limit and limit > 0:
        return entries[:limit]
    return entries


def resolve_session(
    query: str,
    *,
    current_thread_id: str | None = None,
    include_archived: bool = False,
) -> SessionEntry | None:
    """Resolve a user pick (index, id, suffix, or unique title)."""
    raw = (query or "").strip()
    if not raw:
        return None
    q = raw.lower()
    if q in {"here", "current", ".", "this"}:
        if not current_thread_id:
            return None
        return resolve_session(current_thread_id, include_archived=True)

    entries = list_directory(include_archived=include_archived, limit=_MAX_LIST)
    if q.isdigit():
        idx = int(q)
        if 1 <= idx <= len(entries):
            return entries[idx - 1]
        return None

    for e in entries:
        if raw == e.session_id or raw == e.thread_id:
            return e
        if q == e.session_id.lower() or q == e.thread_id.lower():
            return e

    if len(raw) >= 6:
        suffix_hits = [
            e for e in entries
            if e.session_id.lower().endswith(q) or e.thread_id.lower().endswith(q)
        ]
        if len(suffix_hits) == 1:
            return suffix_hits[0]

    title_hits = [e for e in entries if q in e.title.lower()]
    if len(title_hits) == 1:
        return title_hits[0]
    return None


def _relative(iso: str) -> str:
    if not iso:
        return ""
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        sec = max(0, int((datetime.now(UTC) - ts).total_seconds()))
    except Exception:
        return ""
    if sec < 60:
        return "just now"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


def format_session_list(
    entries: list[SessionEntry],
    *,
    current_thread_id: str | None = None,
) -> str:
    """Plain-text list safe for Telegram / Discord / Slack / TUI."""
    if not entries:
        return (
            "No seasons yet.\n"
            "Send a message or `/session new` to start one. "
            "The same list appears in the Web chat sidebar."
        )
    lines = [
        "Your seasons — pick any of these from Web, Telegram, Discord, or Slack.",
        "",
    ]
    for i, e in enumerate(entries, start=1):
        mark = " ← here" if current_thread_id and e.thread_id == current_thread_id else ""
        when = _relative(e.updated_at)
        meta = f"{e.platform} · {e.message_count} msgs"
        if when:
            meta += f" · {when}"
        # Avoid [id] / [name] — Rich markup and some chat clients eat those.
        lines.append(f"{i}. #{e.short_id}  {e.title} ({meta}){mark}")
    lines.extend(
        [
            "",
            "/session 2 — continue that season here (take over)",
            "/session new <name> — start a fresh season",
            "Web: open /chat?s=<id> or pick it in the sidebar.",
        ]
    )
    return "\n".join(lines)


def _ensure_web_row(*, session_id: str, thread_id: str, title: str, platform: str) -> None:
    try:
        from kazma_ui.session_manager import ChatSession, get_session_manager

        store = get_session_manager()
        sess = store.get(session_id) or store.get_by_thread_id(thread_id)
        if sess is None:
            sess = ChatSession(
                session_id=session_id,
                thread_id=thread_id,
                title=title,
            )
            store.put(sess)
        else:
            if not sess.thread_id:
                sess.thread_id = thread_id
            if title and (not sess.title or sess.title.startswith("Linked ")):
                sess.title = title
            store.put(sess)
    except Exception:
        logger.debug("[sessions] ensure web row failed", exc_info=True)
    stamp_last_platform(thread_id, platform)


async def bind_sender_to_thread(
    sender_id: str,
    thread_id: str,
    *,
    platform: str,
    delivery_ctx: dict[str, Any] | None = None,
    session_store: Any = None,
) -> SessionEntry | None:
    """Point this mouth at *thread_id* and move delivery here (take-over).

    Updates ConfigStore ``active_thread.{sender}``, merges current platform
    context into SessionStore (so replies go to this chat), and stamps
    last-mouth. Does not put chat_id into graph state.
    """
    if not sender_id or not thread_id:
        return None
    remember_sender_thread(sender_id, thread_id)

    if session_store is not None and delivery_ctx is not None:
        try:
            existing = dict(await session_store.get(thread_id) or {})
            # Keep prior keys; overwrite delivery fields from this inbound.
            merged = {**existing, **dict(delivery_ctx)}
            merged["thread_id"] = thread_id
            merged.setdefault("sender_id", sender_id)
            await session_store.put(thread_id, merged)
        except Exception:
            logger.warning("[sessions] SessionStore take-over merge failed", exc_info=True)

    stamp_last_platform(thread_id, platform)

    entry = resolve_session(thread_id, include_archived=True)
    if entry is not None:
        return entry
    title = (delivery_ctx or {}).get("username") or thread_id[:8]
    _ensure_web_row(
        session_id=thread_id,
        thread_id=thread_id,
        title=str(title),
        platform=platform,
    )
    return SessionEntry(
        session_id=thread_id,
        thread_id=thread_id,
        title=str(title),
        platform=platform,
        origin=infer_origin(thread_id),
        message_count=0,
        updated_at=datetime.now(UTC).isoformat(),
    )


def create_named_session(
    *,
    platform: str,
    sender_id: str = "",
    title: str = "",
) -> SessionEntry:
    """Mint a new season and register it in the directory."""
    plat = (platform or "web").strip().lower() or "web"
    if plat == "web":
        sid = str(uuid.uuid4())
    else:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", sender_id or "user")[:40]
        sid = f"gw-{plat}-{safe}-{uuid.uuid4().hex[:8]}"
    nice = (title or "").strip()[:120]
    if not nice:
        nice = f"{plat.capitalize()} session"
    _ensure_web_row(session_id=sid, thread_id=sid, title=nice, platform=plat)
    return SessionEntry(
        session_id=sid,
        thread_id=sid,
        title=nice,
        platform=plat,
        origin=plat,
        message_count=0,
        updated_at=datetime.now(UTC).isoformat(),
    )
