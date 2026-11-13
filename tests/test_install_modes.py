"""Kazma installs as Express, Full or Custom (the owner's spec, 2026-11-07).

The drill VM needed a dozen manual steps and met about ten fresh-install bugs
one at a time. ``kazma setup install`` takes one table of parts
(:mod:`kazma_core.install_components`): Express (the default, no Docker),
Full (every part) or Custom (a checklist). It checks everything first, shows
one plan, asks once, installs, records, and a second run continues where the
first stopped. Every package manager and program is faked here; nothing is
installed.

Gates: every extra in pyproject.toml belongs to a part or is declared (a
planted extra is caught); Full is the whole table; Express needs no Docker
and holds the test suite; the extras step never installs Kazma by name.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from kazma_core import install_components as ic
from kazma_core import system_deps as sd
from kazma_core.postgres_service import Platform

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = Platform("windows", "winget")


# ── the table ─────────────────────────────────────────────────────────


def _pyproject_extras() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(data["project"]["optional-dependencies"])


def uncovered_extras(extras: set[str], components=ic.COMPONENTS, aliases=ic.EXTRA_ALIASES) -> set[str]:
    """Extras no part installs and no alias explains."""
    covered = {e for c in components for e in c.extras} | set(aliases)
    return extras - covered


def test_every_extra_belongs_to_a_part_or_is_declared():
    assert uncovered_extras(_pyproject_extras()) == set()
    # Negative control: an extra added to pyproject.toml and nowhere else.
    assert uncovered_extras(_pyproject_extras() | {"newthing"}) == {"newthing"}
    # And nothing in the table names an extra pyproject.toml does not have.
    named = {e for c in ic.COMPONENTS for e in c.extras} | set(ic.EXTRA_ALIASES)
    assert named <= _pyproject_extras(), named - _pyproject_extras()


def test_full_is_the_whole_table_and_express_is_inside_it():
    assert [c.id for c in ic.components_for("full")] == ic.full_ids() == [c.id for c in ic.COMPONENTS]
    assert set(ic.express_ids()) < set(ic.full_ids())
    assert len({c.id for c in ic.COMPONENTS}) == len(ic.COMPONENTS)


def _needs_closure(cid: str) -> set[str]:
    seen, todo = set(), [cid]
    while todo:
        for need in ic.by_id(todo.pop()).needs:
            if need not in seen:
                seen.add(need)
                todo.append(need)
    return seen


def _needs_containers(comp) -> bool:
    return "docker" in comp.system or "docker" in _needs_closure(comp.id)


def express_failing_for_want_of_docker(components=ic.COMPONENTS) -> list[tuple[str, str]]:
    """(system, part) for every Express part that needs containers and is not
    best effort: on a computer that cannot run them, it would fail Express."""
    out = []
    for os_name in ic.OS_NAMES:
        for comp in components:
            if ic._in_express(comp, os_name) and _needs_containers(comp) and not comp.best_effort:
                out.append((os_name, comp.id))
    return out


def test_express_never_fails_for_want_of_docker():
    """The owner (2026-11-13): SearXNG and Neo4j install by default, on
    Kazma's container engine. Express on a computer that cannot run
    containers must still end as done, so every Express part that needs them
    is best effort; macOS Express stays container-free (Docker Desktop waits
    for a sign-in there)."""
    assert express_failing_for_want_of_docker() == []
    for os_name in ("windows", "linux"):
        assert {"docker", "private_search", "graph_neo4j"} <= set(ic.express_ids(os_name)), os_name
    for comp in ic.components_for("express", os_name="macos"):
        assert not _needs_containers(comp), comp.id
    assert not any(_needs_containers(c) for c in ic.components_for("express")), "no system named: none"
    # Negative control: Neo4j in Express without best effort.
    import dataclasses

    planted = tuple(dataclasses.replace(c, best_effort=False) if c.id == "graph_neo4j" else c
                    for c in ic.COMPONENTS)
    assert ("windows", "graph_neo4j") in express_failing_for_want_of_docker(planted)


def test_every_part_says_what_it_gives_and_what_kazma_lacks():
    for comp in ic.COMPONENTS:
        assert comp.gives and comp.lacks and comp.title, comp.id
        assert all(need in ic.full_ids() for need in comp.needs), comp.id
        assert all(dep in sd.DEPENDENCIES for dep in comp.system), comp.id


def test_every_mode_holds_the_test_suite():
    from tests.test_setup_extras_run_the_suite import _project, missing_for

    data = _project()
    for mode in ("express", "full", "custom"):
        extras = set(ic.extras_for(ic.components_for(mode)))
        assert not missing_for(extras, data), (mode, sorted(missing_for(extras, data)))


def test_needs_follow_a_part_in_and_out():
    assert {"ocr", "neural_ocr"} <= {c.id for c in ic.components_for("express")}
    dropped = {c.id for c in ic.components_for("express", without=["ocr"])}
    assert "ocr" not in dropped and "neural_ocr" not in dropped  # it needs ocr
    added = {c.id for c in ic.components_for("express", with_=["private_search"])}
    assert {"private_search", "docker"} <= added
    assert ic.toggle(set(ic.express_ids()), "document_container") >= {"document_container", "docker"}
    assert "document_container" not in ic.toggle({"docker", "document_container"}, "docker")
    with pytest.raises(KeyError):
        ic.components_for("express", with_=["no_such_part"])
    with pytest.raises(ValueError):
        ic.components_for("everything")


def quickstart_parts(text: str) -> list[tuple[str, str, str]]:
    """The quickstart's table of parts: (title, Express mark, without it)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Part | Express | Full | Without it |"))
    rows = []
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append((cells[0], cells[1], cells[3]))
    return rows


def _express_mark(comp) -> str:
    """The quickstart's Express column: "yes" everywhere, the systems by name
    for a part limited to some (``express_on``), empty otherwise."""
    if not comp.express:
        return ""
    names = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}
    return ", ".join(names[s] for s in comp.express_on) if comp.express_on else "yes"


def test_the_quickstart_lists_every_part_as_the_table_says():
    text = (ROOT / "docs" / "docs" / "guide" / "quickstart.md").read_text(encoding="utf-8")
    expected = [(c.title, _express_mark(c), c.lacks) for c in ic.COMPONENTS]
    assert quickstart_parts(text) == expected
    # Negative control: a part the page does not name.
    assert quickstart_parts(text) != expected + [("Something new", "", "nothing")]


