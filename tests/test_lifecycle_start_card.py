"""One card per boot, with each chat app's connection (2026-09-29).

The owner: "reduce the start/stop restart messages", and make the restart
card say which adapters are up ("Telegram ✅ / Discord ✅ / Slack ❌"). A
reload sent three messages -- "shutting down gracefully", "starting up" and
"restarted" -- and the last listed the adapters by name whether or not any
had connected. Now "starting" and "shutting_down" are recorded but not
announced by default, and the start card waits for the adapters and marks
each one with what its connection said.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from pathlib import Path
from typing import Any

import pytest

from kazma_core import lifecycle_notifier as ln
from kazma_core.lifecycle_notifier import PreviousRun, _compose_start_card

REPO = Path(__file__).resolve().parents[1]
OLD_DEFAULT = ["starting", "started", "shutting_down", "startup_failed"]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every message the notifier hands to the (fake) Telegram bus adapter;
    a fresh process (no boot recorded yet); a fast poll."""
    import kazma_core.swarm.bus as bus_mod
    from kazma_core.observability import ops_alerts

    out: list[str] = []

    class _Telegram:
        name = "telegram"

        async def send(self, message: Any) -> None:
            out.append(message.content)

    class _Bus:
        adapter = bus_mod.FanOutBusAdapter([_Telegram()])

    monkeypatch.setattr(bus_mod, "get_message_bus", lambda: _Bus())
    monkeypatch.setattr(ops_alerts, "_ops_channels", lambda: ["telegram"])
    monkeypatch.setattr(ln, "_boot", None)
    monkeypatch.setattr(ln, "_POLL_S", 0.01)
    return out


def _new_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ln, "_boot", None)


def _connected(*names: str):
    return lambda: [{"name": n, "state": "connected", "detail": ""} for n in names]


def _reload(monkeypatch: pytest.MonkeyPatch, connections=None) -> None:
    """What one operator reload runs: the old process's shutdown hook, then
    the new process's startup (top, then the card at the end)."""
    asyncio.run(ln.notify_lifecycle("shutting_down"))
    _new_process(monkeypatch)
    asyncio.run(ln.notify_lifecycle("starting"))
    asyncio.run(ln.announce_started(
        connections or _connected("Telegram", "Discord", "Slack"),
        model="deepseek-flash", build="abc1234",
    ))


# ── one message per reload ───────────────────────────────────────────────


def test_a_reload_sends_one_card(sent, monkeypatch):
    _reload(monkeypatch)

    assert len(sent) == 1, sent
    lines = sent[0].split("\n")
    assert lines[0] == "🟢 [System] Kazma restarted"
    assert lines[1].startswith("Down for ") and lines[1].endswith(" s · build abc1234")
    assert lines[3:7] == ["Adapters", "✅ Telegram", "✅ Discord", "✅ Slack"]
    assert lines[-1] == "Model: deepseek-flash"


def test_the_old_default_sent_three_messages(sent, monkeypatch):
    """Negative control: the list every install shipped with until today."""
    from kazma_core.config_store import get_config_store

    get_config_store().set(ln.EVENTS_KEY, OLD_DEFAULT, category="notifications")
    _reload(monkeypatch)

    assert [m.split("\n", 1)[0] for m in sent] == [
        "🟡 [System] Kazma is shutting down",
        "🔵 [System] Kazma is starting",
        "🟢 [System] Kazma restarted",
    ]


def test_the_stop_is_recorded_when_it_is_not_announced(sent, monkeypatch):
    """With shutting_down off, the next card still knows the stop was clean
    -- it says "restarted", never "did not shut down cleanly"."""
    asyncio.run(ln.notify_lifecycle("shutting_down"))
    assert sent == []
    _new_process(monkeypatch)
    asyncio.run(ln.notify_lifecycle("starting"))
    assert sent == []
    assert ln._boot == PreviousRun(stopped_at=ln._boot.stopped_at, clean=True)
    assert ln._boot.stopped_at is not None


def test_a_run_that_did_not_stop_cleanly_is_said(sent, monkeypatch):
    asyncio.run(ln.notify_lifecycle("starting"))          # a boot ...
    _new_process(monkeypatch)                             # ... that crashed
    asyncio.run(ln.notify_lifecycle("starting"))
    asyncio.run(ln.announce_started(_connected("Telegram"), build="abc1234"))

    lines = sent[0].split("\n")
    assert lines[0] == "🟡 [System] Kazma started"
    assert lines[1] == "The last run did not shut down cleanly (a crash, a forced stop or a power cut)."
    assert lines[2] == "Build abc1234"


def test_the_boot_is_read_before_it_is_stamped(monkeypatch):
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    cs.set(ln._LAST_BOOT_KEY, 50.0, category="internal")
    cs.set(ln._LAST_SHUTDOWN_KEY, 100.0, category="internal")
    _new_process(monkeypatch)

    first = ln._record_boot()
    assert first == PreviousRun(stopped_at=100.0, clean=True)
    assert float(cs.get(ln._LAST_BOOT_KEY)) > 100.0, "this boot is stamped"
    assert ln._record_boot() is first, "once per process"

    _new_process(monkeypatch)                  # no shutdown since that boot
    assert ln._record_boot() == PreviousRun(stopped_at=None, clean=False)


