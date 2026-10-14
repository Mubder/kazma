"""``/replay`` and ``/fork`` typed in the web chat.

They are chat commands on Telegram, Discord and Slack (the gateway's
``_handle_replay`` / ``_handle_fork``). The web chat had no branch for them,
so the text went to the model, which searched the codebase for what
"replay" means and improvised an answer (live 2026-09-28). The web answers
at once, without the model, in the page's language:

- ``/replay clear`` deletes this chat's saved steps (it used to answer with a
  count and a link and delete nothing, TT-05);
- anything else says how many steps were saved and links the Time Travel
  page, where each step can be read before it is restored or branched from
  -- a bare command cannot show what it would restore.

An unreadable snapshot store is said plainly, never "nothing saved" (TT-11).
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

__all__ = ["is_time_travel_command", "time_travel_reply"]

_COMMAND = re.compile(r"^/(replay|fork)(?:\s|$)", re.IGNORECASE)


def is_time_travel_command(text: str) -> bool:
    """True for ``/replay`` or ``/fork`` with any arguments."""
    return bool(_COMMAND.match((text or "").strip()))


def _when(timestamp: str) -> str:
    """``2026-09-28 06:05 UTC`` from a snapshot's ISO timestamp."""
    stamp = str(timestamp or "")
    return f"{stamp[:10]} {stamp[11:16]} UTC" if len(stamp) >= 16 else stamp


def time_travel_reply(text: str, thread_id: str, recorder: Any, lang: str = "en") -> str:
    """The web chat's answer to a ``/replay`` or ``/fork`` command.

    Reads (and for ``/replay clear`` writes) the snapshot store: call it off
    the event loop. Raises ``SnapshotStoreError`` when the store cannot be
    read; the caller words it.
    """
    from kazma_ui.i18n import t, t_plural

    link = f"/replay?thread={quote(thread_id, safe='')}"
    parts = text.strip().split()
    command = _COMMAND.match(text.strip()).group(1).lower()
    note = t("replay.chat_page_note", lang, command=command)
    if recorder is None:
        return t("replay.chat_unavailable", lang)
    if command == "replay" and len(parts) > 1 and parts[1].lower() == "clear":
        from kazma_core.time_travel import forget_thread_snapshots
        from kazma_core.time_travel_audit import write_audit_row
        from kazma_ui.thread_rewind import chat_keys_for

        n = forget_thread_snapshots(chat_keys_for(thread_id), recorder=recorder)
        write_audit_row("clear", "ok", actor="web:chat", surface="web-chat", thread_id=thread_id,
                        detail={"deleted": n})
        return t_plural("replay.chat_cleared", n, lang)
    snapshots = recorder.list_snapshots(thread_id)
    if not snapshots:
        return f"{t('replay.chat_no_steps', lang)} {note}"
    last = snapshots[-1]
    head = t_plural("replay.chat_steps", len(snapshots), lang, when=_when(last.timestamp))
    return f"{head}\n\n{t('replay.chat_open_link', lang, link=link)}\n\n{note}"
