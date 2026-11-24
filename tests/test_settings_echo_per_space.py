"""An echo of every setting shows the install's and the bound space's only.

Settings' ``GET /api/settings``, the chat apps' ``/config show`` and
``/config export`` and the agent's ``config_read`` read every stored setting.
On an install that enforces Spaces they go through one view
(``kazma_core.settings_scope.echo_view`` / ``shown_in_echo``): settings only
-- never Kazma's own records (a chat's continue context, the Souls, the
per-space account lists) -- and a space's key only in that space, a person's
only to them.

A person's space holds a planted lesson, mail address, X handle and a chat's
partial answer; the owner's space an X handle of its own. The admin, in the
owner's space, reads every setting through the real route, ``/config
export`` and ``config_read``: none of the person's words, the owner's own
handle and the install's settings still there.

Control: the echo of before (every stored row, masked for secrets only)
shows each of the person's words to the admin.

Gate: every product read of every setting (``.get_all()``) is an echo that
goes through ``echo_view``, or is declared with why nobody is shown it.
"""

from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

ROOT = Path(__file__).resolve().parents[1]

LESSON = "plantedlesson" + uuid.uuid4().hex[:6]
ADDRESS = "plantedaddr" + uuid.uuid4().hex[:6]
HANDLE = "plantedhandle" + uuid.uuid4().hex[:6]
PARTIAL = "plantedpartial" + uuid.uuid4().hex[:6]
OWNER_HANDLE = "ownerhandle" + uuid.uuid4().hex[:6]
LANGUAGE_KEY = "agent.reply_hint_" + uuid.uuid4().hex[:6]
INSTALL_WORD = "installword" + uuid.uuid4().hex[:6]
MEMBER_WORDS = (LESSON, ADDRESS, HANDLE, PARTIAL)


@pytest.fixture
def client():
    """One app per test: the Settings router keeps the settings store it was
    built with, and each test's store is its own."""
    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    with TestClient(create_app(), client=("127.0.0.1", 40000), base_url="http://127.0.0.1:9090") as client:
        yield client


