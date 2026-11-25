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
from collections.abc import Callable, Iterator
from pathlib import Path

__all__ = ["inside", "is_linked", "own_files", "real_path", "short_name"]

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


def own_files(folder: Path, *, unreadable: list[Path] | None = None, hidden: bool = True,
              most_folders: int | None = None, keep: Callable[[Path], bool] | None = None,
              folders_seen: list[Path] | None = None, linked_at_top: bool = False) -> Iterator[Path]:
    """Every file inside *folder* that is its own: never one a link or a
    junction leads to, so nothing of another place is counted, exported,
    moved or backed up as this one's (:func:`is_linked`). Every walk of a
    space's files asks it, a backup of the whole data folder too
    (``tests/test_space_file_walks.py``). *folder* itself may be a link, and
    with *linked_at_top* a folder directly in it: an install's own layout
    (``document-store`` on another disk).

    Sorted by name, folder by folder. A folder that cannot be read raises, or
    is added to *unreadable* when the caller counts them, as is the first one
    past *most_folders*. Without *hidden*, no name that starts with a dot;
    with *keep*, no file it answers False for, and no such folder is entered.
    Each folder read is added to *folders_seen*.
    """
    folders = [folder]
    read = 0
    while folders:
        here = folders.pop()
        read += 1
        if most_folders is not None and read > most_folders:
            if unreadable is not None:
                unreadable.append(here)
            return
        try:
            with os.scandir(here) as found:
                entries = sorted(found, key=lambda entry: entry.name)
        except OSError:
            if unreadable is None:
                raise
            unreadable.append(here)
            continue
        if folders_seen is not None:
            folders_seen.append(here)
        inner = []
        for entry in entries:
            linked = is_linked(entry.path)
            if linked and not (linked_at_top and here == folder and os.path.isdir(entry.path)):
                continue
            if not hidden and entry.name.startswith("."):
                continue
            if keep is not None and not keep(Path(entry.path)):
                continue
            if linked or entry.is_dir(follow_symlinks=False):
                inner.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                yield Path(entry.path)
        folders.extend(reversed(inner))


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
