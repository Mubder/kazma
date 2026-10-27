"""The Markdown session export says when it leaves system messages out
(audit 2026-10-26 AUD-072).

It dropped them silently while its "Messages" count included them, and its
numbering skipped their places. The count is now of what is shown, the left
out ones are named, and the JSON export still holds every message.
"""

from __future__ import annotations

import asyncio
import json

MESSAGES = [
    {"role": "system", "content": "You are Kazma."},
    {"role": "user", "content": "Hello"},
    {"role": "system", "content": "TURN NOTE"},
    {"role": "assistant", "content": "Hi there"},
]


def _export(fmt: str, messages: list[dict]) -> str:
    from kazma_core.tools.export_session import export_session

    return asyncio.run(export_session(format=fmt, messages=messages))


def test_the_markdown_export_names_the_system_messages_it_leaves_out() -> None:
    out = _export("markdown", MESSAGES)
    assert "You are Kazma." not in out and "TURN NOTE" not in out
    assert "**Messages:** 2" in out
    assert "**System messages left out:** 2" in out
    # Numbered in the order shown, without gaps.
    assert "## 1. User" in out and "## 2. Assistant" in out


def test_no_note_when_nothing_was_left_out() -> None:
    out = _export("markdown", MESSAGES[1:2] + MESSAGES[3:])
    assert "left out" not in out and "**Messages:** 2" in out


def test_the_json_export_keeps_every_message() -> None:
    data = json.loads(_export("json", MESSAGES))
    assert data["message_count"] == 4 and len(data["messages"]) == 4


def _old_header_count(messages: list[dict]) -> tuple[int, int]:
    """The old export: header counted every message, body skipped system."""
    shown = sum(1 for m in messages if m.get("role", "").capitalize() != "System")
    return len(messages), shown


def test_negative_control_the_old_count_disagreed_with_what_was_shown() -> None:
    header, shown = _old_header_count(MESSAGES)
    assert header != shown
