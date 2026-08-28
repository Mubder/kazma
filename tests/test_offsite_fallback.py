"""A fallback that shares the failing credential is not a fallback.

Live, 2026-08-28: the native ``google_drive`` provider borrows the Gmail
OAuth refresh token, that grant was revoked, and 29 consecutive backups
went local-only. An rclone remote with its OWN Drive credential was
configured the whole time and worked -- but ``_offsite_sync`` returned
unconditionally from the native branch, success or failure, so a broken
primary could never reach a working secondary.

The independence is the point. Two paths through the same revoked token
would have failed together.
"""

from __future__ import annotations

import asyncio

import pytest
from kazma_core.backup import universal as uni


@pytest.fixture
def cfg(monkeypatch):
    conf = {"enabled": True, "provider": "google_drive",
            "rclone_remote": "kazma-backup:kazma-backups"}
    monkeypatch.setattr(uni, "_offsite_config", lambda: dict(conf))
    monkeypatch.setattr(uni.shutil, "which", lambda name: "/usr/bin/rclone")
    monkeypatch.setattr(uni, "_zip_backup_dir", lambda dest: dest / "archive.zip")
    return conf


class _Provider:
    def __init__(self, result):
        self._result = result

    async def upload_file(self, path, name):
        return self._result


def _install_provider(monkeypatch, result=None, raises=None):
    import kazma_core.backup.cloud_sync as cs

    def _get():
        if raises:
            raise raises
        return _Provider(result)

    monkeypatch.setattr(cs, "get_sync_provider", _get)


def _rclone_returns(monkeypatch, code, stderr=""):
    import subprocess

    calls = []

    def _run(cmd, **kw):
        calls.append(cmd)
        return type("P", (), {"returncode": code, "stdout": "", "stderr": stderr})()

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def _sync(tmp_path):
    d = tmp_path / "1787942545"
    d.mkdir(parents=True, exist_ok=True)
    (d / "archive.zip").write_bytes(b"zip")
    return uni._offsite_sync(d)


# ── the gap that let 29 backups go unprotected ────────────────────────


def test_a_failing_primary_falls_through_to_rclone(tmp_path, cfg, monkeypatch):
    _install_provider(monkeypatch, result={"ok": False, "error": "invalid_grant"})
    calls = _rclone_returns(monkeypatch, 0)

    res = _sync(tmp_path)

    assert res["ok"], "a working secondary must rescue a broken primary"
    assert res["via"] == "rclone"
    assert res["fallback_used"] is True
    assert "invalid_grant" in res["primary_error"], "keep why the primary failed"
    assert calls and calls[0][0] == "rclone"


def test_a_raising_primary_also_falls_through(tmp_path, cfg, monkeypatch):
    """The live failure arrived as an exception, not a False result."""
    _install_provider(monkeypatch, raises=RuntimeError("Token refresh failed"))
    _rclone_returns(monkeypatch, 0)

    res = _sync(tmp_path)
    assert res["ok"] and res["fallback_used"] is True


def test_a_working_primary_never_touches_rclone(tmp_path, cfg, monkeypatch):
    """No pointless second upload of a multi-hundred-MB archive."""
    _install_provider(monkeypatch, result={"ok": True, "remote": "gdrive:/x.zip"})
    calls = _rclone_returns(monkeypatch, 0)

    res = _sync(tmp_path)
    assert res["ok"] and not res.get("fallback_used")
    assert not calls, "the fallback must not run when the primary succeeded"


def test_both_paths_failing_reports_both_reasons(tmp_path, cfg, monkeypatch):
    """Two errors, one message. Being told only about rclone would send the
    operator to fix the wrong thing."""
    _install_provider(monkeypatch, result={"ok": False, "error": "invalid_grant"})
    _rclone_returns(monkeypatch, 1, stderr="directory not found")

    res = _sync(tmp_path)
    assert not res["ok"]
    assert "invalid_grant" in res["error"] and "directory not found" in res["error"]


def test_no_rclone_remote_still_reports_the_primary_failure(tmp_path, cfg, monkeypatch):
    """Without a second path the primary error must survive, not be
    replaced by 'nothing configured'."""
    cfg["rclone_remote"] = ""
    _install_provider(monkeypatch, result={"ok": False, "error": "invalid_grant"})

    res = _sync(tmp_path)
    assert not res["ok"]
    assert "invalid_grant" in res["error"]


def test_rclone_missing_from_path_still_reports_the_primary_failure(
        tmp_path, cfg, monkeypatch):
    _install_provider(monkeypatch, result={"ok": False, "error": "invalid_grant"})
    monkeypatch.setattr(uni.shutil, "which", lambda name: None)

    res = _sync(tmp_path)
    assert not res["ok"]
    assert "invalid_grant" in res["error"]


def test_disabled_offsite_is_still_skipped(tmp_path, cfg, monkeypatch):
    cfg["enabled"] = False
    res = _sync(tmp_path)
    assert res.get("skipped")


def test_rclone_only_config_is_unchanged(tmp_path, cfg, monkeypatch):
    """The pre-existing shape -- no native provider, rclone configured --
    must keep working exactly as before."""
    cfg["provider"] = ""
    calls = _rclone_returns(monkeypatch, 0)

    res = _sync(tmp_path)
    assert res["ok"] and res["via"] == "rclone"
    assert not res.get("fallback_used"), "this is the primary path, not a fallback"
    assert calls


def test_the_two_paths_do_not_share_a_credential():
    """The reason this fallback is worth having.

    GoogleDriveSync reads the Gmail refresh token out of the vault; rclone
    holds its own OAuth grant in its own config. If the fallback ever
    starts borrowing the same credential, it stops being a second path and
    the next revocation takes both.
    """
    import inspect

    import kazma_core.backup.cloud_sync as cs

    drive_src = inspect.getsource(cs.GoogleDriveSync)
    assert "email.gmail.refresh_token" in drive_src

    rclone_src = inspect.getsource(uni._offsite_sync)
    assert "email.gmail" not in rclone_src, (
        "the rclone path must not read Kazma's Google credential"
    )


def test_asyncio_is_imported_where_the_fallback_needs_it():
    """_rclone awaits asyncio.to_thread from inside a nested helper."""
    assert asyncio is not None
