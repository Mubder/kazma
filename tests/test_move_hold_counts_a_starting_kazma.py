"""A move holds a Kazma its guard runs, not only one that answers (2026-11-21).

Live: the owner ran ``kazma setup data-folder`` seconds after
``kazma_guard.py --resume``. The guard's new child was booting (spawned
13:35:51, up at 13:36:58), its port quiet, and ``setup._MoveHold`` -- which
decided from the port alone -- paused nothing: the database moved under a
Kazma starting on it. Every move holds Kazma through that one class: the
database's move from a container, the Postgres upgrade, the engine's move
from Docker Desktop, the data-folder move and ``kazma migrate import``.

``setup._kazma_to_hold`` counts a live guard (its fresh heartbeat): its
pause holds a booting child and a backoff alike, as ``kazma update``'s hold
(``update._ServerHold``) always did. The port-only rule is the negative
control. A source gate holds every product call of
``update._is_server_running`` -- the port alone -- to a declared reason.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ("kazma-core", "kazma-cli", "kazma-gateway", "kazma-ui", "kazma-tui", "kazma-skills", "scripts")


def _holds() -> dict[str, Callable[[], object]]:
    """Each move's hold, built as its command builds it."""
    from kazma_cli import migrate, postgres_upgrade, setup

    return {
        "the database's move from a container": lambda: setup._MoveHold(),
        "the Postgres upgrade": lambda: setup._MoveHold(postgres_upgrade.UPGRADE_PAUSE_REASON,
                                                        what="its database"),
        "the engine's move": lambda: setup._MoveHold(reason=setup._ENGINE_MOVE_REASON,
                                                     what="its containers' data"),
        "the data-folder move": lambda: setup._MoveHold(reason=setup._DATA_FOLDER_REASON,
                                                        what="its database and engine"),
        "the migration import": migrate._import_hold,
    }


@pytest.fixture
def quiet_port(monkeypatch) -> int:
    """Kazma's port, with nothing listening on it."""
    from kazma_cli import update

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setattr(update, "_resolve_server_port", lambda: port)
    return port


def _guard_state(*, heartbeat_age: float = 1.0) -> None:
    """The guard's state as it is between a resume and its child's first
    answer: a fresh heartbeat, a child spawned seconds ago, nothing on the
    port yet (the conftest points ``KAZMA_GUARD_STATE`` at the test's folder)."""
    now = time.time()
    Path(os.environ["KAZMA_GUARD_STATE"]).write_text(json.dumps({
        "heartbeat": now - heartbeat_age, "guard_pid": os.getpid(), "child_pid": os.getpid(),
        "child_spawned": now - 5, "guard_features": ["pause_held", "data_folder_move"],
    }), encoding="utf-8")


def _asked(hold) -> list[str]:
    """The reasons the hold asks its guard to stop Kazma for (stood in for:
    the real one waits for the guard's own word)."""
    asked: list[str] = []

    def stop_for_maintenance(reason, **kw):
        asked.append(reason)
        return "held"

    hold.guard.stop_for_maintenance = stop_for_maintenance
    return asked


@pytest.mark.parametrize("which", list(_holds()))
def test_every_move_holds_a_kazma_its_guard_is_still_starting(which, quiet_port):
    _guard_state()
    hold = _holds()[which]()
    assert hold.guard is not None
    assert hold.running, "the guard's child boots: there is a Kazma to hold"
    assert hold.refusal() is None
    asked = _asked(hold)
    assert hold.stop() and hold.held and asked == [hold.reason]


@pytest.mark.parametrize("which", list(_holds()))
def test_control_the_port_alone_holds_nothing_while_the_child_boots(which, quiet_port, monkeypatch):
    """The rule live ran (2026-11-21): nothing answers yet, so nothing held."""
    from kazma_cli import setup, update

    monkeypatch.setattr(setup, "_kazma_to_hold",
                        lambda guard, reason: update._is_server_running(update._resolve_server_port()))
    _guard_state()
    hold = _holds()[which]()
    asked = _asked(hold)
    assert not hold.running and hold.stop() and not hold.held and asked == []


def test_a_guard_paused_for_something_else_starts_nothing_so_nothing_is_held(quiet_port):
    from kazma_cli import setup, update

    _guard_state()
    update._load_guard(update._find_git_root()).write_pause("an operator's diagnosis", 600)
    hold = setup._MoveHold(reason=setup._DATA_FOLDER_REASON)
    assert not hold.running and hold.refusal() is None


def test_a_guard_that_stopped_beating_runs_nothing(quiet_port):
    from kazma_cli import setup

    _guard_state(heartbeat_age=3600)
    assert not setup._MoveHold(reason=setup._DATA_FOLDER_REASON).running


def test_a_kazma_that_answers_with_no_guard_is_still_refused(quiet_port):
    from kazma_cli import setup

    with socket.socket() as server:
        server.bind(("127.0.0.1", quiet_port))
        server.listen()
        hold = setup._MoveHold(reason=setup._DATA_FOLDER_REASON)
    assert hold.running and "no guard supervises it" in (hold.refusal() or "")


# -- every port-only decision is declared ------------------------------------------------------------------------

#: Each product function that calls ``update._is_server_running`` -- the
#: port alone -- and why that never decides "nothing to hold" under a guard.
DECLARED = {
    ("kazma-cli/kazma_cli/setup.py", "_kazma_to_hold"):
        "the moves' rule: the port, or a live guard (this file's tests)",
    ("kazma-cli/kazma_cli/update.py", "_packages_in_use"):
        "a refusal backstop for the installers (refuses while the port answers); every update holds "
        "through _ServerHold first, which holds whenever a guard runs",
    ("kazma-cli/kazma_cli/update.py", "_ServerHold.preview"):
        "the port decides only with no live guard (plan != 'guard'); a live guard is always paused",
    ("kazma-cli/kazma_cli/update.py", "_ServerHold.stop_for_update"):
        "the port decides only with no live guard, or after the guard answered it is gone or paused",
}


def _port_only_callers(source: str) -> set[str]:
    """The functions (``Class.function``) of *source* that call ``_is_server_running``."""
    found: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []

        def _scope(self, node) -> None:
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802 - ast's own name
            self._scope(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 - ast's own name
            self._scope(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802 - ast's own name
            self._scope(node)

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name == "_is_server_running":
                found.add(".".join(self.stack) or "<module>")
            self.generic_visit(node)

    Visitor().visit(ast.parse(source))
    return found


def _product_callers() -> set[tuple[str, str]]:
    callers: set[tuple[str, str]] = set()
    for top in PRODUCT:
        for path in (ROOT / top).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if "/tests/" in f"/{rel}" or ".venv" in rel or "node_modules" in rel:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "_is_server_running" not in text:
                continue
            callers |= {(rel, where) for where in _port_only_callers(text)}
    return callers


def test_every_port_only_decision_is_declared():
    found = _product_callers()
    assert found - set(DECLARED) == set(), "a new port-only decision: hold through the guard, or declare it"
    assert set(DECLARED) - found == set(), "a declaration that matches no call"


def test_control_a_planted_port_only_decision_is_found():
    planted = "class Mover:\n    def plan(self):\n        return _is_server_running(9090)\n"
    assert _port_only_callers(planted) == {"Mover.plan"}
