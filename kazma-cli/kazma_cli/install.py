"""``kazma setup install`` -- Kazma installed as Express, Full or Custom.

The owner's three installs (2026-11-07), from one table
(:mod:`kazma_core.install_components`):

* **Express** (the default): the least Kazma can lack, with no Docker.
* **Full**: every part, Docker and PostgreSQL among them.
* **Custom**: a checklist; each part says what Kazma lacks without it.

One run checks every part first and reports every problem it can see before
anything is done (disk space, the network, an administrator terminal,
whether Docker can run here at all), shows one plan, asks once, and installs
what is missing. A step that fails is tried again; a part that still fails
stops only what needs it, and the end names what was not done and what Kazma
lacks for it. What was chosen and how each part ended is recorded in
``<data dir>/setup_record.json`` (``install``), so a second run -- after a
restart, or to repair -- checks again and continues where the first stopped;
everything said and every program's output goes to ``<install>/.kazma/install.log``.

When the service is installed it waits for Kazma to answer and opens the
browser on it: the chat page asks for a provider key the first time.

The drill VM (2026-11-06/07) is where this came from: an install that needed
a dozen manual steps and met about ten fresh-install bugs, one at a time.
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import textwrap
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from kazma_cli.setup import (
    _SYSTEM_ATTEMPTS,
    _SYSTEM_RETRY_WAIT_S,
    _ask,
    _env_path,
    _extra_requirements,
    _run_step,
    _set_env_value,
    run_system_step,
)

#: The Windows task ``scripts/service/install_service.py`` registers.
SERVICE_TASK = "KazmaAgent"
#: The systemd user unit and the launchd label it writes.
SERVICE_UNIT = "kazma"
SERVICE_LABEL = "com.kazma.agent"
#: Where SearXNG answers on this machine (docker-compose.yml's search profile).
SEARXNG_URL = "http://127.0.0.1:8088"
#: Free space each mode asks for before it starts (PyTorch alone is ~2 GB;
#: Docker's disk and the document worker image take most of Full's).
DISK_NEEDED = {"express": 8 * 2**30, "full": 25 * 2**30, "custom": 8 * 2**30}
#: How long the end waits for a freshly started Kazma to answer.
ANSWER_WAIT_S = 240.0

_MODE_TEXT = {
    "express": "everything that needs no Docker",
    "full": "every part, Docker and PostgreSQL included (Windows may restart once)",
    "custom": "choose each part",
}

_OS_NAMES = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}

#: Kazma's colours, readable on a light terminal and a dark one (setup.sh has
#: the same 256-colour codes, setup.ps1 their 24-bit originals): the brand's
#: royal blue, then darker green, amber and red than a terminal's bright ones.
_TONES = {"brand": "1;38;5;33", "ok": "38;5;28", "warn": "38;5;172", "fail": "38;5;160"}
_COLOURS: list[bool] = []


def _colours_on() -> bool:
    """Colour only on a terminal that shows it, and never with NO_COLOR set.
    Windows' console shows escape codes once asked to (ENABLE_VIRTUAL_TERMINAL_PROCESSING)."""
    if _COLOURS:
        return _COLOURS[0]
    on = False
    try:
        on = not os.environ.get("NO_COLOR") and sys.stdout.isatty()
    except (AttributeError, ValueError):  # no stdout to ask
        on = False
    if on and os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            on = bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode))
                      and kernel32.SetConsoleMode(handle, mode.value | 0x0004))
        except (AttributeError, OSError):  # not a console Windows can switch: plain text
            on = False
    _COLOURS.append(on)
    return on


def _paint(text: str, tone: str = "") -> str:
    if not tone or not text or not _colours_on():
        return text
    return f"\033[{_TONES[tone]}m{text}\033[0m"


def add_install_parser(sub) -> None:
    install = sub.add_parser("install", help="install Kazma: Express (default), Full or Custom")
    install.add_argument("--mode", choices=("express", "full", "custom"),
                         help="express (default), full, or custom (a checklist)")
    install.add_argument("--with", dest="with_", action="append", default=[], metavar="PART",
                         help="add parts (comma-separated; see --list)")
    install.add_argument("--without", action="append", default=[], metavar="PART",
                         help="leave parts out (comma-separated; see --list)")
    install.add_argument("-y", "--yes", action="store_true", help="do not ask before acting")
    install.add_argument("--check", action="store_true", help="report each part; change nothing")
    install.add_argument("--dry-run", action="store_true", help="print the plan; run nothing")
    install.add_argument("--list", action="store_true", help="every part, what it gives, what Kazma lacks without it")
    install.add_argument("--packages-only", action="store_true",
                         help="install Kazma's Python packages for the mode only; show the rest as the plan")
    install.add_argument("--no-browser", action="store_true", help="do not open the browser at the end")


# -- the log ------------------------------------------------------------------------------------


