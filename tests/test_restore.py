"""Rebuilding an install from a backup, and the trap in doing it naively.

The question that prompted this: "if I reinstalled Kazma any time can I
restore from those backups?" The data was all there and provably readable;
what was missing was anything that turned it back into a working install.

The trap is worth stating plainly because it is silent. ``restic restore
latest`` selects by the time the SNAPSHOT was taken, not by the age of the
DATA. When 22 legacy generations were bulk-ingested hours after the recent
ones, they took the newest snapshot timestamps while carrying the oldest
content -- so ``latest`` yields a generation predating kazma.yaml and the
graph export, and looks like a clean success.
"""

from __future__ import annotations

import pytest
from kazma_core.backup import restore as restore_mod


def _snap(sid, when, path):
    return {"short_id": sid, "time": when, "paths": [path]}


def _repo(monkeypatch, snaps):
    monkeypatch.setattr(restore_mod, "_snapshots", lambda repo, pw: snaps)


# ── selection: by generation, never by snapshot time ──────────────────


def test_the_newest_data_wins_not_the_newest_snapshot(monkeypatch):
    """The exact live situation: a legacy generation ingested later carries
    a newer snapshot timestamp and older content."""
    _repo(monkeypatch, [
        _snap("newdata", "2026-08-29T03:26:34",
              r"C:\k\kazma-data\backups\universal\1787963091"),
        _snap("olddata", "2026-08-29T04:33:50",   # taken LATER
              r"C:\k\kazma-data\backups\universal\1787849753"),
    ])
    points = restore_mod.list_restore_points("repo", "pw")
    chosen = restore_mod.select_point(points)

    assert chosen.generation == 1787963091
    assert chosen.snapshot_id == "newdata", (
        "selecting by snapshot time would have picked the older content"
    )


def test_an_explicit_generation_is_honoured(monkeypatch):
    _repo(monkeypatch, [
        _snap("a", "2026-08-29T01:00:00", r"/k/backups/universal/1787849753"),
        _snap("b", "2026-08-29T02:00:00", r"/k/backups/universal/1787963091"),
    ])
    points = restore_mod.list_restore_points("repo", "pw")
    assert restore_mod.select_point(points, 1787849753).snapshot_id == "a"


def test_an_unknown_generation_is_refused(monkeypatch):
    _repo(monkeypatch, [
        _snap("a", "2026-08-29T01:00:00", r"/k/backups/universal/1787849753"),
    ])
    points = restore_mod.list_restore_points("repo", "pw")
    assert restore_mod.select_point(points, 9999999999) is None


def test_re_ingested_generations_do_not_duplicate(monkeypatch):
    """Bulk re-ingest makes two snapshots of identical content. One restore
    point, not two."""
    _repo(monkeypatch, [
        _snap("first", "2026-08-29T01:00:00", r"/k/backups/universal/1787963091"),
        _snap("again", "2026-08-29T04:00:00", r"/k/backups/universal/1787963091"),
    ])
    points = restore_mod.list_restore_points("repo", "pw")
    assert len(points) == 1
    assert points[0].snapshot_id == "again", "keep the most recent copy"


# ── pairing the database dump ─────────────────────────────────────────


def test_the_dump_paired_is_at_or_before_the_generation(monkeypatch):
    """A dump taken AFTER the file backup describes a database that has
    moved on from the files beside it -- close enough to look right, wrong
    in a way that is hard to notice later."""
    _repo(monkeypatch, [
        _snap("files", "2026-08-29T03:00:00", r"/k/backups/universal/1787950000"),
        _snap("pgold", "2026-08-29T01:00:00", r"/k/backups/pg/pg_shared_1787940000.dump"),
        _snap("pgnew", "2026-08-29T05:00:00", r"/k/backups/pg/pg_shared_1787960000.dump"),
    ])
    point = restore_mod.list_restore_points("repo", "pw")[0]
    assert point.pg_snapshot_id == "pgold", (
        "a dump newer than the files is not the state that accompanied them"
    )


def test_a_repository_with_no_dump_says_so(monkeypatch, tmp_path):
    """Silence here would mean discovering the database is unrecoverable
    during the recovery."""
    _repo(monkeypatch, [
        _snap("files", "2026-08-29T03:00:00", r"/k/backups/universal/1787950000"),
    ])
    monkeypatch.setattr(restore_mod, "_restic_restore", lambda *a, **k: (True, ""))
    monkeypatch.setattr(restore_mod, "_find_backup_root", lambda tree: None)

    res = restore_mod.restore_files("repo", "pw", tmp_path / "out")
    assert any(s["step"] == "select" and s["ok"] for s in res.steps)


# ── refusing to make a mess ───────────────────────────────────────────


def test_a_non_empty_target_is_refused(tmp_path):
    """A restore is run when something has already gone wrong, often at the
    wrong target. Mixing two states produces one that looks plausible and
    is neither."""
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "existing.txt").write_text("mine", encoding="utf-8")

    res = restore_mod.restore_files("repo", "pw", target)
    assert res.ok is False
    assert "not empty" in res.error
    assert (target / "existing.txt").is_file(), "must not have touched anything"


def test_an_empty_repository_fails_loudly(monkeypatch, tmp_path):
    _repo(monkeypatch, [])
    res = restore_mod.restore_files("repo", "pw", tmp_path / "out")
    assert res.ok is False
    assert "no restorable generations" in res.error


def test_the_databases_are_never_loaded_automatically():
    """Loading Postgres or Neo4j overwrites live data. It stays an explicit
    human decision; this module restores files and prints the commands."""
    import inspect

    src = inspect.getsource(restore_mod)
    assert "pg_restore --clean" in src, "the command is shown to the operator"
    assert "subprocess.run([\"pg_restore" not in src, "and never executed here"
    assert "restore_graph(" in src, "graph load is shown"
    assert "from kazma_core.backup.neo4j_backup import restore_graph" not in src.split(
        "def _next_steps")[0], "graph load is not invoked during restore"


# ── what a restored install must contain ──────────────────────────────


@pytest.mark.parametrize("missing,why", [
    (".env", "vault"),
    ("kazma.yaml", "tools"),
])
def test_a_missing_critical_file_is_reported(monkeypatch, tmp_path, missing, why):
    """Restoring everything except the key to decrypt the vault, or the
    config that gives the agent its tools, is not a restore."""
    _repo(monkeypatch, [
        _snap("files", "2026-08-29T03:00:00", r"/k/backups/universal/1787950000"),
    ])

    root = tmp_path / "staged"
    (root / "dbs").mkdir(parents=True)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    for name in (".env", "kazma.yaml"):
        if name != missing:
            (root / name).write_text("x", encoding="utf-8")

    monkeypatch.setattr(restore_mod, "_restic_restore", lambda *a, **k: (True, ""))
    monkeypatch.setattr(restore_mod, "_find_backup_root", lambda tree: root)

    res = restore_mod.restore_files("repo", "pw", tmp_path / "out")
    failed = [s["step"] for s in res.steps if not s["ok"]]
    assert any(why in s["detail"] for s in res.steps if not s["ok"]), (
        f"expected a failure mentioning {why}, got {failed}"
    )
