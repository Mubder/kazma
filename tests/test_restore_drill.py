"""A backup nobody has restored is a hypothesis.

The drill's own value depends on it failing when a backup is bad, so most
of these build a broken backup on purpose. A verifier that has only ever
returned PASS is the same category of claim as a recovery mechanism that
has never fired -- which, this week, turned out to mean "could not fire".
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from kazma_core.backup.restore_drill import verify_backup


def _make_db(path, *, tables=2, corrupt=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    for i in range(tables):
        conn.execute(f"CREATE TABLE t{i} (a TEXT)")
        conn.execute(f"INSERT INTO t{i} VALUES ('x')")
    conn.commit()
    conn.close()
    if corrupt:
        # Scribble over the page data, leaving the 16-byte header intact so
        # the file still looks like SQLite to anything that only sniffs it.
        raw = bytearray(path.read_bytes())
        for i in range(100, min(len(raw), 4000)):
            raw[i] = 0
        path.write_bytes(bytes(raw))


def _make_backup(root, *, with_env=True, with_manifest=True, failed=0,
                 corrupt_db=False):
    d = root / "1787942545"
    (d / "dbs").mkdir(parents=True)
    _make_db(d / "dbs" / "chat_sessions.db")
    _make_db(d / "dbs" / "checkpoints.db", corrupt=corrupt_db)
    if with_env:
        (d / ".env").write_text("KAZMA_SECRET=abc\n", encoding="utf-8")
    if with_manifest:
        (d / "manifest.json").write_text(json.dumps({
            "timestamp": 1787942545, "version": 1,
            "databases": {"ok": 2, "failed": failed, "items": []},
        }), encoding="utf-8")
    return d


# ── a good backup passes ──────────────────────────────────────────────


def test_a_healthy_backup_passes(tmp_path):
    res = verify_backup(_make_backup(tmp_path))
    assert res.ok, res.failures
    assert any(c["check"] == "sqlite:chat_sessions.db" for c in res.checks)


def test_the_backup_is_never_written_to(tmp_path):
    """The drill must be safe to run against a real backup at any time."""
    d = _make_backup(tmp_path)
    before = {p: p.stat().st_mtime_ns for p in d.rglob("*") if p.is_file()}
    verify_backup(d)
    after = {p: p.stat().st_mtime_ns for p in d.rglob("*") if p.is_file()}
    assert before == after, "the drill modified the backup it was verifying"


# ── and the failures it has to catch ──────────────────────────────────


def test_a_corrupt_database_is_caught(tmp_path):
    """The whole point. The file is present and the right size, and it is
    not restorable -- which a file-exists check calls success."""
    res = verify_backup(_make_backup(tmp_path, corrupt_db=True))
    assert not res.ok
    assert any("checkpoints.db" in c["check"] for c in res.failures)


def test_a_missing_env_fails_the_drill(tmp_path):
    """.env holds KAZMA_SECRET. Without it the backed-up encrypted vault
    is unreadable, so a backup that restores everything except the key to
    read it is not a backup."""
    res = verify_backup(_make_backup(tmp_path, with_env=False))
    assert not res.ok
    detail = next(c["detail"] for c in res.failures if c["check"] == "env:present")
    assert "vault" in detail, "say why it matters, not just that it is absent"


def test_a_manifest_recording_failures_fails_the_drill(tmp_path):
    """The backup told us it was incomplete. Believe it."""
    res = verify_backup(_make_backup(tmp_path, failed=3))
    assert not res.ok
    assert any(c["check"] == "manifest:databases" for c in res.failures)


def test_a_missing_manifest_is_caught(tmp_path):
    res = verify_backup(_make_backup(tmp_path, with_manifest=False))
    assert not res.ok


def test_an_empty_backup_directory_is_not_a_pass(tmp_path):
    """The most dangerous shape: nothing failed because nothing was there."""
    empty = tmp_path / "empty"
    empty.mkdir()
    res = verify_backup(empty)
    assert not res.ok


def test_a_nonexistent_backup_is_not_a_pass(tmp_path):
    assert not verify_backup(tmp_path / "nope").ok


# ── the Postgres archive ──────────────────────────────────────────────


def test_a_dump_without_the_magic_is_rejected(tmp_path):
    d = _make_backup(tmp_path)
    bad = tmp_path / "pg_shared_1.dump"
    bad.write_bytes(b"this is not a postgres archive")
    res = verify_backup(d, pg_dump=bad)
    assert not res.ok
    assert any(c["check"] == "postgres:header" for c in res.failures)


def test_a_truncated_dump_is_rejected(tmp_path):
    """A dump cut short by a full disk keeps its PGDMP header. Only
    parsing the table of contents notices."""
    pytest.importorskip("shutil")
    import shutil as _sh

    if not _sh.which("pg_restore"):
        pytest.skip("pg_restore not on PATH")
    d = _make_backup(tmp_path)
    bad = tmp_path / "pg_shared_2.dump"
    bad.write_bytes(b"PGDMP" + b"\x00" * 512)
    res = verify_backup(d, pg_dump=bad)
    assert not res.ok
    assert any(c["check"] == "postgres:toc" for c in res.failures)


def test_a_host_without_pg_restore_still_passes(tmp_path, monkeypatch):
    """Missing client tools is not a broken backup. Failing the drill for
    it would train operators to ignore a real failure."""
    import kazma_core.migration.pg_bridge as bridge

    monkeypatch.setattr(bridge, "resolve_pg_restore",
                        lambda: (_ for _ in ()).throw(RuntimeError("not installed")))
    d = _make_backup(tmp_path)
    good = tmp_path / "pg_shared_3.dump"
    good.write_bytes(b"PGDMP" + b"\x00" * 512)
    res = verify_backup(d, pg_dump=good)
    assert res.ok, res.failures


# ── the verdict is usable ─────────────────────────────────────────────


def test_the_summary_names_the_backup_and_the_verdict(tmp_path):
    res = verify_backup(_make_backup(tmp_path))
    assert "PASS" in res.summary()
    assert "1787942545" in res.summary()


def test_the_cli_exits_nonzero_on_a_bad_backup(tmp_path):
    """So it can be scheduled, rather than relying on someone reading it."""
    from kazma_core.backup.restore_drill import main

    bad = _make_backup(tmp_path, with_env=False)
    assert main(["--backup", str(bad)]) == 1
    good = _make_backup(tmp_path / "other")
    assert main(["--backup", str(good)]) == 0


# ── the containerised deployment shape ────────────────────────────────


def test_a_dockerised_pg_restore_is_fed_over_stdin(tmp_path, monkeypatch):
    """resolve_pg_restore may return "docker exec -i <c> pg_restore". That
    binary runs INSIDE the container and cannot see a host path -- the first
    live run of this drill failed with "could not open input file" naming a
    Windows path the container has no view of. The archive has to be piped
    in over stdin instead.
    """
    import kazma_core.backup.restore_drill as rd
    import kazma_core.migration.pg_bridge as bridge

    monkeypatch.setattr(bridge, "resolve_pg_restore",
                        lambda: ["docker", "exec", "-i", "kazma-db", "pg_restore"])

    seen = {}

    def _fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["stdin"] = kw.get("stdin")
        return type("P", (), {"returncode": 0, "stdout": "1; 2; 3\nTABLE x\n",
                              "stderr": ""})()

    monkeypatch.setattr(rd.subprocess, "run", _fake_run)

    d = _make_backup(tmp_path)
    dump = tmp_path / "pg_shared_4.dump"
    dump.write_bytes(b"PGDMP" + b"\x00" * 64)
    res = verify_backup(d, pg_dump=dump)

    assert res.ok, res.failures
    assert str(dump) not in seen["cmd"], (
        "a host path must not be passed to a tool running in the container"
    )
    assert seen["stdin"] is not None, "the archive must be piped over stdin"


def test_a_local_pg_restore_still_gets_the_path(tmp_path, monkeypatch):
    """The non-docker case must not regress into needless piping."""
    import kazma_core.backup.restore_drill as rd
    import kazma_core.migration.pg_bridge as bridge

    monkeypatch.setattr(bridge, "resolve_pg_restore", lambda: ["pg_restore"])

    seen = {}

    def _fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["stdin"] = kw.get("stdin")
        return type("P", (), {"returncode": 0, "stdout": "TABLE x\n", "stderr": ""})()

    monkeypatch.setattr(rd.subprocess, "run", _fake_run)

    d = _make_backup(tmp_path)
    dump = tmp_path / "pg_shared_5.dump"
    dump.write_bytes(b"PGDMP" + b"\x00" * 64)
    assert verify_backup(d, pg_dump=dump).ok
    assert str(dump) in seen["cmd"]
    assert seen["stdin"] is None
