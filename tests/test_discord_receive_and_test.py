"""Kazma's Discord connection says what it received, and its Test says why a
message went unanswered (2026-09-29).

The owner sent Kazma a message on Discord and got no reply; the live log held
no line about it, nor about any Discord message for eight days, and the
connector's Test said "Connected" (it only checked the token). The adapter
left a message without a word when it had no text, came from a server outside
the Guild ID, or came from a bot.

- Every MESSAGE_CREATE is accounted for: handed on, or dropped with a reason
  that is logged and recorded (``discord_receive``).
- The Test (``discord_diagnose``) asks Discord about the token, the Message
  Content Intent, the servers, the delivery channel and the newest message a
  person wrote there -- and says whether that message reached Kazma.
- The card's Guild ID, saved and read by nothing, now limits server messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO = Path(__file__).resolve().parents[1]
ADAPTERS = REPO / "kazma-gateway" / "kazma_gateway" / "adapters"


def _adapter(**kw: Any):
    from kazma_gateway.adapters.discord import DiscordAdapter

    return DiscordAdapter(token="fake:token", **kw)


def _msg(mid: str = "m1", *, author: str = "u1", content: str = "hello", guild: str | None = None,
         bot: bool = False, channel: str = "c1", attachments: list | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": mid, "channel_id": channel, "content": content,
        "author": {"id": author, "username": f"name-{author}", "bot": bot},
        "attachments": attachments or [],
    }
    if guild:
        data["guild_id"] = guild
    return data


# ── every way a message is left, recorded and said ─────────────────────


@pytest.mark.parametrize(
    "setup, data, reason, level",
    [
        ({"allow_all": True}, _msg(bot=True), "from_a_bot", logging.DEBUG),
        ({"allow_all": True}, _msg(content="", guild="g1"), "no_text", logging.WARNING),
        ({"allow_all": True}, _msg(channel=""), "no_channel", logging.INFO),
        ({"allowed_users": ["u2"]}, _msg(author="u1"), "user_not_allowed", logging.INFO),
        ({}, _msg(), "no_allowlist", logging.WARNING),
        ({"allow_all": True, "guilds": ["g1"]}, _msg(guild="g2"), "server_not_allowed", logging.WARNING),
    ],
    ids=["bot", "no-text", "no-channel", "not-allowed", "no-allowlist", "other-server"],
)
def test_a_message_left_unanswered_says_why(caplog, setup, data, reason, level) -> None:
    adapter = _adapter(allowed_users=setup.get("allowed_users"), allow_all=setup.get("allow_all", False))
    if setup.get("guilds"):
        adapter.set_allowed_guilds(setup["guilds"])
    with caplog.at_level(logging.DEBUG, logger="kazma_gateway.adapters.discord"):
        assert adapter._accept_message(data) is None
    snap = adapter.diagnostics()
    assert snap["dropped"] == {reason: 1}
    assert snap["recent"][data["id"]] == reason
    lines = [r for r in caplog.records if r.name == "kazma_gateway.adapters.discord"]
    assert lines and lines[-1].levelno == level, [(r.levelname, r.getMessage()) for r in lines]
    if reason != "from_a_bot":
        assert data["author"]["id"] in lines[-1].getMessage()
        assert snap["last_human_drop"]["reason"] == reason


def test_a_repeated_setting_problem_warns_once_per_ten_minutes(caplog) -> None:
    adapter = _adapter(allow_all=True)
    with caplog.at_level(logging.INFO, logger="kazma_gateway.adapters.discord"):
        adapter._accept_message(_msg("a", content="", guild="g1"))
        adapter._accept_message(_msg("b", content="", guild="g1"))
    levels = [r.levelno for r in caplog.records if r.name == "kazma_gateway.adapters.discord"]
    assert levels == [logging.WARNING, logging.INFO]
    assert adapter.diagnostics()["dropped"] == {"no_text": 2}


def test_a_direct_message_passes_the_guild_filter_and_is_handed_on() -> None:
    adapter = _adapter(allowed_users=["u1"])
    adapter.set_allowed_guilds(["g1"])
    data = _msg("dm1")  # no guild: a direct message
    parsed = adapter._accept_message(data)
    assert parsed is not None and adapter.diagnostics()["recent"]["dm1"] == "accepted"

    queue: asyncio.Queue = asyncio.Queue()
    asyncio.run(adapter._process_and_enqueue(parsed, queue))
    snap = adapter.diagnostics()
    assert queue.qsize() == 1
    assert snap["passed_on"] == 1 and snap["recent"]["dm1"] == "passed_on"


def test_every_parser_refusal_has_its_own_reason() -> None:
    """Gate: drop_reason mirrors every way parse_message_create returns None
    -- a new refusal there without a reason here fails."""
    from kazma_gateway.adapters.discord_parse import drop_reason, parse_message_create
    from kazma_gateway.adapters.discord_receive import DROP_REASONS

    cases = {
        "empty_event": None,
        "from_a_bot": _msg(bot=True),
        "no_text": _msg(content="  "),
        "no_channel": _msg(channel=""),
    }
    for reason, data in cases.items():
        assert parse_message_create(data) is None, reason
        assert drop_reason(data) == reason
        assert reason in DROP_REASONS
    src = (ADAPTERS / "discord_parse.py").read_text(encoding="utf-8")
    body = src[src.index("def parse_message_create"):]
    assert body.count("return None") == len(cases), (
        "parse_message_create gained a refusal: give it a reason in drop_reason"
    )


class _Socket:
    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = [json.dumps({"op": 10, "d": {"heartbeat_interval": 3_600_000}})]
        self._frames += [json.dumps(f) for f in frames]
        self.sent: list[Any] = []

    async def recv(self) -> str:
        return self._frames.pop(0)

    async def send(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self._frames.clear()

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


def test_no_received_message_is_unaccounted_for(monkeypatch) -> None:
    """Every MESSAGE_CREATE the connection reads is handed on or dropped with
    a reason -- run through the real receive loop."""
    import websockets

    frames = [
        {"op": 0, "t": "READY", "s": 1, "d": {"session_id": "s", "resume_gateway_url": "wss://r",
                                              "guilds": [{"id": "g1"}], "user": {"id": "bot"}}},
        {"op": 0, "t": "GUILD_CREATE", "s": 2, "d": {"id": "g1"}},
        {"op": 0, "t": "MESSAGE_CREATE", "s": 3, "d": _msg("1", guild="g1")},
        {"op": 0, "t": "MESSAGE_CREATE", "s": 4, "d": _msg("2", bot=True, guild="g1")},
        {"op": 0, "t": "MESSAGE_CREATE", "s": 5, "d": _msg("3", content="", guild="g1")},
        {"op": 0, "t": "MESSAGE_CREATE", "s": 6, "d": _msg("4", author="stranger")},
        {"op": 0, "t": "MESSAGE_CREATE", "s": 7, "d": _msg("5")},
    ]
    sock = _Socket(frames)

    class _Ctx:
        async def __aenter__(self):
            return sock

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr(websockets, "connect", lambda *a, **k: _Ctx())
    adapter = _adapter(allowed_users=["u1"])

    async def run() -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        try:
            await adapter._connect_gateway(queue, asyncio.Event())
            await asyncio.gather(*adapter._channel_chains.values())
        finally:
            if adapter._heartbeat_task is not None:
                adapter._heartbeat_task.cancel()
        return queue

    queue = asyncio.run(run())
    snap = adapter.diagnostics()
    assert snap["messages"] == 5
    assert snap["passed_on"] + sum(snap["dropped"].values()) == snap["messages"]
    assert queue.qsize() == 2
    assert snap["events"] == {"READY": 1, "GUILD_CREATE": 1, "MESSAGE_CREATE": 5}
    assert snap["guilds_at_ready"] == 1 and snap["bot_user_id"] == "bot"
    assert snap["recent"] == {"1": "passed_on", "2": "from_a_bot", "3": "no_text",
                              "4": "user_not_allowed", "5": "passed_on"}


# ── the Test ─────────────────────────────────────────────────────────────


NOW = datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _discord(routes: dict[str, tuple[int, Any]]) -> httpx.MockTransport:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v10")
        seen.append(path)
        assert request.headers["Authorization"] == "Bot tok"
        status, body = routes.get(path, (404, {"message": "Unknown"}))
        return httpx.Response(status, json=body)

    transport = httpx.MockTransport(handler)
    transport.seen = seen  # type: ignore[attr-defined]
    return transport


def _good_routes(**over: tuple[int, Any]) -> dict[str, tuple[int, Any]]:
    routes = {
        "/users/@me": (200, {"id": "bot", "username": "Kazma"}),
        "/applications/@me": (200, {"flags": 1 << 19}),
        "/users/@me/guilds": (200, [{"id": "g1", "name": "Home"}]),
        "/channels/c1": (200, {"id": "c1", "type": 1, "recipients": [{"id": "u1", "username": "bader"}]}),
        "/channels/c1/messages": (200, [
            {"id": "k", "author": {"id": "bot", "username": "Kazma", "bot": True}, "timestamp": _iso(NOW)},
            {"id": "m9", "author": {"id": "u1", "username": "bader"}, "timestamp": _iso(NOW)},
        ]),
        "/users/@me/channels": (200, {"id": "dm1", "type": 1}),
        "/channels/dm1/messages": (200, [
            {"id": "d7", "author": {"id": "u1", "username": "bader"}, "timestamp": _iso(NOW)},
        ]),
    }
    routes.update(over)
    return routes


def _live(**over: Any) -> dict[str, Any]:
    live = {
        "connected": True, "connected_since": _iso(NOW - timedelta(hours=1)),
        "session_since": _iso(NOW - timedelta(hours=1)), "last_event_at": _iso(NOW),
        "messages": 3, "passed_on": 2, "dropped": {"from_a_bot": 1}, "events": {"MESSAGE_CREATE": 3},
        "recent": {"m9": "passed_on", "d7": "passed_on"}, "allow_all": False,
    }
    live.update(over)
    return live


def _run(routes, *, live=None, allowed=("u1",), guild_ids=(), channel="c1"):
    from kazma_gateway.adapters.discord_diagnose import diagnose

    return asyncio.run(diagnose(
        "tok", channel_id=channel, guild_ids=list(guild_ids), allowed_users=list(allowed),
        live=live, transport=_discord(routes),
    ))


def _check(result: dict[str, Any], key: str) -> dict[str, Any]:
    return next(c for c in result["checks"] if c["key"] == key)


def test_all_is_well() -> None:
    result = _run(_good_routes(), live=_live())
    assert result["success"] is True and result["bot_name"] == "Kazma", result
    assert [c["key"] for c in result["checks"]] == [
        "token", "message_text", "servers", "channel", "latest", "direct_message", "allowed", "listening", "commands"]
    assert all(c["ok"] is True for c in result["checks"] if c["key"] != "commands"), result["checks"]
    assert _check(result, "commands")["ok"] is None
    assert "direct message with bader (user u1)" in _check(result, "channel")["detail"]
    assert "reached Kazma" in _check(result, "latest")["detail"]
    assert "direct message user u1 wrote to the bot" in _check(result, "direct_message")["detail"]
    assert _check(result, "direct_message")["link"] == "https://discord.com/channels/@me/dm1"
    assert "link" not in _check(result, "token")


def test_a_refused_token_stops_there() -> None:
    result = _run(_good_routes(**{"/users/@me": (401, {"message": "401: Unauthorized"})}), live=_live())
    assert result["success"] is False and "401" in result["error"]
    assert [c["key"] for c in result["checks"]] == ["token", "allowed", "listening", "commands"]


@pytest.mark.parametrize(
    "routes, live, allowed, guild_ids, key, words",
    [
        (_good_routes(**{"/applications/@me": (200, {"flags": 0})}), _live(), ("u1",), (),
         "message_text", "MESSAGE CONTENT INTENT"),
        (_good_routes(**{"/channels/c1": (403, {"message": "Missing Access"})}), _live(), ("u1",), (),
         "channel", "cannot see channel c1"),
        (_good_routes(), _live(), ("u2",), (), "latest", "put u1 in Allowed User IDs"),
        (_good_routes(), _live(recent={}), ("u1",), (), "latest", "never reached Kazma"),
        (_good_routes(), _live(recent={"m9": "no_text"}), ("u1",), (), "latest", "Message Content Intent"),
        (_good_routes(), _live(connected=False), ("u1",), (), "listening", "down right now"),
        (_good_routes(), None, ("u1",), (), "listening", "not running"),
        (_good_routes(), _live(), ("u1",), ("g7",), "servers", "Guild ID g7"),
        (_good_routes(), _live(), (), (), "allowed", "every message is refused"),
    ],
    ids=["intent-off", "channel-hidden", "author-not-allowed", "never-delivered", "dropped-no-text",
         "connection-down", "not-running", "guild-not-joined", "no-allowlist"],
)
def test_each_problem_is_named_with_its_fix(routes, live, allowed, guild_ids, key, words) -> None:
    result = _run(routes, live=live, allowed=allowed, guild_ids=guild_ids)
    failing = [c for c in result["checks"] if c["key"] == key and c["ok"] is False]
    assert failing and words in failing[0]["detail"], result["checks"]
    assert result["success"] is False


def test_a_message_from_before_the_session_is_not_blamed_on_the_connection() -> None:
    """A message older than the current session (it may well have been
    answered by the run before) is worth knowing, not a fault."""
    old = [{"id": "m1", "author": {"id": "u1", "username": "bader"},
            "timestamp": _iso(NOW - timedelta(hours=3))}]
    result = _run(_good_routes(**{"/channels/c1/messages": (200, old)}), live=_live(recent={"d7": "passed_on"}))
    latest = _check(result, "latest")
    assert latest["ok"] is None and "older than Kazma's current Discord session" in latest["detail"]
    assert "cannot say what became of it" in latest["detail"]
    assert result["success"] is True


def test_direct_messages_that_never_arrive_are_named() -> None:
    """The owner's report: answered in the server, silent in direct messages."""
    result = _run(_good_routes(), live=_live(recent={"m9": "passed_on"}))
    dm = _check(result, "direct_message")
    assert dm["ok"] is False and "never reached Kazma" in dm["detail"], dm
    assert _check(result, "latest")["ok"] is True
    assert result["success"] is False


