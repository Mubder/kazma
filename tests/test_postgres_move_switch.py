"""Moving Kazma's database off its container switches Kazma in one run.

Live 2026-11-06: ``kazma setup postgres --move-from-container kazma-db-win``
copied the container while Kazma kept writing to it, wrote the new address
into ``.env`` and said to reload when idle. Kazma was still on the container,
so the next restart -- a reload, a deploy, a crash -- would have started it on
a copy missing everything written since; and running the move again failed,
because the copy step refused a database that already held the tables.

Held here (the service faked, nothing installed):

* the move stops Kazma through its guard BEFORE it copies, and starts it on
  the new database after ``.env`` names it; the container is stopped (kept)
  only once Kazma is seen signed in to the new server;
* a running Kazma no guard can stop is refused before anything is done;
* on a healthy service whose address ``.env`` already names, a second run is
  the move only: no repair step, so no admin rights;
* an earlier copy is replaced only when Kazma never started on it
  (``pg_move._replace_refusal``: its last start not newer than the
  container's, nothing connected); a database Kazma started on is never
  emptied, and the dump comes before the emptying;
* a failed copy after the new database was emptied, with ``.env`` naming it,
  keeps Kazma stopped (started, it would run on an incomplete database); one
  refused before anything changed starts Kazma again.

The old setup is the negative control: it copied with Kazma running (no stop
before the copy) and said to reload; the order assertions fail on it.
"""

from __future__ import annotations

import subprocess

import pytest

from kazma_core import postgres_service as pgs
from kazma_core.backup import pg_move
from tests.test_setup_postgres import _options, _Runner, install  # noqa: F401 - the fixture

NEW_URL_PW = "App-Pw-Kept-9"


class _Hold:
    """The guard's stop and start, recorded in *events*."""

    def __init__(self, events: list[str], *, running: bool = True, refusal: str | None = None,
                 resume_result: str = "up") -> None:
        self.events = events
        self.running = running
        self._refusal = refusal
        self.resume_result = resume_result
        self.held = False

    def refusal(self):
        return self._refusal

    def stop(self) -> bool:
        self.events.append("stop")
        self.held = self.running
        return True

    def resume(self) -> str:
        if not self.held:
            return "not_held"
        self.events.append("resume")
        self.held = False
        return self.resume_result


def _healthy_runner():
    """The service is installed and healthy; docker update/stop recorded."""
    return _Runner(installed=True)


@pytest.fixture
def switched_once(install, monkeypatch):  # noqa: F811 - the imported fixture
    """The state live was in: the service set up, .env already naming it, the
    superuser's password kept; Kazma still running on the container."""
    (install / ".kazma" / "postgres_superuser.pass").write_text("Super-Kept-1\n", encoding="utf-8")
    url = pgs.database_url(NEW_URL_PW, 5432)
    (install / ".env").write_text(f"KAZMA_DATABASE_URL={url}\n", encoding="utf-8")
    monkeypatch.setattr(pgs, "is_admin", lambda: False)  # a plain terminal
    monkeypatch.setattr("kazma_core.docker_cli.find_docker_cli", lambda: "docker")
    return install


def _fake_move(events: list[str], *, ok: bool = True, empties: bool = False):
    def move(container, dest_url, *, port, say, empty_target=None, **_):
        events.append("copy")
        if empties:
            assert empty_target is not None
            events.append("empty")
            empty_target()
        return ok
    return move


def test_a_move_stops_kazma_before_the_copy_and_starts_it_on_the_new_database(switched_once, monkeypatch, capsys):
    from kazma_cli import setup

    events: list[str] = []
    monkeypatch.setattr(pg_move, "move_from_container", _fake_move(events))
    monkeypatch.setattr(setup, "_kazma_signed_in", lambda url, **kw: events.append("seen") or True)
    env_before = (switched_once / ".env").read_bytes()
    runner = _healthy_runner()
    hold = _Hold(events)

    code = setup._setup_postgres(_options(move_from_container="kazma-db-win"), run=runner, hold=hold)

    assert code == 0, capsys.readouterr().out
    assert events == ["stop", "copy", "resume", "seen"]
    # The container is stopped only after Kazma was seen on the new server, and kept.
    docker = [c["argv"] for c in runner.calls if c["argv"][:1] == ["docker"]]
    assert docker == [["docker", "update", "--restart=no", "kazma-db-win"], ["docker", "stop", "kazma-db-win"]]
    assert not any(c["argv"][:1] == ["docker"] and "rm" in c["argv"] for c in runner.calls)
    # The move only: no repair step (sc config/failure, icacls, psql), so no admin rights.
    assert {c["argv"][0] for c in runner.calls} <= {"sc", "docker"}
    assert not any(c["argv"][:2] in (["sc", "config"], ["sc", "failure"]) for c in runner.calls)
    assert (switched_once / ".env").read_bytes() == env_before
    out = capsys.readouterr().out
    assert "the move only" in out and "Kazma runs on the new database" in out
    assert NEW_URL_PW not in out


