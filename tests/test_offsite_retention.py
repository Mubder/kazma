"""Kazma deletes its old offsite copies (2026-11-03).

Every universal backup -- four a day, ~650 MB each -- went offsite and
nothing ever deleted one: the owner found the remote full and deleted the old
copies by hand. After a copy is made, on the path that made it, Kazma now
lists the remote and deletes its own copies past the newest
``backups.offsite.keep`` (7 by default; 0 keeps every copy).

What must hold, each with its negative control:

* only Kazma's copies are deleted -- ``<epoch>.zip`` from a native provider,
  ``<epoch>/`` from rclone -- never anything else in the folder;
* a listing that does not show the copy just made deletes nothing (a stale
  or cut answer may be missing others too);
* a failed listing or delete is said, never fails the backup;
* every provider can list and delete, and refuses a name that is not a
  plain file name before any I/O.
"""

from __future__ import annotations

import asyncio
import inspect
import subprocess
import types
from pathlib import Path
from typing import Any

import pytest

from kazma_core.backup import cloud_sync as cs
from kazma_core.backup import universal as uni

# ── which copies are old ──────────────────────────────────────────────


def test_the_copies_past_the_newest_keep_are_old_oldest_first():
    names = ["300.zip", "100.zip", "500.zip", "200.zip", "400.zip"]
    assert uni._old_copies(names, uni._NATIVE_COPY, "500.zip", 2) == ["100.zip", "200.zip", "300.zip"]
    assert uni._old_copies(names, uni._NATIVE_COPY, "500.zip", 3) == ["100.zip", "200.zip"]


def test_keep_one_keeps_only_the_copy_just_made():
    names = ["100.zip", "200.zip", "300.zip"]
    assert uni._old_copies(names, uni._NATIVE_COPY, "300.zip", 1) == ["100.zip", "200.zip"]


def test_fewer_copies_than_keep_deletes_nothing():
    assert uni._old_copies(["100.zip", "200.zip"], uni._NATIVE_COPY, "200.zip", 10) == []


def test_ordering_is_by_number_not_by_text():
    """'99' sorts after '1000' as text; it is the older copy."""
    names = ["99.zip", "1000.zip", "1001.zip"]
    assert uni._old_copies(names, uni._NATIVE_COPY, "1001.zip", 2) == ["99.zip"]


def test_nothing_but_kazmas_copies_is_ever_old():
    names = ["100.zip", "notes.txt", "backup_20260101.zip", "100.zip.tmp",
             "200/", "x100.zip", "300.zip"]
    assert uni._old_copies(names, uni._NATIVE_COPY, "300.zip", 1) == ["100.zip"]
    assert uni._old_copies(["100/", "200/", "notes/", "100.zip", "300/"],
                           uni._RCLONE_COPY, "300/", 1) == ["100/", "200/"]


def test_a_listing_without_the_copy_just_made_is_not_trusted():
    with pytest.raises(ValueError, match="does not show the copy just made"):
        uni._old_copies(["100.zip", "200.zip"], uni._NATIVE_COPY, "300.zip", 1)


# ── the prune ─────────────────────────────────────────────────────────


class _Remote:
    """A remote holding names; records every listing and delete."""

    def __init__(self, names: list[str], *, refuse: dict[str, Any] | None = None) -> None:
        self.names = list(names)
        self.refuse = refuse or {}
        self.listed = 0
        self.deleted: list[str] = []

    async def list_names(self) -> dict[str, Any]:
        self.listed += 1
        return {"ok": True, "names": list(self.names)}

    async def delete_one(self, name: str) -> dict[str, Any]:
        why = self.refuse.get(name)
        if isinstance(why, Exception):
            raise why
        if why:
            return {"ok": False, "error": why}
        self.deleted.append(name)
        self.names.remove(name)
        return {"ok": True}


def _prune(remote: _Remote, *, current: str, keep: int, pattern=uni._NATIVE_COPY) -> dict[str, Any]:
    return asyncio.run(uni._prune_offsite(
        remote.list_names, remote.delete_one, pattern=pattern, current=current, keep=keep))


def test_a_clean_prune_deletes_the_old_copies_and_says_so():
    remote = _Remote(["100.zip", "200.zip", "300.zip", "400.zip", "notes.txt"])
    out = _prune(remote, current="400.zip", keep=2)
    assert out == {"pruned": ["100.zip", "200.zip"]}
    assert remote.names == ["300.zip", "400.zip", "notes.txt"]


def test_keep_zero_keeps_every_copy_and_lists_nothing():
    remote = _Remote(["100.zip", "200.zip"])
    assert _prune(remote, current="200.zip", keep=0) == {}
    assert remote.listed == 0 and remote.deleted == []


