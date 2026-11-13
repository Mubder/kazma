"""Postgres as a normal OS service, on every install (resilience plan A1).

Live 2026-11-04: an update tool stopped Docker Desktop, and Kazma's database
(the ``kazma-db-win`` container) went with it for eleven hours. Docker Desktop
is a program that starts when someone signs in, so after a power cut with
nobody signed in the database never starts at all. A database installed as
an OS service starts at boot and is restarted by the OS when it dies.

This module is what ``kazma setup postgres`` (``kazma_cli/setup.py``),
``kazma doctor`` and the guard share:

* :func:`detect_platform` -- the OS and its package manager (winget on
  Windows, Homebrew on macOS, apt on Debian/Ubuntu, dnf on Fedora/RHEL; WSL is
  Linux inside its distro);
* :func:`setup_plan` -- the steps that install Postgres (:data:`PG_MAJOR`,
  or the major an install already runs) as a service that
  starts at boot and restarts on failure, listening on this machine only,
  create Kazma's database and its ``kazma_app`` role
  (``deploy/postgres/kazma_app_role.sql``), and turn on WAL archiving (A2).
  A password is never on a command line except the Windows installer's
  throwaway one, which the next step replaces through ``psql``'s stdin;
* :func:`service_state` -- installed, running, starting at boot, restarting
  on failure, from the OS's own service manager;
* the setup record (``<data dir>/setup_record.json``, :func:`read_record` /
  :func:`write_record`), which says what was installed where: ``kazma
  doctor`` checks it and the guard watches the service it names.

Every function that runs a program takes ``run`` (``subprocess.run`` by
default), so the tests fake each package manager. Blocking: off the loop.
"""

from __future__ import annotations

import json
import os
import platform as _platform
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "PG_MAJOR",
    "Platform",
    "Step",
    "detect_platform",
    "empty_database_steps",
    "read_record",
    "service_name",
    "service_state",
    "setup_plan",
    "write_record",
]

#: The major a new install gets. An install made before runs its own major
#: (its record names it) until ``kazma setup postgres --upgrade`` moves it
#: (``postgres_upgrade``).
PG_MAJOR = 18
DEFAULT_PORT = 5432
DATABASE = "kazma"
APP_ROLE = "kazma_app"


def service_name(os_name: str, major: int = PG_MAJOR) -> str | None:
    """The service *os_name*'s package registers for *major* (None: no
    package manager Kazma knows). Debian runs every cluster under one
    ``postgresql`` service and controls each with ``pg_ctlcluster``."""
    return {
        "windows": f"postgresql-x64-{major}",
        "debian": "postgresql",
        "fedora": f"postgresql-{major}",
        "macos": f"homebrew.mxcl.postgresql@{major}",
    }.get(os_name)


#: The service each platform's package registers for :data:`PG_MAJOR`.
SERVICE = {name: service_name(name) for name in ("windows", "debian", "fedora", "macos")}

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class Platform:
    """The OS family Kazma runs on and its package manager (None: none known)."""

    os: str
    manager: str | None
    wsl: bool = False


@dataclass(frozen=True)
class Step:
    """One step of the setup: a program and its arguments, or ``action`` for
    a step this module carries out itself (restart, wait). ``stdin`` is what
    the program reads -- where a password goes, never ``argv``. ``shown`` is
    the step as printed (no secret). ``dep`` names the system dependency a
    step of ``kazma setup system`` is for (``system_deps.DEPENDENCIES``)."""

    title: str
    argv: tuple[str, ...] = ()
    stdin: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    shown: str = ""
    admin: bool = False
    ok_codes: tuple[int, ...] = (0,)
    action: str = ""
    dep: str = ""
    #: For an ``action``: the service, its major and the port it acts on
    #: (empty: the plan's own service and port).
    service: str = ""
    major: int = 0
    port: int = 0

    def printable(self) -> str:
        return self.shown or " ".join(self.argv) or self.action


# -- the platform -------------------------------------------------------------------


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _os_release_ids(text: str) -> set[str]:
    ids: set[str] = set()
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key in ("ID", "ID_LIKE"):
            ids.update(value.strip().strip('"').lower().split())
    return ids