def test_the_service_names_match_the_service_installer():
    from kazma_cli import install

    text = (ROOT / "scripts" / "service" / "install_service.py").read_text(encoding="utf-8")
    assert re.search(rf'^TASK_NAME = "{install.SERVICE_TASK}"', text, re.M)
    assert re.search(rf'^SERVICE_NAME = "{install.SERVICE_UNIT}"', text, re.M)
    assert re.search(rf'^LAUNCHD_LABEL = "{install.SERVICE_LABEL}"', text, re.M)


def test_the_extras_step_never_installs_kazma_by_name(tmp_path):
    """No Kazma package is published on PyPI (AUD-029): outside a checkout the
    extras' own requirements are installed, never ``kazma[...]``."""
    from kazma_cli.install import Machine, _extras_step

    machine = Machine(which=lambda name: "uv" if name == "uv" else None, root=tmp_path, python="py")
    step = _extras_step(["rag", "dev", "document-platform"], machine)
    assert step.argv[:5] == ("uv", "pip", "install", "--python", "py")
    assert step.argv[5:] and not any(a.lower().startswith("kazma") for a in step.argv[5:]), step.argv
    # In a checkout whose environment this is: uv sync --inexact, the lock's versions.
    import sys

    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    checkout = Machine(which=lambda name: "uv", root=Path(sys.prefix).parent, python="py")
    if (Path(sys.prefix).parent / "uv.lock").is_file():
        argv = _extras_step(["rag"], checkout).argv
        assert argv[1:2] == ("sync",) and "--inexact" in argv and argv[-2:] == ("--extra", "rag")


# ── Node.js and Docker's preconditions ────────────────────────────────


def _runner(answers: dict[str, tuple[int, str]]):
    def run(argv, **kw):
        for key, (code, out) in answers.items():
            if key in " ".join(argv):
                return subprocess.CompletedProcess(argv, code, out, "")
        return subprocess.CompletedProcess(argv, 127, "", "")
    return run


@pytest.mark.parametrize("version,ok", [("v22.11.0", True), ("v16.20.2", False)])
def test_node_is_checked_by_its_version(version, ok, tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path))
    which = {"npx": "/usr/bin/npx", "node": "/usr/bin/node"}.get
    state = sd._check_node(_runner({"--version": (0, version)}), which)
    assert state.ok is ok and (ok or state.missing == ["node"])
    assert sd._check_node(_runner({}), lambda name: None).ok is False


def _facts(model: str, hypervisor: bool, firmware: bool) -> str:
    return json.dumps({"model": model, "hypervisor": hypervisor, "firmware": firmware})


def test_docker_readiness_on_windows():
    vm = sd.docker_readiness(WINDOWS, run=_runner({
        "Get-CimInstance": (0, _facts("Microsoft Corporation Virtual Machine", True, False)), "wsl": (1, "")}))
    assert vm.ok is None and "virtual machine" in vm.note and vm.wsl_missing
    off = sd.docker_readiness(WINDOWS, run=_runner({
        "Get-CimInstance": (0, _facts("Dell Inc. OptiPlex", False, False)), "wsl": (0, "")}))
    assert off.ok is False and "firmware" in off.problem and not off.wsl_missing
    on = sd.docker_readiness(WINDOWS, run=_runner({
        "Get-CimInstance": (0, _facts("Dell Inc. OptiPlex", True, False)), "wsl": (0, "")}))
    assert on.ok is True  # Hyper-V running: Windows no longer reports the switch
    unknown = sd.docker_readiness(WINDOWS, run=_runner({"wsl": (0, "")}))
    assert unknown.ok is None and unknown.note
    assert sd.docker_readiness(Platform("debian", "apt"), run=_runner({})).ok is True


# ── an install, end to end, on a fake machine ─────────────────────────


class _BoxFont:
    """Kazma's document font on the fake Windows: not installed until setup does it."""

    def __init__(self):
        self.installed = False

    def state(self):
        from kazma_core.system_deps import DepState

        if self.installed:
            return DepState("fonts", True, "IBM Plex Sans Arabic, installed for all users")
        return DepState("fonts", False, "IBM Plex Sans Arabic is not installed for all users", ["ibm plex"])

    def install(self):
        self.installed = True
        return True, "installed for all users: IBMPlexSansArabic-Regular.ttf, IBMPlexSansArabic-Bold.ttf"