@pytest.mark.parametrize("listing", [
    {"ok": False, "error": "403 AccessDenied"},
    RuntimeError("connection reset"),
    "not a dict",
    None,
])
def test_a_listing_that_fails_deletes_nothing_and_says_why(listing):
    deleted: list[str] = []

    async def list_names():
        if isinstance(listing, Exception):
            raise listing
        return listing

    async def delete_one(name):
        deleted.append(name)
        return {"ok": True}

    out = asyncio.run(uni._prune_offsite(list_names, delete_one, pattern=uni._NATIVE_COPY,
                                         current="300.zip", keep=1))
    assert out["prune_error"].startswith("could not list the offsite copies")
    assert "pruned" not in out and deleted == []


def test_a_listing_missing_the_copy_just_made_deletes_nothing():
    remote = _Remote(["100.zip", "200.zip"])
    out = _prune(remote, current="300.zip", keep=1)
    assert "does not show the copy just made" in out["prune_error"]
    assert remote.deleted == []


def test_negative_control_without_the_check_a_stale_listing_deletes(monkeypatch):
    """The check is what stops it: a rule that trusts the listing as given
    deletes from one that does not even hold this run's copy."""
    def trusting(names, pattern, current, keep):
        older = sorted((int(m.group(1)), n) for n in names if (m := pattern.match(n)))
        return [n for _, n in older[: max(0, len(older) - (keep - 1))]]

    monkeypatch.setattr(uni, "_old_copies", trusting)
    remote = _Remote(["100.zip", "200.zip"])
    out = _prune(remote, current="300.zip", keep=1)
    assert remote.deleted == ["100.zip", "200.zip"] and "prune_error" not in out


def test_a_refused_or_raising_delete_is_named_and_the_rest_still_go():
    remote = _Remote(["100.zip", "200.zip", "300.zip", "400.zip"],
                     refuse={"100.zip": "403 AccessDenied", "200.zip": RuntimeError("timeout")})
    out = _prune(remote, current="400.zip", keep=1)
    assert out["pruned"] == ["300.zip"]
    assert "100.zip: 403 AccessDenied" in out["prune_error"]
    assert "200.zip: timeout" in out["prune_error"]
    assert "prune_left" not in out


def test_a_prune_out_of_time_says_how_many_are_left(monkeypatch):
    monkeypatch.setattr(uni, "_PRUNE_BUDGET_S", 0.0)
    remote = _Remote(["100.zip", "200.zip", "300.zip"])
    out = _prune(remote, current="300.zip", keep=1)
    assert out == {"pruned": [], "prune_left": 2}
    assert remote.deleted == []


def test_a_prune_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(uni, "_prune_offsite_steps", boom)
    out = _prune(_Remote(["100.zip"]), current="100.zip", keep=1)
    assert out["prune_error"].startswith("offsite prune failed")


# ── the native path ───────────────────────────────────────────────────


class _Provider:
    """A native provider: the upload lands, and the remote lists it."""

    def __init__(self, existing: list[str], *, upload_ok: bool = True) -> None:
        self.names = list(existing)
        self.upload_ok = upload_ok
        self.listed = 0
        self.deleted: list[str] = []

    async def upload_file(self, local: Path, remote_name: str) -> dict[str, Any]:
        if not self.upload_ok:
            return {"ok": False, "error": "invalid_grant"}
        self.names.append(remote_name)
        return {"ok": True, "remote": f"fake:{remote_name}"}

    async def list_backups(self) -> dict[str, Any]:
        self.listed += 1
        return {"ok": True, "names": list(self.names)}

    async def delete_backup(self, name: str) -> dict[str, Any]:
        self.deleted.append(name)
        self.names.remove(name)
        return {"ok": True}


@pytest.fixture
def backup(tmp_path):
    dest = tmp_path / "5000"
    dest.mkdir()
    (dest / "manifest.json").write_text("{}", encoding="utf-8")
    return dest


def _native(monkeypatch, provider, **cfg):
    config = {"enabled": True, "provider": "s3", "rclone_remote": "", **cfg}
    monkeypatch.setattr(uni, "_offsite_config", lambda: dict(config))
    monkeypatch.setattr(cs, "get_sync_provider", lambda: provider)
    monkeypatch.setattr(uni.shutil, "which", lambda name: None)


def test_the_native_path_deletes_its_old_zips_after_the_upload(monkeypatch, backup):
    provider = _Provider([f"{n}.zip" for n in (1000, 2000, 3000, 4000)] + ["notes.txt"])
    _native(monkeypatch, provider, keep=3)
    result = uni._offsite_sync(backup)
    assert result["ok"] is True and result["pruned"] == ["1000.zip", "2000.zip"]
    assert provider.names == ["3000.zip", "4000.zip", "notes.txt", "5000.zip"]
    assert not (backup.parent / "5000.zip").exists(), "the local zip is still removed"


