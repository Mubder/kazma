"""``kazma setup postgres --upgrade``: Kazma's database onto a newer Postgres
major (2026-11-13).

Every install made before 18 runs Postgres 16 as the OS service ``kazma setup
postgres`` set up. A major cannot open another's data folder, so the upgrade
installs the new major beside the old one on a spare port, holds Kazma, copies
and compares, then switches the servers on the old port (``.env`` unchanged),
keeping the old one stopped. Held here, on a fake Windows machine whose
services keep their own state (installed, running, port, boot):

* the order: the new server installed on its spare port before Kazma stops;
  the copy while Kazma is held; the record naming the new server before any
  server stops (the guard's watch never starts the old one); Kazma started
  only after the new server answers on the old port;
* a failed copy starts Kazma again on the old server, untouched;
* a switch that fails is undone: the old server back on its port and on the
  boot list, the record naming it, Kazma started on it; the next run reuses
  the installed new server (no second install);
* a run killed in the switch leaves a journal the next run undoes first;
* refusals before anything changes: no record, a running Kazma no guard can
  stop, a database already on the current major;
* a repair run of a 16 install repairs 16 and installs no 18 beside it;
* each major's WAL archive, base backups and offsite names are its own: with
  one shared base folder, the old major's base backup made the prune take the
  new major's segments (the negative control);
* the guard's database watch leaves the service alone while a switch is
  journaled, and acts again once the hold has passed.
"""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
import time
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest
from kazma_core import postgres_service as pgs
from kazma_core import postgres_upgrade as pgu
from kazma_core.backup import wal_archive
from kazma_core.postgres_service import PG_MAJOR, Platform

from tests.test_setup_postgres import _options, install  # noqa: F401 - the fixture

OLD, NEW = 16, PG_MAJOR
OLD_SVC, NEW_SVC = f"postgresql-x64-{OLD}", f"postgresql-x64-{NEW}"
APP_PW = "App-Pw-Kept-7"
SUPER_PW = "Super-Kept-3"


