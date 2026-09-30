"""The Slack connector's Test: what Slack says about the app, joined with what
Kazma's connection received (2026-09-29).

The owner asked for Discord's Test on every adapter. The Slack Test used to
call auth.test and say "Connected". Each check answers a question a person
would ask, with the step that fixes it (shared pieces:
``kazma_gateway.connector_test``):

- ``token``: does Slack accept the bot token (xoxb-)?
- ``app_token``: does the app-level token (xapp-) open Socket Mode? Without
  it Kazma polls channels, slowly.
- ``scopes``: which permissions the bot token carries -- to write, and to be
  told about channel, private-channel and direct messages and mentions.
- ``channel``: the delivery channel -- does Slack know it, is the bot in it?
- ``latest``: the newest message a person wrote there -- did it reach Kazma?
- ``direct_message``: each allowed user's newest direct message to the bot
  (conversations.open returns the one DM; it sends nothing).
- ``allowed``: who may talk to the bot?
- ``listening``: is Kazma's connection up, and what has it received?
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kazma_core.http_tls import shared_ssl_context
from kazma_gateway.adapters.slack_receive import SLACK_REASONS
from kazma_gateway.connector_test import Checks, judge_message, listening, show, when

logger = logging.getLogger(__name__)

__all__ = ["diagnose"]

_API = "https://slack.com/api"
_HISTORY = 20
_DM_USERS = 3
#: What each scope lets Kazma do, for the scopes it needs.
_SCOPES = {
    "chat:write": "answer (post messages)",
    "channels:history": "be told about messages in public channels",
    "groups:history": "be told about messages in private channels",
    "im:history": "be told about direct messages",
    "app_mentions:read": "be told about @mentions",
}
_NEVER = (
    "Slack did not deliver it to the app. Slack sends an app only the events it "
    "subscribes to: your app → Event Subscriptions → Subscribe to bot events -- "
    "message.channels, message.groups, message.im and app_mention -- and Socket Mode "
    "on, then reinstall the app."
)
_TOKEN_FIX = (
    "Copy the Bot User OAuth Token (xoxb-…) again from your app's OAuth & Permissions "
    "page, paste it here and Save."
)


async def _call(
    client: httpx.AsyncClient, method: str, token: str, **params: Any
) -> tuple[bool, dict[str, Any], httpx.Headers | None]:
    """(ok, body, headers) of one Web API call; ok is Slack's own flag."""
    try:
        r = await client.post(
            f"/{method}", data=params or None, headers={"Authorization": f"Bearer {token}"}
        )
    except httpx.HTTPError as exc:
        logger.debug("[slack.test] %s failed: %s", method, type(exc).__name__)
        return False, {"error": f"could not reach Slack ({type(exc).__name__})"}, None
    try:
        body = r.json() if r.content else {}
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    if r.status_code != 200 and "error" not in body:
        body["error"] = f"HTTP {r.status_code}"
    return bool(body.get("ok")), body, r.headers


def _newest_person(messages: Any) -> dict[str, Any] | None:
    if not isinstance(messages, list):
        return None
    return next(
        (m for m in messages if m.get("user") and not m.get("bot_id") and not m.get("subtype")), None
    )


def _judge(msg: dict[str, Any], channel: str, live, allowed, *, place: str) -> tuple[bool | None, str]:
    try:
        stamp = when(float(msg.get("ts") or 0) or None)
    except (TypeError, ValueError):
        stamp = None
    return judge_message(
        message_id=f"{channel}:{msg.get('ts')}",
        author_id=msg.get("user"),
        who=f"user {msg.get('user')}",
        stamp=stamp,
        live=live,
        allowed=allowed,
        place=place,
        platform="Slack",
        never_hint=_NEVER,
        reasons=SLACK_REASONS,
    )


