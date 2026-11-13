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


#: winget's answer when this machine's copy of its package list was never set
#: up: 0x8A15000F, "Data required by the source is missing". A fresh Windows 11
#: (the clean VM, 2026-11-08) answered it to every install; ``winget source
#: update`` said "Done" and changed nothing, and registering the list's own
#: package -- Microsoft's signed MSIX, the fix winget's maintainers give --
#: made installs work.
WINGET_SOURCE_MISSING = 0x8A15000F
WINGET_SOURCE_PACKAGE = "https://cdn.winget.microsoft.com/cache/source.msix"


def _winget_source_missing(step, detail: str) -> bool:
    """Whether *step* is a winget command that failed for want of its package list."""
    return (bool(step.argv) and Path(step.argv[0]).stem.lower() == "winget"
            and detail.startswith(f"exit {_exit_text(WINGET_SOURCE_MISSING)}"))


def _register_winget_source(run, log) -> tuple[bool, str]:
    from kazma_core.postgres_service import Step

    repair = Step("register winget's package list",
                  ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                   "$ProgressPreference = 'SilentlyContinue'; "
                   f"Add-AppxPackage -Path '{WINGET_SOURCE_PACKAGE}' -ErrorAction Stop"))
    return _run_step(repair, [], run=run, log=log)


#: How long one setup step may run. The programs a step runs have network
#: timeouts of their own (uv, winget, apt), so this only guards against a
#: program that never ends. It was 30 minutes until the clean VM's install
#: (2026-11-08): at about 1 MB/s Kazma's optional packages took longer, uv was
#: killed mid-download, and the step said "could not run".
STEP_TIMEOUT_S = 3 * 3600