def test_without_a_keep_setting_seven_copies_stay(monkeypatch, backup):
    provider = _Provider([f"{n}.zip" for n in range(1000, 1010)])
    _native(monkeypatch, provider)
    result = uni._offsite_sync(backup)
    assert len(result["pruned"]) == 4
    assert len([n for n in provider.names if n.endswith(".zip")]) == 7


def test_keep_zero_on_the_native_path_lists_nothing(monkeypatch, backup):
    provider = _Provider(["1000.zip"])
    _native(monkeypatch, provider, keep=0)
    result = uni._offsite_sync(backup)
    assert result["ok"] is True and "pruned" not in result
    assert provider.listed == 0


def test_a_failed_upload_prunes_nothing(monkeypatch, backup):
    provider = _Provider(["1000.zip", "2000.zip"], upload_ok=False)
    _native(monkeypatch, provider, keep=1)
    result = uni._offsite_sync(backup)
    assert result.get("ok") is not True
    assert provider.listed == 0 and provider.deleted == []


def test_a_provider_that_cannot_list_keeps_the_upload_and_says_so(monkeypatch, backup):
    class _UploadOnly:
        async def upload_file(self, local, remote_name):
            return {"ok": True, "remote": remote_name}

    _native(monkeypatch, _UploadOnly(), keep=1)
    result = uni._offsite_sync(backup)
    assert result["ok"] is True
    assert "cannot list" in result["prune_error"]


# ── the rclone path ───────────────────────────────────────────────────


class _Rclone:
    """subprocess.run for rclone: copy, lsf and purge."""

    def __init__(self, folders: str, *, purge_stderr: str = "", purge_rc: int = 0) -> None:
        self.folders = folders
        self.purge_stderr = purge_stderr
        self.purge_rc = purge_rc
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[1] == "lsf":
            return subprocess.CompletedProcess(argv, 0, self.folders, "")
        if argv[1] == "purge":
            return subprocess.CompletedProcess(argv, self.purge_rc, "", self.purge_stderr)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def purged(self) -> list[str]:
        return [a[-1] for a in self.calls if a[1] == "purge"]


def _rclone(monkeypatch, run, remote="r2:kazma-backups", keep=2):
    monkeypatch.setattr(uni, "_offsite_config", lambda: {
        "enabled": True, "provider": "", "rclone_remote": remote, "keep": keep})
    monkeypatch.setattr(uni.shutil, "which", lambda name: "rclone")
    monkeypatch.setattr(subprocess, "run", run)


@pytest.fixture
def rclone_backup(tmp_path):
    dest = tmp_path / "9999"
    dest.mkdir()
    (dest / "manifest.json").write_text("{}", encoding="utf-8")
    return dest


def test_the_rclone_path_purges_its_old_folders_for_good(monkeypatch, rclone_backup):
    run = _Rclone("100/\n200/\n300/\n9999/\nnotes/\n")
    _rclone(monkeypatch, run)
    result = uni._offsite_sync(rclone_backup)
    assert result["ok"] is True and result["pruned"] == ["100/", "200/"]
    assert run.purged() == ["r2:kazma-backups/100", "r2:kazma-backups/200"]
    for argv in (a for a in run.calls if a[1] == "purge"):
        assert "--drive-use-trash=false" in argv, "Drive's trash would keep the space"
        assert argv.index("--") == len(argv) - 2
    lsf = next(a for a in run.calls if a[1] == "lsf")
    assert lsf == ["rclone", "lsf", "--dirs-only", "--", "r2:kazma-backups"]


def test_a_remote_ending_in_a_slash_gives_no_double_slash(monkeypatch, rclone_backup):
    run = _Rclone("100/\n9999/\n")
    _rclone(monkeypatch, run, remote="r2:kazma-backups/", keep=1)
    uni._offsite_sync(rclone_backup)
    assert run.purged() == ["r2:kazma-backups/100"]


def test_a_folder_already_gone_counts_as_deleted(monkeypatch, rclone_backup):
    run = _Rclone("100/\n9999/\n", purge_rc=3, purge_stderr="ERROR : directory not found")
    _rclone(monkeypatch, run, keep=1)
    result = uni._offsite_sync(rclone_backup)
    assert result["pruned"] == ["100/"] and "prune_error" not in result


def test_a_refused_purge_is_said(monkeypatch, rclone_backup):
    run = _Rclone("100/\n9999/\n", purge_rc=1, purge_stderr="AccessDenied")
    _rclone(monkeypatch, run, keep=1)
    result = uni._offsite_sync(rclone_backup)
    assert result["ok"] is True
    assert "100/: rclone purge: AccessDenied" in result["prune_error"]