def test_a_running_kazma_no_guard_can_stop_is_refused_before_anything(switched_once, monkeypatch, capsys):
    from kazma_cli import setup

    events: list[str] = []
    monkeypatch.setattr(pg_move, "move_from_container", _fake_move(events))
    runner = _healthy_runner()
    hold = _Hold(events, refusal="Kazma is running and no guard supervises it ... Nothing was done.")

    assert setup._setup_postgres(_options(move_from_container="kazma-db-win"), run=runner, hold=hold) == 2
    assert events == []
    assert {c["argv"][0] for c in runner.calls} <= {"sc"}
    assert "Nothing was done" in capsys.readouterr().out


def test_kazma_is_not_seen_on_the_new_server_and_the_container_keeps_running(switched_once, monkeypatch):
    from kazma_cli import setup

    events: list[str] = []
    monkeypatch.setattr(pg_move, "move_from_container", _fake_move(events))
    monkeypatch.setattr(setup, "_kazma_signed_in", lambda url, **kw: False)
    runner = _healthy_runner()

    assert setup._setup_postgres(_options(move_from_container="kazma-db-win"), run=runner,
                                 hold=_Hold(events)) == 1
    assert not any(c["argv"][:1] == ["docker"] for c in runner.calls)


def test_a_failed_copy_after_the_database_was_emptied_keeps_kazma_stopped(switched_once, monkeypatch, capsys):
    from kazma_cli import setup

    events: list[str] = []
    monkeypatch.setattr(pg_move, "move_from_container", _fake_move(events, ok=False, empties=True))
    runner = _healthy_runner()

    assert setup._setup_postgres(_options(move_from_container="kazma-db-win"), run=runner,
                                 hold=_Hold(events)) == 1
    assert events == ["stop", "copy", "empty"]  # never "resume": .env names an incomplete database
    # The emptying is the superuser's DROP and CREATE, then the role script; no password on a command line.
    psql = [c for c in runner.calls if c["argv"] and c["argv"][0].endswith("psql.exe")]
    assert "DROP DATABASE IF EXISTS kazma;" in psql[0]["stdin"] and "FORCE" not in psql[0]["stdin"]
    assert not any("Super-Kept-1" in " ".join(c["argv"]) for c in runner.calls)
    assert "Kazma stays stopped" in capsys.readouterr().out


def test_a_copy_refused_before_anything_changed_starts_kazma_again(switched_once, monkeypatch):
    from kazma_cli import setup

    events: list[str] = []
    monkeypatch.setattr(pg_move, "move_from_container", _fake_move(events, ok=False))
    runner = _healthy_runner()

    assert setup._setup_postgres(_options(move_from_container="kazma-db-win"), run=runner,
                                 hold=_Hold(events)) == 1
    assert events == ["stop", "copy", "resume"]
    assert not any(c["argv"][:1] == ["docker"] for c in runner.calls)


# -- the copy: replace an earlier copy only if Kazma never started on it ---------------------------


@pytest.mark.parametrize(("container", "target", "others", "allowed"), [
    (100.0, 100.0, 0, True),    # the earlier copy, unchanged: Kazma never started there
    (200.0, 100.0, 0, True),    # Kazma started again on the container since the copy
    (100.0, 150.0, 0, False),   # Kazma started on the new server: it is in use
    (None, 100.0, 0, False),    # cannot be told
    (100.0, None, 0, False),
    (100.0, 100.0, 1, False),   # something is connected to it
])
def test_an_earlier_copy_is_replaced_only_when_kazma_never_started_on_it(container, target, others, allowed):
    assert (pg_move._replace_refusal(container, target, others) is None) is allowed


