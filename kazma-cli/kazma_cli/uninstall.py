"""``kazma uninstall``: take Kazma off this computer (2026-11-14, REMAINING 2.5).

What ``kazma setup install`` put on the machine is taken off in the order it
can be, and what it cannot know is safe to remove is kept and said:

* **Removed** -- Kazma stopped through its guard; the service that starts it at
  boot (the ``KazmaAgent`` task, the systemd user unit, the launchd agent);
  ClamAV's daily signature task setup made (``KazmaFreshclam``, Windows; the
  macOS job); the containers setup made from this install's compose files
  (their volumes kept); Kazma's own container engine (Windows: its tasks, its
  shutdown service, its WSL distro, its Docker command-line tools; refused
  while another project's containers live in it); Kazma's Python environment
  (``.venv``; on Windows after this command ends, as Python is running from it).
* **Kept unless asked** (``--remove-data``) -- the data folder (memory, chats,
  settings, the vault, backups), Kazma's home (``.kazma``: the install log,
  keys), the containers' volumes, and Kazma's database on the PostgreSQL setup
  installed. Asked, the database is first backed up into the data folder
  (``kazma-database-<date>.dump``, ``pg_restore`` reads it), then dropped with
  its role; the folders are MOVED to ``<home>/Kazma-uninstalled-<date>/``,
  never deleted; ``--purge`` deletes them for good. A database whose backup
  fails is not dropped.
* **Always kept, and said** -- the PostgreSQL server (other programs may use
  it; the command to remove it is shown), the
  system programs setup installed (Tesseract, LibreOffice, ClamAV, Node.js,
  the Visual C++ runtime: other programs may use them; each with its removal
  command), the fonts installed for all users, uv, and Kazma's source folder
  of a git install.

Nothing happens before the plan is shown and accepted (``-y`` accepts it);
``--dry-run`` shows it and changes nothing. A step that needs an
administrator on Windows is refused, named, when this terminal is not one.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kazma_cli.install import SERVICE_TASK

__all__ = ["Item", "Machine", "build_plan", "main"]

#: Exit codes: everything in the plan done; a step failed or was refused.
EXIT_DONE, EXIT_NOT_DONE, EXIT_CANCELLED = 0, 1, 2


@dataclass(frozen=True)
class Item:
    """One line of the plan. ``how``: ``remove``, ``move``, ``delete`` or
    ``keep``. ``argv`` is a program to run; ``action`` a step this module
    carries out itself."""

    what: str
    how: str
    why: str = ""
    argv: tuple[str, ...] = ()
    action: str = ""
    path: str = ""
    admin: bool = False
    ok_codes: tuple[int, ...] = (0,)


@dataclass
class Machine:
    """What the plan is made from (tests pass their own)."""

    os_name: str
    root: Path | None
    data: Path
    home: Path
    user_home: Path
    python: str = sys.executable
    run: Callable[..., Any] = subprocess.run
    #: Starts a program that outlives this one (the delayed delete of .venv on Windows).
    spawn: Callable[..., Any] = subprocess.Popen
    which: Callable[[str], str | None] = shutil.which
    admin: bool = True
    record: dict[str, Any] = field(default_factory=dict)
    now: Callable[[], float] = time.time

    @classmethod
    def here(cls) -> Machine:
        from kazma_core.paths import data_dir, user_home
        from kazma_core.postgres_service import is_admin, read_record

        root = _install_root()
        os_name = "windows" if os.name == "nt" else ("macos" if sys.platform == "darwin" else "linux")
        return cls(os_name=os_name, root=root, data=Path(data_dir()), home=Path(user_home()),
                   user_home=Path.home(), admin=is_admin(), record=read_record())


def _install_root() -> Path | None:
    """The git install this Kazma runs from (its source folder), or None."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "scripts" / "service" / "kazma_guard.py").is_file() and (parent / "pyproject.toml").is_file():
            return parent
    return None


def _size(path: Path) -> str:
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if total < 1024 or unit == "GB":
            return f"{total:.0f} {unit}" if unit == "B" else f"{total:.1f} {unit}"
        total /= 1024
    return "?"