def detect_platform(*, system: str | None = None, which: Callable[[str], str | None] = shutil.which,
                    os_release: str | None = None, proc_version: str | None = None) -> Platform:
    """The OS family and its package manager."""
    system = system or _platform.system()
    if system == "Windows":
        return Platform("windows", "winget" if which("winget") else None)
    if system == "Darwin":
        return Platform("macos", "brew" if which("brew") else None)
    if system == "Linux":
        ids = _os_release_ids(os_release if os_release is not None else _read("/etc/os-release"))
        wsl = "microsoft" in (proc_version if proc_version is not None else _read("/proc/version")).lower()
        if ids & {"debian", "ubuntu"}:
            return Platform("debian", "apt" if which("apt-get") else None, wsl)
        if ids & {"fedora", "rhel", "centos", "rocky", "almalinux"}:
            return Platform("fedora", "dnf" if which("dnf") else None, wsl)
        return Platform("linux", None, wsl)
    return Platform("unknown", None)


# -- the plan ----------------------------------------------------------------------------


def _bin_dir(p: Platform, major: int = PG_MAJOR) -> str:
    if p.os == "windows":
        return rf"C:\Program Files\PostgreSQL\{major}\bin"
    if p.os == "fedora":
        return f"/usr/pgsql-{major}/bin"
    if p.os == "debian":
        return f"/usr/lib/postgresql/{major}/bin"
    return f"/opt/homebrew/opt/postgresql@{major}/bin"


def data_dir_for(p: Platform, major: int = PG_MAJOR) -> str:
    """Where the package puts *major*'s data folder (Debian: its
    configuration folder, which ``pg_conftool`` edits)."""
    if p.os == "windows":
        return rf"C:\Program Files\PostgreSQL\{major}\data"
    if p.os == "fedora":
        return f"/var/lib/pgsql/{major}/data"
    if p.os == "debian":
        return f"/etc/postgresql/{major}/main"
    return f"/opt/homebrew/var/postgresql@{major}"


def _psql(p: Platform, *, port: int, database: str = "postgres", major: int = PG_MAJOR) -> tuple[str, ...]:
    """psql as the server's superuser on this machine."""
    exe = str(Path(_bin_dir(p, major)) / ("psql.exe" if p.os == "windows" else "psql"))
    # -w: a refused password is an error, never a prompt on the console (psql
    # asks there whatever its input is, and the setup waited for a key).
    common = ("-w", "-v", "ON_ERROR_STOP=1", "-X", "-q", "-p", str(port), "-d", database)
    if p.os == "windows":
        return (exe, "-h", "127.0.0.1", "-U", "postgres", *common)
    if p.os in ("debian", "fedora"):
        return ("sudo", "-u", "postgres", exe, *common)
    return (exe, "-h", "127.0.0.1", *common)       # macOS: Homebrew's superuser is this account


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def port_step(p: Platform, major: int, port: int) -> Step:
    """*major*'s server set to listen on *port* at its next start (it may be
    running or stopped: the setting is written on disk, never through a
    connection, so a server that cannot start on its old port can be moved).
    Windows and macOS: :func:`set_port_on_disk` in this process (the folder
    is the elevated setup's, or this account's); Debian: ``pg_conftool``;
    Fedora: the same edit as the server's own account."""
    title = f"Set PostgreSQL {major} to listen on port {int(port)}"
    if p.os == "debian":
        return Step(title, ("sudo", "pg_conftool", str(major), "main", "set", "port", str(int(port))), admin=True,
                    major=major, port=int(port))
    if p.os == "fedora":
        conf = f"{data_dir_for(p, major)}/postgresql.auto.conf"
        script = f"sed -i '/^port[[:space:]]*=/d' {conf} && echo \"port = '{int(port)}'\" >> {conf}"
        return Step(title, ("sudo", "-u", "postgres", "sh", "-c", script), admin=True, major=major, port=int(port))
    return Step(title, action="set_port", major=major, port=int(port), admin=p.os != "macos",
                shown=f"port = {int(port)} in postgresql.auto.conf ({data_dir_for(p, major)})")


