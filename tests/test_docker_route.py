"""Kazma reaches its own engine with no Windows docker.exe (2026-11-26).

Windows' file check refused the docker CLI Kazma's engine installs (an
unsigned static build). Kazma's engine is Docker Engine inside the
``kazma-engine`` WSL distro, so ``docker_cli.docker_command`` runs the
distro's own docker through Microsoft's signed ``wsl.exe`` (``wsl.exe -d
kazma-engine -u root --exec docker ...``): a Linux program inside WSL is
outside Windows' file check. Paths on this machine are given as the distro
reads them, a variable a call takes by name is carried in ``WSLENV`` (never
on a command line), and ``--exec`` means no Linux shell reads an argument.
The Windows CLI stays the route for Docker Desktop, the operator's own
engine, ``KAZMA_DOCKER_ROUTE=cli`` and a path the distro cannot reach.

Measured on the owner's PC (medians; the Windows CLI first): docker version
145/145 ms, docker exec 281/152 ms, docker run --rm 699/425 ms, 50 MB out of
docker exec 551/316 ms, 20 MB into docker exec -i 334/198 ms, bytes intact.

The gate: every docker call goes through the one decision. A module that
looks the docker CLI up itself is found from the real source; the few that
manage the Windows CLI itself are declared with why.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core import docker_cli

REPO = Path(__file__).resolve().parents[1]
WSL = r"C:\Windows\System32\wsl.exe"
PREFIX = [WSL, "-d", "kazma-engine", "-u", "root", "--exec", "docker"]


@pytest.fixture
def kazma_engine(monkeypatch):
    """Windows, Kazma's engine in force, its distro registered, fixed drives."""
    monkeypatch.setattr(docker_cli, "_on_windows", lambda: True)
    monkeypatch.setattr(docker_cli, "_wsl_exe", lambda: WSL)
    monkeypatch.setattr(docker_cli, "wsl_distro_location", lambda name: (Path(r"C:\engine"), "ext4.vhdx"))
    monkeypatch.setattr(docker_cli, "_drive_is_fixed", lambda drive: drive.upper() != "Z:")
    monkeypatch.setattr(docker_cli, "_context_in_force", lambda: docker_cli.KAZMA_CONTEXT)
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: r"C:\Program Files\Docker\docker.exe")
    for name in ("KAZMA_DOCKER_BIN", "DOCKER_HOST", docker_cli.ROUTE_ENV, "WSLENV"):
        monkeypatch.delenv(name, raising=False)


def test_kazmas_engine_is_reached_through_wsl_with_paths_as_the_distro_reads_them(kazma_engine):
    cmd = docker_cli.docker_command([
        "run", "--rm", "-i", "-v", r"G:\work\data:/data:ro",
        "--mount", r"type=bind,src=G:\GitHubRepos\kazma,dst=/repo,ro",
        r"--mount=type=bind,source=C:\Users\me\x,dst=/x",
        "--env-file", r"G:\kazma\.env", r"--iidfile=C:\tmp\id",
        "-e", "PGPASSWORD", "alpine", "true"])
    assert cmd is not None and cmd.route == "wsl"
    assert cmd.argv[:7] == PREFIX, "the distro's docker, started with --exec: no shell reads the arguments"
    args = cmd.argv[7:]
    assert args[args.index("-v") + 1] == "/g/work/data:/data:ro"
    assert args[args.index("--mount") + 1] == "type=bind,src=/g/GitHubRepos/kazma,dst=/repo,ro"
    assert "--mount=type=bind,source=/c/Users/me/x,dst=/x" in args
    assert args[args.index("--env-file") + 1] == "/g/kazma/.env"
    assert "--iidfile=/c/tmp/id" in args
    assert "-i" in args, "stdin is passed through"
    assert cmd.env is not None and "PGPASSWORD/u" in cmd.env["WSLENV"].split(":")
    assert not any(a in ("sh", "bash", "-c") for a in cmd.argv[:8])


