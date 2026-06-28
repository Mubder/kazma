"""Swarm Notify — Worker-to-coordinator Telegram notifications.

Formal module replacing ad-hoc swarm_dispatch.py. Provides:
- SwarmNotifier: send messages to worker chats via bot token, rate-limited
- SwarmTaskTracker: track dispatched tasks, post periodic progress updates,
  and send completion notifications with result summaries

Standalone mode: if kazma_core.swarm is unavailable, reads config from env vars:
    SWARM_BOT_TOKEN  — Telegram bot token
    SWARM_CHAT_ID    — Default chat ID for notifications

Usage:
    notifier = SwarmNotifier.from_env()
    await notifier.send_message("worker-1", "Task started")

    tracker = SwarmTaskTracker(notifier)
    task_id = tracker.start_task("worker-1", "Analyze repo", chat_id=12345)
    await tracker.post_progress(task_id, "50% complete")
    await tracker.complete_task(task_id, success=True, summary="Done")
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"

# Telegram rate limit: 30 messages/sec per bot (global)
_RATE_LIMIT_MSGS = 30
_RATE_LIMIT_WINDOW = 1.0  # seconds


# ── SwarmNotifier ─────────────────────────────────────────────────


class SwarmNotifier:
    """Send Telegram messages from swarm workers to coordinator chats.

    Rate-limit aware: enforces Telegram's 30 msg/sec per-bot limit
    via a sliding window counter.

    Args:
        bot_token: Telegram Bot API token.
        default_chat_id: Fallback chat ID when not specified per-message.
        parse_mode: Telegram parse_mode ("Markdown", "MarkdownV2", "HTML").
    """

    def __init__(
        self,
        bot_token: str,
        default_chat_id: str | int | None = None,
        parse_mode: str = "Markdown",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        self._token = bot_token
        self._api_base = _TELEGRAM_API.format(token=bot_token)
        self._default_chat_id = str(default_chat_id) if default_chat_id else None
        self._parse_mode = parse_mode
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None

        # Rate limiting: sliding window
        self._send_timestamps: list[float] = []
        self._rate_lock = asyncio.Lock()

    @classmethod
    def from_env(cls, client: httpx.AsyncClient | None = None) -> SwarmNotifier:
        """Create a notifier from environment variables.

        Reads:
            SWARM_BOT_TOKEN  — Telegram bot token (required)
            SWARM_CHAT_ID    — Default chat ID (optional)
            SWARM_PARSE_MODE — parse_mode (default "Markdown")
        """
        token = os.environ.get("SWARM_BOT_TOKEN", "")
        chat_id = os.environ.get("SWARM_CHAT_ID")
        parse_mode = os.environ.get("SWARM_PARSE_MODE", "Markdown")
        return cls(
            bot_token=token,
            default_chat_id=chat_id,
            parse_mode=parse_mode,
            client=client,
        )

    @classmethod
    def from_swarm_manager(
        cls,
        swarm_manager: Any,
        client: httpx.AsyncClient | None = None,
    ) -> SwarmNotifier:
        """Create a notifier from a SwarmManager instance.

        Extracts bot_token and chat_id from the manager's config.
        Falls back to env vars for any missing values.
        """
        token = getattr(swarm_manager, "bot_token", None) or os.environ.get("SWARM_BOT_TOKEN", "")
        chat_id = getattr(swarm_manager, "chat_id", None) or os.environ.get("SWARM_CHAT_ID")
        parse_mode = getattr(swarm_manager, "parse_mode", None) or os.environ.get(
            "SWARM_PARSE_MODE", "Markdown"
        )
        return cls(
            bot_token=token,
            default_chat_id=chat_id,
            parse_mode=parse_mode,
            client=client,
        )

    # ── Rate limiting ──────────────────────────────────────────────

    async def _throttle(self) -> None:
        """Wait if necessary to respect Telegram's 30 msg/sec rate limit."""
        async with self._rate_lock:
            now = time.monotonic()
            # Purge timestamps older than the window
            self._send_timestamps = [
                ts for ts in self._send_timestamps if now - ts < _RATE_LIMIT_WINDOW
            ]
            if len(self._send_timestamps) >= _RATE_LIMIT_MSGS:
                # Wait until the oldest timestamp exits the window
                sleep_for = _RATE_LIMIT_WINDOW - (now - self._send_timestamps[0])
                if sleep_for > 0:
                    logger.debug("[SwarmNotifier] Rate limit hit, sleeping %.3fs", sleep_for)
                    await asyncio.sleep(sleep_for)
                # Re-purge after sleep
                now = time.monotonic()
                self._send_timestamps = [
                    ts for ts in self._send_timestamps if now - ts < _RATE_LIMIT_WINDOW
                ]
            self._send_timestamps.append(time.monotonic())

    # ── Send ───────────────────────────────────────────────────────

    async def send_message(
        self,
        chat_id: str | int | None = None,
        text: str = "",
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        """Send a text message via Telegram Bot API.

        Args:
            chat_id: Target chat ID. Falls back to default_chat_id.
            text: Message body (supports Markdown formatting).
            parse_mode: Override parse_mode for this message.

        Returns:
            Telegram API response dict.

        Raises:
            ValueError: If no chat_id available.
            httpx.HTTPStatusError: On non-2xx Telegram response.
        """
        target = str(chat_id) if chat_id else self._default_chat_id
        if not target:
            raise ValueError("No chat_id provided and no default_chat_id configured")

        await self._throttle()

        payload: dict[str, Any] = {
            "chat_id": target,
            "text": text,
        }
        mode = parse_mode or self._parse_mode
        if mode:
            payload["parse_mode"] = mode

        resp = await self._client.post(f"{self._api_base}/sendMessage", json=payload)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            logger.warning("[SwarmNotifier] Telegram API returned ok=false: %s", data)
        return data

    async def close(self) -> None:
        """Close the underlying HTTP client (only if we own it)."""
        if self._owns_client:
            await self._client.aclose()


# ── Task tracking ──────────────────────────────────────────────────


@dataclass
class TrackedTask:
    """A dispatched task being tracked by SwarmTaskTracker."""

    task_id: str
    worker_id: str
    description: str
    chat_id: str
    started_at: float = field(default_factory=time.monotonic)
    last_progress_at: float = 0.0
    completed_at: float | None = None
    status: str = "running"  # running | completed | failed
    progress_messages: list[str] = field(default_factory=list)


class SwarmTaskTracker:
    """Track dispatched swarm tasks and post progress/completion notifications.

    Periodically posts progress updates (every progress_interval seconds
    while a task is running) and sends a completion notification with
    result summary.

    Args:
        notifier: SwarmNotifier instance for Telegram messages.
        progress_interval: Seconds between automatic progress posts (default 30).
    """

    def __init__(
        self,
        notifier: SwarmNotifier,
        progress_interval: float = 30.0,
    ) -> None:
        self._notifier = notifier
        self._progress_interval = progress_interval
        self._tasks: dict[str, TrackedTask] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"task-{self._counter}"

    def start_task(
        self,
        worker_id: str,
        description: str,
        chat_id: str | int | None = None,
        task_id: str | None = None,
    ) -> str:
        """Register a new dispatched task.

        Args:
            worker_id: Identifier of the worker handling the task.
            description: Human-readable task description.
            chat_id: Telegram chat ID for notifications.
            task_id: Optional custom ID (auto-generated if omitted).

        Returns:
            The task ID.
        """
        tid = task_id or self._next_id()
        target = str(chat_id) if chat_id else (self._notifier._default_chat_id or "unknown")
        task = TrackedTask(
            task_id=tid,
            worker_id=worker_id,
            description=description,
            chat_id=target,
        )
        self._tasks[tid] = task
        logger.info("[SwarmTaskTracker] Task %s started: %s -> %s", tid, worker_id, description[:60])
        return tid

    async def post_progress(self, task_id: str, message: str) -> dict[str, Any] | None:
        """Post a progress update for a tracked task.

        Sends a formatted message to the task's chat.

        Args:
            task_id: The task identifier.
            message: Progress description.

        Returns:
            Telegram API response, or None if task not found.
        """
        task = self._tasks.get(task_id)
        if not task:
            logger.warning("[SwarmTaskTracker] Unknown task %s", task_id)
            return None

        task.progress_messages.append(message)
        task.last_progress_at = time.monotonic()

        elapsed = time.monotonic() - task.started_at
        text = (
            f"⏳ *Progress* — `{task_id}`\n"
            f"Worker: `{task.worker_id}`\n"
            f"Elapsed: {elapsed:.0f}s\n"
            f"{message}"
        )
        return await self._notifier.send_message(task.chat_id, text)

    async def complete_task(
        self,
        task_id: str,
        success: bool = True,
        summary: str = "",
    ) -> dict[str, Any] | None:
        """Mark a task complete and send a summary notification.

        Args:
            task_id: The task identifier.
            success: Whether the task succeeded.
            summary: Result summary text.

        Returns:
            Telegram API response, or None if task not found.
        """
        task = self._tasks.get(task_id)
        if not task:
            logger.warning("[SwarmTaskTracker] Unknown task %s", task_id)
            return None

        task.completed_at = time.monotonic()
        task.status = "completed" if success else "failed"

        elapsed = task.completed_at - task.started_at
        icon = "✅" if success else "❌"
        text = (
            f"{icon} *Task {task.status}* — `{task_id}`\n"
            f"Worker: `{task.worker_id}`\n"
            f"Description: {task.description}\n"
            f"Duration: {elapsed:.1f}s\n"
        )
        if summary:
            text += f"\n{summary}"
        return await self._notifier.send_message(task.chat_id, text)

    async def should_post_progress(self, task_id: str) -> bool:
        """Check if enough time has elapsed to warrant a progress post.

        Returns True if the task is running and at least progress_interval
        seconds have passed since the last progress post (or start).
        """
        task = self._tasks.get(task_id)
        if not task or task.status != "running":
            return False
        reference = task.last_progress_at or task.started_at
        return (time.monotonic() - reference) >= self._progress_interval

    def get_task(self, task_id: str) -> TrackedTask | None:
        """Return a tracked task by ID, or None."""
        return self._tasks.get(task_id)

    def active_tasks(self) -> list[TrackedTask]:
        """Return all tasks with status 'running'."""
        return [t for t in self._tasks.values() if t.status == "running"]

    def all_tasks(self) -> list[TrackedTask]:
        """Return all tracked tasks."""
        return list(self._tasks.values())