def setup_plan(p: Platform, *, port: int = DEFAULT_PORT, superuser_password: str,
               throwaway_password: str, app_password: str, role_sql: str,
               wal_archive_command: str | None = None, major: int = PG_MAJOR) -> list[Step]:
    """Every step for *p*, in order. Raises ValueError for a platform with no
    package manager Kazma knows (the CLI prints the manual steps instead).

    A *port* other than the default (``postgres_upgrade``: the new major
    beside the old one, on a spare port) is set before the server first
    starts where the package would start it on its own default."""
    if p.manager is None:
        raise ValueError(f"no supported package manager on this {p.os} system")
    service = service_name(p.os, major)
    steps: list[Step] = []
    other_port = port != DEFAULT_PORT

    # 1. Install Postgres as a service that starts at boot.
    if p.os == "windows":
        override = (f"--mode unattended --unattendedmodeui none --disable-components stackbuilder,pgAdmin "
                    f"--servicename {service} --serverport {port} --superpassword {throwaway_password}")
        steps.append(Step(
            f"Install PostgreSQL {major} (a Windows service)",
            ("winget", "install", "--id", f"PostgreSQL.PostgreSQL.{major}", "--exact", "--silent",
             "--accept-package-agreements", "--accept-source-agreements", "--override", override),
            shown=(f"winget install --id PostgreSQL.PostgreSQL.{major} --exact --silent "
                   f"--override \"... --servicename {service} --serverport {port} --superpassword ****\""),
            admin=True))
        steps.append(Step("Start it at boot", ("sc", "config", service, "start=", "auto"), admin=True))
        steps.append(Step("Restart it when it fails",
                          ("sc", "failure", service, "reset=", "86400",
                           "actions=", "restart/5000/restart/30000/restart/60000"), admin=True))
    elif p.os == "debian":
        steps += [
            Step("Add the PostgreSQL project's apt repository",
                 ("sudo", "apt-get", "install", "-y", "postgresql-common"), admin=True),
            Step("", ("sudo", "/usr/share/postgresql-common/pgdg/apt.postgresql.org.sh", "-y"), admin=True),
            Step(f"Install postgresql-{major}",
                 ("sudo", "apt-get", "install", "-y", f"postgresql-{major}"), admin=True),
        ]
        if other_port:
            # apt makes and starts the cluster on the next free port.
            steps += [port_step(p, major, port),
                      Step("Restart it there", action="restart", service=service, major=major, port=port,
                           admin=True)]
        steps.append(Step("Start it at boot", ("sudo", "systemctl", "enable", "--now", service), admin=True))
    elif p.os == "fedora":
        steps += [
            Step("Add the PostgreSQL project's dnf repository",
                 ("sudo", "dnf", "install", "-y",
                  "https://download.postgresql.org/pub/repos/yum/reporpms/EL-9-x86_64/"
                  "pgdg-redhat-repo-latest.noarch.rpm"), admin=True),
            Step(f"Install postgresql{major}-server",
                 ("sudo", "dnf", "install", "-y", f"postgresql{major}-server"), admin=True),
            Step("Create its data folder (sign-in by password on this machine)",
                 ("sudo", "env", "PGSETUP_INITDB_OPTIONS=--auth-host=scram-sha-256 --auth-local=peer",
                  f"/usr/pgsql-{major}/bin/postgresql-{major}-setup", "initdb"),
                 admin=True, ok_codes=(0, 1)),
            *([port_step(p, major, port)] if other_port else []),
            Step("Start it at boot", ("sudo", "systemctl", "enable", "--now", service), admin=True),
        ]
    elif p.os == "macos":
        steps += [
            Step(f"Install postgresql@{major}", ("brew", "install", f"postgresql@{major}")),
            *([port_step(p, major, port)] if other_port else []),
            Step("Start it at boot (a LaunchDaemon: before anyone signs in)",
                 ("sudo", "brew", "services", "start", f"postgresql@{major}"), admin=True),
        ]
    if p.os in ("debian", "fedora"):
        steps.append(Step(
            "Restart it when it fails",
            ("sudo", "sh", "-c",
             f"mkdir -p /etc/systemd/system/{service}.service.d && printf '[Service]\\nRestart=on-failure\\n"
             f"RestartSec=5s\\n' > /etc/systemd/system/{service}.service.d/kazma.conf && "
             "systemctl daemon-reload"), admin=True))

    steps.append(Step("Wait for it to answer", action="wait", port=port))

    # 2. The superuser's password (Windows: replace the installer's throwaway),
    #    this machine only, Kazma's database. Read from stdin.
    env = {"PGPASSWORD": throwaway_password} if p.os == "windows" else {}
    sql = [f"ALTER ROLE {'postgres' if p.os != 'macos' else 'CURRENT_USER'} PASSWORD "
           f"{_sql_literal(superuser_password)};",
           "ALTER SYSTEM SET listen_addresses = 'localhost';",
           "ALTER SYSTEM SET password_encryption = 'scram-sha-256';",
           f"SELECT 'CREATE DATABASE {DATABASE}' WHERE NOT EXISTS "
           f"(SELECT 1 FROM pg_database WHERE datname = '{DATABASE}')\\gexec"]
    if wal_archive_command:
        sql += ["ALTER SYSTEM SET archive_mode = 'on';",
                "ALTER SYSTEM SET archive_timeout = '300';",
                f"ALTER SYSTEM SET archive_command = {_sql_literal(wal_archive_command)};"]
    psql = _psql(p, port=port, major=major)
    steps.append(Step("Set the superuser's password, this machine only, Kazma's database",
                      psql, stdin="\n".join(sql) + "\n", env=env,
                      shown=" ".join(psql) + " < (password, settings)"))
    steps.append(Step("Restart it to apply the settings", action="restart", service=service, major=major,
                      port=port))
    steps.append(Step("Wait for it to answer", action="wait", port=port))

    # 3. Kazma's role: not a superuser, the owner of Kazma's objects.
    env = {"PGPASSWORD": superuser_password} if p.os == "windows" else {}
    psql = _psql(p, port=port, database=DATABASE, major=major)
    steps.append(Step(f"Create the {APP_ROLE} role (not a superuser)", psql,
                      stdin=role_sql, env=env, shown=" ".join(psql) + " < kazma_app_role.sql"))
    steps.append(Step(f"Give {APP_ROLE} its password", psql,
                      stdin=f"ALTER ROLE {APP_ROLE} PASSWORD {_sql_literal(app_password)};\n", env=env,
                      shown=" ".join(psql) + " < (password)"))
    return steps