def test_a_variable_given_by_value_is_never_carried(kazma_engine):
    cmd = docker_cli.docker_command(["run", "-e", "A=1", "alpine"], env={"PATH": "x"})
    assert cmd.route == "wsl" and "WSLENV" not in (cmd.env or {})


def test_carry_env_adds_compose_names_and_keeps_what_is_there(kazma_engine):
    argv = [*PREFIX, "compose", "up"]
    env = docker_cli.carry_env(argv, {"WSLENV": "X/p", "KAZMA_PG_UPGRADING": "1"}, names=("KAZMA_PG_UPGRADING",))
    assert env["WSLENV"] == "X/p:KAZMA_PG_UPGRADING/u"
    assert docker_cli.carry_env([r"C:\docker.exe", "exec", "-e", "PGPASSWORD"], {"A": "1"}) == {"A": "1"}


def test_docker_args_start_finds_docker_after_either_route():
    assert docker_cli.docker_args_start([*PREFIX, "exec", "x"]) == 7
    assert docker_cli.docker_args_start([r"C:\x\docker.exe", "exec"]) == 1
    assert docker_cli.docker_args_start(["pg_dump"]) is None


# ── the controls: where the Windows CLI is the route ─────────────────────


def test_control_docker_desktop_keeps_the_windows_cli(kazma_engine):
    cmd = docker_cli.docker_command(["version"], context=docker_cli.DESKTOP_CONTEXT)
    assert cmd.route == "cli"
    assert cmd.argv == [r"C:\Program Files\Docker\docker.exe", "--context", "desktop-linux", "version"]


def test_control_the_operator_can_ask_for_the_windows_cli(kazma_engine, monkeypatch):
    monkeypatch.setenv(docker_cli.ROUTE_ENV, "cli")
    assert docker_cli.docker_command(["version"]).route == "cli"
    monkeypatch.delenv(docker_cli.ROUTE_ENV)
    monkeypatch.setenv("KAZMA_DOCKER_BIN", r"C:\mine\docker.exe")
    assert docker_cli.docker_command(["version"]).route == "cli"


def test_a_path_the_distro_cannot_reach_takes_the_windows_cli(kazma_engine):
    assert docker_cli.docker_command(["cp", r"\\server\share\f", "c:/f"]).route == "cli"
    assert docker_cli.docker_command(["cp", r"Z:\removable\f", "c:/f"]).route == "cli"


def test_elsewhere_than_windows_the_cli_route(monkeypatch):
    monkeypatch.setattr(docker_cli, "_on_windows", lambda: False)
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: "/usr/bin/docker")
    monkeypatch.delenv(docker_cli.ROUTE_ENV, raising=False)
    assert docker_cli.docker_command(["ps"]).argv == ["/usr/bin/docker", "ps"]


def test_no_route_at_all_is_none(kazma_engine, monkeypatch):
    monkeypatch.setattr(docker_cli, "wsl_distro_location", lambda name: None)
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: None)
    assert docker_cli.docker_command(["version"]) is None


def test_pg_bridge_puts_the_password_by_name_after_either_route(kazma_engine):
    from kazma_core.migration import pg_bridge

    parts = pg_bridge._DsnParts(host="h", port="5432", user="u", password="secret", dbname="d")
    argv = pg_bridge._with_docker_pgpassword([*PREFIX, "exec", "-i", "kazma-db", "pg_dump"], parts)
    assert argv[7:11] == ["exec", "-i", "-e", "PGPASSWORD"]
    assert "secret" not in " ".join(argv)
    env = docker_cli.carry_env(argv, {"PGPASSWORD": "secret"})
    assert env["PGPASSWORD"] == "secret" and "PGPASSWORD/u" in env["WSLENV"]


# ── the gate: one decision for every docker call ─────────────────────────

