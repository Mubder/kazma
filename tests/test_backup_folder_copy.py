"""A backup copy in a folder the owner chooses (Settings → Backup, 20 Nov 2026).

The owner's item: Kazma's backups live on the disk Kazma runs on, and the
cloud copy needs an account; a third destination is a folder on another
drive, a USB disk or a network share. ``backup.folder_copy`` keeps a restic
repository there (``<folder>/kazma-restic``): every universal backup is
copied into it, encrypted, its old snapshots go by the folder's own count
(``backups.folder.keep``), and a folder that is not there (a USB disk
unplugged) is said once and said again once when it is back.

Each rule runs through the real code with real restic, with its negative
control: the backup without making the folder's repository fails as an
unmade repository does; the notice without its stored mark repeats on every
backup; the maintenance with the default policy ignores the folder's count.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from kazma_core.backup import folder_copy as fc
from kazma_core.backup import restic_repo as rr
from kazma_core.config_store import get_config_store
from kazma_core.settings_validation import SettingRejected, validate_setting

_PW = "test-passphrase-not-a-real-secret"

needs_restic = pytest.mark.skipif(not rr.restic_available(), reason="restic is not installed")


@pytest.fixture
def data(tmp_path, monkeypatch):
    """The install's own repositories under a folder of this test."""
    folder = tmp_path / "data"
    monkeypatch.setattr(rr, "_data_dir", lambda: folder)
    monkeypatch.setenv("KAZMA_RESTIC_PASSWORD", _PW)
    return folder


@pytest.fixture
def alerts(monkeypatch):
    raised: list[str] = []

    def fake_alert(key, *_a, **_k):
        raised.append(key)

    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", fake_alert)
    return raised


