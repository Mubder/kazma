"""The agent's vault tools ask every time and never touch Kazma's own secrets.

Audit 2026-11-10: the vault tools were danger tier, so a YOLO window, an
"Approve for task" or an "Allow tool" grant let them run with no card. Text the
agent had just read could then ``vault_list`` (read tier: every row name,
Kazma's ``cfg:...`` rows among them), ``vault_retrieve("cfg:...")`` (a
provider key or a bot token, in clear, into the model's context) and
``vault_store(name="cfg:...")`` (a setting Settings guards, the memory
mirror's address say, overwritten around ``safety.protected_config``).

Now ``vault_retrieve``, ``vault_store`` and ``vault_delete`` are always-ask
tools (``ALWAYS_HITL_TOOLS``), and every vault tool refuses Kazma's own rows
(``security.vault.is_kazma_owned_secret``) before it touches the vault and
leaves them out of the list. Each test has its negative control: the old list,
or the predicate switched off.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.safety import hitl
from kazma_core.safety.hitl import (
    ALWAYS_HITL_TOOLS,
    approval_need,
    reset_current_thread_id,
    set_current_thread_id,
)
from kazma_core.safety.hitl_grants import clear_grants, grant_tool, one_time_only
from kazma_core.safety.task_grants import clear_task_grant, grant_task
from kazma_core.safety.unasked import BY_TASK_GRANT, BY_YOLO
from kazma_core.safety.yolo import disable_yolo, enable_yolo
from kazma_core.security import vault as vault_mod
from kazma_skills.native.secret_vault import tools

TID = "thr-vault-tools"
HITL_ON = {"enabled": True}
VAULT_TOOLS = ("vault_retrieve", "vault_store", "vault_delete")
#: The list before 2026-11-10: the negative control.
OLD_LIST = frozenset({
    "x_post", "x_delete_post", "x_schedule_post", "x_cancel_scheduled_post",
    "request_path_access",
})
PROVIDER_KEY = "cfg:providers.list.deepseek.api_key"
MIRROR_URL = "cfg:memory.backends.state.url"
X_TOKEN = "cfg:connectors.x.access_token"  # a settings secret, not install-scoped
MAIL = "email.gmail.refresh_token"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.delenv("KAZMA_ALLOW_YOLO", raising=False)
    disable_yolo(TID)
    clear_grants(TID)
    clear_task_grant(TID)
    token = set_current_thread_id(TID)
    yield
    reset_current_thread_id(token)
    disable_yolo(TID)
    clear_grants(TID)
    clear_task_grant(TID)


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "v" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    v.store(PROVIDER_KEY, "provider-key-value", category="config")
    v.store(MIRROR_URL, "postgresql://kazma:pw@localhost/kazma", category="config")
    v.store(X_TOKEN, "x-token-value", category="config")
    v.store(MAIL, "mail-refresh-value", category="email")
    v.store("my_weather_key", "weather-value", category="general")
    yield v
    v.close()


def _predicate_off(monkeypatch):
    """The tools as they were: no name was Kazma's."""
    monkeypatch.setattr(vault_mod, "is_kazma_owned_secret", lambda name: False)


# ── always ask ─────────────────────────────────────────────────────────────


def test_the_vault_tools_are_always_ask_on_both_lists():
    from kazma_core.swarm.safety import _ALWAYS_HITL_FALLBACK

    assert set(VAULT_TOOLS) <= ALWAYS_HITL_TOOLS
    assert set(VAULT_TOOLS) <= _ALWAYS_HITL_FALLBACK
    assert "vault_list" not in ALWAYS_HITL_TOOLS  # names only, read tier


@pytest.mark.parametrize("tool", VAULT_TOOLS)
@pytest.mark.parametrize(("kind", "by"), [("yolo", BY_YOLO), ("task", BY_TASK_GRANT)])
def test_each_asks_under_a_standing_approval(tool, kind, by, monkeypatch):
    if kind == "yolo":
        enable_yolo(TID, actor="web:admin:test")
    else:
        grant_task(TID, actor="web:admin:test")
    assert approval_need(tool, HITL_ON) == (True, None)
    monkeypatch.setattr(hitl, "ALWAYS_HITL_TOOLS", OLD_LIST)  # control
    assert approval_need(tool, HITL_ON) == (False, by)


@pytest.mark.parametrize("tool", VAULT_TOOLS)
def test_each_takes_a_one_time_approval_only(tool, monkeypatch):
    assert one_time_only([{"name": tool}]) == [tool]
    refused = grant_tool(TID, tool, actor="web:admin:test")
    assert refused["active"] is False and refused.get("error") == "one_time_only"
    monkeypatch.setattr(hitl, "ALWAYS_HITL_TOOLS", OLD_LIST)  # control
    assert grant_tool(TID, tool, actor="web:admin:test")["active"] is True


