"""A person's code runs only in a container, with the network only where Kazma
filters it (Spaces S4-05, S4-05b; ``kazma_core/spaces/member_code.py``).

* The rule (``code_rule``): once Spaces is enforced, anyone but the admin is
  held to a container, with the network only where the admin allowed it and
  Kazma can hold it to the public internet; the admin, and an install that
  does not enforce Spaces, keep the rules they had; work no person asked
  for, a container with no network.
* Per space, through the real ``python_exec`` with Docker missing and the
  lab switch that allows the host (``KAZMA_CODE_EXEC_ALLOW_LOCAL``): the
  admin in the owner's space runs on the machine as before; a person in
  their own space, and in a space they share, is refused. **Control: the
  local fallback** -- the same person under the rules of before ran on the
  machine itself and read a file of the host.
* **The network, filtered** (S4-05b, after the review of S4-05: the
  engine's default network reached this computer, the LAN and the router's
  admin page). ``network_refusal`` says why it cannot be given -- a public
  install, ``KAZMA_TRUST_LAN``, another engine, no filter put in -- and
  People refuses turning it on with that code; where Kazma filters it, the
  switch is taken and the person's jail joins ``kazma-egress`` (checked as
  setup's before every run) with the network modules unblocked; everyone
  else's runs with ``--network none``. A stored allowance is not used where
  Kazma cannot filter. E2B never runs a person's code. **Control: the
  engine's default network** -- no product code puts the jail on it.
* A chat-app turn runs as the person whose linked account sent it (Spaces
  stage 3): their code is held to a container there too (control: the
  owner's own linked account keeps the admin's rules).
* A real Docker run where the sandbox image is present: a snippet in the
  container reaches neither the network nor a host file; the same snippet
  through the local fallback reads the host file (the control). The
  filtered network's own real-engine test is
  ``tests/test_member_code_egress_engine.py``.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from kazma_core.spaces import identity, member_code, people, scope
from kazma_core.spaces.member_code import CODE_NETWORK_POLICY, CodeRule, code_rule
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_core.tools import code_exec

from tests._space_matrix import Matrix

A, B = Matrix.SPACE_A, Matrix.SPACE_B
CONTAINER = CodeRule(container_only=True, network=False)
AS_BEFORE = CodeRule(container_only=False, network=False)


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


def _stored_allowance(account: str) -> None:
    """What the first build of S4-05 stored when the admin allowed it."""
    def _change(reg: dict[str, Any]) -> None:
        reg["policies"].setdefault(account, {})[CODE_NETWORK_POLICY] = True

    identity.update_registry(_change)


# ── the rule ────────────────────────────────────────────────────────────


def test_the_rule_for_each_person(two_people) -> None:
    m = two_people
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert code_rule() == AS_BEFORE  # the admin: as before
    for space, person in ((A, m.account_a), (A, m.account_b), (B, m.account_b)):
        with scope.bind_scope(space, person):
            assert code_rule() == CONTAINER
    assert code_rule() == CONTAINER  # no person bound


def test_nothing_is_read_while_spaces_is_not_enforced(matrix, monkeypatch) -> None:
    def no_reading() -> Any:
        raise AssertionError("the identity store was read")

    monkeypatch.setattr(identity, "read_registry", no_reading)
    with scope.bind_scope(A, matrix.account_a):
        assert code_rule() == AS_BEFORE


# ── the network, where Kazma filters it (S4-05b) ────────────────────────


@pytest.fixture
def filtered(monkeypatch):
    """Kazma's own engine is the one in force and its filter is put in."""
    from kazma_core import container_engine_setup, docker_cli

    monkeypatch.delenv("KAZMA_CLOUD_PROFILE", raising=False)
    monkeypatch.delenv("KAZMA_TRUST_LAN", raising=False)
    monkeypatch.setattr(docker_cli, "engine_in_force", lambda: "kazma")
    monkeypatch.setattr(container_engine_setup, "egress_installed", lambda: True)
    monkeypatch.setattr(container_engine_setup, "egress_network_ok", lambda run=None: True)


def test_why_the_network_cannot_be_given_here(filtered, monkeypatch) -> None:
    from kazma_core import container_engine_setup, docker_cli

    assert member_code.network_refusal() == ""
    monkeypatch.setattr(container_engine_setup, "egress_installed", lambda: False)
    assert member_code.network_refusal() == "code_network_no_filter"
    for engine in ("desktop", "system", "other"):
        monkeypatch.setattr(docker_cli, "engine_in_force", lambda engine=engine: engine)
        assert member_code.network_refusal() == "code_network_engine", engine
    monkeypatch.setenv("KAZMA_TRUST_LAN", "1")
    assert member_code.network_refusal() == "code_network_trust_lan"
    monkeypatch.setenv("KAZMA_CLOUD_PROFILE", "1")
    assert member_code.network_refusal() == "code_network_cloud"
    assert set(member_code.NETWORK_REFUSALS) == {
        "code_network_cloud", "code_network_trust_lan", "code_network_engine", "code_network_no_filter"}


