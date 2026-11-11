"""A separate Telegram bot for Kazma's alerts (Settings -> Alerts, 2026-11-11).

Every alert Kazma sent -- the ops alerts, the daily digest, the weekly
report, the start card, the guard's pages -- went through Kazma's chat bot,
the one the owner talks to, so the alerts landed among his chats with Kazma
and there was no setting to move them. Now ``notifications.ops.telegram_bot``
chooses ``chat_bot`` (as before) or ``alerts_bot``: a token (in the vault,
install-scoped) and a chat. With it:

* the Telegram route goes to the alerts bot's chat and Kazma's chat bot does
  not send it again; the group route goes to the alerts group with it;
* if the alerts bot does not take an alert, Kazma's chat bot sends it (a
  revoked token never silences an alert);
* the start card follows the same rule, and so does the guard's pager (its
  settings lookup runs ``pager_credentials``, read again after ten minutes);
* the Settings save asks Telegram first: a token it does not know is refused
  and nothing is written; a chat the bot cannot see yet is saved and said.

Each rule with its negative control: the delivery as it was.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import sys
import threading
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kazma_core.observability import ops_alerts

REPO = Path(__file__).resolve().parents[1]
BOT_TOKEN = "987654321:AAalerts-bot-token-for-tests-only-xyz"
MAIN_TOKEN = "123456789:AAmain-chat-bot-token-for-tests-xyz"


class _Store:
    """A settings store holding plain values (the vault is not this test's)."""

    def __init__(self, **values):
        self.values = {"connectors.telegram.token": MAIN_TOKEN, "guard.telegram.chat_id": "111", **values}

    def get(self, key, default=None):
        return self.values.get(key, default)


def _own_bot(**extra) -> _Store:
    return _Store(**{ops_alerts.ALERTS_BOT_KEY: "alerts_bot", ops_alerts.ALERTS_BOT_TOKEN_KEY: BOT_TOKEN,
                     ops_alerts.ALERTS_BOT_CHAT_KEY: "-100555", **extra})


class _Adapter:
    def __init__(self, name: str, takes: bool = True) -> None:
        self.name = name
        self.takes = takes
        self.sent: list[str] = []

    async def send(self, msg):
        self.sent.append(msg.content)
        return self.takes


@pytest.fixture
def wire(monkeypatch):
    """A bus of Telegram (Kazma's chat bot) and Discord, a recording Bot API,
    and the store the test hands in."""
    import kazma_core.swarm.bus as bus_mod

    telegram, discord = _Adapter("telegram"), _Adapter("discord")
    fan = bus_mod.FanOutBusAdapter([telegram, discord])
    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: SimpleNamespace(adapter=fan))
    monkeypatch.setattr(ops_alerts, "_ops_channels", lambda: [])
    posts: list[tuple[str, str]] = []
    state = SimpleNamespace(telegram=telegram, discord=discord, posts=posts, takes=True, store=_Store())

    def post(token, chat, text):
        posts.append((token, chat))
        return state.takes if token == BOT_TOKEN else True

    monkeypatch.setattr(ops_alerts, "_send_telegram", post)
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: state.store)
    return state


def test_the_alerts_bot_takes_the_telegram_route_and_the_chat_bot_leaves_it(wire):
    wire.store = _own_bot()
    outcome: dict = {}
    assert asyncio.run(ops_alerts._deliver("the alert", outcome)) is True
    assert wire.posts == [(BOT_TOKEN, "-100555")]
    assert wire.telegram.sent == [], "Kazma's chat bot sent it too"
    assert wire.discord.sent == ["the alert"]
    assert outcome["sent"] == ["telegram", "discord"]


def test_with_kazma_s_chat_bot_chosen_nothing_changes(wire):
    """Negative control: the default (and every alert before the setting)."""
    wire.store = _Store(**{ops_alerts.ALERTS_BOT_KEY: "chat_bot", ops_alerts.ALERTS_BOT_TOKEN_KEY: BOT_TOKEN,
                           ops_alerts.ALERTS_BOT_CHAT_KEY: "-100555"})
    assert asyncio.run(ops_alerts._deliver("the alert")) is True
    assert wire.posts == [] and wire.telegram.sent == ["the alert"]