def _as_admin(monkeypatch) -> None:
    from kazma_ui import auth

    principal = {"source": "secret", "role": "admin", "user_id": "", "actor": "secret"}
    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    monkeypatch.setattr(auth, "admin_decision", lambda request: "ok")


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)
    person = people.create_person("echo-one", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    space = person["personal_space"]
    thread = "echo-" + uuid.uuid4().hex
    store = get_config_store()
    planted = {
        "self_improvement.space_evolution": {"spaces": {space: {"agents": {"main": {"soul": LESSON}}}}},
        "email.accounts_by_space": {space: [{"alias": "home", "address": f"{ADDRESS}@example.com"}]},
        f"tenant.{space}.connectors.x.handle": HANDLE,
        f"long_task.continue.{thread}": {"partial": PARTIAL},
        "connectors.x.handle": OWNER_HANDLE,
        LANGUAGE_KEY: INSTALL_WORD,
    }
    for key, value in planted.items():
        store.set(key, value)
    try:
        yield {"person": person, "space": space, "thread": thread, "keys": list(planted)}
    finally:
        for key in planted:
            store.delete(key)
        store.delete(identity.REGISTRY_KEY)


def _admin_get_all(client, monkeypatch) -> str:
    _as_admin(monkeypatch)
    answer = client.get("/api/settings", headers={"X-Kazma-Space": OWNER_SPACE_ID})
    assert answer.status_code == 200, answer.text[:300]
    return answer.text


def test_the_admins_settings_show_no_other_spaces_words(client, world, monkeypatch) -> None:
    text = _admin_get_all(client, monkeypatch)
    for word in MEMBER_WORDS:
        assert word not in text, word
    assert OWNER_HANDLE in text  # the owner's space's own setting
    assert INSTALL_WORD in text  # an install setting


def test_config_export_shows_no_other_spaces_words(world) -> None:
    from kazma_gateway.slash_commands import _config_export

    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        text = _config_export()
    for word in MEMBER_WORDS:
        assert word not in text, word
    assert OWNER_HANDLE in text and INSTALL_WORD in text


def test_config_export_with_no_space_bound_shows_the_install_alone(world) -> None:
    from kazma_gateway.slash_commands import _config_export

    text = _config_export()
    for word in (*MEMBER_WORDS, OWNER_HANDLE):
        assert word not in text, word
    assert INSTALL_WORD in text


def test_config_read_refuses_another_spaces_key(world) -> None:
    from kazma_core.agent.tool_registry import LocalToolRegistry

    config_read = LocalToolRegistry(include_builtins=True)._tools["config_read"].func
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        for key in world["keys"][:4]:
            out = json.loads(config_read(key))
            assert out["status"] == "not_here", (key, out)
            assert not any(w in json.dumps(out) for w in MEMBER_WORDS)
        assert json.loads(config_read("connectors.x.handle"))["value"] == OWNER_HANDLE
    with scope.bind_scope(world["space"], world["person"]["id"]):
        # The person's own space reads its own key, never the owner's.
        assert json.loads(config_read(f"tenant.{world['space']}.connectors.x.handle"))["value"] == HANDLE
        assert json.loads(config_read("connectors.x.handle"))["status"] == "not_here"


def test_not_enforced_the_echo_is_as_before(client, world, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    text = _admin_get_all(client, monkeypatch)
    for word in (LESSON, HANDLE, PARTIAL, OWNER_HANDLE, INSTALL_WORD):
        assert word in text, word


def test_control_the_echo_of_before_shows_the_persons_words(client, world, monkeypatch) -> None:
    from kazma_core import settings_scope

    monkeypatch.setattr(settings_scope, "echo_view", lambda grouped: grouped)
    text = _admin_get_all(client, monkeypatch)
    for word in MEMBER_WORDS:
        assert word in text, word


# ── Gate: every read of every setting is an echo through echo_view, or declared ──

#: (file, function) -> what reading every setting there shows, and to whom.
ECHOES = {
    ("kazma-ui/kazma_ui/settings.py", "_every_setting_shown_here"): "GET /api/settings",
    ("kazma-gateway/kazma_gateway/slash_commands.py", "_load_config"): "/config show and export",
}

NOT_ECHOED = {
    ("kazma-core/kazma_core/config_store.py", "move_plaintext_secrets"):
        "moves secrets into the vault; shows nothing",
    ("kazma-core/kazma_core/config_store.py", "export_yaml"):
        "the whole install's copy for a migration bundle and the local settings snapshot "
        "file; never shown to a person",
    ("kazma-core/kazma_core/config_store.py", "get_validated_config"):
        "builds the install's configuration object; shows nothing",
    ("kazma-core/kazma_core/model_registry_store.py", "load_providers_unresolved"):
        "reads the provider rows; shows nothing",
    ("kazma-core/kazma_core/settings_restore.py", "_stored_rows"):
        "the Settings backup: its own filter, _held_by_another_space (S1-28)",
    ("kazma-core/kazma_core/migration/exporter.py", "export_bundle"):
        "a migration bundle carries the whole install, every space with the identity store",
    ("kazma-core/kazma_core/safety/hitl_grants.py", "_grant_keys"):
        "finds a chat's grant keys; shows nothing",
    ("kazma-core/kazma_core/spaces/space_data.py", "_settings_keys"):
        "one space's count, export and erase (S1-27): its own rule picks the space's keys",
    ("kazma-core/kazma_core/spaces/space_data.py", "forget_chat_state"):
        "finds the state of one chat that ended, and deletes it (S2-12); shows nothing",
    ("kazma-core/kazma_core/spaces/space_move.py", "_space_settings"):
        "one space's bundle, sealed by its owner (S4-06): only that space's settings named in "
        "_MOVED_SETTINGS (_moved_setting: its tenant.<id>. keys) and its own entry of the mappings",
    ("kazma-ui/kazma_ui/providers.py", "_load_connector_config"):
        "one chat app's install connector settings, by its prefix",
    ("kazma-ui/kazma_ui/providers.py", "_purge"):
        "deletes one chat app's install connector settings, by its prefix",
}


def _get_all_calls(source: str) -> list[tuple[str, int]]:
    """``(function, line)`` of every ``.get_all()`` call in *source*."""
    found: list[tuple[str, int]] = []
    stack: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: Any) -> None:
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            if (isinstance(node.func, ast.Attribute) and node.func.attr == "get_all"
                    and not node.args and not node.keywords):
                found.append((stack[-1] if stack else "<module>", node.lineno))
            self.generic_visit(node)

    _Visitor().visit(ast.parse(source))
    return found


def _function_source(source: str, name: str) -> str:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    return ""


def _problems(files: dict[str, str]) -> list[str]:
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for rel, source in files.items():
        for function, line in _get_all_calls(source):
            site = (rel, function)
            seen.add(site)
            if site in ECHOES:
                if "echo_view(" not in _function_source(source, function):
                    problems.append(f"{rel}:{line} {function} echoes every setting without echo_view")
            elif site not in NOT_ECHOED:
                problems.append(f"{rel}:{line} {function} reads every setting and is not declared")
    return problems


def _product_files() -> dict[str, str]:
    files: dict[str, str] = {}
    for folder in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli", "kazma-tui", "kazma-skills"):
        for path in (ROOT / folder).rglob("*.py"):
            if "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "get_all()" in text:
                files[path.relative_to(ROOT).as_posix()] = text
    return files


def test_every_read_of_every_setting_is_an_echo_through_the_view_or_declared() -> None:
    files = _product_files()
    assert _problems(files) == []
    sites = {(rel, fn) for rel, text in files.items() for fn, _ in _get_all_calls(text)}
    stale = (set(ECHOES) | set(NOT_ECHOED)) - sites
    assert not stale, f"declared but no longer read there: {sorted(stale)}"


def test_gate_control_a_planted_echo_without_the_view_fails() -> None:
    planted = {
        "kazma-ui/kazma_ui/planted.py": (
            "def api_dump(store):\n"
            "    return store.get_all()\n"
        ),
        "kazma-ui/kazma_ui/settings.py": (
            "def _every_setting_shown_here(config_store):\n"
            "    return config_store.get_all()\n"
        ),
    }
    problems = _problems(planted)
    assert any("planted.py" in p and "not declared" in p for p in problems)
    assert any("without echo_view" in p for p in problems)