#: Modules that look the Windows docker CLI up themselves, and why.
DECLARED: dict[str, str] = {
    "kazma-core/kazma_core/docker_cli.py": "the one decision itself",
    "kazma-core/kazma_core/container_engine_setup.py":
        "creates the Windows CLI's own kazma-engine context and resets its current context on removal: "
        "configuration of that CLI, not a docker call",
    "kazma-core/kazma_core/windows_trust.py": "reports which Windows CLI Windows allows (kazma doctor)",
    "scripts/service/kazma_guard.py":
        "its Docker watch takes the route first (_docker_route_argv) and keeps the Windows CLI for "
        "Docker Desktop and another engine, with the refused-CLI handling (§71 F12)",
    "scripts/prepush.py": "docker_route goes through docker_command; docker_exe is its fallback",
}


def _lookups(sources: dict[str, str]) -> list[str]:
    """Where a module finds a docker CLI itself: ``find_docker_cli`` named,
    or ``which("docker")``."""
    hits = []
    for name, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Name) and node.id == "find_docker_cli":
                hits.append(f"{name}:{node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr == "find_docker_cli":
                hits.append(f"{name}:{node.lineno}")
            elif isinstance(node, ast.alias) and node.name == "find_docker_cli":
                hits.append(f"{name}:{getattr(node, 'lineno', 0)}")
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "which"
                  and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "docker"):
                hits.append(f"{name}:{node.lineno}")
    return hits


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "kazma-*/**/*.py", "kazma-*/*.py", "scripts/*.py",
                             "scripts/**/*.py", "serve.py"], cwd=REPO, capture_output=True, text=True,
                            check=True).stdout.split()
    return {n: (REPO / n).read_text(encoding="utf-8") for n in listed
            if "/tests/" not in f"/{n}" and (REPO / n).is_file()}


def test_every_docker_call_goes_through_the_one_route():
    sources = _product_sources()
    assert len(sources) > 500
    undeclared = sorted({hit for hit in _lookups(sources) if hit.split(":")[0] not in DECLARED})
    assert undeclared == [], ("a module looks the docker CLI up itself; build the call with "
                              "docker_cli.docker_command / docker_argv, or declare it with why")
    assert all(name in sources for name in DECLARED), "a declared module is gone: drop its line"


def test_control_a_module_with_its_own_lookup_is_found():
    planted = {
        "kazma-core/kazma_core/x.py": "from kazma_core.docker_cli import find_docker_cli\n"
                                      "subprocess.run([find_docker_cli(), 'ps'])\n",
        "kazma-core/kazma_core/y.py": "import shutil\nshutil.which('docker')\n",
    }
    assert sorted({h.split(":")[0] for h in _lookups(planted)}) == ["kazma-core/kazma_core/x.py",
                                                                    "kazma-core/kazma_core/y.py"]


# ── a docker exec is recognised by its route, never by argv[0] (review H1) ──


def test_a_dump_through_the_wsl_route_connects_inside_the_container():
    from kazma_core.migration import pg_bridge

    parts = pg_bridge._DsnParts(host="127.0.0.1", port="55433", user="u", password="pw", dbname="d")
    inside = pg_bridge._docker_adjusted_parts([*PREFIX, "exec", "-i", "kazma-db", "pg_dump"], parts)
    assert (inside.host, inside.port) == ("localhost", "5432")
    host = pg_bridge._docker_adjusted_parts([r"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe"], parts)
    assert (host.host, host.port) == ("127.0.0.1", "55433"), "control: a host pg_dump keeps the DSN's address"


