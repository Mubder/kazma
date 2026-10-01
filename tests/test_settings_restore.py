"""A Settings backup restores the settings, and keeps the owner's keys
(kazma_core.settings_restore; AUD-015, 2026-10-01).

Settings -> System -> "Download settings backup" saves every stored setting
with keys as ``vault://`` references; "Restore settings backup..." previews
the restore and writes only the plan the owner confirmed. The rules, each
tested here on a real settings store:

- the backup holds no credential, no running state, no plaintext secret;
- a key held now is never replaced; a missing one comes back only when its
  reference opens in this vault, and is listed to enter again when not;
- named lists merge by name: entries and fields added since stay;
- running state and credentials in an old backup are never written;
- a value the Settings page refuses is refused here too;
- a retired default keeps today's value; the learned Soul only fills a gap;
- the preview writes nothing, a stale plan writes nothing, nothing is deleted;
- undo puts back what the restore changed, and keeps the keys it restored.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from kazma_core import settings_restore as sr
from kazma_core.config_store import ConfigStore
from kazma_core.settings_manager import SettingsManager

#: The vault this install holds: what a reference opens to.
VAULT = {
    "cfg:providers.list.deepseek.api_key": "demo-deepseek-key",
    "cfg:providers.list.zai.api_key": "demo-zai-key",
    "cfg:connectors.slack.bot_token": "demo-slack-token",
    "email.account.work.refresh_token": "demo-refresh",
}


def opens(name: str) -> bool:
    return name in VAULT


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("KAZMA_VAULT_KEY", raising=False)
    (tmp_path / "kazma.yaml").write_text(
        "agent:\n  language: en\nnotifications:\n  lifecycle:\n    events: [started, startup_failed]\n",
        encoding="utf-8",
    )
    cs = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "kazma.yaml"))
    yield cs
    cs.close()


def _seed(cs: ConfigStore) -> None:
    cs.set("agent.language", "ar", category="agent")
    cs.set("memory.v2.summaries_min_turns", 4, category="memory")
    cs.set("connectors.slack.allowed_users", "U0BBRDBJ491", category="connectors")
    cs.set("providers.list", [
        {"name": "deepseek", "base_url": "https://api.deepseek.com",
         "api_key": "vault://cfg:providers.list.deepseek.api_key"},
    ], category="providers")
    cs.set("connectors.slack.bot_token", "vault://cfg:connectors.slack.bot_token", category="connectors")
    # Kazma's own state and credentials, which a backup must not carry.
    cs.set("web_session.0f3a", {"username": "owner", "role": "admin"}, category="auth")
    cs.set("account.password_hash", "demo-hash", category="account")
    cs.set("long_task.t1", {"budget": 40}, category="agent")
    cs.set("task_grant.t1", {"until": 1}, category="safety")
    cs.set("system.lifecycle.last_boot_epoch", 1.0, category="internal")


def _restore(cs, text, **kw):
    return sr.restore(text, cs, vault_has=opens, **kw)


def _rows(cs: ConfigStore) -> dict:
    return {k: v for cat in cs.get_all().values() for k, v in cat.items()}


# ── the backup ──────────────────────────────────────────────────────────────


def test_a_backup_holds_settings_and_no_credential_or_running_state(store) -> None:
    _seed(store)
    doc = yaml.safe_load(sr.backup_text(store))
    assert doc[sr.BACKUP_MARKER] == 1
    held = doc["settings"]
    assert held["agent.language"] == "ar"
    assert held["providers.list"][0]["api_key"] == "vault://cfg:providers.list.deepseek.api_key"
    assert held["connectors.slack.bot_token"] == "vault://cfg:connectors.slack.bot_token"
    for key in ("web_session.0f3a", "account.password_hash", "long_task.t1",
                "task_grant.t1", "system.lifecycle.last_boot_epoch"):
        assert key not in held, key
    assert json.loads(sr.backup_text(store, "json"))["settings"] == held


def test_a_backup_never_holds_a_secret(store) -> None:
    """A key kept in plain text (no vault) and a URL password are stars."""
    store._write_db_value("connectors.telegram.bot_token", "123:demo-plain-token", "connectors")
    store._write_db_value("backups.offsite.webdav.url", "https://owner:demo-pw@nas.local/dav", "backups")
    store._write_db_value("mcp.servers", json.dumps([
        {"name": "gh", "command": "npx", "env": {"GITHUB_TOKEN": "demo-gh-token"}},
    ]), "mcp")
    text = sr.backup_text(store)
    for secret in ("demo-plain-token", "demo-pw", "demo-gh-token"):
        assert secret not in text, secret
    held = yaml.safe_load(text)["settings"]
    assert held["connectors.telegram.bot_token"] == "****"
    assert json.loads(held["mcp.servers"])[0]["env"]["GITHUB_TOKEN"] == "****"


# ── the restore ─────────────────────────────────────────────────────────────


def test_a_restore_brings_the_settings_back(store) -> None:
    _seed(store)
    backup = sr.backup_text(store)
    store.set("agent.language", "fr", category="agent")
    store.set("memory.v2.summaries_min_turns", 9, category="memory")
    out = _restore(store, backup)
    assert out["restored"] == 2
    assert store.get("agent.language") == "ar"
    assert store.get("memory.v2.summaries_min_turns") == 4


def test_a_key_held_now_is_never_replaced(store) -> None:
    _seed(store)
    backup = sr.backup_text(store).replace(
        "vault://cfg:connectors.slack.bot_token", "demo-older-plain-token"
    )
    out = _restore(store, backup, dry_run=True)["plan"]
    assert "connectors.slack.bot_token" in out["kept_keys"]
    assert "connectors.slack.bot_token" not in out["changed"]
    _restore(store, backup)
    assert store._stored_raw("connectors.slack.bot_token") == "vault://cfg:connectors.slack.bot_token"


def test_a_missing_key_comes_back_only_when_its_reference_opens(store) -> None:
    _seed(store)
    store._write_db_value("connectors.discord.bot_token", "vault://cfg:connectors.discord.bot_token", "connectors")
    backup = sr.backup_text(store)
    store.delete("connectors.slack.bot_token")
    store.delete("connectors.discord.bot_token")  # its vault entry is gone too
    plan = _restore(store, backup, dry_run=True)["plan"]
    assert plan["keys_restored"] == ["connectors.slack.bot_token"]
    assert plan["keys_to_reenter"] == ["connectors.discord.bot_token"]
    _restore(store, backup)
    assert store._stored_raw("connectors.slack.bot_token") == "vault://cfg:connectors.slack.bot_token"
    assert store._stored_raw("connectors.discord.bot_token") is None


def test_named_lists_merge_by_name_and_keep_their_keys(store) -> None:
    store.set("providers.list", [
        {"name": "deepseek", "base_url": "https://api.deepseek.com",
         "api_key": "vault://cfg:providers.list.deepseek.api_key"},
        {"name": "zai", "base_url": "https://old.z.example", "api_key": "vault://cfg:providers.list.zai.api_key"},
    ], category="providers")
    backup = sr.backup_text(store)
    # Since the backup: deepseek lost its key, zai moved, and a new provider was added.
    store._write_db_value("providers.list", [
        {"name": "deepseek", "base_url": "https://changed", "api_key": ""},
        {"name": "zai", "base_url": "https://z.example", "api_key": "vault://cfg:providers.list.zai.api_key"},
        {"name": "local", "base_url": "http://127.0.0.1:1234", "api_key": ""},
    ], category="providers")
    out = _restore(store, backup)
    assert out["plan"]["lists"]["providers.list"] == {"added": [], "updated": ["deepseek", "zai"]}
    by_name = {p["name"]: p for p in store._stored_raw("providers.list")}
    assert by_name["deepseek"]["base_url"] == "https://api.deepseek.com"
    assert by_name["deepseek"]["api_key"] == "vault://cfg:providers.list.deepseek.api_key"  # came back
    assert by_name["zai"]["api_key"] == "vault://cfg:providers.list.zai.api_key"
    assert by_name["zai"]["base_url"] == "https://old.z.example"
    assert "local" in by_name, "a provider added since the backup stays"


def test_running_state_and_credentials_in_an_old_backup_are_never_written(store) -> None:
    """The earlier backup (``export_yaml``) carried every row."""
    _seed(store)
    old_backup = store.export_yaml()
    store.set("web_session.0f3a", {"username": "owner", "role": "admin", "signed_out": True}, category="auth")
    store.set("long_task.t1", {"budget": 7}, category="agent")
    store.set("agent.language", "fr", category="agent")
    plan = _restore(store, old_backup, dry_run=True)["plan"]
    assert plan["kept"]["credentials"] >= 2 and plan["kept"]["runtime"] >= 3
    _restore(store, old_backup)
    assert store.get("web_session.0f3a")["signed_out"] is True
    assert store.get("long_task.t1") == {"budget": 7}
    assert store.get("agent.language") == "ar"


def test_the_old_grouped_export_restores_without_inventing_keys(store) -> None:
    """Import/Export's earlier export grouped stored keys by category; read
    as nesting, it wrote ``<category>.<key>`` rows."""
    _seed(store)
    grouped = yaml.safe_dump({"agent": {"agent.language": "ar"}, "memory": {"memory.v2.summaries_min_turns": 4}})
    store.set("agent.language", "fr", category="agent")
    _restore(store, grouped)
    rows = _rows(store)
    assert rows["agent.language"] == "ar"
    assert not any(k.startswith(("agent.agent.", "memory.memory.")) for k in rows), sorted(rows)


def test_a_value_the_settings_page_refuses_is_not_restored(store) -> None:
    store.set("notifications.lifecycle.events", ["started"], category="notifications")
    store.set("cron.timezone", "Asia/Kuwait", category="cron")
    backup = yaml.safe_dump({sr.BACKUP_MARKER: 1, "settings": {
        "notifications.lifecycle.events": ["started", "a_message_kazma_never_sends"],
        "cron.timezone": "Mars/Olympus",
        "agent.language": "ar",
    }})
    out = _restore(store, backup)
    assert set(out["plan"]["refused"]) == {"notifications.lifecycle.events", "cron.timezone"}
    assert store.get("notifications.lifecycle.events") == ["started"]
    assert store.get("cron.timezone") == "Asia/Kuwait"
    assert store.get("agent.language") == "ar"


def test_a_retired_default_keeps_todays_value(store) -> None:
    store.set("notifications.lifecycle.events", ["started", "startup_failed"], category="notifications")
    old = ["starting", "started", "shutting_down", "startup_failed"]
    backup = yaml.safe_dump({sr.BACKUP_MARKER: 1, "settings": {"notifications.lifecycle.events": old}})
    plan = _restore(store, backup)["plan"]
    assert plan["kept"]["retired_defaults"] == 1
    assert store.get("notifications.lifecycle.events") == ["started", "startup_failed"]


def test_the_soul_comes_back_only_where_there_is_none(store) -> None:
    soul = {"agents": {"main": {"soul": "be brief"}}}
    backup = yaml.safe_dump({sr.BACKUP_MARKER: 1, "settings": {"self_improvement.agent_evolution": soul}})
    store.set("self_improvement.agent_evolution", {"agents": {"main": {"soul": "learned since"}}}, category="agent")
    assert _restore(store, backup)["plan"]["kept"]["learned"] == 1
    assert store.get("self_improvement.agent_evolution")["agents"]["main"]["soul"] == "learned since"
    store.delete("self_improvement.agent_evolution")
    _restore(store, backup)
    assert store.get("self_improvement.agent_evolution") == soul


def test_a_mail_account_comes_back_only_while_this_vault_holds_its_sign_in(store) -> None:
    accounts = [{"alias": "work", "type": "gmail"}, {"alias": "old", "type": "imap"}]
    backup = yaml.safe_dump({sr.BACKUP_MARKER: 1, "settings": {"email.accounts": accounts}})
    plan = _restore(store, backup)["plan"]
    assert plan["keys_to_reenter"] == ["email.accounts:old"]
    assert [a["alias"] for a in store.get("email.accounts")] == ["work"]


def test_sections_restore_only_what_was_picked(store) -> None:
    _seed(store)
    backup = sr.backup_text(store)
    store.set("agent.language", "fr", category="agent")
    store.set("memory.v2.summaries_min_turns", 9, category="memory")
    plan = _restore(store, backup, sections=["memory"])["plan"]
    assert plan["changed"] == ["memory.v2.summaries_min_turns"]
    assert store.get("agent.language") == "fr"


def test_the_preview_writes_nothing(store) -> None:
    _seed(store)
    backup = sr.backup_text(store)
    store.set("agent.language", "fr", category="agent")
    before = _rows(store)
    out = _restore(store, backup, dry_run=True)
    assert out["plan"]["changed"] == ["agent.language"]
    assert _rows(store) == before


def test_the_preview_runs_where_a_write_is_refused() -> None:
    """Negative control for the preview's scope: a write under it raises."""
    from kazma_core.diagnostic_scope import DiagnosticWriteRefused, read_only_diagnostic

    class Writes:
        def get_all(self):
            from kazma_core.diagnostic_scope import refuse_write

            refuse_write("config", "x")  # what any store write does first
            return {}

    with read_only_diagnostic("probe"), pytest.raises(DiagnosticWriteRefused):
        sr._plan_restore(yaml.safe_dump({sr.BACKUP_MARKER: 1, "settings": {}}), Writes())