async def diagnose(
    token: str,
    *,
    app_token: str = "",
    channel_id: str = "",
    allowed_users: list[str] | None = None,
    live: dict[str, Any] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Run every check; ``{"success", "bot_name", "error", "checks"}``."""
    checks = Checks()
    add = checks.add
    allowed = [str(a) for a in (allowed_users or []) if str(a).strip()]
    bot_name: str | None = None
    team_id = ""

    async with httpx.AsyncClient(
        base_url=_API, timeout=15.0, verify=shared_ssl_context(), transport=transport
    ) as client:
        ok, me, headers = await _call(client, "auth.test", token)
        if ok:
            bot_name = str(me.get("user") or "") or None
            team_id = str(me.get("team_id") or "")
            add("token", True, f"Signed in to {me.get('team')} as {bot_name} (bot user {me.get('user_id')}).")
        else:
            add("token", False, f"Slack refused the bot token ({me.get('error')}). {_TOKEN_FIX}")

        if checks.ok("token"):
            if app_token:
                ok, body, _ = await _call(client, "apps.connections.open", app_token)
                if ok:
                    add("app_token", True, "Socket Mode can connect: the app-level token works.")
                elif body.get("error") == "not_allowed_token_type":
                    add("app_token", False, (
                        "The App Token field holds another kind of token: it must be an app-level "
                        "token (xapp-…) with connections:write, from Basic Information → App-Level Tokens."
                    ))
                else:
                    add("app_token", False, (
                        f"Slack refused the app-level token ({body.get('error')}). Generate one under "
                        "Basic Information → App-Level Tokens with connections:write, turn Socket Mode "
                        "on, paste it here and Save."
                    ))
            else:
                add("app_token", None, (
                    "No app-level token: Kazma polls channels every few seconds instead of Socket "
                    "Mode, and only channels it can list. For real-time delivery, direct messages "
                    "and mentions, add one (Basic Information → App-Level Tokens, connections:write) "
                    "and turn Socket Mode on."
                ))

            granted = {s.strip() for s in ((headers or {}).get("x-oauth-scopes") or "").split(",") if s.strip()}
            if granted:
                missing = [s for s in _SCOPES if s not in granted]
                if missing:
                    add("scopes", False, (
                        "The bot token lacks " + "; ".join(f"{s} (to {_SCOPES[s]})" for s in missing)
                        + ". Add them under OAuth & Permissions → Bot Token Scopes, then reinstall the app."
                    ))
                else:
                    add("scopes", True, "The bot token may " + ", ".join(_SCOPES.values()) + ".")
            else:
                add("scopes", None, "Slack did not say which permissions the bot token has.")

            if channel_id:
                ok, body, _ = await _call(client, "conversations.info", token, channel=channel_id)
                if ok:
                    ch = body.get("channel") or {}
                    name = "a direct message" if ch.get("is_im") else f"#{ch.get('name') or channel_id}"
                    if ch.get("is_im") or ch.get("is_member"):
                        add("channel", True, f"The delivery channel is {name}, and the bot is in it.")
                    else:
                        add("channel", False, (
                            f"The delivery channel is {name}, but the bot is not in it: type "
                            f"/invite @{bot_name or 'your-bot'} in that channel."
                        ))
                    ok, hist, _ = await _call(client, "conversations.history", token, channel=channel_id, limit=_HISTORY)
                    if ok:
                        person = _newest_person(hist.get("messages"))
                        if person is None:
                            add("latest", None, f"No message from a person among the latest {_HISTORY} there.")
                        else:
                            add("latest", *_judge(person, channel_id, live, allowed,
                                                  place="The latest message a person wrote there"))
                    else:
                        add("latest", None, (
                            f"The Test cannot read that channel's messages ({hist.get('error')}), so it "
                            "cannot see your latest message there."
                        ))
                else:
                    add("channel", False, (
                        f"Slack answered {body.get('error')} for channel {channel_id}: check Channel ID "
                        "(open the channel → its name → the ID at the bottom), and that the bot is in it."
                    ))
            else:
                add("channel", None, "No delivery channel is set, so alerts and reports have nowhere to go on Slack.")

            for user_id in allowed[:_DM_USERS]:
                ok, body, _ = await _call(client, "conversations.open", token, users=user_id)
                if not ok:
                    add("direct_message", None, (
                        f"Could not open the direct messages with user {user_id} ({body.get('error')}; "
                        "the bot token needs im:write)."
                    ))
                    continue
                dm = str((body.get("channel") or {}).get("id") or "")
                link = f"https://slack.com/app_redirect?channel={dm}" + (f"&team={team_id}" if team_id else "")
                ok, hist, _ = await _call(client, "conversations.history", token, channel=dm, limit=_HISTORY)
                if not ok:
                    add("direct_message", None, (
                        f"Could not read the direct messages with user {user_id} ({hist.get('error')}; "
                        "the bot token needs im:history)."
                    ), link)
                    continue
                person = next((m for m in hist.get("messages") or []
                               if m.get("user") == user_id and not m.get("subtype")), None)
                if person is None:
                    add("direct_message", None, (
                        f"User {user_id} has written nothing to this bot in direct messages. "
                        "Open the conversation with the link to write there."
                    ), link)
                else:
                    add("direct_message", *_judge(
                        person, dm, live, allowed,
                        place=f"The latest direct message user {user_id} wrote to the bot",
                    ), link)

    if allowed:
        add("allowed", True, f"{len(allowed)} allowed user(s): {', '.join(allowed)}.")
    elif live is not None and live.get("allow_all"):
        add("allowed", None, "Allowed User IDs is empty: anyone who can message the bot is answered.")
    else:
        add("allowed", False, (
            "Allowed User IDs is empty, so every message is refused. Put your Slack member ID "
            "there (your profile → ⋮ → Copy member ID)."
        ))

    ok, said = listening(live, "Slack", not_running=(
        "Kazma's Slack connection is not running: turn Slack on above and Save, or check the tokens."
    ))
    shared = (live or {}).get("slack_open_connections")
    if ok and isinstance(shared, int) and shared > 1:
        # Slack hands each event to ONE of the app's connections: another
        # program on this app-level token takes some of Kazma's messages.
        ok, said = None, (
            f"{said} But when Kazma connected ({show(when(live.get('slack_open_connections_at')))}) "
            f"Slack counted {shared} open connections for this app, and it hands each event to one "
            "of them: another program using this app-level token (a second Kazma, an old test bot) "
            "takes some of Kazma's messages. Stop it, or give it its own Slack app."
        )
    add("listening", ok, said)
    return checks.result(bot_name)