def test_the_restore_drill_pipes_the_archive_through_the_wsl_route(tmp_path, monkeypatch):
    from kazma_core.backup import restore_drill

    dump = tmp_path / "x.dump"
    dump.write_bytes(b"PGDMP")
    seen = {}

    def run(cmd, **kw):
        seen.update(cmd=cmd, stdin=kw.get("stdin"), env=kw.get("env"))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(restore_drill.subprocess, "run", run)
    prefix = [*PREFIX, "exec", "-i", "-e", "PGPASSWORD", "kazma-db", "pg_restore"]
    restore_drill._run_pg_restore(prefix, dump, ["--list"], env={"PGPASSWORD": "pw"})
    assert str(dump) not in seen["cmd"] and seen["stdin"] is not None, "the archive goes over stdin"
    assert "PGPASSWORD/u" in seen["env"]["WSLENV"]
    restore_drill._run_pg_restore([r"C:\pg\pg_restore.exe"], dump, ["--list"])
    assert seen["cmd"][-1] == str(dump), "control: a host pg_restore reads the file"


def test_the_rehearsal_pipes_and_carries_through_the_wsl_route():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("_route_rehearsal", REPO / "scripts" / "restore_rehearsal.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    text = (REPO / "scripts" / "restore_rehearsal.py").read_text(encoding="utf-8")
    assert "is_docker_exec(prefix)" in text and text.count("carry_env(cmd, self.env)") == 2


def _argv0_checks(sources: dict[str, str]) -> list[str]:
    """Where code tells a docker call by ``"docker" in <x>[0]`` (also through
    ``.lower()`` / ``Path(...).name``): wrong on the wsl.exe route."""
    hits = []
    for name, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if (isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant)
                    and isinstance(node.left.value, str) and "docker" in node.left.value
                    and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops)
                    and any("[0]" in ast.unparse(c) for c in node.comparators)):
                hits.append(f"{name}:{node.lineno}")
    return hits


def test_no_docker_call_is_told_by_its_first_argument():
    sources = _product_sources()
    sources.update({n: (REPO / n).read_text(encoding="utf-8") for n in ("scripts/restore_rehearsal.py",)})
    assert _argv0_checks(sources) == [], "use docker_cli.is_docker_exec / docker_args_start"


def test_control_a_first_argument_check_is_found():
    planted = {"x.py": 'if prefix and "docker" in prefix[0].lower():\n    pass\n',
               "y.py": 'ok = "docker" not in Path(out[0]).name.lower()\n'}
    assert sorted(h.split(":")[0] for h in _argv0_checks(planted)) == ["x.py", "y.py"]


# ── the stored choice decides the first call too; no Windows context needed (M1, M4) ──


@pytest.fixture
def fresh_process(tmp_path, monkeypatch):
    """A process that has made no docker call yet: the CLI's current context
    is Docker Desktop's, the stored choice is Kazma's engine, its distro is
    registered."""
    import json

    from kazma_core import container_engine

    config = tmp_path / "docker"
    config.mkdir()
    (config / "config.json").write_text(json.dumps({"currentContext": "desktop-linux"}), encoding="utf-8")
    monkeypatch.setenv("DOCKER_CONFIG", str(config))
    for name in ("DOCKER_CONTEXT", "DOCKER_HOST", "KAZMA_DOCKER_BIN", docker_cli.ROUTE_ENV,
                 docker_cli.ENGINE_BY_CHOICE_ENV):
        # Registered first, so what apply_engine_choice writes is undone too.
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    monkeypatch.setattr(docker_cli, "_choice_applied", False)
    monkeypatch.setattr(docker_cli, "_engine_choice", "")
    monkeypatch.setattr(docker_cli, "_applied", None)
    monkeypatch.setattr(docker_cli, "_applied_said", "the docker CLI's current context")
    monkeypatch.setattr(docker_cli, "_on_windows", lambda: True)
    monkeypatch.setattr(docker_cli, "_wsl_exe", lambda: WSL)
    monkeypatch.setattr(docker_cli, "wsl_distro_location", lambda name: (Path(r"C:\engine"), "ext4.vhdx"))
    monkeypatch.setattr(docker_cli, "find_docker_cli", lambda: r"C:\Program Files\Docker\docker.exe")
    monkeypatch.setattr(container_engine, "apply_engine_setting", lambda: docker_cli.apply_engine_choice("kazma"))
    return monkeypatch