def test_a_plan_that_changed_since_the_preview_writes_nothing(store) -> None:
    _seed(store)
    backup = sr.backup_text(store)
    store.set("agent.language", "fr", category="agent")
    digest = _restore(store, backup, dry_run=True)["plan"]["digest"]
    store.set("memory.v2.summaries_min_turns", 9, category="memory")  # changed after the preview
    before = _rows(store)
    with pytest.raises(sr.PlanChanged) as caught:
        _restore(store, backup, expect=digest)
    assert _rows(store) == before
    assert set(caught.value.summary["changed"]) == {"agent.language", "memory.v2.summaries_min_turns"}


def test_a_restore_deletes_nothing(store) -> None:
    _seed(store)
    backup = sr.backup_text(store)
    store.set("voice.enabled", True, category="voice")  # added since
    _restore(store, backup)
    assert store.get("voice.enabled") is True


@pytest.mark.parametrize("text", [
    "- just\n- a list\n", "plain words", "{not yaml: [", "", "kazma_settings_backup: 2\nsettings: {}\n",
    "kazma_settings_backup: 1\nsettings: [1, 2]\n",
])
def test_text_that_is_not_a_backup_is_refused(store, text) -> None:
    with pytest.raises(sr.NotABackup):
        _restore(store, text, dry_run=True)


