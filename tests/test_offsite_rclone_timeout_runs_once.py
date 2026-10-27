"""An rclone copy that times out is a failed copy, tried once.

Audit 2026-10-26 AUD-045: ``subprocess.TimeoutExpired`` from the rclone
fallback escaped into the native provider's ``except Exception`` handler,
which ran rclone a second time: another 30 minutes, then the same timeout,
then a generic error. Now the timeout is the fallback's answer (``ok:
False``, the primary's error kept), and the fallback runs outside the native
path's handler.

Negative control: the old shape (fallback inside the handler) runs rclone
twice.
"""

from __future__ import annotations

import subprocess

import pytest
from kazma_core.backup import universal as uni


@pytest.fixture
def cfg(monkeypatch):
    conf = {"enabled": True, "provider": "google_drive", "rclone_remote": "kazma-backup:kazma-backups"}
    monkeypatch.setattr(uni, "_offsite_config", lambda: dict(conf))
    monkeypatch.setattr(uni.shutil, "which", lambda name: "/usr/bin/rclone")
    monkeypatch.setattr(uni, "_zip_backup_dir", lambda dest: dest / "archive.zip")
    import kazma_core.backup.cloud_sync as cs

    class _Failing:
        async def upload_file(self, path, name):
            return {"ok": False, "error": "invalid_grant"}

    monkeypatch.setattr(cs, "get_sync_provider", lambda: _Failing())
    return conf


def _rclone_times_out(monkeypatch) -> list:
    calls: list = []

    def _run(cmd, **kw):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1800))

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def test_a_timed_out_rclone_runs_once_and_reports_both_failures(tmp_path, cfg, monkeypatch):
    calls = _rclone_times_out(monkeypatch)
    dest = tmp_path / "1790000000"
    dest.mkdir()
    (dest / "archive.zip").write_bytes(b"zip")

    result = uni._offsite_sync(dest)

    assert len(calls) == 1, f"rclone ran {len(calls)} times"
    assert result["ok"] is False
    assert result["via"] == "rclone"
    assert "timed out" in result["error"]
    assert "invalid_grant" in result["error"]


def test_negative_control_the_fallback_inside_the_handler_runs_twice(monkeypatch):
    import asyncio

    calls = _rclone_times_out(monkeypatch)

    async def _rclone(reason: str = ""):
        # The old _rclone: no TimeoutExpired handling.
        return subprocess.run(["rclone", "copy"], timeout=1800)

    async def _old_run():
        try:
            return await _rclone("invalid_grant")
        except Exception as exc:  # the native path's handler
            return await _rclone(str(exc))

    with pytest.raises(subprocess.TimeoutExpired):
        asyncio.run(_old_run())
    assert len(calls) == 2