def empty_database_steps(p: Platform, *, port: int = DEFAULT_PORT, superuser_password: str,
                         role_sql: str, major: int = PG_MAJOR) -> list[Step]:
    """Kazma's database on this server dropped and made again, empty and
    owned by ``kazma_app``: before a move copies the container in again over
    an earlier copy Kazma never started on (``backup.pg_move``). A plain
    ``DROP DATABASE``, never ``WITH (FORCE)``: a session still connected
    fails the step instead of being cut off."""
    env = {"PGPASSWORD": superuser_password} if p.os == "windows" else {}
    server = _psql(p, port=port, major=major)
    kazma = _psql(p, port=port, database=DATABASE, major=major)
    return [
        Step(f"Empty Kazma's database on the new server (drop and create {DATABASE})", server,
             stdin=f"DROP DATABASE IF EXISTS {DATABASE};\nCREATE DATABASE {DATABASE};\n", env=env,
             shown=" ".join(server) + f" < (DROP DATABASE {DATABASE}; CREATE DATABASE {DATABASE})"),
        Step(f"Give it to {APP_ROLE}", kazma, stdin=role_sql, env=env,
             shown=" ".join(kazma) + " < kazma_app_role.sql"),
    ]


def database_url(app_password: str, port: int = DEFAULT_PORT) -> str:
    """The address Kazma signs in with (the password URL-quoted)."""
    from urllib.parse import quote

    return f"postgresql://{APP_ROLE}:{quote(app_password, safe='')}@127.0.0.1:{port}/{DATABASE}"


