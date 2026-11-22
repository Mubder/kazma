"""Whether one folder is another, or inside it, under any of its names.

A real path keeps a second name of the same folder: a loopback share
(``\\\\localhost\\C$\\...``) and ``\\\\?\\`` paths resolve to themselves. So a
folder is also compared by its identity -- the volume and the file id
(``st_dev``, ``st_ino``), the same whatever name reaches it (2026-11-21, the
SonarCloud review of the backup folder's Test). One home for the rule: the
backup folder (``backup.folder_copy``) and a shared space's project folder
(``spaces.project_folder``) ask it.
"""

from __future__ import annotations

import errno
import os
from pathlib import Path

__all__ = ["UnreachableError", "identity", "inside", "not_made_yet"]


class UnreachableError(Exception):
    """A path that is there to ask about but did not answer: a share whose
    host is offline, a folder this account may not read."""


def not_made_yet(exc: OSError) -> bool:
    """True for "no such file or folder", as opposed to a host or a disk that
    cannot be reached (Windows maps an unreachable share to ENOENT too, so its
    own code decides there)."""
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in (2, 3)  # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND
    return exc.errno in (errno.ENOENT, errno.ENOTDIR)


def identity(path: Path) -> tuple[int, int] | None:
    """*path*'s ``(st_dev, st_ino)``: the volume and the file on it, the same
    whatever name reaches it; None when it is not there. Raises
    :class:`UnreachableError` when it could not be asked."""
    try:
        info = os.stat(path)
    except OSError as exc:
        if not_made_yet(exc):
            return None
        raise UnreachableError(str(path)) from exc
    except ValueError:
        return None
    return info.st_dev, info.st_ino


def inside(child: Path, parent: Path) -> bool:
    """True when *child* is *parent* or under it, by its real path or by its
    identity: the identity of each of *child*'s folders that exists is
    compared with *parent*'s. The walk up stops at the first folder that
    cannot be asked: an offline share answers each level only after the
    network's timeout."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        pass
    try:
        target = identity(parent)
        if target is None or not target[1]:
            return False  # not there, or a file system with no file ids
        here = child
        while True:
            if identity(here) == target:
                return True
            up = here.parent
            if up == here:
                return False
            here = up
    except UnreachableError:
        return False
