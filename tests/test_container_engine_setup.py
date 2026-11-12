"""Kazma's own container engine, set up by Kazma (2026-11-12).

``kazma_core.container_engine_setup`` installs Docker Engine in the
``kazma-engine`` WSL distro, the ``KazmaEngine`` boot task that keeps it up
with nobody signed in, Docker's Windows CLI and the ``kazma-engine`` context.
These tests drive it with a fake machine (every program it starts is answered
here) and a fake download, so nothing is installed and nothing leaves the
machine. The real install was drilled on a clean Windows VM (the plan's
Stage 2 record).
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from pathlib import Path

import pytest

from kazma_core import container_engine_setup as ces


def _context(name: str) -> None:
    """Create the docker CLI's context *name* in the test's DOCKER_CONFIG."""
    import os

    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    folder = Path(os.environ["DOCKER_CONFIG"]) / "contexts" / "meta" / digest
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "meta.json").write_text(json.dumps({"Name": name}), encoding="utf-8")


class Machine:
    """A Windows machine: WSL's distros, the task, the engine's answers."""

    def __init__(self) -> None:
        self.wsl = True
        self.distros = ["Ubuntu", ces.DISTRO]
        self.running: list[str] = [ces.DISTRO]
        self.dockerd_in: set[str] = set()
        self.task = True
        self.engine_version = "29.9.3"
        self.containers = ["kazma-searxng"]
        self.ran: list[list[str]] = []
        self.stdin: list[str | None] = []
        self.answers: dict[tuple[str, ...], tuple[int, str]] = {}

    def __call__(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.ran.append(argv)
        self.stdin.append(kw.get("input"))
        for prefix, answer in self.answers.items():
            if tuple(argv[:len(prefix)]) == prefix:
                return subprocess.CompletedProcess(argv, answer[0], answer[1], "")
        code, out = self._answer(argv)
        return subprocess.CompletedProcess(argv, code, out, "")

    def _answer(self, argv):
        name = Path(argv[0]).name.lower()
        if name == "wsl.exe":
            if not self.wsl:
                return 1, "WSL is not installed"
            if argv[1:3] == ["-l", "-q"]:
                names = self.running if "--running" in argv else self.distros
                return 0, "\n".join(names)
            if argv[1] == "-d" and argv[3:6] == ["-e", "pgrep", "-x"]:
                return (0, "123") if argv[2] in self.dockerd_in else (1, "")
            if argv[1] == "--unregister":
                if ces.DISTRO in self.distros:
                    self.distros.remove(ces.DISTRO)
                return 0, ""
            return 0, ""
        if name == "schtasks":
            if argv[1] == "/Query":
                return (0, ces.TASK_NAME) if self.task else (1, "not found")
            if argv[1] == "/Delete":
                self.task = False
            return 0, ""
        if name in ("docker.exe", "docker"):
            rest = argv[3:] if argv[1:2] == ["--context"] else argv[1:]
            if rest[:1] == ["version"]:
                return (0, self.engine_version) if self.engine_version else (1, "cannot connect")
            if rest[:2] == ["ps", "-a"]:
                return (0, "\n".join(self.containers)) if self.engine_version else (1, "cannot connect")
            return 0, ""
        return 0, ""


@pytest.fixture
def machine(monkeypatch, tmp_path):
    box = Machine()
    cli = tmp_path / "bin" / "docker.exe"
    monkeypatch.setattr("kazma_core.docker_cli.find_docker_cli", lambda: str(cli))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    # Each test its own setup record (the data folder is the session's).
    monkeypatch.setattr("kazma_core.postgres_service._record_path", lambda: tmp_path / "setup_record.json")
    _context(ces.CONTEXT)
    return box


# ── checking ────────────────────────────────────────────────────────────


def test_a_complete_engine_is_found_whole(machine):
    found = ces.check(machine)
    assert found.ok is True and found.missing == [] and found.version == "29.9.3"
    assert any(a[:3] == [a[0], "--context", ces.CONTEXT] for a in machine.ran if a[0].endswith("docker.exe")), \
        "the engine is asked through its own context, whatever the CLI's current one is"


def test_each_missing_piece_is_named(machine, monkeypatch, tmp_path):
    machine.distros = ["Ubuntu"]
    machine.task = False
    found = ces.check(machine)
    assert found.ok is False
    assert found.missing == ["distro", "task"]
    assert "the kazma-engine distro is not there" in found.detail
    assert "KazmaEngine task" in found.detail


def test_without_wsl_nothing_else_is_asked_of_it(machine):
    machine.wsl = False
    found = ces.check(machine)
    assert "wsl" in found.missing and "docker" not in found.missing
    assert not any(a[0].endswith("docker.exe") for a in machine.ran)


def test_an_engine_that_does_not_answer_is_missing_docker(machine):
    machine.engine_version = ""
    assert ces.check(machine).missing == ["docker"]


def test_without_the_context_the_engine_is_not_asked(machine, monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "other-config"))
    found = ces.check(machine)
    assert found.missing == ["context", "docker"]


# ── what would stop it ──────────────────────────────────────────────────


def test_localhost_forwarding_off_is_said_before_anything_is_installed(machine, tmp_path):
    config = tmp_path / ".wslconfig"
    config.write_text("[wsl2]\nlocalhostForwarding=false\n", encoding="utf-8")
    problem = ces.preflight(machine, wslconfig=config)
    assert "localhost forwarding is off" in problem and "--docker desktop" in problem


def test_mirrored_networking_needs_no_forwarding(machine, tmp_path):
    config = tmp_path / ".wslconfig"
    config.write_text("[wsl2]\nnetworkingMode=mirrored\nlocalhostForwarding=false\n", encoding="utf-8")
    assert ces.preflight(machine, wslconfig=config) == ""


def test_docker_in_another_distro_is_said(machine, tmp_path):
    machine.running = ["Ubuntu", ces.DISTRO, "docker-desktop"]
    machine.dockerd_in = {"Ubuntu", ces.DISTRO, "docker-desktop"}
    problem = ces.preflight(machine, wslconfig=tmp_path / "none")
    assert "'Ubuntu'" in problem
    assert ces.DISTRO not in problem and "docker-desktop" not in problem, \
        "its own distro and Docker Desktop's are not another engine"


def test_nothing_in_the_way_is_quiet(machine, tmp_path):
    machine.running = ["Ubuntu"]
    assert ces.preflight(machine, wslconfig=tmp_path / "none") == ""


# ── the steps ───────────────────────────────────────────────────────────


def test_a_bare_machine_gets_every_step_in_order():
    found = ces.EngineCheck(False, "", ["wsl", "distro", "docker", "cli", "context", "task"])
    steps = ces.install_steps(found)
    assert [s.action for s in steps] == ["engine:wsl", "engine:distro", "engine:docker", "engine:cli",
                                         "engine:context", "engine:task", "engine:record"]
    assert {s.action for s in steps if s.admin} == {"engine:wsl", "engine:task"}
    assert {s.dep for s in steps} == {"docker"}


def test_a_complete_engine_is_only_checked_and_recorded():
    assert [s.action for s in ces.install_steps(ces.EngineCheck(True, "", []))] == ["engine:record"]


def test_a_stopped_docker_is_set_up_again_and_reconnected():
    steps = ces.install_steps(ces.EngineCheck(False, "", ["docker"]))
    assert [s.action for s in steps] == ["engine:docker", "engine:context", "engine:record"]


# ── downloads ───────────────────────────────────────────────────────────


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _download(data: bytes, *, size: int | None = None) -> ces.Download:
    return ces.Download("https://example.invalid/x", len(data) if size is None else size,
                        hashlib.sha256(data).hexdigest(), "x.bin")


def test_a_download_that_matches_is_kept(tmp_path):
    tmp_path = tmp_path / "downloads"
    data = b"pinned bytes" * 1000
    path = ces._fetch(_download(data), tmp_path, opener=lambda url, timeout: _Response(data))
    assert path.read_bytes() == data
    # A second fetch reads nothing: the file is checked and kept.
    again = ces._fetch(_download(data), tmp_path, opener=lambda url, timeout: pytest.fail("downloaded again"))
    assert again == path


def test_a_download_that_does_not_match_keeps_nothing(tmp_path):
    tmp_path = tmp_path / "downloads"
    item = _download(b"what was pinned")
    with pytest.raises(ValueError, match="did not match"):
        ces._fetch(item, tmp_path, opener=lambda url, timeout: _Response(b"something else entirely"))
    assert list(tmp_path.iterdir()) == []


def test_a_download_longer_than_pinned_keeps_nothing(tmp_path):
    tmp_path = tmp_path / "downloads"
    data = b"x" * 10
    item = _download(data)
    with pytest.raises(ValueError):
        ces._fetch(item, tmp_path, opener=lambda url, timeout: _Response(data + b"more"))
    assert list(tmp_path.iterdir()) == []


def test_every_download_is_https_and_pinned():
    for item in ces.DOWNLOADS.values():
        assert item.url.startswith("https://")
        assert len(item.sha256) == 64 and int(item.sha256, 16) >= 0
        assert "/latest/" not in item.url and "current" not in item.url, "a pinned URL never moves"


# ── the engine itself ───────────────────────────────────────────────────


def test_the_engine_script_reaches_the_distro_as_lf_text():
    script = ces._asset("setup-engine.sh")
    assert "\r" not in script, "bash refuses CRLF"
    assert script.startswith("#!/bin/bash") and "set -euo pipefail" in script
    assert "ENGINE_READY" in script


def test_the_engine_listens_on_this_machine_only_and_asks_for_a_client_certificate():
    script = ces._asset("setup-engine.sh")
    assert '"tcp://127.0.0.1:2376"' in script
    assert "0.0.0.0" not in script
    assert '"tlsverify": true' in script
    # Docker is never upgraded under running containers by an unattended upgrade.
    assert "apt-mark hold docker-ce" in script


def test_the_package_files_are_lf_on_disk():
    root = Path(ces.__file__).parent / "container_engine_assets"
    assert b"\r" not in (root / "setup-engine.sh").read_bytes()


def test_the_docker_step_pipes_the_script_and_needs_its_ready_line(machine):
    machine.answers[("wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "bash")] = (0, "ENGINE_READY Docker 29.9.3")
    machine.answers[("powershell",)] = (0, "192.168.1.0/24\n10.213.0.0/16\n")
    ok, detail = ces.run_action("engine:docker", run=machine)
    assert ok and "ENGINE_READY" in detail
    call = next(i for i, a in enumerate(machine.ran) if "bash" in a)
    assert machine.ran[call][-2:] == ["10.231.0.1/24", "10.231.128.0/17"], "a range no route of this machine uses"
    assert "ENGINE_READY" in (machine.stdin[call] or "")


def test_control_a_script_that_stops_short_is_a_failed_step(machine):
    machine.answers[("wsl.exe", "-d", ces.DISTRO, "-u", "root", "--", "bash")] = (0, "E: Unable to locate package")
    ok, detail = ces.run_action("engine:docker", run=machine)
    assert not ok and "failed" in detail


def test_the_boot_task_runs_with_nobody_signed_in_and_normal_rights(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    script = ces._task_script()
    assert "-LogonType S4U" in script, "S4U: it runs at boot with nobody signed in"
    assert "-RunLevel Limited" in script, "the engine needs no administrator rights"
    assert "-MultipleInstances IgnoreNew" in script and "-AtStartup" in script
    assert str(tmp_path / "Kazma" / "engine" / "keep-engine.ps1") in script


def test_the_record_makes_the_engine_kazmas_choice(machine, monkeypatch):
    from kazma_core.container_engine import RECORD_KEY, _recorded_engine
    from kazma_core.postgres_service import read_record

    ok, detail = ces.run_action("engine:record", run=machine)
    assert ok, detail
    entry = read_record()[RECORD_KEY]
    assert entry["kind"] == "kazma" and entry["docker"] == "29.9.3" and entry["task"] == ces.TASK_NAME
    assert _recorded_engine() == "kazma"
    assert ["setx", "COMPOSE_CONVERT_WINDOWS_PATHS", "1"] in machine.ran


def test_control_an_engine_that_never_answers_is_not_recorded(machine, monkeypatch):
    from kazma_core.container_engine import RECORD_KEY
    from kazma_core.postgres_service import read_record

    machine.engine_version = ""
    monkeypatch.setattr(ces.time, "sleep", lambda s: None)
    ok, detail = ces._action_record(machine, lambda m: None, None, wait_s=0)
    assert not ok and "did not answer" in detail
    assert RECORD_KEY not in read_record()


# ── kazma setup engine ──────────────────────────────────────────────────


def _engine_options(**kw):
    from types import SimpleNamespace

    return SimpleNamespace(**{"yes": True, "check": False, "dry_run": False, "remove": False, "force": False, **kw})


@pytest.fixture
def actions(monkeypatch):
    done: list[str] = []

    def act(action, **kw):
        done.append(action)
        return True, ""

    monkeypatch.setattr(ces, "run_action", act)
    monkeypatch.setattr(ces, "preflight", lambda run: "")
    return done


def _windows():
    from kazma_core.postgres_service import Platform

    return Platform("windows", "winget")


def test_setup_engine_puts_in_only_what_is_missing(machine, actions, capsys):
    from kazma_cli.setup import _setup_engine

    machine.task = False
    assert _setup_engine(_engine_options(), run=machine, platform=_windows(), admin=True) == 0
    assert actions == ["engine:task", "engine:record"]
    assert "NO   the KazmaEngine task" in capsys.readouterr().out


def test_setup_engine_check_changes_nothing(machine, actions):
    from kazma_cli.setup import _setup_engine

    machine.distros = ["Ubuntu"]
    assert _setup_engine(_engine_options(check=True), run=machine, platform=_windows()) == 1
    assert actions == []


def test_setup_engine_asks_for_an_administrator_before_anything(machine, actions, capsys):
    from kazma_cli.setup import _setup_engine

    machine.task = False
    assert _setup_engine(_engine_options(), run=machine, platform=_windows(), admin=False) == 2
    assert actions == [] and "elevated terminal" in capsys.readouterr().out


def test_setup_engine_stops_at_the_wsl_restart(machine, monkeypatch, capsys):
    from kazma_cli.setup import _setup_engine

    machine.wsl = False
    done: list[str] = []
    monkeypatch.setattr(ces, "run_action", lambda action, **kw: (done.append(action), (True, ces.RESTART))[1])
    monkeypatch.setattr(ces, "preflight", lambda run: "")
    assert _setup_engine(_engine_options(), run=machine, platform=_windows(), admin=True) == 1
    assert done == ["engine:wsl"]
    assert "Restart Windows" in capsys.readouterr().out


def test_setup_engine_says_what_stops_it(machine, monkeypatch, actions, capsys):
    from kazma_cli.setup import _setup_engine

    monkeypatch.setattr(ces, "preflight", lambda run: "Docker already runs in the WSL distro 'Ubuntu'")
    assert _setup_engine(_engine_options(), run=machine, platform=_windows(), admin=True) == 2
    assert actions == [] and "'Ubuntu'" in capsys.readouterr().out


def test_setup_engine_elsewhere_is_the_systems_docker(monkeypatch):
    from kazma_cli import setup
    from kazma_core.postgres_service import Platform

    called = []
    monkeypatch.setattr(setup, "_setup_system", lambda options, **kw: called.append(kw["platform"]) or 0)
    assert setup._setup_engine(_engine_options(), platform=Platform("debian", "apt")) == 0
    assert called == [Platform("debian", "apt")]


def test_setup_engine_is_a_command():
    from kazma_cli import setup

    with pytest.raises(SystemExit) as exc:
        setup.run(["engine", "--help"])
    assert exc.value.code == 0


# ── removing ────────────────────────────────────────────────────────────


def test_removing_refuses_while_another_projects_containers_live_there(machine):
    machine.containers = ["kazma-searxng", "shipx-postgres"]
    ok, detail = ces.remove(machine)
    assert not ok and "shipx-postgres" in detail
    assert ces.DISTRO in machine.distros and machine.task, "nothing was removed"


def test_removing_refuses_when_the_engine_cannot_be_asked(machine):
    machine.engine_version = ""
    ok, detail = ces.remove(machine)
    assert not ok and "--force" in detail
    assert ces.DISTRO in machine.distros


def test_removing_takes_only_kazmas_pieces(machine, monkeypatch, tmp_path):
    import os

    from kazma_core.atomic_files import atomic_write_text
    from kazma_core.container_engine import RECORD_KEY
    from kazma_core.postgres_service import _record_path, read_record

    atomic_write_text(_record_path(), json.dumps({"version": 1, "postgres": {"x": 1}, RECORD_KEY: {"kind": "kazma"}}))
    plugins = Path(os.environ["DOCKER_CONFIG"]) / "cli-plugins"
    plugins.mkdir(parents=True)
    (plugins / "docker-compose.exe").write_bytes(b"the owner's own compose")
    ces.bin_dir().mkdir(parents=True)
    (ces.bin_dir() / "docker.exe").write_bytes(b"cli")
    ok, detail = ces.remove(machine)
    assert ok, detail
    assert ces.DISTRO not in machine.distros and not machine.task
    assert not ces.bin_dir().exists()
    assert (plugins / "docker-compose.exe").exists(), "a compose plugin setup did not put there stays"
    record = read_record()
    assert RECORD_KEY not in record and record["postgres"] == {"x": 1}
    assert not any(a[-2:] == ["use", "default"] for a in machine.ran), \
        "the owner's current context was not Kazma's, so it is left alone"
