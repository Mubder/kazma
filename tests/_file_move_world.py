"""The people, spaces and files of the file move's tests (Spaces S2-12,
piece 7), in an install of the test's own.

Reem and Omar are in the shared space Family, Omar as one of its owners; each
has a personal space. :class:`Files` moves a file as the route does (in the
asker's scope) and answers where each file is: :meth:`Files.where` looks in
every space's folder and in every holding folder.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from kazma_core.spaces import file_moves, identity, people, scope, shared_files
from kazma_core.spaces.files import FILES, moving_dir, space_dir
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_world import own_install

BIG = 20 * 1024 * 1024
PLAN = b"the trip plan"


class Killed(BaseException):
    """The process stopped here: no handler of the code under test runs."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Files:
    """The world, and what is in it."""

    def __init__(self) -> None:
        reem = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        omar = people.create_person("Omar", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        self.family = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)["id"]
        people.add_member(self.family, reem["id"], by=OWNER_ACCOUNT_ID)
        identity.add_member(self.family, omar["id"], role="owner")
        self.reem, self.omar = reem["id"], omar["id"]
        self.mine, self.omars = reem["personal_space"], omar["personal_space"]

    def root(self, space: str) -> Path:
        return space_dir(space) / FILES

    def put(self, space: str, rel: str, data: bytes = PLAN) -> Path:
        path = self.root(space) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def read(self, space: str, rel: str) -> bytes | None:
        path = self.root(space) / rel
        return path.read_bytes() if path.is_file() else None

    def move(self, rel: str, source: str, target: str, *, by: str | None = None, max_bytes: int = BIG) -> dict[str, Any]:
        who = by or self.reem
        with scope.bind_scope(source, who):
            return file_moves.move_file(rel, from_space=source, to_space=target, by=who, max_bytes=max_bytes)

    def refused(self, rel: str, source: str, target: str, **kwargs: Any) -> str:
        """The code the move is refused with."""
        try:
            self.move(rel, source, target, **kwargs)
        except file_moves.FileMoveRefused as exc:
            return exc.code
        return "moved"

    def share(self, rel: str, data: bytes = PLAN) -> None:
        self.put(self.mine, rel, data)
        self.move(rel, self.mine, self.family)

    def where(self, data: bytes = PLAN) -> list[str]:
        """Every place that holds *data*: ``own:<path>``, ``family:<path>``,
        ``held`` for a holding folder's claimed bytes."""
        names = {self.mine: "own", self.family: "family", self.omars: "omar"}
        found = []
        for space, name in names.items():
            root = self.root(space)
            found += [f"{name}:{p.relative_to(root).as_posix()}" for p in root.rglob("*")
                      if p.is_file() and p.read_bytes() == data]
            found += ["held" for p in moving_dir(space).glob("*/claimed") if p.read_bytes() == data]
        return sorted(found)

    def held(self) -> list[str]:
        """What is left in any holding folder."""
        return sorted(f"{space}/{p.name}" for space in (self.mine, self.family, self.omars)
                      for p in moving_dir(space).glob("*"))

    def maker(self, space: str, rel: str) -> tuple[str, str] | None:
        row = shared_files.maker_of(space, rel)
        return (row["maker"], row["sha256"]) if row else None

    def lines(self) -> list[tuple[str, str]]:
        return [(line["path"], line["by"]) for line in shared_files._taken_out_files(self.family)]


@contextmanager
def files_world(monkeypatch, folder: Path) -> Iterator[Files]:
    with own_install(monkeypatch, folder):
        monkeypatch.setattr(scope, "_enforced", True)
        yield Files()
        assert file_moves._live == {}, "a move is still under way"
