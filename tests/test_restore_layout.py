"""A restored backup is laid out where Kazma reads its stores (FA-268, 267, 270).

The 2026-11 functional audit restored the live install's newest restic
generation the documented way. ``restore_files`` reported every step ok, and
the restored ``kazma-data`` held two folders, ``dbs/`` and ``assets/``: Kazma's
own path functions, pinned to that data dir, found 0 of its 15 stores, all
one level down. A Kazma started on it would have booted with an empty vault
and no memory. Its own report was all the restore was ever checked by; the
test staged a ``dbs/`` folder and checked that the restore ran.

Here the backup is made by the real universal backup from a real data dir,
handed to ``restore_files`` as restic would (under the source's absolute
path), and the result is judged by Kazma's path functions in a child pinned
to the restored data dir. The old assemble is the negative control.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kazma_core.backup import restore as restore_mod
from kazma_core.backup import universal

_STORES = ("settings.db", "vault.db", "memory_state.db", "memory_ops.db", "checkpoints.db")


def _db(path: Path, rows: int = 3, *, keep_open: bool = False) -> sqlite3.Connection | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [(f"row {i}",) for i in range(rows)])
    conn.commit()
    if keep_open:
        return conn  # a live writer: its -wal is beside the file
    conn.close()
    return None


@pytest.fixture
def backup(tmp_path, monkeypatch):
    """A real universal backup of a data dir with stores, assets and a nested live store."""
    data = tmp_path / "src" / "kazma-data"
    data.mkdir(parents=True)
    # The install root: the backup copies its .env and kazma.yaml.
    (data.parent / ".env").write_text("KAZMA_VAULT_KEY=demo-not-a-key", encoding="utf-8")
    (data.parent / "kazma.yaml").write_text("agent: {}", encoding="utf-8")
    for name in _STORES:
        _db(data / name)
    (data / "attachments").mkdir()
    (data / "attachments" / "photo.png").write_bytes(b"not really a png")
    nested = _db(data / "document-store" / "catalog.db", rows=5, keep_open=True)
    (data / "document-store" / "originals").mkdir()
    (data / "document-store" / "originals" / "a.docx").write_bytes(b"PK docx")
    base = data / "backups" / "universal"
    base.mkdir(parents=True)
    monkeypatch.setattr(universal, "_data_dir", lambda: data)
    monkeypatch.setattr(universal, "_universal_dir", lambda: base)
    monkeypatch.setattr(universal, "_backup_progress", {"phase": "idle"})
    monkeypatch.setattr(universal, "_offsite_sync", lambda dest: {"skipped": "test"})
    monkeypatch.setattr(universal, "_snapshot_to_restic", lambda dest: None)
    monkeypatch.setattr(universal, "_pg_dump_state", lambda: {})
    monkeypatch.setattr(universal, "_rebuilt_dirs", lambda: set())
    import kazma_core.backup.neo4j_backup as neo

    monkeypatch.setattr(neo, "export_graph", lambda dest: type("R", (), {"as_dict": lambda self: {}})())
    monkeypatch.setattr(universal, "_alert_on_backup_gaps", lambda *a, **k: None)
    universal.perform_universal_backup(trigger="test")
    if nested is not None:
        nested.close()
    return next(p for p in base.iterdir() if (p / "manifest.json").is_file())


def _restore(tmp_path, monkeypatch, generation: Path) -> tuple[restore_mod.RestoreResult, Path]:
    """``restore_files`` with restic replaced by what restic does: the
    generation, under its source's absolute path, inside the staging dir."""
    points = [restore_mod.RestorePoint(generation=int(generation.name), snapshot_id="abc12345",
                                       taken_at="", repo="repo")]

    def restic_restore(repo, password, snapshot, target):
        inner = Path(target) / Path(*generation.resolve().parts[1:])
        shutil.copytree(generation, inner)
        return True, ""

    monkeypatch.setattr(restore_mod, "list_restore_points", lambda repo, pw: points)
    monkeypatch.setattr(restore_mod, "_restic_restore", restic_restore)
    target = tmp_path / "restored"
    return restore_mod.restore_files("repo", "pw", target), target


_ASK = "\n".join([
    "import json",
    "from kazma_core import paths",
    "names = ['settings_db', 'vault_db_path', 'primary_memory_db', 'memory_ops_db', 'checkpoints_db']",
    "print(json.dumps([getattr(paths, n)() for n in names]))",
    "",
])