# ── the adapters on the card ─────────────────────────────────────────────


def test_the_card_waits_for_the_adapters_to_connect(sent):
    polls = {"n": 0}

    def report():
        polls["n"] += 1
        state = "connected" if polls["n"] > 3 else "connecting"
        return [{"name": "Discord", "state": state, "detail": ""}]

    asyncio.run(ln.announce_started(report, wait_s=5))

    assert polls["n"] == 4
    assert "✅ Discord" in sent[0].split("\n")
    assert sent[0].startswith("🟢")


def test_an_adapter_that_failed_or_never_connected_is_marked(sent):
    def report():
        return [
            {"name": "Telegram", "state": "connected", "detail": ""},
            {"name": "Discord", "state": "connecting", "detail": "Could not reach Discord; retrying"},
            {"name": "Slack", "state": "down", "detail": "Slack refused the app-level token (invalid_auth)"},
        ]

    asyncio.run(ln.announce_started(report, wait_s=0.05))

    lines = sent[0].split("\n")
    assert lines[0].startswith("🟡 [System] Kazma "), "a chat app down is not green"
    assert "✅ Telegram" in lines
    assert "❌ Discord: no connection after 0 s (Could not reach Discord; retrying)" in lines
    assert "❌ Slack: Slack refused the app-level token (invalid_auth)" in lines


def test_no_card_when_kazma_begins_shutting_down_during_the_wait(sent):
    from kazma_core.shutdown import signal_shutdown

    def report():
        signal_shutdown()
        return [{"name": "Slack", "state": "connecting", "detail": ""}]

    assert asyncio.run(ln.announce_started(report, wait_s=5)) is False
    assert sent == []


def test_the_card_can_be_switched_off(sent):
    from kazma_core.config_store import get_config_store

    get_config_store().set(ln.EVENTS_KEY, [], category="notifications")
    assert asyncio.run(ln.announce_started(_connected("Telegram"))) is False
    asyncio.run(ln.notify_lifecycle("startup_failed", detail="Gateway: bad token"))
    assert sent == [], "an empty list is every message off"


# ── the card's words ─────────────────────────────────────────────────────


def _card(previous: PreviousRun, **kw: Any) -> list[str]:
    kw.setdefault("now", 10_000.0)
    kw.setdefault("restart_window_s", 60)
    kw.setdefault("connections", [])
    return _compose_start_card(previous, **kw)[1].split("\n")


def test_a_long_stop_is_a_start_with_its_downtime():
    lines = _card(PreviousRun(stopped_at=10_000.0 - 7_500, clean=True), build="abc1234")
    assert lines[:2] == ["🟢 [System] Kazma started", "Down for 2 h 5 min · build abc1234"]


def test_window_zero_turns_restart_detection_off():
    lines = _card(PreviousRun(stopped_at=9_990.0, clean=True), restart_window_s=0)
    assert lines[0] == "🟢 [System] Kazma started"
    assert not any(line.startswith("Down for") for line in lines)
    unclean = _card(PreviousRun(stopped_at=None, clean=False), restart_window_s=0)
    assert not any("did not shut down" in line for line in unclean)


def test_no_adapter_set_up_is_said():
    assert "Adapters: none set up" in _card(PreviousRun(None, None))


def test_the_bare_started_event_keeps_its_detail(sent):
    asyncio.run(ln.notify_lifecycle("started", detail="Adapters: telegram"))
    assert sent[0].split("\n")[-1] == "Adapters: telegram"


@pytest.mark.parametrize("seconds, text", [
    (34.84, "34.8 s"), (312, "5 min 12 s"), (300, "5 min"),
    (7_500, "2 h 5 min"), (7_200, "2 h"), (273_600, "3 d 4 h"),
])
def test_durations_read_like_a_person_wrote_them(seconds, text):
    assert ln._duration(seconds) == text


def test_the_events_setting():
    parse = ln.parse_lifecycle_events
    assert parse(["shutting_down", "started"]) == (["started", "shutting_down"], [])
    assert parse("started, starting") == (["started", "starting"], [])
    assert parse(["started", "restarted"]) == (["started"], ["restarted"])
    assert parse(5) == ([], ["5"])


@pytest.mark.parametrize("stored, events", [
    (None, ["started", "startup_failed"]),
    ([], []),
    (OLD_DEFAULT, ["started", "startup_failed", "starting", "shutting_down"]),
    (["nonsense"], ["started", "startup_failed"]),
])
def test_the_config_reader(stored, events):
    from kazma_core.config_store import get_config_store

    if stored is not None:
        get_config_store().set(ln.EVENTS_KEY, stored, category="notifications")
    assert ln.get_lifecycle_config()["events"] == events


# ── each adapter says whether it is connected ────────────────────────────


