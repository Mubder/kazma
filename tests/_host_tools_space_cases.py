"""The matrix cases of Spaces S1-20: host tools only in a host space.

Every case runs with Spaces enforced, as person A in ``matrix-a``, a
sandboxed space. A host tool is neither offered to A's model nor run when
A's turn calls it anyway (through the product's registry, where every call
passes); a host route is refused by the real tenant middleware before any
handler runs. Nothing of the owner's machine is shown or changed: the tool's
own function is never entered.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S1-20"

#: The host tools whose cases this module writes (the rest of the host
#: groups were cased by their own items: S1-17's GitHub tools, S1-18's vault
#: tools; ``tests/test_host_tools_absent.py`` holds every host tool to the rule).
HOST_TOOLS = (
    "shell_exec", "computer_use", "request_path_access",
    "codebase_search", "codebase_status",
    "git_status", "git_log", "git_diff", "git_commit", "git_checkout",
    "git_merge", "git_pull", "git_push",
    "mcp_list_resources", "mcp_read_resource", "mcp_list_prompts",
    "mcp_get_prompt", "mcp_test_server",
    "config_read", "config_save",
    "list_agent_skills", "search_agent_skills", "activate_skill",
    "install_agent_skill", "uninstall_agent_skill",
    "install_python_packages", "install_npm_packages", "check_environment",
    "lint_code", "format_code", "run_unit_tests",
    "get_system_stats", "list_active_processes", "read_system_logs",
    "repository_metrics",
    "send_message", "dispatch_notification", "send_approval_request",
)


@contextmanager
def enforced() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        yield


def call_through_the_registry(matrix: Matrix, name: str) -> tuple[dict[str, Any], list[str]]:
    """A's turn calls *name* (with no arguments a schema would refuse first:
    the registry's own checks come in order). Returns the registry's answer
    and the record of the tool's own function being entered."""
    from tests._chat_space_cases import _registry

    registry = _registry()
    entered: list[str] = []
    tool = registry.get_tool(name)
    assert tool is not None, name

    async def recorder(*_a: Any, **_k: Any) -> str:
        entered.append(name)
        return "ran on the machine"

    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(registry._tools, name, replace(tool, func=recorder, is_async=True))
        with matrix.as_a():
            answer = asyncio.run(registry.execute(name, {}))
    return answer, entered


def offered_to(matrix: Matrix) -> set[str]:
    """The tool names A's model is offered (``skills.switches.offered``,
    the list every model call of a turn is given)."""
    from kazma_core.skills.switches import offered

    from tests._chat_space_cases import _registry

    with matrix.as_a():
        return {d["function"]["name"] for d in offered(_registry().get_tool_definitions())}


def _tool_case(name: str) -> None:
    def run(matrix: Matrix) -> None:
        with enforced():
            assert name not in offered_to(matrix), f"{name} was offered in a sandboxed space"
            answer, entered = call_through_the_registry(matrix, name)
        assert entered == [], f"{name} ran in a sandboxed space"
        assert answer.get("refused") and "does not exist in this space" in str(answer.get("content")), answer

    run.__name__ = f"host_tool_{name}"
    case(f"tool:{name}", ITEM)(run)


for _name in HOST_TOOLS:
    _tool_case(_name)


# ── routes ───────────────────────────────────────────────────────────────


@contextmanager
def requests_through_the_middleware(matrix: Matrix, who: str) -> Iterator[tuple[TestClient, list[str]]]:
    """A client signed in as *who*, through the real tenant middleware, in
    front of a handler that records the paths it was reached for."""
    principal = matrix.principal(who)
    reached: list[str] = []
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scope, "_enforced", True)
        patch.setattr(auth, "get_request_principal", lambda request: principal)
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())

        @app.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
        def handler(rest: str) -> dict[str, Any]:
            reached.append("/" + rest)
            return {"reached": True}

        with TestClient(app) as client:
            yield client, reached


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def _route_case(target: str) -> None:
    method, path = target.split(" ", 1)

    def run(matrix: Matrix) -> None:
        with requests_through_the_middleware(matrix, "a") as (client, reached):
            got = client.request(method, _concrete(path),
                                 headers={space_binding.SPACE_HEADER: Matrix.SPACE_A})
        assert got.status_code == 403 and got.json().get("code") == "host_only", (target, got.text[:200])
        assert reached == [], f"{target}: the handler ran in a sandboxed space"

    run.__name__ = f"host_route_{method}_{path}"
    case(target, ITEM)(run)


def register_route_cases(targets: Callable[[], list[str]]) -> None:
    for target in targets():
        if not target.startswith("WEBSOCKET "):
            _route_case(target)


def _development_route_targets() -> list[str]:
    """The built app's space routes the Development rules decide, as the
    matrix lists them (``tests/test_route_scopes.served_routes``)."""
    from kazma_ui.app import create_app
    from kazma_ui.route_scopes import is_development_route

    from kazma_core.security import vault as vault_module

    from tests._space_matrix import space_routes
    from tests.test_route_scopes import served_routes

    # This runs while pytest imports the file, before any test's guard: the
    # app's build decides the vault ("no key, no vault") and may set
    # variables, and a later test that sets a key would get that decision
    # (tests/test_order_independence.py's vault pair failed after this file).
    # So what the build changes is put back here.
    singleton = ("_vault", "_vault_init_attempted", "_vault_retry_at", "_vault_retry_wait")
    environment = dict(os.environ)
    vault_state = {name: getattr(vault_module, name) for name in singleton}
    try:
        routes = served_routes(create_app())
    finally:
        for key in [k for k in os.environ if k not in environment]:
            del os.environ[key]
        os.environ.update(environment)
        for name, value in vault_state.items():
            setattr(vault_module, name, value)
    out = []
    for target in space_routes(routes):
        method, path = target.split(" ", 1)
        if method != "WEBSOCKET" and is_development_route(path, method):
            out.append(target)
    return out


register_route_cases(_development_route_targets)
