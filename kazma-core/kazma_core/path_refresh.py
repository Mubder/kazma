"""Adopt PATH entries the OS gained after this process tree started.

Windows builds a process's environment once, when the process starts, from
the registry (``HKLM\\...\\Session Manager\\Environment`` then
``HKCU\\Environment``). A child inherits its parent's copy, not the
registry's. The live server is started by ``kazma_guard``, which the
``KazmaAgent`` scheduled task starts at boot, so every server the guard spawns
carries the PATH the guard saw at boot -- however long ago that was.

Live 2026-09-25: a Docker Desktop update dropped its CLI folder from PATH.
The operator put it back (``where.exe docker`` found it at once), and the
server restarted afterwards still logged "the docker CLI is not on PATH":
the guard, up since 04:25, handed it the 04:25 PATH. ``kazma_guard.py
--reload`` could not pass the fix on; only restarting the scheduled task, or
the machine, would have. The same is true of any tool installed while Kazma
runs -- LibreOffice, Tesseract, Node for an MCP server, restic, git.

So the server, while it builds itself, reads PATH from the OS settings and
appends the entries it is missing -- what ``--reload`` means to an operator.

Append only. Nothing is removed or reordered: a virtualenv first on PATH
stays first, an entry the running tree was given on purpose stays, and an
entry the settings gained can never shadow a program the process already
resolves. An entry the settings LOST stays too -- a directory that no longer
exists costs nothing on PATH, while removing one this process still relies
on would be a change nobody asked for.

Windows only. Elsewhere the service manager owns the environment (systemd
``Environment=``, launchd plists) and a restart of the service re-reads it.
"""

from __future__ import annotations

import logging
import ntpath
import os
from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

__all__ = ["refresh_path_from_os"]

#: Where Windows keeps PATH, in the order it concatenates them for a new
#: process: the machine's entries, then the user's.
_SOURCES = (
    ("HKEY_LOCAL_MACHINE", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
    ("HKEY_CURRENT_USER", "Environment"),
)


def _os_path_settings() -> list[str]:
    """PATH as the OS settings hold it now: machine value, then user value.

    ``REG_EXPAND_SZ`` values are expanded the way Windows expands them for a
    new process (``%SystemRoot%``, ``%USERPROFILE%``, ...); ``REG_SZ`` values
    are taken literally, as Windows takes them. Empty off Windows, and for
    any hive that cannot be read.
    """
    if os.name != "nt":
        return []
    import winreg

    values: list[str] = []
    for hive_name, subkey in _SOURCES:
        try:
            with winreg.OpenKey(getattr(winreg, hive_name), subkey) as key:
                raw, kind = winreg.QueryValueEx(key, "Path")
            if not isinstance(raw, str) or not raw.strip():
                continue
            if kind == winreg.REG_EXPAND_SZ:
                raw = winreg.ExpandEnvironmentStrings(raw)
        except OSError as exc:
            # No user PATH at all is normal; say so only at debug.
            logger.debug("[env] PATH not readable from %s\\%s: %s", hive_name, subkey, exc)
            continue
        values.append(raw)
    return values


def _key(entry: str) -> str:
    """Comparison form, by Windows' rules: case, quotes and a trailing slash do not count.

    ``ntpath`` on every platform, not ``os.path``: only Windows has settings to
    adopt, and a comparison that changed meaning with the host would pass on
    one machine and fail on the next.
    """
    text = entry.strip().strip('"').strip()
    return ntpath.normcase(ntpath.normpath(text)) if text else ""


def _merge_path(current: str, settings: Iterable[str], sep: str = os.pathsep) -> tuple[str, list[str]]:
    """``current`` plus every entry of ``settings`` it lacks, appended in order.

    Returns the merged PATH and the entries that were added. ``current`` is
    only appended to (trailing separators aside), never rewritten.
    """
    seen = {_key(e) for e in current.split(sep)}
    seen.discard("")
    added: list[str] = []
    for value in settings:
        for entry in value.split(sep):
            key = _key(entry)
            if not key or key in seen:
                continue
            seen.add(key)
            added.append(entry.strip())
    if not added:
        return current, []
    head = current.rstrip(sep)
    return (sep.join([head, *added]) if head else sep.join(added)), added


def refresh_path_from_os(read: Callable[[], list[str]] | None = None) -> list[str]:
    """Append to ``os.environ["PATH"]`` what the OS settings added since start.

    Returns the entries appended (empty when there was nothing new, off
    Windows, or when the settings could not be read). A registry or data
    problem is logged, not raised: a server that cannot read the registry
    boots with the PATH it was given, which is what it did before this existed.
    """
    try:
        merged, added = _merge_path(os.environ.get("PATH", ""), (read or _os_path_settings)())
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        logger.warning("[env] could not compare PATH with the OS settings: %s", exc)
        return []
    if added:
        os.environ["PATH"] = merged
        logger.info(
            "[env] PATH gained %d entr%s from the OS settings since this process "
            "tree started: %s",
            len(added), "y" if len(added) == 1 else "ies", os.pathsep.join(added),
        )
    return added
