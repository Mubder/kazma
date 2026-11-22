"""A shared space's project folder (Spaces S2-12, plan 4.11, N29).

A shared space of the host kind works in a folder the admin chooses when it
is made. Its people may write there, so it is never a folder of Kazma's own
and never another space's:

* an existing absolute local folder: not a network share or a device path
  (refused on the text before anything opens it);
* not inside Kazma's install folder, its data folder or the Kazma home, and
  not containing any of them (a folder holding Kazma holds its code and its
  data);
* not inside or containing the owner's machine workspace (the owner's
  space's workspace roots, ``KAZMA_WORKSPACE``);
* not inside or containing another space's root.

Compared by real path AND by identity (volume and file id,
``workspace.folder_identity``), so a second name for the same folder -- a
loopback share, a ``\\\\?\\`` path -- cannot pass.

Checked twice: when the space is made (:func:`problem`, a code the page
words) and every time the space's tools resolve the folder
(:func:`check_at_use`): a folder that became a junction or a link into
Kazma's data after the space was made is refused then, before anything
reads or writes through it.

Blocking (the workspace store, the file system): call it off the loop.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from kazma_core.spaces.scope import OWNER_SPACE_ID
from kazma_core.workspace import folder_identity

__all__ = ["PROBLEMS", "check_at_use", "problem"]

logger = logging.getLogger(__name__)

#: Every code :func:`problem` answers, in the order it asks; the People page
#: words each (``settings.people_err.folder_<code>``).
PROBLEMS = (
    "empty", "network", "relative", "missing", "not_a_folder", "unreachable",
    "kazma_folder", "machine_workspace", "another_space",
)


def _kazma_folders() -> list[Path]:
    """Kazma's install folder, its data folder and the Kazma home, as set."""
    from kazma_core.paths import data_dir, installed_project_root

    folders = [data_dir(create=False)]
    install = installed_project_root()
    if install is None:
        # A wheel install: the folder holding Kazma's packages.
        install = Path(__file__).resolve().parents[2]
    folders.append(install)
    home = (os.environ.get("KAZMA_USER_HOME") or "").strip()
    if home:
        folders.append(Path(home).expanduser())
    return folders


def _roots_of_spaces(space_id: str) -> tuple[list[Path], list[Path]]:
    """The owner's machine workspace and every other space's root, apart."""
    from kazma_core.stores import get_workspace_store

    owners: list[Path] = []
    others: list[Path] = []
    for space, root in get_workspace_store().roots_by_space():
        if not root or space == space_id:
            continue
        (owners if space == OWNER_SPACE_ID else others).append(Path(root))
    machine = (os.environ.get("KAZMA_WORKSPACE") or "").strip()
    if machine and space_id != OWNER_SPACE_ID:
        owners.append(Path(machine).expanduser())
    return owners, others


def _overlaps(folder: Path, other: Path) -> bool:
    """True when *folder* is *other*, inside it, or holds it."""
    return folder_identity.inside(folder, other) or folder_identity.inside(other, folder)


def _overlap(folder: Path, space_id: str) -> str | None:
    """Which kind of folder *folder* overlaps, as a :data:`PROBLEMS` code."""
    if any(_overlaps(folder, kazma) for kazma in _kazma_folders()):
        return "kazma_folder"
    owners, others = _roots_of_spaces(space_id)
    if any(_overlaps(folder, root) for root in owners):
        return "machine_workspace"
    if any(_overlaps(folder, root) for root in others):
        return "another_space"
    return None


def problem(path: str, *, space_id: str = "") -> str | None:
    """Why *path* cannot be a shared space's project folder, or None: a code
    of :data:`PROBLEMS`. *space_id*: the space it is for, whose own roots
    are no conflict ("" for a space being made)."""
    from kazma_core.workspace.remote_paths import remote_path_reason

    text = (path or "").strip()
    if not text:
        return "empty"
    if remote_path_reason(text):
        return "network"
    folder = Path(text).expanduser()
    if not folder.is_absolute():
        return "relative"
    try:
        if folder_identity.identity(folder) is None:
            return "missing"
    except folder_identity.UnreachableError:
        return "unreachable"
    if not folder.is_dir():
        return "not_a_folder"
    return _overlap(folder, space_id)


def check_at_use(root: Path, *, space_id: str) -> None:
    """Refuse the project folder *root* of *space_id* when it now leads into
    a folder of Kazma's, the owner's machine workspace or another space's
    (a junction or a link made after the space was): before anything reads
    or writes through it, with ``RefusedBeforeEffect``. A root that cannot be
    asked is refused too."""
    try:
        found = _overlap(root, space_id)
    except OSError:
        found = "unreachable"
    if found is None:
        return
    logger.warning("[spaces] the project folder of %s is refused at use: %s", space_id, found)
    # Imported here: the agent package's __init__ builds the graph.
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect

    raise RefusedBeforeEffect(
        "This space's project folder cannot be used: it now leads into a folder "
        "Kazma keeps apart. Ask the admin to choose the folder again."
    )