class _InstallLog:
    """Every line said, and every program's output, in one file."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.path = None

    def write(self, text: str) -> None:
        if self.path is None:
            return
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self.path.open("a", encoding="utf-8") as out:
                for line in text.splitlines() or [""]:
                    out.write(f"{stamp}  {line}\n")
        except OSError:
            self.path = None


def _default_log_path() -> Path | None:
    try:
        from kazma_core.paths import user_home

        return user_home() / "install.log"
    except OSError:  # no home: no log, the install still runs
        return None


# -- the machine --------------------------------------------------------------------------------


def _free_bytes(path: Path) -> int | None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _reachable(host: str, port: int = 443, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:  # noqa: S310 -- this machine's own Kazma
            return 200 <= response.status < 300
    except (OSError, ValueError):
        return False


def _interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


@dataclass
class Machine:
    """What the install reads and runs; each piece replaced in the tests."""

    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    which: Callable[[str], str | None] = shutil.which
    ask: Callable[[str], str] = input
    sleep: Callable[[float], None] = time.sleep
    open_url: Callable[[str], object] = webbrowser.open
    answers: Callable[[str], bool] = _answers
    free_bytes: Callable[[Path], int | None] = _free_bytes
    reachable: Callable[[str], bool] = _reachable
    is_admin: Callable[[], bool] | None = None
    interactive: Callable[[], bool] = _interactive
    platform: object | None = None
    finders: dict = field(default_factory=dict)
    root: Path | None = None
    python: str = sys.executable
    log_path: Path | None = None
    record_path: Path | None = None
    env_path: Path | None = None


# -- what is there ------------------------------------------------------------------------------


def _extras_present() -> set[str]:
    import importlib

    from kazma_cli.update import _EXTRA_MARKERS, _module_available

    importlib.invalidate_caches()
    return {extra for extra, markers in _EXTRA_MARKERS.items() if any(_module_available(m) for m in markers)}


def _embedding_model_downloaded() -> bool:
    """The meaning-search model on disk, read from the local cache only."""
    try:
        from kazma_core.memory.embedder import local_model_downloaded

        return local_model_downloaded()
    except ImportError:  # the rag packages are not here: the part says so first
        return False


def _env_value(path: Path, key: str) -> str:
    from kazma_cli.setup import _env_file_value

    return (os.environ.get(key) or _env_file_value(path, key) or "").strip()


def _playwright_chromium() -> bool:
    configured = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    if configured and configured != "0":
        roots = [Path(configured)]
    elif os.name == "nt":
        roots = [Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"]
    elif sys.platform == "darwin":
        roots = [Path.home() / "Library" / "Caches" / "ms-playwright"]
    else:
        roots = [Path.home() / ".cache" / "ms-playwright"]
    return any(any(root.glob("chromium-*")) for root in roots if root.is_dir())


def _service_state(platform, run) -> tuple[bool | None, str]:
    os_name = platform.os

    def out(argv: list[str]) -> tuple[int, str]:
        try:
            done = run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, check=False,
                       encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            return 127, ""
        return done.returncode, (done.stdout or "") + (done.stderr or "")

    if os_name == "windows":
        code, _ = out(["schtasks", "/Query", "/TN", SERVICE_TASK])
        return code == 0, ("the KazmaAgent task is registered" if code == 0 else "no KazmaAgent task")
    if os_name == "macos":
        code, _ = out(["launchctl", "list", SERVICE_LABEL])
        return code == 0, (f"{SERVICE_LABEL} is loaded" if code == 0 else f"{SERVICE_LABEL} is not loaded")
    code, text = out(["systemctl", "--user", "is-enabled", SERVICE_UNIT])
    if code == 127:
        return None, "no systemd here: cannot tell"
    return code == 0, ("the kazma user unit is enabled" if code == 0 else "no kazma user unit")


@dataclass
class PartState:
    ok: bool | None
    detail: str


def _check_parts(parts, machine: Machine, platform) -> tuple[dict[str, PartState], list]:
    """Each part as found, and the system dependencies as ``kazma setup
    system`` sees them."""
    from kazma_core.system_deps import check_all

    extras = _extras_present()
    states = check_all(platform, run=machine.run, which=machine.which, **machine.finders)
    system = {s.name: s for s in states}
    root = machine.root
    env_path = machine.env_path or _env_path()
    found: dict[str, PartState] = {}
    for part in parts:
        missing_extras = [e for e in part.extras if e not in extras]
        missing_system = [d for d in part.system if d in system and system[d].ok is False]
        unsure_system = [d for d in part.system if d in system and system[d].ok is None]
        if missing_extras or missing_system:
            bits = ([f"packages {', '.join(missing_extras)} not installed"] if missing_extras else []) + [
                system[d].detail for d in missing_system]
            found[part.id] = PartState(False, "; ".join(bits))
            continue
        detail = "; ".join(system[d].detail for d in part.system if d in system) or "installed"
        ok: bool | None = None if unsure_system else True
        if part.id == "meaning_search":
            # Its packages alone are not the part: the model comes on first use,
            # and the server's first start waited for it (4.3 GB, 5.5 min).
            ok, detail = (True, "installed, model downloaded") if _embedding_model_downloaded() else (
                False, "the bge-m3 model is not downloaded")
        elif part.id == "neural_ocr":
            from kazma_core.documents.ocr.neural_models import verify_model_directory

            status = verify_model_directory()
            ok, detail = (True, f"models in {status.directory}") if status.ready else (
                False, status.reason or "the models are not installed")
        elif part.id == "browser":
            ok, detail = (True, "Chromium installed") if _playwright_chromium() else (False, "Chromium not installed")
        elif part.id == "postgres":
            ok, detail = _postgres_state(platform, machine)
        elif part.id == "document_container":
            ok, detail = _container_state(machine, root)
        elif part.id == "private_search":
            url = _env_value(env_path, "KAZMA_SEARXNG_URL")
            if not url:
                ok, detail = False, "KAZMA_SEARXNG_URL is not set"
            else:
                answered = machine.answers(url.rstrip("/") + "/healthz")
                ok, detail = (True, f"SearXNG answers at {url}") if answered else (
                    False, f"SearXNG does not answer at {url}")
        elif part.id == "service":
            ok, detail = _service_state(platform, machine.run)
        found[part.id] = PartState(ok, detail)
    return found, states


def _postgres_state(platform, machine: Machine) -> tuple[bool | None, str]:
    from kazma_core import postgres_service as pgs
    from kazma_core.db.postgres_pool import driver_missing

    rec = pgs.read_record(machine.record_path).get("postgres") or {}
    if not rec:
        return False, "not set up"
    state = pgs.service_state(platform.os, rec.get("service", ""), int(rec.get("port", pgs.DEFAULT_PORT)),
                              run=machine.run)
    if not state.get("running") or not state.get("answers"):
        return False, f"{rec.get('service')} is not running"
    if driver_missing():
        return False, "Kazma's Postgres driver is not installed"
    return True, f"PostgreSQL {rec.get('major')} as {rec.get('service')}"


def _container_state(machine: Machine, root: Path | None) -> tuple[bool | None, str]:
    from kazma_core.docker_cli import find_docker_cli
    from kazma_core.safety.deployment_policy import DOCUMENT_WORKER_ISOLATION_KEY

    if root is None or not (root / "deploy" / "document-worker").is_dir():
        return None, "the image is built from Kazma's checkout; none here"
    try:
        from kazma_core.config_store import get_config_store

        profile = str(get_config_store().get(DOCUMENT_WORKER_ISOLATION_KEY) or "")
    except (RuntimeError, OSError) as exc:  # the settings store may be away: cannot tell
        return None, f"the settings cannot be read ({type(exc).__name__})"
    docker = machine.finders.get("docker", find_docker_cli)()
    if not docker:
        return False, "Docker is not installed"
    from kazma_core.documents.worker_image import expected_tag

    tag = expected_tag(root)
    try:
        done = machine.run([docker, "image", "inspect", tag], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=60, check=False)
        image = done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        image = False
    if image and profile == "container":
        return True, f"the container profile, image {tag}"
    return False, ("the image is not built" if not image else "documents do not use the container profile")


# -- the plan -----------------------------------------------------------------------------------


@dataclass
class PlannedStep:
    part: str
    step: object
    attempts: int = 1
    cwd: str | None = None


def _extras_step(missing: list[str], machine: Machine):
    """One step that installs the *missing* extras into the environment Kazma
    runs in: ``uv sync --inexact`` in a checkout (the lock's versions; it
    removes nothing), else each extra's requirements with ``uv pip``."""
    from kazma_core.postgres_service import Step

    root = machine.root
    uv = machine.which("uv")
    in_checkout = (root is not None and (root / "uv.lock").is_file()
                   and Path(sys.prefix).resolve() == (root / ".venv").resolve())
    if uv and in_checkout:
        argv = [uv, "sync", "--project", str(root), "--inexact"]
        for extra in missing:
            argv += ["--extra", extra]
        return Step(f"install Kazma's packages: {', '.join(missing)}", tuple(argv))
    requirements: list[str] = []
    for extra in missing:
        requirements += [r for r in _extra_requirements(extra) if r not in requirements]
    argv = ([uv, "pip", "install", "--python", machine.python, *requirements] if uv
            else [machine.python, "-m", "pip", "install", *requirements])
    return Step(f"install Kazma's packages: {', '.join(missing)}", tuple(argv))


