"""Host tools exist only in a host space (Spaces S1-20, plan 4.8 and 4.11).

Every registered tool is in one group (``spaces.tools``): a new tool with
none fails. Once Spaces is enforced a sandboxed space is offered no host
tool and runs none it calls anyway; the owner's space has the admin's tools
and, while the owner's Development switch is on, Development's; another host
space has none until it has its own folder (S1-34); work with no space bound
has none. Host routes, the sidebar's Workspace and IDE entries, the chat
apps' ``/ide`` and a clone's credentials follow the same rule.

Each rule is shown failing with it taken out: the plan's control is a policy
that only hides a host tool from the list.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from kazma_core.spaces import identity, scope
from kazma_core.spaces import tools as host_tools
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, bind_scope

from tests import _host_tools_space_cases as cases
from tests._space_matrix import Matrix


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


def _registry():
    from tests._chat_space_cases import _registry as registry

    return registry()


def _host_names() -> set[str]:
    return set(host_tools.DEVELOPMENT | host_tools.ADMIN)


def as_owner():
    return bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)


# ── every tool has a group ───────────────────────────────────────────────


def ungrouped(names: set[str]) -> list[str]:
    grouped = set().union(*host_tools.GROUPS.values())
    return sorted(names - grouped)


def test_every_registered_tool_is_in_a_group() -> None:
    names = set(_registry()._tools)
    assert len(names) > 150
    missing = ungrouped(names | {"handoff_to_worker"})
    assert not missing, (
        "These tools are in no group of kazma_core/spaces/tools.py: put each in "
        "DEVELOPMENT, ADMIN, SPACE or OUTSIDE (plan 4.8):\n  " + "\n  ".join(missing)
    )
    # A tool is in one group only.
    seen: dict[str, str] = {}
    for group, members in host_tools.GROUPS.items():
        for name in members:
            assert name not in seen, f"{name} is in {seen.get(name)} and {group}"
            seen[name] = group
    # No group names a tool that does not exist.
    stale = sorted(set(seen) - names - {"handoff_to_worker"})
    assert not stale, stale


def test_the_group_gate_finds_a_planted_tool() -> None:
    assert ungrouped({"memory_search", "planted_new_tool"}) == ["planted_new_tool"]
    # A tool in no group (an MCP server's) is a host tool, never guessed safe.
    assert host_tools._tool_group("mcp__fs__write_file") == "development"


# ── a sandboxed space ────────────────────────────────────────────────────


def test_a_sandboxed_space_is_offered_no_host_tool_and_runs_none(matrix, enforced) -> None:
    offered = cases.offered_to(matrix)
    assert offered and not (offered & _host_names()), sorted(offered & _host_names())
    assert {"memory_search", "web_search", "python_exec"} <= offered
    for name in sorted(_host_names()):  # the GitHub and vault tools too
        answer, entered = cases.call_through_the_registry(matrix, name)
        assert entered == [] and answer.get("refused"), (name, answer)


def test_control_a_policy_that_only_hides_the_tool_lets_its_call_run(matrix, enforced, monkeypatch) -> None:
    """The plan's control: the offer filtered, the call not checked."""
    first = host_tools._host_refusal
    monkeypatch.setattr(host_tools, "offered_here", lambda defs: [
        d for d in defs if first(d["function"]["name"]) is None])
    monkeypatch.setattr(host_tools, "_host_refusal", lambda *_a, **_k: None)
    # A read-tier host tool: no approval card stands in front of it.
    assert "get_system_stats" not in cases.offered_to(matrix)  # hidden ...
    _answer, entered = cases.call_through_the_registry(matrix, "get_system_stats")
    assert entered == ["get_system_stats"]  # ... and it ran
    # (With the rule in place the same call never enters the tool.)
    monkeypatch.setattr(host_tools, "_host_refusal", first)
    _answer, entered = cases.call_through_the_registry(matrix, "get_system_stats")
    assert entered == []


