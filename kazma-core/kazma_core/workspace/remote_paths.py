"""Refuse a network or device path before anything touches it.

On Windows, resolving, ``stat``-ing or checking the existence of
``\\\\host\\share\\x`` (or ``//host/share/x``) opens an SMB connection to that
host as the service account: the account's NetNTLM hash goes to whoever runs
the host, and the path itself carries data out. Path policy refused such a
path as "outside the workspace" -- AFTER ``Path.resolve()`` /
``os.path.realpath`` had already made the connection (2026-11-10 audit:
``file_read`` is read tier, no approval card; two GET routes could be reached
by a cross-site navigation).

``remote_path_reason`` is the one check, made on the TEXT, before any
filesystem call. It refuses every form that leaves the local drives:
``\\\\server\\share``, ``//server/share``, ``\\\\?\\UNC\\...``, the device
namespaces ``\\\\.\\`` and ``\\\\?\\`` (a local long path ``\\\\?\\C:\\...`` too:
nothing Kazma does needs one), and the NT object prefix ``\\??\\``. It
applies on every platform, so the rule is one rule: on POSIX ``//x`` is a
local path, and nothing Kazma does names one.

Standard library only: ``kazma_core.workspace.binding`` imports it, and that
module is imported early (AGENTS.md §24 A).
"""

from __future__ import annotations

import os
from pathlib import PureWindowsPath

__all__ = ["device_path_reason", "drive_relative_reason", "remote_path_reason"]


def _flat_text(path: str | os.PathLike[str] | None) -> str | None:
    """*path* as text, stripped of whitespace and one pair of quotes, with
    every ``/`` read as ``\\``; None for what is not a path."""
    if path is None:
        return None
    try:
        text = os.fspath(path)
    except TypeError:
        return None
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    return text.replace("/", "\\")


def device_path_reason(path: str | os.PathLike[str] | None) -> str | None:
    """Why *path* is refused as a device-namespace path, or None.

    The narrower rule, for the one place a network share is the feature (the
    backup folder the owner chooses, ``backup.folder_copy``): ``\\\\.\\``
    (a named pipe, a raw disk, a device), ``\\\\?\\`` (a local long path or
    ``\\\\?\\UNC\\``: Kazma names a share in its plain form) and the NT object
    prefix ``\\??\\`` are refused; ``\\\\server\\share`` is not. Reads the
    text only: no filesystem or network call is made.
    """
    flat = _flat_text(path)
    if not flat:
        return None
    if flat.startswith("\\??\\") or (
        flat.startswith("\\\\") and len(flat) >= 3 and flat[2] in ".?" and (len(flat) == 3 or flat[3] == "\\")
    ):
        return (
            f"'{flat}' is a device path (\\\\.\\, \\\\?\\ or \\??\\). Name a folder "
            "by its drive letter, or a network share as \\\\server\\share."
        )
    return None


def _inner_drive_reason(path: str | os.PathLike[str] | None) -> str | None:
    """Why *path* is refused for naming a drive after its first part, or None.

    Windows only (2026-11-25): ``file:///C:/x`` and ``a?b/C:/x`` are relative
    paths, and ``Path.resolve()`` of one under a workspace answers the
    drive-relative ``C:x``. The access check read that as the workspace's own
    ``x`` and allowed it; the tool then opened ``x`` in the server's current
    folder. A part after the first that Windows reads as a drive (its second
    character is a colon) names no file that can exist. Reads the text only.
    """
    flat = _flat_text(path)
    if not flat:
        return None
    if any(part[1:2] == ":" for part in flat.split("\\")[1:]):
        return (
            f"'{flat}' names a drive in the middle of a path. Give a path in the "
            "workspace, or a full path that begins with its drive."
        )
    return None


def drive_relative_reason(path: str | os.PathLike[str] | None) -> str | None:
    """Why *path* is refused as drive-relative (``C:x``), or None.

    Windows only: it means "x in the server's current folder on that drive",
    and joined to a workspace on the same drive it means the workspace's own
    ``x``. The access check and the tool read it differently (2026-11-25).
    """
    flat = _flat_text(path) if os.name == "nt" else None
    if flat and flat[1:2] == ":" and flat[2:3] != "\\":
        return f"'{flat}' is relative to a drive. Give a path in the workspace, or a full path."
    return None


def remote_path_reason(path: str | os.PathLike[str] | None) -> str | None:
    """Why *path* is refused as a network or device path, or None. On Windows
    a path that names a drive after its first part is refused here too
    (:func:`_inner_drive_reason`): every door that takes a path asks this.

    Reads the text only: no filesystem or network call is made.
    """
    if path is None:
        return None
    try:
        text = os.fspath(path)
    except TypeError:
        return None
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    flat = text.replace("/", "\\")
    if flat.startswith("\\\\") or flat.startswith("\\??\\"):
        return (
            f"'{text}' is a network or device path. Kazma reads and writes "
            "local files only; copy the file into the workspace first."
        )
    try:
        drive = PureWindowsPath(text).drive
    except (TypeError, ValueError):
        drive = ""
    if drive.startswith("\\\\") or drive.startswith("//"):
        return (
            f"'{text}' is a network path. Kazma reads and writes local files "
            "only; copy the file into the workspace first."
        )
    return _inner_drive_reason(text) if os.name == "nt" else None