@pytest.mark.parametrize("refusal", member_code.NETWORK_REFUSALS)
def test_allowing_is_refused_where_kazma_cannot_filter(two_people, monkeypatch, refusal) -> None:
    m = two_people
    monkeypatch.setattr(member_code, "network_refusal", lambda: refusal)
    with pytest.raises(people.PeopleRefused) as refused:
        people.set_code_network(m.account_b, True, by=OWNER_ACCOUNT_ID)
    assert refused.value.code == refusal and refused.value.status == 409
    assert CODE_NETWORK_POLICY not in (identity.read_registry()["policies"].get(m.account_b) or {})
    with pytest.raises(people.PeopleRefused) as admin:
        people.set_code_network(OWNER_ACCOUNT_ID, False, by=OWNER_ACCOUNT_ID)
    assert admin.value.code == "admin_code_rules"
    with pytest.raises(people.PeopleRefused) as bad:
        people.set_code_network(m.account_b, "yes", by=OWNER_ACCOUNT_ID)
    assert bad.value.code == "bad_code_network"


def test_allowed_where_kazma_filters_it_and_off_takes_it_back(two_people, filtered) -> None:
    m = two_people
    people.set_code_network(m.account_b, True, by=OWNER_ACCOUNT_ID)
    assert identity.read_registry()["policies"][m.account_b][CODE_NETWORK_POLICY] is True
    listed = {p["id"]: p for p in people.list_people()}
    assert listed[m.account_b]["code_network"] is True and listed[OWNER_ACCOUNT_ID]["code_network"] is None
    with scope.bind_scope(A, m.account_b):
        assert code_rule() == CodeRule(container_only=True, network=True)
    with scope.bind_scope(A, m.account_a):
        assert code_rule() == CONTAINER  # not allowed for them
    people.set_code_network(m.account_b, False, by=OWNER_ACCOUNT_ID)
    assert CODE_NETWORK_POLICY not in (identity.read_registry()["policies"].get(m.account_b) or {})
    with scope.bind_scope(A, m.account_b):
        assert code_rule() == CONTAINER
    events = [e for e in identity.read_registry()["events"] if e.get("kind") == "code_network_set"]
    assert len(events) == 2 and events[-1].get("account") == m.account_b


def test_a_stored_allowance_is_not_used_where_kazma_cannot_filter(two_people, filtered, monkeypatch) -> None:
    from kazma_core import docker_cli

    m = two_people
    _stored_allowance(m.account_b)
    with scope.bind_scope(A, m.account_b):
        assert code_rule().network is True
    monkeypatch.setattr(docker_cli, "engine_in_force", lambda: "desktop")
    assert member_code.allowed_network(identity.read_registry(), m.account_b) is False
    with scope.bind_scope(A, m.account_b):
        assert code_rule() == CONTAINER
    listed = {p["id"]: p for p in people.list_people()}
    assert listed[m.account_b]["code_network"] is False


def test_an_identity_store_that_cannot_answer_gives_no_network(two_people, filtered, monkeypatch) -> None:
    m = two_people
    _stored_allowance(m.account_b)

    def away() -> Any:
        raise identity.IdentityUnavailable("the store is away")

    monkeypatch.setattr(identity, "read_registry", away)
    with scope.bind_scope(A, m.account_b):
        assert code_rule() == CONTAINER


def _people_client(m: Matrix):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth
    from kazma_ui.people_api import create_people_router

    app = FastAPI()
    app.include_router(create_people_router())
    patch = pytest.MonkeyPatch()
    patch.setattr(auth, "get_request_principal", lambda request: {"source": "secret", "actor": "owner", "role": "admin"})
    return patch, TestClient(app)


