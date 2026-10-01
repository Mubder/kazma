"""A chat app's saved settings reach the running adapters, whoever saved them.

Live 2026-10-01: the owner revoked the old Slack app-level tokens and saved a
new one in Settings. The running adapter kept the revoked token and retried
with ``invalid_auth``; the Test, run straight after the save, said "Kazma's
Slack connection is down right now" for a token that worked, and only a
separate Refresh applied it. Refresh rebuilt the adapters with a second copy
of the boot code that had drifted: it started a platform switched off,
dropped Telegram's webhook secret and the allow-all posture, and sat out a
5 s stop grace per adapter. The Settings switch itself was read by nothing.

Now the settings store announces every write (``ConfigStore._announces``),
``kazma_gateway.chat_adapters.ChatAdapters`` applies what concerns a chat app
to the running gateway, and boot, Refresh and that path build through one
builder. Each guard below has its negative control.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kazma_core.config_store import ConfigStore, _InMemoryStore
from kazma_gateway.chat_adapters import ChatAdapters, _PlatformSettings, _watched_key
from kazma_gateway.gateway import BaseAdapter, GatewayManager

REPO_ROOT = Path(__file__).resolve().parent.parent

_TOKEN_ENV = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "KAZMA_GATEWAY_STRICT_ALLOWLIST",
)


@pytest.fixture
def store(tmp_path, monkeypatch) -> ConfigStore:
    for name in _TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    return ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"))


async def _until(check, timeout: float = 5.0) -> Any:
    """Poll *check* until it answers something truthy (a rebuild runs half a
    second after the last write, in the background)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        value = check()
        if value:
            return value
        await asyncio.sleep(0.05)
    raise AssertionError("the running gateway never caught up with the saved settings")


class _FakeAdapter(BaseAdapter):
    """Stands in for a platform adapter: runs until halted, reaches no one."""

    def __init__(self, settings: _PlatformSettings) -> None:
        super().__init__()
        self.name = settings.platform
        self.settings = settings
        self.allowed_users: list[str] = []
        self._started_at: float | None = None

    async def listen(self, queue: asyncio.Queue, shutdown_event: asyncio.Event) -> None:
        self._started_at = asyncio.get_running_loop().time()
        await shutdown_event.wait()

    async def send(self, outbound: Any) -> bool:
        return True

    def set_allowed_users(self, ids: list[str]) -> None:
        self.allowed_users = list(ids)

    def connection_state(self) -> tuple[str, str]:
        if self._task is None or self._task.done():
            return "down", "not started"
        if self.settings.app_token == "xapp-demo-old":
            return "connecting", "Slack refused the Socket Mode connection (invalid_auth)"
        started = self._started_at
        if started is None or asyncio.get_running_loop().time() - started < 0.3:
            return "connecting", ""
        return "connected", ""

    def diagnostics(self) -> dict[str, Any]:
        state, why = self.connection_state()
        return {"built_with": self.settings.app_token, "state": state, "problem": why}


def _fake_builder(built: list[_FakeAdapter]):
    def build(settings: _PlatformSettings, store: Any, *, voice: Any = None) -> _FakeAdapter | None:
        if not (settings.enabled and settings.token):
            return None
        adapter = _FakeAdapter(settings)
        built.append(adapter)
        return adapter

    return build


# ── The store announces every write ─────────────────────────────────────

_MARKERS = {"_ONE_KEY", "_ONE_KEY_IF_WRITTEN", "_BATCH_KEYS", "_ANY_KEY_IF_WRITTEN"}

#: Writers that need no notice of their own, with the reason.
_SILENT_WRITERS = {
    "ConfigStore._write_db_value": "the lazy vault move re-stores the value get() just read, encrypted",
    "_InMemoryStore.transaction": (
        "a no-op context that hands back the store itself: the writes in it are "
        "its own set()/batch_set() calls, which announce"
    ),
}


def _calls(node: ast.AST, name: str) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            func = sub.func
            called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if called == name:
                return True
    return False


