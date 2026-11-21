"""The data folder: Kazma's database and engine off Windows' own drive (2026-11-21).

The 19 Nov reset lost the database: ``kazma setup postgres`` always put it on
C: (Program Files, its WAL archive in ProgramData) and the engine's disk in
``%LOCALAPPDATA%``, while the install folder with ``.env`` and the dumps was on
another drive. Held here, on a fake Windows machine whose Postgres service,
ACL tool, WSL registration and engine task keep their own state, with real
folders for the data:

* a folder that cannot hold it is refused before anything changes: Windows'
  own drive, a removable, network or optical drive, a ``subst`` drive, a
  share, a relative path, characters the archive command cannot take, a
  folder inside Kazma's install or data folder, too little free space, not
  Windows;
* a new cluster, its WAL archive and base backups go in the chosen folder (the
  installer's ``--datadir``), restricted, and the record keeps it; a repair and
  a port change write in that folder (the control: the choice not read puts
  it under Program Files);
* after a reset the kept database is attached to the freshly installed
  service, signed in to with its own superuser password (the control: a run
  that knows no folder installs an empty one);
* the move of an installed database: the service disabled while its data is
  copied, every file compared, the copy's archive_command and the service's
  ``-D`` pointed at the copy, started from it, Kazma signs in; the old folders
  kept; the record names the new ones. A copy that differs, a server that
  does not start from the copy, a run that died: put back as it was (the
  controls: no comparison uses the bad copy; no journal leaves the service on
  half a copy);
* the engine's disk: its task disabled, the distro stopped, hashed, moved by
  WSL, hashed again, started; a disk WSL did not move whole is moved back
  (the control: no hash check keeps it); a disk kept from before a reset is
  registered in place (the control: a fresh import over it);
* the guard leaves a moving part alone for a bounded time, and acts again
  after (the control: no journal, the service is started and paged).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core import data_folder as df
from kazma_core import data_folder_move as dfm
from kazma_core import postgres_service as pgs
from kazma_core.postgres_service import PG_MAJOR, Platform

from tests.test_install_modes import box  # noqa: F401 - the fixture
from tests.test_setup_postgres import _options, install  # noqa: F401 - the fixture

SVC = f"postgresql-x64-{PG_MAJOR}"
APP_PW = "App-Pw-Kept-7"
SUPER_PW = "Super-Kept-3"
FIXED = df.Disk(drive_type=lambda root: df.DRIVE_FIXED, dos_device=lambda d: r"\Device\HarddiskVolume3",
                free_bytes=lambda p: 500 * 2**30, system_drive="Z:")


@pytest.fixture(autouse=True)
def _own_record(tmp_path, monkeypatch):
    """Each test's own data folder, so its setup record is its own (the
    session's is every test's)."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "kazma-data"))


# -- the folder's refusals ---------------------------------------------------------------------------------


def _disk(**kw) -> df.Disk:
    base = dict(drive_type=lambda root: df.DRIVE_FIXED, dos_device=lambda d: r"\Device\HarddiskVolume3",
                free_bytes=lambda p: 500 * 2**30, system_drive="C:")
    base.update(kw)
    return df.Disk(**base)


@pytest.mark.parametrize("text, disk, want", [
    (r"C:\Kazma", _disk(), "Windows' own drive"),
    (r"E:\Kazma", _disk(drive_type=lambda r: df.DRIVE_REMOVABLE), "removable"),
    (r"E:\Kazma", _disk(drive_type=lambda r: df.DRIVE_REMOTE), "network drive"),
    (r"E:\Kazma", _disk(drive_type=lambda r: df.DRIVE_CDROM), "optical"),
    (r"E:\Kazma", _disk(drive_type=lambda r: 1), "not there"),
    (r"S:\Kazma", _disk(dos_device=lambda d: r"\??\C:\Users\me\fake"), "subst drive"),
    (r"\\nas\share\Kazma", _disk(), "network or device path"),
    ("Kazma", _disk(), "not a full path"),
    (r"D:\Kazma%data", _disk(), "letters, digits"),
    (r"D:\Kazma\x.", _disk(), "not end with"),
    (r"D:\kazma\pg", _disk(), "install folder"),
    (r"D:\data\inside", _disk(), "data folder"),
])
def test_a_folder_that_cannot_hold_it_is_refused(text, disk, want):
    problem = df.folder_problem(df.normalized(text), disk=disk, install_root=Path(r"D:\kazma"),
                                data_dir=Path(r"D:\data"))
    assert problem and want in problem, problem


def test_too_little_room_and_not_windows_are_refused():
    small = _disk(free_bytes=lambda p: 3 * 2**30)
    assert "3.0 GB free" in df.folder_problem(r"D:\Kazma", disk=small, need_bytes=10 * 2**30)
    assert "for Windows" in df.folder_problem(r"D:\Kazma", disk=_disk(), os_name="debian")


def test_a_good_folder_passes_and_a_drive_alone_means_its_kazma_folder():
    assert df.folder_problem(r"D:\Kazma", disk=_disk(), install_root=Path(r"E:\kazma"),
                             data_dir=Path(r"E:\kazma\kazma-data"), need_bytes=10 * 2**30) is None
    assert df.normalized("d:") == r"D:\Kazma" and df.normalized("D:\\") == r"D:\Kazma"
    assert df.normalized("d:/Data/Kazma/") == r"D:\Data\Kazma"


# -- the Postgres installer and the record -----------------------------------------------------------------


def _plan(**kw):
    return pgs.setup_plan(Platform("windows", "winget"), superuser_password="s", throwaway_password="t",
                          app_password="a", role_sql="", **kw)


def test_the_installer_makes_the_cluster_in_the_chosen_folder():
    override = _plan(data_dir=r"D:\Kazma\postgres-18\data")[0].argv
    assert '--datadir "D:\\Kazma\\postgres-18\\data"' in override[-1]
    # The control: with no data folder the installer puts it under Program Files.
    assert "--datadir" not in _plan()[0].argv[-1]
    with pytest.raises(ValueError):
        pgs.setup_plan(Platform("debian", "apt"), superuser_password="s", throwaway_password="t",
                       app_password="a", role_sql="", data_dir="/x")


def test_the_service_command_line_names_its_data_folder():
    cmd = rf'"C:\Program Files\PostgreSQL\18\bin\pg_ctl.exe" runservice -N "{SVC}" -D "C:\Program Files\PostgreSQL\18\data" -w'
    assert pgs.data_dir_in_command(cmd) == r"C:\Program Files\PostgreSQL\18\data"
    moved = pgs.with_data_dir(cmd, r"D:\Kazma\postgres-18\data")
    assert pgs.data_dir_in_command(moved) == r"D:\Kazma\postgres-18\data"
    assert moved.endswith(" -w") and moved.startswith('"C:\\Program Files\\PostgreSQL\\18\\bin\\pg_ctl.exe"')