def test_an_alias_bomb_is_refused_before_it_is_walked(store) -> None:
    bomb = "a: &a [x, x, x, x, x, x, x, x, x, x]\n"
    for i in range(1, 9):
        bomb += f"l{i}: &l{i} [" + ", ".join(["*" + ("a" if i == 1 else f"l{i - 1}")] * 10) + "]\n"
    with pytest.raises(sr.NotABackup):
        _restore(store, bomb, dry_run=True)


# ── undo ────────────────────────────────────────────────────────────────────


def test_undo_puts_back_what_the_restore_changed_and_keeps_its_keys(store) -> None:
    _seed(store)
    backup = sr.backup_text(store)
    store.set("agent.language", "fr", category="agent")
    store.delete("connectors.slack.bot_token")
    store.delete("memory.v2.summaries_min_turns")
    _restore(store, backup)
    assert store.get("agent.language") == "ar"
    store.set("connectors.slack.allowed_users", "changed-after-the-restore", category="connectors")

    plan = sr.undo(store, dry_run=True)["plan"]
    assert plan["reverted"] == ["agent.language", "memory.v2.summaries_min_turns"]
    assert plan["kept_keys"] == ["connectors.slack.bot_token"]
    sr.undo(store, expect=plan["digest"])
    assert store.get("agent.language") == "fr"
    assert store._stored_raw("memory.v2.summaries_min_turns") is None  # it had no row before
    assert store._stored_raw("connectors.slack.bot_token") == "vault://cfg:connectors.slack.bot_token"
    assert sr.undo(store, dry_run=True) == {"dry_run": True, "available": False}