class Box:
    """A Windows machine with nothing on it; ``run`` installs as the real
    programs would."""

    def __init__(self, tmp_path: Path):
        self.extras: set[str] = set()
        self.langs: tuple[str, ...] | None = None
        self.soffice = self.clam = self.scheduled = self.docker = self.node = False
        self.models = self.chromium = self.service = self.kazma_up = self.pg = self.searxng = False
        self.neo4j = False
        self.embedding_model = False
        self.container = False
        #: How many /healthz asks a just-started SearXNG leaves unanswered
        #: (the boot drill: 2 s after `compose up` it did not answer yet);
        #: None never answers.
        self.searxng_silent_for: int | None = 0
        self.vcruntime: tuple[int, int] | None = None  # a fresh Windows has none (the clean VM, 2026-11-08)
        self.virtualization = _facts("Dell Inc. OptiPlex", True, True)
        self.wsl = True
        self.fail: dict[str, int] = {}  # winget package -> times it fails
        self.ran: list[list[str]] = []
        self.opened: list[str] = []
        self.root = tmp_path / "kazma"
        for rel in ("scripts/ocr_models.py", "scripts/service/install_service.py",
                    "scripts/document_worker_image.py", "docker-compose.yml", "deploy/docker-compose.neo4j.yml",
                    "scripts/service/kazma_guard.py"):
            (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.root / rel).write_text("", encoding="utf-8")
        (self.root / "deploy" / "document-worker").mkdir(parents=True)
        self.home = tmp_path
        self.house_font = _BoxFont()
        #: Ports another program listens on (the install's port check reads
        #: these, never this computer's real ports).
        self.busy_ports: set[int] = set()
        # Kazma's own container engine (Windows): what of it is missing, the
        # steps setup ran, and what its preflight finds.
        self.engine_missing = ["distro", "docker", "cli", "context", "task", "shutdown"]
        self.engine_actions: list[str] = []
        self.engine_problem = ""

    def engine_check(self):
        from kazma_core.container_engine_setup import EngineCheck

        missing = (["wsl"] if not self.wsl else []) + list(self.engine_missing)
        if not missing:
            return EngineCheck(True, "Kazma's engine, Docker 29.9.3", [], "29.9.3")
        return EngineCheck(False, "missing: " + ", ".join(missing), missing)

    def engine_action(self, action):
        from kazma_core.container_engine_setup import RESTART

        self.engine_actions.append(action)
        if action == "engine:wsl":
            self.wsl = True
            return True, RESTART
        gives = {"engine:distro": "distro", "engine:docker": "docker", "engine:cli": "cli",
                 "engine:context": "context", "engine:task": "task", "engine:shutdown": "shutdown"}
        if action in gives and gives[action] in self.engine_missing:
            self.engine_missing.remove(gives[action])
        if action == "engine:cli":
            self.docker = True
        return True, "done"

    def port_problem(self, port, *, current=None):
        from kazma_core.server_port import port_problem

        return port_problem(port, current=current, ranges=[],
                            in_use=lambda n, host: "another program already listens on it" if n in self.busy_ports
                            else None)

    def which(self, name):
        found = {"tesseract": self.langs is not None, "clamscan": self.clam, "freshclam": self.clam,
                 "node": self.node, "npx": self.node, "uv": True}.get(name)
        return f"C:/bin/{name}.exe" if found else None

    def finders(self):
        return {"soffice": lambda: "C:/LO/soffice.com" if self.soffice else None,
                "docker": lambda: "C:/Docker/docker.exe" if self.docker else None,
                "vcruntime": lambda: self.vcruntime,
                "house_font": self.house_font,
                "engine": self.engine_check,
                "engine_action": self.engine_action,
                "engine_preflight": lambda: self.engine_problem}

    def run(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.ran.append(argv)
        line = " ".join(argv)
        done = lambda code=0, out="": subprocess.CompletedProcess(argv, code, out, "")  # noqa: E731
        name = Path(argv[0]).stem
        if argv[:2] == ["winget", "install"]:
            package = argv[argv.index("--id") + 1]
            if self.fail.get(package, 0) > 0:
                self.fail[package] -= 1
                return done(0x80004004, "Cancelled\n")
            setattr_for = {"UB-Mannheim.TesseractOCR": ("langs", ("eng", "osd")),
                           "TheDocumentFoundation.LibreOffice": ("soffice", True), "Cisco.ClamAV": ("clam", True),
                           "OpenJS.NodeJS.LTS": ("node", True), "Docker.DockerDesktop": ("docker", True),
                           "Microsoft.VCRedist.2015+.x64": ("vcruntime", (14, 44))}
            attr, value = setattr_for[package]
            setattr(self, attr, value)
            return done(0, "Successfully installed\n")
        if name == "uv" and "--extra" in argv:
            self.extras |= {argv[i + 1] for i, a in enumerate(argv) if a == "--extra"}
            return done()
        if name == "tesseract":
            return done(0, "List of languages:\n" + "\n".join(self.langs or ()) if "--list-langs" in argv
                        else "tesseract 5.4.0\n")
        if name == "node":
            return done(0, "v22.11.0\n")
        if name == "docker" and "version" in argv:
            return done(0 if self.docker else 1, "29.0.0\n")
        if argv[:2] == ["schtasks", "/Create"]:
            self.scheduled = True
            return done()
        if argv[:2] == ["schtasks", "/Query"]:
            wanted = argv[argv.index("/TN") + 1]
            return done(0 if (self.scheduled if wanted == sd.FRESHCLAM_TASK else self.service) else 1)
        if argv[:2] == ["schtasks", "/Run"]:
            self.kazma_up = True
            return done()
        # PyTorch and ONNX Runtime load the Visual C++ runtime: without it the
        # model steps fail as they did on the clean VM (2026-11-08).
        if "ocr_models.py" in line:
            if not self.vcruntime:
                return done(1, "ImportError: DLL load failed while importing onnxruntime_pybind11_state\n")
            self.models = True
            return done()
        if "download_local_model" in line:
            if not self.vcruntime:
                return done(1, 'OSError: [WinError 126] Error loading "torch\\lib\\c10.dll" or one of its '
                               "dependencies.\n")
            self.embedding_model = True
            return done()
        if "install_service.py" in line:
            self.service = True
            return done()
        if "playwright" in line:
            self.chromium = True
            return done()
        if "document_worker_image.py" in line:
            self.container = True
            return done()
        if "compose" in argv:
            if "kazma-neo4j" in argv:
                self.neo4j = True
            else:
                self.searxng = True
            return done()
        if "kazma_guard.py" in line and "--reload" in argv:
            self.reloaded = getattr(self, "reloaded", 0) + 1
            return done()
        if "Get-CimInstance" in line:
            return done(0, self.virtualization)
        if argv[:2] == ["wsl", "--status"]:
            return done(0 if self.wsl else 1)
        if argv[:2] == ["wsl", "--install"]:
            self.wsl = True
            return done()
        return done()

    def answers(self, url: str) -> bool:
        if url == "http://127.0.0.1:7474/":
            return self.neo4j
        if url.endswith("/healthz"):
            if not self.searxng or self.searxng_silent_for is None:
                return False
            if self.searxng_silent_for > 0:
                self.searxng_silent_for -= 1
                return False
            return True
        return self.kazma_up


def _options(**kw) -> argparse.Namespace:
    base = dict(mode=None, with_=[], without=[], yes=False, check=False, dry_run=False, list=False,
                packages_only=False, no_browser=False, docker="kazma", port=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def box(tmp_path, monkeypatch):
    from kazma_cli import install
    from kazma_core.documents.ocr import neural_models

    b = Box(tmp_path)
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    monkeypatch.delenv("KAZMA_SEARXNG_URL", raising=False)
    monkeypatch.delenv("KAZMA_PORT", raising=False)
    monkeypatch.setattr(install, "_extras_present", lambda: set(b.extras))
    monkeypatch.setattr(install, "_playwright_chromium", lambda: b.chromium)
    monkeypatch.setattr(install, "_embedding_model_downloaded", lambda: b.embedding_model)
    monkeypatch.setattr(install, "_postgres_state", lambda platform, machine: (
        (True, "PostgreSQL 16") if b.pg else (False, "not set up")))
    monkeypatch.setattr(install, "_container_state", lambda machine, root: (
        (True, "the container profile") if b.container else (False, "the image is not built")))
    monkeypatch.setattr(install, "_postgres", lambda log: (setattr(b, "pg", True), (True, ""))[1])
    monkeypatch.setattr(install, "_container_profile", lambda: (True, ""))
    monkeypatch.setattr(install, "_env_path", lambda: tmp_path / ".env")
    monkeypatch.setattr(install, "_extras_step", lambda missing, machine: __import__(
        "kazma_core.postgres_service", fromlist=["Step"]).Step(
            f"install Kazma's packages: {', '.join(missing)}",
            ("uv", "sync", "--inexact", *[a for e in missing for a in ("--extra", e)])))
    monkeypatch.setattr(neural_models, "verify_model_directory", lambda directory=None: neural_models.ModelStatus(
        b.models, "models", "1" if b.models else None, None if b.models else "not installed"))

    def fetch(lang, folder, **kw):
        b.langs = tuple(sorted(set(b.langs or ()) | {lang}))
        return folder / f"{lang}.traineddata"

    monkeypatch.setattr(sd, "fetch_tessdata", fetch)
    return b


def _machine(box: Box, *, answers=(), interactive=True, admin=True, online=True, free=500 * 2**30):
    from kazma_cli.install import Machine

    queue = list(answers)
    asked: list[str] = []
    sleeps: list[float] = []

    def ask(question):
        asked.append(question)
        if not queue:
            raise AssertionError(f"asked with no answer left: {question}")
        return queue.pop(0)

    machine = Machine(run=box.run, which=box.which, ask=ask, sleep=sleeps.append,
                      open_url=box.opened.append, answers=box.answers, free_bytes=lambda path: free,
                      reachable=lambda host: online, is_admin=lambda: admin, interactive=lambda: interactive,
                      platform=WINDOWS, finders=box.finders(), root=box.root, python="py",
                      log_path=box.home / "install.log", record_path=box.home / "setup_record.json",
                      env_path=box.home / ".env", port_problem=box.port_problem)
    machine.asked, machine.sleeps = asked, sleeps
    return machine


def _installs(box: Box) -> list[str]:
    return [a[a.index("--id") + 1] for a in box.ran if a[:2] == ["winget", "install"]]


def test_express_installs_the_engine_searxng_and_neo4j_and_a_second_run_does_nothing(box, capsys):
    """Express on Windows: every everyday part, and (2026-11-13) private web
    search and the Neo4j memory graph on Kazma's own engine, never Docker
    Desktop. Each service gets its generated secret before it starts, and
    Kazma is pointed at it."""
    from kazma_cli.install import install

    machine = _machine(box, answers=["", "", ""])  # Express (the default), port 9090 (Enter), then yes
    assert install(_options(), machine) == 0
    out = capsys.readouterr().out
    assert machine.asked == ["Choose 1, 2 or 3 [1]: ", "Port for Kazma's page [9090]: ", "Install now? [Y/n] "]
    assert "KAZMA_PORT=9090" in (box.home / ".env").read_text(encoding="utf-8")
    assert box.extras == set(ic.extras_for(ic.components_for("express", os_name="windows")))
    assert "neo4j" in box.extras
    assert _installs(box) == ["Microsoft.VCRedist.2015+.x64", "UB-Mannheim.TesseractOCR",
                              "TheDocumentFoundation.LibreOffice", "Cisco.ClamAV", "OpenJS.NodeJS.LTS"]
    assert box.models and box.embedding_model and box.service and box.scheduled and box.langs and "ara" in box.langs
    assert "Docker.DockerDesktop" not in _installs(box)
    assert box.engine_actions == ["engine:distro", "engine:docker", "engine:cli", "engine:context",
                                  "engine:task", "engine:shutdown", "engine:record"]
    assert box.searxng and box.neo4j
    env = (box.home / ".env").read_text(encoding="utf-8")
    for line in ("KAZMA_SEARXNG_URL=http://127.0.0.1:8088", "KAZMA_NEO4J_URL=bolt://127.0.0.1:7687",
                 "KAZMA_NEO4J_DEFAULT=1"):
        assert line in env, line
    for secret in ("SEARXNG_SECRET", "KAZMA_NEO4J_PASSWORD"):
        value = [ln.split("=", 1)[1] for ln in env.splitlines() if ln.startswith(secret + "=")]
        assert value and len(value[0]) >= 64, secret
    assert box.opened == ["http://127.0.0.1:9090/setup"], out  # the setup guide
    record = json.loads((box.home / "setup_record.json").read_text(encoding="utf-8"))["install"]
    assert record["mode"] == "express" and record["components"] == ic.express_ids("windows")
    assert all(r["ok"] for r in record["results"].values()), record["results"]
    log = (box.home / "install.log").read_text(encoding="utf-8")
    assert "The plan:" in log and "Successfully installed" in log
    # Not chosen: named on screen; what Kazma lacks without each is in the log.
    assert "Left out (6): " in out and "Locked document worker" in out
    assert "Not chosen" in log and ic.by_id("document_container").lacks in log

    box.ran.clear()
    again = _machine(box, answers=[])
    assert install(_options(), again) == 0
    assert again.asked == [] and not _installs(box)
    assert "Installed as Express" in capsys.readouterr().out


def test_control_without_the_runtime_meaning_search_and_neural_ocr_fail(box, capsys, monkeypatch):
    """Negative control: the part table before 2026-11-08 named no Visual C++
    runtime. On a fresh Windows PyTorch and ONNX Runtime could not load, so
    the model downloads failed, as on the clean VM."""
    import dataclasses

    from kazma_cli.install import install

    old = tuple(dataclasses.replace(c, system=tuple(d for d in c.system if d != "vcruntime"))
                for c in ic.COMPONENTS)
    monkeypatch.setattr(ic, "COMPONENTS", old)
    monkeypatch.setattr(ic, "_BY_ID", {c.id: c for c in old})
    machine = _machine(box, answers=["", "", ""])
    install(_options(), machine)
    assert "Microsoft.VCRedist.2015+.x64" not in _installs(box)
    assert not box.embedding_model and not box.models, "the model steps failed without the runtime"


def test_problems_are_said_before_anything_is_done(box, capsys):
    from kazma_cli.install import install

    machine = _machine(box, answers=["", "", "n"], online=False, free=2 * 2**30)
    assert install(_options(), machine) == 1
    out = capsys.readouterr().out
    assert out.index("Problems found before anything was done") < out.index("steps to run")
    assert "neither pypi.org nor github.com answers" in out and "GB free" in out
    assert not _installs(box) and not box.extras and "Nothing was done." in out


def test_full_where_docker_cannot_run_installs_the_rest_and_says_why(box, capsys):
    from kazma_cli.install import install

    box.virtualization = _facts("Dell Inc. OptiPlex", False, False)
    machine = _machine(box)
    assert install(_options(mode="full", yes=True), machine) == 1
    out = capsys.readouterr().out
    assert "Docker cannot run here: virtualization is turned off" in out
    assert "Docker.DockerDesktop" not in _installs(box) and not box.container and not box.searxng
    assert box.pg and box.chromium and box.models and box.service
    # The Neo4j driver is not installed where Neo4j cannot run.
    assert box.extras == set(ic.extras_for(ic.components_for("full"))) - {"neo4j"}
    results = json.loads((box.home / "setup_record.json").read_text(encoding="utf-8"))["install"]["results"]
    assert results["docker"]["ok"] is False and "firmware" in results["docker"]["detail"]
    assert results["document_container"]["ok"] is False and results["postgres"]["ok"] is True
    assert ic.by_id("private_search").lacks in out


def test_express_where_containers_cannot_run_ends_as_done_and_says_what_kazma_lacks(box, capsys):
    """The owner (2026-11-13): SearXNG and Neo4j in Express. A computer with
    virtualization off gets every other part, is told what it lacks, and the
    install ends as done: Fable's review found a blocked part made every run
    end "stopped short" forever."""
    from kazma_cli.install import EXIT_DONE, install

    box.virtualization = _facts("Dell Inc. OptiPlex", False, False)
    assert install(_options(yes=True), _machine(box)) == EXIT_DONE
    out = capsys.readouterr().out
    assert "Not available on this computer, and what Kazma lacks:" in out
    for part in ("docker", "private_search", "graph_neo4j"):
        assert ic.by_id(part).lacks in out, part
    assert not box.searxng and not box.neo4j and box.engine_actions == []
    assert box.service and box.models and box.embedding_model, "everything else is installed"
    # A second run does nothing more and still ends as done.
    assert install(_options(yes=True), _machine(box)) == EXIT_DONE


def test_control_a_blocked_part_that_is_not_best_effort_fails_the_install(box, capsys, monkeypatch):
    """Negative control: the same machine with the container parts not marked
    best effort ends as not done, as every run did before 2026-11-13."""
    import dataclasses

    from kazma_cli.install import EXIT_NOT_DONE, install

    old = tuple(dataclasses.replace(c, best_effort=False) for c in ic.COMPONENTS)
    monkeypatch.setattr(ic, "COMPONENTS", old)
    monkeypatch.setattr(ic, "_BY_ID", {c.id: c for c in old})
    box.virtualization = _facts("Dell Inc. OptiPlex", False, False)
    assert install(_options(yes=True), _machine(box)) == EXIT_NOT_DONE


def test_express_without_wsl_asks_for_the_restart_then_the_next_run_finishes(box, capsys):
    from kazma_cli.install import EXIT_DONE, EXIT_RESTART, install

    box.wsl = False
    assert install(_options(yes=True), _machine(box)) == EXIT_RESTART
    out = capsys.readouterr().out
    assert "Restart Windows to finish WSL, then run setup again" in out
    assert box.engine_actions == ["engine:wsl"] and not box.searxng and not box.neo4j
    assert box.service, "the rest is installed before the restart"
    assert install(_options(yes=True), _machine(box)) == EXIT_DONE
    assert box.searxng and box.neo4j


def test_a_part_left_out_by_name_stays_out_on_the_next_run(box, capsys):
    """Fable's review: `--without` was forgotten, so a re-run (or Express
    gaining a part) installed what the owner had left out."""
    from kazma_cli.install import EXIT_DONE, install

    assert install(_options(yes=True, without=["graph_neo4j"]), _machine(box)) == EXIT_DONE
    assert box.searxng and not box.neo4j
    record = json.loads((box.home / "setup_record.json").read_text(encoding="utf-8"))["install"]
    assert record["without"] == ["graph_neo4j"]
    assert install(_options(yes=True), _machine(box)) == EXIT_DONE
    assert not box.neo4j, "a plain re-run keeps it out"
    assert install(_options(yes=True, with_=["graph_neo4j"]), _machine(box)) == EXIT_DONE
    assert box.neo4j, "asking for it by name brings it in"


def test_an_express_install_from_before_gains_the_new_parts_on_a_rerun(box, capsys):
    """An Express install recorded before SearXNG and Neo4j joined Express:
    Settings names them (install_overview), and a plain re-run adds them."""
    from kazma_cli.install import EXIT_DONE, install

    assert install(_options(yes=True, without=["docker"]), _machine(box)) == EXIT_DONE
    record_path = box.home / "setup_record.json"
    data = json.loads(record_path.read_text(encoding="utf-8"))
    data["install"]["without"] = []  # what a record from before 2026-11-13 holds
    record_path.write_text(json.dumps(data), encoding="utf-8")
    overview = ic.install_overview(data, "windows")
    assert overview["new_in_express"] == ["docker", "private_search", "graph_neo4j"]
    assert ic.install_overview(data, "macos")["new_in_express"] == []
    assert install(_options(yes=True), _machine(box)) == EXIT_DONE
    assert box.searxng and box.neo4j
    after = json.loads(record_path.read_text(encoding="utf-8"))
    assert ic.install_overview(after, "windows")["new_in_express"] == []


def test_a_running_kazma_is_reloaded_to_use_the_services_set_up(box, capsys):
    """Kazma reads .env when it starts: when the service was already running,
    setup asks its guard to reload it once no chat is running."""
    from kazma_cli.install import EXIT_DONE, install

    assert install(_options(yes=True, without=["docker"]), _machine(box)) == EXIT_DONE
    assert box.service and not getattr(box, "reloaded", 0)
    assert install(_options(yes=True, without=[], with_=["private_search", "graph_neo4j"]),
                   _machine(box)) == EXIT_DONE
    assert box.searxng and box.neo4j and box.reloaded == 1


def test_full_installs_kazmas_engine_not_docker_desktop(box, capsys):
    """2026-11-12: Docker Desktop starts only at a sign-in, so after an
    unattended restart every container waited. A container part now brings
    Kazma's own engine (started at boot by its task), never Docker Desktop;
    a second run finds it and does nothing more."""
    from kazma_cli.install import install

    assert install(_options(mode="full", yes=True), _machine(box)) == 0
    assert "Docker.DockerDesktop" not in _installs(box)
    assert box.engine_actions == ["engine:distro", "engine:docker", "engine:cli", "engine:context",
                                  "engine:task", "engine:shutdown", "engine:record"]
    assert box.container and box.searxng, "the container parts ran on the engine"
    results = json.loads((box.home / "setup_record.json").read_text(encoding="utf-8"))["install"]["results"]
    assert results["docker"]["ok"] is True and "Kazma's engine" in results["docker"]["detail"]
    box.engine_actions.clear()
    assert install(_options(mode="full", yes=True), _machine(box)) == 0
    assert box.engine_actions == [], "a second run changes nothing"


def test_a_just_started_searxng_is_waited_for_before_it_is_judged(box, capsys):
    """The boot drill (2026-11-12): a Full install asked SearXNG 2 s after
    `docker compose up`, called private search not installed and ended with
    exit 1 ("run setup again"); SearXNG answered 49 s later. Setup now waits
    for it, within SEARXNG_READY_WAIT_S."""
    from kazma_cli.install import SEARXNG_READY_WAIT_S, install

    box.searxng_silent_for = 6  # the drill's: not yet, for a while
    machine = _machine(box)
    assert install(_options(mode="full", yes=True), machine) == 0
    out = capsys.readouterr().out
    assert "wait for SearXNG to answer" in out and "Private web search" not in out.split("Done:")[-1]
    assert machine.sleeps.count(3.0) == 6, "one short wait per unanswered ask"
    results = json.loads((box.home / "setup_record.json").read_text(encoding="utf-8"))["install"]["results"]
    assert results["private_search"]["ok"] is True
    assert SEARXNG_READY_WAIT_S <= 180, "bounded: a broken SearXNG fails its step, it does not hold setup"


def test_a_searxng_that_never_answers_fails_its_step_in_bounded_time(box, capsys):
    """Negative control: the wait is not a way to call a dead SearXNG ready."""
    from kazma_cli.install import SEARXNG_READY_WAIT_S, install

    box.searxng_silent_for = None
    machine = _machine(box)
    assert install(_options(mode="full", yes=True), machine) == 1
    out = capsys.readouterr().out
    assert f"SearXNG did not answer at http://127.0.0.1:8088 within {int(SEARXNG_READY_WAIT_S)} s" in out
    assert machine.sleeps.count(3.0) <= SEARXNG_READY_WAIT_S / 3.0 + 1


def test_full_installs_wsl_first_and_asks_for_the_restart(box, capsys):
    from kazma_cli.install import install

    from kazma_cli.install import EXIT_RESTART

    box.wsl = False
    assert install(_options(mode="full", yes=True), _machine(box)) == EXIT_RESTART
    out = capsys.readouterr().out
    assert box.engine_actions == ["engine:wsl"], "the engine's other steps wait for the restart"
    assert "after Windows restarts" in out
    assert "Restart Windows to finish WSL, then run setup again" in out
    assert "Docker.DockerDesktop" not in _installs(box)


def test_control_docker_desktop_only_when_chosen(box, capsys):
    """Negative control, and the owner's choice: ``--docker desktop`` installs
    Docker Desktop and none of Kazma's engine. WSL comes from the engine's
    own step (its pinned MSI): `wsl --install` is refused outside a
    signed-in console (the boot drill's control, 2026-11-13), and an
    unattended Desktop install never got Docker Desktop at all."""
    from kazma_cli.install import install

    box.wsl = False
    install(_options(mode="full", yes=True, docker="desktop"), _machine(box))
    out = capsys.readouterr().out
    assert box.engine_actions == ["engine:wsl"], "WSL only; none of the engine's own steps"
    assert "Docker.DockerDesktop" not in _installs(box), "after the restart"
    assert "Restart Windows to finish WSL, then run setup again" in out
    install(_options(mode="full", yes=True, docker="desktop"), _machine(box))
    assert "Docker.DockerDesktop" in _installs(box)
    assert box.engine_actions == ["engine:wsl"], "WSL was there: nothing of the engine ran"
    assert not [a for a in box.ran if a[:2] == ["wsl", "--install"]], "never the refused command"


def test_what_stops_kazmas_engine_is_said_first_and_blocks_only_containers(box, capsys):
    from kazma_cli.install import install

    box.engine_problem = "Docker already runs in the WSL distro 'Ubuntu'"
    assert install(_options(mode="full", yes=True), _machine(box)) == 1
    out = capsys.readouterr().out
    assert "Kazma's container engine cannot run here: Docker already runs" in out
    assert box.engine_actions == [] and not box.container and not box.searxng
    assert box.pg and box.service, "the rest is installed"


def test_a_virtual_machine_is_told_what_docker_needs(box, capsys):
    from kazma_cli.install import install

    box.virtualization = _facts("Microsoft Corporation Virtual Machine", True, False)
    install(_options(mode="full", yes=True), _machine(box))
    assert "ExposeVirtualizationExtensions" in capsys.readouterr().out


def test_a_failing_part_is_retried_then_stops_only_what_needs_it(box, capsys):
    from kazma_cli.install import _SYSTEM_ATTEMPTS, _SYSTEM_RETRY_WAIT_S, install

    box.fail["UB-Mannheim.TesseractOCR"] = 99
    machine = _machine(box)
    assert install(_options(yes=True), machine) == 1
    out = capsys.readouterr().out
    assert _installs(box).count("UB-Mannheim.TesseractOCR") == _SYSTEM_ATTEMPTS
    assert machine.sleeps.count(_SYSTEM_RETRY_WAIT_S) >= _SYSTEM_ATTEMPTS - 1
    assert not box.models  # neural OCR needs Tesseract
    assert box.soffice and box.clam and box.node and box.service  # the others went on
    assert ic.by_id("ocr").lacks in out and "Run setup again to finish" in out


def test_without_a_terminal_nothing_is_done_and_packages_only_installs_packages(box, capsys):
    from kazma_cli.install import install

    assert install(_options(), _machine(box, interactive=False)) == 1
    assert "no terminal to ask" in capsys.readouterr().out and not box.extras
    assert install(_options(packages_only=True), _machine(box, interactive=False)) in (0, 1)
    assert box.extras == set(ic.extras_for(ic.components_for("express", os_name="windows")))
    assert not _installs(box) and not box.service


def test_an_administrator_terminal_is_asked_for_before_anything(box, capsys):
    from kazma_cli.install import install

    assert install(_options(yes=True), _machine(box, admin=False)) == 2
    out = capsys.readouterr().out
    assert "administrator terminal" in out and not _installs(box) and not box.extras


def test_custom_is_a_checklist_and_a_rerun_keeps_the_choice(box, capsys):
    from kazma_cli.install import install

    rag = 1 + ic.full_ids().index("meaning_search")
    browser = 1 + ic.full_ids().index("browser")
    machine = _machine(box, answers=["3", f"{rag} {browser}", "", "", ""])
    assert install(_options(), machine) == 0
    assert "rag" not in box.extras and "web" in box.extras and box.chromium
    record = json.loads((box.home / "setup_record.json").read_text(encoding="utf-8"))["install"]
    assert record["mode"] == "custom" and "meaning_search" not in record["components"]
    assert "without it:" in capsys.readouterr().out
    again = _machine(box, answers=[])
    assert install(_options(), again) == 0 and again.asked == []
    assert "rag" not in box.extras


def test_check_and_dry_run_change_nothing(box, capsys):
    from kazma_cli.install import install

    assert install(_options(check=True), _machine(box, answers=[""])) == 1
    assert install(_options(mode="full", dry_run=True), _machine(box)) == 0
    assert not _installs(box) and not box.extras and not box.service
    assert not (box.home / "setup_record.json").exists() or "install" not in json.loads(
        (box.home / "setup_record.json").read_text(encoding="utf-8"))


def test_the_list_names_every_part(capsys):
    from kazma_cli.install import install

    assert install(_options(list=True)) == 0
    out = capsys.readouterr().out
    for comp in ic.COMPONENTS:
        assert comp.id in out and comp.lacks in out


# -- what the screen shows (the clean Windows VM, 2026-11-07) -------------------------------


def test_an_install_shows_a_short_summary_and_logs_every_step(box, capsys):
    """The first clean-VM run printed every part's state, every part left out
    with what Kazma lacks without it, and every step's command line before
    one question. The screen now names the parts; the log holds the rest."""
    from kazma_cli.install import install
    from kazma_core.version import get_version

    machine = _machine(box, answers=["", "", ""])
    assert install(_options(), machine) == 0
    out = capsys.readouterr().out
    first = out.splitlines()[0]
    assert first == f"Kazma {get_version()}: setup on Windows, with winget", first
    before_question = out[: out.index("steps to run")]
    assert "To install (" in before_question and "Left out (6): " in before_question
    assert "Meaning search" in before_question and "(bge-m3)" not in before_question
    assert "winget install" not in out and "The plan:" not in out
    assert len(before_question.splitlines()) <= 20, before_question
    log = (box.home / "install.log").read_text(encoding="utf-8")
    assert "The plan:" in log and "winget install" in log and "Not chosen" in log
    assert "Done: " in out  # the end: a line, not every part's state again
    assert "After the install:" not in out and "After the install:" in log


def test_a_part_here_though_not_chosen_is_never_called_left_out(box, capsys):
    """Live 2026-11-14: a Custom run for SearXNG listed "PostgreSQL as a
    service" as left out, and the log said Kazma lacked it ("SQLite: a restore
    goes back to the last backup"), on an install that runs PostgreSQL 18 as
    a service. The parts not chosen are checked too."""
    from kazma_cli.install import install

    box.pg = True
    box.extras |= set(ic.by_id("postgres").extras)
    assert install(_options(), _machine(box, answers=["", "", ""])) == 0
    out = capsys.readouterr().out
    assert "Here, not chosen (1): PostgreSQL as a service" in out
    assert "Left out (5): " in out
    log = (box.home / "install.log").read_text(encoding="utf-8")
    assert "Here, though not chosen" in log and "PostgreSQL 16" in log
    assert ic.by_id("postgres").lacks not in log
    record = json.loads((box.home / "setup_record.json").read_text(encoding="utf-8"))["install"]
    assert "postgres" not in record["components"], "a part not chosen is not recorded as chosen"


def test_negative_control_unchecked_parts_not_chosen_read_as_left_out(box, capsys, monkeypatch):
    from kazma_cli import install as inst

    real = inst._check_parts
    express = set(ic.express_ids("windows"))

    def chosen_only(parts, machine, platform, docker="system"):
        found, states = real(parts, machine, platform, docker)
        return {k: v if k in express else inst.PartState(False, "not checked") for k, v in found.items()}, states

    monkeypatch.setattr(inst, "_check_parts", chosen_only)
    box.pg = True
    box.extras |= set(ic.by_id("postgres").extras)
    assert inst.install(_options(), _machine(box, answers=["", "", ""])) == 0
    out = capsys.readouterr().out
    assert "Left out (6): " in out and "Here, not chosen" not in out


def test_a_dry_run_still_shows_every_step(box, capsys):
    """--dry-run and --check are for reading: they keep the whole listing."""
    from kazma_cli.install import install

    assert install(_options(mode="express", dry_run=True), _machine(box)) == 0
    out = capsys.readouterr().out
    assert "The plan:" in out and "winget install" in out and "Not chosen" in out


def test_keys_typed_before_a_question_are_dropped(monkeypatch):
    """An Enter left in the console answered "Choose 1, 2 or 3" on the clean
    VM. Before asking a person, what was typed ahead is thrown away; a
    question answered by code (the tests' own) drains nothing."""
    import builtins
    import os

    from kazma_cli import setup

    drained: list[str] = []
    pending = ["\r", "x"]

    class Console:
        def isatty(self):
            return True

    monkeypatch.setattr(setup.sys, "stdin", Console())
    if os.name == "nt":
        import msvcrt

        monkeypatch.setattr(msvcrt, "kbhit", lambda: bool(pending))
        monkeypatch.setattr(msvcrt, "getwch", lambda: drained.append(pending.pop(0)) or drained[-1])
    else:
        import termios

        monkeypatch.setattr(termios, "tcflush", lambda fd, how: (drained.extend(pending), pending.clear()))
    monkeypatch.setattr(builtins, "input", lambda question: "2")

    assert setup._ask(builtins.input, "Choose 1, 2 or 3 [1]: ") == "2"
    assert drained == ["\r", "x"] and not pending

    # The negative control: an answer that is not a person's leaves the console alone.
    pending[:] = ["\r"]
    drained.clear()
    assert setup._ask(lambda question: "", "Install now? [Y/n] ") == ""
    assert drained == [] and pending == ["\r"]


ESC = chr(27)
BACKSLASH = chr(92)


def test_colours_only_on_a_terminal_and_the_log_stays_plain(box, capsys, monkeypatch):
    from kazma_cli import install as inst

    monkeypatch.setattr(inst, "_COLOURS", [True])
    painted = inst._paint("Kazma", "brand")
    assert painted == f"{ESC}[1;38;5;33mKazma{ESC}[0m"
    assert inst._paint("Kazma") == "Kazma"
    inst.install(_options(), _machine(box, answers=["", "", ""]))
    out = capsys.readouterr().out
    log = (box.home / "install.log").read_text(encoding="utf-8")
    assert ESC in out and ESC not in log
    monkeypatch.setattr(inst, "_COLOURS", [False])
    assert inst._paint("Kazma", "brand") == "Kazma"


# The bright yellow, bright cyan and white of a terminal's palette all but
# vanish on a light background: no setup script uses them. The scripts spell
# an escape as the text \033 (a backslash, then 033).
_FADING_ON_WHITE = re.compile(
    r"-ForegroundColor\s+(Yellow|Cyan|White|Gray)\b"  # PowerShell's bright ones
    + "|" + re.escape(BACKSLASH) + r"033\[(?:[01];)?(?:33|36|37|9[0-7])m"  # yellow, cyan, white
)


def _fading_colours(text: str) -> list[str]:
    return [m.group(0) for m in _FADING_ON_WHITE.finditer(text)]


def test_setup_scripts_end_with_the_install_s_exit_code():
    """The clean VM (2026-11-08): seven parts were left out, and setup still
    said "Setup Complete" and exited 0, so a script that ran it could not
    tell. Both scripts now end with the install's own code."""
    ps1 = (ROOT / "setup.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "setup.sh").read_text(encoding="utf-8")
    assert "$installExit = $LASTEXITCODE" in ps1 and ps1.rstrip().endswith("exit $installExit")
    assert '|| INSTALL_EXIT=$?' in sh and sh.rstrip().endswith('exit "$INSTALL_EXIT"')
    for text in (ps1, sh):
        assert "Setup Stopped Short" in text, "a short install is not called complete"


def test_setup_scripts_use_colours_a_light_terminal_shows():
    root = Path(__file__).resolve().parent.parent
    for name in ("setup.ps1", "setup.sh"):
        text = (root / name).read_text(encoding="utf-8-sig")
        assert _fading_colours(text) == [], name
    # The negative control: the colours both scripts used until 2026-11-07.
    assert _fading_colours('Write-Host "x" -ForegroundColor Yellow') == ["-ForegroundColor Yellow"]
    old_yellow = BACKSLASH + "033[1;33m"
    assert _fading_colours(f"YELLOW='{old_yellow}'") == [old_yellow]


def test_the_setup_intro_says_what_kazma_is_in_its_own_words():
    """The intro's one line is the first sentence of What Kazma is."""
    root = Path(__file__).resolve().parent.parent
    page = (root / "docs" / "docs" / "guide" / "what-is-kazma.md").read_text(encoding="utf-8")
    sentence = re.search(r"\*\*(Kazma is the [^*]+)\*\*", page).group(1)
    line = sentence.replace("Kazma is the", "The", 1)
    for name in ("setup.ps1", "setup.sh"):
        assert line in (root / name).read_text(encoding="utf-8-sig"), (name, line)


# -- the first start (the clean Windows VM, 2026-11-07) -------------------------------------


def test_the_meaning_search_model_is_downloaded_by_the_install(box, capsys):
    """The server's first start downloaded 4.3 GB before it answered at all
    (5.5 minutes, nothing on screen). The meaning-search part is in place only
    with its model on disk, and the install downloads it."""
    from kazma_cli.install import install

    box.extras = set(ic.extras_for(ic.components_for("express", os_name="windows")))  # packages there, model not
    assert install(_options(yes=True), _machine(box)) == 0
    downloads = [a for a in box.ran if "download_local_model" in " ".join(a)]
    assert len(downloads) == 1 and box.embedding_model
    assert not [a for a in box.ran if "--extra" in a]  # no package step for it
    record = json.loads((box.home / "setup_record.json").read_text(encoding="utf-8"))["install"]
    assert record["results"]["meaning_search"]["ok"] is True

    # The negative control: with the model there, the part is done.
    box.ran.clear()
    assert install(_options(yes=True), _machine(box)) == 0
    assert not [a for a in box.ran if "download_local_model" in " ".join(a)]


def test_the_wait_for_kazma_counts_the_time_each_try_takes(monkeypatch):
    """A refused connection to this machine takes about 2 s on Windows; the
    wait counted only its 5 s sleeps, and 240 s became 325 s."""
    from kazma_cli import install as inst
    from kazma_cli.install import ANSWER_WAIT_S, Machine, PartState

    clock = [0.0]

    def slow_refusal(url):
        clock[0] += 2.0  # each try takes 2 s, as on Windows
        return False

    monkeypatch.setattr(inst.time, "monotonic", lambda: clock[0])
    sleeps: list[float] = []
    machine = Machine(answers=slow_refusal, sleep=sleeps.append, interactive=lambda: False)
    said: list[str] = []
    inst._finish([ic.by_id("service")], {"service": PartState(True, "")}, machine,
                 _options(no_browser=True), lambda text="", tone="": said.append(text))
    tries = len(sleeps)
    assert tries * 7.0 <= ANSWER_WAIT_S + 7.0, tries
    assert tries < ANSWER_WAIT_S / 5.0  # the old count of sleeps alone: 48 tries
    assert any("has not answered yet" in line for line in said)