def test_a_sandboxed_spaces_swarm_worker_offers_and_runs_no_host_tool(matrix, enforced, monkeypatch) -> None:
    """A real in-process worker in A's space, its model scripted to call the
    shell anyway."""
    import kazma_core.agent.tool_registry as tool_registry
    from kazma_core.llm_provider import ToolCall
    from kazma_core.swarm.task import WorkerCapabilities
    from kazma_core.swarm.worker import InProcessWorker

    from tests.test_swarm_spaces import _reply, _scripted_model

    registry = _registry()
    entered: list[str] = []

    async def recorder(*_a: Any, **_k: Any) -> str:
        entered.append("shell_exec")
        return "ran on the machine"

    monkeypatch.setitem(registry._tools, "shell_exec",
                        replace(registry.get_tool("shell_exec"), func=recorder, is_async=True))
    monkeypatch.setattr(tool_registry, "get_tool_registry", lambda: registry)
    sent = _scripted_model(monkeypatch, [
        _reply(calls=[ToolCall(id="c1", name="shell_exec", arguments={"command": "whoami"})]),
        _reply("done"),
    ])
    from unittest.mock import AsyncMock

    import kazma_core.model_registry as model_registry

    provider = model_registry.get_model_registry().get_client()
    offered_tools: list[set[str]] = []
    real_chat = provider.chat.side_effect

    async def recording_chat(*args: Any, **kwargs: Any) -> Any:
        offered_tools.append({t["function"]["name"] for t in kwargs.get("tools") or []})
        return await real_chat(*args, **kwargs)

    provider.chat = AsyncMock(side_effect=recording_chat)
    worker = InProcessWorker(name="probe", role="tester", capabilities=WorkerCapabilities(
        role="tester", tools=["shell_exec", "memory_search"]))
    with matrix.as_a():
        asyncio.run(worker.dispatch("who runs this"))
    assert sent and offered_tools, "the worker never called its model"
    assert "shell_exec" not in offered_tools[0] and "memory_search" in offered_tools[0], offered_tools
    assert entered == [], "the worker ran the shell in a sandboxed space"


def test_an_mcp_servers_tool_is_refused_in_a_sandboxed_space(matrix, enforced) -> None:
    from unittest.mock import MagicMock

    from kazma_core.mcp.manager import UnifiedToolExecutor

    mcp = MagicMock()
    mcp.is_mcp_tool.return_value = True
    executor = UnifiedToolExecutor(local=None, mcp=mcp)
    with matrix.as_a():
        answer = asyncio.run(executor.execute("mcp__fs__read_file", {"path": "x"}))
    assert answer.get("refused") and "does not exist in this space" in answer["content"], answer
    mcp.get_server_for_tool.assert_not_called()


# ── the owner's space and the Development switch ─────────────────────────


def test_the_owners_space_has_every_host_tool_while_development_is_on(matrix, enforced) -> None:
    with as_owner():
        allowance = host_tools.host_allowance()
        assert allowance.development and allowance.admin
        assert all(host_tools._host_refusal(name) is None for name in _host_names())


def test_with_development_off_the_owner_keeps_the_admin_tools_only(matrix, enforced) -> None:
    identity._set_development(OWNER_ACCOUNT_ID, False)
    try:
        with as_owner():
            assert host_tools._host_refusal("shell_exec") is not None
            assert host_tools._host_refusal("git_push") is not None
            assert host_tools._host_refusal("config_read") is None  # admin tools stay
            assert host_tools._host_refusal("install_python_packages") is None
            assert host_tools._host_refusal("memory_search") is None
    finally:
        identity._set_development(OWNER_ACCOUNT_ID, True)
    # Control: today's "always" (Spaces not enforced) ignores the switch.
    identity._set_development(OWNER_ACCOUNT_ID, False)
    try:
        scope.set_enforced(False)
        with as_owner():
            assert host_tools._host_refusal("shell_exec") is None
    finally:
        scope.set_enforced(True)
        identity._set_development(OWNER_ACCOUNT_ID, True)


def test_a_member_holds_no_development_until_granted() -> None:
    assert identity.holds_development(OWNER_ACCOUNT_ID) is True
    assert identity.holds_development("no-such-account") is False


def test_a_second_host_space_has_no_host_tool_until_its_own_folder(matrix, enforced) -> None:
    identity.create_space("Dev", created_by=OWNER_ACCOUNT_ID, space_id="matrix-dev", kind="host")
    with bind_scope("matrix-dev", OWNER_ACCOUNT_ID):
        allowance = host_tools.host_allowance()
    assert not allowance.development and not allowance.admin
    assert "S1-34" in allowance.why_not


def test_work_with_no_space_bound_has_no_host_tool(matrix, enforced) -> None:
    assert not host_tools.host_allowance().development
    assert host_tools._host_refusal("shell_exec") is not None
    assert host_tools._host_refusal("memory_search") is None  # its own rules refuse it


def test_an_unreadable_identity_store_gives_no_host_tool(matrix, enforced, monkeypatch) -> None:
    def away(*_a: Any, **_k: Any) -> Any:
        raise identity.IdentityUnavailable("away")

    monkeypatch.setattr(identity, "read_registry", away)
    with as_owner():
        assert not host_tools.host_allowance().admin


def test_without_spaces_every_tool_is_offered_and_runs_as_before(matrix) -> None:
    assert not scope.spaces_enforced()
    with matrix.as_a():
        assert host_tools.host_allowance().development
        assert _host_names() <= cases.offered_to(matrix) | {
            name for name in _host_names() if name not in _registry()._tools}
    assert host_tools.development_here() is True


# ── routes, the sidebar, /ide, a clone ───────────────────────────────────