def build_plan(parts, found: dict[str, PartState], states, machine: Machine, platform,
               readiness=None) -> list[PlannedStep]:
    """The steps that put in what *found* says is missing, in table order."""
    from kazma_core.install_components import extras_for
    from kazma_core.postgres_service import Step
    from kazma_core.system_deps import install_plan, wsl_install_step

    todo = [p for p in parts if found[p.id].ok is False]
    todo_ids = {p.id for p in todo}
    present = _extras_present()
    plan: list[PlannedStep] = []
    missing_extras = [e for e in extras_for(todo) if e not in present]
    if missing_extras:
        owner = next(p.id for p in todo if any(e in missing_extras for e in p.extras))
        plan.append(PlannedStep(owner, _extras_step(missing_extras, machine), attempts=_SYSTEM_ATTEMPTS))
    wanted_deps = {d for p in todo for d in p.system}
    owner_of = {d: next(p.id for p in todo if d in p.system) for d in wanted_deps}
    chosen_states = [s for s in states if s.name in wanted_deps]
    if "docker" in wanted_deps and readiness is not None and readiness.wsl_missing:
        plan.append(PlannedStep("docker", wsl_install_step()))
    for step in install_plan(platform, chosen_states):
        part = owner_of.get(step.dep, "")  # the package lists' refresh is no part's
        plan.append(PlannedStep(part, step, attempts=_SYSTEM_ATTEMPTS))
    root = machine.root
    python = machine.python
    if "meaning_search" in todo_ids:
        plan.append(PlannedStep("meaning_search", Step(
            "download the meaning-search model (bge-m3, about 4 GB)",
            (python, "-c", "from kazma_core.memory.embedder import download_local_model; download_local_model()")),
            attempts=_SYSTEM_ATTEMPTS))
    if "neural_ocr" in todo_ids and root is not None and (root / "scripts" / "ocr_models.py").is_file():
        plan.append(PlannedStep("neural_ocr", Step("install the neural OCR models (downloaded and checked)",
                                                   (python, str(root / "scripts" / "ocr_models.py"), "install")),
                                attempts=_SYSTEM_ATTEMPTS))
    if "browser" in todo_ids:
        plan.append(PlannedStep("browser", Step("download Chromium for the browser tools",
                                                (python, "-m", "playwright", "install", "chromium")),
                                attempts=_SYSTEM_ATTEMPTS))
    if "postgres" in todo_ids:
        plan.append(PlannedStep("postgres", Step("set up PostgreSQL 16 as a service", action="postgres",
                                                 shown="kazma setup postgres -y", admin=True)))
    docker = "docker"
    if "document_container" in todo_ids and root is not None:
        plan.append(PlannedStep("document_container", Step(
            "build the document worker image", (python, str(root / "scripts" / "document_worker_image.py"), "build")),
            attempts=2, cwd=str(root)))
        plan.append(PlannedStep("document_container", Step(
            "documents use the container profile", action="container_profile",
            shown="setting security.execution.document_worker_isolation = container")))
    if "private_search" in todo_ids and root is not None:
        plan.append(PlannedStep("private_search", Step(
            "start SearXNG (docker compose, search profile)",
            (docker, "compose", "-f", str(root / "docker-compose.yml"), "--profile", "search", "up", "-d", "searxng")),
            attempts=_SYSTEM_ATTEMPTS, cwd=str(root)))
        plan.append(PlannedStep("private_search", Step(f"point Kazma's web search at {SEARXNG_URL}",
                                                       action="searxng_env",
                                                       shown=f"KAZMA_SEARXNG_URL={SEARXNG_URL} in .env")))
    if "service" in todo_ids and root is not None:
        plan.append(PlannedStep("service", Step(
            "install Kazma as a service (its guard starts at boot)",
            (python, str(root / "scripts" / "service" / "install_service.py"), "--install"),
            admin=platform.os == "windows")))
        if platform.os == "windows":
            plan.append(PlannedStep("service", Step("start it now", ("schtasks", "/Run", "/TN", SERVICE_TASK),
                                                    admin=True)))
    return plan


