"""A space's files are its own: no walk of them follows a link or a junction
out of the space's folder (Spaces S1-27, S4-06, S2-08).

``export_space`` and the S4-06 bundle walked the folder with ``rglob`` and
``is_file``, which follow a link to a file (and, on Python 3.11, enter a
Windows junction): a link inside a space's folder put the file it leads to
into the space's export. Nothing a member has today makes such a link; an
owner's copy of a folder can.
"""

from __future__ import annotations

import ast
import os
import sys
import zipfile
from pathlib import Path

import pytest
from kazma_core.spaces import files as space_files
from kazma_core.spaces import space_data, space_limits, space_move
from kazma_core.spaces.files import FILES, own_files, space_dir

from tests._space_world import A, plant_world

REPO = Path(__file__).resolve().parents[1]
SPACES = REPO / "kazma-core" / "kazma_core" / "spaces"
OUTSIDE = b"a file of this computer, outside every space"


def _junction(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _plant_links(root: Path, outside: Path) -> None:
    """A folder link and, where this account may make one, a file link, in
    *root*, each leading to *outside*."""
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "secret.txt").write_bytes(OUTSIDE)
    root.mkdir(parents=True, exist_ok=True)
    _junction(root / "elsewhere", outside)
    try:
        os.symlink(outside / "secret.txt", root / "linked.txt")
    except OSError:  # Windows without the right to make one: the junction is the case
        assert sys.platform == "win32"


@pytest.fixture
def world(monkeypatch, tmp_path):
    yield from plant_world(monkeypatch, tmp_path)


def _rglob_files(folder: Path, *, unreadable: list[Path] | None = None):
    """The walk as it was, entering what it leads to (a junction on 3.11)."""
    for base, _dirs, names in os.walk(folder, followlinks=True):
        for name in sorted(names):
            path = Path(base) / name
            if path.is_file():
                yield path


# ── what each walk leaves alone ─────────────────────────────────────────


def test_an_export_never_follows_a_link_out_of_the_space(world, tmp_path) -> None:
    before = space_data.count_space(A)["files"]
    _plant_links(space_dir(A) / FILES, tmp_path / "outside")
    target = tmp_path / "a.zip"
    space_data.export_space(A, target)
    with zipfile.ZipFile(target) as zipped:
        assert all(OUTSIDE not in zipped.read(name) for name in zipped.namelist()), "a linked file was exported"
    assert space_data.count_space(A)["files"] == before


def test_control_the_walk_as_it_was_exports_the_linked_file(world, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(space_files, "own_files", _rglob_files)
    with pytest.raises(AssertionError, match="a linked file was exported"):
        test_an_export_never_follows_a_link_out_of_the_space(world, tmp_path)


def _bundled(space: str) -> bytes:
    return b"".join(path.read_bytes() for _part, _where, path in space_move._moving_files(space))


def test_a_bundle_never_follows_a_link_out_of_the_space(world, tmp_path) -> None:
    _plant_links(space_dir(A) / FILES, tmp_path / "outside")
    assert OUTSIDE not in _bundled(A), "a linked file travels"


def test_control_the_walk_as_it_was_bundles_the_linked_file(world, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(space_files, "own_files", _rglob_files)
    with pytest.raises(AssertionError, match="a linked file travels"):
        test_a_bundle_never_follows_a_link_out_of_the_space(world, tmp_path)


def test_a_linked_file_is_no_file_of_the_spaces_size(world, tmp_path) -> None:
    before = space_limits._folder_bytes(space_dir(A))
    _plant_links(space_dir(A) / FILES, tmp_path / "outside")
    assert space_limits._folder_bytes(space_dir(A)) == before


def test_the_walk_finds_every_own_file_and_says_what_it_could_not_read(tmp_path, monkeypatch) -> None:
    root = tmp_path / "own"
    (root / "b" / "c").mkdir(parents=True)
    for rel in ("a.txt", "b/b.txt", "b/c/c.txt", "ملف.txt"):
        (root / rel).write_bytes(b"x")
    assert sorted(p.relative_to(root).as_posix() for p in own_files(root)) == [
        "a.txt", "b/b.txt", "b/c/c.txt", "ملف.txt"]
    real = os.scandir

    def refuses(path):
        if Path(path).name == "c":
            raise PermissionError("not yours")
        return real(path)

    monkeypatch.setattr(space_files.os, "scandir", refuses)
    with pytest.raises(PermissionError):
        list(own_files(root))
    missed: list[Path] = []
    assert len(list(own_files(root, unreadable=missed))) == 3
    assert [p.name for p in missed] == ["c"]


# ── the gate: one walk of a space's files ───────────────────────────────

_WALKS = frozenset({"rglob", "walk", "iterdir", "copytree"})


def _walks(text: str) -> list[int]:
    """The lines of *text* that walk a folder themselves."""
    return [node.lineno for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.Attribute) and node.attr in _WALKS
            and not (isinstance(node.value, ast.Name) and node.value.id == "ast")]


def test_every_walk_of_a_spaces_files_asks_own_files() -> None:
    found = {p.name: _walks(p.read_text(encoding="utf-8")) for p in sorted(SPACES.glob("*.py"))}
    assert found, "the spaces package was not found"
    assert {name: lines for name, lines in found.items() if lines} == {}


def test_control_a_walk_of_its_own_is_caught() -> None:
    assert _walks("for path in folder.rglob('*'):\n    pass\n") == [1]
    assert _walks("import os\nfor base in os.walk(folder):\n    pass\n") == [2]