def test_the_first_call_of_a_process_follows_the_stored_choice(fresh_process):
    fresh_process.setattr(docker_cli, "_context_exists", lambda name: True)
    assert docker_cli.docker_command(["version"]).route == "wsl"


def test_control_without_the_choice_applied_first_the_first_call_took_the_cli(fresh_process):
    fresh_process.setattr(docker_cli, "_context_exists", lambda name: True)
    fresh_process.setattr(docker_cli, "_apply_stored_choice_once", lambda: None)
    assert docker_cli.docker_command(["version"]).route == "cli"


def test_kazmas_engine_needs_no_windows_context(fresh_process):
    """Only the Windows docker.exe creates the kazma-engine context; a PC
    where Windows refuses it (no Docker Desktop) still reaches the engine."""
    fresh_process.setattr(docker_cli, "_context_exists", lambda name: False)
    cmd = docker_cli.docker_command(["run", "-v", r"G:\w:/w", "alpine"])
    assert cmd.route == "wsl" and docker_cli.engine_in_force() == "kazma"
    assert docker_cli.engine_host_path(r"G:\w", windows=True) == "/g/w"


@pytest.mark.parametrize("forced, value", [("KAZMA_DOCKER_ROUTE", "cli"),
                                           ("KAZMA_DOCKER_BIN", r"C:\tools\docker.exe")])
def test_a_forced_windows_cli_is_said_and_named_as_what_is_used(fresh_process, caplog, forced, value):
    """Review L-a (2026-11-27): Kazma's engine chosen, no kazma-engine
    context, the Windows CLI forced: the line said wsl.exe and
    engine_in_force said Kazma's while the call took the CLI's current
    context."""
    import logging

    fresh_process.setattr(docker_cli, "_context_exists", lambda name: False)
    fresh_process.setenv(forced, value)
    caplog.set_level(logging.INFO, logger="kazma_core.docker_cli")
    assert docker_cli.docker_command(["version"]).route == "cli"
    assert docker_cli.engine_in_force() != "kazma"
    assert docker_cli.ENGINE_BY_CHOICE_ENV not in docker_cli.os.environ
    said = " ".join(r.getMessage() for r in caplog.records if "container engine" in r.getMessage())
    assert "wsl.exe" not in said and "current context" in said and forced in said, said


def test_control_without_the_forced_cli_the_wsl_route_and_kazmas_engine(fresh_process, caplog):
    import logging

    fresh_process.setattr(docker_cli, "_context_exists", lambda name: False)
    caplog.set_level(logging.INFO, logger="kazma_core.docker_cli")
    assert docker_cli.docker_command(["version"]).route == "wsl"
    assert docker_cli.engine_in_force() == "kazma"
    assert any("wsl.exe" in r.getMessage() for r in caplog.records)


def test_the_brokers_calls_keep_what_the_route_adds(monkeypatch):
    """Review L-b (2026-11-27): the broker ran Kazma's route with its own
    environment and lost WSL_UTF8 (wsl.exe's errors in its log as UTF-16)."""
    from kazma_core.documents import worker_broker

    monkeypatch.setenv("KAZMA_VAULT_KEY", "not-for-docker")
    monkeypatch.setattr(worker_broker, "_docker_module", lambda: SimpleNamespace(
        docker_command=lambda args: docker_cli.DockerCommand(
            [*PREFIX, *args], {"WSL_UTF8": "1", "KAZMA_VAULT_KEY": "x", "PATH": "p"}, "wsl")))
    argv, route = worker_broker._docker_call(worker_broker._ROUTE, ["ps"])
    assert argv == [*PREFIX, "ps"] and route == {"WSL_UTF8": "1"}
    broker = worker_broker._Broker.__new__(worker_broker._Broker)
    env = broker._env(route)
    assert env["WSL_UTF8"] == "1" and "KAZMA_VAULT_KEY" not in env
    # The control: the CLI route adds nothing.
    assert docker_cli.DockerCommand(["docker", "ps"], {"WSL_UTF8": "1"}, "cli").route_entries() == {}