# -- the service ---------------------------------------------------------------------------


def _port_answers(port: int, host: str = "127.0.0.1", timeout: float = 2.0) -> bool:
    """A TCP connection to the server's port is taken."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _out(run: Runner, argv: list[str]) -> tuple[int, str]:
    try:
        done = run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return -1, ""
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def service_state(os_name: str, service: str, port: int = DEFAULT_PORT, *,
                  run: Runner = subprocess.run, answers: Callable[[int], bool] | None = None) -> dict[str, Any]:
    """What the OS says about the database service: ``installed``,
    ``running``, ``at_boot``, ``restarts`` (each True/False/None when the
    manager cannot say) and whether the port answers."""
    state: dict[str, Any] = {"service": service, "installed": None, "running": None,
                             "at_boot": None, "restarts": None}
    if os_name == "windows":
        code, text = _out(run, ["sc", "query", service])
        state["installed"] = code == 0
        if code == 0:
            state["running"] = "RUNNING" in text
            code, text = _out(run, ["sc", "qc", service])
            state["at_boot"] = code == 0 and "AUTO_START" in text
            code, text = _out(run, ["sc", "qfailure", service])
            state["restarts"] = code == 0 and "RESTART" in text.upper()
    elif os_name in ("debian", "fedora", "linux"):
        code, text = _out(run, ["systemctl", "is-active", service])
        state["installed"] = "could not be found" not in text and code != 4
        state["running"] = code == 0 and text.strip().startswith("active")
        code, text = _out(run, ["systemctl", "is-enabled", service])
        state["at_boot"] = code == 0 and text.strip().startswith("enabled")
        code, text = _out(run, ["systemctl", "show", "-p", "Restart", service])
        state["restarts"] = "Restart=on-failure" in text or "Restart=always" in text
    elif os_name == "macos":
        code, text = _out(run, ["launchctl", "print", f"system/{service}"])
        state["installed"] = code == 0
        state["running"] = code == 0 and "state = running" in text
        state["at_boot"] = code == 0          # a LaunchDaemon in the system domain
        state["restarts"] = code == 0 and "keepalive" in text.lower()
    state["answers"] = (answers or _port_answers)(port)
    return state


def restart_service(os_name: str, service: str, port: int, *, run: Runner = subprocess.run,
                    answers: Callable[[int], bool] | None = None, sleep: Callable[[float], None] = time.sleep,
                    wait_s: float = 60.0, major: int = 0) -> bool:
    """Stop and start the service, and wait until the port answers. On
    Debian with *major*: that cluster only (``postgresql`` restarts every
    cluster, the one Kazma uses among them)."""
    if os_name == "windows":
        stop_service(os_name, service, run=run, sleep=sleep, wait_s=30.0)
        _out(run, ["sc", "start", service])
    elif os_name == "macos":
        _out(run, ["launchctl", "kickstart", "-k", f"system/{service}"])
    elif os_name == "debian" and major:
        _out(run, ["sudo", "pg_ctlcluster", str(major), "main", "restart"])
    else:
        _out(run, ["sudo", "systemctl", "restart", service])
    return wait_for_port(port, answers=answers, sleep=sleep, wait_s=wait_s)


def stop_service(os_name: str, service: str, *, run: Runner = subprocess.run,
                 sleep: Callable[[float], None] = time.sleep, major: int = 0, wait_s: float = 90.0) -> bool:
    """Stop the service (Debian with *major*: that cluster) and wait until
    the OS says it stopped. True when it is stopped (or was)."""
    if os_name == "windows":
        code, text = _out(run, ["sc", "query", service])
        if code == 0 and "STOPPED" in text:
            return True
        _out(run, ["sc", "stop", service])
        waited = 0.0
        while waited <= wait_s:
            code, text = _out(run, ["sc", "query", service])
            if code != 0 or "STOPPED" in text:
                return code == 0
            sleep(1.0)
            waited += 1.0
        return False
    if os_name == "macos":
        return _out(run, ["sudo", "brew", "services", "stop", service.removeprefix("homebrew.mxcl.")])[0] == 0
    if os_name == "debian" and major:
        code, text = _out(run, ["sudo", "pg_ctlcluster", str(major), "main", "stop"])
        return code == 0 or "not running" in text.lower()
    return _out(run, ["sudo", "systemctl", "stop", service])[0] == 0


def start_service(os_name: str, service: str, port: int, *, run: Runner = subprocess.run,
                  answers: Callable[[int], bool] | None = None, sleep: Callable[[float], None] = time.sleep,
                  major: int = 0, wait_s: float = 60.0) -> bool:
    """Start the service (Debian with *major*: that cluster) and wait until
    the port answers."""
    if os_name == "windows":
        _out(run, ["sc", "start", service])
    elif os_name == "macos":
        _out(run, ["sudo", "brew", "services", "start", service.removeprefix("homebrew.mxcl.")])
    elif os_name == "debian" and major:
        _out(run, ["sudo", "pg_ctlcluster", str(major), "main", "start"])
    else:
        _out(run, ["sudo", "systemctl", "start", service])
    return wait_for_port(port, answers=answers, sleep=sleep, wait_s=wait_s)


def set_port_on_disk(data_dir: str | Path, port: int) -> None:
    """``port`` in the data folder's ``postgresql.auto.conf`` (where ``ALTER
    SYSTEM`` writes, and which wins over ``postgresql.conf``). Read at the
    server's next start; written whether it runs or not."""
    from kazma_core.atomic_files import atomic_write_text

    path = Path(data_dir) / "postgresql.auto.conf"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = ["# Do not edit this file manually!", "# It will be overwritten by the ALTER SYSTEM command."]
    kept = [line for line in lines if line.split("=", 1)[0].strip().lower() != "port"]
    atomic_write_text(path, "\n".join([*kept, f"port = '{int(port)}'"]) + "\n")


