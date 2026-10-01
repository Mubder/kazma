"""Every way a setting's value is written applies one check
(kazma_core.settings_validation, 2026-10-01).

The checks lived in ``PUT /api/settings/single`` only. The batch save
(tests/test_settings.py), a restore (tests/test_settings_restore.py) and the
agent's ``config_save`` (tests/test_protected_config.py) now ask the same
function; this file covers the TUI's Settings panel and the client it talks
through, and the table itself.

The TUI sent its payload positionally to ``request_json``'s keyword-only
``payload``: every save raised TypeError inside the ``except Exception`` that
was meant for "no server", was written straight into the TUI process's
settings store, and was reported as "server unreachable". And a value the
server refused (400) took the same path -- written locally after the server
said no.
"""

from __future__ import annotations

import httpx
import pytest

from kazma_core.runtime import local_api
from kazma_core.settings_validation import SettingRejected, _rules, validate_setting


@pytest.mark.parametrize("key,value,expect", [
    ("cron.timezone", " Asia/Kuwait ", ("Asia/Kuwait", None)),
    ("cron.timezone", "", ("", None)),
    ("swarm.task_retention_days", "14", (14, "swarm")),
    ("checkpoints.retention_days", 0, (0, "system")),
    ("notifications.lifecycle.events", "started, startup_failed", (["started", "startup_failed"], "notifications")),
    ("tenant.acme.cron.timezone", "UTC", ("UTC", None)),
    ("agent.language", "anything", ("anything", None)),
])
def test_values_a_setting_can_hold(key, value, expect) -> None:
    assert validate_setting(key, value) == expect


@pytest.mark.parametrize("key,value", [
    ("cron.timezone", "Mars/Olympus"),
    ("cron.timezone", "../../etc/passwd"),
    ("swarm.task_retention_days", -1),
    ("swarm.task_retention_days", True),
    ("checkpoints.retention_days", "forever"),
    ("notifications.lifecycle.events", ["started", "a_message_kazma_never_sends"]),
    ("notifications.lifecycle.events", 7),
])
def test_values_a_setting_cannot_hold(key, value) -> None:
    with pytest.raises(SettingRejected):
        validate_setting(key, value)


def test_the_table_names_the_keys_the_owning_modules_define() -> None:
    from kazma_core.checkpoint_retention import RETENTION_KEY
    from kazma_core.lifecycle_notifier import EVENTS_KEY
    from kazma_core.swarm.task_store import TASK_RETENTION_KEY

    assert set(_rules()) == {"cron.timezone", RETENTION_KEY, EVENTS_KEY, TASK_RETENTION_KEY}


# ── the TUI's client: an answered refusal is not "no server" ───────────────


def _serve(monkeypatch, handler) -> None:
    monkeypatch.setattr(local_api, "candidate_api_bases", lambda: ["http://127.0.0.1:9"])
    real = httpx.Client

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)


def test_a_refusal_from_the_server_is_raised_as_one(monkeypatch) -> None:
    _serve(monkeypatch, lambda req: httpx.Response(400, json={"detail": "Invalid timezone 'Mars/Olympus'"}))
    with pytest.raises(local_api.LocalApiRefused) as caught:
        local_api.request_json("PUT", "/api/settings/single", payload={"key": "cron.timezone"})
    assert caught.value.status == 400 and "Mars/Olympus" in caught.value.detail


def test_no_server_is_still_a_plain_runtime_error(monkeypatch) -> None:
    def down(req):
        raise httpx.ConnectError("refused", request=req)

    _serve(monkeypatch, down)
    with pytest.raises(RuntimeError) as caught:
        local_api.request_json("GET", "/api/settings")
    assert not isinstance(caught.value, local_api.LocalApiRefused)


# ── the TUI's Settings panel ───────────────────────────────────────────────


@pytest.fixture
def panel():
    from kazma_tui.settings_panel import SettingsPanel

    return SettingsPanel()


def test_the_tui_saves_through_the_server_with_its_payload(monkeypatch, panel) -> None:
    sent = []

    def request_json(method, path, *, payload=None, timeout=8.0):
        sent.append((method, path, payload))
        return {"status": "ok"}

    monkeypatch.setattr(local_api, "request_json", request_json)
    assert panel._persist_setting("agent.language", "ar", "agent") == "server"
    assert sent == [("PUT", "/api/settings/single", {"key": "agent.language", "value": "ar", "category": "agent"})]


def test_the_tui_never_writes_what_the_server_refused(monkeypatch, panel) -> None:
    def request_json(method, path, *, payload=None, timeout=8.0):
        raise local_api.LocalApiRefused("Invalid timezone 'Mars/Olympus'", 400)

    writes = []
    monkeypatch.setattr(local_api, "request_json", request_json)
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Recorder(writes))
    with pytest.raises(local_api.LocalApiRefused):
        panel._persist_setting("cron.timezone", "Mars/Olympus", "cron")
    assert writes == []


def test_with_no_server_the_tui_writes_locally_through_the_same_check(monkeypatch, panel) -> None:
    def request_json(method, path, *, payload=None, timeout=8.0):
        raise RuntimeError("Kazma server is not running on this machine")

    writes = []
    monkeypatch.setattr(local_api, "request_json", request_json)
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Recorder(writes))
    assert panel._persist_setting("swarm.task_retention_days", "14", "anything") == "local"
    assert writes == [("swarm.task_retention_days", 14, "swarm")]
    with pytest.raises(SettingRejected):
        panel._persist_setting("cron.timezone", "Mars/Olympus", "cron")
    assert len(writes) == 1


class _Recorder:
    def __init__(self, writes: list) -> None:
        self.writes = writes

    def set(self, key, value, category="general") -> None:
        self.writes.append((key, value, category))
