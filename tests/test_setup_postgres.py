"""Postgres 16 as an OS service, and WAL archiving (resilience plan A1, A2).

Live 2026-11-04: an update tool stopped Docker Desktop and Kazma's database,
a container, was gone for eleven hours; after a power cut with nobody signed
in it would never start. ``kazma setup postgres`` installs Postgres 16 as an
OS service on every OS Kazma supports. Held here, with every package manager
faked (nothing is installed):

* each OS gets its own steps: the service starts at boot and restarts on
  failure, listens on this machine only, Kazma's database and its
  ``kazma_app`` role (not a superuser), WAL archiving on;
* no password is on a command line (the Windows installer's throwaway one
  aside, replaced by the next step) or in anything printed;
* ``--check`` and ``--dry-run`` change nothing; a second run repairs and never
  reinstalls;
* the WAL archive: the copy command per OS, retention by the oldest kept base
  backup, the archiver's numbers read as a problem or none, and a restore that
  goes into a new folder and replays to a time.

A command that puts the password on psql's command line (the obvious way) is
the negative control of the password check.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core import postgres_service as pgs
from kazma_core.backup import wal_archive
from kazma_core.postgres_service import PG_MAJOR, Platform, Step

SUPER, THROW, APP = "Super-Secret-Pw-1", "Throwaway-Pw-2", "App-Pw-3"
ROLE_SQL = "-- the role script\n"


def _plan(platform: Platform) -> list[Step]:
    return pgs.setup_plan(platform, superuser_password=SUPER, throwaway_password=THROW, app_password=APP,
                          role_sql=ROLE_SQL, wal_archive_command="copy %p")


def _passwords_on_a_command_line(steps: list[Step]) -> list[str]:
    """Steps whose argv or printed form carries a password the setup keeps."""
    bad = []
    for step in steps:
        argv = " ".join(step.argv)
        for secret in (SUPER, APP):
            if secret in argv or secret in step.printable():
                bad.append(step.title or argv)
        if THROW in step.printable():
            bad.append(step.title or argv)
    return bad


# -- the platform -------------------------------------------------------------------------


@pytest.mark.parametrize("system, os_release, which, want", [
    ("Windows", "", {"winget"}, Platform("windows", "winget")),
    ("Windows", "", set(), Platform("windows", None)),
    ("Darwin", "", {"brew"}, Platform("macos", "brew")),
    ("Linux", 'ID=ubuntu\nID_LIKE=debian\n', {"apt-get"}, Platform("debian", "apt")),
    ("Linux", 'ID="rocky"\nID_LIKE="rhel centos fedora"\n', {"dnf"}, Platform("fedora", "dnf")),
    ("Linux", "ID=arch\n", set(), Platform("linux", None)),
])
def test_the_platform_and_its_package_manager(system, os_release, which, want):
    got = pgs.detect_platform(system=system, os_release=os_release, proc_version="Linux 6.6",
                              which=lambda name: f"/usr/bin/{name}" if name in which else None)
    assert got == want


def test_wsl_is_linux_inside_its_distro():
    got = pgs.detect_platform(system="Linux", os_release="ID=ubuntu\n", proc_version="Linux microsoft-standard-WSL2",
                              which=lambda name: "/usr/bin/apt-get")
    assert got == Platform("debian", "apt", wsl=True)


# -- the plan ------------------------------------------------------------------------------


@pytest.mark.parametrize("platform", [Platform("windows", "winget"), Platform("debian", "apt"),
                                      Platform("fedora", "dnf"), Platform("macos", "brew")])
def test_each_os_installs_a_service_that_starts_at_boot_and_restarts(platform):
    steps = _plan(platform)
    text = "\n".join(" ".join(s.argv) for s in steps)
    if platform.os == "windows":
        assert f"PostgreSQL.PostgreSQL.{PG_MAJOR}" in text and "start= auto" in text and "sc failure" in text
    elif platform.os in ("debian", "fedora"):
        assert "systemctl enable --now" in text and "Restart=on-failure" in text
    else:
        assert f"sudo brew services start postgresql@{PG_MAJOR}" in text
    stdin = "\n".join(s.stdin or "" for s in steps)
    assert "listen_addresses = 'localhost'" in stdin
    assert "archive_mode = 'on'" in stdin and "archive_timeout = '300'" in stdin
    assert ROLE_SQL in stdin and "ALTER ROLE kazma_app PASSWORD" in stdin
    assert _passwords_on_a_command_line(steps) == []


def test_the_password_check_sees_a_password_on_psqls_command_line():
    # The negative control: the obvious way to set a password.
    obvious = Step("Give kazma_app its password",
                   ("psql", "-c", f"ALTER ROLE kazma_app PASSWORD '{APP}'"))
    assert _passwords_on_a_command_line([obvious]) == ["Give kazma_app its password"]


def test_a_quote_in_a_password_cannot_end_the_sql_string():
    steps = pgs.setup_plan(Platform("debian", "apt"), superuser_password="it's", throwaway_password=THROW,
                           app_password="o'neil", role_sql=ROLE_SQL)
    stdin = "\n".join(s.stdin or "" for s in steps)
    assert "PASSWORD 'it''s'" in stdin and "PASSWORD 'o''neil'" in stdin


def test_no_package_manager_is_refused():
    with pytest.raises(ValueError):
        _plan(Platform("linux", None))


# -- the command ------------------------------------------------------------------------------


class _Runner:
    """subprocess.run, recorded: the service manager answers ``state``."""

    def __init__(self, *, installed: bool) -> None:
        self.installed = installed
        self.calls: list[dict] = []

    def __call__(self, argv, **kw):
        self.calls.append({"argv": list(argv), "stdin": kw.get("input"), "env": kw.get("env") or {}})
        if argv[:2] == ["sc", "query"]:
            out = "STATE              : 4  RUNNING" if self.installed else "FAILED 1060"
            return subprocess.CompletedProcess(argv, 0 if self.installed else 1060, out, "")
        if argv[:2] == ["sc", "qc"]:
            return subprocess.CompletedProcess(argv, 0, "START_TYPE         : 2   AUTO_START", "")
        if argv[:2] == ["sc", "qfailure"]:
            return subprocess.CompletedProcess(argv, 0, "FAILURE_ACTIONS : RESTART -- Delay = 5000", "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "install"
    (root / "deploy" / "postgres").mkdir(parents=True)
    (root / "deploy" / "postgres" / "kazma_app_role.sql").write_text(ROLE_SQL, encoding="utf-8")
    home = root / ".kazma"
    home.mkdir()
    data = root / "kazma-data"
    data.mkdir()
    from kazma_core import paths

    monkeypatch.setattr(paths, "get_project_root", lambda: root)
    monkeypatch.setenv("KAZMA_USER_HOME", str(home))
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.delenv("KAZMA_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(pgs, "detect_platform", lambda: Platform("windows", "winget"))
    monkeypatch.setattr(pgs, "is_admin", lambda: True)
    monkeypatch.setattr(pgs, "_port_answers", lambda port, host="127.0.0.1", timeout=2.0: True)
    monkeypatch.setattr(pgs, "wait_for_port", lambda port, **kw: True)
    monkeypatch.setattr(pgs, "restart_service", lambda *a, **kw: True)
    monkeypatch.setattr("kazma_core.db.postgres_pool.refusal_reason", lambda dsn: None)
    monkeypatch.setattr("kazma_core.observability.outside_watch._restrict_to_owner", lambda path: True)
    # Kazma's Postgres driver is installed here, whatever the test machine has
    # (CI installs none, 2026-11-09); the driver tests take it away themselves
    # (_driver_missing_here).
    monkeypatch.setattr("kazma_core.db.postgres_pool.driver_missing", lambda modules=None: None)
    return root


def _options(**kw):
    base = dict(yes=True, check=False, dry_run=False, port=5432, move_from_container=None, no_env=False)
    base.update(kw)
    return SimpleNamespace(**base)


def test_a_full_setup_writes_env_and_the_record_and_prints_no_password(install, capsys):
    from kazma_cli.setup import _setup_postgres

    runner = _Runner(installed=False)
    assert _setup_postgres(_options(), run=runner) == 0
    out = capsys.readouterr().out
    env = (install / ".env").read_text(encoding="utf-8")
    assert "KAZMA_DATABASE_URL=postgresql://kazma_app:" in env and "@127.0.0.1:5432/kazma" in env
    app_pw = env.split("kazma_app:", 1)[1].split("@", 1)[0]
    superuser_pw = (install / ".kazma" / "postgres_superuser.pass").read_text(encoding="utf-8").strip()
    for secret in (app_pw, superuser_pw):
        assert secret not in out
        assert not any(secret in " ".join(c["argv"]) for c in runner.calls)
    assert any(c["argv"][:2] == ["winget", "install"] for c in runner.calls)
    record = json.loads((install / "kazma-data" / "setup_record.json").read_text(encoding="utf-8"))
    assert record["postgres"]["service"] == f"postgresql-x64-{PG_MAJOR}" and record["postgres"]["port"] == 5432


def test_check_and_dry_run_change_nothing(install, capsys):
    from kazma_cli.setup import _setup_postgres

    runner = _Runner(installed=False)
    assert _setup_postgres(_options(check=True), run=runner) == 1
    assert {c["argv"][0] for c in runner.calls} <= {"sc"}
    assert _setup_postgres(_options(dry_run=True), run=runner) == 0
    assert {c["argv"][0] for c in runner.calls} <= {"sc"}
    assert not (install / ".env").exists()
    assert not (install / ".kazma" / "postgres_superuser.pass").exists()
    assert "winget install" in capsys.readouterr().out


def test_a_second_run_repairs_and_never_reinstalls(install):
    from kazma_cli.setup import _setup_postgres

    assert _setup_postgres(_options(), run=_Runner(installed=False)) == 0
    first_env = (install / ".env").read_text(encoding="utf-8")
    runner = _Runner(installed=True)
    assert _setup_postgres(_options(), run=runner) == 0
    assert not any(c["argv"][:1] == ["winget"] for c in runner.calls)
    assert any(c["argv"][:2] == ["sc", "failure"] for c in runner.calls)
    # The role keeps the password Kazma already signs in with.
    assert (install / ".env").read_text(encoding="utf-8") == first_env


def test_check_reports_the_service(install, capsys):
    from kazma_cli.setup import _setup_postgres

    assert _setup_postgres(_options(check=True), run=_Runner(installed=True)) == 0
    out = capsys.readouterr().out
    assert "ok   starts at boot" in out and "ok   restarts on failure" in out
    assert "ok   Kazma's Postgres driver" in out


# ── Kazma's own driver (the drill VM, 2026-11-06) ─────────────────────
#
# setup wrote KAZMA_DATABASE_URL into an environment without psycopg (the
# setup scripts' exact uv sync never installed the postgres extra), and the
# server waited for a database it could not reach.

_UV = "C:/stand-in/uv.exe"


def _driver_missing_here(monkeypatch):
    import shutil

    which = shutil.which
    monkeypatch.setattr("kazma_core.db.postgres_pool.driver_missing",
                        lambda modules=None: "Kazma's Postgres driver is not installed")
    monkeypatch.setattr(shutil, "which", lambda name, *a, **kw: _UV if name == "uv" else which(name, *a, **kw))


class _DriverRunner(_Runner):
    """The service manager as before; the driver's install answers *code*."""

    def __init__(self, *, installed: bool, driver_code: int) -> None:
        super().__init__(installed=installed)
        self.driver_code = driver_code

    def __call__(self, argv, **kw):
        if list(argv[:3]) == [_UV, "pip", "install"]:
            self.calls.append({"argv": list(argv), "stdin": kw.get("input"), "env": kw.get("env") or {}})
            return subprocess.CompletedProcess(argv, self.driver_code, "", "")
        return super().__call__(argv, **kw)