def test_the_route_allows_where_filtered_and_refuses_elsewhere(two_people, filtered, monkeypatch) -> None:
    from kazma_core import docker_cli

    m = two_people
    patch, client = _people_client(m)
    try:
        with client:
            listed = client.get("/api/people").json()
            on = client.put(f"/api/people/{m.account_b}/code-network", json={"allowed": True})
            bad = client.put(f"/api/people/{m.account_b}/code-network", json={"allowed": "on"})
            monkeypatch.setattr(docker_cli, "engine_in_force", lambda: "desktop")
            elsewhere = client.get("/api/people").json()
            refused = client.put(f"/api/people/{m.account_a}/code-network", json={"allowed": True})
            off = client.put(f"/api/people/{m.account_b}/code-network", json={"allowed": False})
    finally:
        patch.undo()
    assert listed["code_network_refusal"] == "" and elsewhere["code_network_refusal"] == "code_network_engine"
    assert on.status_code == 200, on.text
    assert bad.status_code == 400 and bad.json()["error"] == "bad_code_network", bad.text
    assert refused.status_code == 409 and refused.json()["error"] == "code_network_engine", refused.text
    assert off.status_code == 200, off.text
    assert CODE_NETWORK_POLICY not in (identity.read_registry()["policies"].get(m.account_b) or {})


def test_every_refusal_is_worded_in_both_languages() -> None:
    from kazma_ui.i18n.catalog.settings import TRANSLATIONS

    for code in member_code.NETWORK_REFUSALS:
        key = f"settings.people_err.{code}"
        assert TRANSLATIONS[key]["en"] and TRANSLATIONS[key]["ar"], key
    for key in ("settings.people_code_network_title", "settings.people_code_network_means",
                "settings.people_code_network_on", "settings.people_code_network_off"):
        assert TRANSLATIONS[key]["en"] and TRANSLATIONS[key]["ar"], key
    assert "public internet" in TRANSLATIONS["settings.people_code_network_means"]["en"]


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
    monkeypatch.setattr(member_code, "code_rule", lambda: AS_BEFORE)
    monkeypatch.setattr(code_exec, "_outside_the_owners_space", lambda: False)
    monkeypatch.setattr("kazma_core.spaces.files.in_sandboxed_space", lambda: False)
    out = _exec_as(A, m.account_b, _reads(_host_file(tmp_path)))
    assert "host 35" in out, out


# ── the jail and E2B ────────────────────────────────────────────────────


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


def test_the_jail_joins_the_filtered_network_only_for_an_allowed_person(two_people, filtered, monkeypatch) -> None:
    from kazma_core.container_engine_setup import EGRESS_NETWORK

    m = two_people
    _stored_allowance(m.account_b)
    args, script = _docker_args(monkeypatch, A, m.account_b)
    assert _network(args) == EGRESS_NETWORK and "'socket'" not in script.decode()
    with scope.bind_scope(A, m.account_b):
        assert code_exec.sandbox_refusal("import socket") is None
        assert "public internet only" in code_exec.jail_note_for_tool("python_exec")
    for space, person in ((A, m.account_a), (OWNER_SPACE_ID, OWNER_ACCOUNT_ID)):
        args, script = _docker_args(monkeypatch, space, person)
        assert _network(args) == "none", (space, person, args)
        assert "'socket'" in script.decode()  # the network modules stay blocked
    with scope.bind_scope(A, m.account_a):
        assert "socket" in (code_exec.sandbox_refusal("import socket") or "")
        assert "(no network)" in code_exec.jail_note_for_tool("python_exec")


def test_every_container_has_no_network_where_kazma_cannot_filter(two_people, monkeypatch) -> None:
    from kazma_core import docker_cli

    m = two_people
    _stored_allowance(m.account_b)
    monkeypatch.setattr(docker_cli, "engine_in_force", lambda: "desktop")
    for space, person in ((A, m.account_b), (A, m.account_a), (OWNER_SPACE_ID, OWNER_ACCOUNT_ID)):
        args, script = _docker_args(monkeypatch, space, person)
        assert _network(args) == "none", (space, person, args)
        assert "'socket'" in script.decode()


def test_a_network_that_is_not_setups_refuses_the_run(two_people, filtered, monkeypatch) -> None:
    """A ``kazma-egress`` network made any other way (or removed by a prune)
    would not be filtered: the run is refused, never put on another network."""
    from kazma_core import container_engine_setup

    m = two_people
    _stored_allowance(m.account_b)
    monkeypatch.setattr(container_engine_setup, "egress_network_ok", lambda run=None: False)
    spawned: list[Any] = []

    async def spawn(*args: Any, **kwargs: Any) -> Any:
        spawned.append(args)
        raise AssertionError("a container was started")

    monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", "1")
    code_exec.reset_docker_probe()
    monkeypatch.setattr(code_exec, "_docker_cli", lambda: "docker")
    monkeypatch.setattr(code_exec.asyncio, "create_subprocess_exec", spawn)
    out = _exec_as(A, m.account_b, "print('hi')")
    assert out.startswith("[Exit code: 1]") and "kazma setup engine" in out, out
    assert spawned == []