def _loads_docker_cli_by_path(text: str) -> bool:
    return "spec_from_file_location" in text and '"docker_cli.py"' in text


#: Functions of a by-path caller that build a docker call and run nothing
#: with it, each with why.
_DECIDE_ONLY = {
    ("scripts/service/kazma_guard.py", "_docker_route_argv"):
        "returns the argv: Dependencies._run_docker runs it with _docker_route_entries(); "
        "the tick only asks whether the route is taken",
    ("kazma-core/kazma_core/documents/worker_broker.py", "_find_docker_cli"):
        "asks only whether Kazma's route reaches an engine; runs nothing",
}
#: What carries the route's entries into a call's environment.
_CARRIES = {"route_entries", "WSL_ROUTE_ENV", "_docker_route_entries"}


def _drops_the_route_env(sources: dict[str, str]) -> list[str]:
    """``file::function`` for each function of a file that loads
    docker_cli.py by path, builds a docker call (``docker_command`` /
    ``docker_argv``) and carries none of what the route adds: per call, not
    per file (review delta 3)."""
    found = []
    for name, text in sources.items():
        if not _loads_docker_cli_by_path(text):
            continue
        for fn in ast.walk(ast.parse(text)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or (name, fn.name) in _DECIDE_ONLY:
                continue
            own = [n for n in ast.walk(fn) if n is not fn and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            inner = {id(x) for f in own for x in ast.walk(f)}
            nodes = [n for n in ast.walk(fn) if id(n) not in inner]
            builds = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                         and n.func.attr in ("docker_command", "docker_argv") for n in nodes)
            carries = any((isinstance(n, ast.Attribute) and (n.attr in _CARRIES or n.attr == "env"))
                          or (isinstance(n, ast.Name) and n.id in _CARRIES) for n in nodes)
            if builds and not carries:
                found.append(f"{name}::{fn.name}")
    return sorted(found)


def test_every_by_path_caller_runs_the_route_with_its_entries():
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard",
                            "kazma-*/*.py", "kazma-*/**/*.py", "scripts/*.py", "scripts/**/*.py"],
                           cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    sources = {f: (REPO / f).read_text(encoding="utf-8", errors="replace") for f in files
               if "/tests/" not in f and (REPO / f).is_file()}
    loaders = [name for name, text in sources.items() if _loads_docker_cli_by_path(text)]
    assert {"scripts/service/kazma_guard.py", "scripts/prepush.py",
            "kazma-core/kazma_core/documents/worker_broker.py"} <= set(loaders), loaders
    assert _drops_the_route_env(sources) == []
    for path, function in _DECIDE_ONLY:
        assert f"def {function}(" in sources[path], f"{path}::{function} is declared but gone"


def test_control_a_by_path_caller_that_drops_the_entries_is_found():
    """One good call and one dropped call in the same file: the file names
    WSL_ROUTE_ENV, and the dropped call is still found."""
    planted = {"scripts/x.py": (
        "import importlib.util, os, subprocess\n"
        "spec = importlib.util.spec_from_file_location('m', root / \"docker_cli.py\")\n"
        "def good():\n"
        "    cmd = m.docker_command(['ps'])\n"
        "    subprocess.run(cmd.argv, env={**os.environ, **cmd.route_entries()})\n"
        "def dropped():\n"
        "    subprocess.run(m.docker_argv(['ps']))\n"
        "ROUTE = m.WSL_ROUTE_ENV\n")}
    assert _drops_the_route_env(planted) == ["scripts/x.py::dropped"]