def test_an_alerts_bot_that_refuses_hands_the_alert_to_the_chat_bot(wire):
    wire.store = _own_bot()
    wire.takes = False
    outcome: dict = {}
    assert asyncio.run(ops_alerts._deliver("the alert", outcome)) is True
    assert wire.posts == [(BOT_TOKEN, "-100555")]
    assert wire.telegram.sent == ["the alert"], "a refused alerts bot must not silence the alert"
    assert outcome["failed"] == [ops_alerts.ALERTS_BOT_ROUTE] and "telegram" in outcome["sent"]


def test_in_a_process_with_no_bus_the_alerts_bot_alone_sends(wire, monkeypatch):
    import kazma_core.swarm.bus as bus_mod

    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: SimpleNamespace(adapter=bus_mod.NullBusAdapter()))
    wire.store = _own_bot()
    assert asyncio.run(ops_alerts._deliver("from a worker")) is True
    assert wire.posts == [(BOT_TOKEN, "-100555")], "the chat bot's direct send ran as well"


def test_routes_without_telegram_never_reach_the_alerts_bot(wire, monkeypatch):
    monkeypatch.setattr(ops_alerts, "_ops_channels", lambda: ["discord"])
    wire.store = _own_bot()
    assert asyncio.run(ops_alerts._deliver("the alert")) is True
    assert wire.posts == [] and wire.discord.sent == ["the alert"]


def test_the_group_route_goes_to_the_alerts_group_with_the_alerts_bot(wire, monkeypatch):
    monkeypatch.setattr(ops_alerts, "_ops_channels", lambda: ["telegram-group"])
    wire.store = _own_bot(**{ops_alerts.ALERTS_GROUP_KEY: "-100777"})
    assert asyncio.run(ops_alerts._deliver("the alert")) is True
    assert wire.posts == [(BOT_TOKEN, "-100777")]
    # No alerts group: the route is the chat bot's, as before.
    wire.posts.clear()
    wire.store = _own_bot()
    direct: list = []
    monkeypatch.setattr(ops_alerts, "_telegram_direct", lambda text, **kw: direct.append(kw) or True)
    assert asyncio.run(ops_alerts._deliver("the alert")) is True
    assert wire.posts == [] and direct == [{"group_route": True}]


def test_an_incomplete_alerts_bot_is_kazma_s_chat_bot():
    assert ops_alerts.alerts_bot(_own_bot(**{ops_alerts.ALERTS_BOT_CHAT_KEY: ""})) is None
    assert ops_alerts.alerts_bot(_own_bot(**{ops_alerts.ALERTS_BOT_TOKEN_KEY: "vault://cfg:x"})) is None
    assert ops_alerts.alerts_bot(_own_bot()) == ops_alerts.AlertsBot(BOT_TOKEN, "-100555")


def test_the_start_card_follows_the_alerts_bot(wire):
    from kazma_core import lifecycle_notifier

    wire.store = _own_bot()
    delivered = asyncio.run(lifecycle_notifier._deliver("Kazma started", "info"))
    assert wire.posts == [(BOT_TOKEN, "-100555")] and wire.telegram.sent == []
    assert sorted(delivered) == ["discord", "telegram"]


def test_every_bus_route_picker_caller_decides_the_telegram_route():
    """``skip_telegram`` has no default: a new sender of Kazma's alerts that
    forgets the alerts bot fails, it does not quietly use the chat bot."""
    import inspect

    param = inspect.signature(ops_alerts.bus_send_targets).parameters["skip_telegram"]
    assert param.default is inspect.Parameter.empty and param.kind is inspect.Parameter.KEYWORD_ONLY
    with pytest.raises(TypeError):
        ops_alerts.bus_send_targets(None, [])  # type: ignore[call-arg]


# -- the guard ------------------------------------------------------------------------------


def test_the_guard_pages_with_the_alerts_bot():
    assert ops_alerts.pager_credentials(_own_bot()) == (BOT_TOKEN, "-100555")
    assert ops_alerts.pager_credentials(_Store()) == (MAIN_TOKEN, "111")


