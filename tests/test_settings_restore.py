"""A Settings backup restores what it saved (AUD-015).

Settings -> System -> "Create backup" downloads ``kazma-backup-DATE.yaml``
(``/api/settings/system/backup`` -> ``SettingsManager.create_backup``, i.e.
``ConfigStore.export_yaml``: nested by key path, secrets as ``vault://``
references). ``/api/settings/system/restore`` imports it through
``import_config``, which flattens the nesting back to dotted keys and writes
them in one ``batch_set``. Neither half had a test; this is the round trip,
through the manager and through the route.
"""

from __future__ import annotations

import pytest

from kazma_core.config_store import ConfigStore
from kazma_core.settings_manager import SettingsManager

SAVED = {
    "agent.language": "ar",
    "connectors.slack.allowed_users": "U0BBRDBJ491",
    "memory.v2.summaries_min_turns": 4,
    "notifications.lifecycle.events": ["started", "startup_failed"],
}


@pytest.fixture
def manager(tmp_path):
    cs = ConfigStore(
        db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "none.yaml"),
    )
    for key, value in SAVED.items():
        cs.set(key, value, category=key.split(".")[0])
    yield SettingsManager(config_store=cs), cs
    cs.close()


def test_a_backup_restores_what_it_saved(manager) -> None:
    sm, cs = manager
    backup = sm.create_backup()

    for key in SAVED:
        cs.set(key, "changed-after-the-backup", category=key.split(".")[0])
    assert cs.get("agent.language") == "changed-after-the-backup"

    assert sm.restore_backup(backup) >= len(SAVED)
    for key, value in SAVED.items():
        assert cs.get(key) == value, key


def test_a_restore_of_something_else_changes_nothing(manager) -> None:
    """Negative control: text that is not a mapping restores nothing."""
    sm, cs = manager
    assert sm.restore_backup("- just\n- a list\n") == 0
    assert sm.restore_backup("plain words") == 0
    for key, value in SAVED.items():
        assert cs.get(key) == value, key


def test_the_restore_route_round_trips(manager) -> None:
    """Through the real Settings router, on the same store."""
    from pathlib import Path
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient

    from kazma_ui.settings import create_settings_router

    _sm, cs = manager
    templates = Jinja2Templates(
        directory=str(Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates")
    )
    app = FastAPI()
    app.include_router(create_settings_router(SimpleNamespace(), cs, templates))
    client = TestClient(app)

    backup = client.get("/api/settings/system/backup")
    assert backup.status_code == 200
    cs.set("agent.language", "en", category="agent")

    resp = client.post("/api/settings/system/restore", content=backup.content)
    assert resp.status_code == 200 and resp.json().get("status") == "ok", resp.text
    assert cs.get("agent.language") == "ar"

    bad = client.post("/api/settings/system/restore", content=b"{not yaml: [")
    assert "error" in bad.json()
