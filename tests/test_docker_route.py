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