def _by_path_copy(name: str):
    """docker_cli.py loaded by path, as the guard and its document broker load it."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(name, REPO / "kazma-core" / "kazma_core" / "docker_cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _no_windows_cli(monkeypatch, module) -> None:
    """A Windows PC with Kazma's engine registered and no docker.exe it may run."""
    monkeypatch.setattr(module, "_on_windows", lambda: True)
    monkeypatch.setattr(module, "_wsl_exe", lambda: WSL)
    monkeypatch.setattr(module, "wsl_distro_location", lambda name: (Path(r"C:\engine"), "ext4.vhdx"))
    monkeypatch.setattr(module, "_context_exists", lambda name: False)
    monkeypatch.setattr(module, "find_docker_cli", lambda: None)


def _broker_child(monkeypatch, *, exported: bool):
    """The guard applies the choice (a by-path copy), then its broker -- a
    second by-path copy that never applies it -- runs in the environment
    the guard gives it (``_BROKER_ENV_KEEP``)."""
    from tests.test_guard_document_broker import guard as guard_module

    guard_copy = _by_path_copy("_route_guard_docker_cli")
    _no_windows_cli(monkeypatch, guard_copy)
    assert guard_copy.apply_engine_choice("kazma") is None
    assert guard_copy.docker_command(["version"]).route == "wsl"
    child_env = {k: v for k, v in docker_cli.os.environ.items() if k.upper() in guard_module._BROKER_ENV_KEEP}
    if not exported:
        child_env.pop(docker_cli.ENGINE_BY_CHOICE_ENV, None)
    for name in list(docker_cli.os.environ):
        if name not in child_env:
            monkeypatch.delenv(name)
    broker_copy = _by_path_copy("_route_broker_docker_cli")
    _no_windows_cli(monkeypatch, broker_copy)
    return broker_copy.docker_command(["version"])


def test_a_by_path_copy_in_the_brokers_environment_reaches_kazmas_engine(fresh_process):
    """Review M-a (2026-11-27): the guard's broker loads its own copy and
    never applies the choice; with no Windows context it got no route (or
    Docker Desktop's engine). The guard exports the choice for it."""
    routed = _broker_child(fresh_process, exported=True)
    assert routed is not None and routed.route == "wsl"


def test_control_without_the_export_the_brokers_copy_misses_the_engine(fresh_process):
    routed = _broker_child(fresh_process, exported=False)
    assert routed is None or routed.route != "wsl"


def test_the_export_is_cleared_when_the_choice_is_desktop(fresh_process):
    copy = _by_path_copy("_route_choice_docker_cli")
    _no_windows_cli(fresh_process, copy)
    copy.apply_engine_choice("kazma")
    assert docker_cli.os.environ.get(docker_cli.ENGINE_BY_CHOICE_ENV) == "kazma"
    copy.apply_engine_choice("desktop")
    assert docker_cli.ENGINE_BY_CHOICE_ENV not in docker_cli.os.environ


def test_control_without_the_distro_the_cli_and_its_context(fresh_process):
    fresh_process.setattr(docker_cli, "_context_exists", lambda name: False)
    fresh_process.setattr(docker_cli, "wsl_distro_location", lambda name: None)
    assert docker_cli.docker_command(["version"]).route == "cli"
    assert docker_cli.engine_in_force() == "desktop"


# ── the lows: UTF-8, another engine, only docker's own paths (L2, L5, L6) ──


def test_wsl_route_speaks_utf8(kazma_engine):
    assert docker_cli.docker_command(["version"]).env["WSL_UTF8"] == "1"


def test_another_engine_named_is_never_sent_to_kazmas_distro(kazma_engine, monkeypatch):
    monkeypatch.setenv(docker_cli.ROUTE_ENV, "wsl")
    assert docker_cli.docker_command(["rm", "-f", "x"], context=docker_cli.DESKTOP_CONTEXT).route == "cli"
    assert docker_cli.docker_command(["ps"]).route == "wsl", "control: unnamed, wsl is honoured"