@pytest.fixture
def backup_dir(tmp_path):
    """A finished universal backup folder, as ``_snapshot_to_restic`` gets it."""
    dest = tmp_path / "backups" / "backup_1"
    dest.mkdir(parents=True)
    (dest / "manifest.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    (dest / "settings.db").write_bytes(b"settings stand-in")
    return dest


def _choose(path: Path | str, *, keep: int = 7, enabled: bool = True) -> None:
    get_config_store().batch_set([
        (fc.ENABLED_KEY, enabled, "backups"),
        (fc.PATH_KEY, str(path), "backups"),
        (fc.KEEP_KEY, keep, "backups"),
    ])


def _count(repo: Path) -> int:
    res = rr.snapshots(str(repo), _PW)
    assert res.ok, res.error
    return int(res.detail["count"])


def _snapshot(dest: Path) -> dict:
    from kazma_core.backup.universal import _snapshot_to_restic

    return _snapshot_to_restic(dest) or {}


# ── Every backup is copied into the chosen folder ─────────────────────────


@needs_restic
def test_every_backup_is_copied_into_the_chosen_folder(tmp_path, data, alerts, backup_dir):
    usb = tmp_path / "usb"
    usb.mkdir()
    _choose(usb)
    assert fc.folder_state()["state"] == "new"

    out = _snapshot(backup_dir)

    assert out["folder"]["ok"], out["folder"]
    repo = usb / fc.REPO_FOLDER
    assert (repo / "config").is_file(), "the first backup makes Kazma's repository in the folder"
    assert _count(repo) == 1
    assert fc.folder_state()["state"] == "ready"
    assert not [k for k in alerts if k.startswith("backup.folder")], "a folder that is there says nothing"
    _snapshot(backup_dir)
    assert _count(repo) == 2


@needs_restic
def test_control_without_making_the_folders_repository_the_copy_fails(
        tmp_path, data, alerts, backup_dir, monkeypatch):
    usb = tmp_path / "usb"
    usb.mkdir()
    _choose(usb)
    monkeypatch.setattr(rr, "_folder_repo_made", lambda *_a: None)

    out = _snapshot(backup_dir)

    assert out["folder"]["ok"] is False
    assert not (usb / fc.REPO_FOLDER / "config").exists()


@needs_restic
def test_the_folder_is_copied_only_while_it_is_switched_on(tmp_path, data, alerts, backup_dir):
    usb = tmp_path / "usb"
    usb.mkdir()
    _choose(usb, enabled=False)
    out = _snapshot(backup_dir)
    assert "folder" not in out
    assert not (usb / fc.REPO_FOLDER).exists()


# ── A folder that is not there is said once, and once when it is back ─────


@needs_restic
def test_a_missing_folder_is_said_once_and_its_return_once(tmp_path, data, alerts, backup_dir):
    usb = tmp_path / "usb-unplugged"
    _choose(usb)

    first = _snapshot(backup_dir)
    second = _snapshot(backup_dir)

    assert first["folder"] == {"ok": False, "skipped": "missing"}
    assert second["folder"] == {"ok": False, "skipped": "missing"}
    assert [k for k in alerts if k.startswith("backup.folder")] == ["backup.folder_missing"]
    assert get_config_store().get(fc.MISSING_SINCE_KEY)

    usb.mkdir()  # the disk is plugged in again
    back = _snapshot(backup_dir)
    again = _snapshot(backup_dir)

    assert back["folder"]["ok"] and again["folder"]["ok"]
    assert [k for k in alerts if k.startswith("backup.folder")] == [
        "backup.folder_missing", "backup.folder_back"]
    assert not get_config_store().get(fc.MISSING_SINCE_KEY)
    assert _count(usb / fc.REPO_FOLDER) == 2


def test_control_without_its_mark_a_missing_folder_is_said_every_time(tmp_path, alerts, monkeypatch):
    _choose(tmp_path / "usb-unplugged")

    class Forgetful:
        """The store as it would be with no mark kept."""

        def get(self, key):
            return None if key == fc.MISSING_SINCE_KEY else get_config_store().get(key)

        def set(self, *_a, **_k):
            return None

        def delete(self, *_a, **_k):
            return None

    monkeypatch.setattr(fc, "_store", lambda: Forgetful())
    for _ in range(3):
        assert fc.note_presence() == "missing"
    assert alerts == ["backup.folder_missing"] * 3


def test_a_folder_switched_off_while_missing_says_nothing_back(tmp_path, alerts):
    _choose(tmp_path / "usb-unplugged")
    assert fc.note_presence() == "missing"
    _choose(tmp_path / "usb-unplugged", enabled=False)
    assert fc.note_presence() == "off"
    assert alerts == ["backup.folder_missing"]
    assert not get_config_store().get(fc.MISSING_SINCE_KEY), "the mark goes with the setting"


# ── The folder keeps its own count ────────────────────────────────────────


def _three_snapshots(repo: Path, tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir(exist_ok=True)
    for i in range(3):
        (payload / "dump.txt").write_text(f"dump {i}", encoding="utf-8")
        res = rr.backup(str(repo), _PW, [str(payload)], tags=["kazma", "universal"])
        assert res.ok, res.error


@needs_restic
def test_the_folder_keeps_its_own_count(tmp_path, data, alerts):
    from kazma_core.memory.worker_bootstrap import _handle_restic_maintenance

    usb = tmp_path / "usb"
    usb.mkdir()
    _choose(usb, keep=2)
    repo = usb / fc.REPO_FOLDER
    _three_snapshots(repo, tmp_path)
    assert _count(repo) == 3

    assert asyncio.run(_handle_restic_maintenance({})) is True
    assert _count(repo) == 2


@needs_restic
def test_a_count_of_zero_keeps_every_copy(tmp_path, data, alerts):
    from kazma_core.memory.worker_bootstrap import _handle_restic_maintenance

    usb = tmp_path / "usb"
    usb.mkdir()
    _choose(usb, keep=0)
    repo = usb / fc.REPO_FOLDER
    _three_snapshots(repo, tmp_path)

    asyncio.run(_handle_restic_maintenance({}))
    assert _count(repo) == 3


@needs_restic
def test_control_with_the_default_policy_the_folders_count_is_ignored(tmp_path, data, alerts, monkeypatch):
    """The maintenance as it was: every repository forgets by KEEP_POLICY
    (seven daily snapshots and more), so three of today stay whatever the
    folder's count says."""
    from kazma_core.memory.worker_bootstrap import _handle_restic_maintenance

    usb = tmp_path / "usb"
    usb.mkdir()
    _choose(usb, keep=1)
    repo = usb / fc.REPO_FOLDER
    _three_snapshots(repo, tmp_path)
    monkeypatch.setattr(fc, "keep_policy", lambda: rr.KEEP_POLICY)

    asyncio.run(_handle_restic_maintenance({}))
    assert _count(repo) > 1


# ── What a folder may be ──────────────────────────────────────────────────


def test_the_settings_are_checked_before_they_are_kept(tmp_path):
    from kazma_core.paths import data_dir

    assert validate_setting(fc.ENABLED_KEY, True)[0] is True
    assert validate_setting(fc.KEEP_KEY, "3")[0] == 3
    assert validate_setting(fc.KEEP_KEY, 0)[0] == 0
    assert validate_setting(fc.PATH_KEY, str(tmp_path))[0] == str(tmp_path)
    assert validate_setting(fc.PATH_KEY, "")[0] == ""
    for key, value in [
        (fc.ENABLED_KEY, "sometimes"),
        (fc.KEEP_KEY, -1),
        (fc.KEEP_KEY, "seven"),
        (fc.KEEP_KEY, True),
        (fc.PATH_KEY, "backups/usb"),
        (fc.PATH_KEY, str(data_dir() / "backups" / "copy")),
    ]:
        with pytest.raises(SettingRejected):
            validate_setting(key, value)


def test_keep_reads_a_bad_stored_value_as_the_default():
    assert fc._keep_count(None) == fc.DEFAULT_KEEP
    assert fc._keep_count("x") == fc.DEFAULT_KEEP
    assert fc._keep_count(True) == fc.DEFAULT_KEEP
    assert fc._keep_count(-3) == 0
    assert fc._keep_count("4") == 4


def test_a_folder_test_writes_and_removes_its_probe_only(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_RESTIC_PASSWORD", _PW)
    target = tmp_path / "new" / "backups"
    res = fc.test_folder(str(target))
    checks = {c["check"]: c for c in res["checks"]}
    assert target.is_dir()
    assert checks["writable"]["ok"] and checks["writable"]["detail"] == "made"
    assert list(target.iterdir()) == [], "the probe file is removed"
    assert res["repo"] == str(target / fc.REPO_FOLDER)
    assert res["ok"] is (rr.restic_available())
    assert fc.test_folder("relative/path")["problem"] == "relative"
    assert fc.test_folder("")["problem"] == "empty"


# ── The page's routes ─────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path):
    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_ui.settings import create_settings_router

    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "settings.html").write_text("ok")
    app = FastAPI()
    app.include_router(create_settings_router(
        MagicMock(), get_config_store(), Jinja2Templates(directory=str(tmp_path / "templates"))))
    return TestClient(app)


def test_the_routes_save_show_and_refuse(client, tmp_path):
    usb = tmp_path / "usb"
    usb.mkdir()
    assert client.get("/api/settings/backup/folder").json()["state"] == "off"

    refused = client.put("/api/settings/backup/folder", json={"enabled": True, "path": ""})
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "no_folder"
    bad = client.put("/api/settings/backup/folder", json={"path": "relative", "keep": 3})
    assert bad.status_code == 400
    bad_keep = client.put("/api/settings/backup/folder", json={"path": str(usb), "keep": -1})
    assert bad_keep.status_code == 400
    assert get_config_store().get(fc.PATH_KEY) in (None, ""), "a refused save writes nothing"

    saved = client.put("/api/settings/backup/folder", json={"enabled": True, "path": str(usb), "keep": 3})
    assert saved.status_code == 200, saved.text
    assert saved.json()["state"] == "new"
    shown = client.get("/api/settings/backup/folder").json()
    assert shown["enabled"] is True and shown["path"] == str(usb) and shown["keep"] == 3
    assert shown["missing_since"] is None

    tested = client.post("/api/settings/backup/folder/test", json={"path": str(usb)}).json()
    assert {c["check"] for c in tested["checks"]} == {"writable", "free", "other_disk", "restic", "passphrase"}


def test_the_folder_picker_lists_folders_and_never_a_network_path(client, tmp_path, monkeypatch):
    (tmp_path / "pick" / "b").mkdir(parents=True)
    (tmp_path / "pick" / "a").mkdir()
    (tmp_path / "pick" / ".hidden").mkdir()
    (tmp_path / "pick" / "file.txt").write_text("x", encoding="utf-8")
    listed = client.post("/api/settings/backup/folder/browse", json={"path": str(tmp_path / "pick")}).json()
    assert listed["folders"] == ["a", "b"]
    assert listed["parent"] == str(tmp_path)
    assert listed["drives"]
    assert client.post("/api/settings/backup/folder/browse", json={}).json()["folders"] == []

    asked: list[str] = []
    real_browse = fc.browse

    def watching_browse(path=""):
        asked.append(path)
        return real_browse(path)

    monkeypatch.setattr(fc, "browse", watching_browse)
    share = client.post("/api/settings/backup/folder/browse", json={"path": r"\\attacker\share"}).json()
    assert share["problem"] == "network"
    assert asked == [""], "listing a share connects to its host: only the drives are read"
    assert client.get("/api/settings/backup/folder/browse").status_code == 405, "a POST only"