def test_the_driver_is_installed_first_when_it_is_missing(install, monkeypatch):
    from kazma_cli.setup import _setup_postgres

    _driver_missing_here(monkeypatch)
    runner = _DriverRunner(installed=False, driver_code=0)
    assert _setup_postgres(_options(), run=runner) == 0
    argvs = [c["argv"] for c in runner.calls]
    driver = [i for i, a in enumerate(argvs) if a[:3] == [_UV, "pip", "install"]]
    assert len(driver) == 1
    args = argvs[driver[0]]
    assert args[3:5] == ["--python", sys.executable], "into the environment Kazma runs in"
    assert any(a.startswith("psycopg[binary,pool]") for a in args), args
    assert any(a.startswith("langgraph-checkpoint-postgres") for a in args), args
    winget = [i for i, a in enumerate(argvs) if a[:2] == ["winget", "install"]]
    assert winget and driver[0] < winget[0], "the driver goes in before anything else"
    assert (install / ".env").exists()


def test_a_driver_that_does_not_install_writes_no_address(install, monkeypatch, capsys):
    from kazma_cli.setup import _setup_postgres

    _driver_missing_here(monkeypatch)
    assert _setup_postgres(_options(), run=_DriverRunner(installed=False, driver_code=1)) == 1
    assert not (install / ".env").exists(), "no KAZMA_DATABASE_URL Kazma cannot reach"
    assert "Install Kazma's Postgres driver" in capsys.readouterr().out