class _Machine:
    """Two Windows services and their ports, as ``sc``, winget and the
    port checks see them. A server starts on its configured port, and fails
    to when another running server holds that port."""

    def __init__(self) -> None:
        self.services = {OLD_SVC: dict(installed=True, running=True, port=5432, conf_port=5432, boot="auto")}
        self.calls: list[list[str]] = []
        self.fail_start: set[str] = set()
        self.on_stop = None

    def _svc_for_major(self, major: int) -> str:
        return f"postgresql-x64-{major}"

    def run(self, argv, **kw):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:2] == ["winget", "install"]:
            override = argv[argv.index("--override") + 1].split()
            name = override[override.index("--servicename") + 1]
            port = int(override[override.index("--serverport") + 1])
            self.services[name] = dict(installed=True, running=False, port=port, conf_port=port, boot="auto")
            self._start(name)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:1] == ["sc"] and len(argv) >= 3:
            svc = self.services.get(argv[2])
            if svc is None or not svc["installed"]:
                return subprocess.CompletedProcess(argv, 1060, "FAILED 1060", "")
            if argv[1] == "query":
                state = "4  RUNNING" if svc["running"] else "1  STOPPED"
                return subprocess.CompletedProcess(argv, 0, f"STATE              : {state}", "")
            if argv[1] == "stop":
                if self.on_stop:
                    self.on_stop(argv[2])
                svc["running"] = False
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "start":
                self._start(argv[2])
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "config":
                svc["boot"] = "auto" if argv[-1] == "auto" else "demand"
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "qc":
                text = "AUTO_START" if svc["boot"] == "auto" else "DEMAND_START"
                return subprocess.CompletedProcess(argv, 0, f"START_TYPE : {text}", "")
            if argv[1] == "qfailure":
                return subprocess.CompletedProcess(argv, 0, "FAILURE_ACTIONS : RESTART -- Delay = 5000", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def _start(self, name: str) -> None:
        svc = self.services[name]
        taken = any(o["running"] and o["port"] == svc["conf_port"] for n, o in self.services.items() if n != name)
        if name in self.fail_start or taken:
            svc["running"] = False
            return
        svc["running"], svc["port"] = True, svc["conf_port"]

    def answers(self, port: int) -> bool:
        return any(s["running"] and s["port"] == port for s in self.services.values())

    def major_on(self, port: int) -> int | None:
        for name, s in self.services.items():
            if s["running"] and s["port"] == port:
                return int(name.rsplit("-", 1)[1])
        return None


class _Hold:
    """The guard's stop and start, as setup._MoveHold: a Kazma that is not
    running is not held (and not waited for)."""

    def __init__(self, events: list[str], *, refusal: str | None = None, running: bool = True) -> None:
        self.events, self._refusal = events, refusal
        self.held, self.running = False, running

    def refusal(self):
        return self._refusal

    def adopt_leftover(self) -> bool:
        return False

    def stop(self) -> bool:
        self.events.append("hold")
        self.held = self.running
        return True

    def resume(self) -> str:
        if not self.held:
            return "not_held"
        self.events.append("resume")
        self.held = False
        return "up"


@pytest.fixture
def machine(install, monkeypatch):  # noqa: F811 - the imported fixture
    """A 16 install as ``kazma setup postgres`` left it: the service, its
    record, .env naming kazma_app on it, the superuser's password kept."""
    m = _Machine()
    (install / ".kazma" / "postgres_superuser.pass").write_text(SUPER_PW + "\n", encoding="utf-8")
    (install / ".env").write_text(f"KAZMA_DATABASE_URL={pgs.database_url(APP_PW, 5432)}\n", encoding="utf-8")
    pgs.write_record(pgs.PostgresRecord(
        os="windows", manager="winget", service=OLD_SVC, port=5432, bin_dir=pgs.bin_dir_for(
            Platform("windows", "winget"), OLD), major=OLD, wal_archive_dir=r"C:\ProgramData\Kazma\pg_wal_archive",
        installed_at=1_790_000_000.0))
    monkeypatch.setattr(pgs, "wait_for_port", lambda port, **kw: m.answers(port))
    monkeypatch.setattr(pgs, "_port_answers", lambda port, host="127.0.0.1", timeout=2.0: m.answers(port))
    monkeypatch.setattr(pgs, "restart_service", lambda os_name, service, port, **kw: (
        m.run(["sc", "stop", service]), m.run(["sc", "start", service]), m.answers(port))[-1])
    # The machine is a Windows one: its data folders are Windows paths, read
    # as such on every OS the suite runs on (Linux CI split nothing at "\\").
    monkeypatch.setattr(pgs, "set_port_on_disk", lambda data_dir, port: m.services[
        f"postgresql-x64-{PureWindowsPath(data_dir).parent.name}"].__setitem__("conf_port", port))
    monkeypatch.setattr(pgs, "time", SimpleNamespace(sleep=lambda s: None, time=time.time))
    monkeypatch.setattr(pgu, "server_major", lambda url: m.major_on(int(url.rsplit(":", 1)[1].split("/")[0])))
    monkeypatch.setattr(pgu, "_free_port", lambda *a, **kw: 55432)
    monkeypatch.setattr("kazma_cli.setup._kazma_signed_in", lambda url, **kw: True)
    monkeypatch.setattr(wal_archive, "_base_backup", lambda rec, pw, **kw: Path("1"))
    m.events = []

    def copy(source_url, dest_url, **kw):
        m.events.append(("copy", m.major_on(int(source_url.rsplit(":", 1)[1].split("/")[0])),
                         m.major_on(int(dest_url.rsplit(":", 1)[1].split("/")[0]))))
        return m.copy_ok

    m.copy_ok = True
    monkeypatch.setattr(pgu, "copy_database", copy)
    return m


def _upgrade(m: _Machine, *, hold=None, **kw) -> int:
    from kazma_cli.postgres_upgrade import upgrade_postgres

    return upgrade_postgres(_options(upgrade=True, **kw), run=m.run, hold=hold or _Hold(m.events))


def _record() -> dict:
    return pgs.read_record()


# -- the whole move ---------------------------------------------------------------------------


def test_the_upgrade_switches_kazma_to_the_new_major_on_the_same_port(machine, install, capsys):  # noqa: F811 - the imported fixture
    env_before = (install / ".env").read_bytes()
    seen_at_old_stop = []
    machine.on_stop = lambda name: seen_at_old_stop.append(_record()["postgres"]["service"]) \
        if name == OLD_SVC else None

    assert _upgrade(machine) == 0, capsys.readouterr().out

    # Installed on its spare port before Kazma was held; copied from 16 into it while held.
    installs = [i for i, c in enumerate(machine.calls) if c[:2] == ["winget", "install"]]
    assert len(installs) == 1
    assert machine.events[:2] == ["hold", ("copy", OLD, NEW)]
    assert machine.events[-1] == "resume"
    # The record named the new server before the old one stopped.
    assert seen_at_old_stop == [NEW_SVC]
    old, new = machine.services[OLD_SVC], machine.services[NEW_SVC]
    assert (old["running"], old["boot"]) == (False, "demand")          # kept, stopped, off the boot list
    assert (new["running"], new["port"], new["boot"]) == (True, 5432, "auto")
    rec = _record()
    assert rec["postgres"]["major"] == NEW and rec["postgres"]["service"] == NEW_SVC
    assert rec["postgres"]["previous"]["major"] == OLD and rec["postgres"]["previous"]["service"] == OLD_SVC
    assert rec["postgres"]["offsite_tag"] == f"pg{NEW}-"
    assert rec["postgres"]["base_backup_dir"].endswith(f"base-{NEW}")
    assert rec["postgres"]["wal_archive_dir"].endswith(f"pg_wal_archive-{NEW}")
    assert pgu.JOURNAL_KEY not in rec
    assert (install / ".env").read_bytes() == env_before           # every address stays as it is
    out = capsys.readouterr().out
    for secret in (APP_PW, SUPER_PW):
        assert secret not in out and not any(secret in " ".join(c) for c in machine.calls)


def test_a_failed_copy_starts_kazma_again_on_the_old_server(machine, capsys):
    machine.copy_ok = False
    assert _upgrade(machine) == 1
    assert machine.events == ["hold", ("copy", OLD, NEW), "resume"]
    assert machine.services[OLD_SVC]["running"] and machine.services[OLD_SVC]["boot"] == "auto"
    rec = _record()
    assert rec["postgres"]["major"] == OLD and rec[pgu.JOURNAL_KEY]["stage"] == "installed"
    assert "Kazma was started again on PostgreSQL 16" in capsys.readouterr().out


def test_a_switch_that_fails_is_undone_and_the_next_run_finishes(machine, capsys):
    machine.fail_start.add(NEW_SVC)
    # The first start (the install on the spare port) works; the switch's does not.
    machine.services_started = 0
    original = machine._start

    def start(name):
        if name == NEW_SVC and machine.services.get(NEW_SVC, {}).get("conf_port") == 5432:
            machine.services[name]["running"] = False
            return
        machine.fail_start.discard(name)
        original(name)

    machine._start = start
    assert _upgrade(machine) == 1
    out = capsys.readouterr().out
    assert "The switch was undone" in out
    old, new = machine.services[OLD_SVC], machine.services[NEW_SVC]
    assert (old["running"], old["port"], old["boot"]) == (True, 5432, "auto")
    assert (new["running"], new["conf_port"], new["boot"]) == (False, 55432, "demand")
    assert _record()["postgres"]["major"] == OLD
    assert machine.events[-1] == "resume"

    machine._start = original
    machine.events.clear()
    assert _upgrade(machine) == 0, capsys.readouterr().out
    assert sum(1 for c in machine.calls if c[:2] == ["winget", "install"]) == 1   # reused, not installed again
    assert _record()["postgres"]["major"] == NEW and machine.services[NEW_SVC]["port"] == 5432


def test_without_the_undo_a_failed_switch_leaves_no_database(machine, monkeypatch, capsys):
    """The negative control: the undo is what puts the old server back."""
    from kazma_cli import postgres_upgrade as cli

    monkeypatch.setattr(cli, "_undo", lambda *a, **kw: True)
    original = machine._start
    machine._start = lambda name: (machine.services[name].__setitem__("running", False)
                                   if name == NEW_SVC and machine.services[name]["conf_port"] == 5432
                                   else original(name))
    _upgrade(machine)
    assert not machine.answers(5432)


def test_a_run_killed_in_the_switch_is_undone_by_the_next(machine, capsys):
    # Killed after the old server stopped and before the new one started.
    machine.services[NEW_SVC] = dict(installed=True, running=False, port=55432, conf_port=5432, boot="auto")
    machine.services[OLD_SVC].update(running=False, boot="demand")
    old_record = _record()["postgres"]
    pgu.write_journal({"stage": "switching", "from": OLD, "to": NEW, "temp_port": 55432, "port": 5432,
                       "old_record": old_record, "switching_since": time.time()})
    pgu.write_postgres_record({**old_record, "service": NEW_SVC, "major": NEW})
    assert not machine.answers(5432)

    code = _upgrade(machine)
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.index("Undoing the switch") < out.index("Dumping") if "Dumping" in out else "Undoing the switch" in out
    assert _record()["postgres"]["major"] == NEW and machine.services[NEW_SVC]["port"] == 5432
    assert machine.services[OLD_SVC]["boot"] == "demand" and not machine.services[OLD_SVC]["running"]
    assert pgu.JOURNAL_KEY not in _record()


# -- refusals before anything changes ------------------------------------------------------------


def test_a_running_kazma_no_guard_can_stop_is_refused(machine, capsys):
    hold = _Hold(machine.events, refusal="Kazma is running and no guard supervises it")
    assert _upgrade(machine, hold=hold) == 2
    assert not any(c[:1] == ["winget"] for c in machine.calls) and machine.events == []
    assert _record()["postgres"]["major"] == OLD


def test_no_record_and_the_current_major_are_nothing_to_upgrade(machine, install, capsys):  # noqa: F811 - the imported fixture
    pgu.write_postgres_record({**_record()["postgres"], "major": NEW, "service": NEW_SVC})
    assert _upgrade(machine) == 0
    assert "already runs PostgreSQL" in capsys.readouterr().out
    (install / "kazma-data" / "setup_record.json").write_text("{}", encoding="utf-8")
    assert _upgrade(machine) == 2
    assert not any(c[:1] == ["winget"] for c in machine.calls)


def test_a_dry_run_changes_nothing(machine, capsys):
    assert _upgrade(machine, dry_run=True) == 0
    assert not any(c[:1] in (["winget"], ["icacls"]) or c[:2] in (["sc", "stop"], ["sc", "config"])
                   for c in machine.calls)
    assert pgu.JOURNAL_KEY not in _record() and machine.events == []
    assert f"PostgreSQL {OLD} -> {NEW}" in capsys.readouterr().out


# -- a repair keeps the major it has ---------------------------------------------------------------


def test_a_repair_of_a_16_install_repairs_16(machine, capsys):
    from kazma_cli.setup import _setup_postgres

    before = _record()["postgres"]
    assert _setup_postgres(_options(), run=machine.run) == 0, capsys.readouterr().out
    assert not any(c[:2] == ["winget", "install"] for c in machine.calls)
    after = _record()["postgres"]
    assert after["major"] == OLD and after["service"] == OLD_SVC
    assert after["installed_at"] == before["installed_at"] and after["wal_archive_dir"] == before["wal_archive_dir"]
    assert "--upgrade" in capsys.readouterr().out


# -- the plan on each platform ------------------------------------------------------------------------


@pytest.mark.parametrize("os_name,manager", [("windows", "winget"), ("debian", "apt"), ("fedora", "dnf"),
                                             ("macos", "brew")])
def test_the_switch_and_its_undo_on_each_platform(os_name, manager):
    p = Platform(os_name, manager)
    old = {"service": pgs.service_name(os_name, OLD), "major": OLD, "port": 5432}
    up = pgu.plan_upgrade(p, old, temp_port=55432)
    switch, undo = pgu.switch_steps(up), pgu.undo_steps(up)
    # The switch: the new server's port written before it stops; the old stopped before the new starts.
    actions = [(s.action or "cmd", s.major) for s in switch]
    assert actions[0][1] == NEW and actions[-1] == ("start", NEW)
    assert actions.index(("stop", OLD)) < len(actions) - 1
    assert switch[-1].port == 5432
    assert undo[-1].action == "start" and undo[-1].major == OLD and undo[-1].port == 5432
    text = " ".join(" ".join(s.argv) for s in switch + undo)
    if os_name == "debian":
        assert "pg_conftool" in text and f"/etc/postgresql/{OLD}/main/start.conf" in text
    if os_name == "fedora":
        assert f"systemctl disable postgresql-{OLD}" in text and f"systemctl enable postgresql-{OLD}" in text
    if os_name == "windows":
        assert f"sc config {OLD_SVC} start= demand" in text and f"sc config {OLD_SVC} start= auto" in text


def test_the_port_is_written_on_disk_and_replaces_an_earlier_one(tmp_path):
    (tmp_path / "postgresql.auto.conf").write_text("# keep\narchive_mode = 'on'\nport = '55432'\n", encoding="utf-8")
    pgs.set_port_on_disk(tmp_path, 5432)
    lines = (tmp_path / "postgresql.auto.conf").read_text(encoding="utf-8").splitlines()
    assert lines == ["# keep", "archive_mode = 'on'", "port = '5432'"]
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    pgs.set_port_on_disk(fresh, 55432)
    assert (fresh / "postgresql.auto.conf").read_text(encoding="utf-8").endswith("port = '55432'\n")


# -- each major's WAL archive is its own -----------------------------------------------------------------


def _base(root: Path, epoch: int, start_segment: str) -> Path:
    folder = root / str(epoch)
    folder.mkdir(parents=True)
    label = f"START WAL LOCATION: 32/AB000028 (file {start_segment})\n".encode()
    with tarfile.open(folder / "base.tar.gz", "w:gz") as tar:
        info = tarfile.TarInfo("backup_label")
        info.size = len(label)
        tar.addfile(info, io.BytesIO(label))
    return folder


def test_the_new_majors_segments_survive_the_old_majors_base_backup(tmp_path):
    old_root, new_root, new_archive = tmp_path / "base", tmp_path / f"base-{NEW}", tmp_path / f"wal-{NEW}"
    _base(old_root, 1_790_000_000, "0000000100000032000000AB")         # 16: far into its WAL
    _base(new_root, 1_790_100_000, "000000010000000000000002")         # 18: from the start again
    new_archive.mkdir()
    for n in range(1, 6):
        (new_archive / f"00000001000000000000000{n}").write_bytes(b"x")
    assert wal_archive.prune(new_archive, new_root) == 1                 # only the segment before 18's base
    left = sorted(p.name for p in new_archive.iterdir())
    assert left == [f"00000001000000000000000{n}" for n in range(2, 6)]
    # The negative control: one shared base folder; 16's base made every 18 segment "older".
    assert wal_archive.prune(new_archive, old_root) == 4


def test_offsite_names_carry_the_major(tmp_path):
    archive, root = tmp_path / "wal", tmp_path / "base"
    archive.mkdir()
    (archive / "000000010000000000000001").write_bytes(b"x")
    _base(root, 1_790_100_000, "000000010000000000000001")
    sent = []

    class Provider:
        async def upload_file(self, local, remote):
            sent.append(remote)
            return {"ok": True}

    from unittest import mock

    with mock.patch("kazma_core.config_store.get_config_store") as store:
        store.return_value.get.return_value = []
        wal_archive._push_offsite(archive, root=root, provider=Provider(), pushed=set(), tag=f"pg{NEW}-")
    assert sent == [f"pgwal-pg{NEW}-000000010000000000000001", f"pgbase-pg{NEW}-1790100000.tar.gz"]


def test_a_restore_before_the_upgrade_reads_the_server_it_replaced():
    rec = {"major": NEW, "installed_at": time.mktime(time.strptime("2026-11-20 12:00", "%Y-%m-%d %H:%M")),
           "wal_archive_dir": "new", "previous": {"major": OLD, "wal_archive_dir": "old"}}
    assert wal_archive.generation_for("2026-11-20 11:00", rec)["major"] == OLD
    assert wal_archive.generation_for("2026-11-20 13:00", rec)["major"] == NEW
    assert wal_archive.generation_for("2026-11-20 11:00", {"major": OLD, "wal_archive_dir": "x"})["major"] == OLD


def test_a_fresh_install_records_its_majors_folders(install, monkeypatch, capsys):  # noqa: F811
    from kazma_cli.setup import _setup_postgres

    from tests.test_setup_postgres import _Runner

    assert _setup_postgres(_options(), run=_Runner(installed=False)) == 0
    rec = _record()["postgres"]
    assert rec["major"] == NEW and rec["offsite_tag"] == f"pg{NEW}-"
    assert rec["base_backup_dir"].endswith(f"base-{NEW}") and rec["wal_archive_dir"].endswith(f"-{NEW}")


# -- the guard leaves a journaled switch alone ---------------------------------------------------------


def test_the_guard_and_the_upgrade_hold_the_switch_for_the_same_time():
    from tests.test_guard_database_service import guard

    assert guard.DBSVC_UPGRADE_HOLD_S == pgu.SWITCH_HOLD_S


def test_the_guard_reads_a_journaled_switch_and_lets_it_lapse(tmp_path, monkeypatch):
    from tests.test_guard_database_service import guard

    monkeypatch.setattr(guard, "_data_dir_guess", lambda: tmp_path)
    rec = {"os": "windows", "service": NEW_SVC, "port": 5432}

    def write(since):
        (tmp_path / "setup_record.json").write_text(json.dumps({
            "postgres": rec, "postgres_upgrade": {"stage": "switching", "switching_since": since}}), encoding="utf-8")

    write(time.time())
    assert guard._setup_record().get("upgrading") is True
    write(time.time() - guard.DBSVC_UPGRADE_HOLD_S - 1)
    assert "upgrading" not in guard._setup_record()


def test_the_watch_neither_starts_nor_pages_during_a_switch(monkeypatch):
    from tests.test_guard_database_service import _Harness

    h = _Harness(monkeypatch, record={"os": "windows", "service": OLD_SVC, "port": 5432, "upgrading": True})
    h.run(300)
    h.running = h.answers = False
    h.run(900)
    assert h.starts() == 0 and h.pages == []
    # The negative control: the same outage with no switch journaled is started and paged.
    h2 = _Harness(monkeypatch, record={"os": "windows", "service": OLD_SVC, "port": 5432})
    h2.run(300)
    h2.running = h2.answers = False
    h2.run(900)
    assert h2.starts() == 1 and len(h2.pages) == 1


# -- a Kazma that is not running (the drill VM, 2026-11-13) --------------------------------------
#
# On the drill VM no Kazma ran. The finish still waited to see Kazma sign in to
# the new server, gave up after two minutes, returned 1 before the first base
# backup and left the journal: a second run waited again.


def test_with_no_kazma_running_the_upgrade_finishes_without_waiting_for_it(machine, monkeypatch, capsys):
    base_backups = []
    monkeypatch.setattr("kazma_cli.setup._kazma_signed_in", lambda url, **kw: False)   # nobody connects
    monkeypatch.setattr(wal_archive, "_base_backup", lambda rec, pw, **kw: base_backups.append(pw) or Path("1"))
    hold = _Hold(machine.events, running=False)
    assert _upgrade(machine, hold=hold) == 0, capsys.readouterr().out
    assert base_backups == [SUPER_PW] and pgu.JOURNAL_KEY not in _record()
    assert "resume" not in machine.events
    capsys.readouterr()
    assert _upgrade(machine, hold=_Hold(machine.events, running=False)) == 0
    assert "already runs PostgreSQL" in capsys.readouterr().out


def test_a_running_kazma_must_still_be_seen_on_the_new_server(machine, monkeypatch, capsys):
    """The other side: Kazma was running, and it never signs in to 18."""
    monkeypatch.setattr("kazma_cli.setup._kazma_signed_in", lambda url, **kw: False)
    assert _upgrade(machine) == 1
    assert "was not seen signed in" in capsys.readouterr().out
    assert _record()[pgu.JOURNAL_KEY]["stage"] == "switched"       # the next run finishes it


def test_a_finish_picked_up_by_a_later_run_takes_the_base_backup(machine, monkeypatch, capsys):
    from kazma_cli import postgres_upgrade as cli

    base_backups = []
    monkeypatch.setattr(wal_archive, "_base_backup", lambda rec, pw, **kw: base_backups.append(pw) or Path("1"))
    rec = _record()["postgres"]
    pgu.write_postgres_record({**rec, "major": NEW, "service": NEW_SVC, "previous": rec})
    pgu.write_journal({"stage": "switched", "from": OLD, "to": NEW, "port": 5432, "temp_port": 55432,
                       "old_record": rec})
    assert cli._finish(pgu.journal(), _Hold(machine.events, running=False), run=machine.run) == 0
    assert base_backups == [SUPER_PW] and pgu.JOURNAL_KEY not in _record()
