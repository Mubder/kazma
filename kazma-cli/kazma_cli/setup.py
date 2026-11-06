"""``kazma setup postgres`` -- Postgres 16 as an OS service (resilience plan A1).

One command for every OS: winget (Windows), Homebrew (macOS), apt
(Debian/Ubuntu, WSL) or dnf (Fedora/RHEL). It prints the plan and asks
before doing anything (``-y`` skips the question), then installs Postgres 16
as a service that starts at boot and restarts on failure, listening on this
machine only, creates Kazma's database and its ``kazma_app`` role (not a
superuser), turns on WAL archiving, writes ``KAZMA_DATABASE_URL`` into the
install's ``.env`` (never printed), signs in once as Kazma will, and records
what it did in ``<data dir>/setup_record.json`` for ``kazma doctor`` and the
guard.

``--check`` reports and changes nothing. ``--dry-run`` prints the commands.
A second run checks and repairs (start at boot, restart on failure, the
role) and never reinstalls. ``--move-from-container NAME`` switches Kazma
from the Docker container it uses today in the same run: it stops Kazma
through its guard (once no chat turn runs), copies the container's data into
the new server and compares every Kazma table's row count, writes ``.env``,
starts Kazma on the new database, sees it signed in there, and stops the
container (kept, not removed). Run again, it replaces an earlier copy only
if Kazma never started on it (:mod:`kazma_core.backup.pg_move`); on a healthy
service whose address ``.env`` already names, it is the move only and needs
no admin rights.

The superuser's password is kept in ``<install>/.kazma/postgres_superuser.pass``
(this account only); the role's only in ``.env``. Neither is printed, and a
password is never on a command line except the Windows installer's throwaway
one, replaced by the next step.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path


def _say(text: str = "") -> None:
    print(text, flush=True)


def _scrub(text: str, secrets_: list[str]) -> str:
    for value in secrets_:
        if value:
            text = text.replace(value, "****")
    return text


def _env_path() -> Path:
    from kazma_core.paths import get_project_root

    return get_project_root() / ".env"


def _set_env_value(path: Path, key: str, value: str) -> None:
    """Set ``key=value`` in *path* (added, or replacing the line), restricted
    to this account."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    from kazma_core.atomic_files import atomic_write_text

    atomic_write_text(path, "\n".join(out) + "\n")
    from kazma_core.observability.outside_watch import _restrict_to_owner

    _restrict_to_owner(path)


def _env_file_value(path: Path, key: str) -> str | None:
    """*key*'s value in an env file (the last line that sets it), or None."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    found = None
    for line in lines:
        name, sep, value = line.partition("=")
        if sep and name.strip() == key and not line.lstrip().startswith("#"):
            found = value.strip().strip('"').strip("'")
    return found


def _existing_app_password(port: int) -> str | None:
    """The kazma_app password in the install's current address, when it points here."""
    from urllib.parse import unquote, urlsplit

    from kazma_core.db.backend import get_database_url

    url = get_database_url() or _env_file_value(_env_path(), "KAZMA_DATABASE_URL") or ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.username == "kazma_app" and parts.hostname in ("127.0.0.1", "localhost") and parts.port == port:
        return unquote(parts.password or "") or None
    return None


def _superuser_pass_file() -> Path:
    from kazma_core.paths import user_home

    return user_home() / "postgres_superuser.pass"


def _role_sql() -> str:
    from kazma_core.paths import get_project_root

    return (get_project_root() / "deploy" / "postgres" / "kazma_app_role.sql").read_text(encoding="utf-8")


#: A line of an installer's progress display: bars, a spinner, "59.0 MB /
#: 358 MB", "45%". winget redraws one such line per megabyte.
_PROGRESS_LINE = re.compile(
    r"^[\s▀-▟\-\\|/.]*"
    r"(\d+(\.\d+)?\s*[KMGT]?i?B\s*/\s*\d+(\.\d+)?\s*[KMGT]?i?B|\d{1,3}(\.\d+)?\s*%)?[\s▀-▟]*$")