def _unannounced_writers(source: str, classes: tuple[str, ...] = ("ConfigStore", "_InMemoryStore")) -> list[str]:
    """Methods of the stores that write (they pass the diagnostic write guard,
    ``refuse_write``, or wrap a mutator of the protocol) but neither carry a
    change-notice marker nor announce in their body."""
    tree = ast.parse(source)
    protocol = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ConfigStoreProtocol"
    )
    read_only = {"get", "get_category", "get_all", "export_yaml", "close", "add_change_listener"}
    protocol_writers = {
        n.name for n in protocol.body if isinstance(n, ast.FunctionDef) and n.name not in read_only
    }
    missing: list[str] = []
    for cls in tree.body:
        if not (isinstance(cls, ast.ClassDef) and cls.name in classes):
            continue
        for method in cls.body:
            if not isinstance(method, ast.FunctionDef):
                continue
            writes = method.name in protocol_writers or _calls(method, "refuse_write")
            if not writes or f"{cls.name}.{method.name}" in _SILENT_WRITERS:
                continue
            marked = any(
                isinstance(d, ast.Name) and d.id in _MARKERS for d in method.decorator_list
            )
            if not (marked or _calls(method, "_announce")):
                missing.append(f"{cls.name}.{method.name}")
    return missing


def test_every_store_write_announces_itself() -> None:
    source = (REPO_ROOT / "kazma-core/kazma_core/config_store.py").read_text(encoding="utf-8")
    assert _unannounced_writers(source) == []


def test_the_gate_sees_a_write_that_does_not_announce() -> None:
    source = (REPO_ROOT / "kazma-core/kazma_core/config_store.py").read_text(encoding="utf-8")
    quiet = source.replace("    @_ONE_KEY_IF_WRITTEN\n    def delete(", "    def delete(", 1)
    assert quiet != source
    assert _unannounced_writers(quiet) == ["_InMemoryStore.delete"]


def test_each_write_tells_the_listeners_what_it_changed(store) -> None:
    heard: list[frozenset[str] | None] = []
    stop = store.add_change_listener(heard.append)

    store.set("a.one", 1)
    assert heard[-1] == {"a.one"}
    store.batch_set([("a.two", 2, "general"), ("a.three", 3, "general")])
    assert heard[-1] == {"a.two", "a.three"}
    store.atomic_update("a.one", lambda value: (value or 0) + 1)
    assert heard[-1] == {"a.one"}
    count = len(heard)
    assert store.set_if_absent("a.one", 5) is False
    assert len(heard) == count, "a write that wrote nothing announced"
    assert store.set_if_absent("a.four", 4) is True
    assert heard[-1] == {"a.four"}
    assert store.delete("a.four") is True
    assert heard[-1] == {"a.four"}
    count = len(heard)
    assert store.delete("a.missing") is False
    assert len(heard) == count
    with store.transaction() as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", ('"x"', "a.one"))
    assert heard[-1] is None, "raw SQL names no keys: any may have changed"
    assert store.reset_all() > 0
    assert heard[-1] is None

    stop()
    count = len(heard)
    store.set("a.five", 5)
    assert len(heard) == count


def test_the_in_memory_store_announces_too() -> None:
    fallback = _InMemoryStore()
    heard: list[frozenset[str] | None] = []
    fallback.add_change_listener(heard.append)
    fallback.set("a.one", 1)
    fallback.batch_set([("a.two", 2, "general")])
    assert heard == [{"a.one"}, {"a.two"}]


def test_a_failing_listener_never_fails_the_write(store, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="kazma_core.config_store")

    def broken(_keys: frozenset[str] | None) -> None:
        raise RuntimeError("listener bug")

    store.add_change_listener(broken)
    store.set("a.one", 1)
    assert store.get("a.one") == 1
    assert any("Change listener" in r.getMessage() for r in caplog.records)


# ── One builder ─────────────────────────────────────────────────────────

_BUILT_CLASSES = {
    "TelegramAdapter", "DiscordAdapter", "SlackAdapter",
    "TelegramBusAdapter", "DiscordBusAdapter", "SlackBusAdapter",
}
_BUILDER = "kazma-gateway/kazma_gateway/chat_adapters.py"


