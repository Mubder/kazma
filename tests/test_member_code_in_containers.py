"""A person's code runs only in a container, with no network unless the admin
allows it (Spaces S4-05, ``kazma_core/spaces/member_code.py``).

* The rule (``code_rule``): once Spaces is enforced, anyone but the admin is
  held to a container, the network off unless allowed; the admin, and an
  install that does not enforce Spaces, keep the rules they had; work no
  person asked for, and an identity store that cannot answer, a container
  with no network.
* Per space, through the real ``python_exec`` with Docker missing and the
  lab switch that allows the host (``KAZMA_CODE_EXEC_ALLOW_LOCAL``): the
  admin in the owner's space runs on the machine as before; a person in
  their own space, and in a space they share, is refused. **Control: the
  local fallback** -- the same person under the rules of before ran on the
  machine itself and read a file of the host.
* The network: the Docker jail runs a person's code with ``--network none``
  and the network modules blocked, unless the admin allowed it (the engine's
  default network, the modules importable); E2B, which always has the
  internet, never runs a person's code without it; the check before the
  card says the same. The admin's code is as before.
* A real Docker run where the sandbox image is present: a person's snippet
  in the container reaches neither the network nor a host file; the same
  snippet through the local fallback reads the host file (the control).
* People: the admin allows or forbids it per person (recorded); never for
  the admin; the listing says it; the route.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_core.spaces import identity, member_code, people, scope
from kazma_core.spaces.member_code import CodeRule, code_rule
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_core.tools import code_exec

from tests._space_matrix import Matrix

A, B = Matrix.SPACE_A, Matrix.SPACE_B


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def two_people(matrix, enforced):
    """Space A shared by its maker (person A) and person B."""
    identity.add_member(A, matrix.account_b)
    return matrix


def _allow_network(account: str, allowed: bool = True) -> None:
    people.set_code_network(account, allowed, by=OWNER_ACCOUNT_ID)


# ── the rule ────────────────────────────────────────────────────────────


def test_the_rule_for_each_person(two_people, monkeypatch) -> None:
    m = two_people
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert code_rule() == CodeRule(container_only=False, network=False)  # the admin: as before
    for space, person in ((A, m.account_a), (A, m.account_b), (B, m.account_b)):
        with scope.bind_scope(space, person):
            assert code_rule() == CodeRule(container_only=True, network=False)
    _allow_network(m.account_b)
    with scope.bind_scope(A, m.account_b):
        assert code_rule() == CodeRule(container_only=True, network=True)
    with scope.bind_scope(A, m.account_a):
        assert code_rule().network is False  # one person's allowance is theirs alone
    assert code_rule() == CodeRule(container_only=True, network=False)  # no person bound

    def away() -> Any:
        raise identity.IdentityUnavailable("away")

    monkeypatch.setattr(identity, "read_registry", away)
    with scope.bind_scope(A, m.account_b):
        assert code_rule() == CodeRule(container_only=True, network=False)


def test_nothing_is_read_while_spaces_is_not_enforced(matrix, monkeypatch) -> None:
    def no_reading() -> Any:
        raise AssertionError("the identity store was read")

    monkeypatch.setattr(identity, "read_registry", no_reading)
    with scope.bind_scope(A, matrix.account_a):
        assert code_rule() == CodeRule(container_only=False, network=False)


# ── per space, through the real python_exec ─────────────────────────────


@pytest.fixture
def no_docker_and_the_lab_switch(monkeypatch):
    """Docker missing, and the lab switch that lets code run on the host."""
    monkeypatch.setenv("KAZMA_CODE_EXEC_ALLOW_LOCAL", "1")
    monkeypatch.delenv("KAZMA_CODE_EXEC_DOCKER", raising=False)
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.delenv("KAZMA_E2B_API_KEY", raising=False)
    monkeypatch.setattr(code_exec, "_docker_cli", lambda: None)
    code_exec.reset_docker_probe()
    yield
    code_exec.reset_docker_probe()


def _host_file(tmp_path: Path) -> Path:
    secret = tmp_path / "host_only.txt"
    secret.write_text("a file of the machine Kazma runs on", encoding="utf-8")
    return secret


def _reads(path: Path) -> str:
    return f"print('host', len(open({str(path)!r}).read()))"


def _exec_as(space: str, person: str, code: str) -> str:
    async def run() -> str:
        with scope.bind_scope(space, person):
            return await code_exec.python_exec(code)

    return asyncio.run(run())


def test_the_admin_in_the_owners_space_runs_as_before(two_people, no_docker_and_the_lab_switch, tmp_path) -> None:
    out = _exec_as(OWNER_SPACE_ID, OWNER_ACCOUNT_ID, _reads(_host_file(tmp_path)))
    assert "host 35" in out, out


@pytest.mark.parametrize("where", ["own space", "shared space"])
def test_a_persons_code_never_runs_on_the_machine(two_people, no_docker_and_the_lab_switch, tmp_path,
                                                  monkeypatch, where) -> None:
    m = two_people
    ran: list[str] = []
    monkeypatch.setattr(code_exec, "_run_local_subprocess", lambda *a, **k: ran.append("host"))
    space = B if where == "own space" else A
    out = _exec_as(space, m.account_b, _reads(_host_file(tmp_path)))
    # Held to a container: the jail is tried, and with no Docker the run is
    # refused -- never the host.
    assert out.startswith("[Exit code: 1]") and "Docker jail" in out and "host 35" not in out, out
    assert ran == []


def test_control_the_local_fallback_ran_a_persons_code_on_the_machine(two_people, no_docker_and_the_lab_switch,
                                                                     tmp_path, monkeypatch) -> None:
    """The rules of before for a person (no container-only rule, and the
    space rule of S1-19/S1-34 off): the lab switch put their code on the
    machine, where it read a file of the host."""
    m = two_people
    monkeypatch.setattr(member_code, "code_rule", lambda: CodeRule(container_only=False, network=False))
    monkeypatch.setattr(code_exec, "_outside_the_owners_space", lambda: False)
    monkeypatch.setattr("kazma_core.spaces.files.in_sandboxed_space", lambda: False)
    out = _exec_as(A, m.account_b, _reads(_host_file(tmp_path)))
    assert "host 35" in out, out


# ── the network ─────────────────────────────────────────────────────────


def _docker_args(monkeypatch, space: str, person: str) -> tuple[list[str], bytes]:
    captured: dict[str, Any] = {}

    class _Proc:
        returncode = 0

        async def communicate(self, input: bytes) -> tuple[bytes, bytes]:
            captured["input"] = input
            return (b"ok\n", b"")

        def kill(self) -> None:
            pass

        async def wait(self) -> int:
            return 0

    async def spawn(*args: Any, **kwargs: Any) -> _Proc:
        captured["args"] = list(args)
        return _Proc()

    monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "1")
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.delenv("KAZMA_E2B_API_KEY", raising=False)
    code_exec.reset_docker_probe()
    monkeypatch.setattr(code_exec, "_docker_cli", lambda: "docker")
    monkeypatch.setattr(code_exec.asyncio, "create_subprocess_exec", spawn)
    out = _exec_as(space, person, "print('hi')")
    assert "[sandbox: docker" in out, out
    return captured["args"], captured["input"]


def _network(args: list[str]) -> str:
    return args[args.index("--network") + 1]


def test_a_persons_container_has_no_network_unless_allowed(two_people, monkeypatch) -> None:
    m = two_people
    args, script = _docker_args(monkeypatch, A, m.account_b)
    assert _network(args) == "none" and "'socket'" in script.decode()
    _allow_network(m.account_b)
    args, script = _docker_args(monkeypatch, A, m.account_b)
    assert _network(args) == "bridge"
    assert "'socket'" not in script.decode() and "'subprocess'" in script.decode()  # only the network modules
    args, _script = _docker_args(monkeypatch, OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
    assert _network(args) == "none"  # the admin's code: as before


def test_e2b_runs_a_persons_code_only_with_the_network(two_people, monkeypatch) -> None:
    from kazma_core.sandbox import e2b

    m = two_people
    monkeypatch.setattr(e2b, "e2b_api_key", lambda: "install-e2b-key")
    monkeypatch.delenv("KAZMA_E2B", raising=False)
    from kazma_core.safety import deployment_policy

    monkeypatch.setattr(deployment_policy, "container_required", lambda: False)
    with scope.bind_scope(A, m.account_b):
        assert e2b.e2b_enabled() is False
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert e2b.e2b_enabled() is True  # the admin: as before
    _allow_network(m.account_b)
    with scope.bind_scope(A, m.account_b):
        assert e2b.e2b_enabled() is True


def test_the_check_before_the_card_follows_the_network(two_people) -> None:
    m = two_people
    with scope.bind_scope(A, m.account_b):
        assert "socket" in (code_exec.sandbox_refusal("import socket") or "")
        assert "(no network)" in code_exec.jail_note_for_tool("python_exec")
    _allow_network(m.account_b)
    with scope.bind_scope(A, m.account_b):
        assert code_exec.sandbox_refusal("import socket") is None
        assert "network allowed by the admin" in code_exec.jail_note_for_tool("python_exec")
        assert "os" in (code_exec.sandbox_refusal("import os") or "")  # host escapes stay blocked
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert "socket" in (code_exec.sandbox_refusal("import socket") or "")


# ── a real Docker run ───────────────────────────────────────────────────


def _docker_with_image() -> bool:
    from kazma_core.docker_cli import find_docker_cli

    docker = find_docker_cli()
    if not docker:
        return False
    try:
        probe = subprocess.run(  # noqa: S603 - fixed argv, the docker CLI
            [docker, "image", "inspect", code_exec.DEFAULT_DOCKER_IMAGE],
            capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


_SNIPPET = (
    "import socket\n"
    "try:\n"
    "    socket.create_connection(('1.1.1.1', 53), timeout=3).close()\n"
    "    print('net reached')\n"
    "except OSError:\n"
    "    print('net 0')\n"
    "try:\n"
    "    print('host', len(open({path!r}).read()))\n"
    "except OSError:\n"
    "    print('host 0')\n"
)


@pytest.mark.real_docker
def test_a_real_container_reaches_neither_the_network_nor_the_host(tmp_path, monkeypatch) -> None:
    """Skipped without docker and the sandbox image (never pulled here)."""
    if not _docker_with_image():
        pytest.skip("docker or the sandbox image is not available")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = _host_file(outside)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(code_exec._fw, "_get_workspace", lambda: work)
    script = work.parent / "snippet.py"
    # Built as for a person the admin allowed the network, run as one who is
    # not: only the container keeps the network away.
    script.write_text(code_exec._build_sandbox_script(_SNIPPET.format(path=str(secret)), network=True),
                      encoding="utf-8")
    out = asyncio.run(code_exec._run_docker_jail(script, str(work.parent), 60, network=False))
    assert "network=none" in out and "net 0" in out and "host 0" in out, out

    # Control: the same snippet through the local fallback read the host file.
    local = asyncio.run(code_exec._run_local_subprocess(script, str(work.parent), 30))
    assert "host 35" in local, local


# ── People ──────────────────────────────────────────────────────────────


def test_the_admin_allows_it_per_person_recorded(two_people) -> None:
    m = two_people
    _allow_network(m.account_b)
    listed = {p["id"]: p for p in people.list_people()}
    assert listed[m.account_b]["code_network"] is True and listed[m.account_a]["code_network"] is False
    assert listed[OWNER_ACCOUNT_ID]["code_network"] is None
    events = [e for e in identity.read_registry()["events"] if e.get("kind") == "code_network_set"]
    assert events and events[-1].get("account") == m.account_b
    _allow_network(m.account_b, False)
    assert member_code.allowed_network(identity.read_registry(), m.account_b) is False
    with pytest.raises(people.PeopleRefused) as admin:
        people.set_code_network(OWNER_ACCOUNT_ID, True, by=OWNER_ACCOUNT_ID)
    assert admin.value.code == "admin_code_rules"
    with pytest.raises(people.PeopleRefused) as bad:
        people.set_code_network(m.account_b, "yes", by=OWNER_ACCOUNT_ID)
    assert bad.value.code == "bad_code_network"


def test_the_route_sets_it(two_people) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.people_api import create_people_router

    m = two_people
    app = FastAPI()
    app.include_router(create_people_router())
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "get_request_principal",
                      lambda request: {"source": "secret", "actor": "owner", "role": "admin"})
        with TestClient(app) as client:
            got = client.put(f"/api/people/{m.account_b}/code-network", json={"allowed": True})
            bad = client.put(f"/api/people/{m.account_b}/code-network", json={"allowed": "on"})
    assert got.status_code == 200, got.text
    assert bad.status_code == 400 and bad.json()["error"] == "bad_code_network", bad.text
    assert member_code.allowed_network(identity.read_registry(), m.account_b) is True
