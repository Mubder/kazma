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

__all__ = ["remote_path_reason"]


def remote_path_reason(path: str | os.PathLike[str] | None) -> str | None:
    """Why *path* is refused as a network or device path, or None.

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
    return None