def test_no_direct_message_to_this_bot_points_at_another_bot() -> None:
    """Live 2026-09-29: the owner's DMs went to another bot account with the
    same name. The Test says so, names this bot by name and tag, and links
    to the one conversation between the user and THIS bot."""
    routes = _good_routes(**{
        "/channels/dm1/messages": (200, []),
        "/users/@me": (200, {"id": "bot", "username": "KazmaAI", "discriminator": "6245"}),
    })
    result = _run(routes, live=_live())
    dm = _check(result, "direct_message")
    assert dm["ok"] is None, dm
    assert "KazmaAI#6245 (bot id bot)" in dm["detail"] and "another bot account" in dm["detail"]
    assert dm["link"] == "https://discord.com/channels/@me/dm1"
    assert "KazmaAI#6245" in _check(result, "token")["detail"]


def test_opening_the_direct_messages_sends_nothing() -> None:
    from kazma_gateway.adapters.discord_diagnose import diagnose

    calls: list[tuple[str, str]] = []
    routes = _good_routes()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v10")
        calls.append((request.method, path))
        status, body = routes.get(path, (404, {}))
        return httpx.Response(status, json=body)

    asyncio.run(diagnose("tok", channel_id="c1", allowed_users=["u1"], live=_live(),
                         transport=httpx.MockTransport(handler)))
    writes = [c for c in calls if c[0] != "GET"]
    assert writes == [("POST", "/users/@me/channels")], "the only write opens (or returns) the DM channel"
    assert not any(p.endswith("/messages") and m == "POST" for m, p in calls)


