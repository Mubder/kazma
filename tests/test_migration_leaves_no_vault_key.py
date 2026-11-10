"""A migration never leaves the vault key in clear in the data dir.

Audit 2026-11-10: an import extracted the whole bundle, ``meta.env`` (the
vault key in clear) included, into ``<data dir>/.migrate-staging-<ts>/`` and
kept that folder when it stopped; an export wrote ``meta.env`` into
``.migrate-export-<ts>/`` and removed the folder only on success. The
6-hourly universal backup then copied the key into the plain offsite copy
(``tests/test_offsite_copy_leaves_env_out.py`` holds the backup half).

Now the importer reads ``meta.env`` from the bundle and never extracts it,
the exporter removes it whatever happens, and both clean leftovers of an
earlier run. Each test has its negative control.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
import zipfile
from pathlib import Path

import pytest

from kazma_core.migration import importer
from kazma_core.migration.bundle import (
    BUNDLE_VERSION,
    KazmaBundle,
    Manifest,
    sweep_working_folders,
)

META = "KAZMA_VAULT_KEY=\nKAZMA_PUBLIC_URL=https://planted.example\n"


def _sqlite_bytes(tmp: Path) -> bytes:
    path = tmp / "src-cron.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def _bundle(path: Path) -> Path:
    files: dict[str, str | bytes] = {
        "meta.env": META, "config.yaml": "{}\n", "pathmap.json": "{}\n",
        "data/cron.db": _sqlite_bytes(path.parent),
    }
    hashes = {name: hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()
              for name, data in files.items()}
    manifest = Manifest(bundle_version=BUNDLE_VERSION, source_workspace_root="",
                        source_data_dir="", file_hashes=hashes)
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
    return data


def _import_that_stops_after_staging(tmp_path: Path, monkeypatch) -> list[Path]:
    def stop(*args, **kwargs):
        raise RuntimeError("stopped after staging")

    monkeypatch.setattr(importer, "_apply_path_rewrite", stop)
    monkeypatch.setattr(importer, "_live_server_detected", lambda: (False, ""))
    with pytest.raises(RuntimeError, match="stopped after staging"):
        report = importer.import_bundle(_bundle(tmp_path / "b.zip"),
                                        target_workspace_root=str(tmp_path))
        pytest.fail(f"the import did not reach staging: {report.errors}")
    return [p for p in (tmp_path / "kazma-data").glob(".migrate-staging-*") if p.is_dir()]


def test_an_import_that_stops_leaves_no_meta_env(tmp_path, target, monkeypatch):
    kept = _import_that_stops_after_staging(tmp_path, monkeypatch)
    assert kept, "the staging folder is kept on failure, as before"
    assert not any((p / "meta.env").exists() for p in kept)
    assert all((p / "config.yaml").is_file() for p in kept)  # the rest is extracted


def test_negative_control_the_old_extraction_leaves_the_key(tmp_path, target, monkeypatch):
    real = KazmaBundle.extract_all
    monkeypatch.setattr(KazmaBundle, "extract_all",
                        lambda self, dest, exclude=(): real(self, dest))
    kept = _import_that_stops_after_staging(tmp_path, monkeypatch)
    assert any((p / "meta.env").read_text(encoding="utf-8") == META for p in kept)


def test_the_sweep_strips_meta_env_and_removes_old_folders(tmp_path):
    now = time.time()
    old = tmp_path / f".migrate-staging-{int(now) - 2 * 86400}"
    young = tmp_path / f".migrate-staging-{int(now) - 60}"
    for folder in (old, young):
        folder.mkdir()
        (folder / "meta.env").write_text(META, encoding="utf-8")
        (folder / "kept.txt").write_text("x", encoding="utf-8")
    other = tmp_path / ".migrate-backup-1"  # pre-import backups are not touched
    other.mkdir()
    (other / "meta.env").write_text(META, encoding="utf-8")

    removed = sweep_working_folders(tmp_path, ".migrate-staging-", now=now)

    assert removed == [old.name] and not old.exists()
    assert (young / "kept.txt").is_file() and not (young / "meta.env").exists()
    assert (other / "meta.env").is_file()


def _run(code: str, data_dir: Path, cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("KAZMA_")}
    env.update({"KAZMA_DB_BACKEND": "sqlite", "KAZMA_VAULT_KEY": "test-only-not-a-secret",
                "KAZMA_DATA_DIR": str(data_dir)})
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=180)


_FAILED_EXPORT = """
    import json, pathlib, sqlite3
    from kazma_core.paths import data_dir
    from kazma_core.migration import exporter
    c = sqlite3.connect(str(data_dir() / "agent_artifacts.db")); c.execute("CREATE TABLE t (v)"); c.commit(); c.close()
    {patch}
    def broken(*a, **k):
        raise RuntimeError("disk full")
    exporter.atomic_write_text = broken  # the manifest write, after meta.env
    try:
        exporter.export_bundle({bundle!r}, include_assets=False)
    except RuntimeError:
        pass
    left = [str(p.relative_to(data_dir())) for p in data_dir().glob(".migrate-export-*/**/*")]
    print(json.dumps(left))
"""


def _left_after_failed_export(tmp_path: Path, *, old: bool = False) -> list[str]:
    data_dir = tmp_path / "install" / "kazma-data"
    data_dir.mkdir(parents=True)
    # The old exporter had no removal: a no-op unlink reproduces it.
    patch = "pathlib.Path.unlink = lambda self, missing_ok=False: None" if old else ""
    proc = _run(_FAILED_EXPORT.format(bundle=str(tmp_path / "b.zip"), patch=patch),
                data_dir, tmp_path)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return [p.replace("\\", "/") for p in json.loads(proc.stdout.strip().splitlines()[-1])]


def test_an_export_that_fails_leaves_no_meta_env(tmp_path):
    left = _left_after_failed_export(tmp_path)
    assert any(p.endswith("config.yaml") for p in left), left  # the folder is there
    assert not any(p.endswith("meta.env") for p in left), left


def test_negative_control_without_the_removal_the_key_stays(tmp_path):
    left = _left_after_failed_export(tmp_path, old=True)
    assert any(p.endswith("meta.env") for p in left), left