# -- choosing -----------------------------------------------------------------------------------


def _split(values: list[str]) -> list[str]:
    return [v.strip() for item in values for v in item.split(",") if v.strip()]


def _choose_mode(options, recorded: dict, machine: Machine, say) -> str:
    if options.mode:
        return options.mode
    if recorded.get("mode") in ("express", "full", "custom"):
        when = time.strftime("%Y-%m-%d", time.localtime(float(recorded.get("at") or 0)))
        say(f"Installed as {recorded['mode'].capitalize()} on {when}: checking it, and finishing what is not done.")
        return recorded["mode"]
    if options.yes or not machine.interactive():
        return "express"  # -y asks nothing, the mode included
    say("How should Kazma be installed?", "brand")
    for number, mode in enumerate(("express", "full", "custom"), 1):
        say(f"  {number}. {mode.capitalize()}{' (recommended)' if mode == 'express' else ''}: {_MODE_TEXT[mode]}")
    while True:
        answer = _ask(machine.ask, "Choose 1, 2 or 3 [1]: ").strip().lower()
        if answer in ("", "1", "e", "express"):
            return "express"
        if answer in ("2", "f", "full"):
            return "full"
        if answer in ("3", "c", "custom"):
            return "custom"
        say("Type 1, 2 or 3.")