def _load_guard():
    spec = importlib.util.spec_from_file_location("_alerts_bot_guard", REPO / "scripts" / "service" / "kazma_guard.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_lookup(code: str, store, monkeypatch) -> dict:
    """The guard's lookup code as its child runs it, against *store*."""
    monkeypatch.setattr("kazma_core.env_files.load_env_files", lambda *a, **k: None)
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    out = io.StringIO()
    with redirect_stdout(out):
        exec(compile(code, "<guard lookup>", "exec"), {})
    line = [x for x in out.getvalue().splitlines() if x.startswith("KAZMA_GUARD_NOTIFY ")][-1]
    return json.loads(line[len("KAZMA_GUARD_NOTIFY "):])


def test_the_guard_s_settings_lookup_reads_the_alerts_bot(monkeypatch):
    guard = _load_guard()
    assert _run_lookup(guard._NOTIFY_LOOKUP, _own_bot(), monkeypatch) == {"token": BOT_TOKEN, "chat": "-100555"}
    assert _run_lookup(guard._NOTIFY_LOOKUP, _Store(), monkeypatch) == {"token": MAIN_TOKEN, "chat": "111"}
    # Negative control: the lookup as it was paged with the chat bot whatever was chosen.
    old = guard._NOTIFY_LOOKUP.replace(
        "    from kazma_core.observability.ops_alerts import pager_credentials\n"
        "    token, chat = pager_credentials(get_config_store())\n",
        "    cs = get_config_store()\n"
        "    token = str(cs.get('connectors.telegram.token', '') or '').strip()\n"
        "    chat = str(cs.get('guard.telegram.chat_id', '') or '').strip()\n")
    assert old != guard._NOTIFY_LOOKUP
    assert _run_lookup(old, _own_bot(), monkeypatch)["token"] == MAIN_TOKEN


def test_the_guard_reads_its_settings_again_after_ten_minutes(monkeypatch, tmp_path):
    guard = _load_guard()
    for name in ("KAZMA_GUARD_TELEGRAM_TOKEN", "KAZMA_GUARD_TELEGRAM_CHAT", "SWARM_BOT_TOKEN", "SWARM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    answers = [("main", "111"), ("bot", "-100555"), ("", "")]
    monkeypatch.setattr(guard.Notifier, "_from_config_store", lambda self: answers.pop(0))
    n = guard.Notifier(guard.GuardLog(tmp_path / "g.log"))
    assert n.configured and (n.token, n.chat) == ("main", "111")
    assert n.configured and len(answers) == 2, "read again inside the window"
    n._looked_up_at -= guard._NOTIFY_REFRESH_S + 1
    assert n.configured and (n.token, n.chat) == ("bot", "-100555"), "a changed bot never reached the guard"
    n._looked_up_at -= guard._NOTIFY_REFRESH_S + 1
    assert n.configured and (n.token, n.chat) == ("bot", "-100555"), "a failed re-read must keep the ones that worked"


def test_the_guard_never_re_reads_environment_credentials(monkeypatch, tmp_path):
    guard = _load_guard()
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "env-token")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "42")

    def no_lookup(self):
        raise AssertionError("the guard looked up settings over its environment's credentials")

    monkeypatch.setattr(guard.Notifier, "_from_config_store", no_lookup)
    n = guard.Notifier(guard.GuardLog(tmp_path / "g.log"))
    n._looked_up_at = -10**9
    assert n.configured and n.token == "env-token"


# -- Telegram's answer to a save -------------------------------------------------------------


class _Telegram(BaseHTTPRequestHandler):
    """A stand-in Bot API: getMe for the tokens it knows, getChat for the
    chats they see, sendMessage recorded in ``sent`` as (token, chat)."""

    known: set[str] = set()
    chats: set[str] = set()
    sent: list[tuple[str, str]] = []

    def do_POST(self):
        from urllib.parse import parse_qs

        token = self.path.split("/bot", 1)[1].split("/", 1)[0]
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        chat = parse_qs(raw).get("chat_id", [""])[0]
        if token not in self.known or chat not in self.chats:
            return self._reply(400, {"ok": False, "description": "Bad Request: chat not found"})
        self.sent.append((token, chat))
        return self._reply(200, {"ok": True, "result": {"message_id": len(self.sent)}})

    def do_GET(self):
        token = self.path.split("/bot", 1)[1].split("/", 1)[0]
        method = self.path.split("/")[2].split("?")[0]
        if token not in self.known:
            return self._reply(401, {"ok": False, "description": "Unauthorized"})
        if method == "getMe":
            return self._reply(200, {"ok": True, "result": {"username": "kazma_alerts_bot"}})
        chat = self.path.split("chat_id=", 1)[1] if "chat_id=" in self.path else ""
        from urllib.parse import unquote

        if unquote(chat) in self.chats:
            return self._reply(200, {"ok": True, "result": {"id": chat}})
        return self._reply(400, {"ok": False, "description": "Bad Request: chat not found"})

    def _reply(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        return


@pytest.fixture
def telegram(monkeypatch):
    handler = type("T", (_Telegram,), {"known": {BOT_TOKEN}, "chats": {"-100555"}, "sent": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(ops_alerts, "TELEGRAM_API", f"http://127.0.0.1:{server.server_address[1]}")
    yield handler
    server.shutdown()


def test_telegram_is_asked_about_the_bot_and_its_chat(telegram):
    assert ops_alerts.check_alerts_bot(BOT_TOKEN, "-100555") == {"ok": True, "bot": "@kazma_alerts_bot", "chat_ok": True}
    assert ops_alerts.check_alerts_bot(BOT_TOKEN, "-100999")["chat_ok"] is False
    assert ops_alerts.check_alerts_bot(MAIN_TOKEN, "-100555") == {"ok": False, "code": "bot_refused"}


@pytest.fixture
def client(tmp_path, telegram):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    cs = ConfigStore(db_path=str(tmp_path / "alerts.db"))
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(MagicMock(), cs, Jinja2Templates(directory=str(tmp_path / "templates"))))
    test_client = TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})
    test_client.store = cs
    return test_client


