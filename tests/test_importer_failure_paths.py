"""A migration import that stops leaves the target as it was
(audit 2026-10-26 AUD-003, AUD-004).

AUD-003: the pre-swap backup's result was ignored, and the rollback took "no
backup file" for "the file did not exist before" and deleted it -- a failed
backup followed by a failed install lost the live database both ways.

AUD-004: the bundle's vault key was written into ``.env`` and the target's
vault.db moved aside BEFORE the preflight checks that can still stop the
import. An import that stopped there ("Live SQLite files were not modified")
left every secret on the target unreadable.
"""

from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from kazma_core.migration import importer
from kazma_core.migration.bundle import BUNDLE_VERSION, Manifest
from kazma_core.migration.importer import import_bundle
from kazma_core.migration.vault_pairing import sync_vault_key


def _sqlite_bytes(tmp: Path, name: str, marker: str) -> bytes:
    path = tmp / f"src-{name}"
    _write_sqlite(path, marker)
    return path.read_bytes()


def _write_sqlite(path: Path, marker: str) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES (?)", (marker,))
        conn.commit()
    finally:
        conn.close()


def _marker(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("SELECT v FROM t").fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else ""


def _bundle(path: Path, files: dict[str, bytes | str]) -> Path:
    hashes = {
        name: hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
        for name, data in files.items()
    }
    manifest = Manifest(
        bundle_version=BUNDLE_VERSION, source_workspace_root="",
        source_data_dir="", file_hashes=hashes,
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest.to_json())
        for name, content in files.items():
            zf.writestr(name, content)
    return path


@pytest.fixture
def target(tmp_path: Path, monkeypatch):
    data = tmp_path / "kazma-data"
    data.mkdir()
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: data)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KAZMA_VAULT_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("OTHER=1\n", encoding="utf-8")
    return data, env


# ── AUD-003 ────────────────────────────────────────────────────────────────


def test_a_failed_pre_swap_backup_stops_the_import(tmp_path: Path, target) -> None:
    data, _env = target
    live = data / "cron.db"
    _write_sqlite(live, "ORIGINAL-LIVE")
    bundle = _bundle(tmp_path / "b.zip", {
        "meta.env": "KAZMA_VAULT_KEY=\n", "config.yaml": "{}\n", "pathmap.json": "{}\n",
        "data/cron.db": _sqlite_bytes(tmp_path, "cron.db", "FROM-BUNDLE"),
    })

    from kazma_core.memory import backup as mem_backup

    real = mem_backup._backup_one

    def failing_live_copy(src: Path, dest: Path) -> bool:
        if Path(src) == live:  # the pre-swap copy of the live file
            return False
        return real(src, dest)

    with patch.object(mem_backup, "_backup_one", failing_live_copy):
        report = import_bundle(bundle, target_workspace_root=str(tmp_path))

    assert report.ok is False
    assert any("could not back up the live cron.db" in e for e in report.errors)
    assert _marker(live) == "ORIGINAL-LIVE"
    assert report.files_restored == []


def test_rollback_keeps_a_file_that_existed_but_has_no_copy(tmp_path: Path) -> None:
    from kazma_core import paths

    data = tmp_path / "d"
    data.mkdir()
    live = data / "cron.db"
    _write_sqlite(live, "IMPORTED")
    empty_backup = tmp_path / "bak"
    empty_backup.mkdir()
    with patch("kazma_core.paths.data_dir", lambda: data):
        importer._rollback_sqlite_swap(empty_backup, ["cron.db"], paths, data, {"cron.db"})
    assert live.exists()


def test_the_old_rollback_rule_deletes_it(tmp_path: Path) -> None:
    """Negative control: with no record of what existed (the old rule), a
    missing copy reads as "did not exist" and the live file is deleted."""
    from kazma_core import paths

    data = tmp_path / "d"
    data.mkdir()
    live = data / "cron.db"
    _write_sqlite(live, "IMPORTED")
    empty_backup = tmp_path / "bak"
    empty_backup.mkdir()
    with patch("kazma_core.paths.data_dir", lambda: data):
        importer._rollback_sqlite_swap(empty_backup, ["cron.db"], paths, data, set())
    assert not live.exists()


# ── AUD-004 ────────────────────────────────────────────────────────────────


def _vault_bundle(tmp_path: Path, *, with_pg_dump: bool) -> Path:
    files: dict[str, bytes | str] = {
        "meta.env": "KAZMA_VAULT_KEY=bundle-key\n",
        "config.yaml": "{}\n",
        "pathmap.json": "{}\n",
        "data/vault.db": _sqlite_bytes(tmp_path, "vault.db", "BUNDLE-VAULT"),
    }
    if with_pg_dump:
        files["data/postgres.dump"] = b"PGDMP-not-real"
    return _bundle(tmp_path / "v.zip", files)


def test_a_stopped_import_leaves_the_key_and_the_vault(tmp_path: Path, target) -> None:
    data, env = target
    vault = data / "vault.db"
    _write_sqlite(vault, "TARGET-VAULT")
    bundle = _vault_bundle(tmp_path, with_pg_dump=True)

    with patch("kazma_core.db.backend.is_postgres", return_value=False):
        report = import_bundle(bundle, target_workspace_root=str(tmp_path))

    assert report.ok is False
    assert any("Postgres dump" in e for e in report.errors)
    assert env.read_text(encoding="utf-8") == "OTHER=1\n"
    assert _marker(vault) == "TARGET-VAULT"
    assert not list(data.glob(".migrate-vault-backup-*"))
    import os
    assert "KAZMA_VAULT_KEY" not in os.environ


def test_the_old_step_five_changed_both_before_the_check(tmp_path: Path, target) -> None:
    """Negative control: what the old step 5 did, at that point, on this target."""
    data, env = target
    vault = data / "vault.db"
    _write_sqlite(vault, "TARGET-VAULT")
    sync_vault_key(bundle_vault_key="bundle-key", env_path=env, target_data_dir=data)
    assert "KAZMA_VAULT_KEY=bundle-key" in env.read_text(encoding="utf-8")
    assert not vault.exists()


def test_a_finished_import_writes_the_key_and_keeps_the_bundle_vault(
    tmp_path: Path, target
) -> None:
    data, env = target
    vault = data / "vault.db"
    _write_sqlite(vault, "TARGET-VAULT")
    bundle = _vault_bundle(tmp_path, with_pg_dump=False)

    report = import_bundle(bundle, target_workspace_root=str(tmp_path))

    assert "KAZMA_VAULT_KEY=bundle-key" in env.read_text(encoding="utf-8")
    assert _marker(vault) == "BUNDLE-VAULT", report.errors
    assert not list(data.glob(".migrate-vault-backup-*"))


def test_a_rollback_after_the_swap_puts_the_key_back(tmp_path: Path, target) -> None:
    data, env = target
    vault = data / "vault.db"
    _write_sqlite(vault, "TARGET-VAULT")
    bundle = _vault_bundle(tmp_path, with_pg_dump=False)

    with patch.object(importer, "_guard_restored_x", return_value=False):
        report = import_bundle(bundle, target_workspace_root=str(tmp_path))

    assert report.ok is False
    assert env.read_text(encoding="utf-8") == "OTHER=1\n"
    assert _marker(vault) == "TARGET-VAULT"