def test_e2b_never_runs_a_persons_code(two_people, monkeypatch) -> None:
    from kazma_core.safety import deployment_policy
    from kazma_core.sandbox import e2b

    m = two_people
    _stored_allowance(m.account_b)
    monkeypatch.setattr(e2b, "e2b_api_key", lambda: "install-e2b-key")
    monkeypatch.delenv("KAZMA_E2B", raising=False)
    monkeypatch.setattr(deployment_policy, "container_required", lambda: False)
    with scope.bind_scope(A, m.account_b):
        assert e2b.e2b_enabled() is False
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert e2b.e2b_enabled() is True  # the admin: as before


def test_no_product_code_puts_a_container_on_the_engines_default_network() -> None:
    """The control of S4-05's review: the first build ran the jail with
    ``--network bridge``, the engine's default network, which reached this
    computer, the LAN and cloud metadata. The jail names one network
    argument, and it is ``none`` or the filtered network."""
    source = Path(code_exec.__file__).read_text(encoding="utf-8")
    assert source.count('"--network"') == 1 and '"--network", joined,' in source
    assert 'joined = "none"' in source and "joined = EGRESS_NETWORK" in source
    assert '"--network", "bridge"' not in source and "'bridge' if network" not in source
    first_build = source.replace('"--network", joined,', '"--network", "bridge" if network else "none",')
    assert first_build.count('"--network"') == 1 and '"--network", joined,' not in first_build


# ── a chat-app turn runs as its linked person (stage 3) ─────────────────


class _Graph:
    """A graph that answers at once and records the code rule it ran under."""

    def __init__(self) -> None:
        self.values: dict = {}
        self.rules: list[CodeRule] = []

    async def aget_state(self, config: Any) -> Any:
        return SimpleNamespace(values=self.values, next=(), tasks=())

    async def ainvoke(self, state: Any, config: Any) -> Any:
        self.rules.append(code_rule())
        self.values = {**state, "messages": [*state["messages"], {"role": "assistant", "content": "ok"}]}
        return self.values


@pytest.fixture
def linked(monkeypatch):
    """Spaces on; a person, and the owner, each linked on Telegram."""
    from kazma_core.config_store import get_config_store
    from kazma_core.spaces import chat_links

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    person = people.create_person(f"dana-code-{uuid4().hex[:4]}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)

    def link(reg: dict[str, Any]) -> None:
        reg["links"].append({"platform": "telegram", "user_id": "5151", "account": person["id"]})
        reg["links"].append({"platform": "telegram", "user_id": "4242", "account": OWNER_ACCOUNT_ID})

    identity.update_registry(link)
    chat_links.refresh_links()
    yield person
    store.delete(identity.REGISTRY_KEY)
    chat_links._forget_links_for_tests()
    from kazma_core.security.vault import reset_posture_cache

    reset_posture_cache()


async def _chat_app_turn(user_id: str) -> _Graph:
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore
    from kazma_gateway.gateway import IncomingMessage

    graph = _Graph()
    manager = SimpleNamespace(send=AsyncMock(), adapters=[])
    handler = create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())
    await handler(IncomingMessage("telegram", f"telegram:{uuid4().hex}", "run my code",
                                  context_metadata={"chat_id": int(user_id), "user_id": user_id,
                                                    "chat_type": "private"}))
    return graph


async def test_a_chat_app_turn_holds_its_linked_persons_code_to_a_container(linked) -> None:
    graph = await _chat_app_turn("5151")
    assert graph.rules == [CONTAINER], graph.rules


async def test_control_the_owners_linked_account_keeps_the_admins_rules(linked) -> None:
    graph = await _chat_app_turn("4242")
    assert graph.rules == [AS_BEFORE], graph.rules


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
    """Skipped without docker and the sandbox image (never pulled here). The
    snippet may import ``socket`` here (the runner's blocklist is set aside):
    the container alone keeps the network away."""
    if not _docker_with_image():
        pytest.skip("docker or the sandbox image is not available")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = _host_file(outside)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(code_exec._fw, "_get_workspace", lambda: work)
    script = work.parent / "snippet.py"
    script.write_text(code_exec._SANDBOX_RUNNER.format(
        blocked=sorted(code_exec._BLOCKED_IMPORT_ROOTS - {"socket", "select", "selectors"}),
        blocked_calls=code_exec._BLOCKED_BUILTINS, source=_SNIPPET.format(path=str(secret))), encoding="utf-8")
    out = asyncio.run(code_exec._run_docker_jail(script, str(work.parent), 60))
    assert "network=none" in out and "net 0" in out and "host 0" in out, out

    # Control: the same snippet through the local fallback read the host file.
    local = asyncio.run(code_exec._run_local_subprocess(script, str(work.parent), 30))
    assert "host 35" in local, local