def test_connection_state_is_what_the_connection_said():
    from kazma_gateway.gateway import BaseAdapter, GatewayManager
    from kazma_gateway.receive_log import ReceiveLog

    class _Chat(BaseAdapter):
        name = "slack"

        def __init__(self) -> None:
            super().__init__()
            self._receive = ReceiveLog()

        async def listen(self, queue, shutdown_event) -> None:
            await shutdown_event.wait()

        async def send(self, outbound) -> bool:
            return True

    async def run() -> list[Any]:
        seen = []
        gateway = GatewayManager()
        chat = _Chat()
        gateway.add_adapter(chat)
        seen.append(chat.connection_state())
        await gateway.start()
        seen.append(gateway.connection_report()[0])
        seen.append((await gateway.get_status())["adapters"][0]["status"])
        chat._receive.connected_now(new_session=True)
        seen.append(chat.connection_state())
        chat._receive.disconnected("Slack refused the token")
        await gateway.stop()
        seen.append(chat.connection_state())
        seen.append((await gateway.get_status())["adapters"][0]["status"])
        return seen

    before, started, monitor, connected, stopped, monitor_after = asyncio.run(run())
    assert before == ("down", "not started")
    assert started == {"name": "Slack", "state": "connecting", "detail": ""}
    assert monitor == "connecting", "running is not connected (it used to say so)"
    assert connected == ("connected", "")
    assert stopped == ("down", "Slack refused the token")
    assert monitor_after == "offline"


# ── wiring ───────────────────────────────────────────────────────────────


def test_the_app_sends_the_card_in_the_background_with_the_adapters():
    from kazma_ui.app import KazmaAppBuilder

    startup = inspect.getsource(KazmaAppBuilder._on_startup)
    shutdown = inspect.getsource(KazmaAppBuilder._on_shutdown)
    assert 'notify_lifecycle("starting")' in startup, "the boot is recorded"
    assert 'notify_lifecycle("shutting_down")' in shutdown, "the stop is recorded"
    card = startup[startup.index("spawn_background(\n                announce_started("):]
    assert "_gateway.connection_report" in card[:400]
    assert "get_build_info()" in card[:400]
    assert 'notify_lifecycle(\n                "started"' not in startup, "no second start message"


def test_settings_offers_every_message_and_saves_the_list():
    html = (REPO / "kazma-ui/kazma_ui/templates/settings.html").read_text(encoding="utf-8")
    core = (REPO / "kazma-ui/kazma_ui/static/js/settings_core.js").read_text(encoding="utf-8")
    hub = (REPO / "kazma-ui/kazma_ui/static/js/settings_hub.js").read_text(encoding="utf-8")
    assert 'x-for="ev in lifecycleEventNames"' in html
    assert "toggleRoutingList(adapterRouting.lifecycleEvents, ev" in html
    assert "lifecycleEventNames: ['started', 'startup_failed', 'starting', 'shutting_down']" in core
    assert list(ln.EVENT_NAMES) == ["started", "startup_failed", "starting", "shutting_down"]
    assert "single('notifications.lifecycle.events', curr.lifecycleEvents, 'notifications')" in hub


@pytest.fixture
def client(tmp_path):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    cs = ConfigStore(db_path=str(tmp_path / "api.db"))
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), cs, Jinja2Templates(directory=str(tmp_path / "templates"))))
    test_client = TestClient(app)
    test_client.kazma_config_store = cs
    return test_client


def test_the_api_saves_the_messages_in_order(client):
    ok = client.put("/api/settings/single", json={
        "key": ln.EVENTS_KEY, "value": ["shutting_down", "started"], "category": "general"})
    assert ok.status_code == 200
    assert client.kazma_config_store.get(ln.EVENTS_KEY) == ["started", "shutting_down"]


@pytest.mark.parametrize("bad", [["started", "restarted"], None, 3])
def test_the_api_refuses_a_message_kazma_does_not_send(client, bad):
    client.put("/api/settings/single", json={"key": ln.EVENTS_KEY, "value": ["started"]})

    resp = client.put("/api/settings/single", json={"key": ln.EVENTS_KEY, "value": bad})

    assert resp.status_code == 400
    assert "started, startup_failed, starting, shutting_down" in resp.json()["detail"]
    assert client.kazma_config_store.get(ln.EVENTS_KEY) == ["started"], "a refused save changed nothing"


def test_the_log_line_prints_on_a_windows_console(sent, caplog):
    """The card's emoji stays out of the log: cp1252 cannot encode it, and
    logging then prints a traceback instead of the line (seen on a Windows
    console in the browser check, 2026-09-29)."""
    with caplog.at_level(logging.INFO, logger="kazma_core.lifecycle_notifier"):
        asyncio.run(ln.announce_started(_connected("Telegram"), build="abc1234"))
    lines = [r.getMessage() for r in caplog.records if "Start card" in r.getMessage()]
    assert lines == [
        "[LifecycleNotifier] Start card (Kazma started, success) sent to telegram; "
        "adapters: 1, not connected: none"
    ]
    lines[0].encode("cp1252")


def test_the_card_is_sent_within_the_wait_not_after_it(sent):
    """A connected set of adapters does not sit out the whole wait."""
    began = time.monotonic()
    asyncio.run(ln.announce_started(_connected("Telegram"), wait_s=30))
    assert time.monotonic() - began < 5
    assert len(sent) == 1
