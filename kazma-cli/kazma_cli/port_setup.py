"""Choosing Kazma's port: at install, and afterwards with ``kazma setup port``.

The owner's request (2026-11-13): Kazma's page stays on 9090 by default, and
whoever installs it may choose another port. One rule decides a port
(``kazma_core.server_port.port_problem``), and both paths keep the choice in
the install's ``.env`` as ``KAZMA_PORT``, which the server, the guard and
every ``kazma`` command read (``server_port.server_port``).

* The install (:func:`choose_for_install`): ``--port N`` is checked and kept;
  a port already set for this install is kept and never asked about again; a
  first install asks once, after the mode (Enter keeps 9090), and with ``-y``
  takes 9090, or the first usable port after it when 9090 cannot be Kazma's
  here, said loudly.
* ``kazma setup port [N]`` (:func:`run`): with no port, which one Kazma uses
  and from where; with ``--check``, whether N can be used; with N, keeps it
  and asks the guard to reload Kazma once no chat is running, then names what
  else still points at the old address (``KAZMA_PUBLIC_URL``, a Cloudflare
  tunnel's configuration, sign-in redirect addresses).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kazma_core.server_port import DEFAULT_PORT, configured_port, first_free_port, port_problem

_KEY = "KAZMA_PORT"


class PortRefused(ValueError):
    """The port asked for cannot be Kazma's; the message says why."""


@dataclass(frozen=True)
class PortChoice:
    """The port this install will use, and the one it used before (None on a
    first install)."""

    port: int
    previous: int | None

    @property
    def needs_writing(self) -> bool:
        return self.previous is None or self.port != self.previous

    @property
    def moves(self) -> bool:
        """An install that already listened on another port moves."""
        return self.previous is not None and self.port != self.previous


def _shell_port() -> int | None:
    raw = (os.environ.get(_KEY) or "").strip()
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= 65535 else None


def _current_port(env_path: Path, *, installed_before: bool) -> int | None:
    """The port this install uses now: ``KAZMA_PORT`` (shell, else *env_path*),
    else 9090 for an install made before (the server's default), else None."""
    port = configured_port([env_path])
    if port is None and installed_before:
        return DEFAULT_PORT
    return port


def choose_for_install(requested: int | None, env_path: Path, *, installed_before: bool, interactive: bool,
                       ask: Callable[[str], str], say: Callable[..., None],
                       problem: Callable[..., str | None] = port_problem) -> PortChoice:
    """The port for this install run; raises :class:`PortRefused` for a
    ``--port`` that cannot be Kazma's."""
    previous = _current_port(env_path, installed_before=installed_before)
    if requested is not None:
        why = problem(requested, current=previous)
        if why:
            raise PortRefused(why)
        shell = _shell_port()
        if shell is not None and shell != requested:
            raise PortRefused(f"KAZMA_PORT={shell} is set in this shell's environment, and the server would take "
                              f"it over the .env: remove it there first (or choose {shell})")
        return PortChoice(int(requested), previous)
    if previous is not None:
        return PortChoice(previous, previous)  # chosen before: never asked again
    default = DEFAULT_PORT
    why_not = problem(DEFAULT_PORT)
    if why_not:
        default = first_free_port(DEFAULT_PORT + 1, problem=problem)
        say(f"Port {DEFAULT_PORT} cannot be Kazma's on this computer ({why_not}): using {default}.", "warn")
    if not interactive:
        return PortChoice(default, None)
    say(f"Kazma's page will be at http://127.0.0.1:{default}. Press Enter to keep that port, "
        "or type another (1024-65535).", "brand")
    while True:
        answer = ask(f"Port for Kazma's page [{default}]: ").strip()
        if not answer:
            return PortChoice(default, None)
        why = problem(answer)
        if why is None:
            return PortChoice(int(answer), None)
        say(f"  {why}. Type another port, or press Enter for {default}.", "warn")


def keep(port: int, env_path: Path) -> None:
    """``KAZMA_PORT=port`` in *env_path* (the file restricted to this account)."""
    from kazma_cli.setup import _set_env_value

    env_path.parent.mkdir(parents=True, exist_ok=True)
    _set_env_value(env_path, _KEY, str(port))


# -- what else names the old address ----------------------------------------------------------


def _cloudflared_configs() -> list[Path]:
    folders = [Path.home() / ".cloudflared"]
    if os.name == "nt":
        folders.append(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "cloudflared")
    else:
        folders.append(Path("/etc/cloudflared"))
    found = []
    for folder in folders:
        try:
            found += [p for p in folder.iterdir() if p.suffix.lower() in (".yml", ".yaml") and p.is_file()]
        except OSError:
            continue
    return found