def test_a_programs_own_path_options_are_its_own(kazma_engine):
    """Review L-1 (2026-11-27): past the image or the container, -f, -v and
    --config are the program's, not docker's."""
    dump = docker_cli.docker_command(["exec", "-i", "-e", "PGPASSWORD", "kazma-db", "pg_dump", "-f", r"C:\out.dump"])
    assert dump.argv[-2:] == ["-f", r"C:\out.dump"]
    run = docker_cli.docker_command(["run", "--rm", "-it", "--name", "j", "-v", r"G:\w:/w", "--cap-drop=ALL",
                                     "alpine", "tool", "-v", r"C:\x", "--config", r"C:\c"]).argv[7:]
    assert run == ["run", "--rm", "-it", "--name", "j", "-v", "/g/w:/w", "--cap-drop=ALL",
                   "alpine", "tool", "-v", r"C:\x", "--config", r"C:\c"]
    # The control: docker's own options before the image are still translated.
    assert docker_cli.docker_command(["create", "--mount", r"type=bind,src=G:\w,dst=/w", "img"]).argv[-2] \
        == "type=bind,src=/g/w,dst=/w"


@pytest.mark.parametrize("before", [["-eX=1"], ["--rm", "--use-api-socket"], ["-it"], ["--name=j"],
                                    ["--some-future-flag"], ["-Z"],
                                    # Review L-c2: grouped flags ending in a value option.
                                    ["-itp", "80:80"], ["-ite", "FOO"], ["-dp", "8080:80"]])
def test_no_option_before_a_mount_stops_its_translation(kazma_engine, before):
    """Review L-c (2026-11-27): an attached short value or an unlisted
    boolean before -v made L-1's boundary stop at the mount's source; L-c2:
    so did grouped flags ending in a value option (-itp 80:80)."""
    argv = docker_cli.docker_command(["run", *before, "-v", r"G:\w:/w", "img"]).argv
    assert "/g/w:/w" in argv and r"G:\w:/w" not in argv, argv
    mount = docker_cli.docker_command(["run", *before, "--mount", r"type=bind,src=G:\w,dst=/w", "img"]).argv
    assert "type=bind,src=/g/w,dst=/w" in mount, mount


@pytest.mark.parametrize("args, start", [
    # dd51e621d's boundaries for these forms (the reference), kept.
    (["run", "-itp", "80:80", "-v", "x", "img", "p"], 5),
    (["run", "-ite", "FOO", "img", "p"], 3),
    (["run", "-dp", "8080:80", "img"], 3),
    (["exec", "-itu", "root", "c", "x"], 3),
    (["exec", "-iuroot", "c", "x"], 2),           # the value attached
    (["run", "-it", "img", "p"], 2),
    (["run", "-iZ", "img"], None),                # a character the grammar does not name
])
def test_a_short_word_is_read_one_character_at_a_time(args, start):
    assert docker_cli._program_start(args) == start


def test_control_docker_grammar_still_stops_at_the_image(kazma_engine):
    argv = docker_cli.docker_command(["run", "-eX=1", "--name", "j", "img", "tool", "-v", r"C:\x"]).argv
    assert argv[-2:] == ["-v", r"C:\x"], "the program's own -v is the caller's"
    # An option docker's grammar does not name: the whole call as before.
    assert docker_cli._program_start(["run", "--some-future-flag", "img"]) is None


def test_only_dockers_own_paths_are_translated(kazma_engine):
    cmd = docker_cli.docker_command(["exec", "c", "python", "-c", r"C:\x"])
    assert cmd.argv[-1] == r"C:\x", "the container program's own argument is the caller's"
    build = docker_cli.docker_command(["build", "-f", r"G:\k\Dockerfile", "-t", "kazma:1", r"G:\k"]).argv[7:]
    assert build == ["build", "-f", "/g/k/Dockerfile", "-t", "kazma:1", "/g/k"]