def _answers(machine: Machine, argv: list[str]) -> bool:
    try:
        return machine.run(argv, capture_output=True, text=True, timeout=30,
                           stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _same_folder(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def _decoded(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    raw = raw or b""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw[:200]:
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _service_folder(machine: Machine) -> str | None:
    """The folder whose Kazma this computer's service starts: None when there is
    no service, "" when it cannot be told. The service's name is the same for
    every install, so an uninstall in one folder must not take another's."""
    if machine.os_name == "windows":
        try:
            done = machine.run(["schtasks", "/Query", "/TN", SERVICE_TASK, "/XML", "ONE"], capture_output=True,
                               timeout=30, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            return ""
        if done.returncode != 0:
            return None
        text = _decoded(done.stdout)
        found = re.search(r"<WorkingDirectory>(.*?)</WorkingDirectory>", text)
        if found:
            return found.group(1).strip()
        found = re.search(r'<Arguments>"?(.*?)[\\/]scripts[\\/]service', text)
        return found.group(1).strip() if found else ""
    if machine.os_name == "macos":
        unit = machine.user_home / "Library" / "LaunchAgents" / "com.kazma.agent.plist"
    else:
        unit = machine.user_home / ".config" / "systemd" / "user" / "kazma.service"
    if not unit.is_file():
        return None
    text = unit.read_text(encoding="utf-8", errors="replace")
    found = re.search(r"(\S+?)[\\/]scripts[\\/]service[\\/]kazma_guard\.py", text)
    return found.group(1).strip('"') if found else ""


# ── the plan ─────────────────────────────────────────────────────────────


def _service_items(machine: Machine, owner: str | None) -> list[Item]:
    items: list[Item] = []
    root = machine.root
    elsewhere = owner not in (None, "") and root is not None and not _same_folder(owner, root)
    if elsewhere:
        items.append(Item(f"Kazma's service ({SERVICE_TASK if machine.os_name == 'windows' else 'kazma'})", "keep",
                          f"it starts another Kazma, the one in the folder {owner!r}: left alone"))
    if root is not None:
        guard = str(root / "scripts" / "service" / "kazma_guard.py")
        items.append(Item("Stop Kazma (through its guard, once no chat is running)", "remove",
                          argv=(machine.python, guard, "--pause", "--stop", "--reason", "uninstall"),
                          ok_codes=(0, 1, 2)))
        if machine.os_name == "windows" and not elsewhere:
            if owner is not None:
                items.append(Item(f"Stop the guard ({SERVICE_TASK} task)", "remove",
                                  argv=("schtasks", "/End", "/TN", SERVICE_TASK), admin=True, ok_codes=(0, 1)))
                items.append(Item("Kazma's service (starts it at boot)", "remove",
                                  argv=(machine.python, str(root / "scripts" / "service" / "install_service.py"),
                                        "--uninstall"), admin=True))
        elif not elsewhere and owner is not None:
            items.append(Item("Kazma's service (starts it at boot)", "remove",
                              argv=(machine.python, str(root / "scripts" / "service" / "install_service.py"),
                                    "--uninstall"), ok_codes=(0, 1, 2)))
    if elsewhere:
        # The rest is the computer's, and the other Kazma uses it.
        return items
    if machine.os_name == "windows":
        from kazma_core.system_deps import FRESHCLAM_TASK

        if _answers(machine, ["schtasks", "/Query", "/TN", FRESHCLAM_TASK]):
            items.append(Item("ClamAV's daily signature task setup made", "remove",
                              argv=("schtasks", "/Delete", "/TN", FRESHCLAM_TASK, "/F"), admin=True))
    elif machine.os_name == "macos":
        from kazma_core.system_deps import FRESHCLAM_AGENT

        plist = machine.user_home / "Library" / "LaunchAgents" / f"{FRESHCLAM_AGENT}.plist"
        if plist.is_file():
            items.append(Item("ClamAV's daily signature job setup made", "remove", action="launchd_agent",
                              path=str(plist)))
    return items


def _container_items(machine: Machine, *, remove_data: bool) -> list[Item]:
    from kazma_core import container_services as cs
    from kazma_core.docker_cli import find_docker_cli

    root = machine.root
    docker = find_docker_cli() if root is not None else None
    if root is None or not docker:
        return []
    items: list[Item] = []
    env_path = root / ".env"
    for svc in cs.SERVICES:
        state = cs._image_state(svc, root, docker, run=machine.run)
        if state.state in ("absent", "not_ours", "unknown"):
            continue
        argv = [docker, "compose", "-f", str(root / svc.compose_file), "--env-file", str(env_path)]
        if svc.project:
            argv += ["-p", svc.project]
        if svc.profile:
            argv += ["--profile", svc.profile]
        argv += ["rm", "--stop", "--force", svc.service]
        items.append(Item(f"{svc.title} (its container{'' if remove_data else '; its data is kept'})",
                          "remove", argv=tuple(argv)))
        # Its data is its named volumes (compose's own rm never removes those):
        # removed only when asked, after the container that holds them.
        if remove_data and state.volumes:
            items.append(Item(f"{svc.title}'s data ({', '.join(state.volumes)})", "remove",
                              argv=(docker, "volume", "rm", *state.volumes)))
    return items


def _engine_items(machine: Machine, owner: str | None) -> list[Item]:
    if machine.os_name != "windows":
        return []
    if owner not in (None, "") and machine.root is not None and not _same_folder(owner, machine.root):
        return [Item("Kazma's container engine", "keep", f"left alone: it is used by the Kazma in the folder {owner!r}")]
    from kazma_core.container_engine import RECORD_KEY

    if not machine.record.get(RECORD_KEY):
        return []
    return [Item("Kazma's container engine (its WSL distro, tasks, shutdown service and Docker tools)",
                 "remove", "refused while another project's containers live in it", action="engine", admin=True)]


def _environment_items(machine: Machine) -> list[Item]:
    if machine.root is None:
        return [Item("Kazma's Python packages", "remove", argv=(machine.python, "-m", "pip", "uninstall", "-y", "kazma"))]
    venv = machine.root / ".venv"
    if not venv.is_dir():
        return []
    later = machine.os_name == "windows"
    return [Item(f"Kazma's Python environment ({venv}, {_size(venv)})", "remove",
                 "on Windows once this command ends: Python runs from it" if later else "",
                 action="remove_later" if later else "remove_tree", path=str(venv))]


def _data_items(machine: Machine, *, remove_data: bool, purge: bool) -> list[Item]:
    places = [(machine.data, "Kazma's data (memory, chats, settings, the vault, backups)"),
              (machine.home, "Kazma's home (the install log, keys)")]
    items: list[Item] = []
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(machine.now()))
    target = machine.user_home / f"Kazma-uninstalled-{stamp}"
    seen: set[Path] = set()
    for path, what in places:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        shown = f"{what}: {path} ({_size(path)})"
        if not remove_data:
            items.append(Item(shown, "keep", "kept; kazma uninstall --remove-data moves it aside"))
        elif purge:
            items.append(Item(shown, "delete", "deleted for good (--purge)", action="delete_tree", path=str(path)))
        else:
            items.append(Item(shown, "move", f"moved to {target}", action="move", path=str(path)))
    return items


def _database_items(machine: Machine, *, remove_data: bool) -> list[Item]:
    """Kazma's database on the PostgreSQL setup installed: backed up, then
    dropped -- only when asked (``--remove-data``)."""
    pg = machine.record.get("postgres") or {}
    if not pg or not remove_data:
        return []
    service = pg.get("service") or "PostgreSQL"
    return [Item(f"Kazma's database (kazma on {service})", "remove",
                 "backed up into the data folder first (kazma-database-<date>.dump), then dropped with its "
                 "role; not dropped if the backup fails", action="database")]


def _database(machine: Machine) -> tuple[bool, str]:
    """Back up Kazma's database into the data folder, then drop it and its role."""
    from kazma_core import postgres_service as ps

    pg = machine.record.get("postgres") or {}
    platform = ps.Platform(str(pg.get("os") or machine.os_name), pg.get("manager"))
    port, major = int(pg.get("port") or ps.DEFAULT_PORT), int(pg.get("major") or ps.PG_MAJOR)
    env = dict(os.environ)
    if platform.os == "windows":
        try:
            env["PGPASSWORD"] = (machine.home / "postgres_superuser.pass").read_text(encoding="utf-8").strip()
        except OSError:
            return False, "the superuser's password file is missing; nothing was dropped"
    psql = ps._psql(platform, port=port, major=major)
    dump_exe = str(Path(ps._bin_dir(platform, major)) / ("pg_dump.exe" if platform.os == "windows" else "pg_dump"))
    # As the server's superuser, as setup's psql runs (postgres_service._psql).
    prefix = ["sudo", "-u", "postgres"] if platform.os in ("debian", "fedora") else []
    connect = ["-h", "127.0.0.1", "-U", "postgres"] if platform.os == "windows" else (
        ["-h", "127.0.0.1"] if platform.os == "macos" else [])
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(machine.now()))
    dump = machine.data / f"kazma-database-{stamp}.dump"
    dump.parent.mkdir(parents=True, exist_ok=True)
    try:
        with dump.open("wb") as fh:
            done = machine.run([*prefix, dump_exe, *connect, "-w", "-p", str(port), "-Fc", ps.DATABASE],
                               stdout=fh, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=3600, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        dump.unlink(missing_ok=True)
        return False, f"the backup could not run ({exc}); nothing was dropped"
    if done.returncode != 0 or not dump.exists() or dump.stat().st_size == 0:
        dump.unlink(missing_ok=True)
        said = (done.stderr.decode("utf-8", "replace") if isinstance(done.stderr, bytes) else str(done.stderr or ""))
        return False, f"the backup failed ({said.strip()[-200:] or done.returncode}); nothing was dropped"
    sql = f"DROP DATABASE IF EXISTS {ps.DATABASE};\nDROP ROLE IF EXISTS {ps.APP_ROLE};\n"
    try:
        dropped = machine.run(list(psql), input=sql, capture_output=True, text=True, timeout=300, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"backed up to {dump}, but not dropped ({exc})"
    if dropped.returncode != 0:
        said = (dropped.stderr or dropped.stdout or "").strip().splitlines()
        return False, f"backed up to {dump}, but not dropped ({(said[-1] if said else dropped.returncode)})"
    return True, f"backed up to {dump}, then dropped"


def _kept_items(machine: Machine) -> list[Item]:
    from kazma_core.system_deps import DEPENDENCIES, LABELS, PACKAGES

    items: list[Item] = []
    pg = machine.record.get("postgres") or {}
    if pg:
        service = pg.get("service") or "PostgreSQL"
        items.append(Item(f"PostgreSQL ({service})", "keep",
                          "other programs may use it. Kazma's database on it goes with --remove-data (backed "
                          "up first); the server: your package manager"))
    manager = {"windows": "winget", "macos": "brew"}.get(machine.os_name, "apt")
    for dep in DEPENDENCIES:
        packages = (PACKAGES.get(manager) or {}).get(dep)
        if not packages:
            continue
        command = {"winget": "winget uninstall --id {}", "brew": "brew uninstall {}",
                   "apt": "sudo apt remove {}"}[manager].format(" ".join(packages))
        items.append(Item(LABELS.get(dep, dep), "keep", f"other programs may use it; to remove: {command}"))
    items.append(Item("The fonts installed for all users (IBM Plex Sans Arabic)", "keep",
                      "documents on this computer may use them"))
    if machine.root is not None:
        items.append(Item(f"Kazma's source folder ({machine.root})", "keep", "delete it when you like"))
    return items


def build_plan(machine: Machine, *, remove_data: bool = False, purge: bool = False) -> list[Item]:
    """Every line of the uninstall, in the order it runs."""
    owner = _service_folder(machine)
    return [*_service_items(machine, owner), *_container_items(machine, remove_data=remove_data),
            *_engine_items(machine, owner), *_environment_items(machine),
            # The database's backup lands in the data folder, which moves (or goes) after it.
            *_database_items(machine, remove_data=remove_data),
            *_data_items(machine, remove_data=remove_data, purge=purge), *_kept_items(machine)]


# ── running it ───────────────────────────────────────────────────────────


def _remove_later(path: Path, machine: Machine) -> tuple[bool, str]:
    """Windows: delete *path* once this process has ended (Python runs from it)."""
    script = (f'ping -n 6 127.0.0.1 >nul & rmdir /s /q "{path}"')
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        machine.spawn(["cmd.exe", "/c", script], creationflags=flags, close_fds=True,  # noqa: S603 - fixed program
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        return False, f"could not start the delete ({exc}); delete {path} yourself"
    return True, "removed a few seconds after this command ends"


def _do(item: Item, machine: Machine, target: Path) -> tuple[bool, str]:
    if item.admin and not machine.admin:
        return False, "needs an administrator terminal"
    if item.argv:
        try:
            done = machine.run(list(item.argv), capture_output=True, text=True, timeout=900,
                               stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"could not run: {exc}"
        said = (done.stdout or done.stderr or "").strip().splitlines()
        return done.returncode in item.ok_codes, (said[-1] if said else f"exit {done.returncode}")[:300]
    path = Path(item.path)
    if item.action == "database":
        return _database(machine)
    if item.action == "engine":
        from kazma_core import container_engine_setup

        return container_engine_setup.remove(machine.run)
    if item.action == "remove_later":
        return _remove_later(path, machine)
    if item.action in ("remove_tree", "delete_tree"):
        shutil.rmtree(path, ignore_errors=True)
        return not path.exists(), "deleted" if not path.exists() else f"{path} could not be fully deleted"
    if item.action == "move":
        target.mkdir(parents=True, exist_ok=True)
        dest = target / path.name
        try:
            shutil.move(str(path), str(dest))
        except OSError as exc:
            return False, f"could not move it ({exc})"
        return True, f"moved to {dest}"
    if item.action == "launchd_agent":
        machine.run(["launchctl", "unload", "-w", str(path)], capture_output=True, text=True, timeout=60)
        path.unlink(missing_ok=True)
        return not path.exists(), "removed"
    return False, f"nothing to do for {item.action!r}"


def _run_plan(plan: list[Item], machine: Machine, say: Callable[[str], None] = print) -> int:
    """Carry out every line that is not ``keep``; the exit code."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(machine.now()))
    target = machine.user_home / f"Kazma-uninstalled-{stamp}"
    failed = 0
    for item in plan:
        if item.how == "keep":
            continue
        ok, detail = _do(item, machine, target)
        say(f"  {'done' if ok else 'NOT DONE'}: {item.what}: {detail}")
        failed += 0 if ok else 1
    return EXIT_DONE if not failed else EXIT_NOT_DONE


def _show_plan(plan: list[Item], say: Callable[[str], None] = print) -> None:
    words = {"remove": "Removed", "move": "Moved aside", "delete": "DELETED FOR GOOD", "keep": "Kept"}
    for how in ("remove", "move", "delete", "keep"):
        lines = [i for i in plan if i.how == how]
        if not lines:
            continue
        say(f"{words[how]}:")
        for item in lines:
            say(f"  - {item.what}" + (f"  ({item.why})" if item.why else ""))


def main(argv: list[str] | None = None, *, machine: Machine | None = None,
         ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> int:
    parser = argparse.ArgumentParser(prog="kazma uninstall", description="Take Kazma off this computer.")
    parser.add_argument("--remove-data", action="store_true",
                        help="also move Kazma's data and home aside (to Kazma-uninstalled-<date> in your home)")
    parser.add_argument("--purge", action="store_true", help="with --remove-data: delete them for good instead")
    parser.add_argument("--dry-run", action="store_true", help="show the plan; change nothing")
    parser.add_argument("-y", "--yes", action="store_true", help="do not ask")
    args = parser.parse_args(argv)
    if args.purge and not args.remove_data:
        say("--purge deletes the data --remove-data moves aside: give both.")
        return EXIT_CANCELLED
    machine = machine or Machine.here()
    plan = build_plan(machine, remove_data=args.remove_data, purge=args.purge)
    say("Kazma uninstall: the plan")
    _show_plan(plan, say)
    if args.dry_run:
        return EXIT_DONE
    if not args.yes:
        word = "delete" if args.purge else "uninstall"
        try:
            answer = ask(f"Type '{word}' to go ahead (anything else stops): ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != word:
            say("Stopped: nothing was changed.")
            return EXIT_CANCELLED
    code = _run_plan(plan, machine, say)
    say("Kazma is uninstalled." if code == EXIT_DONE else
        "Some steps were not done (above): run kazma uninstall again from an administrator terminal, "
        "or do them by hand.")
    return code