def _choose_custom(start: set[str], machine: Machine, say) -> set[str]:
    """The Custom checklist: numbers switch a part, Enter accepts."""
    from kazma_core.install_components import COMPONENTS, toggle

    chosen = set(start)
    while True:
        say("Choose what to install. Type numbers to switch parts on or off (for example 3 7), then Enter.")
        for number, comp in enumerate(COMPONENTS, 1):
            mark = "x" if comp.id in chosen else " "
            size = f" ({comp.size})" if comp.size else ""
            say(f"  {number:2}. [{mark}] {comp.title}{size}")
            say(f"          without it: {comp.lacks}")
        answer = _ask(machine.ask, "Switch (Enter when done): ").strip()
        if not answer:
            return chosen
        for token in answer.replace(",", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(COMPONENTS):
                chosen = toggle(chosen, COMPONENTS[int(token) - 1].id)
            else:
                say(f"  {token!r} is not a number from the list")


# -- running ------------------------------------------------------------------------------------


def _container_profile() -> tuple[bool, str]:
    from kazma_core.config_store import get_config_store
    from kazma_core.safety.deployment_policy import DOCUMENT_WORKER_ISOLATION_KEY
    from kazma_core.settings_validation import SettingRejected, validate_setting

    try:
        value, category = validate_setting(DOCUMENT_WORKER_ISOLATION_KEY, "container")
        get_config_store().set(DOCUMENT_WORKER_ISOLATION_KEY, value, category=category or "security")
    except SettingRejected as exc:
        return False, str(exc)
    except (RuntimeError, OSError) as exc:  # the settings store may be away
        return False, f"the setting could not be saved: {type(exc).__name__}"
    return True, ""


def _postgres(log) -> tuple[bool, str]:
    from kazma_cli.setup import _setup_postgres

    code = _setup_postgres(argparse.Namespace(yes=True, check=False, dry_run=False, port=5432,
                                              move_from_container=None, no_env=False))
    log(f"kazma setup postgres ended with {code}")
    return code == 0, "" if code == 0 else f"kazma setup postgres ended with {code} (see above)"


def _run_planned(item: PlannedStep, machine: Machine, env_path: Path, log) -> tuple[bool, str]:
    step = item.step
    if step.action == "postgres":
        return _postgres(log)
    if step.action == "container_profile":
        return _container_profile()
    if step.action == "searxng_env":
        try:
            _set_env_value(env_path, "KAZMA_SEARXNG_URL", SEARXNG_URL)
        except OSError as exc:
            return False, str(exc)
        os.environ["KAZMA_SEARXNG_URL"] = SEARXNG_URL
        return True, ""
    if step.argv and step.argv[0] == "docker":
        from kazma_core.docker_cli import find_docker_cli

        docker = machine.finders.get("docker", find_docker_cli)()
        if not docker:
            return False, "Docker is not installed"
        step = type(step)(step.title, (docker, *step.argv[1:]), admin=step.admin)
    if step.action or step.dep:
        return run_system_step(step, which=machine.which, run=machine.run, log=log)
    return _run_step(step, [], run=machine.run, cwd=item.cwd, log=log)


def _record(mode: str, chosen: list[str], found: dict[str, PartState], path: Path | None) -> None:
    import json

    from kazma_core.atomic_files import atomic_write_text
    from kazma_core.postgres_service import _record_path, read_record

    target = path or _record_path()
    data = read_record(target)
    now = time.time()
    data.update({"version": 1, "install": {
        "mode": mode, "components": chosen, "at": now,
        "results": {cid: {"ok": state.ok, "detail": state.detail, "at": now} for cid, state in found.items()},
    }})
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, json.dumps(data, indent=2))
    except OSError:
        pass


def _kazma_url() -> str:
    port = (os.environ.get("KAZMA_PORT") or "").strip()
    if not port:
        from kazma_cli.setup import _env_file_value

        port = (_env_file_value(_env_path(), "KAZMA_PORT") or "").strip()
    return f"http://127.0.0.1:{port if port.isdigit() else '9090'}"


def _project_root() -> Path | None:
    try:
        from kazma_core.paths import get_project_root

        root = get_project_root()
    except OSError:  # a wheel install has no checkout
        return None
    return root if (root / "pyproject.toml").is_file() else None


def _list_parts(say) -> int:
    from kazma_core.install_components import COMPONENTS

    for comp in COMPONENTS:
        where = "Express and Full" if comp.express else "Full"
        say(f"{comp.id:20} {comp.title} [{where}]")
        say(f"{'':20}   gives: {comp.gives}")
        say(f"{'':20}   without it: {comp.lacks}")
    return 0


def _select_parts(options, recorded: dict, machine: Machine, say) -> tuple[str, list]:
    """The mode and its parts; a recorded Custom choice is kept on a re-run.
    An unknown part raises ``KeyError``, an unknown mode ``ValueError``."""
    from kazma_core.install_components import COMPONENTS, components_for, express_ids

    mode = _choose_mode(options, recorded, machine, say)
    with_, without = _split(options.with_), _split(options.without)
    if mode == "custom" and not with_ and not without and recorded.get("mode") == "custom" and not options.mode:
        chosen = set(recorded.get("components") or [])
        return mode, [c for c in COMPONENTS if c.id in chosen]
    parts = components_for(mode, with_=with_, without=without)
    if mode == "custom" and machine.interactive() and not options.yes and not (with_ or without):
        start = set(recorded.get("components") or []) if recorded.get("mode") == "custom" else set()
        picked = _choose_custom(start or {p.id for p in parts} or set(express_ids()), machine, say)
        parts = [c for c in COMPONENTS if c.id in picked]
    return mode, parts