def test_choosing_the_alerts_bot_saves_it_and_never_answers_the_token(client):
    res = client.put("/api/settings/alerts", json={"bot": "alerts_bot", "bot_token": BOT_TOKEN, "bot_chat": "-100555"})
    assert res.status_code == 200, res.text
    assert sorted(res.json()["saved"]) == ["bot", "bot_chat", "bot_token"]
    assert res.json()["bot"] == {"ok": True, "bot": "@kazma_alerts_bot", "chat_ok": True}
    store = client.store
    assert ops_alerts.alerts_bot(store) == ops_alerts.AlertsBot(BOT_TOKEN, "-100555")
    got = client.get("/api/settings/alerts")
    assert BOT_TOKEN not in got.text
    data = got.json()
    assert data["bot"] == {"choice": "alerts_bot", "token_saved": True, "chat": "-100555", "active": True}
    assert data["guard"]["via_alerts_bot"] is True and data["routes"]["ready"]["telegram"] is True
    # An empty token keeps the saved one; a chat the bot cannot see is saved and said.
    res = client.put("/api/settings/alerts", json={"bot_token": "", "bot_chat": "-100999"})
    assert res.status_code == 200 and res.json()["bot"]["chat_ok"] is False
    assert ops_alerts.alerts_bot(store) == ops_alerts.AlertsBot(BOT_TOKEN, "-100999")


def test_a_token_telegram_does_not_know_is_refused_and_nothing_is_written(client):
    store = client.store
    routes_before = store.get("notifications.ops.channels")
    res = client.put("/api/settings/alerts", json={
        "routes": ["discord"], "bot": "alerts_bot", "bot_token": MAIN_TOKEN, "bot_chat": "-100555"})
    assert res.status_code == 400 and res.json()["code"] == "bot_refused"
    assert store.get(ops_alerts.ALERTS_BOT_KEY) is None
    assert store.get("notifications.ops.channels") == routes_before, "the save's other fields were written"


def test_the_alerts_bot_needs_its_token_and_its_chat(client):
    res = client.put("/api/settings/alerts", json={"bot": "alerts_bot", "bot_chat": "-100555"})
    assert res.status_code == 400 and "token and its chat" in res.json()["error"]
    res = client.put("/api/settings/alerts", json={"bot_token": "not-a-token"})
    assert res.status_code == 400 and "bot_token" in res.json()["error"]
    # Back to Kazma's chat bot needs nothing.
    assert client.put("/api/settings/alerts", json={"bot": "chat_bot"}).status_code == 200


def test_the_token_belongs_to_the_install_not_to_the_request():
    """Saved in an admin's request, read by alerts with no tenant bound."""
    from kazma_core.security.vault import is_install_scoped_secret

    assert is_install_scoped_secret("cfg:" + ops_alerts.ALERTS_BOT_TOKEN_KEY)
    from kazma_core.config_store import is_sensitive_config_key

    assert is_sensitive_config_key(ops_alerts.ALERTS_BOT_TOKEN_KEY)
    assert not is_sensitive_config_key(ops_alerts.ALERTS_BOT_CHAT_KEY)
