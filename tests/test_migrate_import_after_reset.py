"""What restoring the old Kazma after the 2026-11-19 PC reset taught the import.

The owner imported the old install's bundle into a fresh one, and three
things went wrong, each fixed here with its witness inverted:

* After ``--reset-vault-key`` wrote the bundle's key, the end of the import
  still said "vault: mismatch ... Re-run with --reset-vault-key": the status
  printed was the pairing read before the swap. It is now what is true after
  the import (``replaced``, or ``set`` for an install with no key).
* The bundle's document list is a SQLite file (the old install kept it
  there), and the fresh install's list followed Postgres: the import said one
  line, "documents.db not installed", and 29 documents came back as contents
  with no list. The import now keeps the list in SQLite, as the source did
  (``KAZMA_DOCUMENTS_METADATA_BACKEND=sqlite`` written into ``.env``), or,
  where the install names Postgres for it, stops before anything changes.
* With Kazma held stopped by its guard, the import still refused for two
  minutes over the heartbeat the server had stamped before it stopped:
  ``server_stopped`` (``kazma migrate import``'s hold) is no live server.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest
from kazma_core.migration import importer
from kazma_core.migration.bundle import BUNDLE_VERSION, Manifest, vault_key_fingerprint
from kazma_core.migration.vault_pairing import VaultKeyStatus, check_vault_key

BUNDLE_KEY = "bundle-test-key-not-a-secret"
TARGET_KEY = "target-test-key-not-a-secret"


def _sqlite_bytes(tmp: Path, name: str) -> bytes:
    path = tmp / f"src-{name}"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def _bundle(path: Path, *, documents: bool = False) -> Path:
    files: dict[str, str | bytes] = {
        "meta.env": f"KAZMA_VAULT_KEY={BUNDLE_KEY}\n",
        "config.yaml": "{}\n",
        "pathmap.json": "{}\n",
        "data/vault.db": _sqlite_bytes(path.parent, "vault.db"),
    }
    if documents:
        files["data/documents.db"] = _sqlite_bytes(path.parent, "documents.db")
    hashes = {name: hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
              for name, data in files.items()}
    manifest = Manifest(bundle_version=BUNDLE_VERSION, source_workspace_root="", source_data_dir="",
                        vault_key_fingerprint=vault_key_fingerprint(BUNDLE_KEY), file_hashes=hashes)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest.to_json())
        for name, content in files.items():
            zf.writestr(name, content)
    return path


@pytest.fixture
def target(tmp_path: Path, monkeypatch):
    """A SQLite install in tmp_path, with its own vault key in .env."""
    data = tmp_path / "kazma-data"
    data.mkdir()
    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: data)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(f"KAZMA_VAULT_KEY={TARGET_KEY}\n", encoding="utf-8")
    monkeypatch.setenv("KAZMA_VAULT_KEY", TARGET_KEY)
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    monkeypatch.delenv("KAZMA_DATABASE_URL", raising=False)
    monkeypatch.delenv("KAZMA_DOCUMENTS_METADATA_BACKEND", raising=False)
    from kazma_core.config_store import reset_config_store

    reset_config_store()
    monkeypatch.setattr(importer, "_live_server_detected", lambda: (False, ""))
    return data


def _env(tmp_path: Path) -> dict[str, str]:
    out = {}
    for line in (tmp_path / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


# ── the vault status said at the end ─────────────────────────────────────────


def test_a_reset_key_is_reported_as_replaced(tmp_path, target):
    bundle = _bundle(tmp_path / "b.zip")
    report = importer.import_bundle(bundle, target_workspace_root=str(tmp_path), reset_vault_key=True)
    assert report.ok, report.errors
    assert _env(tmp_path)["KAZMA_VAULT_KEY"] == BUNDLE_KEY
    assert report.vault_status == "replaced"
    assert "mismatch" not in report.vault_message and "Re-run" not in report.vault_message


def test_control_the_pairing_read_before_the_swap_says_mismatch(tmp_path, target):
    """The status the import printed before: the pairing of the target's key
    and the bundle's, read before anything changed."""
    pairing = check_vault_key(bundle_vault_key=BUNDLE_KEY, bundle_has_vault_db=True,
                              bundle_fingerprint=vault_key_fingerprint(BUNDLE_KEY))
    assert pairing.status == VaultKeyStatus.MISMATCH and "--reset-vault-key" in pairing.message


def test_an_install_with_no_key_is_reported_as_set(tmp_path, target, monkeypatch):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.delenv("KAZMA_VAULT_KEY", raising=False)
    report = importer.import_bundle(_bundle(tmp_path / "b.zip"), target_workspace_root=str(tmp_path))
    assert report.ok, report.errors
    assert report.vault_status == "set" and _env(tmp_path)["KAZMA_VAULT_KEY"] == BUNDLE_KEY


# ── the document list follows the bundle ────────────────────────────────────