def _readable(output: str) -> str:
    """An installer's output without its progress display: what it SAID."""
    lines = re.split(r"[\r\n]+", output)
    return "\n".join(line.rstrip() for line in lines if not _PROGRESS_LINE.match(line)).strip()


def _exit_text(code: int) -> str:
    """An exit code as Windows documents it: an HRESULT (winget's are) in hex."""
    return f"0x{code & 0xFFFFFFFF:08X}" if code >= 0x80000000 or code < -0x10000 else str(code)


def _run_step(step, secrets_: list[str], *, run=subprocess.run) -> tuple[bool, str]:
    env = {**os.environ, **step.env}
    # A step reads what it is given, or nothing: an installer handed the
    # terminal waited for a key nobody knew it wanted (the drill VM,
    # 2026-11-06: "I need to press enter to move to the next step").
    feed = {"input": step.stdin} if step.stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        # UTF-8: winget, apt and brew write it whatever the console's code page;
        # read as the ANSI code page, winget's own messages came back garbled.
        done = run(list(step.argv), **feed, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", env=env, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"could not run: {type(exc).__name__}"
    out = _scrub(_readable((done.stdout or "") + "\n" + (done.stderr or "")), secrets_)
    return done.returncode in step.ok_codes, f"exit {_exit_text(done.returncode)}" + (f": {out[-600:]}" if out else "")


def _print_check(platform, record: dict, state: dict | None) -> int:
    from kazma_core import postgres_service as pgs

    _say(f"platform    : {platform.os}" + (" (WSL)" if platform.wsl else "")
         + f", package manager: {platform.manager or 'none known'}")
    rec = record.get("postgres") or {}
    if not rec:
        _say("setup       : not set up by kazma setup postgres")
    else:
        _say(f"setup       : PostgreSQL {rec.get('major')} as {rec.get('service')} on port {rec.get('port')}")
    if state is None:
        return 1
    problems = 0
    for key, label in (("installed", "installed"), ("running", "running"), ("at_boot", "starts at boot"),
                       ("restarts", "restarts on failure"), ("answers", f"it answers on port {rec.get('port', pgs.DEFAULT_PORT)}")):
        value = state.get(key)
        mark = "ok" if value else ("??" if value is None else "NO")
        problems += 0 if value else 1
        _say(f"  {mark:4} {label}")
    if problems:
        _say("Repair it with: kazma setup postgres")
    return 0 if not problems else 1


def run(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="kazma setup", description="Set up what Kazma runs on.")
    sub = parser.add_subparsers(dest="what", required=True)
    pg = sub.add_parser("postgres", help="PostgreSQL 16 as an OS service")
    pg.add_argument("-y", "--yes", action="store_true", help="do not ask before acting")
    pg.add_argument("--check", action="store_true", help="report only; change nothing")
    pg.add_argument("--dry-run", action="store_true", help="print the commands; run nothing")
    pg.add_argument("--port", type=int, default=5432)
    pg.add_argument("--move-from-container", metavar="NAME",
                    help="copy the data of this Docker container (Kazma's database today) into the new server")
    pg.add_argument("--no-env", action="store_true", help="do not write KAZMA_DATABASE_URL into .env")
    system = sub.add_parser("system", help="OCR, LibreOffice, ClamAV, the Arabic fonts and Docker")
    system.add_argument("-y", "--yes", action="store_true", help="do not ask before acting")
    system.add_argument("--check", action="store_true", help="report only; change nothing")
    system.add_argument("--dry-run", action="store_true", help="print the plan; run nothing")
    options = parser.parse_args(args)
    if options.what == "system":
        return _setup_system(options)
    return _setup_postgres(options)


def _print_states(states) -> int:
    from kazma_core.system_deps import LABELS

    problems = 0
    for state in states:
        mark = "ok" if state.ok else ("??" if state.ok is None else "NO")
        problems += 1 if state.ok is False else 0
        _say(f"  {mark:4} {LABELS[state.name]}: {state.detail}")
    return problems


#: How often ``kazma setup system`` tries a step, and how long it waits in
#: between. A download can be cut off (live 2026-11-06, a fresh VM: winget
#: answered 0x80004004 "Cancelled" at 59 of 358 MB of LibreOffice), and a
#: second run usually goes through.
_SYSTEM_ATTEMPTS = 3
_SYSTEM_RETRY_WAIT_S = 15.0


def _ask(ask, question: str) -> str:
    try:
        return ask(question)
    except EOFError:  # no terminal to answer from: no
        return ""


def _setup_system(options, *, run=subprocess.run, which=None, ask=input, platform=None, finders=None,
                  sleep=time.sleep) -> int:
    """``kazma setup system`` (resilience plan K2): check, show the plan, ask,
    install what is missing, record it. A second run finds nothing to do.

    A failed step is tried again (:data:`_SYSTEM_ATTEMPTS`); a failed install
    whose package is there after all counts as done (an installer can fail
    after it installed). A dependency that still fails stops only its own
    later steps (Tesseract's models, ClamAV's schedule): the others are
    installed, and the end names what did not."""
    import shutil as _shutil

    from kazma_core import postgres_service as pgs
    from kazma_core.system_deps import (
        LABELS,
        DepState,
        check_all,
        fetch_tessdata,
        freshclam_agent_plist,
        install_plan,
        installs_package,
        package_is_in,
        record_system,
        tessdata_folder,
    )

    which = which or _shutil.which
    platform = platform or pgs.detect_platform()
    _say(f"platform: {platform.os}" + (" (WSL)" if platform.wsl else "")
         + f", package manager: {platform.manager or 'none known'}")
    states: list[DepState] = check_all(platform, run=run, which=which, **(finders or {}))
    problems = _print_states(states)
    if options.check:
        record_system(states)
        if problems:
            _say("Install what is missing with: kazma setup system")
        return 1 if problems else 0
    steps = install_plan(platform, states)
    if not steps:
        _say("Nothing to install." if not problems else
             "Kazma knows no package manager here (winget, Homebrew, apt, dnf): install the missing ones by hand.")
        record_system(states)
        return 0 if not problems else 2
    _say("The plan:")
    for i, step in enumerate(steps, 1):
        _say(f"  {i:2}. {step.title}\n      {step.printable()}")
    if options.dry_run:
        return 0
    if platform.os == "windows" and any(s.admin for s in steps) and not pgs.is_admin():
        _say("Run this from an elevated terminal (Run as administrator): installing needs it.")
        return 2
    if not options.yes and _ask(ask, "Install what is missing now? [y/N] ").strip().lower() not in ("y", "yes"):
        _say("Nothing was done.")
        return 1

    def do(step) -> tuple[bool, str]:
        if step.action.startswith("tessdata:"):
            folder = tessdata_folder(which)
            try:
                if folder is None:
                    raise ValueError("tesseract was not found after its install")
                fetch_tessdata(step.action.split(":", 1)[1], folder)
                return True, ""
            except (OSError, ValueError) as exc:
                return False, str(exc)
        if step.action == "freshclam_agent":
            freshclam = which("freshclam") or "/opt/homebrew/bin/freshclam"
            agent = Path.home() / "Library" / "LaunchAgents" / "ai.kazma.freshclam.plist"
            try:
                agent.parent.mkdir(parents=True, exist_ok=True)
                agent.write_text(freshclam_agent_plist(freshclam), encoding="utf-8")
                return _run_step(type(step)("load it", ("launchctl", "load", "-w", str(agent))), [], run=run)
            except OSError as exc:
                return False, str(exc)
        return _run_step(step, [], run=run)

    def installed_anyway(step) -> bool:
        if not installs_package(step):
            return False
        found = {s.name: s for s in check_all(platform, run=run, which=which, **(finders or {}))}
        return step.dep in found and package_is_in(found[step.dep])

    failed_deps: set[str] = set()
    not_done: list[str] = []
    for step in steps:
        if step.dep in failed_deps:
            _say(f"-> {step.title}: not done ({LABELS[step.dep]} did not install)")
            not_done.append(step.title)
            continue
        _say(f"-> {step.title}")
        ok, detail = False, ""
        for attempt in range(1, _SYSTEM_ATTEMPTS + 1):
            ok, detail = do(step)
            if not ok and installed_anyway(step):
                _say(f"   installed, though the installer answered {detail.split(':', 1)[0]}")
                ok = True
            if ok or attempt == _SYSTEM_ATTEMPTS:
                break
            _say(f"   failed ({detail.split(':', 1)[0]}); trying again in {_SYSTEM_RETRY_WAIT_S:.0f} s"
                 f" (attempt {attempt + 1} of {_SYSTEM_ATTEMPTS})")
            sleep(_SYSTEM_RETRY_WAIT_S)
        if not ok:
            _say(f"   FAILED after {_SYSTEM_ATTEMPTS} attempts: {detail}")
            not_done.append(step.title)
            if step.dep:
                failed_deps.add(step.dep)
    after = check_all(platform, run=run, which=which, **(finders or {}))
    record_system(after)
    _say("After the install:")
    left = _print_states(after)
    if not_done:
        _say("Not done: " + "; ".join(not_done) + ". Run kazma setup system again to finish.")
    return 0 if not left and not not_done else 1


#: The pause ``--move-from-container`` asks Kazma's guard for while it copies.
_MOVE_PAUSE_REASON = "kazma setup postgres: moving Kazma's database"
#: How long the move waits for running chat turns before it gives up.
_MOVE_IDLE_TIMEOUT_S = 900.0
#: How long it waits to see Kazma signed in on the new server after the start.
_SIGN_IN_WAIT_S = 120.0


class _MoveHold:
    """Kazma stopped through its guard while its database is copied.

    The first move (live, 2026-11-06) copied the container while Kazma kept
    writing to it and then said to reload: a reload would have started Kazma
    on a copy missing everything written since. So the move stops Kazma
    first (once no chat turn runs), holds it while the copy is made and
    compared, and starts it again on the new database after ``.env`` names
    it -- one run, nothing written in between. A Kazma no guard supervises is
    not stopped here: the move is refused before anything changes, and the
    operator stops it.
    """

    def __init__(self) -> None:
        from kazma_cli.update import _find_git_root, _is_server_running, _load_guard, _resolve_server_port

        self.guard = _load_guard(_find_git_root())
        self.running = _is_server_running(_resolve_server_port())
        self.held = False

    def refusal(self) -> str | None:
        """Why the move must not start (Kazma runs and nothing can stop it), or None."""
        if not self.running:
            return None
        plan = str(self.guard.maintenance_preview(_MOVE_PAUSE_REASON)) if self.guard is not None else "no_guard"
        if plan == "guard":
            return None
        why = "its guard is paused for something else" if plan == "paused" else "no guard supervises it"
        return (f"Kazma is running and {why}, so it cannot be stopped while its database is copied: "
                "what it wrote during the copy would be lost. Stop Kazma, run this again, then start it. "
                "Nothing was done.")

    def stop(self) -> bool:
        """Kazma stopped and held (or it was not running). False: it still runs."""
        if not self.running:
            return True
        _say("-> Stopping Kazma through its guard once no chat turn runs (held while the data is copied)")
        try:
            outcome = str(self.guard.stop_for_maintenance(_MOVE_PAUSE_REASON, idle_timeout_s=_MOVE_IDLE_TIMEOUT_S))
        except OSError as exc:  # the pause file could not be written
            _say(f"   The guard could not stop Kazma ({exc}).")
            return False
        if outcome == "held":
            self.held = True
            return True
        _say({"busy": f"   A chat turn was still running after {_MOVE_IDLE_TIMEOUT_S / 60:.0f} minutes: "
                      "Kazma was not stopped.",
              "not_held": "   The guard did not stop Kazma in time."}.get(
            outcome, f"   The guard did not stop Kazma ({outcome})."))
        return False

    def resume(self) -> str:
        """Lift the hold and wait for Kazma: ``"up"``, ``"slow"``, ``"unsupervised"``,
        ``"error"``, or ``"not_held"`` when this run held nothing."""
        if not self.held:
            return "not_held"
        try:
            result = str(self.guard.resume_after_maintenance(_MOVE_PAUSE_REASON))
        except OSError as exc:  # the pause file could not be removed
            _say(f"   Could not start Kazma again ({exc}): python scripts/service/kazma_guard.py --resume")
            return "error"
        self.held = False
        return result


def _kazma_signed_in(url: str, *, wait_s: float = _SIGN_IN_WAIT_S) -> bool:
    """A session of Kazma's role, other than this one, is connected to its
    database at *url* within *wait_s*: Kazma started on the new server."""
    import time

    import psycopg

    deadline = time.monotonic() + wait_s
    while True:
        try:
            with psycopg.connect(url, connect_timeout=10, autocommit=True) as conn:
                seen = conn.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND usename = current_user AND pid <> pg_backend_pid()").fetchone()[0]
            if seen:
                return True
        except psycopg.Error:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(2.0)


#: Memory backend addresses that keep their own copy of a database address
#: (no fallback to KAZMA_DATABASE_URL): repointed when they name the container.
_MEMORY_ADDRESS_KEYS = ("memory.backends.state.url", "memory.backends.vector.url")


def _container_port(name: str, *, run=subprocess.run) -> int | None:
    """The port on this machine the container publishes its Postgres on."""
    from kazma_core.docker_cli import find_docker_cli

    docker = find_docker_cli()
    if not docker:
        return None
    try:
        done = run([docker, "port", name, "5432/tcp"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                   timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (done.stdout or "").splitlines() if done.returncode == 0 else []:
        port = line.strip().rsplit(":", 1)[-1]
        if port.isdigit():
            return int(port)
    return None


def _names_container(address: str | None, port: int, database: str) -> bool:
    """*address* is the container's database on this machine."""
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(address or "")
        return (parts.scheme.startswith("postgres") and parts.hostname in ("127.0.0.1", "localhost", "::1")
                and parts.port == port and (parts.path or "").lstrip("/") == database)
    except ValueError:
        return False


def _repoint_memory_backends(container: str, new_url: str, *, run=subprocess.run) -> tuple[list[str], list[str]]:
    """Each memory backend address that names the container's database,
    pointed at the new server. ``(changed, by_hand)``: the keys changed in the
    vault, and those stored in plain text that must be set in Settings.

    The memory mirror keeps its own address (FA-218): left on the container,
    it would write to a database nothing else reads, or fail once the
    container is stopped. Read from the new server's own settings (the copy),
    changed only through the vault, where Kazma keeps these addresses."""
    import json
    from urllib.parse import urlsplit

    import psycopg

    from kazma_core.security.vault import get_vault

    port = _container_port(container, run=run)
    if port is None:
        return [], []
    database = (urlsplit(new_url).path or "/kazma").lstrip("/")
    vault = get_vault()
    changed: list[str] = []
    by_hand: list[str] = []
    with psycopg.connect(new_url, connect_timeout=10, autocommit=True) as conn:
        for key in _MEMORY_ADDRESS_KEYS:
            row = conn.execute("SELECT value FROM kazma_settings WHERE key = %s", (key,)).fetchone()
            if not row:
                continue
            try:
                stored = json.loads(row[0])
            except (TypeError, ValueError):
                stored = row[0]
            if not isinstance(stored, str):
                continue
            if stored.startswith("vault://") and vault is not None:
                name = stored[len("vault://"):]
                if _names_container(vault.retrieve(name), port, database):
                    vault.store(name, new_url)
                    changed.append(key)
            elif _names_container(stored, port, database):
                by_hand.append(key)
    return changed, by_hand


def _stop_container(name: str, *, run=subprocess.run) -> bool:
    """The container stopped and kept (never removed): it is the way back."""
    from kazma_core.docker_cli import find_docker_cli

    docker = find_docker_cli()
    if not docker:
        return False
    for argv in ([docker, "update", "--restart=no", name], [docker, "stop", name]):
        try:
            done = run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.SubprocessError):
            return False
        if done.returncode != 0:
            return False
    return True


def _setup_postgres(options, *, run=subprocess.run, hold: _MoveHold | None = None) -> int:
    from kazma_core import postgres_service as pgs

    platform = pgs.detect_platform()
    record = pgs.read_record()
    service = pgs.SERVICE.get(platform.os)
    state = pgs.service_state(platform.os, service, options.port, run=run) if service else None
    if options.check:
        return _print_check(platform, record, state)
    if platform.manager is None:
        _say(f"No package manager Kazma knows on this {platform.os} system (winget, Homebrew, apt, dnf).")
        _say("Install PostgreSQL 16 by hand: docs/docs/ops/postgres-and-saas.md, \"Postgres as an OS service\".")
        return 2

    installed = bool(state and state.get("installed"))
    healthy = installed and all(state.get(k) for k in ("running", "at_boot", "restarts", "answers"))
    pass_file = _superuser_pass_file()
    stored_superuser = pass_file.read_text(encoding="utf-8").strip() if pass_file.exists() else ""
    superuser_password = stored_superuser or secrets.token_urlsafe(24)
    existing_app = _existing_app_password(options.port)
    app_password = existing_app or secrets.token_urlsafe(24)
    throwaway = secrets.token_urlsafe(18)
    hidden = [superuser_password, app_password, throwaway]
    container = options.move_from_container

    from kazma_core.backup import wal_archive

    archive_dir = wal_archive.default_archive_dir(platform)
    steps = pgs.setup_plan(platform, port=options.port, superuser_password=superuser_password,
                           throwaway_password=throwaway, app_password=app_password, role_sql=_role_sql(),
                           wal_archive_command=wal_archive.archive_command(platform, archive_dir))
    steps = wal_archive.prepare_steps(platform, archive_dir) + steps
    if installed:
        # A second run checks and repairs: it never reinstalls. The superuser's
        # password and the role are set again; the install steps are skipped.
        steps = [s for s in steps if not s.title.startswith(("Install", "Add the", "Create its data"))
                 and s.argv[:1] != ("",)]
        if not pass_file.exists() and platform.os == "windows":
            _say("Postgres is installed, but this install does not know its superuser's password")
            _say(f"({pass_file} is missing). Put it there, then run this again.")
            return 2
        if container and healthy and existing_app:
            # The service runs as set up and Kazma's address already names it:
            # this run is the move only, which needs no admin rights.
            steps = []

    if container:
        hold = hold or _MoveHold()
        refusal = hold.refusal()
        if refusal:
            _say(refusal)
            return 2

    _print_postgres_plan(f"PostgreSQL {pgs.PG_MAJOR} as a {platform.os} service ({service}) on "
                         f"127.0.0.1:{options.port}", options, steps, installed=installed, hold=hold)
    if options.dry_run:
        return 0
    if platform.os == "windows" and any(s.admin or s.action == "restart" for s in steps) and not pgs.is_admin():
        _say("Run this from an elevated terminal (Run as administrator): installing a service needs it.")
        return 2
    if not options.yes and input("Go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
        _say("Nothing was done.")
        return 1

    from kazma_core.atomic_files import atomic_write_text
    from kazma_core.observability.outside_watch import _restrict_to_owner

    if stored_superuser != superuser_password:
        atomic_write_text(pass_file, superuser_password + "\n")
        _restrict_to_owner(pass_file)

    if not _run_setup_steps(steps, platform, service, options.port, hidden, run=run):
        return 1

    url = pgs.database_url(app_password, options.port)
    if container:
        failed = _copy_with_kazma_held(container, url, options.port, hold, platform=platform,
                                       superuser_password=superuser_password, hidden=hidden,
                                       env_names_it=bool(existing_app), run=run)
        if failed is not None:
            return failed

    if not options.no_env:
        if _env_file_value(_env_path(), "KAZMA_DATABASE_URL") != url:
            _set_env_value(_env_path(), "KAZMA_DATABASE_URL", url)
            _say(f"-> KAZMA_DATABASE_URL written to {_env_path()} (this account only)")
        else:
            _say("-> .env already names the new server")

    from kazma_core.db.postgres_pool import refusal_reason

    reason = refusal_reason(url)
    if reason:
        _say(f"Signing in as kazma_app failed: {reason}")
        if container and hold.held and not options.no_env:
            _say("Kazma stays stopped: its .env names a server it cannot sign in to. Fix the sign-in, then "
                 "run this same command again.")
        elif hold is not None:
            hold.resume()
        return 1
    _say("-> Kazma signs in as kazma_app (not a superuser).")
    if container and not options.no_env:
        changed, by_hand = _repoint_memory_backends(container, url, run=run)
        for key in changed:
            _say(f"-> {key} pointed at the new server (it named the container)")
        for key in by_hand:
            _say(f"   {key} names the container and is stored in plain text: set it to the new server in "
                 "Settings -> Memory.")
    pgs.write_record(pgs.PostgresRecord(
        os=platform.os, manager=platform.manager, service=service, port=options.port,
        bin_dir=pgs.bin_dir_for(platform), wal_archive_dir=str(archive_dir), installed_at=__import__("time").time()))

    if not container:
        _say("Done. Reload Kazma when it is idle to use the new database:")
        _say("  python scripts/service/kazma_guard.py --reload --when-idle")
        return 0
    return _finish_move(container, url, hold, no_env=options.no_env, run=run)


def _print_postgres_plan(head: str, options, steps, *, installed: bool, hold: _MoveHold | None) -> None:
    container = options.move_from_container
    _say(head + (" -- installed: checking and repairing" if installed and steps else "")
         + (" -- installed and healthy: the move only" if installed and not steps else ""))
    for i, step in enumerate(steps, 1):
        title = step.title or "  (continued)"
        _say(f"  {i:2}. {title}\n      {step.printable()}")
    if container:
        if hold is not None and hold.running:
            _say("  then: once no chat turn runs, stop Kazma through its guard and hold it while the data is copied")
        _say(f"  then: copy the data of container {container} into it and compare row counts"
             + (" (an earlier copy there is replaced only if Kazma never started on it)" if installed else ""))
    if not options.no_env:
        _say("  then: write KAZMA_DATABASE_URL into .env (not shown), and sign in once as Kazma will")
    if container and not options.no_env:
        _say(f"  then: start Kazma on the new database, see it signed in there, and stop container {container}"
             " (kept, not removed)")


def _run_setup_steps(steps, platform, service, port: int, hidden: list[str], *, run=subprocess.run) -> bool:
    """Each step in order; False (said) at the first that fails."""
    from kazma_core import postgres_service as pgs

    for step in steps:
        _say(f"-> {step.title or step.printable()}")
        if step.action == "wait":
            ok = pgs.wait_for_port(port)
            detail = "" if ok else f"nothing answered on port {port} within a minute"
        elif step.action == "restart":
            ok = pgs.restart_service(platform.os, service, port, run=run)
            detail = "" if ok else "it did not answer after the restart"
        else:
            ok, detail = _run_step(step, hidden, run=run)
        if not ok:
            _say(f"   FAILED: {detail}")
            _say("Nothing past this step was done. Fix it and run kazma setup postgres again.")
            return False
    return True


def _copy_with_kazma_held(container: str, url: str, port: int, hold: _MoveHold, *, platform,
                          superuser_password: str, hidden: list[str], env_names_it: bool,
                          run=subprocess.run) -> int | None:
    """Kazma stopped, the container copied in and compared. None when the copy
    is whole (Kazma still held); else the exit code, Kazma started again only
    where that is safe: never on a database ``.env`` names that was emptied
    and not filled again."""
    from kazma_core import postgres_service as pgs
    from kazma_core.backup import pg_move

    if not hold.stop():
        _say("The data was NOT moved, and .env was not changed.")
        return 1
    emptied: list[bool] = []

    def _empty_target() -> bool:
        emptied.append(True)
        for step in pgs.empty_database_steps(platform, port=port, superuser_password=superuser_password,
                                             role_sql=_role_sql()):
            _say(f"-> {step.title}")
            ok, detail = _run_step(step, hidden, run=run)
            if not ok:
                _say(f"   FAILED: {detail}")
                return False
        return True

    if pg_move.move_from_container(container, url, port=port, say=_say, empty_target=_empty_target):
        return None
    if emptied and env_names_it:
        # .env names the new server, and its database no longer holds a whole copy.
        _say("Kazma stays stopped: its .env names the new server, and the database there is not a complete "
             "copy now. Run this same command again; it starts Kazma once the copy is whole. Or start Kazma "
             "as it is: python scripts/service/kazma_guard.py --resume")
        return 1
    _say("The data was NOT moved." + ("" if env_names_it else " .env was not changed: Kazma still uses the container."))
    hold.resume()
    return 1


def _finish_move(container: str, url: str, hold: _MoveHold, *, no_env: bool, run=subprocess.run) -> int:
    """Kazma started on the new database, seen signed in there, then the
    container stopped and kept -- never before Kazma is seen on the new one."""
    if no_env:
        hold.resume()
        _say(f"Done. The data is copied; .env was not changed (--no-env), so Kazma still uses {container}.")
        return 0
    if hold.held:
        _say("-> Starting Kazma on the new database")
        result = hold.resume()
        if result != "up":
            _say(f"Kazma has not answered yet ({result}); its guard keeps at it. Container {container} is left "
                 "running until Kazma is seen on the new server: python scripts/service/kazma_guard.py --status")
            return 1
        if not _kazma_signed_in(url):
            _say(f"Kazma answers, but it was not seen signed in to the new server within "
                 f"{_SIGN_IN_WAIT_S:.0f} s. Container {container} is left running; check .env and "
                 "kazma doctor.")
            return 1
        _say("-> Kazma runs on the new database.")
    if _stop_container(container, run=run):
        _say(f"-> Container {container} stopped and kept (it will not start again by itself). It is the way "
             f"back; remove it with docker rm {container} (and its data volume) once you are sure.")
    else:
        _say(f"Container {container} could not be stopped: stop it with docker stop {container}.")
    _say("Done." if hold.running else "Done. Start Kazma; it uses the new database.")
    return 0


def run_restore(args: list[str]) -> int:
    """``kazma restore postgres --to TIME --into DIR``: lay the newest base
    backup older than TIME into a new folder and set it to replay the WAL up to
    TIME. The live database is never touched; the steps left are printed."""
    parser = argparse.ArgumentParser(prog="kazma restore", description="Restore the database to a time.")
    sub = parser.add_subparsers(dest="what", required=True)
    pg = sub.add_parser("postgres", help="point-in-time restore of Kazma's database")
    pg.add_argument("--to", required=True, help='the time to stop at, e.g. "2026-11-20 14:05"')
    pg.add_argument("--into", required=True, help="a new, empty folder for the restored data")
    options = parser.parse_args(args)

    from kazma_core import postgres_service as pgs
    from kazma_core.backup import wal_archive

    rec = pgs.read_record().get("postgres") or {}
    if not rec.get("wal_archive_dir"):
        _say("No WAL archive is set up on this install (kazma setup postgres turns it on).")
        return 2
    _say(f"Restoring to {options.to} into {options.into} (the live database is not touched).")
    try:
        steps = wal_archive.restore_to(options.to, Path(options.into), Path(rec["wal_archive_dir"]),
                                       os_name=rec.get("os", ""))
    except (ValueError, OSError) as exc:
        _say(f"Nothing was restored: {exc}")
        return 1
    _say("Laid out. What is left:")
    for i, step in enumerate(steps, 1):
        _say(f"  {i}. {step}")
    return 0


if __name__ == "__main__":
    from kazma_core.env_files import load_env_files

    load_env_files()
    sys.exit(run(sys.argv[1:]))
