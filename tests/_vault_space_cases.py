"""The matrix cases of Spaces S1-18: the vault tools (``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b``, through the product's own tools on a real vault of
the case's own. Each space stores a secret under the SAME name with its own
marker; the owner's space holds one stored before Spaces (a row with no
tenant) and one of its own. A's tool call never shows B's or the owner's
marker and never changes what they hold, and A reads and changes its own.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._space_matrix import Matrix, case

ITEM = "S1-18"
NAME = "shared_name"
OWNER_OLD = "owner-from-before-spaces-q3"
OWNER_OWN = "owner-own-secret-q3"


class World:
    def __init__(self, matrix: Matrix, vault: Any, marks: Any) -> None:
        self.matrix, self.vault, self.marks = matrix, vault, marks

    def as_who(self, who: str) -> Any:
        if who == "owner":
            return scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
        return {"a": self.matrix.as_a, "b": self.matrix.as_b}[who]()

    def tool(self, who: str, tool_name: str, /, **kwargs: Any) -> str:
        from kazma_core.agent.tool_refusal import RefusedBeforeEffect

        from tests._chat_space_cases import _registry

        tool = _registry().get_tool(tool_name)
        assert tool is not None, tool_name

        async def as_them() -> str:
            with self.as_who(who):
                try:
                    return str(await tool.func(**kwargs))
                except RefusedBeforeEffect as refused:
                    return str(refused)

        return asyncio.run(as_them())

    def held(self, who: str) -> dict[str, Any]:
        with self.as_who(who):
            return {
                "value": self.vault.retrieve(NAME),
                "old": self.vault.retrieve("old_name"),
                "names": sorted(row["name"] for row in self.vault.list_secrets()),
            }

    def others_unchanged(self) -> None:
        assert self.held("b") == {"value": self.marks.b, "old": None, "names": [NAME]}, self.held("b")
        owner = self.held("owner")
        assert owner == {"value": OWNER_OWN, "old": OWNER_OLD, "names": ["old_name", NAME]}, owner


@contextmanager
def world(matrix: Matrix) -> Iterator[World]:
    from kazma_core.security import vault as vault_mod

    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        patch.setenv("KAZMA_VAULT_KEY", "k" * 64)
        v = vault_mod.SecretVault(db_path=str(Path(tmp) / "vault.db"))
        patch.setattr(vault_mod, "get_vault", lambda: v)
        v.store("old_name", OWNER_OLD)  # before Spaces, nothing bound: no tenant
        patch.setattr(scope, "_enforced", True)
        marks = matrix.plant("secret")
        made = World(matrix, v, marks)
        try:
            with made.as_who("owner"):
                v.store(NAME, OWNER_OWN)
            with matrix.as_a():
                v.store(NAME, marks.a)
            with matrix.as_b():
                v.store(NAME, marks.b)
            yield made
        finally:
            v.close()


def _in_world(run: Callable[[World], None]) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


def _nothing_of_the_others(w: World, shown: str) -> None:
    assert w.marks.b not in shown and OWNER_OLD not in shown and OWNER_OWN not in shown, shown


@case("tool:vault_retrieve", ITEM)
@_in_world
def vault_retrieve(w: World) -> None:
    shown = w.tool("a", "vault_retrieve", name=NAME)
    assert w.marks.a in shown, shown
    _nothing_of_the_others(w, shown)
    # The owner's secret from before Spaces, and a name that carries B's space.
    _nothing_of_the_others(w, w.tool("a", "vault_retrieve", name="old_name"))
    _nothing_of_the_others(w, w.tool("a", "vault_retrieve", name=f"space:{Matrix.SPACE_B}:{NAME}"))
    w.others_unchanged()


@case("tool:vault_list", ITEM)
@_in_world
def vault_list(w: World) -> None:
    shown = w.tool("a", "vault_list")
    assert NAME in shown and "old_name" not in shown, shown
    assert Matrix.SPACE_B not in shown and '"global"' not in shown, shown
    w.others_unchanged()


@case("tool:vault_store", ITEM)
@_in_world
def vault_store(w: World) -> None:
    w.tool("a", "vault_store", name=NAME, value="a-new-value")
    w.tool("a", "vault_store", name="old_name", value="a-wrote-this")
    assert w.held("a")["value"] == "a-new-value"
    assert w.held("a")["old"] == "a-wrote-this"  # A's own row of that name
    w.others_unchanged()


@case("tool:vault_delete", ITEM)
@_in_world
def vault_delete(w: World) -> None:
    w.tool("a", "vault_delete", name="old_name")  # the owner's: not A's to delete
    w.tool("a", "vault_delete", name=NAME)
    assert w.held("a")["value"] is None
    w.others_unchanged()