def test_without_the_driver_step_the_address_is_written_anyway(install, monkeypatch):
    """Negative control: the plan of before, with the driver missing."""
    import kazma_cli.setup as setup_mod
    from kazma_cli.setup import _setup_postgres

    _driver_missing_here(monkeypatch)
    monkeypatch.setattr(setup_mod, "_driver_step", lambda: None)
    assert _setup_postgres(_options(), run=_DriverRunner(installed=False, driver_code=1)) == 0
    assert "KAZMA_DATABASE_URL" in (install / ".env").read_text(encoding="utf-8")


def test_a_second_run_installs_a_driver_still_missing(install, monkeypatch):
    """The drill VM's case: the service is set up; the driver never was."""
    from kazma_cli.setup import _setup_postgres

    assert _setup_postgres(_options(), run=_Runner(installed=False)) == 0
    _driver_missing_here(monkeypatch)
    runner = _DriverRunner(installed=True, driver_code=0)
    assert _setup_postgres(_options(), run=runner) == 0
    assert any(c["argv"][:3] == [_UV, "pip", "install"] for c in runner.calls)
    assert not any(c["argv"][:1] == ["winget"] for c in runner.calls)


def test_check_says_when_the_driver_is_missing(install, monkeypatch, capsys):
    from kazma_cli.setup import _setup_postgres

    _driver_missing_here(monkeypatch)
    assert _setup_postgres(_options(check=True), run=_Runner(installed=True)) == 1
    assert "NO   Kazma's Postgres driver" in capsys.readouterr().out