def _restore_recorder(seen: list):
    def restore(staging, staged_data, report, _log):
        from kazma_core.documents.jobs_pg import document_metadata_backend

        seen.append(((staged_data / "documents.db").exists(), document_metadata_backend()))
    return restore


@pytest.fixture
def postgres_list(monkeypatch):
    """This install's document list follows Postgres unless .env says sqlite."""
    def backends():
        named = (os.environ.get("KAZMA_DOCUMENTS_METADATA_BACKEND") or "").strip().lower()
        return ("sqlite" if named == "sqlite" else "postgres"), "postgres"

    monkeypatch.setattr("kazma_core.documents.backup.installed_document_backends", backends)
    monkeypatch.setattr("kazma_core.documents.jobs_pg.document_jobs_backend", lambda: "postgres")


def test_a_sqlite_list_is_kept_in_sqlite(tmp_path, target, postgres_list, monkeypatch):
    seen: list = []
    monkeypatch.setattr(importer, "_restore_document_store", _restore_recorder(seen))
    report = importer.import_bundle(_bundle(tmp_path / "b.zip", documents=True),
                                    target_workspace_root=str(tmp_path), reset_vault_key=True)
    assert report.ok, report.errors
    # The restore saw the bundle's list and an install that keeps it in SQLite.
    assert seen == [(True, "sqlite")]
    assert _env(tmp_path)["KAZMA_DOCUMENTS_METADATA_BACKEND"] == "sqlite"
    assert any("KAZMA_DOCUMENTS_METADATA_BACKEND=sqlite" in w for w in report.warnings)


def test_control_without_the_plan_the_restore_sees_postgres(tmp_path, target, postgres_list, monkeypatch):
    """The import as it ran on 2026-11-19: the restore met an install whose
    list follows Postgres, and ``_restore_document_store`` installs a
    documents.db only for a SQLite list ("documents.db not installed")."""
    seen: list = []
    monkeypatch.setattr(importer, "_restore_document_store", _restore_recorder(seen))
    monkeypatch.setattr(importer, "_document_list_plan", lambda bundle, data_dir: (False, ""))
    report = importer.import_bundle(_bundle(tmp_path / "b.zip", documents=True),
                                    target_workspace_root=str(tmp_path), reset_vault_key=True)
    assert report.ok, report.errors
    assert seen == [(True, "postgres")]
    assert "KAZMA_DOCUMENTS_METADATA_BACKEND" not in _env(tmp_path)


def test_a_list_named_postgres_stops_the_import_before_anything_changes(tmp_path, target, postgres_list,
                                                                        monkeypatch):
    monkeypatch.setenv("KAZMA_DOCUMENTS_METADATA_BACKEND", "postgres")
    before = (tmp_path / ".env").read_text(encoding="utf-8")
    for dry_run in (True, False):
        report = importer.import_bundle(_bundle(tmp_path / "b.zip", documents=True),
                                        target_workspace_root=str(tmp_path), reset_vault_key=True,
                                        dry_run=dry_run)
        assert not report.ok and any("names Postgres for its list" in e for e in report.errors)
        assert "Nothing was changed" in report.errors[-1]
    assert (tmp_path / ".env").read_text(encoding="utf-8") == before
    assert not list(target.glob(".migrate-*"))


def test_a_dry_run_says_where_the_list_goes(tmp_path, target, postgres_list):
    said: list[str] = []
    report = importer.import_bundle(_bundle(tmp_path / "b.zip", documents=True),
                                    target_workspace_root=str(tmp_path), reset_vault_key=True,
                                    dry_run=True, progress=said.append)
    assert report.ok, report.errors
    assert any("KAZMA_DOCUMENTS_METADATA_BACKEND=sqlite" in line for line in said), said
    assert "KAZMA_DOCUMENTS_METADATA_BACKEND" not in _env(tmp_path)


def test_a_bundle_with_no_sqlite_list_changes_nothing_there(tmp_path, target, postgres_list):
    report = importer.import_bundle(_bundle(tmp_path / "b.zip"), target_workspace_root=str(tmp_path),
                                    reset_vault_key=True)
    assert report.ok, report.errors
    assert "KAZMA_DOCUMENTS_METADATA_BACKEND" not in _env(tmp_path)


# ── a held server is not a live one ─────────────────────────────────────────


def test_a_server_held_by_the_guard_is_not_refused(tmp_path, target, monkeypatch):
    monkeypatch.setattr(importer, "_live_server_detected",
                        lambda: (True, "fresh heartbeat (system.heartbeat.epoch <120s old)"))
    bundle = _bundle(tmp_path / "b.zip")
    refused = importer.import_bundle(bundle, target_workspace_root=str(tmp_path), reset_vault_key=True)
    assert any("live Kazma server was detected" in e for e in refused.errors)
    held = importer.import_bundle(bundle, target_workspace_root=str(tmp_path), reset_vault_key=True,
                                  server_stopped=True)
    assert held.ok, held.errors
