"""The space folder: where a space's files live (Spaces S1-19).

A space's rows carry its id; its files live in one folder,
``<data dir>/spaces/<space id>/``:

``files/``        the space's own working folder: what its file tools read
                  and write, what ``python_exec`` mounts, where research
                  reports and downloads go
``attachments/``  what its people uploaded, and what Kazma shared into a chat
``images/``       generated images and browser screenshots
``documents/``    generated and converted documents
``exports/``      exports its people asked for

**The owner's space keeps the install's folders** (``<data dir>/attachments``
and the rest, and the workspace the install has always had): a mapping, not a
move, so nothing an install holds changes place (plan section 7, step 6).

**Nothing here acts unless Spaces is enforced.** On an install that does not
enforce it every function answers what the install answered before Spaces.

A *sandboxed* space (every space that is not a host space) has no other
place: its file tools cannot leave its folder (``workspace.path_policy``),
and nothing of the machine's workspace, its extra folders or a path grant
reaches it. A host space keeps the machine's workspace ladder
(``workspace.binding``); which folder a second host space works in is
S1-34's.

Every function reads the bound scope and, for a space's kind, the identity
store (a settings read): blocking, so async code calls them in a thread.
"""

from __future__ import annotations

import logging
from pathlib import Path

from kazma_core.spaces.scope import (
    OWNER_SPACE_ID,
    current_scope,
    require_space_id,
    spaces_enforced,
    valid_space_id,
)

__all__ = [
    "FOLDER_KINDS",
    "SpaceKindUnknown",
    "another_spaces_path",
    "in_sandboxed_space",
    "is_space_files_path",
    "sandboxed_files_root",
    "sandboxed_space",
    "space_dir",
    "space_folder",
]

logger = logging.getLogger(__name__)

#: The folders of a space, beside ``files``.
FOLDER_KINDS = ("attachments", "images", "documents", "exports")

#: The space's own working folder inside its space folder.
FILES = "files"


class SpaceKindUnknown(RuntimeError):
    """Whether the bound space is a host space could not be read (the
    identity store is away, or holds no such space). Never answered as
    "host": a space whose kind is unknown gets no path of the machine."""


def _spaces_root() -> Path:
    """``<data dir>/spaces``: one folder per space other than the owner's."""
    from kazma_core.paths import data_dir

    return data_dir() / "spaces"


def space_dir(space_id: str) -> Path:
    """The folder of *space_id* (not created). The id becomes a folder name,
    so it must be a space id and nothing else."""
    if not valid_space_id(space_id):
        raise ValueError(f"not a space id: {space_id!r}")
    return _spaces_root() / space_id


def _bound_space() -> str | None:
    """The space this work is in once Spaces is enforced; None while it is
    not enforced. ``NoSpaceBound`` enforced with none bound."""
    if not spaces_enforced():
        return None
    return require_space_id()


def _kind_of(space_id: str) -> str:
    """``host`` or ``sandboxed``, from the identity store."""
    if space_id == OWNER_SPACE_ID:
        return "host"  # the owner's space is the install's host space, always
    from kazma_core.spaces import identity

    try:
        spaces = identity.read_registry().get("spaces") or {}
    except (identity.IdentityUnavailable, identity.IdentityError) as exc:
        raise SpaceKindUnknown(f"the kind of space {space_id!r} could not be read") from exc
    space = spaces.get(space_id) if isinstance(spaces, dict) else None
    kind = (space or {}).get("kind") if isinstance(space, dict) else None
    if kind not in ("host", "sandboxed"):
        raise SpaceKindUnknown(f"space {space_id!r} is not a space of this install")
    return str(kind)


def sandboxed_space() -> str | None:
    """The id of the bound space when it is a sandboxed one, else None: None
    on an install that does not enforce Spaces, for a host space, and for
    work with no space bound (the install's own work keeps the machine's
    workspace: the MCP binding, the code index, health).

    Raises :class:`SpaceKindUnknown` when a space is bound and its kind
    cannot be read.
    """
    if not spaces_enforced():
        return None
    bound = current_scope()
    if bound is None:
        return None
    return bound.space_id if _kind_of(bound.space_id) == "sandboxed" else None


def in_sandboxed_space() -> bool:
    """True when this work runs in a sandboxed space (see
    :func:`sandboxed_space`)."""
    return sandboxed_space() is not None


def space_folder(kind: str, *, install: Path | None = None, create: bool = True) -> Path:
    """Where the bound space keeps its files of *kind* (``files`` or one of
    :data:`FOLDER_KINDS`).

    The owner's space, and every space of an install that does not enforce
    Spaces: *install* when given (the folder the caller has always used),
    else ``<data dir>/<kind>``. Any other space: ``<its folder>/<kind>``.
    ``NoSpaceBound`` once enforced with no space bound: a file is never kept
    for no one.
    """
    if kind != FILES and kind not in FOLDER_KINDS:
        raise ValueError(f"not a space folder: {kind!r}")
    space = _bound_space()
    if space is None or space == OWNER_SPACE_ID:
        if install is not None:
            folder = Path(install)
        else:
            from kazma_core.paths import data_dir

            folder = data_dir() / kind
    else:
        folder = space_dir(space) / kind
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def sandboxed_files_root() -> Path | None:
    """The working folder of the bound sandboxed space (created), or None
    when this work is not in a sandboxed space."""
    space = sandboxed_space()
    if space is None:
        return None
    root = space_dir(space) / FILES
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def is_space_files_path(path: Path) -> bool:
    """True when *path* (resolved) is inside some space's working folder,
    ``<data dir>/spaces/<id>/files``: a person's own files, as the default
    coding sandbox is, so a database or ``.env`` there is theirs and not one
    of Kazma's."""
    try:
        relative = path.relative_to(_spaces_root().resolve())
    except (ValueError, OSError, RuntimeError):
        return False
    parts = relative.parts
    return len(parts) >= 2 and parts[1] == FILES


def another_spaces_path(path: Path) -> bool:
    """True when *path* (resolved) is inside ``<data dir>/spaces`` and not
    inside the bound space's own folder: another space's files, which no
    tool of this work may open -- the owner's file tools and shell included
    (the machine's workspace can hold the data folder: on the live install
    it does). With no scope bound, every space's folder is another's."""
    try:
        relative = path.relative_to(_spaces_root().resolve())
    except (ValueError, OSError, RuntimeError):
        return False
    bound = current_scope()
    parts = relative.parts
    return not (bound is not None and parts and parts[0] == bound.space_id)
