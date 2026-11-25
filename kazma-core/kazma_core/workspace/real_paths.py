"""Where a path really leads: symbolic links and junctions followed.

A containment check made on a path as it is written can be walked out of:
a junction (``mklink /J``, no administrator needed) or a symbolic link
inside the workspace leads to whatever folder it names, and a walk that
enters it lists that folder's files as the workspace's. Every check that
asks "is this inside the workspace?" asks it of the real path
(:func:`real_path`), and every walk of the workspace leaves a linked
folder (:func:`is_linked`) unentered.

Python 3.11 has no ``os.path.isjunction`` and its ``os.path.islink`` is
False for a junction, so a walk that asked those alone entered junctions:
``file_search`` over a git repository read files through one (git lists
them). Standard library only: ``project_files`` and ``store_registry``
import this.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

__all__ = ["inside", "is_linked", "real_path", "short_name"]

#: Reparse points that lead elsewhere: a symbolic link, and a junction or
#: volume mount point. Other reparse points (cloud-file placeholders, dedup)
#: are the file itself.
_LINK_TAGS = frozenset(
    {
        getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
        getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003),
    }
)


def real_path(path: str | os.PathLike[str]) -> Path:
    """*path* with every link, junction and short name resolved (never raises).
    Always absolute: Windows answers a drive-relative ``C:x`` for a part it
    cannot hold, and "is it inside?" asked of that is asked of another file."""
    text = os.fspath(path)
    try:
        real = Path(os.path.realpath(text))
        if real.is_absolute():
            return real
    except (OSError, ValueError):
        pass
    return Path(os.path.abspath(text))


def is_linked(path: str | os.PathLike[str]) -> bool:
    """True for a symbolic link or a junction (on any Python version)."""
    text = os.fspath(path)
    try:
        if os.path.islink(text):
            return True
        info = os.lstat(text)
    except (OSError, ValueError):
        return False
    tag = getattr(info, "st_reparse_tag", 0)
    return bool(tag) and tag in _LINK_TAGS


def short_name(path: str | os.PathLike[str]) -> str | None:
    """The Windows short (8.3) name of an existing file (``ENV~1`` for
    ``.env``), or None: not Windows, no short name, or not found.

    A program that expands its own wildcards with ``FindFirstFile`` matches
    a pattern against both names, so ``RESTIC*.PAS`` reaches ``restic.pass``.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        # A private handle: setting argtypes on ctypes.windll's shared
        # function object would change it for every other caller.
        get = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW  # type: ignore[attr-defined]
        get.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get.restype = wintypes.DWORD
        text = os.fspath(path)
        size = get(text, None, 0)
        if not size:
            return None
        buffer = ctypes.create_unicode_buffer(size)
        if not get(text, buffer, size):
            return None
    except (OSError, AttributeError, ValueError):
        return None
    name = os.path.basename(buffer.value)
    return name or None


def inside(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> bool:
    """True when *path*'s real path is *root*'s real path or under it."""
    target = real_path(path)
    base = real_path(root)
    try:
        target.relative_to(base)
        return True
    except ValueError:
        return False
