"""Every surface that refuses multi-user mode asks one rule (decision D1).

Kazma is one operator per install. Surfaces with no per-user separation yet
(the swarm; chat's and the agent's document access; the Knowledge Library and
Scheduled tasks as their audits close) refuse on an install with platform
users. Each used to ask ``multi_user_enabled()`` itself with its own failure
rule -- the swarm refused when the store could not answer, Documents only for
a store error. ``kazma_core.security.single_operator`` is now the one rule,
and a check that cannot answer refuses.
"""

from __future__ import annotations

import ast
import sqlite3
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HOME = "kazma-core/kazma_core/security/single_operator.py"


def test_one_operator_is_single_operator(monkeypatch) -> None:
    import kazma_core.security.platform_rbac as rbac
    from kazma_core.security.single_operator import is_single_operator, single_operator_refusal

    monkeypatch.delenv("KAZMA_MULTI_USER", raising=False)
    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [])
    assert is_single_operator() is True
    assert single_operator_refusal("off here") is None


def test_platform_users_or_the_switch_refuse(monkeypatch) -> None:
    import kazma_core.security.platform_rbac as rbac
    from kazma_core.security.single_operator import single_operator_refusal

    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [{"username": "a"}])
    assert single_operator_refusal("off here") == "off here"
    monkeypatch.setattr(rbac, "_load_users_from_store", lambda: [])
    monkeypatch.setenv("KAZMA_MULTI_USER", "1")
    assert single_operator_refusal("off here") == "off here"


def test_a_check_that_cannot_answer_refuses(monkeypatch) -> None:
    import kazma_core.security.platform_rbac as rbac
    from kazma_core.security.single_operator import is_single_operator

    def broken():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.delenv("KAZMA_MULTI_USER", raising=False)
    monkeypatch.setattr(rbac, "_load_users_from_store", broken)
    assert is_single_operator() is False


def test_documents_and_the_swarm_ask_the_shared_rule(monkeypatch) -> None:
    """Both answer from the one rule: a failed check refuses in both (the
    Documents copy caught only store errors)."""
    import kazma_core.security.platform_rbac as rbac
    from kazma_core.documents.principal import _single_operator
    from kazma_core.swarm.single_operator import MULTI_USER_REFUSAL, swarm_refusal

    def odd():
        raise RuntimeError("an unexpected failure")

    monkeypatch.delenv("KAZMA_MULTI_USER", raising=False)
    monkeypatch.setattr(rbac, "_load_users_from_store", odd)
    assert _single_operator() is False
    assert swarm_refusal() == MULTI_USER_REFUSAL


def _product_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    return [REPO / f for f in listed if f.startswith("kazma-") and "/tests/" not in f
            and "_tests/" not in f and (REPO / f).exists()]


def _refusal_modules_asking_directly(tree: ast.AST) -> bool:
    """A module defining a single-operator refusal text that decides it by
    calling ``multi_user_enabled`` itself."""
    defines = any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and "REFUSAL" in t.id for t in node.targets)
        and "single-operator" in ast.unparse(node.value)
        for node in ast.walk(tree))
    calls = any(
        isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "multi_user_enabled"
        for node in ast.walk(tree))
    return defines and calls


def test_no_surface_decides_its_refusal_itself() -> None:
    offenders = []
    for path in _product_files():
        rel = path.relative_to(REPO).as_posix()
        if rel == HOME:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        if _refusal_modules_asking_directly(tree):
            offenders.append(rel)
    assert offenders == [], (
        "a single-operator surface decides multi-user mode itself; ask "
        f"kazma_core.security.single_operator instead: {offenders}")


def test_the_gate_sees_a_surface_asking_itself() -> None:
    """Negative control: the swarm's copy as it was."""
    planted = ast.parse(
        "MULTI_USER_REFUSAL = ('The swarm works on a single-operator install.')\n"
        "def swarm_refusal():\n"
        "    from kazma_core.security.platform_rbac import multi_user_enabled\n"
        "    return MULTI_USER_REFUSAL if multi_user_enabled() else None\n")
    assert _refusal_modules_asking_directly(planted) is True


@pytest.mark.parametrize("module", ["kazma_core.documents.principal", "kazma_core.swarm.single_operator",
                                    "kazma_core.cron.single_operator",
                                    "kazma_core.stores.knowledge_lifecycle"])
def test_each_surface_imports_the_shared_rule(module: str) -> None:
    import importlib
    import inspect

    source = inspect.getsource(importlib.import_module(module))
    assert "kazma_core.security.single_operator" in source
