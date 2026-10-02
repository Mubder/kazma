"""Every adapter says what it received, and every adapter's Test diagnoses
(2026-09-29).

The owner liked Discord's new Test -- real checks instead of "Connected" --
and asked for it on the other adapters. Telegram and Slack had Discord's
blind spots too: Telegram dropped an update it could not read without a word
and logged a user outside the allowlist at DEBUG; Slack logged every message
it RECEIVED at DEBUG only and dropped another channel's without a line at
INFO. Both now record every message (``kazma_gateway.receive_log``), and
their Tests ask the platform about the token, the delivery chat or channel,
direct messages and the connection.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
GATEWAY = REPO / "kazma-gateway" / "kazma_gateway"
NOW = datetime.now(timezone.utc)


# ── Telegram: every update accounted for ────────────────────────────────


def _tg(**kw: Any):
    from kazma_gateway.adapters.telegram import TelegramAdapter

    adapter = TelegramAdapter(token="123:abc", **kw)

    async def no_announce(update: dict) -> None:
        return None

    adapter._announce_if_added_to_group = no_announce  # type: ignore[method-assign]
    return adapter


def _tg_update(uid: int, mid: int, *, user: int = 7, text: str | None = "hi", **extra: Any) -> dict:
    message: dict[str, Any] = {
        "message_id": mid, "chat": {"id": user, "type": "private"},
        "from": {"id": user, "is_bot": False, "first_name": "B"}, "date": 1,
    }
    if text is not None:
        message["text"] = text
    message.update(extra)
    return {"update_id": uid, "message": message}


@pytest.mark.parametrize("setup, update, reason, level", [
    ({"allow_all": True}, _tg_update(1, 1, text=None, contact={"phone_number": "1"}), "unsupported", logging.INFO),
    ({"allowed_users": [9]}, _tg_update(1, 1, user=7), "user_not_allowed", logging.INFO),
    ({}, _tg_update(1, 1), "no_allowlist", logging.WARNING),
], ids=["contact", "not-allowed", "no-allowlist"])
def test_a_telegram_message_left_unanswered_says_why(caplog, setup, update, reason, level) -> None:
    adapter = _tg(allowed_users=setup.get("allowed_users"), allow_all=setup.get("allow_all", False))
    with caplog.at_level(logging.DEBUG, logger="kazma_gateway.adapters.telegram"):
        asyncio.run(adapter._process_update(update, asyncio.Queue()))
    snap = adapter.diagnostics()
    assert snap["dropped"] == {reason: 1} and snap["recent"] == {"7:1": reason}
    said = [r for r in caplog.records if "was not answered" in r.getMessage()]
    assert said and said[-1].levelno == level and "7" in said[-1].getMessage()


def test_no_telegram_message_is_unaccounted_for() -> None:
    adapter = _tg(allowed_users=[7])
    queue: asyncio.Queue = asyncio.Queue()
    updates = [
        _tg_update(1, 1),                                   # handed on
        _tg_update(2, 1),                                   # the same message again
        _tg_update(3, 2, user=8),                           # not allowed
        _tg_update(4, 3, text=None, contact={"x": 1}),      # nothing to read
        {"update_id": 5, "my_chat_member": {"chat": {"id": 7}}},  # no message at all
    ]

    async def run() -> None:
        for u in updates:
            await adapter._process_update(u, queue)

    asyncio.run(run())
    snap = adapter.diagnostics()
    assert snap["messages"] == 4
    assert snap["passed_on"] + sum(snap["dropped"].values()) == snap["messages"]
    assert queue.qsize() == 1
    assert snap["events"] == {"message": 4, "my_chat_member": 1}
    assert snap["last_person"]["author_id"] == "7"


# ── Slack: every event accounted for ────────────────────────────────────


def _slack(**kw: Any):
    from kazma_gateway.adapters.slack import SlackAdapter

    return SlackAdapter(bot_token="xoxb-t", app_token="xapp-t", **kw)


def _ev(ts: str, *, user: str = "U1", channel: str = "C1", text: str = "hi", **extra: Any) -> dict:
    return {"type": "message", "ts": ts, "user": user, "channel": channel, "text": text, **extra}


@pytest.mark.parametrize("setup, event, reason, level", [
    ({"allow_all": True}, _ev("1.0", bot_id="B1"), "from_a_bot", logging.DEBUG),
    ({"allow_all": True}, _ev("1.0", subtype="message_changed"), "not_a_new_message", logging.INFO),
    ({"allow_all": True, "allowed_channels": ["C9"]}, _ev("1.0"), "channel_not_allowed", logging.WARNING),
    ({}, _ev("1.0"), "no_allowlist", logging.WARNING),
    ({"allowed_users": ["U9"]}, _ev("1.0"), "user_not_allowed", logging.INFO),
], ids=["bot", "edit", "other-channel", "no-allowlist", "not-allowed"])
def test_a_slack_message_left_unanswered_says_why(caplog, setup, event, reason, level) -> None:
    adapter = _slack(**setup)
    with caplog.at_level(logging.DEBUG, logger="kazma_gateway.adapters.slack"):
        assert adapter._accept_event(event) is None
    snap = adapter.diagnostics()
    assert snap["dropped"] == {reason: 1} and snap["recent"] == {"C1:1.0": reason}
    lines = [r for r in caplog.records if r.name == "kazma_gateway.adapters.slack"]
    assert lines and lines[-1].levelno == level


def test_a_reaction_is_no_message_and_is_not_counted() -> None:
    adapter = _slack(allow_all=True)
    assert adapter._accept_event({"type": "reaction_added", "user": "U1"}) is None
    snap = adapter.diagnostics()
    assert snap["messages"] == 0 and snap["events"] == {"reaction_added": 1}


def test_a_slack_message_taken_is_logged_at_info(caplog) -> None:
    """It was DEBUG: the log could not say whether a Slack message arrived."""
    adapter = _slack(allowed_users=["U1"])
    adapter._queue = asyncio.Queue()
    event = _ev("2.0")
    incoming = adapter._accept_event(event)
    assert incoming is not None
    with caplog.at_level(logging.INFO, logger="kazma_gateway.adapters.slack"):
        asyncio.run(adapter._finalize_event(incoming, event))
    assert any("Enqueued from U1" in r.getMessage() and r.levelno == logging.INFO for r in caplog.records)
    assert adapter.diagnostics()["recent"]["C1:2.0"] == "passed_on"


def test_no_slack_message_is_unaccounted_for() -> None:
    """A mention arrives twice (app_mention + message): counted once."""
    adapter = _slack(allowed_users=["U1"])
    adapter._queue = asyncio.Queue()
    events = [
        _ev("1.0"),
        {**_ev("1.0"), "type": "app_mention"},
        _ev("2.0", user="U2"),
        _ev("3.0", bot_id="B1"),
        _ev("4.0", subtype="channel_join"),
    ]

    async def run() -> None:
        for e in events:
            incoming = adapter._accept_event(e)
            if incoming is not None:
                await adapter._finalize_event(incoming, e)

    asyncio.run(run())
    snap = adapter.diagnostics()
    assert snap["messages"] == 4
    assert snap["passed_on"] + sum(snap["dropped"].values()) == snap["messages"]
    assert adapter._queue.qsize() == 1


def test_the_slack_polling_path_records_too() -> None:
    adapter = _slack(allowed_users=["U1"], allowed_channels=["C1"])
    adapter._queue = asyncio.Queue()

    async def run() -> None:
        await adapter._handle_message("C1", {"ts": "1.0", "user": "U1", "text": "hi"})
        await adapter._handle_message("C2", {"ts": "2.0", "user": "U1", "text": "hi"})
        await adapter._handle_message("C1", {"ts": "3.0", "user": "U2", "text": "hi"})

    asyncio.run(run())
    snap = adapter.diagnostics()
    assert snap["recent"] == {"C1:1.0": "passed_on", "C2:2.0": "channel_not_allowed",
                              "C1:3.0": "user_not_allowed"}


# ── the Telegram Test ───────────────────────────────────────────────────


def _telegram(routes: dict[str, tuple[int, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        m = re.match(r"^/bot([^/]+)/(\w+)$", request.url.path)
        assert m, request.url.path
        token, method = m.groups()
        body = json.loads(request.content or b"{}") if request.content else {}
        status, payload = routes.get(f"{token}:{method}:{body.get('chat_id', '')}",
                                     routes.get(f"{token}:{method}", (404, {"ok": False, "description": "Not Found"})))
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def _tg_routes(**over: tuple[int, Any]) -> dict[str, tuple[int, Any]]:
    routes = {
        "123:abc:getMe": (200, {"ok": True, "result": {"id": 55, "username": "kazma_bot", "first_name": "Kazma",
                                                      "can_read_all_group_messages": True}}),
        "123:abc:getWebhookInfo": (200, {"ok": True, "result": {"url": "", "pending_update_count": 0}}),
        "123:abc:getChat:7": (200, {"ok": True, "result": {"id": 7, "type": "private", "username": "bader"}}),
        "123:abc:getChat:-100": (200, {"ok": True, "result": {"id": -100, "type": "supergroup", "title": "Ops"}}),
        "123:abc:getChatMember:-100": (200, {"ok": True, "result": {"status": "administrator"}}),
    }
    routes.update(over)
    return routes


def _tg_live(**over: Any) -> dict[str, Any]:
    live = {
        "connected": True, "connected_since": NOW.isoformat(), "session_since": NOW.isoformat(),
        "last_event_at": NOW.isoformat(), "started_at": NOW.isoformat(),
        "messages": 1, "passed_on": 1, "dropped": {}, "events": {"message": 1},
        "last_person": {"id": "7:1", "author_id": "7", "at": NOW.isoformat(), "where": "a private chat"},
        "recent": {"7:1": "passed_on"}, "allow_all": False,
    }
    live.update(over)
    return live


def _run_tg(routes, *, live=None, group=None, allowed=("7",), chat="7"):
    from kazma_gateway.adapters.telegram_diagnose import diagnose

    return asyncio.run(diagnose("123:abc", chat_id=chat, group=group, allowed_users=list(allowed),
                                live=live, transport=_telegram(routes)))


def _check(result: dict[str, Any], key: str) -> dict[str, Any]:
    return next(c for c in result["checks"] if c["key"] == key)


def test_telegram_all_is_well() -> None:
    result = _run_tg(_tg_routes(), live=_tg_live(), group={"enabled": True, "chat_id": -100})
    assert result["success"] is True and result["bot_name"] == "kazma_bot", result["checks"]
    assert [c["key"] for c in result["checks"]] == [
        "token", "receiving", "groups", "chat", "group", "latest", "allowed", "listening"]
    assert "@kazma_bot" in _check(result, "token")["detail"]
    assert "private chat with @bader" in _check(result, "chat")["detail"]
    assert "an administrator" in _check(result, "group")["detail"]
    assert "reached Kazma" in _check(result, "latest")["detail"]


@pytest.mark.parametrize("routes, live, group, key, ok, words", [
    (_tg_routes(**{"123:abc:getMe": (401, {"ok": False, "description": "Unauthorized"})}), None, None,
     "token", False, "@BotFather"),
    (_tg_routes(**{"123:abc:getWebhookInfo": (200, {"ok": True, "result": {"url": "https://x.example/hook"}})}),
     _tg_live(), None, "receiving", False, "refuses polling"),
    (_tg_routes(**{"123:abc:getWebhookInfo": (200, {"ok": True, "result": {
        "url": "https://my.kazma.ai/api/webhooks/telegram"}})}), _tg_live(), None, "receiving", None, "Kazma's webhook"),
    (_tg_routes(**{"123:abc:getMe": (200, {"ok": True, "result": {"id": 55, "username": "kazma_bot"}})}),
     _tg_live(), None, "groups", None, "Group Privacy"),
    (_tg_routes(**{"123:abc:getChat:7": (400, {"ok": False, "description": "Bad Request: chat not found"})}),
     _tg_live(), None, "chat", False, "press Start"),
    (_tg_routes(**{"123:abc:getChat:7": (403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})}),
     _tg_live(), None, "chat", False, "blocked the bot"),
    (_tg_routes(**{"123:abc:getChatMember:-100": (200, {"ok": True, "result": {"status": "left"}})}),
     _tg_live(), {"enabled": True, "chat_id": -100}, "group", False, "not in it"),
    (_tg_routes(), _tg_live(recent={"7:1": "user_not_allowed"}), None, "latest", False, "not answered"),
    (_tg_routes(), _tg_live(last_person=None), None, "latest", None, "No message from a person"),
    (_tg_routes(), _tg_live(connected=False, last_problem={"at": "t", "what": "Another program is collecting"}),
     None, "listening", False, "Another program"),
], ids=["token", "webhook-elsewhere", "own-webhook", "privacy-on", "chat-not-found", "blocked",
        "group-left", "refused", "nothing-yet", "conflict"])
def test_each_telegram_problem_is_named(routes, live, group, key, ok, words) -> None:
    result = _run_tg(routes, live=live, group=group)
    found = [c for c in result["checks"] if c["key"] == key]
    assert found and found[0]["ok"] is ok and words in found[0]["detail"], result["checks"]


def test_a_group_route_to_a_person_is_said_so_not_failed() -> None:
    """The live install's group route is a private chat: it delivers, and
    there is no group membership to fail on."""
    result = _run_tg(_tg_routes(), live=_tg_live(), group={"enabled": True, "chat_id": 7})
    group = _check(result, "group")
    assert group["ok"] is None and "a person's chat, not a group" in group["detail"], group
    assert result["success"] is True


def test_the_group_route_uses_its_own_bot() -> None:
    routes = _tg_routes(**{
        "999:grp:getMe": (200, {"ok": True, "result": {"id": 77, "username": "ops_bot"}}),
        "999:grp:getChat:-100": (200, {"ok": True, "result": {"id": -100, "type": "group", "title": "Ops"}}),
        "999:grp:getChatMember:-100": (200, {"ok": True, "result": {"status": "member"}}),
    })
    result = _run_tg(routes, live=_tg_live(), group={"enabled": True, "chat_id": -100, "bot_token": "999:grp"})
    assert "@ops_bot, is a member" in _check(result, "group")["detail"]


# ── the Slack Test ──────────────────────────────────────────────────────


SCOPES = "chat:write,channels:history,groups:history,im:history,app_mentions:read,im:write"


def _slack_api(routes: dict[str, dict[str, Any]], scopes: str = SCOPES) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        form = dict(httpx.QueryParams(request.content.decode())) if request.content else {}
        token = request.headers["Authorization"].removeprefix("Bearer ")
        body = routes.get(f"{method}:{form.get('channel') or form.get('users') or ''}:{token}",
                          routes.get(f"{method}:{form.get('channel') or form.get('users') or ''}",
                                     routes.get(method, {"ok": False, "error": "unknown_method"})))
        headers = {"x-oauth-scopes": scopes} if method == "auth.test" else {}
        return httpx.Response(200, json=body, headers=headers)

    return httpx.MockTransport(handler)


TS = f"{NOW.timestamp():.6f}"


def _slack_routes(**over: dict[str, Any]) -> dict[str, dict[str, Any]]:
    routes = {
        "auth.test": {"ok": True, "team": "Home", "team_id": "T1", "user": "kazma", "user_id": "UB"},
        "apps.connections.open:": {"ok": True, "url": "wss://x"},
        "conversations.info:C1": {"ok": True, "channel": {"id": "C1", "name": "kazma", "is_member": True}},
        "conversations.history:C1": {"ok": True, "messages": [
            {"ts": TS, "bot_id": "B1", "text": "reply"}, {"ts": TS, "user": "U1", "text": "hi"}]},
        "conversations.open:U1": {"ok": True, "channel": {"id": "D1"}},
        "conversations.history:D1": {"ok": True, "messages": [{"ts": TS, "user": "U1", "text": "dm"}]},
    }
    routes.update(over)
    return routes


def _slack_live(**over: Any) -> dict[str, Any]:
    live = _tg_live(recent={f"C1:{TS}": "passed_on", f"D1:{TS}": "passed_on"}, last_person=None)
    live.update(over)
    return live


def _run_slack(routes, *, live=None, app_token="xapp-t", allowed=("U1",), scopes=SCOPES):
    from kazma_gateway.adapters.slack_diagnose import diagnose

    return asyncio.run(diagnose("xoxb-t", app_token=app_token, channel_id="C1", allowed_users=list(allowed),
                                live=live, transport=_slack_api(routes, scopes)))


def test_slack_all_is_well() -> None:
    result = _run_slack(_slack_routes(), live=_slack_live())
    assert result["success"] is True and result["bot_name"] == "kazma", result["checks"]
    assert [c["key"] for c in result["checks"]] == [
        "token", "app_token", "scopes", "channel", "latest", "direct_message", "allowed", "listening"]
    dm = _check(result, "direct_message")
    assert "reached Kazma" in dm["detail"] and dm["link"] == "https://slack.com/app_redirect?channel=D1&team=T1"


@pytest.mark.parametrize("routes, live, kw, key, ok, words", [
    (_slack_routes(**{"auth.test": {"ok": False, "error": "invalid_auth"}}), None, {}, "token", False, "invalid_auth"),
    (_slack_routes(**{"apps.connections.open:": {"ok": False, "error": "invalid_auth"}}), _slack_live(), {},
     "app_token", False, "App-Level Tokens"),
    (_slack_routes(), _slack_live(), {"app_token": ""}, "app_token", None, "No app-level token"),
    (_slack_routes(), _slack_live(), {"scopes": "chat:write,channels:history"}, "scopes", False, "im:history"),
    (_slack_routes(**{"conversations.info:C1": {"ok": True, "channel": {"name": "kazma", "is_member": False}}}),
     _slack_live(), {}, "channel", False, "/invite"),
    (_slack_routes(), _slack_live(recent={f"C1:{TS}": "passed_on"}), {}, "direct_message", False, "never reached"),
    (_slack_routes(**{"conversations.history:D1": {"ok": True, "messages": []}}), _slack_live(), {},
     "direct_message", None, "written nothing"),
], ids=["token", "app-token", "no-app-token", "scope-missing", "bot-not-in-channel", "dm-never", "no-dm"])
def test_each_slack_problem_is_named(routes, live, kw, key, ok, words) -> None:
    result = _run_slack(routes, live=live, **kw)
    found = [c for c in result["checks"] if c["key"] == key]
    assert found and found[0]["ok"] is ok and words in found[0]["detail"], result["checks"]


def test_the_slack_test_says_when_another_program_shares_the_app() -> None:
    """Slack's count alone cannot say whether another program takes messages:
    it also counts a connection that ended without closing, for hours (live
    2026-10-01: 2 on a token used nowhere else, and all eight messages sent
    in a minute arrived). The messages the Test checks decide."""
    at = NOW.isoformat()
    # Every checked message reached Kazma: a stale count, said plainly.
    stale = _check(_run_slack(_slack_routes(), live=_slack_live(
        slack_open_connections=2, slack_open_connections_at=at)), "listening")
    assert stale["ok"] is True, stale
    assert "Slack counted 2 open connections" in stale["detail"]
    assert "ended without closing" in stale["detail"]
    # A message newer than the session never reached Kazma: another program.
    taken = _check(_run_slack(_slack_routes(), live=_slack_live(
        slack_open_connections=2, slack_open_connections_at=at,
        recent={f"D1:{TS}": "passed_on"})), "listening")
    assert taken["ok"] is None, taken
    assert "never reached Kazma" in taken["detail"] and "another program" in taken["detail"]
    alone = _check(_run_slack(_slack_routes(), live=_slack_live(
        slack_open_connections=1, slack_open_connections_at=at)), "listening")
    assert alone["ok"] is True and "counted" not in alone["detail"], alone


# ── Slack Socket Mode: why a connection ended ───────────────────────────


def _slack_adapter():
    from kazma_gateway.adapters.slack import SlackAdapter

    return SlackAdapter(bot_token="xoxb-t", app_token="xapp-t", allow_all=True)


def test_slack_says_why_it_reconnects(caplog) -> None:
    """Live 2026-09-30: one boot reconnected ten times in 30 s, and the log
    said only "disconnect received". A fake Slack (a real websocket server)
    asks for a refresh on the first connection and shakes hands on the
    second; the loop reconnects at once and says why."""
    import contextlib

    websockets = pytest.importorskip("websockets")
    caplog.set_level(logging.INFO, logger="kazma_gateway.adapters.slack")
    adapter = _slack_adapter()

    async def run() -> int:
        seen = {"n": 0}
        shutdown = asyncio.Event()

        async def slack(ws) -> None:
            seen["n"] += 1
            if seen["n"] == 1:
                await ws.send(json.dumps({"type": "disconnect", "reason": "refresh_requested",
                                          "debug_info": {"host": "applink-1"}}))
            else:
                await ws.send(json.dumps({"type": "hello", "num_connections": 1,
                                          "debug_info": {"host": "applink-2"}}))
                await asyncio.sleep(0.2)
                shutdown.set()
            with contextlib.suppress(websockets.ConnectionClosed):
                await ws.wait_closed()

        server = await websockets.serve(slack, "127.0.0.1", 0)
        url = f"ws://127.0.0.1:{next(iter(server.sockets)).getsockname()[1]}"
        adapter._http = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True, "url": url})))
        adapter._queue = asyncio.Queue()
        adapter._shutdown = shutdown
        try:
            await asyncio.wait_for(adapter._listen_socket_mode(), timeout=20)
        finally:
            await adapter._http.aclose()
            server.close()
            await server.wait_closed()
        return seen["n"]

    assert asyncio.run(run()) == 2
    said = [r.getMessage() for r in caplog.records if r.name == "kazma_gateway.adapters.slack"]
    assert "[Slack] Slack asked for a new connection (refresh_requested, host applink-1) — reconnecting" in said
    assert "[Slack] Socket Mode handshake confirmed (host applink-2; connections open for this app: 1)" in said
    assert adapter._receive.connected is True
    assert adapter._receive.last_problem is None, "a refresh Slack asked for is not a problem"


def test_socket_mode_switched_off_is_a_problem_with_its_fix(caplog) -> None:
    caplog.set_level(logging.INFO, logger="kazma_gateway.adapters.slack")
    adapter = _slack_adapter()
    adapter._on_hello({"type": "hello", "num_connections": 1})
    adapter._on_disconnect({"type": "disconnect", "reason": "link_disabled"})

    assert adapter._receive.connected is False
    assert "api.slack.com/apps" in adapter._receive.last_problem["what"]
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "link_disabled" in warned[0].getMessage()


def test_a_second_program_on_the_app_token_is_warned_once_the_count_is_settled(caplog) -> None:
    """Slack hands each event to ONE of the app's connections. A count taken
    right after Kazma's own reconnect may still include Kazma's old
    connection, so it is neither kept nor warned about."""
    import time

    caplog.set_level(logging.INFO, logger="kazma_gateway.adapters.slack")
    adapter = _slack_adapter()

    adapter._socket_ended_at = time.monotonic()           # Kazma just reconnected
    adapter._on_hello({"type": "hello", "num_connections": 2})
    assert "slack_open_connections" not in adapter.diagnostics()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    adapter._socket_ended_at = None                       # the process's first connection
    adapter._on_hello({"type": "hello", "num_connections": 2})
    live = adapter.diagnostics()
    assert live["slack_open_connections"] == 2 and live["slack_open_connections_at"]
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "2 open Socket Mode connections" in warned[0]


def test_a_full_app_is_said_once_per_run_of_refusals_and_its_count_is_kept(caplog) -> None:
    """Live 2026-10-02, two reloads: Slack refused Kazma's connection four and
    nine times in a row (``too_many_websockets``: the app already held its
    limit of 10) and then let it in with a hello counting 10 -- inside the
    settle window, so no count was kept, and nothing was said above INFO."""
    caplog.set_level(logging.INFO, logger="kazma_gateway.adapters.slack")
    adapter = _slack_adapter()

    for host in ("applink-0", "applink-5", "applink-8"):
        adapter._on_disconnect({"type": "disconnect", "reason": "too_many_websockets",
                                "debug_info": {"host": host}})
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1, "one warning per run of refusals, not one per retry"
    assert "too_many_websockets" in warned[0] and "limit of 10" in warned[0]
    assert adapter._receive.connected is False
    assert "limit of 10" in adapter._receive.last_problem["what"]

    caplog.clear()
    adapter._on_hello({"type": "hello", "num_connections": 10})    # 2-3 s after the last refusal
    live = adapter.diagnostics()
    assert live["slack_open_connections"] == 10 and live["slack_open_connections_at"]
    said = [r.getMessage() for r in caplog.records]
    assert "[Slack] Connected after Slack refused 3 connections in a row (too_many_websockets)" in said
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "10 open Socket Mode connections" in warned[0]

    caplog.clear()                                                 # a new run warns again
    adapter._on_disconnect({"type": "disconnect", "reason": "too_many_websockets"})
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1


# ── the route and the card, for every adapter ───────────────────────────


def test_the_routes_run_each_diagnosis_with_the_saved_settings(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.config_store import get_config_store
    from kazma_gateway.adapters import slack_diagnose, telegram_diagnose
    from kazma_ui import providers

    store = get_config_store()
    for key, value in {
        "connectors.telegram.token": "123:abc", "connectors.telegram.swarm_chat_id": "7",
        "connectors.telegram.allowed_users": "7", "connectors.slack.token": "xoxb-t",
        "connectors.slack.app_token": "****", "connectors.slack.swarm_channel_id": "C1",
        "connectors.slack.allowed_users": "U1",
    }.items():
        store.set(key, value, category="connectors")
    store.set("swarm.output_target", {"chat_id": -100, "enabled": True, "bot_token": ""}, category="swarm")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-env")
    got: dict[str, dict[str, Any]] = {}

    def fake(platform: str):
        async def run(token: str, **kw: Any) -> dict[str, Any]:
            got[platform] = {"token": token, **kw}
            return {"success": True, "bot_name": "b", "error": None, "checks": [{"key": "token", "ok": True, "detail": "d"}]}
        return run

    monkeypatch.setattr(telegram_diagnose, "diagnose", fake("telegram"))
    monkeypatch.setattr(slack_diagnose, "diagnose", fake("slack"))
    monkeypatch.setattr(providers, "_live_adapter_diagnostics", lambda platform: {"live": platform})
    app = FastAPI()
    app.include_router(providers.create_providers_router(store))
    client = TestClient(app)
    assert client.post("/api/connectors/telegram/test").json()["checks"]
    assert client.post("/api/connectors/slack/test").json()["checks"]
    assert got["telegram"] == {"token": "123:abc", "chat_id": "7", "allowed_users": ["7"],
                               "group": {"chat_id": -100, "enabled": True, "bot_token": ""},
                               "live": {"live": "telegram"}}
    assert got["slack"] == {"token": "xoxb-t", "app_token": "xapp-env", "channel_id": "C1",
                            "allowed_users": ["U1"], "live": {"live": "slack"}}


def test_every_check_of_every_adapter_has_a_title_on_the_card() -> None:
    keys: set[str] = set()
    for name in ("discord_diagnose", "telegram_diagnose", "slack_diagnose"):
        src = (GATEWAY / "adapters" / f"{name}.py").read_text(encoding="utf-8")
        keys |= set(re.findall(r'add\("([a-z_]+)"', src))
    keys.add("listening")
    js = (REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "settings_hub.js").read_text(encoding="utf-8")
    body = js[js.index("connectorCheckTitle(key)"):]
    titled = set(re.findall(r"^\s*([a-z_]+): _k\(", body[: body.index("return titles")], re.M))
    assert keys == titled, (keys - titled, titled - keys)

    html = (REPO / "kazma-ui" / "kazma_ui" / "templates" / "settings.html").read_text(encoding="utf-8")
    macro = html[html.index("{% macro connector_checks"): html.index("{% endmacro %}")]
    assert "connectorCheckTitle(c.key)" in macro and 'translate="no" x-text="c.detail"' in macro
    assert 'x-if="connectorLinkOk(c.link)"' in macro and 'rel="noopener noreferrer"' in macro
    for card, platform in (("<!-- Telegram main bot -->", "telegram"), ("<!-- Discord -->", "discord"),
                           ("<!-- Slack -->", "slack")):
        start = html.index(card)
        assert f"{{{{ connector_checks('{platform}') }}}}" in html[start: start + 6000], platform
    # A check's link opens the platform's own app only.
    guard = js[js.index("connectorLinkOk(link)"):]
    guard = guard[: guard.index("},")]
    assert "https://discord.com/" in guard and "https://slack.com/" in guard
    assert guard.count("startsWith(") == 2