def _show_parts(parts, found: dict[str, PartState], say) -> None:
    for part in parts:
        state = found[part.id]
        mark = "ok" if state.ok else ("??" if state.ok is None else "NO")
        say(f"  {mark:4} {part.title}: {state.detail}")


def _short_title(title: str) -> str:
    """A part's name without the brackets: "Meaning search (bge-m3)" -> "Meaning search"."""
    return title.split(" (", 1)[0]


def _say_group(label: str, parts, say) -> None:
    """One line of the summary, wrapped to the terminal: the label, how many, the names."""
    if not parts:
        return
    width = max(60, min(100, shutil.get_terminal_size((100, 24)).columns - 1))
    text = f"{label} ({len(parts)}): " + ", ".join(_short_title(p.title) for p in parts)
    say(textwrap.fill(text, width=width, initial_indent="  ", subsequent_indent="      "))


def _say_summary(mode: str, parts, found: dict[str, PartState], pre: _Preflight, left_out, say) -> None:
    """What the install will do, short: the details are in the log.

    The first run on a clean Windows printed every part's state, every part
    left out with what Kazma lacks without it, and every step's command line:
    some 60 lines before one question (the owner, 2026-11-07). The screen
    says which parts, by name; the log, --dry-run and --check say the rest.
    """
    from kazma_core.english_count import count_noun
    from kazma_core.install_components import full_ids

    say(f"{mode.capitalize()}: {len(parts)} of {count_noun(len(full_ids()), 'part')}", "brand")
    _say_group("To install", [p for p in parts if found[p.id].ok is False and p.id not in pre.blocked], say)
    _say_group("Already here", [p for p in parts if found[p.id].ok], say)
    _say_group("Not possible here", [p for p in parts if p.id in pre.blocked], say)
    _say_group("Could not tell", [p for p in parts if found[p.id].ok is None], say)
    _say_group("Left out", left_out, say)
    if left_out:
        say("  What each part gives, and what Kazma lacks without it: kazma setup install --list")


@dataclass
class _Preflight:
    problems: list[str] = field(default_factory=list)
    blocked: dict[str, str] = field(default_factory=dict)
    readiness: object | None = None


def _preflight(parts, found: dict[str, PartState], machine: Machine, platform, mode: str, options, say) -> _Preflight:
    """Everything that can stop the install, found before anything is done."""
    from kazma_core.system_deps import docker_readiness

    pre = _Preflight()
    todo = [p for p in parts if found[p.id].ok is False]
    if any("docker" in p.system for p in todo):
        pre.readiness = docker_readiness(platform, run=machine.run)
        if pre.readiness.ok is False:
            pre.blocked["docker"] = pre.readiness.problem
            pre.problems.append(f"Docker cannot run here: {pre.readiness.problem}")
        elif pre.readiness.note:
            say(f"Note: {pre.readiness.note}")
    for part in todo:
        blocked_need = next((need for need in part.needs if need in pre.blocked), None)
        if blocked_need and part.id not in pre.blocked:
            pre.blocked[part.id] = f"it needs {blocked_need}"
    if not todo or options.check:
        return pre
    free = machine.free_bytes(machine.root or Path.cwd())
    needed = DISK_NEEDED.get(mode, DISK_NEEDED["express"])
    if free is not None and free < needed:
        pre.problems.append(f"{free / 2**30:.1f} GB free on the install's drive; this install asks for about "
                            f"{needed / 2**30:.0f} GB")
    if not (machine.reachable("pypi.org") or machine.reachable("github.com")):
        pre.problems.append("neither pypi.org nor github.com answers: the install downloads from them")
    from kazma_core.system_deps import needed_here

    by_hand = [p for p in todo if any(needed_here(d, platform) for d in p.system)]
    if platform.manager is None and by_hand:
        pre.problems.append("no package manager Kazma knows here (winget, Homebrew, apt, dnf): install "
                            + ", ".join(p.title for p in by_hand) + " by hand")
    return pre


def _go_ahead(options, machine: Machine, *, admin_short: bool, say) -> int | None:
    """None to go on; else the exit code of an install that does nothing."""
    if options.packages_only:
        return None
    if admin_short:
        say("Nothing was done. Open an administrator terminal and run setup again: it continues from here.")
        return 2
    if options.yes:
        return None
    if not machine.interactive():
        say("Nothing was done: no terminal to ask. Run it with -y to install without the question.")
        return 1
    if _ask(machine.ask, "Install now? [Y/n] ").strip().lower() not in ("", "y", "yes"):
        say("Nothing was done.")
        return 1
    return None