def still_pointing_at(old: int, new: int, *, configs: list[Path] | None = None) -> list[str]:
    """What still names port *old*, each with what to change."""
    notes = []
    public = (os.environ.get("KAZMA_PUBLIC_URL") or "").strip()
    if public and f":{old}" in public:
        notes.append(f"KAZMA_PUBLIC_URL ({public}) names port {old}: change it to {new}.")
    for path in configs if configs is not None else _cloudflared_configs():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f"localhost:{old}" in text or f"127.0.0.1:{old}" in text:
            notes.append(f"Visitors reach port {old} through the Cloudflare tunnel's configuration ({path}): "
                         f"change it to {new} and restart cloudflared.")
    if not public:
        notes.append(f"A sign-in made through http://127.0.0.1:{old} (Gmail, Outlook, GitHub) used a redirect "
                     f"address on that port: add http://127.0.0.1:{new} to that app's redirect addresses "
                     "before signing in again.")
    notes.append(f"Bookmarks of http://127.0.0.1:{old} now need port {new}.")
    return notes


# -- kazma setup port ----------------------------------------------------------------------------


def add_port_parser(sub) -> None:
    port = sub.add_parser("port", help=f"show or change the port Kazma's page listens on (default {DEFAULT_PORT})")
    port.add_argument("port", nargs="?", type=int, help="the new port (1024-65535)")
    port.add_argument("--check", action="store_true", help="say whether the port can be Kazma's; change nothing")
    port.add_argument("--no-reload", action="store_true", help="keep the port; reload Kazma yourself later")


def _env_path() -> Path:
    from kazma_cli.setup import _env_path as install_env

    return install_env()


def _guard_runs(root: Path) -> bool:
    """A guard supervises this install now (its heartbeat, read by the
    guard's own ``_guard_alive``: reads one file, never raises)."""
    from kazma_cli.update import _load_guard

    guard = _load_guard(root)
    return bool(guard is not None and guard._guard_alive())


def run(options: argparse.Namespace, *, env_path: Path | None = None, root: Path | None = None,
        reload: Callable[[list[str]], int] | None = None, say: Callable[[str], None] = print,
        problem: Callable[..., str | None] = port_problem, configs: list[Path] | None = None,
        guard_runs: Callable[[Path], bool] | None = None) -> int:
    """``kazma setup port``; the keyword arguments are for tests."""
    from kazma_core.paths import get_project_root

    env_path = env_path or _env_path()
    root = root or get_project_root()
    now = _current_port(env_path, installed_before=True)
    if options.port is None:
        where = "this shell's environment" if _shell_port() is not None else (
            str(env_path) if configured_port([env_path]) is not None else "the default")
        say(f"Kazma's page: http://127.0.0.1:{now} (port {now}, from {where})")
        return 0
    why = problem(options.port, current=now)
    if why:
        say(f"Port {options.port} cannot be Kazma's: {why}.")
        return 2
    if options.check:
        say(f"Port {options.port} can be Kazma's.")
        return 0
    shell = _shell_port()
    if shell is not None and shell != options.port:
        say(f"KAZMA_PORT={shell} is set in this shell's environment and the server takes it over the .env. "
            "Remove it there first, then run this again.")
        return 2
    if options.port == now:
        if configured_port([env_path]) is None:
            keep(options.port, env_path)  # the default, now written down
        say(f"Kazma already uses port {now}.")
        return 0
    keep(options.port, env_path)
    say(f"Kazma's port is now {options.port} (KAZMA_PORT in {env_path}).")
    for note in still_pointing_at(now, options.port, configs=configs):
        say(f"  - {note}")
    guard = root / "scripts" / "service" / "kazma_guard.py"
    if options.no_reload or not guard.is_file() or not (guard_runs or _guard_runs)(root):
        say(f"Kazma listens on it from its next start: http://127.0.0.1:{options.port}")
        return 0
    say("Reloading Kazma once no chat is running (its guard follows the new port) ...")
    command = [sys.executable, str(guard), "--reload", "--when-idle"]
    code = reload(command) if reload is not None else subprocess.call(command, stdin=subprocess.DEVNULL)
    if code == 0:
        say(f"Kazma is on http://127.0.0.1:{options.port}")
    else:
        say(f"The reload did not finish (exit {code}); Kazma moves to port {options.port} at its next start.")
    return 0 if code == 0 else 1
