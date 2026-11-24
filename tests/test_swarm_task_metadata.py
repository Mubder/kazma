"""Which keys of a swarm task's metadata a request may set (Spaces S1-15 review).

The review's witness: ``POST /api/swarm/dispatch`` took its ``metadata``
whole, and the engine kept a ``tenant_id`` or ``asker`` the task arrived with
(``setdefault``), so a member's request naming another space and person ran
the worker there, as them. Every key the swarm reads from a metadata mapping
is now declared in ``kazma_core.swarm.task_metadata``: one a request may set
(``REQUEST_KEYS``), or one only Kazma writes (``KAZMA_KEYS``), which the
route refuses.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from kazma_core.swarm.task_metadata import KAZMA_KEYS, REQUEST_KEYS, kazma_keys_in

_ROOT = Path(__file__).resolve().parents[1]
#: Where the swarm reads a task's metadata: the package, the panel's routes,
#: the task stream and the run supervisor's swarm adapter.
_READERS = (
    "kazma-core/kazma_core/swarm",
    "kazma-ui/kazma_ui/swarm_panel",
    "kazma-ui/kazma_ui/swarm_sse.py",
    "kazma-core/kazma_core/runs/adapters/swarm.py",
)
#: Names a metadata mapping is bound to in those modules.
_NAMES = {"meta", "md", "metadata", "task_meta", "task_metadata", "new_metadata"}


def _is_metadata(node: ast.AST) -> bool:
    return "metadata" in ast.unparse(node) or (isinstance(node, ast.Name) and node.id in _NAMES)


def _literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _key_read(node: ast.AST) -> str | None:
    """The key *node* reads from a metadata mapping, else None: ``m.get(k)``,
    ``m.pop(k)``, ``m.setdefault(k)``, ``m[k]`` or ``k in m``."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr in {"get", "pop", "setdefault"} and node.args and _is_metadata(node.func.value):
        return _literal(node.args[0])
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) and _is_metadata(node.value):
        return _literal(node.slice)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)) \
            and _is_metadata(node.comparators[0]):
        return _literal(node.left)
    return None


def _keys_read(source: str) -> set[str]:
    return {key for node in ast.walk(ast.parse(source)) if (key := _key_read(node))}


def _reader_sources() -> dict[str, str]:
    found: dict[str, str] = {}
    for where in _READERS:
        path = _ROOT / where
        for file in [path] if path.is_file() else sorted(path.rglob("*.py")):
            found[file.relative_to(_ROOT).as_posix()] = file.read_text(encoding="utf-8")
    return found


def _undeclared(sources: dict[str, str]) -> dict[str, set[str]]:
    declared = set(KAZMA_KEYS) | set(REQUEST_KEYS)
    return {name: keys for name, source in sources.items() if (keys := _keys_read(source) - declared)}


def test_every_metadata_key_the_swarm_reads_is_declared() -> None:
    sources = _reader_sources()
    assert len(sources) > 40, "the readers were not found"
    assert _undeclared(sources) == {}


def test_control_a_planted_read_of_an_undeclared_key_fails() -> None:
    planted = {"planted.py": 'def f(task):\n    return (task.metadata or {}).get("run_as_account")\n'}
    assert _undeclared(planted) == {"planted.py": {"run_as_account"}}
    assert _undeclared({"ok.py": 'def f(meta):\n    return meta.get("tenant_id")\n'}) == {}


def test_a_key_is_the_requests_or_kazmas_never_both() -> None:
    assert not set(KAZMA_KEYS) & set(REQUEST_KEYS)
    assert {"tenant_id", "asker", "source_delivery_target", "unrestricted", "outside_text",
            "commitment_scope"} <= set(KAZMA_KEYS)


def test_kazma_keys_in() -> None:
    assert kazma_keys_in({"routes": {}, "asker": "x", "tenant_id": "y"}) == ["asker", "tenant_id"]
    assert kazma_keys_in({"routes": {}}) == []
    assert kazma_keys_in(None) == [] and kazma_keys_in(["asker"]) == []


@pytest.fixture
def dispatch_client():
    """The Swarm router alone: the refusal comes before the engine is asked."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui.swarm_panel import create_swarm_router

    app = FastAPI()
    app.include_router(create_swarm_router(None))
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("key", sorted(KAZMA_KEYS))
def test_the_dispatch_route_refuses_every_key_kazma_sets(dispatch_client, key: str) -> None:
    refused = dispatch_client.post("/api/swarm/dispatch", json={
        "task": "write the report", "workers": ["probe"], "metadata": {key: "s0b0b0b0b0b0b"}})
    assert refused.status_code == 400, refused.text
    assert refused.json()["code"] == "metadata_set_by_kazma" and refused.json()["keys"] == [key]


def test_the_dispatch_route_takes_a_key_a_request_may_set(dispatch_client) -> None:
    """Not refused for its metadata: the answer is the engine's (none here)."""
    answer = dispatch_client.post("/api/swarm/dispatch", json={
        "task": "write the report", "workers": ["probe"], "metadata": {"checkpoint_timeout": 30}})
    assert answer.json().get("code") != "metadata_set_by_kazma", answer.text
