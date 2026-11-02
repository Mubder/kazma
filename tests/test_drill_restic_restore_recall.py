"""Drill: a real restic backup, restored into a scratch folder, recalls the memory.

Roadmap theme 2 (2026-11): every recovery the docs promise is drilled with
the real thing, not described. The documented restore (FA-267/268/269/270)
was checked by ``tests/test_restore_layout.py`` with restic stood in for;
this drill runs restic itself. A memory is written the product's way into a
data folder; the real universal backup copies it; the generation goes into a
fresh restic repository; ``restore_files`` brings it back into a scratch
folder from that repository; and a recall pinned to the restored memory
database finds it.

The negative control is the generation taken before the memory existed:
restored the same way, its recall finds nothing -- so the recall reads the
restored files, not something else.

Needs the ``restic`` binary (the drills CI job installs it); skipped without.
"""

from __future__ import annotations

import sqlite3
import time
import pytest

from kazma_core.backup import restic_repo, universal
from kazma_core.backup import restore as restore_mod

pytestmark = pytest.mark.skipif(not restic_repo.restic_available(), reason="the restic binary is not installed")

PASSWORD = "drill-passphrase-not-a-secret"
QUESTION = "what is the drill harbour codeword"
ANSWER = "Saffron lantern, said twice."


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A data folder with an empty memory, and the universal backup pointed at it."""
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    data = tmp_path / "src" / "kazma-data"
    data.mkdir(parents=True)
    (data.parent / ".env").write_text("KAZMA_VAULT_KEY=demo-not-a-key", encoding="utf-8")
    (data.parent / "kazma.yaml").write_text("agent: {}", encoding="utf-8")
    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(data / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(data / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    dual_write._reset_mirror()
    for name, ensure in (("memory_state.db", ensure_primary_schema), ("memory_ops.db", ensure_ops_schema)):
        conn = sqlite3.connect(str(data / name))
        ensure(conn)
        conn.close()
    base = data / "backups" / "universal"
    base.mkdir(parents=True)
    monkeypatch.setattr(universal, "_data_dir", lambda: data)
    monkeypatch.setattr(universal, "_universal_dir", lambda: base)
    monkeypatch.setattr(universal, "_backup_progress", {"phase": "idle"})
    monkeypatch.setattr(universal, "_offsite_sync", lambda dest: {"skipped": "drill"})
    monkeypatch.setattr(universal, "_snapshot_to_restic", lambda dest: None)
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")  # the manifest records "no Postgres"
    monkeypatch.setattr(universal, "_rebuilt_dirs", lambda: set())
    import kazma_core.backup.neo4j_backup as neo

    monkeypatch.setattr(neo, "export_graph", lambda dest: type("R", (), {"as_dict": lambda self: {}})())
    monkeypatch.setattr(universal, "_alert_on_backup_gaps", lambda *a, **k: None)
    repo = str(tmp_path / "repo")
    assert restic_repo.init_repo(repo, PASSWORD).ok
    yield type("Install", (), {"data": data, "base": base, "repo": repo, "tmp": tmp_path})
    dual_write._reset_mirror()


def _generation(install, *, older_by: int = 0) -> int:
    """A universal backup, sent to restic. Returns its generation."""
    before = set(install.base.iterdir())
    universal.perform_universal_backup(trigger="drill")
    made = next(p for p in install.base.iterdir() if p not in before)
    if older_by:  # two backups in one second share a name: date this one back
        made = made.rename(made.with_name(str(int(made.name) - older_by)))
    result = restic_repo.backup(install.repo, PASSWORD, [str(made)], tags=["universal"])
    assert result.ok, result.error
    return int(made.name)


def _restore_and_recall(install, generation: int, monkeypatch) -> list[str]:
    from kazma_core.memory import dual_write
    from kazma_core.memory.recall import recall

    target = install.tmp / f"restored-{generation}"
    res = restore_mod.restore_files(install.repo, PASSWORD, target, generation=generation)
    assert res.ok, (res.error, res.steps)
    restored = target / "kazma-data" / "memory_state.db"
    assert restored.is_file()
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(restored))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(target / "kazma-data" / "memory_ops.db"))
    dual_write._reset_mirror()
    found = recall("drill harbour codeword", tenant_id="default", local_only=True)
    return [str((e.metadata or {}).get("display") or e.content) for e in found.episodes]


def test_a_restic_restore_brings_the_memory_back(install, monkeypatch):
    from kazma_core.memory.dual_write import mirror_episode

    empty = _generation(install, older_by=60)
    assert mirror_episode(session_id="drill-chat", turn_number=1, user_text=QUESTION,
                          assistant_text=ANSWER, created_at=time.time() - 3600)
    full = _generation(install)
    assert full > empty

    shown = _restore_and_recall(install, full, monkeypatch)
    assert any("Saffron lantern" in text for text in shown), shown
    # Negative control: the generation from before the memory, restored the
    # same way, holds nothing to recall.
    assert _restore_and_recall(install, empty, monkeypatch) == []


def test_a_sqlite_install_restores_without_a_postgres_dump(install, tmp_path):
    """The manifest says Postgres was not used: no dump is no failure.

    Every restore of a SQLite install used to end FAILED on its "postgres
    dump" step. An older manifest, with no record, still fails on it.
    """
    gen = _generation(install)
    res = restore_mod.restore_files(install.repo, PASSWORD, tmp_path / "sqlite", generation=gen)
    step = next(s for s in res.steps if s["step"] == "postgres dump")
    assert res.ok and step["ok"] and step["detail"] == "not used by this install", res.steps
    # Negative control: the same backup with no postgres record in its manifest.
    manifest = install.base / str(gen) / "manifest.json"
    import json

    body = json.loads(manifest.read_text(encoding="utf-8"))
    body.pop("postgres", None)
    manifest.write_text(json.dumps(body), encoding="utf-8")
    assert restic_repo.backup(install.repo, PASSWORD, [str(manifest.parent)], tags=["universal"]).ok
    again = restore_mod.restore_files(install.repo, PASSWORD, tmp_path / "older", generation=gen)
    assert not again.ok
    assert not next(s for s in again.steps if s["step"] == "postgres dump")["ok"]


def test_the_drill_restores_into_its_scratch_folder_only(install):
    """Nothing outside the target is touched (restore_files' own rule)."""
    _generation(install)
    target = install.tmp / "scratch"
    target.mkdir()
    (target / "kept.txt").write_text("not empty", encoding="utf-8")
    res = restore_mod.restore_files(install.repo, PASSWORD, target)
    assert not res.ok and "not empty" in res.error
    assert (target / "kept.txt").read_text(encoding="utf-8") == "not empty"