def test_undo_leaves_a_setting_changed_again_since(store) -> None:
    _seed(store)
    backup = sr.backup_text(store)
    store.set("agent.language", "fr", category="agent")
    _restore(store, backup)
    store.set("agent.language", "de", category="agent")
    plan = sr.undo(store, dry_run=True)["plan"]
    assert plan["changed_since"] == ["agent.language"] and plan["reverted"] == []
    sr.undo(store)
    assert store.get("agent.language") == "de"


# ── the routes ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(store, monkeypatch):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_ui.settings import create_settings_router

    monkeypatch.setattr(sr, "_vault_has", opens)
    templates = Jinja2Templates(
        directory=str(Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates")
    )
    app = FastAPI()
    app.include_router(create_settings_router(SimpleNamespace(), store, templates))
    return TestClient(app)


def test_the_routes_preview_then_write_the_confirmed_plan(store, client) -> None:
    _seed(store)
    backup = client.get("/api/settings/system/backup")
    assert backup.status_code == 200 and sr.BACKUP_MARKER in backup.text
    store.set("agent.language", "en", category="agent")

    preview = client.post("/api/settings/system/restore?dry_run=true", content=backup.content)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["dry_run"] is True and body["plan"]["changed"] == ["agent.language"]
    assert store.get("agent.language") == "en"

    stale = client.post("/api/settings/system/restore?expect=not-the-plan", content=backup.content)
    assert stale.status_code == 409 and stale.json()["plan"]["changed"] == ["agent.language"]
    assert store.get("agent.language") == "en"

    done = client.post(f"/api/settings/system/restore?expect={body['plan']['digest']}", content=backup.content)
    assert done.status_code == 200, done.text
    assert done.json()["restored"] == 1 and done.json()["restart_recommended"] is True
    assert store.get("agent.language") == "ar"

    undo = client.get("/api/settings/system/restore/undo").json()
    assert undo["available"] is True and undo["plan"]["reverted"] == ["agent.language"]
    put_back = client.post(f"/api/settings/system/restore/undo?expect={undo['plan']['digest']}")
    assert put_back.status_code == 200 and store.get("agent.language") == "en"
    assert client.post("/api/settings/system/restore/undo").status_code == 409


def test_the_restore_route_refuses_what_is_not_a_backup(client) -> None:
    bad = client.post("/api/settings/system/restore?dry_run=true", content=b"{not yaml: [")
    assert bad.status_code == 400 and bad.json()["detail"]
    binary = client.post("/api/settings/system/restore?dry_run=true", content=b"\xff\xfe\x00junk")
    assert binary.status_code == 400
    big = client.post(
        "/api/settings/system/restore?dry_run=true",
        content=b"a: 1\n", headers={"content-length": str(sr.MAX_BACKUP_BYTES + 1)},
    )
    assert big.status_code == 413


def test_the_restore_route_refuses_a_store_that_would_lose_it(tmp_path) -> None:
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_core.config_store import _InMemoryStore
    from kazma_ui.settings import create_settings_router

    templates = Jinja2Templates(directory=str(tmp_path))
    app = FastAPI()
    app.include_router(create_settings_router(SimpleNamespace(), _InMemoryStore(), templates))
    resp = TestClient(app).post("/api/settings/system/restore?dry_run=true", content=b"agent:\n  language: ar\n")
    assert resp.status_code == 503


def test_the_raw_import_and_the_reset_are_gone(client) -> None:
    assert client.post("/api/settings/import", json={"data": "agent: {}"}).status_code in (404, 405)
    assert client.post("/api/settings/reset", json={"confirm": "RESET"}).status_code in (404, 405)


def test_the_manager_is_the_same_engine(store) -> None:
    _seed(store)
    sm = SettingsManager(config_store=store)
    backup = sm.create_backup()
    assert yaml.safe_load(backup)[sr.BACKUP_MARKER] == 1
    store.set("agent.language", "fr", category="agent")
    assert sm.restore_backup(backup, dry_run=True)["plan"]["changed"] == ["agent.language"]