def test_the_driver_step_installs_the_postgres_extra():
    """One list: the step installs what pyproject's postgres extra names."""
    import re
    import tomllib

    from kazma_cli.setup import _extra_requirements
    from kazma_core.paths import get_project_root

    data = tomllib.loads((get_project_root() / "pyproject.toml").read_text(encoding="utf-8"))
    declared = data["project"]["optional-dependencies"]["postgres"]

    def name(req: str) -> str:
        return re.split(r"[<>=!~\[ ;]", req, maxsplit=1)[0].lower()

    assert sorted(map(name, _extra_requirements("postgres"))) == sorted(map(name, declared))


@pytest.mark.parametrize("os_name, answers, want", [
    ("windows", {"sc query": (0, "STATE : 1 STOPPED"), "sc qc": (0, "DEMAND_START"), "sc qfailure": (0, "")},
     {"installed": True, "running": False, "at_boot": False, "restarts": False}),
    ("debian", {"systemctl is-active": (0, "active"), "systemctl is-enabled": (0, "enabled"),
                "systemctl show": (0, "Restart=on-failure")},
     {"installed": True, "running": True, "at_boot": True, "restarts": True}),
    ("debian", {"systemctl is-active": (4, "Unit postgresql.service could not be found."),
                "systemctl is-enabled": (1, ""), "systemctl show": (0, "Restart=no")},
     {"installed": False, "running": False, "at_boot": False, "restarts": False}),
])
def test_the_service_state_is_the_service_managers(os_name, answers, want):
    def run(argv, **kw):
        code, out = answers.get(" ".join(argv[:2]), (1, ""))
        return subprocess.CompletedProcess(argv, code, out, "")

    state = pgs.service_state(os_name, "svc", run=run, answers=lambda port: False)
    assert {k: state[k] for k in want} == want and state["answers"] is False