def test_the_swarm_bus_asks_under_yolo(monkeypatch):
    from kazma_core.swarm.safety import SafetyMiddleware

    enable_yolo(TID, actor="web:admin:test")
    allowed = asyncio.run(SafetyMiddleware(allow_headless_danger=False).check("vault_retrieve"))
    assert allowed is False  # asked, and no bus adapter answers here
    monkeypatch.setattr(hitl, "ALWAYS_HITL_TOOLS", OLD_LIST)  # control
    assert asyncio.run(SafetyMiddleware(allow_headless_danger=False).check("vault_retrieve")) is True


def test_a_chat_app_card_says_it_asks_every_time():
    from kazma_gateway.agent_handler.hitl import ASKS_EVERY_TIME, _build_approval_prompt

    payload = {"tool": "vault_retrieve", "args": {"name": "my_weather_key"},
               "tools": [{"name": "vault_retrieve", "args": {}}], "interrupt_id": "gate-1"}
    out = _build_approval_prompt(payload, "th-1", platform="telegram")
    assert ASKS_EVERY_TIME in out["text"]
    buttons = [b["text"] for row in out["markup"]["inline_keyboard"] for b in row]
    assert not any("task" in b.lower() for b in buttons), buttons


# ── Kazma's own rows ───────────────────────────────────────────────────────


@pytest.mark.parametrize("name", [PROVIDER_KEY, MIRROR_URL, X_TOKEN, MAIL, "calendar.google.refresh",
                                  "  cfg:llm.api_key"])
def test_the_predicate_names_kazmas_own_rows(name):
    assert vault_mod.is_kazma_owned_secret(name)


@pytest.mark.parametrize("name", ["my_weather_key", "openai_key", "config_note", "emails_note"])
def test_the_predicate_leaves_the_users_rows(name):
    assert not vault_mod.is_kazma_owned_secret(name)


@pytest.mark.parametrize("name", [PROVIDER_KEY, X_TOKEN, MAIL])
def test_retrieve_refuses_kazmas_own_secret(vault, name, monkeypatch):
    with pytest.raises(RefusedBeforeEffect) as caught:
        asyncio.run(tools.vault_retrieve(name))
    assert "Settings" in str(caught.value)
    assert vault.retrieve(name) not in str(caught.value)
    _predicate_off(monkeypatch)  # control: the old tool handed the value out
    leaked = asyncio.run(tools.vault_retrieve(name))
    assert vault.retrieve(name) in leaked


def test_retrieve_still_reads_the_users_secret(vault):
    assert "weather-value" in asyncio.run(tools.vault_retrieve("my_weather_key"))


def test_store_cannot_overwrite_a_setting(vault, monkeypatch):
    evil = "postgresql://attacker:pw@evil.example/kazma"
    with pytest.raises(RefusedBeforeEffect):
        asyncio.run(tools.vault_store(MIRROR_URL, evil, "config"))
    assert vault.retrieve(MIRROR_URL) == "postgresql://kazma:pw@localhost/kazma"
    _predicate_off(monkeypatch)  # control: the old tool overwrote it
    asyncio.run(tools.vault_store(MIRROR_URL, evil, "config"))
    assert vault.retrieve(MIRROR_URL) == evil


def test_delete_cannot_remove_a_setting(vault, monkeypatch):
    with pytest.raises(RefusedBeforeEffect):
        asyncio.run(tools.vault_delete(PROVIDER_KEY))
    assert vault.retrieve(PROVIDER_KEY) == "provider-key-value"
    _predicate_off(monkeypatch)  # control
    asyncio.run(tools.vault_delete(PROVIDER_KEY))
    assert vault.retrieve(PROVIDER_KEY) is None


def test_store_and_delete_still_work_for_the_user(vault):
    assert "stored securely" in asyncio.run(tools.vault_store("my_note", "n-value"))
    assert vault.retrieve("my_note") == "n-value"
    assert "deleted" in asyncio.run(tools.vault_delete("my_note"))


def test_the_list_leaves_kazmas_rows_out(vault, monkeypatch):
    names = {row["name"] for row in json.loads(asyncio.run(tools.vault_list()))}
    assert names == {"my_weather_key"}
    _predicate_off(monkeypatch)  # control: every row name was listed
    names = {row["name"] for row in json.loads(asyncio.run(tools.vault_list()))}
    assert {PROVIDER_KEY, MIRROR_URL, X_TOKEN, MAIL} <= names


def test_a_refusal_comes_before_the_vault_is_even_asked(monkeypatch):
    """A refusal is ``RefusedBeforeEffect`` even with no vault configured."""
    monkeypatch.setattr(vault_mod, "get_vault", lambda: pytest.fail("vault opened"))
    for call in (tools.vault_retrieve(PROVIDER_KEY), tools.vault_delete(PROVIDER_KEY),
                 tools.vault_store(PROVIDER_KEY, "x")):
        with pytest.raises(RefusedBeforeEffect):
            asyncio.run(call)