def _run_step(step, secrets_: list[str], *, run=subprocess.run, cwd: str | None = None,
              log=None) -> tuple[bool, str]:
    """Run one step; its whole output (scrubbed) goes to *log* when given, the
    last of it to the answer."""
    env = {**os.environ, **step.env}
    # A step reads what it is given, or nothing: an installer handed the
    # terminal waited for a key nobody knew it wanted (the drill VM,
    # 2026-11-06: "I need to press enter to move to the next step").
    feed = {"input": step.stdin} if step.stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        # UTF-8: winget, apt and brew write it whatever the console's code page;
        # read as the ANSI code page, winget's own messages came back garbled.
        done = run(list(step.argv), **feed, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", env=env, timeout=STEP_TIMEOUT_S, check=False,
                   **({"cwd": cwd} if cwd else {}))
    except subprocess.TimeoutExpired as exc:
        minutes = int((exc.timeout or STEP_TIMEOUT_S) // 60)
        if log is not None:
            log(f"{step.printable()} was still running after {minutes} min and was stopped")
        return False, f"still running after {minutes} min, stopped"
    except (OSError, subprocess.SubprocessError) as exc:
        if log is not None:
            log(f"could not run {step.printable()}: {exc}")
        return False, f"could not run: {type(exc).__name__}"
    out = _scrub(_readable((done.stdout or "") + "\n" + (done.stderr or "")), secrets_)
    if log is not None and out:
        log(out)
    return done.returncode in step.ok_codes, f"exit {_exit_text(done.returncode)}" + (f": {out[-600:]}" if out else "")


def _extra_requirements(extra: str) -> list[str]:
    """The requirements Kazma's *extra* adds (``kazma[postgres]``), from the
    installed package's metadata, else the checkout's pyproject.toml. A
    self-reference (``kazma[...]``) is left out."""
    from importlib.metadata import PackageNotFoundError, requires

    from packaging.requirements import Requirement

    try:
        lines = requires("kazma") or []
    except PackageNotFoundError:
        lines = []
    found: list[str] = []
    for line in lines:
        req = Requirement(line)
        if req.marker is None or req.name.lower() == "kazma":
            continue
        if req.marker.evaluate({"extra": extra}) and not req.marker.evaluate({"extra": ""}):
            extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
            found.append(f"{req.name}{extras}{req.specifier}")
    if found:
        return found
    import tomllib

    from kazma_core.paths import get_project_root

    try:
        data = tomllib.loads((get_project_root() / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    return [r for r in data.get("project", {}).get("optional-dependencies", {}).get(extra, [])
            if not r.strip().lower().startswith("kazma[")]


def _driver_step():
    """The step that installs Kazma's Postgres driver into the environment
    Kazma runs in, or None when it is there. ``kazma setup postgres`` wrote
    ``KAZMA_DATABASE_URL`` into an environment without it (the drill VM,
    2026-11-06): the server then waited for a database it could not reach.
    ``uv pip install`` adds; it never removes what is installed."""
    import shutil as _shutil

    from kazma_core.db.postgres_pool import driver_missing
    from kazma_core.postgres_service import Step

    if not driver_missing():
        return None
    requirements = _extra_requirements("postgres")
    if not requirements:
        raise RuntimeError("Kazma's postgres extra lists no packages here (no package metadata, "
                           "no pyproject.toml): install kazma[postgres] by hand")
    uv = _shutil.which("uv")
    argv = ((uv, "pip", "install", "--python", sys.executable, *requirements) if uv
            else (sys.executable, "-m", "pip", "install", *requirements))
    return Step("Install Kazma's Postgres driver (the kazma[postgres] extra)", argv)


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
    from kazma_core.db.postgres_pool import driver_missing

    driver_ok = driver_missing() is None
    problems += 0 if driver_ok else 1
    _say(f"  {'ok' if driver_ok else 'NO':4} Kazma's Postgres driver (psycopg) in {sys.prefix}")
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
    system = sub.add_parser("system", help="OCR, LibreOffice, ClamAV, the Arabic fonts, Node.js and Docker")
    system.add_argument("-y", "--yes", action="store_true", help="do not ask before acting")
    system.add_argument("--check", action="store_true", help="report only; change nothing")
    system.add_argument("--dry-run", action="store_true", help="print the plan; run nothing")
    engine = sub.add_parser("engine", help="Kazma's own container engine: Docker that starts at boot (Windows)")
    engine.add_argument("-y", "--yes", action="store_true", help="do not ask before acting")
    engine.add_argument("--check", action="store_true", help="report only; change nothing")
    engine.add_argument("--dry-run", action="store_true", help="print the plan; run nothing")
    engine.add_argument("--remove", action="store_true",
                        help="remove it (refused while another project's containers live in it)")
    engine.add_argument("--force", action="store_true", help="with --remove: remove it even then, with their data")
    engine.add_argument("--move-from-desktop", action="store_true",
                        help="move Kazma's containers from Docker Desktop to it (Windows): Kazma held while idle, "
                             "the old containers stopped and kept")
    from kazma_cli.install import add_install_parser

    add_install_parser(sub)
    options = parser.parse_args(args)
    if options.what == "system":
        return _setup_system(options)
    if options.what == "engine":
        return _setup_engine(options)
    if options.what == "install":
        from kazma_cli.install import install

        return install(options)
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


def _drop_typeahead() -> None:
    """Throw away keys pressed before a question: they are not its answer.

    The clean Windows VM's first install (2026-11-07) passed "Choose 1, 2 or
    3" without a key pressed: an Enter left in the console (the end of the
    pasted commands, or one pressed during the long package sync) answered
    it. Installers drain what was typed ahead before they ask.
    """
    try:
        if not sys.stdin.isatty():
            return
        if os.name == "nt":
            import msvcrt

            while msvcrt.kbhit():
                msvcrt.getwch()
        else:
            import termios

            termios.tcflush(sys.stdin, termios.TCIFLUSH)
    except (OSError, ValueError, AttributeError, ImportError):  # no console to drain: nothing typed ahead
        return


def _ask(ask, question: str) -> str:
    if ask is input:  # a person at this console: only what they type after the question counts
        _drop_typeahead()
    try:
        return ask(question)
    except EOFError:  # no terminal to answer from: no
        return ""


def run_system_step(step, *, which, run=subprocess.run, log=None, house_font=None) -> tuple[bool, str]:
    """One step of a system install: a program, or the three this module
    carries out itself (Tesseract's model download, the macOS signature job,
    Kazma's document font). *house_font* installs the font
    (``house_font_install.HouseFont``; tests pass a fake)."""
    from kazma_core.system_deps import fetch_tessdata, freshclam_agent_plist, tessdata_folder

    if step.action == "fonts:house":
        if house_font is None:
            from kazma_core.house_font_install import HouseFont

            house_font = HouseFont.for_this_machine()
        return house_font.install()
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
            return _run_step(type(step)("load it", ("launchctl", "load", "-w", str(agent))), [], run=run, log=log)
        except OSError as exc:
            return False, str(exc)
    ok, detail = _run_step(step, [], run=run, log=log)
    if not ok and _winget_source_missing(step, detail):
        if log is not None:
            log("winget's package list is not set up on this machine: registering it, then trying again")
        fixed, why = _register_winget_source(run, log)
        if not fixed:
            return False, f"{detail} -- registering winget's package list failed: {why}"
        return _run_step(step, [], run=run, log=log)
    return ok, detail


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
    from kazma_core.english_count import count_noun
    from kazma_core.system_deps import (
        LABELS,
        DepState,
        check_all,
        install_plan,
        installs_package,
        package_is_in,
        record_system,
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
        return run_system_step(step, which=which, run=run, house_font=(finders or {}).get("house_font"))

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
            _say(f"   FAILED after {count_noun(_SYSTEM_ATTEMPTS, 'attempt')}: {detail}")
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


_ENGINE_PIECES = (("wsl", "WSL 2"), ("distro", "the kazma-engine WSL distro"),
                  ("docker", "Docker Engine in it, answering"), ("cli", "Docker's command-line tools"),
                  ("context", "the kazma-engine Docker context"),
                  ("task", "the KazmaEngine task (starts it at boot, nobody signed in)"),
                  ("shutdown", "the KazmaEngineShutdown service (stops it cleanly when Windows shuts down)"))


def _say_engine(found) -> None:
    for key, label in _ENGINE_PIECES:
        _say(f"  {'NO' if key in found.missing else 'ok':4} {label}")


def _setup_engine(options, *, run=subprocess.run, ask=input, platform=None, opener=None,
                  admin=None) -> int:
    """``kazma setup engine``: Kazma's own container engine (AGENTS.md section 63).

    Windows: check it, show what is missing, ask, install or repair it (each
    step idempotent), or remove it with ``--remove``. Elsewhere Kazma's engine
    is the system's Docker service, which ``kazma setup system`` installs,
    starts at boot and lets this account use."""

    from kazma_core import container_engine_setup as ces
    from kazma_core import postgres_service as pgs

    platform = platform or pgs.detect_platform()
    if getattr(options, "move_from_desktop", False):
        return _move_from_desktop(options, run=run, ask=ask, platform=platform, opener=opener, admin=admin)
    if platform.os != "windows":
        if options.remove:
            _say("Kazma uses the system's Docker service here: there is no engine of Kazma's own to remove.")
            return 0
        _say("Kazma's container engine here is the system's Docker service: kazma setup system installs it, "
             "starts it at boot and lets this account use it.")
        return _setup_system(options, run=run, ask=ask, platform=platform)
    found = ces.check(run)
    _say("Kazma's container engine (Docker as a service, started at boot):")
    _say_engine(found)
    if options.remove:
        if not options.yes and _ask(ask, "Remove Kazma's engine, with every container, image and volume in it? "
                                         "[y/N] ").strip().lower() not in ("y", "yes"):
            _say("Nothing was done.")
            return 1
        ok, detail = ces.remove(run, _say, force=options.force)
        _say(detail if ok else f"Not removed: {detail}")
        if ok:
            _say("If Settings > System chose Kazma's engine, choose Docker Desktop there: until then Kazma "
                 "uses the docker CLI's current context.")
        return 0 if ok else 1
    if options.check:
        if not found.ok:
            _say("Install or repair it with: kazma setup engine")
        return 0 if found.ok else 1
    problem = ces.preflight(run)
    if problem:
        _say(f"Kazma's engine cannot run here: {problem}")
        return 2
    steps = ces.install_steps(found)
    _say("The plan:")
    for i, step in enumerate(steps, 1):
        _say(f"  {i:2}. {step.title}")
    if options.dry_run:
        return 0
    if any(s.admin for s in steps) and not (pgs.is_admin() if admin is None else admin):
        _say("Run this from an elevated terminal (Run as administrator): "
             + "; ".join(s.title for s in steps if s.admin) + " needs it.")
        return 2
    if not options.yes and _ask(ask, "Set Kazma's engine up now? [y/N] ").strip().lower() not in ("y", "yes"):
        _say("Nothing was done.")
        return 1
    stay = _keep_desktop_choice()
    if not _run_engine_steps(steps, run=run, opener=opener, command="kazma setup engine"):
        return 1
    _say("Kazma's engine is set up: it starts at boot, with nobody signed in.")
    if stay:
        _say("Kazma keeps using Docker Desktop, where its containers are. Move them to its own engine with: "
             "kazma setup engine --move-from-desktop")
    return 0


def _keep_desktop_choice() -> bool:
    """On an install that uses Docker Desktop, write that choice down before
    the engine is recorded: a recorded engine is Kazma's choice while nothing
    is chosen, and its next start would look for its containers on an empty
    engine (:func:`kazma_core.engine_move.keep_desktop`)."""
    from kazma_core import engine_move
    from kazma_core.safety.deployment_policy import policy_read_failures
    from kazma_core.settings_validation import SettingRejected

    try:
        if engine_move.keep_desktop():
            _say("-> containers.engine = desktop (Kazma's containers stay on Docker Desktop until they are moved)")
            return True
    except (*policy_read_failures(), SettingRejected) as exc:
        _say(f"   Kazma's container engine setting could not be read or saved ({type(exc).__name__}).")
    return False


def _run_engine_steps(steps, *, run=subprocess.run, opener=None, command: str) -> bool:
    """The engine's setup steps in order; False (said) at the first that
    fails or waits for a Windows restart."""
    from kazma_core import container_engine_setup as ces

    for step in steps:
        _say(f"-> {step.title}")
        ok, detail = ces.run_action(step.action, run=run, log=lambda m: _say(f"   {m}"),
                                    opener=opener)
        if ok and detail == ces.RESTART:
            _say(f"Restart Windows to finish WSL, then run {command} again: it continues where it stopped.")
            return False
        if not ok:
            _say(f"   FAILED: {detail}")
            _say(f"Run {command} again: it repairs what is missing and keeps what is there.")
            return False
        if detail:
            _say(f"   {detail}")
    return True


#: The pause the engine's move asks Kazma's guard for while it copies.
_ENGINE_MOVE_REASON = "kazma setup engine: moving Kazma's containers"
_MOVE_COMMAND = "kazma setup engine --move-from-desktop"


def _checkout_root(start: Path | None = None) -> Path | None:
    """The install this CLI runs from: the folder above it that holds Kazma's
    guard script, a git checkout or a source folder unpacked from a zip.
    Finding it by ``.git`` alone, the move found no guard on a zip install and
    refused to run (the drill VM, 2026-11-13); finding it from the working
    folder, it depended on where the command was typed."""
    current = (start or Path(__file__)).resolve().parent
    for _ in range(8):
        if (current / "scripts" / "service" / "kazma_guard.py").is_file():
            return current
        current = current.parent
    return None


def _install_root() -> Path | None:
    root = _checkout_root()
    return root if root is not None and (root / "scripts" / "document_worker_image.py").is_file() else None


def _say_move_plan(plan, steps) -> None:
    _say("The move from Docker Desktop to Kazma's own engine:")
    lines = [step.title for step in steps]
    lines.append("prepare it while Kazma runs: each container's image, the code sandbox's image, and the "
                 "document worker image when documents run in containers")
    lines.append("once no chat turn runs, stop Kazma through its guard and hold it while the containers move")
    for moving in plan.containers:
        volumes = ""
        if moving.volumes:
            from kazma_core.engine_move import _shown_volume

            shown = [_shown_volume(v) for v in moving.volumes]
            volumes = f"; its volume{'s' if len(shown) > 1 else ''} ({', '.join(shown)}) copied and checked"
        lines.append(f"{moving.name} ({', '.join(moving.purposes)}): stopped on Docker Desktop and kept{volumes}; "
                     "made on Kazma's engine" + (", started and seen answering" if moving.running
                                                 else " (stopped, as it is)"))
    if not plan.containers:
        lines.append("no container of Kazma's is on Docker Desktop: nothing to copy")
    lines.append("switch Kazma to its own engine (containers.engine = kazma), start it and see it ready")
    for i, line in enumerate(lines, 1):
        _say(f"  {i:2}. {line}")
    for note in plan.notes:
        _say(f"  note: {note}")
    _say("  If any step fails, everything is put back: Kazma stays on Docker Desktop, as it is.")


def _move_from_desktop(options, *, run=subprocess.run, ask=input, platform=None, opener=None, admin=None,
                       docker=None, hold=None, answering=None, journal=None) -> int:
    """``kazma setup engine --move-from-desktop`` (AGENTS.md section 63, the
    plan's Stage 3): set Kazma's engine up if it is not, then move Kazma's
    containers from Docker Desktop to it (:mod:`kazma_core.engine_move`).
    Every refusal comes before anything changes."""
    from kazma_core import engine_move as em
    from kazma_core import postgres_service as pgs
    from kazma_core.guard_dependencies import _port_answers, _read_record
    from kazma_core.safety.deployment_policy import policy_read_failures

    platform = platform or pgs.detect_platform()
    if platform.os != "windows":
        _say("Moving from Docker Desktop is for Windows: elsewhere Kazma's engine is the system's Docker "
             "service (kazma setup engine).")
        return 2
    docker = docker or em.Docker.found()
    if docker is None:
        _say("The docker command was not found: Docker Desktop's containers cannot be read. Nothing was done.")
        return 2
    hold = hold or _MoveHold(reason=_ENGINE_MOVE_REASON, what="its containers' data")
    journal = journal or em.Journal()
    record = hold.record_path()
    steps_for = em.MoveSteps(hold=hold, say=_say, answering=answering or _port_answers, guard_record=record,
                             root=_install_root(), python=sys.executable, run=run)
    try:
        if journal.unfinished():
            hold.adopt_leftover()
            outcome = em.recover(docker, journal, steps_for)
            if outcome == "finished":
                return 0
            if outcome != "rolled_back":
                return 1
        return _move_after_checks(options, docker, hold, journal, steps_for, run=run, ask=ask, opener=opener,
                                  admin=admin, previous=_read_record(record) if record else [])
    except policy_read_failures() as exc:
        _say(f"The move could not go on ({type(exc).__name__}). Run {_MOVE_COMMAND} again once Kazma's "
             "settings can be read.")
        return 1
    finally:
        if hold.held:  # a pause this run took over from an earlier one, never lifted by a move
            hold.resume()


def _move_after_checks(options, docker, hold, journal, steps_for, *, run, ask, opener, admin, previous) -> int:
    from kazma_core import container_engine_setup as ces
    from kazma_core import engine_move as em
    from kazma_core import postgres_service as pgs

    if em.stored_engine() == "kazma":
        _say("Kazma already uses its own engine (containers.engine = kazma): there is nothing to move. If its "
             "containers are still on Docker Desktop, choose Docker Desktop in Settings -> System -> Container "
             "engine, then run this again.")
        return 2
    plan = em.plan_move(docker, previous=previous, answering=steps_for.answering)
    refusals = list(plan.refusals)
    refusal = hold.refusal()
    if refusal:
        refusals.append(refusal)
    found = ces.check(run)
    if not found.ok:
        problem = ces.preflight(run)
        if problem:
            refusals.append(f"Kazma's engine cannot run here: {problem}")
    refusals += em.engine_conflicts(docker, plan)
    if refusals:
        for line in refusals:
            _say(f"Cannot move: {line}")
        _say("Nothing was done: Kazma stays on Docker Desktop.")
        return 2
    steps = ces.install_steps(found) if not found.ok else []
    _say("Kazma's container engine (Docker as a service, started at boot):")
    _say_engine(found)
    _say_move_plan(plan, steps)
    if options.dry_run or options.check:
        return 0
    if any(s.admin for s in steps) and not (pgs.is_admin() if admin is None else admin):
        _say("Run this from an elevated terminal (Run as administrator): "
             + "; ".join(s.title for s in steps if s.admin) + " needs it.")
        return 2
    if not options.yes and _ask(ask, "Move Kazma's containers now? [y/N] ").strip().lower() not in ("y", "yes"):
        _say("Nothing was done.")
        return 1
    _keep_desktop_choice()
    if steps and not _run_engine_steps(steps, run=run, opener=opener, command=_MOVE_COMMAND):
        _say("Nothing was moved: Kazma stays on Docker Desktop.")
        return 1
    return 0 if em.move(docker, plan, journal, steps_for) else 1


#: The pause ``--move-from-container`` asks Kazma's guard for while it copies.
_MOVE_PAUSE_REASON = "kazma setup postgres: moving Kazma's database"
#: How long the move waits for running chat turns before it gives up.
_MOVE_IDLE_TIMEOUT_S = 900.0
#: How long it waits to see Kazma signed in on the new server after the start.
_SIGN_IN_WAIT_S = 120.0


class _MoveHold:
    """Kazma stopped through its guard while its data is copied.

    The first move (live, 2026-11-06) copied the container while Kazma kept
    writing to it and then said to reload: a reload would have started Kazma
    on a copy missing everything written since. So the move stops Kazma
    first (once no chat turn runs), holds it while the copy is made and
    compared, and starts it again on the new database after ``.env`` names
    it -- one run, nothing written in between. A Kazma no guard supervises is
    not stopped here: the move is refused before anything changes, and the
    operator stops it. The engine's move (``kazma setup engine
    --move-from-desktop``) holds Kazma the same way, under its own reason.
    """

    def __init__(self, reason: str = _MOVE_PAUSE_REASON, what: str = "its database") -> None:
        from kazma_cli.update import _find_git_root, _is_server_running, _load_guard, _resolve_server_port

        self.reason = reason
        self.what = what
        self.guard = _load_guard(_find_git_root() or _checkout_root())
        self.running = _is_server_running(_resolve_server_port())
        self.held = False

    def refusal(self) -> str | None:
        """Why the move must not start (Kazma runs and nothing can stop it), or None."""
        if not self.running or self.held:
            return None
        plan = str(self.guard.maintenance_preview(self.reason)) if self.guard is not None else "no_guard"
        if plan == "guard":
            return None
        why = "its guard is paused for something else" if plan == "paused" else "no guard supervises it"
        return (f"Kazma is running and {why}, so it cannot be stopped while {self.what} is copied: "
                "what it wrote during the copy would be lost. Stop Kazma, run this again, then start it. "
                "Nothing was done.")

    def stop(self) -> bool:
        """Kazma stopped and held (or it was not running). False: it still runs."""
        if not self.running or self.held:
            return True
        _say("-> Stopping Kazma through its guard once no chat turn runs (held while the data is copied)")
        try:
            outcome = str(self.guard.stop_for_maintenance(self.reason, idle_timeout_s=_MOVE_IDLE_TIMEOUT_S))
        except OSError as exc:  # the pause file could not be written
            _say(f"   The guard could not stop Kazma ({exc}).")
            return False
        if outcome == "held":
            self.held = True
            return True
        from kazma_core.english_count import count_noun

        _say({"busy": f"   A chat turn was still running after {count_noun(round(_MOVE_IDLE_TIMEOUT_S / 60), 'minute')}: "
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
            result = str(self.guard.resume_after_maintenance(self.reason))
        except OSError as exc:  # the pause file could not be removed
            _say(f"   Could not start Kazma again ({exc}): python scripts/service/kazma_guard.py --resume")
            return "error"
        self.held = False
        return result

    def adopt_leftover(self) -> bool:
        """An earlier run of this move died while it held Kazma: its pause is
        still there, Kazma stopped. This run takes it over (and lifts it when
        it ends), instead of moving with Kazma thought not running."""
        if self.guard is None or self.held:
            return False
        try:
            current = self.guard.read_pause()
        except (OSError, ValueError):
            return False
        if isinstance(current, dict) and current.get("reason") == self.reason:
            self.held = self.running = True
            return True
        return False

    def ready(self) -> bool:
        """Kazma answers ready now, or answers that it waits for its owner's
        setup (a provider key): up, as the guard's own resume counts it."""
        if self.guard is None:
            return False
        try:
            result = self.guard.probe(self.guard.health_url(of_running_guard=True), 5.0)
        except (OSError, ValueError):
            return False
        return bool(result[0]) or bool(getattr(result, "answered", False) and getattr(result, "needs_setup", ()))

    def record_path(self) -> Path | None:
        """Where the guard reads which engine and containers Kazma uses."""
        find = getattr(self.guard, "_deps_record_path", None)
        try:
            return Path(find()) if find is not None else None
        except (OSError, ValueError, TypeError):
            return None


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
    # First, whatever else is done or skipped: without the driver nothing
    # after it reaches the database, and .env must not name a database Kazma
    # cannot reach. Added after the filter above (its title starts "Install").
    try:
        driver = _driver_step()
    except RuntimeError as exc:
        _say(str(exc))
        return 2
    if driver is not None:
        steps = [driver, *steps]

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
