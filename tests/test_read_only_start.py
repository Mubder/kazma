"""Start read-only from the settings' local copy (resilience plan A4).

With the database away at boot Kazma waited and served nothing, and the chat
apps were silent through the outage. A copy of the settings is kept on the
machine; a minute into a boot with the database away the boot stops waiting
and the read-only server starts: health says not ready, database away (the
guard holds a restart); pages say how long; writes are refused with a stable
code; each chat is told once and its messages are kept; when the database
answers Kazma restarts normally and the kept messages are answered in order.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from kazma_core import config_store
from kazma_core.config_availability import ConfigStoreUnavailableError, wait_for_settings_store
from kazma_core.settings_snapshot import SettingsReadOnly, load_snapshot, write_snapshot
from kazma_ui import read_only_mode as rom


@pytest.fixture
def settings(tmp_path):
    store = config_store.ConfigStore(str(tmp_path / "settings.db"))
    store.set("connectors.telegram.enabled", True, category="connectors")
    store.set("connectors.telegram.allowed_users", "123", category="connectors")
    store.set("agent.language", "ar", category="agent")
    yield store
    store.close()


def test_the_copy_holds_the_settings_and_refuses_every_write(settings, tmp_path):
    path = tmp_path / "settings_snapshot.yaml"
    assert write_snapshot(settings, path) > 0
    copy = load_snapshot(path)
    assert copy.get("connectors.telegram.allowed_users") == "123"
    assert copy.get("connectors.telegram")["enabled"] is True, "child keys merged, as the store does"
    assert copy.get("missing.key", "d") == "d"
    with pytest.raises(SettingsReadOnly):
        copy.set("agent.language", "en")
    assert load_snapshot(tmp_path / "none.yaml") is None


def test_a_minute_into_an_outage_with_a_copy_the_boot_stops_waiting(monkeypatch):
    import kazma_core.settings_snapshot as ss

    monkeypatch.setattr(ss, "has_snapshot", lambda path=None: True)
    clock = SimpleNamespace(t=0.0)

    def opener():
        raise ConfigStoreUnavailableError(opening=True, reason="connection refused")

    with pytest.raises(rom.ReadOnlyStart):
        wait_for_settings_store(opener, budget_s=float("inf"), retry_s=10.0,
                                sleep=lambda s: setattr(clock, "t", clock.t + s), clock=lambda: clock.t,
                                on_wait=lambda waited: rom.read_only_after(waited, since=1.0))
    assert clock.t >= rom.READ_ONLY_AFTER_S - 10.0


def test_negative_control_without_a_copy_the_boot_keeps_waiting(monkeypatch):
    import kazma_core.settings_snapshot as ss

    monkeypatch.setattr(ss, "has_snapshot", lambda path=None: False)
    clock = SimpleNamespace(t=0.0)

    def opener():
        raise ConfigStoreUnavailableError(opening=True, reason="connection refused")

    with pytest.raises(ConfigStoreUnavailableError):
        wait_for_settings_store(opener, budget_s=300.0, retry_s=10.0,
                                sleep=lambda s: setattr(clock, "t", clock.t + s), clock=lambda: clock.t,
                                on_wait=lambda waited: rom.read_only_after(waited, since=1.0))


def test_the_read_only_server_answers_health_pages_and_refuses_writes(settings, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import kazma_core.settings_snapshot as ss

    monkeypatch.setattr(ss, "_snapshot_path", lambda: tmp_path / "settings_snapshot.yaml")
    write_snapshot(settings, tmp_path / "settings_snapshot.yaml")
    monkeypatch.setattr(rom, "_start_gateway", _no_gateway)
    app = rom.create_read_only_app(rom.ReadOnlyStart(time.time() - 300), probe=lambda: False)
    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        db = ready.json()["database_availability"]
        assert db["state"] == "away" and db["confirmed"] and db["probe_age_s"] <= db["probe_stale_after_s"]
        assert client.get("/health/live").status_code == 200
        page = client.get("/chat")
        assert page.status_code == 503 and 'dir="rtl"' in page.text and "منذ الساعة" in page.text,             "no choice: the install's language (agent.language in the copy)"
        client.cookies.set("kazma-lang", "en")
        page = client.get("/chat")
        assert 'dir="ltr"' in page.text and "away since" in page.text, "the reader's choice"
        client.cookies.clear()
        refused = client.post("/api/chat/stream", json={})
        assert refused.status_code == 503 and refused.json()["code"] == "database_away_read_only"
        assert isinstance(config_store.get_config_store(), ss.SnapshotSettings), "settings read from the copy"
    config_store.reset_config_store()


async def _no_gateway(snapshot, state):
    return None


def test_each_chat_is_told_once_and_its_messages_are_kept_then_answered(tmp_path, monkeypatch):
    monkeypatch.setattr(rom, "_inbox_path", lambda: str(tmp_path / "chat_inbox.db"))
    msg = SimpleNamespace(platform="telegram", sender_id="telegram:42", text="مرحبا",
                          context_metadata={"chat_id": 42}, timestamp=100.0)
    assert rom.keep(msg, 1.0) is True
    assert rom.keep(SimpleNamespace(**{**msg.__dict__, "text": "hello again", "timestamp": 101.0}), 1.0) is False
    assert rom.keep(SimpleNamespace(**{**msg.__dict__, "sender_id": "telegram:7"}), 1.0) is True
    assert "رسالتك محفوظة" in rom._notice("مرحبا") and "kept" in rom._notice("hello")

    handed = []

    class _Queue:
        async def put(self, incoming):
            handed.append((incoming.sender_id, incoming.text, incoming.context_metadata))

    assert asyncio.run(rom.drain_inbox(SimpleNamespace(queue=_Queue()))) == 3
    assert [h[1] for h in handed] == ["مرحبا", "hello again", "مرحبا"], "in the order received"
    assert handed[0][2] == {"chat_id": 42}
    assert asyncio.run(rom.drain_inbox(SimpleNamespace(queue=_Queue()))) == 0, "each handed once"


def test_when_the_database_answers_kazma_restarts_normally(settings, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import kazma_core.settings_snapshot as ss

    monkeypatch.setattr(ss, "_snapshot_path", lambda: tmp_path / "settings_snapshot.yaml")
    write_snapshot(settings, tmp_path / "settings_snapshot.yaml")
    monkeypatch.setattr(rom, "_start_gateway", _no_gateway)
    monkeypatch.setattr(rom, "PROBE_EVERY_S", 0.05)
    restarted = []
    app = rom.create_read_only_app(rom.ReadOnlyStart(time.time()), probe=lambda: True,
                                   on_back=lambda: restarted.append(True))
    with TestClient(app):
        deadline = time.time() + 5
        while not restarted and time.time() < deadline:
            time.sleep(0.05)
    config_store.reset_config_store()
    assert restarted == [True]