def test_the_boot_stamp_reads_as_stored():
    assert pg_move._boot_time("1793957635.156942\n") == 1793957635.156942
    assert pg_move._boot_time('"12.5"') == 12.5
    assert pg_move._boot_time("") is None and pg_move._boot_time(None) is None


class _Docker:
    """docker exec and pg_restore, recorded; the container's boot stamp is *boot*."""

    def __init__(self, events: list[str], boot: str) -> None:
        self.events, self.boot = events, boot

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        if "pg_dump" in argv:
            self.events.append("dump")
            kw["stdout"].write(b"PGDMP-fake")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[0].endswith(("pg_restore", "pg_restore.exe")):
            self.events.append("restore")
            return subprocess.CompletedProcess(argv, 0, "", "")
        sql = argv[-1]
        if "pg_tables" in sql:
            return subprocess.CompletedProcess(argv, 0, "kazma_settings\nkazma_chat_sessions\n", "")
        if pg_move.BOOT_KEY in sql:
            return subprocess.CompletedProcess(argv, 0, self.boot + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "kazma_settings|3\nkazma_chat_sessions|2\n", "")


@pytest.fixture
def copy_env(monkeypatch):
    monkeypatch.setattr("kazma_core.docker_cli.find_docker_cli", lambda: "docker")
    monkeypatch.setattr("kazma_core.db.backend.get_database_url",
                        lambda: "postgresql://kazma_app:x@127.0.0.1:5433/kazma")


def test_a_database_kazma_started_on_is_never_emptied(copy_env, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(pg_move, "_target_state",
                        lambda url: ({"kazma_settings", "kazma_chat_sessions"}, 300.0, 0))
    said: list[str] = []
    ok = pg_move.move_from_container("kazma-db-win", "postgresql://kazma_app:y@127.0.0.1:5432/kazma",
                                     port=5432, say=said.append, run=_Docker(events, "200.0"),
                                     bin_dir="C:/pg/bin", empty_target=lambda: events.append("empty") or True)
    assert ok is False
    assert events == []  # no dump, no emptying, no restore
    assert any("Kazma has started on the new server" in line for line in said), said


def test_an_earlier_copy_is_emptied_after_the_dump_and_before_the_restore(copy_env, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(pg_move, "_target_state",
                        lambda url: ({"kazma_settings", "kazma_chat_sessions"}, 200.0, 0))

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql):
            return type("R", (), {"fetchall": lambda self: [("kazma_settings", 3), ("kazma_chat_sessions", 2)]})()

    psycopg = pytest.importorskip("psycopg")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **kw: _Conn())
    ok = pg_move.move_from_container("kazma-db-win", "postgresql://kazma_app:y@127.0.0.1:5432/kazma",
                                     port=5432, say=lambda s: None, run=_Docker(events, "200.0"),
                                     bin_dir="C:/pg/bin", empty_target=lambda: events.append("empty") or True)
    assert ok is True
    assert events == ["dump", "empty", "restore"]


def test_an_earlier_copy_is_refused_when_nothing_can_empty_it(copy_env, monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(pg_move, "_target_state",
                        lambda url: ({"kazma_settings"}, 200.0, 0))
    ok = pg_move.move_from_container("kazma-db-win", "postgresql://kazma_app:y@127.0.0.1:5432/kazma",
                                     port=5432, say=lambda s: None, run=_Docker(events, "200.0"),
                                     bin_dir="C:/pg/bin")
    assert ok is False and events == []


def test_the_empty_steps_drop_without_force_and_hand_the_database_to_kazma_app():
    steps = pgs.empty_database_steps(pgs.Platform("windows", "winget"), port=5432, superuser_password="S-1",
                                     role_sql="-- role")
    assert steps[0].stdin == "DROP DATABASE IF EXISTS kazma;\nCREATE DATABASE kazma;\n"
    assert steps[1].stdin == "-- role" and steps[1].argv[-1] == "kazma"
    assert all(s.env == {"PGPASSWORD": "S-1"} and "S-1" not in " ".join(s.argv) for s in steps)
    assert not any(s.admin for s in steps)
