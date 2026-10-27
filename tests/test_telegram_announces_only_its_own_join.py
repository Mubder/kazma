"""Kazma posts a group's chat id only when KAZMA'S bot joins it (audit
2026-10-26 AUD-050).

The join check matched ``is_bot`` on any new member, so adding any other bot
to a group posted Kazma's setup text and the chat id there, before any
allowlist; and with the bot's identity not yet known an empty username
matched every member without one. The bot is now matched by its own user id
(getMe), else by its username when both sides have one.
"""

from __future__ import annotations

import asyncio
from typing import Any


class _Http:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []

    async def post(self, path: str, json: dict[str, Any]) -> None:
        self.posts.append((path, json))


def _join(members: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": -1001, "type": "supergroup"},
            "from": {"id": 7, "is_bot": False},
            "new_chat_members": members,
        },
    }


def _adapter(*, bot_id: int | None, username: str) -> Any:
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:abc", allowed_users=[7])
    adapter._bot_user_id = bot_id
    adapter._bot_username = username
    adapter._http = _Http()
    return adapter


def _announced(adapter: Any, members: list[dict[str, Any]]) -> bool:
    asyncio.run(adapter._announce_if_added_to_group(_join(members)))
    return any(path == "/sendMessage" for path, _ in adapter._http.posts)


OTHER_BOT = {"id": 99, "is_bot": True, "username": "otherbot"}
KAZMA = {"id": 42, "is_bot": True, "username": "kazmabot"}
PERSON = {"id": 8, "is_bot": False, "first_name": "Sara"}


def test_another_bot_joining_posts_nothing() -> None:
    assert not _announced(_adapter(bot_id=42, username="kazmabot"), [OTHER_BOT])


def test_kazma_joining_posts_the_chat_id() -> None:
    adapter = _adapter(bot_id=42, username="kazmabot")
    assert _announced(adapter, [PERSON, KAZMA])
    assert "-1001" in adapter._http.posts[0][1]["text"]


def test_by_username_when_the_id_is_not_known_yet() -> None:
    assert _announced(_adapter(bot_id=None, username="KazmaBot"), [KAZMA])
    assert not _announced(_adapter(bot_id=None, username="kazmabot"), [OTHER_BOT])


def test_an_unknown_identity_matches_no_one() -> None:
    # No getMe yet and a person with no username: "" == "" matched before.
    assert not _announced(_adapter(bot_id=None, username=""), [PERSON])


def _old_is_added(adapter: Any, members: list[dict[str, Any]]) -> bool:
    """The check before AUD-050."""
    me = getattr(adapter, "_bot_username", "") or ""
    return any(
        str(m.get("username") or "").lstrip("@") == me.lstrip("@") or m.get("is_bot")
        for m in members
    )


def test_negative_control_the_old_check_answers_any_bot() -> None:
    adapter = _adapter(bot_id=42, username="kazmabot")
    assert _old_is_added(adapter, [OTHER_BOT]) is True
    assert _old_is_added(_adapter(bot_id=None, username=""), [PERSON]) is True
    assert adapter._is_this_bot(OTHER_BOT) is False
