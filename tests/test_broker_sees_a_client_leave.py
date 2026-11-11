"""The document broker sees a client leave, whatever the number of its socket.

A client that stops reading -- past its output cap, or cancelled -- closes the
connection, and the broker ends the job when the socket turns readable. It
asked ``select.select``, which on Linux refuses a descriptor numbered 1024 or
more (``ValueError: filedescriptor out of range in select()``): in a process
with that many files open the broker never saw the client leave, and the job
ran on to its deadline. Linux CI's test process held thousands of files and
``test_client_cap_closes_the_connection`` failed there (2026-11-10).
"""

from __future__ import annotations

import ast
import os
import select
import socket
from pathlib import Path

import pytest

from kazma_core.documents import worker_broker

ROOT = Path(__file__).resolve().parents[1]
_HIGH_FD = 1500

posix_only = pytest.mark.skipif(os.name == "nt", reason="Windows' select has no descriptor-number limit")


@pytest.fixture
def high_socket():
    """A connected socket numbered past select's limit, and its peer."""
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if hard != resource.RLIM_INFINITY and hard <= _HIGH_FD:
        pytest.skip(f"the descriptor limit ({hard}) is below {_HIGH_FD}")
    if soft <= _HIGH_FD:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_HIGH_FD + 1, hard))
    ours, peer = socket.socketpair()
    os.dup2(ours.fileno(), _HIGH_FD)
    ours.close()
    high = socket.socket(fileno=_HIGH_FD)
    try:
        yield high, peer
    finally:
        high.close()
        peer.close()
        if soft <= _HIGH_FD:
            resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


@posix_only
def test_a_client_that_left_is_seen_on_a_high_descriptor(high_socket) -> None:
    high, peer = high_socket
    assert high.fileno() >= 1024
    assert worker_broker._readable_now(high) is False
    peer.close()
    assert worker_broker._readable_now(high) is True


@posix_only
def test_control_select_refuses_that_descriptor(high_socket) -> None:
    """Negative control: what the broker asked before."""
    high, _ = high_socket
    with pytest.raises(ValueError):
        select.select([high], [], [], 0)


def test_a_client_that_left_is_seen() -> None:
    ours, peer = socket.socketpair()
    try:
        assert worker_broker._readable_now(ours) is False
        peer.close()
        assert worker_broker._readable_now(ours) is True
    finally:
        ours.close()


# ── the gate ───────────────────────────────────────────────────────────────

#: Calls of ``select.select`` product code may keep, with why.
DECLARED = {
    ("kazma-core/kazma_core/documents/worker_broker.py", "_readable_now"):
        "the Windows branch only: Windows has no poll, and its select no descriptor-number limit",
}


def _select_calls(path: Path, rel: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []

    def walk(node: ast.AST, func: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "select" and isinstance(child.func.value, ast.Name)
                    and child.func.value.id == "select"):
                found.append((rel, func))
            walk(child, name)

    walk(ast.parse(path.read_text(encoding="utf-8")), "<module>")
    return found


def _undeclared(root: Path) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    for package in sorted(root.glob("kazma-*/kazma_*")):
        for path in sorted(package.rglob("*.py")):
            calls += _select_calls(path, path.relative_to(root).as_posix())
    return [c for c in calls if c not in DECLARED]


def test_no_product_code_asks_select_select() -> None:
    assert _undeclared(ROOT) == []


def test_control_the_gate_finds_a_planted_select(tmp_path: Path) -> None:
    package = tmp_path / "kazma-core" / "kazma_core"
    package.mkdir(parents=True)
    (package / "planted.py").write_text(
        "import select\n\ndef gone(sock):\n    return select.select([sock], [], [], 0)[0]\n", encoding="utf-8")
    assert _undeclared(tmp_path) == [("kazma-core/kazma_core/planted.py", "gone")]
