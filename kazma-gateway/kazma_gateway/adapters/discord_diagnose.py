"""The Discord connector's Test: what Discord says about the bot, joined with
what Kazma's connection received (2026-09-29).

The Test used to ask one question -- does the token sign in -- and answered
"Connected" while no message from the owner had reached Kazma in eight days.
Each check here answers a question a person would ask, with the step that
fixes it (the shared pieces are ``kazma_gateway.connector_test``):

- ``token``: does Discord accept the bot token?
- ``message_text``: may the bot read what people write in server channels
  (Discord's Message Content Intent)?
- ``servers``: which servers is the bot in -- and the Guild ID, if one is set?
- ``channel``: can the bot see the delivery channel, and what is it?
- ``latest``: the newest message a person wrote in that channel -- did it
  reach Kazma, and what became of it? (Who and when; never the text.)
- ``direct_message``: for each allowed user, their newest direct message to
  the bot -- did it reach Kazma? (Answered in a server, silent in DMs, was
  the owner's report; writing to another bot account looks the same.)
- ``allowed``: who may talk to the bot?
- ``listening``: is Kazma's connection up, and what has it received?
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kazma_core.http_tls import shared_ssl_context
from kazma_gateway.adapters.discord_receive import DROP_REASONS
from kazma_gateway.connector_test import Checks, judge_message, listening, when

logger = logging.getLogger(__name__)

__all__ = ["diagnose"]

_API = "https://discord.com/api/v10"
#: Where a link to a conversation opens (the Discord app, or its web client).
_DISCORD_APP = "https://discord.com"
#: Application flags GATEWAY_MESSAGE_CONTENT (a verified app, approved) and
#: GATEWAY_MESSAGE_CONTENT_LIMITED (the Developer Portal switch, unverified).
_MESSAGE_CONTENT_FLAGS = (1 << 18) | (1 << 19)
_CHANNEL_KINDS = {
    0: "a server text channel",
    1: "a direct message",
    2: "a voice channel",
    3: "a group direct message",
    5: "an announcement channel",
    10: "a thread",
    11: "a thread",
    12: "a private thread",
    15: "a forum",
}
#: How many of the channel's newest messages the Test looks through.
_HISTORY = 20
#: How many allowed users' direct messages it opens.
_DM_USERS = 3
_NEVER = (
    "Discord did not deliver it to the bot. Restarting Kazma opens a new connection; "
    "if messages still do not arrive, the log's [discord] lines say what the "
    "connection is doing."
)


async def _call(client: httpx.AsyncClient, method: str, path: str, **kw: Any) -> tuple[int, Any]:
    """(status, JSON body); status 0 when Discord could not be reached."""
    try:
        r = await client.request(method, path, **kw)
    except httpx.HTTPError as exc:
        logger.debug("[discord.test] %s %s failed", method, path, exc_info=True)
        return 0, type(exc).__name__
    try:
        body = r.json() if r.content else None
    except ValueError:
        body = None
    return r.status_code, body


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> tuple[int, Any]:
    return await _call(client, "GET", path, params=params or None)


def _bot_label(me: Any) -> str:
    """"KazmaAI#1234 (bot id …)": the name and tag Discord shows, so two bots
    with one name can be told apart."""
    me = me if isinstance(me, dict) else {}
    tag = str(me.get("discriminator") or "")
    name = f"{me.get('username')}#{tag}" if tag and tag != "0" else str(me.get("username"))
    return f"{name} (bot id {me.get('id')})"


def _newest_person(history: Any) -> dict[str, Any] | None:
    if not isinstance(history, list):
        return None
    return next((m for m in history if not (m.get("author") or {}).get("bot")), None)


def _judge(
    msg: dict[str, Any],
    live: dict[str, Any] | None,
    allowed: list[str],
    *,
    place: str = "The latest message a person wrote there",
) -> tuple[bool | None, str]:
    author = msg.get("author") or {}
    return judge_message(
        message_id=msg.get("id"),
        author_id=author.get("id"),
        who=f"{author.get('username') or 'someone'} (user {author.get('id')})",
        stamp=when(msg.get("timestamp")),
        live=live,
        allowed=allowed,
        place=place,
        platform="Discord",
        never_hint=_NEVER,
        reasons=DROP_REASONS,
    )


async def _judge_direct_messages(
    client: httpx.AsyncClient,
    user_id: str,
    me: Any,
    live: dict[str, Any] | None,
    allowed: list[str],
) -> tuple[bool | None, str, str | None]:
    """Whether *user_id*'s newest direct message to the bot reached Kazma, and
    a link that opens that very conversation in Discord (there is exactly one
    between a user and a bot)."""
    bot = _bot_label(me)
    status, dm = await _call(client, "POST", "/users/@me/channels", json={"recipient_id": user_id})
    if status != 200 or not isinstance(dm, dict) or not dm.get("id"):
        return None, f"Could not open the direct messages with user {user_id} (Discord answered {status}).", None
    status, history = await _get(client, f"/channels/{dm['id']}/messages", limit=_HISTORY)
    if status != 200:
        return None, f"Could not read the direct messages with user {user_id} (Discord answered {status}).", None
    link = f"{_DISCORD_APP}/channels/@me/{dm['id']}"
    person = _newest_person(history)
    if person is None:
        return None, (
            f"User {user_id} has written nothing to this bot, {bot}, in direct messages. "
            "If you wrote to a bot in your direct messages and got no answer, it was another "
            "bot account with a similar name. Open this bot's conversation with the link and "
            "write there."
        ), link
    ok, detail = _judge(person, live, allowed, place=f"The latest direct message user {user_id} wrote to the bot")
    return ok, detail, link


async def diagnose(
    token: str,
    *,
    channel_id: str = "",
    guild_ids: list[str] | None = None,
    allowed_users: list[str] | None = None,
    live: dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Run every check; ``{"success", "bot_name", "error", "checks"}``."""
    checks = Checks()
    add = checks.add
    allowed = [str(a) for a in (allowed_users or []) if str(a).strip()]
    guild_ids = [str(g) for g in (guild_ids or []) if str(g).strip()]

    bot_name: str | None = None
    async with httpx.AsyncClient(
        base_url=_API,
        headers={"Authorization": f"Bot {token}"},
        timeout=10.0,
        verify=shared_ssl_context(),
        transport=transport,
    ) as client:
        status, me = await _get(client, "/users/@me")
        if status == 200 and isinstance(me, dict):
            bot_name = str(me.get("username") or "") or None
            add("token", True, f"Signed in as {_bot_label(me)}.")
        elif status == 401:
            add("token", False, (
                "Discord refused the bot token (401). Copy it again from the Developer "
                "Portal (your app → Bot → Reset Token), paste it here and Save."
            ))
        elif status == 0:
            add("token", False, f"Could not reach Discord ({me}). Check this computer's internet connection.")
        else:
            add("token", False, f"Discord answered {status} to the sign-in check.")

        if checks.ok("token"):
            status, app = await _get(client, "/applications/@me")
            if status == 200 and isinstance(app, dict):
                if int(app.get("flags") or 0) & _MESSAGE_CONTENT_FLAGS:
                    add("message_text", True, "On: the bot can read what people write in server channels.")
                else:
                    add("message_text", False, (
                        "Off: in server channels Discord gives the bot no text, so Kazma cannot "
                        "answer there (direct messages still work). Developer Portal → your app → "
                        "Bot → Privileged Gateway Intents → MESSAGE CONTENT INTENT, save it there, "
                        "then Save here."
                    ))
            else:
                add("message_text", None, f"Could not read the app's settings (Discord answered {status}).")

            names: dict[str, str] = {}
            status, guilds = await _get(client, "/users/@me/guilds")
            if status == 200 and isinstance(guilds, list):
                names = {str(g.get("id")): str(g.get("name") or g.get("id")) for g in guilds}
                if names:
                    add("servers", True, f"In {len(names)} server(s): {', '.join(names.values())}.")
                else:
                    add("servers", None, (
                        "In no server: only direct messages reach it. To use it in a server, invite "
                        "it (Developer Portal → OAuth2 → URL Generator, scope \"bot\")."
                    ))
                missing = [g for g in guild_ids if g not in names]
                if missing:
                    add("servers", False, (
                        f"Guild ID {', '.join(missing)} is not a server the bot is in, and Kazma "
                        "answers server messages from the Guild ID only. Fix or clear it and Save."
                    ))
            else:
                add("servers", None, f"Could not list the bot's servers (Discord answered {status}).")

            if channel_id:
                status, ch = await _get(client, f"/channels/{channel_id}")
                if status == 200 and isinstance(ch, dict):
                    kind = _CHANNEL_KINDS.get(int(ch.get("type") or 0), f"a channel of type {ch.get('type')}")
                    if ch.get("type") == 1:
                        people = ", ".join(
                            f"{r.get('username')} (user {r.get('id')})" for r in ch.get("recipients") or []
                        ) or "someone"
                        add("channel", True, f"The delivery channel is a direct message with {people}.")
                    else:
                        server = str(ch.get("guild_id") or "")
                        add("channel", True, (
                            f"The delivery channel is #{ch.get('name') or channel_id}, {kind} in "
                            f"server {names.get(server, server)}."
                        ))
                    status, history = await _get(client, f"/channels/{channel_id}/messages", limit=_HISTORY)
                    if status == 200 and isinstance(history, list):
                        person = _newest_person(history)
                        if person is None:
                            add("latest", None, f"No message from a person among the latest {_HISTORY} there.")
                        else:
                            add("latest", *_judge(person, live, allowed))
                    else:
                        add("latest", None, (
                            "The bot cannot read that channel's history (Read Message History), so "
                            "the Test cannot see your latest message there."
                        ))
                elif status == 403:
                    add("channel", False, (
                        f"The bot cannot see channel {channel_id} (Missing Access): add the bot to "
                        "that channel's server, or give its role View Channel there."
                    ))
                elif status == 404:
                    add("channel", False, f"Discord has no channel {channel_id}: check Channel ID.")
                else:
                    add("channel", None, f"Could not look at channel {channel_id} (Discord answered {status}).")
            else:
                add("channel", None, "No delivery channel is set, so alerts and reports have nowhere to go on Discord.")

            # The direct messages with each allowed user (2026-09-29: the owner's
            # server messages were answered, the DMs never). Opening the DM
            # channel sends nothing; an existing one is returned as it is.
            for user_id in allowed[:_DM_USERS]:
                add("direct_message", *await _judge_direct_messages(client, user_id, me, live, allowed))

    if allowed:
        add("allowed", True, f"{len(allowed)} allowed user(s): {', '.join(allowed)}.")
    elif live is not None and live.get("allow_all"):
        add("allowed", None, "Allowed User IDs is empty: anyone who can message the bot is answered.")
    else:
        add("allowed", False, (
            "Allowed User IDs is empty, so every message is refused. Put your Discord user id there "
            "(Discord → Settings → Advanced → Developer Mode, then right-click your name → Copy User ID)."
        ))

    add("listening", *listening(live, "Discord", not_running=(
        "Kazma's Discord connection is not running: turn Discord on above and Save, "
        "or check the bot token."
    )))
    return checks.result(bot_name)