def test_the_owner_reaches_the_development_routes(matrix, enforced) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth

    reached: list[str] = []
    seen_here: list[bool] = []
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())

    @app.api_route("/{rest:path}", methods=["GET", "POST"])
    def handler(rest: str) -> dict[str, Any]:
        reached.append("/" + rest)
        seen_here.append(host_tools.development_here())
        return {}

    owner = {"username": "owner", "role": "admin", "actor": "secret", "source": "secret"}
    with pytest.MonkeyPatch.context() as patch:
        from kazma_ui import space_binding

        patch.setattr(auth, "get_request_principal", lambda request: owner)
        patch.setattr(space_binding, "account_of", lambda principal: OWNER_ACCOUNT_ID)
        with TestClient(app) as client:
            assert client.get("/api/workspace/files").status_code == 200
            assert client.get("/chat").status_code == 200
    assert reached == ["/api/workspace/files", "/chat"]
    assert seen_here[-1] is True


def test_a_members_page_hides_the_workspace_and_ide(matrix, enforced) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui import auth, space_binding

    seen_here: list[bool] = []
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())

    @app.get("/chat")
    def page() -> dict[str, Any]:
        seen_here.append(host_tools.development_here())
        return {}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(auth, "get_request_principal", lambda request: matrix.principal_a)
        with TestClient(app) as client:
            assert client.get("/chat", headers={space_binding.SPACE_HEADER: Matrix.SPACE_A}).status_code == 200
    assert seen_here == [False]


def test_the_sidebars_entries_are_inside_the_rule() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "templates"
    for name in ("components/sidebar.html", "base.html"):
        text = (root / name).read_text(encoding="utf-8")
        for href in ('href="/workspace"', 'href="/ide"'):
            if href not in text:
                continue
            before = text[: text.index(href)]
            rule = "{% if development_here is not defined or development_here() %}"
            assert before.rfind(rule) > before.rfind("{% endif %}"), (name, href)


def test_the_chat_apps_ide_command_is_refused_with_no_space(enforced, monkeypatch) -> None:
    from types import SimpleNamespace

    from kazma_gateway.agent_handler import commands

    said: list[str] = []

    async def reply(_msg: Any, _store: Any, _manager: Any, _thread: Any, text: str) -> None:
        said.append(text)

    monkeypatch.setattr(commands, "_send_model_reply", reply)
    msg = SimpleNamespace(text="/ide run whoami")
    assert asyncio.run(commands._try_ide_command(msg, None, None, "t-1")) is True
    assert said and "/ide is not available here" in said[0]


def test_a_clone_outside_the_owners_space_carries_no_machine_credential(matrix, enforced, monkeypatch) -> None:
    from kazma_core.security.git_auth import clone_env

    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/owners-agent.sock")
    monkeypatch.setenv("GIT_ASKPASS", "/usr/bin/owners-askpass")
    with as_owner():
        owners = clone_env("owner-token")
    assert owners.get("SSH_AUTH_SOCK") == "/tmp/owners-agent.sock"
    with matrix.as_a():
        theirs = clone_env("their-token")
    assert "SSH_AUTH_SOCK" not in theirs and "GIT_ASKPASS" not in theirs
    keys = {theirs[f"GIT_CONFIG_KEY_{i}"]: theirs[f"GIT_CONFIG_VALUE_{i}"]
            for i in range(int(theirs["GIT_CONFIG_COUNT"]))}
    assert keys.get("credential.helper") == ""  # every helper of the machine reset
    assert "AUTHORIZATION: Basic" in keys.get("http.https://github.com/.extraheader", "")
    assert theirs.get("GIT_TERMINAL_PROMPT") == "0"
    # Control: the environment as it was built before keeps the owner's agent.
    from kazma_core.security.child_env import tool_child_env
    from kazma_core.security.git_auth import clone_auth_env

    assert tool_child_env(clone_auth_env("their-token")).get("SSH_AUTH_SOCK") == "/tmp/owners-agent.sock"


def test_the_development_routes_are_the_plans() -> None:
    from kazma_ui.route_scopes import is_development_route

    for path in ("/workspace", "/ide", "/api/ide/files", "/api/workspace/files", "/api/workspaces",
                 "/api/git/status", "/api/bookmarks", "/api/github/repos/clone"):
        assert is_development_route(path), path
    for path in ("/api/github/repos", "/api/github/token", "/chat", "/api/gitx", "/api/memory"):
        assert not is_development_route(path), path


def test_control_without_the_route_rule_a_member_reaches_the_workspace(matrix, monkeypatch) -> None:
    import kazma_ui.route_scopes as route_scopes

    target = next(iter(t for t in cases_route_targets() if t.startswith("GET /api/workspace")))
    monkeypatch.setattr(route_scopes, "is_development_route", lambda *_a, **_k: False)
    with pytest.raises(AssertionError):
        _case_run(target)(matrix)


def cases_route_targets() -> list[str]:
    from tests._space_matrix import load_cases

    return sorted(t for t, c in load_cases().items() if c.item == "S1-20" and not t.startswith("tool:"))


def _case_run(target: str):
    from tests._space_matrix import load_cases

    return load_cases()[target].run
