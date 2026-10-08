"""The Telegram connector's Test: what Telegram says about the bot, joined
with what Kazma's connection received (2026-09-29).

The owner asked for Discord's Test on every adapter. The Telegram Test used
to call getMe and say "Connected". Each check answers a question a person
would ask, with the step that fixes it (shared pieces:
``kazma_gateway.connector_test``):

- ``token``: does Telegram accept the bot token?
- ``receiving``: is a webhook set? (Kazma polls; while a webhook is set
  Telegram refuses polling with 409 and Kazma's Telegram stops.) How many
  updates wait on Telegram's side?
- ``groups``: group privacy mode -- in a group, does the bot see every
  message or only commands, replies and mentions?
- ``chat``: the delivery chat -- does Telegram know it for this bot (a
  person must press Start first), did the person block the bot?
- ``group``: the Telegram group route -- is its bot still in the group?
- ``latest``: the last message a person sent Kazma on Telegram, and what
  became of it (Telegram lets a bot read no chat history, so this is what
  the connection received).
- ``allowed``: who may talk to the bot?
- ``listening``: is Kazma polling, and what has it received (a 409 names
  the other program collecting the bot's messages)?

Every request goes to ``https://api.telegram.org/bot<token>/…``; nothing here
logs a URL, which carries the token.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kazma_core.http_tls import shared_ssl_context
from kazma_gateway.adapters.telegram_receive import TELEGRAM_REASONS
from kazma_gateway.connector_test import Checks, command_registration, judge_message, listening, when

logger = logging.getLogger(__name__)

__all__ = ["diagnose"]

_API = "https://api.telegram.org"
#: Kazma's own webhook ingress (a webhook set there on purpose is fine).
_OWN_WEBHOOK = "/api/webhooks/telegram"
_NEVER = "Telegram did not deliver it to Kazma."


async def _call(client: httpx.AsyncClient, token: str, method: str, **params: Any) -> tuple[int, Any]:
    """(status, the JSON body); status 0 when Telegram could not be reached.
    The URL holds the token: only the method name is ever logged."""
    try:
        r = await client.post(f"/bot{token}/{method}", json=params or None)
    except httpx.HTTPError as exc:
        logger.debug("[telegram.test] %s failed: %s", method, type(exc).__name__)
        return 0, type(exc).__name__
    try:
        body = r.json() if r.content else None
    except ValueError:
        body = None
    return r.status_code, body


def _description(body: Any) -> str:
    return str((body or {}).get("description") or "") if isinstance(body, dict) else ""


def _bot_label(me: dict[str, Any]) -> str:
    return f"@{me.get('username')} ({me.get('first_name') or 'bot'}, bot id {me.get('id')})"


def _chat_label(chat: dict[str, Any]) -> str:
    if chat.get("type") == "private":
        who = f"@{chat['username']}" if chat.get("username") else (chat.get("first_name") or "someone")
        return f"a private chat with {who}"
    return f"the {chat.get('type') or 'chat'} «{chat.get('title') or chat.get('id')}»"


def _chat_problem(chat_id: str, status: int, body: Any) -> str:
    desc = _description(body).lower()
    if "blocked" in desc:
        return f"The person in chat {chat_id} blocked the bot: open the bot in Telegram and press Restart."
    if "kicked" in desc or "not a member" in desc:
        return f"The bot is not in chat {chat_id} any more: add it to the group again."
    if "not found" in desc:
        return (
            f"Telegram knows no chat {chat_id} for this bot. For a private chat, that person "
            "must open the bot in Telegram and press Start first; otherwise check the Chat ID."
        )
    return f"Telegram answered {status} for chat {chat_id}: {_description(body) or 'no reason given'}."


async def _check_group(
    client: httpx.AsyncClient, main_token: str, main_me: dict[str, Any], group: dict[str, Any]
) -> tuple[bool | None, str]:
    """The Telegram group route: its bot (the dedicated one, or the main
    bot) and whether that bot is still in the group."""
    chat_id = str(group.get("chat_id") or "")
    token = str(group.get("bot_token") or "").strip() or main_token
    me = main_me
    if token != main_token:
        status, body = await _call(client, token, "getMe")
        if status != 200 or not (body or {}).get("ok"):
            return False, (
                "Telegram refused the group's own bot token: paste it again on the group card, "
                "or leave it empty to use the main bot."
            )
        me = body["result"]
    status, body = await _call(client, token, "getChat", chat_id=chat_id)
    if status != 200 or not (body or {}).get("ok"):
        return False, _chat_problem(chat_id, status, body)
    title = _chat_label(body["result"])
    if body["result"].get("type") == "private":
        # A group route set to a person's chat still delivers there (the
        # live install's is); there is no membership to ask about.
        return None, (
            f"The group route goes to {title} -- a person's chat, not a group; Kazma posts "
            "there. For a group, put the group's id (it starts with -100) on the group card."
        )
    status, body = await _call(client, token, "getChatMember", chat_id=chat_id, user_id=me.get("id"))
    member = str(((body or {}).get("result") or {}).get("status") or "")
    if member in ("member", "administrator", "creator"):
        role = "an administrator" if member in ("administrator", "creator") else "a member"
        return True, f"The group route goes to {title}; its bot, @{me.get('username')}, is {role} there."
    return False, (
        f"The group route goes to {title}, but its bot, @{me.get('username')}, is not in it "
        f"({member or 'unknown'}): add the bot to the group again."
    )


async def diagnose(
    token: str,
    *,
    chat_id: str = "",
    group: dict[str, Any] | None = None,
    allowed_users: list[str] | None = None,
    live: dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Run every check; ``{"success", "bot_name", "error", "checks"}``."""
    checks = Checks()
    add = checks.add
    allowed = [str(a) for a in (allowed_users or []) if str(a).strip()]
    bot_name: str | None = None
    me: dict[str, Any] = {}

    async with httpx.AsyncClient(
        base_url=_API, timeout=15.0, verify=shared_ssl_context(), transport=transport
    ) as client:
        status, body = await _call(client, token, "getMe")
        if status == 200 and (body or {}).get("ok"):
            me = body["result"]
            bot_name = str(me.get("username") or "") or None
            add("token", True, f"Signed in as {_bot_label(me)}.")
        elif status in (401, 404):
            add("token", False, (
                "Telegram refused the bot token. Get it again from @BotFather (/mybots → your "
                "bot → API Token), paste it here and Save."
            ))
        elif status == 0:
            add("token", False, f"Could not reach Telegram ({body}). Check this computer's internet connection.")
        else:
            add("token", False, f"Telegram answered {status} to the sign-in check: {_description(body)}")

        if checks.ok("token"):
            status, body = await _call(client, token, "getWebhookInfo")
            info = (body or {}).get("result") or {} if status == 200 else {}
            waiting = int(info.get("pending_update_count") or 0)
            url = str(info.get("url") or "")
            if status != 200:
                add("receiving", None, f"Could not read how Telegram delivers (it answered {status}).")
            elif url and _OWN_WEBHOOK in url:
                add("receiving", None, (
                    f"Telegram delivers to Kazma's webhook ({url}) instead of being polled; "
                    f"{waiting} update(s) wait."
                ))
            elif url:
                add("receiving", False, (
                    f"A webhook is set ({url}). Kazma collects messages by polling, and Telegram "
                    "refuses polling while a webhook is set, so Kazma's Telegram stops. Kazma "
                    "removes the webhook when it starts: restart it. If the webhook comes back, "
                    "another program sets it -- stop that program."
                ))
            else:
                add("receiving", True, (
                    "No webhook: Kazma collects messages by polling (the normal setup)"
                    + (f"; {waiting} update(s) wait on Telegram's side." if waiting else ".")
                ))

            if me.get("can_read_all_group_messages"):
                add("groups", True, "Group privacy is off: in a group the bot reads every message.")
            else:
                add("groups", None, (
                    "Group privacy is on: in a group the bot sees only commands, replies to it and "
                    "@mentions. For it to read every message there: @BotFather → /mybots → your bot → "
                    "Bot Settings → Group Privacy → Turn off, then remove the bot from the group and "
                    "add it again."
                ))

            if chat_id:
                status, body = await _call(client, token, "getChat", chat_id=chat_id)
                if status == 200 and (body or {}).get("ok"):
                    add("chat", True, f"The delivery chat is {_chat_label(body['result'])}.")
                else:
                    add("chat", False, _chat_problem(chat_id, status, body))
            else:
                add("chat", None, "No delivery chat is set, so alerts and reports have nowhere to go on Telegram.")

            if group and group.get("enabled") and group.get("chat_id"):
                add("group", *await _check_group(client, token, me, group))

    person = (live or {}).get("last_person")
    if live is None:
        pass  # "listening" says the connection is not running
    elif not person:
        add("latest", None, (
            "No message from a person has reached Kazma on Telegram since it started "
            f"({live.get('started_at')}). Send the bot a message, then Test again."
        ))
    else:
        add("latest", *judge_message(
            message_id=person.get("id"),
            author_id=person.get("author_id"),
            who=f"user {person.get('author_id')} in {person.get('where') or 'a chat'}",
            stamp=when(person.get("at")),
            live=live,
            allowed=allowed,
            place="The last message a person sent Kazma on Telegram",
            platform="Telegram",
            never_hint=_NEVER,
            reasons=TELEGRAM_REASONS,
        ))

    if allowed:
        add("allowed", True, f"{len(allowed)} allowed user(s): {', '.join(allowed)}.")
    elif live is not None and live.get("allow_all"):
        add("allowed", None, "Allowed User IDs is empty: anyone who can message the bot is answered.")
    else:
        add("allowed", False, (
            "Allowed User IDs is empty, so every message is refused. Put your Telegram user id "
            "there (send /start to @userinfobot to see it)."
        ))

    add("listening", *listening(live, "Telegram", not_running=(
        "Kazma's Telegram connection is not running: turn Telegram on above and Save, "
        "or check the bot token."
    )))
    add("commands", *command_registration(live, "Telegram"))
    return checks.result(bot_name)
