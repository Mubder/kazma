"""Migration import must not mutate live SQLite before PG validation (audit C-2)."""

from __future__ import annotations

import hashlib
import sqlite3
import zipfile
from pathlib import Path
from unittest.mock import patch

from kazma_core.migration.bundle import BUNDLE_VERSION, KazmaBundle, Manifest
from kazma_core.migration.importer import import_bundle


def _write_pg_bundle(path: Path, *, dump_bytes: bytes = b"not-a-real-pg-dump") -> Path:
    files: dict[str, bytes | str] = {
        "meta.env": "KAZMA_VAULT_KEY=\n",
        "config.yaml": "agent:\n  name: x\n",
        "pathmap.json": "{}\n",
        "data/cron.db": b"LIVE-SHOULD-NOT-SEE-THIS",
        "data/postgres.dump": dump_bytes,
    }
    hashes = {
        name: hashlib.sha256(
            data.encode() if isinstance(data, str) else data
        ).hexdigest()
        for name, data in files.items()
    }
    manifest = Manifest(
        bundle_version=BUNDLE_VERSION,
        source_workspace_root="",
        source_data_dir="",
        file_hashes=hashes,
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest.to_json())
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def _write_live_sqlite(path: Path, marker: str) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (marker,))
    conn.commit()
    conn.close()


def _live_marker(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    row = conn.execute("SELECT v FROM t").fetchone()
    conn.close()
    return str(row[0]) if row else ""


def test_sqlite_target_aborts_before_swap(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "kazma-data"
    data.mkdir()
    live = data / "cron.db"
    _write_live_sqlite(live, "ORIGINAL-LIVE")
    bundle = _write_pg_bundle(tmp_path / "b.zip")

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: data)

    with patch("kazma_core.db.backend.is_postgres", return_value=False):
        report = import_bundle(
            bundle,
            target_workspace_root=str(tmp_path),
        )

    assert report.ok is False
    assert any("Postgres dump" in e and "SQLite" in e for e in report.errors)
    assert _live_marker(live) == "ORIGINAL-LIVE"
    assert report.files_restored == []


def test_corrupt_pg_dump_leaves_live_sqlite(tmp_path: Path, monkeypatch) -> None:
    data = tmp_path / "kazma-data"
    data.mkdir()
    live = data / "cron.db"
    _write_live_sqlite(live, "ORIGINAL-LIVE")
    bundle = _write_pg_bundle(tmp_path / "b.zip", dump_bytes=b"CORRUPT")

    monkeypatch.setattr("kazma_core.paths.data_dir", lambda: data)

    def _boom(*_a, **_k):
        raise RuntimeError("pg_restore exploded")

    with (
        patch("kazma_core.db.backend.is_postgres", return_value=True),
        patch("kazma_core.db.backend.get_database_url", return_value="postgresql://x"),
        patch("kazma_core.migration.pg_bridge.restore_database", side_effect=_boom),
    ):
        report = import_bundle(bundle, target_workspace_root=str(tmp_path))

    assert report.ok is False
    assert any("pg_restore failed" in e for e in report.errors)
    assert _live_marker(live) == "ORIGINAL-LIVE"
    assert report.files_restored == []