def _constructions(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _BUILT_CLASSES:
                lines.append(node.lineno)
    return lines


def _product_files() -> list[str]:
    # git, not a folder walk: a spawned task's worktree nests inside the checkout.
    files = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "kazma-*/*.py", "kazma-*/**/*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [f for f in files if "_tests" not in f and "/tests/" not in f and (REPO_ROOT / f).is_file()]


def test_chat_adapters_and_senders_are_built_in_one_place() -> None:
    elsewhere = {}
    for rel in _product_files():
        if rel == _BUILDER:
            continue
        lines = _constructions((REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace"))
        if lines:
            elsewhere[rel] = lines
    assert elsewhere == {}, (
        "build chat adapters and swarm senders through kazma_gateway.chat_adapters: "
        f"{elsewhere}"
    )
    assert _constructions((REPO_ROOT / _BUILDER).read_text(encoding="utf-8"))


def test_the_one_builder_gate_sees_a_second_copy() -> None:
    assert _constructions('adapter = TelegramAdapter(token="1:demo")\n') == [1]
    assert _constructions('bus.set_adapter(adapters.SlackBusAdapter(bot_token="b", channel_id="C"))\n') == [1]
    assert _constructions('"""TelegramAdapter(token="...") in a docstring"""\n') == []


def test_boot_and_refresh_build_the_same_adapters(store, monkeypatch) -> None:
    """The real builder: a switched-off platform stays off, Telegram keeps its
    webhook secret, and an adapter with no allowlist takes everyone (or no
    one, strict) -- at boot and on Refresh alike."""
    store.batch_set([
        ("connectors.telegram.token", "123:demo-telegram-token", "connectors"),
        ("connectors.telegram.webhook_secret", "demo-webhook-secret", "connectors"),
        ("connectors.discord.token", "demo-discord-token", "connectors"),
        ("connectors.discord.enabled", False, "connectors"),
        ("connectors.slack.token", "xoxb-demo-bot-token", "connectors"),
        ("connectors.slack.app_token", "xapp-demo-app-token", "connectors"),
    ])
    gateway = GatewayManager()
    adapters = ChatAdapters(gateway, store)
    adapters.build()
    boot = {a.name: a for a in gateway.adapters}
    assert set(boot) == {"telegram", "slack"}, "the Settings switch must keep Discord off"
    assert boot["telegram"]._webhook_secret == "demo-webhook-secret"
    assert all(a._allow_all for a in boot.values())

    async def refresh_twice() -> dict[str, BaseAdapter]:
        await adapters.apply(force=True)
        refreshed = {a.name: a for a in gateway.adapters}
        monkeypatch.setenv("KAZMA_GATEWAY_STRICT_ALLOWLIST", "1")
        await adapters.apply(force=True)
        return refreshed

    refreshed = asyncio.run(refresh_twice())
    assert set(refreshed) == {"telegram", "slack"}
    assert all(refreshed[name] is not boot[name] for name in refreshed)
    assert refreshed["telegram"]._webhook_secret == "demo-webhook-secret"
    assert all(a._allow_all for a in refreshed.values())
    assert not any(a._allow_all for a in gateway.adapters), "strict allowlists refuse everyone"


# ── Saved settings reach the running gateway ────────────────────────────


def test_a_saved_setting_reaches_the_running_adapter(store) -> None:
    store.batch_set([
        ("connectors.slack.token", "xoxb-demo-bot-token", "connectors"),
        ("connectors.slack.app_token", "xapp-demo-old", "connectors"),
        ("connectors.discord.token", "demo-discord-token", "connectors"),
    ])
    built: list[_FakeAdapter] = []

    async def scenario() -> None:
        gateway = GatewayManager()
        adapters = ChatAdapters(gateway, store, build_adapter=_fake_builder(built))
        adapters.build()
        await gateway.start()
        adapters.start_watching()
        try:
            old = gateway.adapter_named("slack")
            await asyncio.to_thread(store.set, "connectors.slack.app_token", "xapp-demo-new", "connectors")
            new = await _until(lambda: gateway.adapter_named("slack") is not old and gateway.adapter_named("slack"))
            assert new.settings.app_token == "xapp-demo-new"
            assert old._task is not None and old._task.done(), "the old connection must be closed"

            # An allowlist is applied to the running adapter in place.
            await asyncio.to_thread(store.set, "connectors.slack.allowed_users", "U1,U2", "connectors")
            await _until(lambda: new.allowed_users == ["U1", "U2"])
            assert gateway.adapter_named("slack") is new

            # The Settings switch: off removes the adapter, on brings it back.
            await asyncio.to_thread(store.set, "connectors.discord.enabled", False, "connectors")
            await _until(lambda: gateway.adapter_named("discord") is None)
            await asyncio.to_thread(store.set, "connectors.discord.enabled", True, "connectors")
            await _until(lambda: gateway.adapter_named("discord") is not None)

            # Anything else rebuilds nothing.
            count = len(built)
            await asyncio.to_thread(store.set, "agent.language", "en", "agent")
            assert await adapters.apply() == []
            assert len(built) == count
        finally:
            adapters.stop_watching()
            await gateway.stop()

    asyncio.run(scenario())


def test_without_the_listener_a_save_reaches_nothing(store) -> None:
    """Negative control: the same save with nobody listening leaves the
    revoked token running -- the 2026-10-01 state."""
    store.batch_set([
        ("connectors.slack.token", "xoxb-demo-bot-token", "connectors"),
        ("connectors.slack.app_token", "xapp-demo-old", "connectors"),
    ])
    built: list[_FakeAdapter] = []

    async def scenario() -> None:
        gateway = GatewayManager()
        adapters = ChatAdapters(gateway, store, build_adapter=_fake_builder(built))
        adapters.build()
        await gateway.start()
        try:
            old = gateway.adapter_named("slack")
            await asyncio.to_thread(store.set, "connectors.slack.app_token", "xapp-demo-new", "connectors")
            # Three settle periods: a listener would have rebuilt it by now.
            with pytest.raises(AssertionError):
                await _until(lambda: gateway.adapter_named("slack") is not old, timeout=1.5)
            # The change was there to apply.
            assert await adapters.apply() == ["slack"]
            assert gateway.adapter_named("slack") is not old
        finally:
            await gateway.stop()

    asyncio.run(scenario())


def test_only_chat_settings_wake_the_gateway() -> None:
    assert _watched_key("connectors.slack.app_token")
    assert _watched_key("connectors.telegram.swarm_chat_id")
    assert _watched_key("connectors.discord.guild_id")
    assert not _watched_key("connectors.slack.workspace")
    assert not _watched_key("connectors.email.token")
    assert not _watched_key("agent.language")
    assert not _watched_key("tenant.x.connectors.slack.token")


def test_the_test_reports_the_connection_made_with_the_saved_token(store, monkeypatch) -> None:
    """The incident: the card saves the new token, then tests. The Test must
    see the adapter built from that token, after its first attempt."""
    from kazma_core import service_container
    from kazma_gateway.adapters import slack_diagnose
    from kazma_ui import providers

    store.batch_set([
        ("connectors.slack.token", "xoxb-demo-bot-token", "connectors"),
        ("connectors.slack.app_token", "xapp-demo-old", "connectors"),
    ])
    container = service_container.ServiceContainer()
    monkeypatch.setattr(service_container, "get_container", lambda: container)
    seen: dict[str, Any] = {}

    async def fake_diagnose(token: str, *, live: Any = None, **_settings: Any) -> dict[str, Any]:
        seen["live"] = live
        return {"success": True}

    monkeypatch.setattr(slack_diagnose, "diagnose", fake_diagnose)

    async def scenario() -> None:
        gateway = GatewayManager()
        adapters = ChatAdapters(gateway, store, build_adapter=_fake_builder([]))
        adapters.build()
        await gateway.start()
        container.register(GatewayManager, gateway)
        container.register(ChatAdapters, adapters)
        try:
            await asyncio.to_thread(store.set, "connectors.slack.app_token", "xapp-demo-new", "connectors")
            # What the Test read before this fix: the revoked token's failure.
            stale = providers._live_adapter_diagnostics("slack")
            assert stale["built_with"] == "xapp-demo-old" and "invalid_auth" in stale["problem"]

            router = providers.create_providers_router(store)
            endpoint = next(r.endpoint for r in router.routes if r.path == "/api/connectors/{name}/test")
            await endpoint("slack")
            assert seen["live"]["built_with"] == "xapp-demo-new"
            assert seen["live"]["state"] == "connected", "the Test must wait for the first attempt"
        finally:
            await gateway.stop()

    asyncio.run(scenario())


# ── Swarm senders ───────────────────────────────────────────────────────


class _FakeSender:
    def __init__(self, settings: _PlatformSettings) -> None:
        self.settings = settings


class _FakeBus:
    def __init__(self) -> None:
        self.adapter: Any = None
        self.calls = 0

    def set_adapter(self, adapter: Any) -> None:
        self.adapter = adapter
        self.calls += 1


def test_a_changed_swarm_channel_rebuilds_only_its_sender(store) -> None:
    from kazma_core.swarm.bus import FanOutBusAdapter

    store.batch_set([
        ("connectors.telegram.token", "123:demo-telegram-token", "connectors"),
        ("connectors.telegram.swarm_chat_id", "1", "connectors"),
        ("connectors.discord.token", "demo-discord-token", "connectors"),
        ("connectors.discord.swarm_channel_id", "D1", "connectors"),
    ])
    bus = _FakeBus()

    def sender(settings: _PlatformSettings) -> _FakeSender | None:
        return _FakeSender(settings) if settings.enabled and settings.token else None

    adapters = ChatAdapters(
        GatewayManager(), store, swarm_bus=bus, build_adapter=lambda *a, **k: None, build_sender=sender,
    )
    adapters.build()
    first = dict(adapters._senders)
    assert isinstance(bus.adapter, FanOutBusAdapter)

    store.set("connectors.discord.swarm_channel_id", "D2", "connectors")
    asyncio.run(adapters.apply())
    assert adapters._senders["telegram"] is first["telegram"], "an unchanged sender keeps its approvals"
    assert adapters._senders["discord"] is not first["discord"]
    assert adapters._senders["discord"].settings.swarm_target == "D2"
    assert set(map(id, bus.adapter.adapters)) == set(map(id, adapters._senders.values()))

    calls = bus.calls
    asyncio.run(adapters.apply())
    assert bus.calls == calls, "nothing changed: the bus keeps its adapter"

    store.set("connectors.telegram.enabled", False, "connectors")
    store.set("connectors.discord.enabled", False, "connectors")
    asyncio.run(adapters.apply())
    assert type(bus.adapter).__name__ == "NullBusAdapter"


# ── The webhook and the fast stop ───────────────────────────────────────


def test_the_webhook_takes_the_adapter_running_now() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_gateway.adapters.telegram import TelegramAdapter, telegram_webhook_router

    first = TelegramAdapter(token="1:demo-first", webhook_secret="secret-first", allow_all=True)
    second = TelegramAdapter(token="1:demo-second", webhook_secret="secret-second", allow_all=True)
    current: dict[str, Any] = {"adapter": first}
    app = FastAPI()
    app.include_router(telegram_webhook_router(lambda: current["adapter"]), prefix="/hook")
    bound = FastAPI()
    bound.include_router(first.create_webhook_router(), prefix="/hook")
    update = {"update_id": 1}

    def post(client: TestClient, secret: str) -> int:
        return client.post("/hook", json=update, headers={"X-Telegram-Bot-Api-Secret-Token": secret}).status_code

    with TestClient(app) as client, TestClient(bound) as old_style:
        assert post(client, "secret-first") != 401
        current["adapter"] = second
        assert post(client, "secret-first") == 401
        assert post(client, "secret-second") != 401
        current["adapter"] = None
        assert post(client, "secret-second") == 503
        # Negative control: a router bound to one adapter keeps it.
        assert post(old_style, "secret-second") == 401


class _Waiting(BaseAdapter):
    name = "waiting"

    async def listen(self, queue: asyncio.Queue, shutdown_event: asyncio.Event) -> None:
        await shutdown_event.wait()

    async def send(self, outbound: Any) -> bool:
        return True


def test_halt_does_not_wait_out_the_stop_grace() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        adapter = _Waiting()
        await adapter.start(asyncio.Queue(), asyncio.Event())
        started = loop.time()
        await adapter.halt()
        assert loop.time() - started < 1.0
        assert adapter._task is not None and adapter._task.done()

        # Negative control: stop() waits for a shutdown event a replacement
        # never sets -- still waiting after half a second of its 5 s grace.
        other = _Waiting()
        await other.start(asyncio.Queue(), asyncio.Event())
        stopping = asyncio.create_task(other.stop())
        done, _pending = await asyncio.wait({stopping}, timeout=0.5)
        assert not done
        await other.halt()
        await stopping

    asyncio.run(scenario())


# ── The flood guard: sane limits, and every message it leaves accounted for ──


class _RecordingAdapter(BaseAdapter):
    """A platform adapter with a receive record, that sends nowhere."""

    def __init__(self, name: str) -> None:
        super().__init__()
        from kazma_gateway.receive_log import ReceiveLog

        self.name = name
        self._receive = ReceiveLog()
        self.sent: list[str] = []

    async def listen(self, queue: asyncio.Queue, shutdown_event: asyncio.Event) -> None:
        await shutdown_event.wait()

    async def send(self, outbound: Any) -> bool:
        self.sent.append(outbound.text)
        return True


def _through_the_guard(count: int, *, recorded: bool) -> tuple[_RecordingAdapter, list[str]]:
    """*count* messages from one Slack user, one a second apart in the guard's
    eyes, with a limit of one a minute -- live 2026-10-01's shape."""
    from kazma_gateway.gateway import IncomingMessage
    from kazma_gateway.rate_feedback import RateFeedbackManager

    handled: list[str] = []

    async def scenario() -> _RecordingAdapter:
        gateway = GatewayManager()
        adapter = _RecordingAdapter("slack")
        if not recorded:
            adapter.note_left_unanswered = lambda msg, reason: None  # type: ignore[method-assign]
        gateway.add_adapter(adapter)
        gateway.set_rate_feedback(RateFeedbackManager(limit={"slack": 1}, window_seconds=60))

        async def handler(msg: Any) -> None:
            handled.append(msg.text)

        gateway.on_message(handler)
        await gateway.start()
        try:
            for n in range(count):
                key = f"D1:{n}.000100"
                adapter._receive.note_passed_on(key)
                await gateway.queue.put(IncomingMessage(
                    platform="slack", sender_id="slack:U1", text=f"message {n}",
                    context_metadata={"receive_key": key, "user_id": "U1", "channel_id": "D1"},
                ))
            await _until(lambda: gateway.queue.empty() and len(adapter._receive.recent) == count)
        finally:
            await gateway.stop()
        return adapter

    return asyncio.run(scenario()), handled


def test_a_message_the_flood_guard_leaves_is_recorded(caplog) -> None:
    caplog.set_level(logging.INFO, logger="kazma_gateway.gateway")
    adapter, handled = _through_the_guard(4, recorded=True)
    assert handled == ["message 0"]
    recent = adapter._receive.recent
    assert [recent[f"D1:{n}.000100"] for n in range(4)] == [
        "passed_on", "rate_limited", "rate_limited", "rate_limited",
    ]
    assert adapter._receive.dropped["rate_limited"] == 3
    said = [r for r in caplog.records if "was not answered" in r.getMessage()]
    assert len(said) == 3
    assert [r.levelno for r in said] == [logging.WARNING, logging.INFO, logging.INFO]
    assert len(adapter.sent) == 1, "one Slow down per cooldown, not one per message"


def test_without_the_record_a_left_message_looks_answered(caplog) -> None:
    """Negative control: the 2026-10-01 state. The adapter's record still
    says the message was handed on, so the Test would report it answered."""
    adapter, handled = _through_the_guard(2, recorded=False)
    assert handled == ["message 0"]
    assert adapter._receive.recent["D1:1.000100"] == "passed_on"


#: Fewer messages a minute than a person types in a burst. The values the
#: limiter shipped with until 2026-10-01 (Discord 5, Slack 1) were the
#: platforms' own send limits, and held the owner to one Slack message a minute.
_LOWEST_SANE_LIMIT = 10


def _too_low(limits: dict[str, Any]) -> list[str]:
    return sorted(name for name, value in limits.items() if int(value) < _LOWEST_SANE_LIMIT)


def test_the_shipped_flood_guard_lets_a_person_type() -> None:
    import yaml

    shipped = yaml.safe_load((REPO_ROOT / "kazma.yaml").read_text(encoding="utf-8"))
    limits = shipped["gateway"]["rate_limits"]
    assert set(limits) >= {"telegram", "discord", "slack"}
    assert _too_low(limits) == []


def test_the_flood_guard_floor_sees_the_old_limits() -> None:
    assert _too_low({"telegram": 30, "discord": 5, "slack": 1}) == ["discord", "slack"]