def _execute(plan: list[PlannedStep], machine: Machine, platform, say, log) -> tuple[list[str], bool]:
    """Run the plan: retries, a failed part stops what needs it. Returns the
    steps not done and whether Windows must restart (WSL)."""
    from kazma_core.install_components import COMPONENTS

    env_path = machine.env_path or _env_path()
    failed: set[str] = set()
    not_done: list[str] = []
    restart_needed = False
    needs_of = {c.id: set(c.needs) for c in COMPONENTS}
    for item in plan:
        if item.part in failed or needs_of.get(item.part, set()) & failed:
            say(f"-> {item.step.title}: not done (an earlier step it needs failed)")
            not_done.append(item.step.title)
            failed.add(item.part)
            continue
        say(f"-> {item.step.title}", "brand")
        ok, detail = _attempt(item, machine, env_path, say, log)
        if not ok and item.step.dep:
            ok = _installed_anyway(item.step, platform, machine, say)
        if not ok:
            say(f"   FAILED: {detail}", "fail")
            not_done.append(item.step.title)
            failed.add(item.part)
            continue
        restart_needed = restart_needed or item.step.argv[:2] == ("wsl", "--install")
        if platform.os == "windows":
            from kazma_core.path_refresh import refresh_path_from_os

            refresh_path_from_os()  # a program installed now is on the PATH of new processes only
    return not_done, restart_needed


def _attempt(item: PlannedStep, machine: Machine, env_path: Path, say, log) -> tuple[bool, str]:
    ok, detail = False, ""
    for attempt in range(1, item.attempts + 1):
        ok, detail = _run_planned(item, machine, env_path, log)
        if ok or attempt == item.attempts:
            break
        say(f"   failed ({detail.split(':', 1)[0]}); trying again in {_SYSTEM_RETRY_WAIT_S:.0f} s"
            f" (attempt {attempt + 1} of {item.attempts})")
        machine.sleep(_SYSTEM_RETRY_WAIT_S)
    return ok, detail


def install(options, machine: Machine | None = None) -> int:
    from kazma_core import postgres_service as pgs
    from kazma_core.english_count import count_noun
    from kazma_core.install_components import COMPONENTS, full_ids
    from kazma_core.system_deps import record_system

    machine = machine or Machine()
    if machine.root is None:
        machine.root = _project_root()
    log = _InstallLog(machine.log_path or _default_log_path())

    def say(text: str = "", tone: str = "") -> None:
        print(_paint(text, tone), flush=True)
        log.write(text)

    if options.list:
        return _list_parts(say)
    platform = machine.platform or pgs.detect_platform()
    recorded = pgs.read_record(machine.record_path).get("install") or {}
    log.write(f"--- kazma setup install ({' '.join(sys.argv[1:]) or 'no arguments'})")
    say(_header(platform), "brand")
    try:
        mode, parts = _select_parts(options, recorded, machine, say)
    except (KeyError, ValueError) as exc:
        say(f"Not a part of Kazma: {exc}. See: kazma setup install --list")
        return 2
    # --check and --dry-run are for reading: every part's state, what each
    # part left out lacks, every step's command. An install shows a summary
    # and keeps all of that in its log.
    detailed = options.check or options.dry_run
    show = say if detailed else log.write
    say(f"Checking {count_noun(len(parts), 'part')} of {mode.capitalize()}...")
    found, states = _check_parts(parts, machine, platform)
    show(f"Install: {mode.capitalize()} ({len(parts)} of {count_noun(len(full_ids()), 'part')})")
    _show_parts(parts, found, show)
    left_out = [c for c in COMPONENTS if c.id not in {p.id for p in parts}]
    if left_out:
        show("Not chosen (what Kazma lacks without each):")
        for comp in left_out:
            show(f"  --   {comp.title}: {comp.lacks}")
    pre = _preflight(parts, found, machine, platform, mode, options, say)
    todo = [p for p in parts if found[p.id].ok is False and p.id not in pre.blocked]
    plan = build_plan(todo, found, states, machine, platform, pre.readiness) if todo else []
    admin_short = (platform.os == "windows" and any(item.step.admin for item in plan)
                   and not (machine.is_admin or pgs.is_admin)())
    if admin_short and not options.packages_only:
        pre.problems.append("some steps need an administrator terminal: open PowerShell with "
                            "'Run as administrator' and run setup again")
    if not detailed:
        _say_summary(mode, parts, found, pre, left_out, say)
    if pre.problems:
        say("Problems found before anything was done:", "warn")
        for problem in pre.problems:
            say(f"  !! {problem}", "warn")
    if options.check:
        record_system(states, machine.record_path)
        if any(found[p.id].ok is False for p in parts):
            say("Install what is missing with: kazma setup install")
            return 1
        return 1 if pre.problems else 0
    if not plan:
        return _nothing_to_do(mode, parts, found, pre, machine, options, say)
    show("The plan:")
    for number, item in enumerate(plan, 1):
        show(f"  {number:2}. {item.step.title}\n      {item.step.printable()}")
    if options.dry_run:
        return 0
    where = f"; every step and its command are in {log.path}" if log.path else ""
    say(f"{count_noun(len(plan), 'step')} to run, needing about "
        f"{DISK_NEEDED.get(mode, DISK_NEEDED['express']) / 2**30:.0f} GB of disk{where}.")
    stop = _go_ahead(options, machine, admin_short=admin_short, say=say)
    if stop is not None:
        if stop == 2:
            _record(mode, [p.id for p in parts], found, machine.record_path)
        return stop
    if options.packages_only:
        plan = [item for item in plan if item.step.title.startswith("install Kazma's packages")]
    not_done, restart_needed = _execute(plan, machine, platform, say, log.write)
    return _conclude(mode, parts, pre, machine, platform, options, say, not_done=not_done,
                     restart_needed=restart_needed, log_only=log.write)