def test_a_failed_listing_purges_nothing(monkeypatch, rclone_backup):
    class _NoList(_Rclone):
        def __call__(self, argv, **kwargs):
            if list(argv)[1] == "lsf":
                self.calls.append(list(argv))
                return subprocess.CompletedProcess(argv, 1, "", "couldn't list")
            return super().__call__(argv, **kwargs)

    run = _NoList("")
    _rclone(monkeypatch, run, keep=1)
    result = uni._offsite_sync(rclone_backup)
    assert result["ok"] is True and "couldn't list" in result["prune_error"]
    assert run.purged() == []


def test_rclone_never_purges_a_folder_kazma_did_not_make(monkeypatch):
    calls: list[Any] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))
    for name in ("notes/", "100", "100.zip", "../100/", "100/x/"):
        out = asyncio.run(uni._rclone_delete_copy("r2:kazma-backups", name))
        assert out["ok"] is False and "not a Kazma backup copy" in out["error"]
    assert calls == []


# ── the setting ───────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    (None, 7), (True, 7), (False, 7), ("x", 7), ("3.7", 7), ("", 7),
    (-1, 0), (0, 0), (" 5 ", 5), (12, 12), ("3", 3),
])
def test_the_keep_setting_reads_as_a_count(value, expected):
    assert uni.offsite_keep(value) == expected


def test_the_offsite_config_reads_keep(monkeypatch):
    values = {"backups.offsite.provider": "s3", "backups.offsite.keep": 3}
    store = types.SimpleNamespace(get=lambda key, default=None: values.get(key, default))
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    cfg = uni._offsite_config()
    assert cfg["keep"] == 3 and cfg["enabled"] is True


def test_the_offsite_config_defaults_keep_when_the_store_fails(monkeypatch):
    def boom():
        raise RuntimeError("database away")

    monkeypatch.setattr("kazma_core.config_store.get_config_store", boom)
    assert uni._offsite_config()["keep"] == uni.DEFAULT_OFFSITE_KEEP


def test_the_setting_is_validated_on_every_write():
    from kazma_core.settings_validation import SettingRejected, validate_setting

    for bad in (-1, "x", 2.5):
        with pytest.raises(SettingRejected):
            validate_setting(uni.OFFSITE_KEEP_KEY, bad)
    for good in (0, 7, 30):
        assert validate_setting(uni.OFFSITE_KEEP_KEY, good)[0] == good


# ── every provider can list and delete, and refuses a path ────────────


def _providers_missing_retention(module) -> list[str]:
    """Each class that uploads a copy, without an async list and delete."""
    missing = []
    for name, cls in vars(module).items():
        if not inspect.isclass(cls) or cls.__module__ != module.__name__:
            continue
        if not inspect.iscoroutinefunction(getattr(cls, "upload_file", None)):
            continue
        if getattr(cls, "_is_protocol", False):
            continue
        for method in ("list_backups", "delete_backup"):
            if not inspect.iscoroutinefunction(getattr(cls, method, None)):
                missing.append(f"{name}.{method}")
    return missing


def test_every_provider_can_list_and_delete_its_copies():
    assert _providers_missing_retention(cs) == []


def test_negative_control_a_provider_without_them_is_found():
    planted = types.ModuleType(cs.__name__)

    class _Planted:
        async def upload_file(self, local, remote_name):
            return {"ok": True}

    _Planted.__module__ = cs.__name__
    planted._Planted = _Planted
    assert _providers_missing_retention(planted) == ["_Planted.list_backups", "_Planted.delete_backup"]


def _upload_classes() -> list[type]:
    return [cls for cls in vars(cs).values()
            if inspect.isclass(cls) and cls.__module__ == cs.__name__
            and inspect.iscoroutinefunction(getattr(cls, "upload_file", None))
            and not getattr(cls, "_is_protocol", False)]


def test_there_are_five_providers():
    assert {c.__name__ for c in _upload_classes()} == {
        "GoogleDriveSync", "OneDriveSync", "WebDAVSync", "FTPSync", "S3Sync"}


@pytest.mark.parametrize("bad", ["", ".", "..", "a/b", "a\\b", "../1000.zip", "/1000.zip"])
def test_every_provider_refuses_a_name_that_is_not_a_file_before_any_io(monkeypatch, bad):
    def no_io(*a, **k):
        raise AssertionError("I/O before the name was checked")

    import ftplib  # nosec B402 - patched to refuse; nothing connects

    monkeypatch.setattr(cs.httpx, "AsyncClient", no_io)
    monkeypatch.setattr(ftplib, "FTP", no_io)
    monkeypatch.setattr(ftplib, "FTP_TLS", no_io)
    monkeypatch.setattr(cs, "_read_config", no_io)
    monkeypatch.setattr(cs, "_read_vault", no_io)
    for cls in _upload_classes():
        out = asyncio.run(cls().delete_backup(bad))
        assert out == {"ok": False, "error": f"not a backup name: {bad!r}"}, cls.__name__
