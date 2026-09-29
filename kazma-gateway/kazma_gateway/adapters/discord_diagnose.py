"""The Discord connector's Test: what Discord says about the bot, joined with
what Kazma's connection received (2026-09-29).

The Test used to ask one question -- does the token sign in -- and answered
"Connected" while no message from the owner had reached Kazma in eight days.
Each check here answers a question a person would ask, with the step that
fixes it:

- ``token``: does Discord accept the bot token?
- ``message_text``: may the bot read what people write in server channels
  (Discord's Message Content Intent)?
- ``servers``: which servers is the bot in -- and the Guild ID, if one is set?
- ``channel``: can the bot see the delivery channel, and what is it?
- ``latest``: the newest message a person wrote in that channel -- did it
  reach Kazma, and what became of it? (Who and when; never the text.)
- ``allowed``: who may talk to the bot?
- ``listening``: is Kazma's connection up, and what has it received?

``ok`` is True, False (something to fix) or None (worth knowing, not wrong).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from kazma_core.http_tls import shared_ssl_context
from kazma_gateway.adapters.discord_receive import DROP_REASONS

logger = logging.getLogger(__name__)

__all__ = ["diagnose"]

_API = "https://discord.com/api/v10"
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


def _when(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _show(stamp: datetime | None) -> str:
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if stamp else "an unknown time"


async def _get(client: httpx.AsyncClient, path: str, **params: Any) -> tuple[int, Any]:
    """(status, JSON body); status 0 when Discord could not be reached."""
    try:
        r = await client.get(path, params=params or None)
    except httpx.HTTPError as exc:
        logger.debug("[discord.test] GET %s failed", path, exc_info=True)
        return 0, type(exc).__name__
    try:
        body = r.json() if r.content else None
    except ValueError:
        body = None
    return r.status_code, body


def _judge_latest(msg: dict[str, Any], live: dict[str, Any] | None, allowed: list[str]) -> tuple[bool | None, str]:
    author = msg.get("author") or {}
    author_id = str(author.get("id") or "")
    who = f"{author.get('username') or 'someone'} (user {author_id})"
    stamp = _when(msg.get("timestamp"))
    lead = f"The latest message a person wrote there ({_show(stamp)}, from {who})"
    if allowed and author_id not in allowed:
        return False, (
            f"{lead} is from someone not in Allowed User IDs ({', '.join(allowed)}), so Kazma "
            f"does not answer it. If that is you, put {author_id} in Allowed User IDs and Save."
        )
    if live is None:
        return None, f"{lead}: Kazma's Discord connection is not running, so it cannot have received it."
    outcome = (live.get("recent") or {}).get(str(msg.get("id") or ""))
    if outcome == "passed_on":
        return True, f"{lead} reached Kazma and was handed on to be answered."
    if outcome == "accepted":
        return True, f"{lead} reached Kazma and is being prepared (a voice note is transcribed first)."
    if outcome:
        return False, f"{lead} reached Kazma but was not answered: it {DROP_REASONS.get(outcome, outcome)}."
    session_since = _when(live.get("session_since"))
    if session_since and stamp and stamp < session_since:
        return None, (
            f"{lead} was written before Kazma's current Discord session began "
            f"({_show(session_since)}), while it was restarting or away; Discord does not "
            "deliver such a message again. Send a new one to test."
        )
    return False, (
        f"{lead} never reached Kazma's connection, though the connection was up "
        f"(its last event: {_show(_when(live.get('last_event_at')))}). Discord did not deliver "
        "it to the bot. Restarting Kazma opens a new connection; if messages still do not "
        "arrive, the log's [discord] lines say what the connection is doing."
    )


def _listening(live: dict[str, Any] | None) -> tuple[bool, str]:
    if live is None:
        return False, (
            "Kazma's Discord connection is not running: turn Discord on above and Save, "
            "or check the bot token."
        )
    if not live.get("connected"):
        return False, (
            "Kazma's Discord connection is down right now; it reconnects by itself. "
            "If this stays, the log's [discord] lines say why."
        )
    dropped = sum((live.get("dropped") or {}).values())
    return True, (
        f"Connected since {_show(_when(live.get('connected_since')))}: "
        f"{live.get('messages', 0)} message(s) received, {live.get('passed_on', 0)} handed on "
        f"to be answered, {dropped} not answered; {sum((live.get('events') or {}).values())} "
        f"event(s) in all since Kazma started."
    )


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
    checks: list[dict[str, Any]] = []
    allowed = [str(a) for a in (allowed_users or []) if str(a).strip()]
    guild_ids = [str(g) for g in (guild_ids or []) if str(g).strip()]

    def add(key: str, ok: bool | None, detail: str) -> None:
        checks.append({"key": key, "ok": ok, "detail": detail})

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
            add("token", True, f"Signed in as {bot_name} (bot id {me.get('id')}).")
        elif status == 401:
            add("token", False, (
                "Discord refused the bot token (401). Copy it again from the Developer "
                "Portal (your app → Bot → Reset Token), paste it here and Save."
            ))
        elif status == 0:
            add("token", False, f"Could not reach Discord ({me}). Check this computer's internet connection.")
        else:
            add("token", False, f"Discord answered {status} to the sign-in check.")

        if checks[0]["ok"]:
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
                        person = next(
                            (m for m in history if not (m.get("author") or {}).get("bot")), None
                        )
                        if person is None:
                            add("latest", None, f"No message from a person among the latest {_HISTORY} there.")
                        else:
                            add("latest", *_judge_latest(person, live, allowed))
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

    if allowed:
        add("allowed", True, f"{len(allowed)} allowed user(s): {', '.join(allowed)}.")
    elif live is not None and live.get("allow_all"):
        add("allowed", None, "Allowed User IDs is empty: anyone who can message the bot is answered.")
    else:
        add("allowed", False, (
            "Allowed User IDs is empty, so every message is refused. Put your Discord user id there "
            "(Discord → Settings → Advanced → Developer Mode, then right-click your name → Copy User ID)."
        ))

    add("listening", *_listening(live))

    failed = next((c for c in checks if c["ok"] is False), None)
    return {
        "success": failed is None,
        "bot_name": bot_name,
        "error": failed["detail"] if failed else None,
        "checks": checks,
    }