def _kazma_finds(data_dir: Path) -> list[str]:
    """The stores a Kazma pointed at *data_dir* finds, asked in a child."""
    from kazma_core.security.child_env import tool_child_env

    env = tool_child_env()
    env["KAZMA_DATA_DIR"] = str(data_dir)
    out = subprocess.run([sys.executable, "-c", _ASK], env=env, capture_output=True, text=True,
                         check=True, timeout=120).stdout
    return [Path(p).name for p in json.loads(out.strip().splitlines()[-1]) if Path(p).is_file()]


def test_kazma_finds_every_store_of_a_restored_backup(tmp_path, monkeypatch, backup):
    res, target = _restore(tmp_path, monkeypatch, backup)
    steps = {s["step"]: s for s in res.steps}
    assert steps["layout readable by Kazma"]["ok"], steps["layout readable by Kazma"]
    assert steps["databases readable"]["ok"], steps["databases readable"]
    data = target / "kazma-data"
    assert sorted(_kazma_finds(data)) == sorted(_STORES)
    assert (data / "attachments" / "photo.png").is_file()
    assert (data / "document-store" / "originals" / "a.docx").is_file()
    assert not (data / "dbs").exists() and not (data / "assets").exists()


def test_a_nested_store_is_the_consistent_copy_with_no_sidecar(tmp_path, monkeypatch, backup):
    """The live writer's -wal sat beside the nested store; the raw copy in
    ``assets/`` must not reach the restore (FA-267), the ``dbs/`` copy does."""
    _res, target = _restore(tmp_path, monkeypatch, backup)
    catalog = target / "kazma-data" / "document-store" / "catalog.db"
    assert catalog.is_file()
    for suffix in ("-wal", "-shm", "-journal"):
        assert not Path(str(catalog) + suffix).exists()
    conn = sqlite3.connect(f"{catalog.resolve().as_uri()}?immutable=1", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 5
    finally:
        conn.close()


def test_the_check_adds_no_file_beside_a_database(tmp_path, monkeypatch, backup):
    """``mode=ro`` on a WAL database made its -wal/-shm (FA-270)."""
    _res, target = _restore(tmp_path, monkeypatch, backup)
    left = sorted(p.name for p in (target / "kazma-data").rglob("*") if p.name.endswith(("-wal", "-shm")))
    assert left == []


def test_the_old_assemble_left_kazma_finding_nothing(tmp_path, monkeypatch, backup):
    """Negative control: ``dbs/`` and ``assets/`` copied in as folders."""

    def old(root: Path, data_dir: Path) -> list[str]:
        for name in ("dbs", "assets"):
            if (root / name).is_dir():
                shutil.copytree(root / name, data_dir / name, dirs_exist_ok=True)
        return sorted(f.relative_to(data_dir).as_posix() for f in (data_dir / "dbs").rglob("*.db"))

    monkeypatch.setattr(restore_mod, "_flatten_into_data_dir", old)
    res, target = _restore(tmp_path, monkeypatch, backup)
    assert _kazma_finds(target / "kazma-data") == []
    steps = {s["step"]: s for s in res.steps}
    assert not steps["layout readable by Kazma"]["ok"]


def test_the_backup_holds_one_consistent_copy_of_a_nested_store(backup):
    """FA-267: a database nested in an asset folder was copied twice, the
    second time raw with its live -wal. ``dbs/`` holds it; ``assets/`` not."""
    assert (backup / "dbs" / "document-store" / "catalog.db").is_file()
    raw = backup / "assets" / "document-store"
    assert (raw / "originals" / "a.docx").is_file()
    assert sorted(p.name for p in raw.iterdir() if p.name.startswith("catalog.db")) == []


def test_the_old_asset_copy_took_it_raw(tmp_path):
    """Negative control: the copier without the skip set takes the live file."""
    src = tmp_path / "document-store"
    conn = _db(src / "catalog.db", keep_open=True)
    try:
        universal._robust_copytree(src, tmp_path / "out")
        assert (tmp_path / "out" / "catalog.db").is_file()
    finally:
        if conn is not None:
            conn.close()


def test_the_restore_cli_names_the_passphrase_file_it_reads(monkeypatch, capsys, tmp_path):
    """FA-269: it named the legacy ``~/.kazma/restic.pass``."""
    import kazma_core.backup.restic_repo as restic_repo

    where = tmp_path / ".kazma" / "restic.pass"
    monkeypatch.setattr(restic_repo, "ensure_password", lambda: ("", None))
    monkeypatch.setattr(restic_repo, "password_file", lambda: where)
    assert restore_mod.main(["--list"]) == 2
    assert str(where) in capsys.readouterr().out
