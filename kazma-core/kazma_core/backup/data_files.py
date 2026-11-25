"""The links a copy of the data folder meets (``workspace.real_paths.own_files``
walks it and follows none).

Directly in the data folder a link is an install's own layout -- a database, a
file or ``document-store`` on another disk -- and every copy takes what it
leads to. Anywhere below, a link is left out, and each one is named: in the
copy's manifest, in one WARNING and, when it leads to files that are nowhere in
the copy, in the gap alert. Nothing is left out in silence.

Standard library only, and *real_paths* can be handed in: ``scripts/
backup_kazma.py`` loads both by their path.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

__all__ = ["leads_out", "linked_files_at_top", "links_below", "named"]

#: A manifest names at most this many links (its count is always whole).
NAMED = 1000


def _links(real_paths: ModuleType | None) -> Any:
    if real_paths is not None:
        return real_paths
    from kazma_core.workspace import real_paths as own

    return own


def linked_files_at_top(root: Path, *, real_paths: ModuleType | None = None) -> list[Path]:
    """The links directly in *root* that are no folder: one that leads to
    nothing now (its disk is away) too, so that a copy says it could not take
    it. A linked folder there is the walk's (``linked_at_top``)."""
    paths = _links(real_paths)
    try:
        with os.scandir(root) as found:
            names = sorted(entry.path for entry in found)
    except OSError:
        return []
    return [Path(name) for name in names if paths.is_linked(name) and not os.path.isdir(name)]


def links_below(folders: Iterable[Path], *, top: Path | None = None, keep: Callable[[Path], bool] | None = None,
                real_paths: ModuleType | None = None) -> list[Path]:
    """The links in *folders* (the ones a walk read), which the walk left:
    not those directly in *top*, and not those whose name a copy leaves out
    anyway (*keep*)."""
    paths = _links(real_paths)
    left: list[Path] = []
    for folder in folders:
        if folder == top:
            continue
        try:
            with os.scandir(folder) as found:
                names = sorted(entry.path for entry in found)
        except OSError:
            continue  # gone since the walk read it
        left += [Path(name) for name in names if paths.is_linked(name) and (keep is None or keep(Path(name)))]
    return left


def leads_out(link: Path, root: Path, *, real_paths: ModuleType | None = None) -> bool:
    """Whether *link* leads to something that is there and not inside *root*:
    files no copy of *root* holds."""
    return os.path.exists(link) and not _links(real_paths).inside(link, root)


def named(paths: list[str], *, most: int = 20) -> str:
    """*paths* in one line."""
    more = f" (+{len(paths) - most} more)" if len(paths) > most else ""
    return ", ".join(paths[:most]) + more