def test_a_port_change_is_written_in_the_data_folder_the_step_names(tmp_path):
    from kazma_cli.setup import _do_setup_step

    folder = tmp_path / "postgres-18" / "data"
    folder.mkdir(parents=True)
    step = pgs.port_step(Platform("windows", "winget"), PG_MAJOR, 55432, str(folder))
    assert _do_setup_step(step, Platform("windows", "winget"), SVC, 5432, [])[0]
    assert "port = '55432'" in (folder / "postgresql.auto.conf").read_text(encoding="utf-8")
    # The control: a step that names no folder writes to the package's own (Program Files).
    assert pgs.port_step(Platform("windows", "winget"), PG_MAJOR, 55432).data_dir == ""


class _Windows:
    """A Windows machine as ``sc``, winget, icacls, PowerShell's service
    change, WSL and Task Scheduler see it. The Postgres service runs from the
    folder its command line's ``-D`` names (it writes ``postmaster.pid``
    there) and answers on its port while it runs."""

    def __init__(self, tmp_path: Path, *, installed: bool = True, data: Path | None = None) -> None:
        self.tmp = tmp_path
        self.calls: list[list[str]] = []
        self.service: dict | None = None
        if installed:
            self.service = dict(command=self.command(data), account="NT AUTHORITY\\NetworkService",
                                running=True, start="auto")
            self._pid()
        self.fail_start_from: set[str] = set()
        self.sign_in_refusal: str | None = None
        self.registry: dict[str, tuple[Path, str]] = {}
        self.distro_running = False
        self.task = dict(enabled=True, running=False)
        self.move_corrupts = False
        self.move_fails = False
        self.imports: list[list[str]] = []

    def command(self, data: Path | None) -> str:
        folder = data or self.tmp / "C" / "Program Files" / "PostgreSQL" / str(PG_MAJOR) / "data"
        return rf'"C:\PG\bin\pg_ctl.exe" runservice -N "{SVC}" -D "{folder}" -w'

    def data(self) -> Path:
        return Path(pgs.data_dir_in_command(self.service["command"]))

    def _pid(self) -> None:
        if (self.data() / "PG_VERSION").is_file():
            (self.data() / "postmaster.pid").write_text("1\n", encoding="ascii")

    def answers(self, port: int) -> bool:
        return bool(self.service and self.service["running"])

    def sign_in(self, url: str) -> str | None:
        return self.sign_in_refusal

    def location(self, name: str):
        return self.registry.get(name)

    def _ok(self, argv, out: str = "", code: int = 0):
        return subprocess.CompletedProcess(argv, code, out, "")

    def run(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        head = argv[0].lower()
        if head == "winget":
            override = argv[argv.index("--override") + 1]
            found = re.search(r'--datadir "([^"]+)"', override)
            data = Path(found.group(1)) if found else None
            if data is not None:
                data.mkdir(parents=True, exist_ok=True)
                (data / "PG_VERSION").write_text(f"{PG_MAJOR}\n", encoding="ascii")
            else:
                default = self.tmp / "C" / "Program Files" / "PostgreSQL" / str(PG_MAJOR) / "data"
                default.mkdir(parents=True, exist_ok=True)
                (default / "PG_VERSION").write_text(f"{PG_MAJOR}\n", encoding="ascii")
            self.service = dict(command=self.command(data), account="NT AUTHORITY\\NetworkService",
                                running=True, start="auto")
            self._pid()
            return self._ok(argv)
        if head == "sc" and len(argv) >= 3:
            return self._sc(argv)
        if head == "powershell" and "Win32_Service" in argv[-1]:
            found = re.search(r"PathName='((?:[^']|'')*)'", argv[-1])
            if self.service is None or not found:
                return self._ok(argv, "no service", 1)
            self.service["command"] = found.group(1).replace("''", "'")
            return self._ok(argv)
        if head == "whoami":
            return self._ok(argv, '"pc\\me","S-1-5-21-11-22-33-1001"\n')
        if head == "wsl.exe":
            return self._wsl(argv)
        if head == "schtasks":
            return self._schtasks(argv)
        if head == "docker":
            return self._ok(argv, "29.0.0\n") if self.distro_running else self._ok(argv, "", 1)
        return self._ok(argv)

    def _sc(self, argv):
        verb, svc = argv[1], self.service
        if svc is None:
            return self._ok(argv, "FAILED 1060", 1060)
        if verb == "query":
            return self._ok(argv, "STATE : 4  RUNNING" if svc["running"] else "STATE : 1  STOPPED")
        if verb == "qc":
            start = {"auto": "2   AUTO_START", "disabled": "4   DISABLED"}.get(svc["start"], "3   DEMAND_START")
            return self._ok(argv, f"SERVICE_NAME: {SVC}\n        START_TYPE         : {start}\n"
                                  f"        BINARY_PATH_NAME   : {svc['command']}\n"
                                  f"        SERVICE_START_NAME : {svc['account']}\n")
        if verb == "qfailure":
            return self._ok(argv, "FAILURE_ACTIONS : RESTART -- Delay = 5000")
        if verb == "stop":
            svc["running"] = False
            (self.data() / "postmaster.pid").unlink(missing_ok=True)
            return self._ok(argv)
        if verb == "start":
            if svc["start"] == "disabled":
                return self._ok(argv, "FAILED 1058", 1058)
            if str(self.data()) in self.fail_start_from or not (self.data() / "PG_VERSION").is_file():
                return self._ok(argv, "FAILED 1067", 1067)
            svc["running"] = True
            self._pid()
            return self._ok(argv)
        if verb == "config" and "start=" in argv:
            svc["start"] = argv[argv.index("start=") + 1]
            return self._ok(argv)
        return self._ok(argv)

    def _wsl(self, argv):
        args = argv[1:]
        if args == ["--version"]:
            return self._ok(argv, "WSL version: 2.6.1.0\n")
        if args[:2] == ["-l", "-q"]:
            names = [n for n in self.registry if self.distro_running] if "--running" in args else list(self.registry)
            return self._ok(argv, "\n".join(names))
        if args[:1] == ["--terminate"]:
            self.distro_running = False
            return self._ok(argv)
        if args[:1] == ["--manage"] and "--move" in args:
            if self.distro_running or self.move_fails:
                return self._ok(argv, "The operation could not be completed", 1)
            name, dst = args[1], Path(args[args.index("--move") + 1])
            base, vhd = self.registry[name]
            dst.mkdir(parents=True, exist_ok=True)
            data = (base / vhd).read_bytes()
            (dst / vhd).write_bytes(data + (b"x" if self.move_corrupts else b""))
            (base / vhd).unlink()
            self.registry[name] = (dst, vhd)
            return self._ok(argv)
        if args[:1] == ["--import-in-place"]:
            self.imports.append(args)
            self.registry[args[1]] = (Path(args[2]).parent, Path(args[2]).name)
            return self._ok(argv)
        if args[:1] == ["--import"]:
            self.imports.append(args)
            return self._ok(argv)
        return self._ok(argv)

    def _schtasks(self, argv):
        if "/Change" in argv:
            self.task["enabled"] = "/ENABLE" in argv
        elif "/End" in argv:
            self.task["running"] = False
        elif "/Run" in argv:
            if not self.task["enabled"]:
                return self._ok(argv, "ERROR: The task is disabled", 1)
            self.task["running"] = True
            self.distro_running = bool(self.registry)
        return self._ok(argv)

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def _machine(win: _Windows, record_path: Path, said: list[str]) -> dfm.Machine:
    return dfm.Machine(run=win.run, disk=FIXED, answers=win.answers, sign_in=win.sign_in,
                       distro_location=win.location, sleep=lambda s: None, say=said.append,
                       record_path=record_path, user="*S-1-5-21-11-22-33-1001")


# -- a new cluster in the chosen folder --------------------------------------------------------------------


@pytest.fixture
def windows(install, monkeypatch, tmp_path):  # noqa: F811 - the imported fixture
    """A fresh Windows: no Postgres service yet, the install's .env and the
    superuser's password as a first setup leaves them."""
    win = _Windows(tmp_path, installed=False)
    monkeypatch.setattr(pgs, "wait_for_port", lambda port, **kw: win.answers(port))
    monkeypatch.setattr(pgs, "_port_answers", lambda port, host="127.0.0.1", timeout=2.0: win.answers(port))
    monkeypatch.setattr(pgs, "restart_service", lambda os_name, service, port, **kw: (
        win.run(["sc", "stop", service]), win.run(["sc", "start", service]), win.answers(port))[-1])
    monkeypatch.setattr(pgs, "time", SimpleNamespace(sleep=lambda s: None, time=time.time))
    return win


def test_a_new_install_puts_the_database_and_its_backups_in_the_data_folder(windows, install, tmp_path, capsys):  # noqa: F811 - the imported fixture
    from kazma_cli.setup import _setup_postgres

    folder = tmp_path / "D" / "Kazma"
    df.record_choice(folder)
    assert _setup_postgres(_options(), run=windows.run) == 0, capsys.readouterr().out
    layout = df.Layout(folder)
    assert pgs.data_dir_in_command(windows.service["command"]) == str(layout.postgres_data(PG_MAJOR))
    # Each folder restricted before anything is in it; the archive's own rights from wal_archive.
    restricted = {c[1] for c in windows.ran("icacls") if "/inheritance:r" in c}
    assert {str(folder), str(layout.postgres(PG_MAJOR)), str(layout.base_backups(PG_MAJOR)),
            str(layout.wal_archive(PG_MAJOR))} <= restricted
    psql = next(c for c in windows.calls if c[0].endswith("psql.exe"))
    assert psql  # the settings were made
    rec = pgs.read_record()["postgres"]
    assert rec["data_dir"] == str(layout.postgres_data(PG_MAJOR))
    assert rec["wal_archive_dir"] == str(layout.wal_archive(PG_MAJOR))
    assert rec["base_backup_dir"] == str(layout.base_backups(PG_MAJOR))
    assert df.chosen() == folder


def test_control_without_the_choice_read_the_database_goes_on_windows_own_drive(windows, install, tmp_path,  # noqa: F811 - the imported fixture
                                                                                 monkeypatch):
    from kazma_cli.setup import _setup_postgres

    df.record_choice(tmp_path / "D" / "Kazma")
    monkeypatch.setattr(df, "chosen", lambda path=None: None)
    assert _setup_postgres(_options(), run=windows.run) == 0
    assert "Program Files" in pgs.data_dir_in_command(windows.service["command"])
    assert "ProgramData" in pgs.read_record()["postgres"]["wal_archive_dir"]


def test_a_repair_keeps_the_database_where_it_is(windows, install, tmp_path):  # noqa: F811 - the imported fixture
    from kazma_cli.setup import _setup_postgres

    folder = tmp_path / "D" / "Kazma"
    df.record_choice(folder)
    assert _setup_postgres(_options(), run=windows.run) == 0
    first = pgs.read_record()["postgres"]
    windows.calls.clear()
    assert _setup_postgres(_options(), run=windows.run) == 0
    assert not windows.ran("winget")
    again = pgs.read_record()["postgres"]
    for key in ("data_dir", "wal_archive_dir", "base_backup_dir", "installed_at"):
        assert again[key] == first[key], key


def test_files_where_the_cluster_would_go_are_never_installed_over(windows, install, tmp_path, capsys):  # noqa: F811 - the imported fixture
    from kazma_cli.setup import _setup_postgres

    folder = tmp_path / "D" / "Kazma"
    stray = df.Layout(folder).postgres_data(PG_MAJOR)
    stray.mkdir(parents=True)
    (stray / "notes.txt").write_text("mine", encoding="utf-8")
    df.record_choice(folder)
    assert _setup_postgres(_options(), run=windows.run) == 2
    assert not windows.ran("winget") and "and no database" in capsys.readouterr().out


# -- after a reset: the kept database attached again ---------------------------------------------------------


def _kept_cluster(folder: Path) -> Path:
    data = df.Layout(folder).postgres_data(PG_MAJOR)
    (data / "base" / "1").mkdir(parents=True)
    (data / "PG_VERSION").write_text(f"{PG_MAJOR}\n", encoding="ascii")
    (data / "base" / "1" / "1259").write_bytes(b"kept rows" * 100)
    (data / "postgresql.auto.conf").write_text("archive_command = 'copy old'\n", encoding="utf-8")
    return data


def test_after_a_reset_the_kept_database_is_attached_to_the_new_service(windows, install, tmp_path, capsys):  # noqa: F811 - the imported fixture
    from kazma_cli.setup import _setup_postgres

    folder = tmp_path / "D" / "Kazma"
    data = _kept_cluster(folder)
    df.record_choice(folder)
    (install / ".kazma" / "postgres_superuser.pass").write_text(SUPER_PW + "\n", encoding="utf-8")
    (install / ".env").write_text(f"KAZMA_DATABASE_URL={pgs.database_url(APP_PW, 5432)}\n", encoding="utf-8")

    assert _setup_postgres(_options(), run=windows.run) == 0, capsys.readouterr().out
    # The installer made its own empty cluster under Program Files; the service now runs the kept one.
    assert pgs.data_dir_in_command(windows.service["command"]) == str(data)
    assert (data / "postmaster.pid").is_file() and (data / "base" / "1" / "1259").read_bytes().startswith(b"kept")
    archive = df.Layout(folder).wal_archive(PG_MAJOR)
    assert str(archive) in (data / "postgresql.auto.conf").read_text(encoding="utf-8").replace("\\\\", "\\")
    # Its settings were made signing in with the kept database's own superuser password.
    after_point = [c for c in windows.calls if c[0].endswith("psql.exe")]
    assert after_point
    rec = pgs.read_record()["postgres"]
    assert rec["data_dir"] == str(data) and rec["wal_archive_dir"] == str(archive)
    assert "is attached" in capsys.readouterr().out
    # The .env Kazma signs in with is the one it had.
    assert APP_PW in (install / ".env").read_text(encoding="utf-8")


def test_reattach_signs_in_with_the_kept_superuser_password(install, tmp_path, monkeypatch):  # noqa: F811 - the imported fixture
    from kazma_cli import setup

    folder = tmp_path / "D" / "Kazma"
    _kept_cluster(folder)
    df.record_choice(folder)
    steps = setup._reattach_steps(Platform("windows", "winget"), PG_MAJOR, 5432,
                                  data_dir=str(df.Layout(folder).postgres_data(PG_MAJOR)),
                                  superuser_password=SUPER_PW, throwaway="throwaway-1",
                                  app_password=APP_PW, archive_command="copy x")
    actions = [s.action for s in steps]
    point = actions.index("point_data_dir")
    assert actions.index("wait") < point
    settings = [s for s in steps[point:] if s.env.get("PGPASSWORD")]
    assert settings and {s.env["PGPASSWORD"] for s in settings} == {SUPER_PW}
    assert not any(s.argv[:2] == ("winget", "install") for s in steps[point:])


def test_control_a_run_that_knows_no_folder_installs_an_empty_database(windows, install, tmp_path, monkeypatch):  # noqa: F811 - the imported fixture
    from kazma_cli.setup import _setup_postgres

    folder = tmp_path / "D" / "Kazma"
    data = _kept_cluster(folder)
    df.record_choice(folder)
    (install / ".kazma" / "postgres_superuser.pass").write_text(SUPER_PW + "\n", encoding="utf-8")
    monkeypatch.setattr(df, "chosen", lambda path=None: None)
    assert _setup_postgres(_options(), run=windows.run) == 0
    assert pgs.data_dir_in_command(windows.service["command"]) != str(data)


def test_a_kept_database_whose_password_is_lost_is_not_touched(windows, install, tmp_path, capsys):  # noqa: F811 - the imported fixture
    from kazma_cli.setup import _setup_postgres

    folder = tmp_path / "D" / "Kazma"
    _kept_cluster(folder)
    df.record_choice(folder)
    assert _setup_postgres(_options(), run=windows.run) == 2
    assert not windows.ran("winget") and "superuser's password" in capsys.readouterr().out


# -- the move of an installed database --------------------------------------------------------------------


def _installed(tmp_path: Path) -> tuple[_Windows, Path, Path, Path]:
    data = tmp_path / "C" / "Program Files" / "PostgreSQL" / str(PG_MAJOR) / "data"
    (data / "base" / "1").mkdir(parents=True)
    (data / "pg_wal").mkdir()
    (data / "PG_VERSION").write_text(f"{PG_MAJOR}\n", encoding="ascii")
    (data / "base" / "1" / "1259").write_bytes(os.urandom(70_000))
    (data / "pg_wal" / "000000010000000000000003").write_bytes(os.urandom(4096))
    archive = tmp_path / "C" / "ProgramData" / "Kazma" / f"pg_wal_archive-{PG_MAJOR}"
    archive.mkdir(parents=True)
    (archive / "000000010000000000000002").write_bytes(os.urandom(4096))
    (data / "postgresql.auto.conf").write_text(
        f"archive_mode = 'on'\narchive_command = 'copy /Y \"%p\" \"{archive}\\\\%f.part\"'\n", encoding="utf-8")
    base = tmp_path / "C" / "kazma-data" / "backups" / "pg" / f"base-{PG_MAJOR}"
    (base / "1790000000").mkdir(parents=True)
    (base / "1790000000" / "base.tar.gz").write_bytes(os.urandom(9000))
    win = _Windows(tmp_path, data=data)
    return win, data, archive, base


def _record(record_path: Path, archive: Path, base: Path) -> None:
    pgs.write_record(pgs.PostgresRecord(
        os="windows", manager="winget", service=SVC, port=5432, bin_dir=r"C:\PG\bin", major=PG_MAJOR,
        wal_archive_dir=str(archive), base_backup_dir=str(base), installed_at=1_790_000_000.0), record_path)


def _postgres_move(tmp_path: Path):
    win, data, archive, base = _installed(tmp_path)
    record_path = tmp_path / "setup_record.json"
    _record(record_path, archive, base)
    said: list[str] = []
    machine = _machine(win, record_path, said)
    folder = tmp_path / "D" / "Kazma"
    rec = pgs.read_record(record_path)["postgres"]
    part, refusals, _already = dfm._postgres_part(rec, df.Layout(folder), machine)
    assert part is not None and refusals == []
    return win, machine, part, folder, (data, archive, base), said


def test_the_database_moves_copied_compared_switched_and_recorded(tmp_path, monkeypatch):
    win, machine, part, folder, (data, archive, base), said = _postgres_move(tmp_path)
    before = dfm._manifest(data)
    layout = df.Layout(folder)
    disabled_during_copy = []
    copy = dfm._copy_tree

    def watching_copy(src, dst):
        disabled_during_copy.append((win.service["start"], win.service["running"]))
        copy(src, dst)

    monkeypatch.setattr(dfm, "_copy_tree", watching_copy)
    assert dfm._move_postgres(part, "postgresql://kazma_app:x@127.0.0.1:5432/kazma", folder=folder,
                              machine=machine), said
    # Disabled and stopped while every copy was made: nothing could start it on half a copy.
    assert disabled_during_copy and set(disabled_during_copy) == {("disabled", False)}
    new_data = layout.postgres_data(PG_MAJOR)
    assert pgs.data_dir_in_command(win.service["command"]) == str(new_data)
    assert win.service["start"] == "auto" and win.service["running"]
    assert (new_data / "postmaster.pid").is_file() and not (data / "postmaster.pid").exists()
    copied = dfm._manifest(new_data)
    copied.pop("postmaster.pid")
    copied.pop("postgresql.auto.conf")
    assert copied == {k: v for k, v in before.items() if k not in ("postmaster.pid", "postgresql.auto.conf")}
    assert dfm._manifest(layout.wal_archive(PG_MAJOR)) == dfm._manifest(archive)
    assert dfm._manifest(layout.base_backups(PG_MAJOR)) == dfm._manifest(base)
    conf = (new_data / "postgresql.auto.conf").read_text(encoding="utf-8")
    assert str(layout.wal_archive(PG_MAJOR)).replace("\\", "\\\\") in conf and str(archive) not in conf
    # The old folders are kept (the way back), the record names the new ones, the journal is gone.
    assert (data / "PG_VERSION").is_file() and any(archive.iterdir()) and any(base.iterdir())
    rec = pgs.read_record(machine.record_path)["postgres"]
    assert rec["data_dir"] == str(new_data)
    assert rec["wal_archive_dir"] == str(layout.wal_archive(PG_MAJOR))
    assert rec["base_backup_dir"] == str(layout.base_backups(PG_MAJOR))
    assert df.journal(machine.record_path) == {}
    restricted = {c[1] for c in win.ran("icacls") if "/inheritance:r" in c}
    assert {str(folder), str(layout.postgres(PG_MAJOR)), str(new_data)} <= restricted
    grants = next(c for c in win.ran("icacls") if c[1] == str(new_data))
    assert "*S-1-5-20:(OI)(CI)F" in grants and not any("S-1-5-21" in g for g in grants)


def test_a_copy_that_differs_is_put_back_as_it_was(tmp_path, monkeypatch):
    win, machine, part, folder, (data, _archive, _base), said = _postgres_move(tmp_path)
    copy = dfm._copy_tree

    def bad_copy(src, dst):
        copy(src, dst)
        victim = dst / "base" / "1" / "1259"
        if victim.is_file():
            victim.write_bytes(b"torn")

    monkeypatch.setattr(dfm, "_copy_tree", bad_copy)
    assert not dfm._move_postgres(part, "url", folder=folder, machine=machine)
    assert pgs.data_dir_in_command(win.service["command"]) == str(data)
    assert win.service["start"] == "auto" and win.service["running"] and (data / "postmaster.pid").is_file()
    assert not df.Layout(folder).postgres(PG_MAJOR).exists()        # what this run made, removed
    assert pgs.read_record(machine.record_path)["postgres"].get("data_dir", "") == ""
    assert df.journal(machine.record_path) == {}
    assert any("not whole" in line for line in said)


def test_control_without_the_comparison_the_torn_copy_is_used(tmp_path, monkeypatch):
    win, machine, part, folder, _old, _said = _postgres_move(tmp_path)
    copy = dfm._copy_tree

    def bad_copy(src, dst):
        copy(src, dst)
        victim = dst / "base" / "1" / "1259"
        if victim.is_file():
            victim.write_bytes(b"torn")

    monkeypatch.setattr(dfm, "_copy_tree", bad_copy)
    monkeypatch.setattr(dfm, "differences", lambda src, dst: [])
    assert dfm._move_postgres(part, "url", folder=folder, machine=machine)
    new_data = df.Layout(folder).postgres_data(PG_MAJOR)
    assert pgs.data_dir_in_command(win.service["command"]) == str(new_data)
    assert (new_data / "base" / "1" / "1259").read_bytes() == b"torn"


@pytest.mark.parametrize("fault", ["no_start", "sign_in"])
def test_a_server_that_is_not_usable_from_the_copy_is_put_back(tmp_path, fault):
    win, machine, part, folder, (data, _archive, _base), said = _postgres_move(tmp_path)
    if fault == "no_start":
        win.fail_start_from.add(str(df.Layout(folder).postgres_data(PG_MAJOR)))
    else:
        win.sign_in_refusal = "password authentication failed"
    assert not dfm._move_postgres(part, "url", folder=folder, machine=machine)
    assert pgs.data_dir_in_command(win.service["command"]) == str(data)
    assert win.service["running"] and win.service["start"] == "auto" and (data / "postmaster.pid").is_file()
    assert df.journal(machine.record_path) == {}


def test_a_run_that_died_mid_move_is_put_back_by_the_next(tmp_path):
    win, machine, part, folder, (data, _archive, _base), said = _postgres_move(tmp_path)

    def dies(line: str) -> None:
        said.append(line)
        if line.startswith("-> Starting"):
            raise KeyboardInterrupt

    machine.say = dies
    with pytest.raises(KeyboardInterrupt):
        dfm._move_postgres(part, "url", folder=folder, machine=machine)
    assert df.journal(machine.record_path)["stage"] == "starting"
    assert pgs.data_dir_in_command(win.service["command"]) != str(data)     # left on the copy
    machine.say = said.append
    assert dfm.recover(machine) == "_put_back"
    assert pgs.data_dir_in_command(win.service["command"]) == str(data)
    assert win.service["running"] and win.service["start"] == "auto"
    assert df.journal(machine.record_path) == {}


def test_control_without_the_journal_a_dead_run_leaves_the_service_on_half_a_copy(tmp_path):
    win, machine, part, folder, (data, _archive, _base), said = _postgres_move(tmp_path)

    def dies(line: str) -> None:
        if line.startswith("-> Starting"):
            raise KeyboardInterrupt

    machine.say = dies
    with pytest.raises(KeyboardInterrupt):
        dfm._move_postgres(part, "url", folder=folder, machine=machine)
    df.clear_journal(machine.record_path)
    machine.say = said.append
    assert dfm.recover(machine) == "none"
    assert pgs.data_dir_in_command(win.service["command"]) != str(data)
    assert win.service["start"] != "auto" or not win.service["running"]


def test_targets_that_hold_files_and_links_refuse_the_move(tmp_path):
    win, data, archive, base = _installed(tmp_path)
    record_path = tmp_path / "setup_record.json"
    _record(record_path, archive, base)
    machine = _machine(win, record_path, [])
    folder = tmp_path / "D" / "Kazma"
    occupied = df.Layout(folder).wal_archive(PG_MAJOR)
    occupied.mkdir(parents=True)
    (occupied / "x").write_text("y", encoding="utf-8")
    rec = pgs.read_record(record_path)["postgres"]
    _part, refusals, _ = dfm._postgres_part(rec, df.Layout(folder), machine)
    assert any("holds files already" in r for r in refusals)


# -- the engine's disk ---------------------------------------------------------------------------------------


@pytest.fixture
def engine(tmp_path, monkeypatch):
    win = _Windows(tmp_path, installed=False)
    old = tmp_path / "C" / "AppData" / "Local" / "Kazma" / "engine" / "kazma-engine"
    old.mkdir(parents=True)
    (old / "ext4.vhdx").write_bytes(os.urandom(200_000))
    win.registry["kazma-engine"] = (old, "ext4.vhdx")
    win.distro_running = True
    win.task["running"] = True
    monkeypatch.setattr("kazma_core.docker_cli.find_docker_cli", lambda: "docker")
    monkeypatch.setattr(dfm, "ENGINE_WAIT_S", 6.0)
    record_path = tmp_path / "setup_record.json"
    said: list[str] = []
    machine = _machine(win, record_path, said)
    folder = tmp_path / "D" / "Kazma"
    part, refusals, _ = dfm._engine_part(df.Layout(folder), machine)
    assert part is not None and refusals == []
    return SimpleNamespace(win=win, machine=machine, part=part, folder=folder, old=old, said=said)


def test_the_engine_disk_moves_and_is_checked(engine):
    e = engine
    original = (e.old / "ext4.vhdx").read_bytes()
    states = []
    run = e.win.run

    def watching(argv, **kw):
        if argv[:2] == ["wsl.exe", "--manage"]:
            states.append((e.win.task["enabled"], e.win.distro_running))
        return run(argv, **kw)

    e.machine.run = watching
    assert dfm._move_engine(e.part, folder=e.folder, machine=e.machine), e.said
    assert states == [(False, False)]                 # the task off and the distro stopped while it moved
    new = df.Layout(e.folder).engine
    assert e.win.registry["kazma-engine"][0] == new
    assert (new / "ext4.vhdx").read_bytes() == original and not (e.old / "ext4.vhdx").exists()
    assert e.win.task["enabled"] and e.win.distro_running
    assert df.journal(e.machine.record_path) == {}


def test_a_disk_wsl_did_not_move_whole_is_moved_back(engine):
    e = engine
    e.win.move_corrupts = True
    assert not dfm._move_engine(e.part, folder=e.folder, machine=e.machine)
    assert e.win.registry["kazma-engine"][0] == e.old
    assert e.win.task["enabled"] and e.win.distro_running
    assert any("not the same" in line for line in e.said)


def test_control_without_the_hash_check_the_damaged_disk_is_kept(engine):
    e = engine
    e.win.move_corrupts = True
    e.machine.hasher = lambda path: "same"
    assert dfm._move_engine(e.part, folder=e.folder, machine=e.machine)
    assert e.win.registry["kazma-engine"][0] == df.Layout(e.folder).engine


def test_a_refused_wsl_move_leaves_the_engine_where_it_was(engine):
    e = engine
    e.win.move_fails = True
    assert not dfm._move_engine(e.part, folder=e.folder, machine=e.machine)
    assert e.win.registry["kazma-engine"][0] == e.old and (e.old / "ext4.vhdx").is_file()
    assert e.win.task["enabled"] and e.win.distro_running


def test_an_old_wsl_refuses_the_engine_move(engine, monkeypatch):
    run = engine.win.run

    def old_wsl(argv, **kw):
        if list(argv) == ["wsl.exe", "--version"]:
            return subprocess.CompletedProcess(argv, 0, "WSL version: 2.4.13.0", "")
        return run(argv, **kw)

    engine.machine.run = old_wsl
    _part, refusals, _ = dfm._engine_part(df.Layout(engine.folder), engine.machine)
    assert any("WSL 2.5" in r for r in refusals)


# -- the engine set up after a reset ------------------------------------------------------------------------


def test_a_kept_engine_disk_is_registered_in_place(tmp_path, monkeypatch):
    from kazma_core import container_engine_setup as ces

    win = _Windows(tmp_path, installed=False)
    folder = tmp_path / "D" / "Kazma"
    kept = df.Layout(folder).engine / "ext4.vhdx"
    kept.parent.mkdir(parents=True)
    kept.write_bytes(b"images, containers, volumes")
    df.record_choice(folder)
    monkeypatch.setattr(ces, "_features_pending", lambda run: False)
    monkeypatch.setattr(ces, "_fetch", lambda *a, **kw: pytest.fail("nothing is downloaded for a kept disk"))
    ok, detail = ces._action_distro(win.run, lambda m: None, None)
    assert ok, detail
    assert win.imports == [["--import-in-place", "kazma-engine", str(kept)]]
    grants = [c for c in win.ran("icacls") if c[1] == str(kept)]
    assert grants and "*S-1-5-21-11-22-33-1001:F" in grants[0]          # the new account's, after a reset


def test_control_with_no_folder_chosen_a_fresh_distro_is_imported(tmp_path, monkeypatch):
    from kazma_core import container_engine_setup as ces

    win = _Windows(tmp_path, installed=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(ces, "_features_pending", lambda run: False)
    image = tmp_path / "ubuntu.tar.gz"
    image.write_bytes(b"x")
    monkeypatch.setattr(ces, "_fetch", lambda *a, **kw: image)
    ok, _detail = ces._action_distro(win.run, lambda m: None, None)
    assert ok and win.imports[0][0] == "--import" and "local" in win.imports[0][2]


def test_with_a_folder_chosen_a_new_engine_disk_goes_there(tmp_path, monkeypatch):
    from kazma_core import container_engine_setup as ces

    win = _Windows(tmp_path, installed=False)
    folder = tmp_path / "D" / "Kazma"
    df.record_choice(folder)
    monkeypatch.setattr(ces, "_features_pending", lambda run: False)
    image = tmp_path / "ubuntu.tar.gz"
    image.write_bytes(b"x")
    monkeypatch.setattr(ces, "_fetch", lambda *a, **kw: image)
    ok, detail = ces._action_distro(win.run, lambda m: None, None)
    assert ok and win.imports[0][:3] == ["--import", "kazma-engine", str(df.Layout(folder).engine)], detail
    assert any(c[1] == str(df.Layout(folder).engine) and "/inheritance:r" in c for c in win.ran("icacls"))


def test_the_engine_disk_folder_is_where_wsl_has_it(monkeypatch, tmp_path):
    from kazma_core import docker_cli

    monkeypatch.setattr(docker_cli, "wsl_distro_location", lambda name: (tmp_path / "D" / "engine", "ext4.vhdx"))
    assert docker_cli.kazma_engine_disk_dir() == tmp_path / "D" / "engine"
    monkeypatch.setattr(docker_cli, "wsl_distro_location", lambda name: None)
    assert docker_cli.kazma_engine_disk_dir().name == "kazma-engine"


# -- the upgrade follows the folder ---------------------------------------------------------------------------


def test_an_upgrade_puts_the_new_major_in_the_data_folder():
    from kazma_core import postgres_upgrade as pgu

    up = pgu.plan_upgrade(Platform("windows", "winget"), {"service": "postgresql-x64-16", "major": 16, "port": 5432},
                          new_major=PG_MAJOR, temp_port=55432, new_data_dir=r"D:\Kazma\postgres-18\data")
    ports = [s for s in pgu.switch_steps(up) + pgu.undo_steps(up) if s.action == "set_port"]
    assert ports and {s.data_dir for s in ports} == {r"D:\Kazma\postgres-18\data"}
    # The control: no folder chosen, the port is written in the package's own folder.
    plain = pgu.plan_upgrade(Platform("windows", "winget"), {"service": "postgresql-x64-16", "major": 16,
                                                              "port": 5432}, new_major=PG_MAJOR, temp_port=55432)
    assert {s.data_dir for s in pgu.switch_steps(plain) if s.action == "set_port"} == {""}


# -- the guard leaves a moving part alone -----------------------------------------------------------------------


def test_the_guard_and_the_move_agree_on_the_hold():
    from tests.test_guard_database_service import guard

    assert guard.DATA_MOVE_HOLD_S == dfm.GUARD_HOLD_S
    assert tuple(guard.DATA_MOVE_STAGES) == tuple(dfm.ACTIVE_STAGES)
    assert "data_folder_move" in guard.GUARD_FEATURES
    from kazma_cli.setup import DATA_FOLDER_GUARD_FEATURE

    assert DATA_FOLDER_GUARD_FEATURE == "data_folder_move"


def test_the_guard_reads_a_moving_database_and_lets_it_lapse(tmp_path, monkeypatch):
    from tests.test_guard_database_service import guard

    monkeypatch.setattr(guard, "_data_dir_guess", lambda: tmp_path)
    rec = {"os": "windows", "service": SVC, "port": 5432}

    def write(since, part="postgres", stage="copying"):
        (tmp_path / "setup_record.json").write_text(json.dumps({
            "postgres": rec, df.JOURNAL_KEY: {"part": part, "stage": stage, "since": since}}), encoding="utf-8")

    write(time.time())
    assert guard._setup_record().get("moving") is True
    write(time.time(), part="engine")
    assert "moving" not in guard._setup_record()
    write(time.time() - guard.DATA_MOVE_HOLD_S - 1)
    assert "moving" not in guard._setup_record()


def test_the_database_watch_neither_starts_nor_pages_a_moving_database(monkeypatch):
    from tests.test_guard_database_service import _Harness

    h = _Harness(monkeypatch, record={"os": "windows", "service": SVC, "port": 5432, "moving": True})
    h.run(300)
    h.running = h.answers = False
    h.run(900)
    assert h.starts() == 0 and h.pages == [] and h.g.dbsvc.state == "moving"
    # The negative control: the same outage with no move journaled is started and paged.
    h2 = _Harness(monkeypatch, record={"os": "windows", "service": SVC, "port": 5432})
    h2.run(300)
    h2.running = h2.answers = False
    h2.run(900)
    assert h2.starts() == 1 and len(h2.pages) == 1


def test_the_engine_watch_does_not_start_a_moving_engine(monkeypatch, tmp_path):
    from tests.test_guard_dependencies import _engine_contexts, _Harness, guard

    _engine_contexts(monkeypatch)
    monkeypatch.setattr(guard, "_data_dir_guess", lambda: tmp_path)

    def journal(part):
        (tmp_path / "setup_record.json").write_text(json.dumps(
            {df.JOURNAL_KEY: {"part": part, "stage": "moving", "since": time.time()}}), encoding="utf-8")

    journal("engine")
    h = _Harness(monkeypatch, containers=None)
    h.write_record(["kazma-searxng"], engine="kazma")
    h.docker.engine = False
    h.run(300)
    assert not [c for c in h.docker.calls if c[0] in ("schtasks", "wsl.exe")]
    assert h.deps.state == "engine_moving" and h.pages == []
    # The negative control: the journal names the database, not the engine: the engine is brought back.
    journal("postgres")
    h2 = _Harness(monkeypatch, containers=None)
    h2.write_record(["kazma-searxng"], engine="kazma")
    h2.docker.engine = False
    h2.run(300)
    assert [c[:4] for c in h2.docker.calls if c[0] == "schtasks"][:1] == [["schtasks", "/Run", "/TN", "KazmaEngine"]]


# -- kazma setup data-folder: the command --------------------------------------------------------------------


class _Hold:
    """The guard's hold, as ``setup._MoveHold``: *lacks* is whether the
    running guard predates ``data_folder_move``."""

    def __init__(self, events: list[str], *, running: bool = True, lacks: bool = False,
                 refusal: str | None = None) -> None:
        self.events, self.running, self.lacks, self._refusal = events, running, lacks, refusal
        self.held = False

    def refusal(self):
        return self._refusal

    def guard_lacks(self, feature: str) -> bool:
        return self.lacks

    def adopt_leftover(self) -> bool:
        return False

    def stop(self) -> bool:
        self.events.append("hold")
        self.held = self.running
        return True

    def resume(self) -> str:
        self.events.append("resume")
        self.held = False
        return "up"


def _cli(machine, hold, *, folder=None, **kw):
    from kazma_cli.setup import _setup_data_folder

    options = SimpleNamespace(folder=folder, yes=True, check=False, dry_run=False)
    for key, value in kw.items():
        setattr(options, key, value)
    return _setup_data_folder(options, run=machine.run, hold=hold, machine=machine,
                              platform=Platform("windows", "winget"))


def test_the_command_is_for_windows(capsys):
    from kazma_cli.setup import _setup_data_folder

    assert _setup_data_folder(SimpleNamespace(folder=r"D:\Kazma", yes=True, check=False, dry_run=False),
                              platform=Platform("debian", "apt")) == 2
    assert "for Windows" in capsys.readouterr().out


def test_with_no_folder_it_says_where_each_part_is_now(engine, capsys):
    e = engine
    e.machine.say = print
    assert _cli(e.machine, _Hold([])) == 0
    out = capsys.readouterr().out
    assert "none chosen" in out and str(e.old) in out and "PostgreSQL's data" in out


@pytest.mark.parametrize("hold_kw, admin, want", [
    (dict(lacks=True), True, "older than this command"),
    (dict(refusal="Kazma is running and no guard supervises it"), True, "no guard supervises it"),
    ({}, False, "administrator terminal"),
])
def test_the_move_is_refused_before_anything_changes(engine, monkeypatch, capsys, hold_kw, admin, want):
    e = engine
    e.machine.say = print
    monkeypatch.setattr(pgs, "is_admin", lambda: admin)
    events: list[str] = []
    assert _cli(e.machine, _Hold(events, **hold_kw), folder=r"D:\Kazma") == 2
    out = capsys.readouterr().out
    assert want in out and "Nothing was done" in out
    assert events == [] and not e.win.ran("schtasks") and not e.win.ran("wsl.exe", "--manage")
    assert df.chosen(e.machine.record_path) is None


def test_with_nothing_elsewhere_the_choice_is_recorded_and_nothing_held(tmp_path, monkeypatch, capsys):
    win = _Windows(tmp_path, installed=False)
    machine = _machine(win, tmp_path / "setup_record.json", [])
    machine.say = print
    events: list[str] = []
    assert _cli(machine, _Hold(events), folder="D:") == 0
    assert df.chosen(machine.record_path) == Path(r"D:\Kazma") and events == []
    assert "puts each part there" in capsys.readouterr().out


@pytest.mark.skipif(os.name != "nt", reason="the folder is a real Windows path")
def test_the_whole_move_holds_kazma_moves_both_and_starts_it_again(tmp_path, monkeypatch, capsys):
    win, data, archive, base = _installed(tmp_path)
    old = tmp_path / "C" / "AppData" / "Local" / "Kazma" / "engine" / "kazma-engine"
    old.mkdir(parents=True)
    (old / "ext4.vhdx").write_bytes(os.urandom(50_000))
    win.registry["kazma-engine"] = (old, "ext4.vhdx")
    win.distro_running = True
    record_path = tmp_path / "setup_record.json"
    _record(record_path, archive, base)
    monkeypatch.setattr("kazma_core.docker_cli.find_docker_cli", lambda: "docker")
    monkeypatch.setattr(pgs, "is_admin", lambda: True)
    monkeypatch.setattr("kazma_cli.setup._existing_app_password", lambda port: APP_PW)
    monkeypatch.setattr("kazma_cli.setup._kazma_signed_in", lambda url, **kw: True)
    monkeypatch.setattr("kazma_cli.setup._checkout_root", lambda start=None: tmp_path / "install")
    machine = _machine(win, record_path, [])
    machine.say = print
    events: list[str] = []
    folder = tmp_path / "D" / "Kazma"
    assert _cli(machine, _Hold(events), folder=str(folder)) == 0, capsys.readouterr().out
    assert events == ["hold", "resume"]
    layout = df.Layout(folder)
    assert pgs.data_dir_in_command(win.service["command"]) == str(layout.postgres_data(PG_MAJOR))
    assert win.registry["kazma-engine"][0] == layout.engine
    assert df.chosen(record_path) == folder and df.journal(record_path) == {}
    # A second run finds nothing to move.
    events.clear()
    assert _cli(machine, _Hold(events), folder=str(folder)) == 0
    assert events == [] and "already there" in capsys.readouterr().out


# -- kazma setup install --data-folder -------------------------------------------------------------------------


def _install_box(the_box, tmp_path, *, system_drive="Z:", engine_at=None):
    from tests.test_install_modes import _machine as install_machine

    machine = install_machine(the_box)
    win = _Windows(tmp_path, installed=False)
    if engine_at is not None:
        win.registry["kazma-engine"] = (engine_at, "ext4.vhdx")
    disk = df.Disk(drive_type=lambda r: df.DRIVE_FIXED, dos_device=lambda d: r"\Device\HarddiskVolume3",
                   free_bytes=lambda p: 500 * 2**30, system_drive=system_drive)
    machine.finders["data_folder_machine"] = dfm.Machine(run=win.run, disk=disk, distro_location=win.location,
                                                         record_path=machine.record_path, user="*S-1-5-21-1")
    return machine


def test_install_refuses_a_folder_on_windows_own_drive_before_anything(box, tmp_path, capsys):  # noqa: F811 - the imported fixture
    from kazma_cli import install as install_mod
    from tests.test_install_modes import _installs
    from tests.test_install_modes import _options as install_options

    machine = _install_box(box, tmp_path, system_drive="C:")
    assert install_mod.install(install_options(yes=True, data_folder=r"C:\Kazma"), machine) == 2
    out = capsys.readouterr().out
    assert "Windows' own drive" in out and "Nothing was done" in out
    assert _installs(box) == [] and df.chosen(machine.record_path) is None


def test_install_records_the_folder_and_moves_what_lives_elsewhere(box, tmp_path, monkeypatch, capsys):  # noqa: F811 - the imported fixture
    from kazma_cli import install as install_mod
    from tests.test_install_modes import _options as install_options

    old = tmp_path / "old-engine"
    old.mkdir()
    (old / "ext4.vhdx").write_bytes(b"disk")
    machine = _install_box(box, tmp_path, engine_at=old)
    moved: list[str] = []
    monkeypatch.setattr(install_mod, "_data_folder_move", lambda folder, log: (moved.append(folder), (True, ""))[1])
    install_mod.install(install_options(yes=True, data_folder="D:"), machine)
    assert df.chosen(machine.record_path) == Path(r"D:\Kazma")
    assert moved == [r"D:\Kazma"]
    # The control: nothing elsewhere, no move step.
    moved.clear()
    machine2 = _install_box(box, tmp_path)
    install_mod.install(install_options(yes=True, data_folder="D:"), machine2)
    assert moved == []


@pytest.mark.skipif(os.name != "nt", reason="WSL's registration is in the Windows registry")
def test_the_registration_is_read_whole_and_its_long_path_prefix_dropped(monkeypatch):
    import winreg

    from kazma_core import docker_cli

    keys = {"{a}": {"DistributionName": "Ubuntu", "BasePath": r"C:\u"},
            "{b}": {"DistributionName": "kazma-engine", "BasePath": "\\\\?\\D:\\Kazma\\engine"}}

    class _Key:
        def __init__(self, name):
            self.name = name

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(key, sub, *a, **kw):
        return _Key(sub if sub in keys else "lxss")

    def enum_key(key, index):
        names = list(keys)
        if index >= len(names):
            raise OSError("no more")
        return names[index]

    def query(key, name):
        values = keys.get(key.name, {})
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    monkeypatch.setattr(winreg, "OpenKey", open_key)
    monkeypatch.setattr(winreg, "EnumKey", enum_key)
    monkeypatch.setattr(winreg, "QueryValueEx", query)
    assert docker_cli.wsl_distro_location("kazma-engine") == (Path(r"D:\Kazma\engine"), "ext4.vhdx")
    assert docker_cli.wsl_distro_location("missing") is None