def _header(platform) -> str:
    """The install's first line: which Kazma it installs, and where.

    It used to start "platform: windows, package manager: winget"; the owner
    asked for the version first (2026-11-07). The version is the checkout's
    own (``0.21.0+g3bf44c5``: the release and its commit).
    """
    from kazma_core.version import get_version

    where = _OS_NAMES.get(platform.os, platform.os) + (" (WSL)" if platform.wsl else "")
    manager = platform.manager or "no package manager Kazma knows"
    return f"Kazma {get_version()}: setup on {where}, with {manager}"


def _nothing_to_do(mode: str, parts, found: dict[str, PartState], pre: _Preflight, machine: Machine, options,
                   say) -> int:
    if options.dry_run:
        say("Nothing to install.")
        return 0
    _record(mode, [p.id for p in parts], found, machine.record_path)
    if pre.blocked:
        say("Not installed: " + "; ".join(f"{k} ({v})" for k, v in pre.blocked.items()))
    say("Nothing to install." if not pre.blocked else "Nothing else to install.")
    _finish(parts, found, machine, options, say)
    return 0 if not pre.blocked else 1


def _conclude(mode: str, parts, pre: _Preflight, machine: Machine, platform, options, say, *,
              not_done: list[str], restart_needed: bool, log_only=None) -> int:
    """Check every part again, record it, and say what Kazma lacks for now."""
    from kazma_core.system_deps import record_system

    after, after_states = _check_parts(parts, machine, platform)
    for cid, reason in pre.blocked.items():
        after[cid] = PartState(False, reason)
    record_system(after_states, machine.record_path)
    _record(mode, [p.id for p in parts], after, machine.record_path)
    # Every part's state is in the log; the screen names what is in place and,
    # for each part that is not, what Kazma lacks for now.
    from kazma_core.english_count import count_noun

    log_only = log_only or say
    log_only("After the install:")
    _show_parts(parts, after, log_only)
    missing = [p for p in parts if after[p.id].ok is False]
    in_place = [p for p in parts if after[p.id].ok]
    if not missing:
        say(f"Done: {count_noun(len(in_place), 'part')} of {mode.capitalize()} in place.", "ok")
    else:
        _say_group("In place", in_place, say)
        say("Not installed, and what Kazma lacks for now:", "warn")
        for part in missing:
            say(f"  -- {part.title}: {part.lacks}", "warn")
    if restart_needed:
        say("Restart Windows to finish WSL, then run setup again: it continues where it stopped.")
    elif missing or not_done:
        say("Run setup again to finish: it checks everything and continues where it stopped.")
    _finish(parts, after, machine, options, say)
    return 0 if not missing and not not_done else 1


def _installed_anyway(step, platform, machine: Machine, say) -> bool:
    from kazma_core.system_deps import check_all, installs_package, package_is_in

    if not installs_package(step):
        return False
    found = {s.name: s for s in check_all(platform, run=machine.run, which=machine.which, **machine.finders)}
    if step.dep in found and package_is_in(found[step.dep]):
        say("   installed, though the installer answered with an error")
        return True
    return False


def _finish(parts, found: dict[str, PartState], machine: Machine, options, say) -> None:
    """Point the owner at Kazma: wait for it and open the browser when the
    service runs it, else say how to start it."""
    url = _kazma_url()
    service = next((p for p in parts if p.id == "service"), None)
    if service is None or not found.get("service", PartState(False, "")).ok:
        say(f"Start Kazma with: {machine.python} -m kazma_cli serve -- then open {url}")
        return
    if not machine.answers(url + "/health/live"):
        say(f"Waiting for Kazma to answer at {url} (its first start loads its models) ...")
        # The time each try takes counts too: on Windows a refused connection
        # to this machine takes about 2 s, and counting the sleeps alone made
        # a 240 s wait last 325 s (the clean VM, 2026-11-07).
        spent = 0.0
        while spent < ANSWER_WAIT_S:
            started = time.monotonic()
            answered = machine.answers(url + "/health/live")
            spent += time.monotonic() - started
            if answered:
                break
            machine.sleep(5.0)
            spent += 5.0
    if not machine.answers(url + "/health/live"):
        say(f"Kazma has not answered yet. Open {url} in a minute; "
            "scripts/service/kazma_guard.py --status says what it is doing.")
        return
    say(f"Kazma is running: {url} (its setup guide connects the AI model)", "ok")
    if not options.no_browser and machine.interactive():
        try:
            machine.open_url(url + "/setup")
        except (webbrowser.Error, OSError) as exc:  # no browser here: the address is said above
            log_text = f"could not open a browser: {type(exc).__name__}"
            say(log_text)