# -- the WAL archive ------------------------------------------------------------------------------------


def test_the_archive_command_copies_then_renames():
    win = wal_archive.archive_command(Platform("windows", "winget"), Path(r"C:\ProgramData\Kazma\wal"))
    assert win.startswith('copy /Y "%p" "C:\\ProgramData\\Kazma\\wal\\%f.part"') and "move /Y" in win
    posix = wal_archive.archive_command(Platform("debian", "apt"), Path("/var/lib/postgresql/16/wal_archive"))
    assert posix.startswith('cp "%p"') and posix.endswith('"/var/lib/postgresql/16/wal_archive/%f"')


def _base(root: Path, epoch: int, start: str) -> None:
    folder = root / str(epoch)
    folder.mkdir(parents=True)
    label = f"START WAL LOCATION: 0/{start[-8:]} (file {start})\n".encode()
    with tarfile.open(folder / "base.tar.gz", "w:gz") as tar:
        info = tarfile.TarInfo("backup_label")
        info.size = len(label)
        tar.addfile(info, io.BytesIO(label))


def test_retention_keeps_the_wal_the_oldest_base_backup_needs(tmp_path):
    root, archive = tmp_path / "base", tmp_path / "wal"
    archive.mkdir()
    _base(root, 100, "000000010000000000000010")
    _base(root, 200, "000000010000000000000020")
    for n in (5, 0x10, 0x15, 0x20, 0x25):
        (archive / f"0000000100000000000000{n:02X}").write_text("x")
    assert wal_archive.prune(archive, root) == 1          # only 05, before the older backup
    assert sorted(p.name[-2:] for p in archive.iterdir()) == ["10", "15", "20", "25"]
    # Control: with no base backup nothing is pruned.
    assert wal_archive.prune(archive, tmp_path / "none") == 0


def test_a_base_backup_failure_leaves_no_partial_folder(tmp_path):
    record = {"bin_dir": str(tmp_path), "os": "windows", "port": 5432}

    def run(argv, **kw):
        Path(argv[argv.index("-D") + 1]).mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 1, "", "pg_basebackup: error: connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        wal_archive._base_backup(record, "pw", run=run, root=tmp_path / "base")
    assert list((tmp_path / "base").iterdir()) == []


@pytest.mark.parametrize("status, want", [
    ({"mode": "off"}, "archive_mode is off"),
    ({"mode": "on", "last_archived_at": 1000.0, "last_failed_at": 2000.0, "last_failed_wal": "X"},
     "the last copy failed"),
    ({"mode": "on", "last_archived_at": None}, "nothing has been archived"),
    ({"mode": "on", "last_archived_at": 1000.0 - 20 * 60}, "no segment archived for 20 minutes"),
    ({"mode": "on", "last_archived_at": 1000.0 - 60}, None),
])
def test_the_archivers_numbers_read_as_a_problem_or_none(status, want):
    got = wal_archive.archive_problem(status, now=1000.0)
    assert (got is None) if want is None else (want in got)


def test_a_restore_goes_into_a_new_folder_and_replays_to_a_time(tmp_path):
    root = tmp_path / "base"
    _base(root, 300, "000000010000000000000030")
    target = tmp_path / "restored"
    steps = wal_archive.restore_to("2026-11-20 14:05", target, tmp_path / "wal", base=root / "300",
                                   os_name="debian")
    conf = (target / "postgresql.auto.conf").read_text(encoding="utf-8")
    assert "recovery_target_time = '2026-11-20 14:05'" in conf and "restore_command" in conf
    assert (target / "recovery.signal").exists() and (target / "backup_label").exists()
    assert any("55432" in s for s in steps)
    with pytest.raises(ValueError, match="not empty"):
        wal_archive.restore_to("2026-11-20 14:05", target, tmp_path / "wal", base=root / "300")