def wait_for_port(port: int, *, answers: Callable[[int], bool] | None = None,
                  sleep: Callable[[float], None] = time.sleep, wait_s: float = 60.0) -> bool:
    answers = answers or _port_answers
    waited = 0.0
    while waited <= wait_s:
        if answers(port):
            return True
        sleep(1.0)
        waited += 1.0
    return False


# -- the record -------------------------------------------------------------------------------


def _record_path() -> Path:
    from kazma_core.paths import data_dir

    return data_dir() / "setup_record.json"


def read_record(path: Path | None = None) -> dict[str, Any]:
    """What ``kazma setup`` recorded (empty when nothing was set up)."""
    try:
        data = json.loads((path or _record_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class PostgresRecord:
    os: str
    manager: str
    service: str
    port: int
    bin_dir: str
    major: int = PG_MAJOR
    wal_archive_dir: str = ""
    installed_at: float = 0.0
    #: Where this server's base backups go (``wal_archive``). Empty: the
    #: folder every install used before 18 (``<data dir>/backups/pg/base``).
    base_backup_dir: str = ""
    #: What the offsite copy's names carry after ``pgwal-``/``pgbase-``: each
    #: major numbers its WAL segments from the start again.
    offsite_tag: str = ""
    #: The server this one replaced (``postgres_upgrade``): stopped and kept,
    #: with its archive and base backups, for a restore to a time before.
    previous: dict[str, Any] = field(default_factory=dict)


def write_record(rec: PostgresRecord, path: Path | None = None) -> Path:
    """Record the database service (atomically): ``{"version": 1, "postgres": {...}}``."""
    from kazma_core.atomic_files import atomic_write_text

    target = path or _record_path()
    data = read_record(target)
    data.update({"version": 1, "postgres": asdict(rec)})
    atomic_write_text(target, json.dumps(data, indent=2))
    return target


def bin_dir_for(p: Platform, major: int = PG_MAJOR) -> str:
    return _bin_dir(p, major)


def is_admin() -> bool:
    """Whether this process can install a service (Windows: elevated; else root
    or sudo, which each step asks for itself)."""
    if os.name != "nt":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False
