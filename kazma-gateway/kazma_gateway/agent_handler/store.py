"""Store submodule — thread resolver, in-memory store, state builder, and context persistence."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from kazma_gateway.gateway import IncomingMessage, SessionStore

logger = logging.getLogger(__name__)

__all__: list[str] = []

_MAX_DICT_ENTRIES = 10_000

_PLATFORM_KEYS = frozenset(
    {
        # Telegram
        "chat_id",
        "user_id",
        "message_id",
        "update_id",
        "chat_type",
        "message_thread_id",
        # Discord / Slack
        "channel_id",
        "guild_id",
        "team_id",
        "thread_ts",
        "message_ts",
        "discord_thread_id",
        "thread_hint",
    }
)

# Per-turn flags that must NOT stick across messages (session merge).
# Without this, a single voice note sets voice_transcribed=True forever and
# every later text reply gets an unwanted TTS voice note.
_EPHEMERAL_CTX_KEYS = frozenset(
    {
        "voice_transcribed",
        "voice_bytes",
        "voice_filename",
        "stt_provider",
        "stt_language",
        "media",
        "tool_used",
        "parse_mode",
        "reply_markup",
        "components",
    }
)


def _sender_suffix(msg: IncomingMessage) -> str:
    """Platform-local sender token (the part after the first colon)."""
    if msg.sender_id and ":" in msg.sender_id:
        return msg.sender_id.split(":", 1)[1]
    return (msg.sender_id or "").strip()


def _native_thread_id(msg: IncomingMessage) -> str | None:
    """Map a platform-native topic/thread onto a Kazma thread_id.

    Telegram forum topics, Slack reply threads, and Discord thread objects
    stamp ``thread_hint`` at parse time. This is the Phase-1 intake selector:
    native threading wins over the ``active_thread.{sender}`` mouth pointer.
    Bare messages (no hint) fall through to the existing pointer.
    """
    ctx = msg.context_metadata or {}
    hint = ctx.get("thread_hint")
    if hint:
        return str(hint)

    if msg.platform == "telegram":
        topic = ctx.get("message_thread_id")
        if topic not in (None, "", 0, "0"):
            sender = _sender_suffix(msg)
            if sender:
                return f"gw-telegram-{sender}-topic-{topic}"

    if msg.platform == "slack":
        thread_ts = ctx.get("thread_ts")
        msg_ts = ctx.get("message_ts")
        if thread_ts and str(thread_ts) != str(msg_ts or ""):
            sender = _sender_suffix(msg)
            if sender:
                safe = str(thread_ts).replace(".", "-")
                return f"gw-slack-{sender}-thread-{safe}"

    if msg.platform == "discord":
        did = ctx.get("discord_thread_id")
        if did:
            sender = _sender_suffix(msg) or str(ctx.get("user_id") or "")
            if sender:
                return f"gw-discord-{sender}-{did}"
    return None


def _resolve_thread(msg: IncomingMessage) -> str:
    """Resolve or generate a stable thread_id for a message.

    Resolution order:
        1. Existing thread_id in context_metadata (from session store)
        2. Platform-native topic / thread hint (Telegram forum, Slack thread)
        3. Durable mouth pointer + existing sidebar season (never mint a twin)
        4. Platform-prefixed deterministic ID from sender_id
        5. Fresh UUID4 (last resort)

    Args:
        msg: The incoming message.

    Returns:
        A stable thread_id string.
    """
    ctx = msg.context_metadata

    # 1. Already resolved (e.g. from a previous message in the session)
    if ctx.get("thread_id"):
        return ctx["thread_id"]

    # 2. Native topic / thread (intake selector). active_thread stays the
    # default for bare messages with no platform-native hint.
    native = _native_thread_id(msg)
    if native:
        return native

    # 3. Durable mouth pointer + existing sidebar season (never mint a twin)
    if msg.sender_id:
        try:
            from kazma_core.sessions.directory import find_mouth_thread

            username = str(ctx.get("username") or ctx.get("display_name") or "")
            found = find_mouth_thread(
                msg.sender_id,
                platform=msg.platform,
                username=username,
            )
            if found:
                return found
        except Exception:
            logger.debug("[store] find_mouth_thread failed", exc_info=True)

    # 3. Deterministic from sender_id (e.g. "telegram:12345" → "gw-telegram-12345")
    if msg.sender_id and ":" in msg.sender_id:
        platform, sender = msg.sender_id.split(":", 1)
        return f"gw-{platform}-{sender}"

    # 4. Fallback UUID
    return f"gw-{uuid.uuid4().hex[:12]}"


class _InMemoryStore(SessionStore):
    """Trivial in-memory store — no persistence, for testing/fallback.

    Tracks a monotonic timestamp per entry so that TTL-based eviction
    (``evict_older_than``) works the same way as the SQLite backend.
    """

    def __init__(self) -> None:
        super().__init__()
        self._data: dict[str, dict[str, Any]] = {}
        self._timestamps: dict[str, float] = {}

    async def get(self, thread_id: str) -> dict[str, Any]:
        return dict(self._data.get(thread_id, {}))

    async def put(self, thread_id: str, context: dict[str, Any]) -> None:
        self._data[thread_id] = dict(context)
        self._timestamps[thread_id] = time.monotonic()

    async def delete(self, thread_id: str) -> None:
        self._data.pop(thread_id, None)
        self._timestamps.pop(thread_id, None)

    async def evict_older_than(self, seconds: float) -> int:
        """Remove entries whose last ``put`` is older than ``seconds`` ago."""
        cutoff = time.monotonic() - seconds
        stale = [tid for tid, ts in self._timestamps.items() if ts < cutoff]
        for tid in stale:
            self._data.pop(tid, None)
            self._timestamps.pop(tid, None)
        return len(stale)


async def _build_initial_state(msg: IncomingMessage, store: SessionStore) -> dict[str, Any]:
    """Build a platform-agnostic graph state from an IncomingMessage.

    Side-effects:
        - Stores full context_metadata in SessionStore via store.put()
        - The graph state's _gateway block contains ZERO platform IDs

    Args:
        msg:   The incoming message from the gateway queue.
        store: SessionStore for persisting platform context.

    Returns:
        LangGraph-compatible initial state dict.
    """
    ctx = msg.context_metadata

    # Resolve thread_id using standardized resolver
    thread_id = _resolve_thread(msg)

    # Store full platform context in SessionStore (NEVER enters the graph).
    # Adapters only set sender_id as the top-level IncomingMessage field,
    # never inside context_metadata — but hitl.py's cross-thread approval
    # ownership check reads original_sender from the persisted context, so
    # without this it always sees "" and the authz guard never fires.
    #
    # Merge with any existing session keys (e.g. active_agent_skill from
    # /skill activate) so a normal chat turn does not wipe them.
    def _mutate_ctx(existing: dict[str, Any]) -> dict[str, Any]:
        base = {
            k: v for k, v in existing.items() if k not in _EPHEMERAL_CTX_KEYS
        }
        persisted = {**base, **dict(ctx)}
        persisted.setdefault("sender_id", msg.sender_id)
        return persisted

    try:
        await store.update(thread_id, _mutate_ctx)
    except Exception:
        persisted_ctx = dict(ctx)
        persisted_ctx.setdefault("sender_id", msg.sender_id)
        await store.put(thread_id, persisted_ctx)

    # Build graph state with ONLY platform-agnostic fields
    try:
        from kazma_core.agent.state import initial_supervisor_state

        from kazma_core.memory.config import resolve_tenant_id

        state = initial_supervisor_state(
            thread_id=thread_id,
            tenant_id=resolve_tenant_id(msg.platform, msg.sender_id or ""),
        )
    except ImportError:
        state = {"thread_id": thread_id, "messages": []}

    state["_gateway"] = {
        "thread_id": thread_id,
        "display_name": ctx.get("username") or "unknown",
        "platform": msg.platform,
        # Platform-prefixed delivery target (e.g. "telegram:<chat_id>"), carried
        # in the internal routing block so tools can capture it for later async
        # delivery (cron reminders) without putting the raw chat_id into graph
        # state as a top-level key (platform-isolation invariant, AGENTS.md §2).
        "delivery_target": _build_target_id(msg.platform, msg.context_metadata),
    }

    # Attach the user message — multimodal when media is present.
    # Plain text (no attachments) stays a string; images become an OpenAI
    # vision content list; documents are persisted and referenced as text.
    try:
        import asyncio as _asyncio

        from kazma_gateway.agent_handler.attachments import build_user_content

        # to_thread: URL attachments (Slack/Discord) do sync HTTP + disk
        # persist + document parse inside build_user_content — running it
        # on the loop stalls every platform for up to the fetch timeout.
        user_content = await _asyncio.to_thread(
            build_user_content, msg.text, msg.attachments
        )
    except Exception:  # noqa: BLE001 — never block a turn on attachment building
        user_content = msg.text
    state["messages"] = [{"role": "user", "content": user_content}]

    # Long-task continue protocol: inject salvaged context from a prior
    # budget-exhausted turn so "Proceed" does not re-do the same work.
    # GATED by reply shape (deep-audit 2026-08-19 Telegram desync): the
    # directive is only injected when the user's message reads as a
    # continuation — a fresh command must NEVER be prefixed with the
    # "do not re-do / produce a final report" framing.
    try:
        from kazma_core.agent.long_task import consume_continue_context

        cont = consume_continue_context(thread_id, user_text=msg.text)
        if cont:
            state["messages"] = [
                {"role": "system", "content": cont},
                {"role": "user", "content": user_content},
            ]
            import logging as _logging

            _logging.getLogger(__name__).info(
                "[agent-handler] injected long-task continue context thread=%s user=%r",
                thread_id,
                (msg.text or "")[:40],
            )
    except Exception:
        pass

    # Defense-in-depth: strip any platform-specific identifiers that might
    # have leaked into the top-level state. ``_PLATFORM_KEYS`` is the
    # authoritative list of keys that must never enter the graph; the state
    # is built from scratch above, so this is a safety net against future
    # refactors that copy ``ctx`` wholesale.
    leaked = _PLATFORM_KEYS.intersection(state)
    for key in leaked:
        state.pop(key, None)

    return state


def _build_target_id(platform: str, ctx: dict[str, Any]) -> str:
    """Build a platform-prefixed target ID from context_metadata.

    Args:
        platform: "telegram", "discord", etc.
        ctx: The restored context_metadata (may be empty on error).

    Returns:
        e.g. "telegram:12345", "discord:98765", or "telegram:unknown"
    """
    # Telegram uses chat_id
    chat_id = ctx.get("chat_id")
    if chat_id is not None:
        return f"{platform}:{chat_id}"

    # Discord threads ARE channels in the Discord API — posting to the
    # thread id lands the reply inside the thread. Inbound sets
    # discord_thread_id (discord_parse), but rebuilt targets ignored it, so
    # thread replies fell back to the parent channel (2026-09-04 audit).
    if platform == "discord":
        thread_id = ctx.get("discord_thread_id")
        if thread_id:
            return f"discord:{thread_id}"

    # Discord / Slack route on channel_id
    channel_id = ctx.get("channel_id")
    if channel_id is not None:
        return f"{platform}:{channel_id}"

    return f"{platform}:unknown"
