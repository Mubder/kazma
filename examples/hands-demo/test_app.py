"""Fails until add() is patched. Not collected by repo pytest (examples/ is
outside testpaths)."""

from app import add


def test_add() -> None:
    assert add(2, 2) == 4