def _postgres_reads(conf: str, name: str) -> str:
    """*name*'s value as Postgres's configuration reader takes it (guc-file.l):
    the last line naming it wins; in a quoted value ``''`` is one quote, and a
    backslash escapes the next character -- ``\\b \\f \\n \\r \\t`` and up to
    three octal digits are control characters, any other character stands for
    itself."""
    value = None
    for line in conf.splitlines():
        key, sep, rest = line.partition("=")
        if sep and key.strip() == name:
            value = rest.strip()
    assert value is not None and value[:1] == value[-1:] == "'", (name, value)
    body, out, i = value[1:-1], [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            octal = re.match(r"[0-7]{1,3}", body[i + 1:])
            if octal:
                out.append(chr(int(octal.group(0), 8)))
                i += 1 + len(octal.group(0))
                continue
            out.append({"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}.get(body[i + 1], body[i + 1]))
            i += 2
        elif ch == "'" and body[i + 1:i + 2] == "'":
            out.append("'")
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def test_a_windows_restore_command_reads_back_as_written(tmp_path):
    """The owner's install, 2026-11-14: the first point-in-time restore of
    PostgreSQL 18 replayed nothing past its base backup. The restore wrote
    its Windows copy command into postgresql.auto.conf as it was, and the
    file's reader takes a backslash as an escape: it read
    ``copy "C:ProgramDataKazmapg_wal_archive-18%f"``, and every segment was
    "not found"."""
    root = tmp_path / "base"
    _base(root, 300, "000000010000000000000030")
    archive = Path(r"C:\ProgramData\Kazma\pg_wal_archive-18")
    target = tmp_path / "restored"
    wal_archive.restore_to("2026-11-20 14:05", target, archive, base=root / "300", os_name="windows")
    conf = (target / "postgresql.auto.conf").read_text(encoding="utf-8")
    assert _postgres_reads(conf, "restore_command") == r'copy "C:\ProgramData\Kazma\pg_wal_archive-18\%f" "%p"'
    assert _postgres_reads(conf, "recovery_target_time") == "2026-11-20 14:05"
    assert _postgres_reads(conf, "archive_mode") == "off"


def test_negative_control_the_value_written_as_it_was_loses_its_separators(tmp_path, monkeypatch):
    monkeypatch.setattr(wal_archive, "_conf_string", lambda value: f"'{value}'")
    root = tmp_path / "base"
    _base(root, 300, "000000010000000000000030")
    target = tmp_path / "restored"
    wal_archive.restore_to("2026-11-20 14:05", target, Path(r"C:\ProgramData\Kazma\pg_wal_archive-18"),
                           base=root / "300", os_name="windows")
    conf = (target / "postgresql.auto.conf").read_text(encoding="utf-8")
    assert _postgres_reads(conf, "restore_command") == 'copy "C:ProgramDataKazmapg_wal_archive-18%f" "%p"'


@pytest.mark.parametrize("value", [r"D:\2026\o'brien\wal\%f", "/var/lib/postgresql/18/wal_archive", "it's"])
def test_a_config_string_reads_back_whole(value):
    assert _postgres_reads(f"x = {wal_archive._conf_string(value)}\n", "x") == value


def test_a_restore_takes_the_newest_base_backup_before_the_time(tmp_path):
    """A weekly base taken after the time asked for cannot replay back to it
    (Postgres refuses a target before the backup's end): the restore takes
    the newest base before the time, and says how far back it can go."""
    from datetime import datetime

    def epoch(text: str) -> int:
        return int(datetime.fromisoformat(text).astimezone().timestamp())

    root = tmp_path / "base"
    older, newer = epoch("2026-11-10 03:00"), epoch("2026-11-17 03:00")
    _base(root, older, "000000010000000000000010")
    _base(root, newer, "000000010000000000000030")
    bases = wal_archive._base_backups(root)
    assert wal_archive._base_before("2026-11-14 12:00", bases).name == str(older)
    assert wal_archive._base_before("2026-11-20 14:05", bases).name == str(newer)
    with pytest.raises(ValueError, match="oldest base backup is from 2026-11-10 03:00"):
        wal_archive._base_before("2026-11-01 00:00", bases)
    with pytest.raises(ValueError, match="not a time"):
        wal_archive._base_before("last tuesday", bases)
    # Negative control: the old choice, the newest base whatever the time,
    # takes the base from after a mid-week target.
    assert bases[0].name == str(newer)


def test_the_sweep_does_nothing_where_setup_was_not_run(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    assert wal_archive.wal_sweep() == "not set up"


def test_offsite_sends_what_is_new_and_keeps_the_order(tmp_path, monkeypatch):
    archive = tmp_path / "wal"
    archive.mkdir()
    for n in (1, 2, 3):
        (archive / f"0000000100000000000000{n:02X}").write_text("x")
    sent = []

    class _Provider:
        def __init__(self, fail_at):
            self.fail_at = fail_at

        async def upload_file(self, local, remote):
            if remote.endswith(self.fail_at):
                return {"ok": False, "error": "down"}
            sent.append(remote)
            return {"ok": True}

    stored = {}

    class _Store:
        def get(self, key, default=None):
            return stored.get(key, default)

        def set(self, key, value, category="general"):
            stored[key] = value

    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store())
    result = wal_archive._push_offsite(archive, root=tmp_path / "none", provider=_Provider("02"))
    assert sent == ["pgwal-000000010000000000000001"] and result["ok"] is False
    result = wal_archive._push_offsite(archive, root=tmp_path / "none", provider=_Provider("never"))
    assert sent[1:] == ["pgwal-000000010000000000000002", "pgwal-000000010000000000000003"]
    assert result == {"ok": True, "sent": 2, "failed": []}


def test_the_record_is_written_whole(tmp_path):
    path = tmp_path / "setup_record.json"
    pgs.write_record(pgs.PostgresRecord(os="windows", manager="winget", service="s", port=5432,
                                        bin_dir="b", installed_at=time.time()), path)
    assert pgs.read_record(path)["postgres"]["service"] == "s"


# -- a fresh Windows's winget (2026-11-13) --------------------------------------------------------
#
# The clean VM (2026-11-08) answered every winget install 0x8A15000F until its
# package list was registered. `kazma setup system` repaired it
# (run_system_step); the Postgres setup's own runner did not, so `kazma setup
# postgres` alone -- and `--upgrade` -- failed at the install on a fresh
# Windows.


class _FreshWinget(_Runner):
    """winget refuses installs until Add-AppxPackage registers its list."""

    def __init__(self) -> None:
        super().__init__(installed=False)
        self.registered = False

    def __call__(self, argv, **kw):
        if argv[:2] == ["winget", "install"] and not self.registered:
            self.calls.append({"argv": list(argv), "stdin": None, "env": {}})
            return subprocess.CompletedProcess(argv, 0x8A15000F, "Data required by the source is missing", "")
        if argv[:1] == ["powershell.exe"] and any("Add-AppxPackage" in a for a in argv):
            self.registered = True
        return super().__call__(argv, **kw)


def test_the_postgres_setup_registers_wingets_package_list_and_tries_again(install, capsys):
    from kazma_cli.setup import _setup_postgres

    runner = _FreshWinget()
    assert _setup_postgres(_options(), run=runner) == 0, capsys.readouterr().out
    installs = [c for c in runner.calls if c["argv"][:2] == ["winget", "install"]]
    assert len(installs) == 2 and runner.registered


def test_without_the_repair_the_postgres_setup_stops_at_the_install(install, monkeypatch, capsys):
    """The negative control: the runner before 2026-11-13."""
    from kazma_cli import setup

    monkeypatch.setattr(setup, "_winget_source_missing", lambda step, detail: False)
    runner = _FreshWinget()
    assert setup._setup_postgres(_options(), run=runner) == 1
    assert not runner.registered