def test_the_test_never_shows_what_was_written() -> None:
    secret = "my private words"
    history = [{"id": "m9", "author": {"id": "u1", "username": "bader"}, "timestamp": _iso(NOW),
                "content": secret}]
    result = _run(_good_routes(**{"/channels/c1/messages": (200, history)}), live=_live())
    assert secret not in json.dumps(result)


# ── the route and the card ──────────────────────────────────────────────


def test_the_route_runs_the_diagnosis_with_the_saved_settings(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_core.config_store import get_config_store
    from kazma_gateway.adapters import discord_diagnose
    from kazma_ui import providers

    store = get_config_store()
    for key, value in {"token": "tok", "swarm_channel_id": "c1", "guild_id": "g1, g2",
                       "allowed_users": "u1"}.items():
        store.set(f"connectors.discord.{key}", value, category="connectors")
    got: dict[str, Any] = {}

    async def fake(token: str, **kw: Any) -> dict[str, Any]:
        got.update(kw, token=token)
        return {"success": False, "bot_name": "Kazma", "error": "x",
                "checks": [{"key": "token", "ok": True, "detail": "d"}]}

    monkeypatch.setattr(discord_diagnose, "diagnose", fake)
    monkeypatch.setattr(providers, "_live_adapter_diagnostics", lambda platform: {"live": platform})
    app = FastAPI()
    app.include_router(providers.create_providers_router(store))
    body = TestClient(app).post("/api/connectors/discord/test").json()
    assert body["checks"] == [{"key": "token", "ok": True, "detail": "d"}], "the response model keeps the checks"
    assert got == {"token": "tok", "channel_id": "c1", "guild_ids": ["g1", "g2"],
                   "allowed_users": ["u1"], "live": {"live": "discord"}}


def test_the_guild_id_reaches_the_running_adapter() -> None:
    from kazma_gateway.allowlists import apply_adapter_allowlists

    class Store:
        def get(self, key: str, default: Any = "") -> Any:
            return {"connectors.discord.guild_id": "g1,g2", "connectors.discord.allowed_users": "u1"}.get(key, default)

    adapter = _adapter()
    apply_adapter_allowlists(adapter, Store())
    assert adapter.diagnostics()["allowed_guilds"] == ["g1", "g2"]
