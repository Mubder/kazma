"""A backup copy in a folder on this computer: another drive, a USB disk, a
network share (the owner's item, 19 Nov 2026).

Kazma's own backups live in ``<data dir>/backups``, on the same disk as Kazma:
they do not survive that disk. The cloud copy does, but needs an account.
This is the third destination: a folder the owner chooses (Settings → Backup),
holding a **restic repository** (``<folder>/kazma-restic``) beside the local and
offsite ones (``restic_repo.repo_paths``, key ``folder``). Restic gives the
rules the cloud copy keeps by hand, and more:

* a snapshot exists only once it is complete (a copy that did not finish is
  never counted), and old ones go only by ``forget`` after a good one, keeping
  the newest ``backups.folder.keep`` of each kind (``--keep-last``; 0 keeps
  every copy);
* the copy is encrypted with Kazma's restic passphrase (Spaces' rule that only
  an encrypted copy leaves the machine, N10, holds for a USB disk too);
* each copy stores only what changed, and ``restic check`` verifies it
  (the restic maintenance, every night);
* ``python -m kazma_core.backup.restore --repo <folder>/kazma-restic
  --target <empty folder>`` restores from it.

A folder that is not there (a USB disk unplugged, a share offline) is not a
failure of the backup: the copy is left out of that run, said once
(``backup.folder_missing``) and said again once when it is back
(``backup.folder_back``), never on every run (the owner's question, answered
20 Nov: a missed copy must be seen, and repeating it teaches nobody).
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import string
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ENABLED_KEY",
    "KEEP_KEY",
    "PATH_KEY",
    "PATH_PROBLEMS",
    "REPO_FOLDER",
    "browse",
    "folder_settings",
    "folder_state",
    "keep_policy",
    "note_presence",
    "path_problem",
    "test_folder",
]

ENABLED_KEY = "backups.folder.enabled"
PATH_KEY = "backups.folder.path"
KEEP_KEY = "backups.folder.keep"
#: Kazma's repository inside the chosen folder: the folder may hold other things.
REPO_FOLDER = "kazma-restic"
#: Copies of each kind kept when ``backups.folder.keep`` is not set: as many
#: as the local copies and the cloud copies (7).
DEFAULT_KEEP = 7
#: Runtime: when the folder was found missing (cleared when it is back).
MISSING_SINCE_KEY = "system.backups.folder_missing_since"
#: Folders listed at most by one browse (a drive's root can hold thousands).
BROWSE_MAX = 500


def _store() -> Any:
    from kazma_core.config_store import get_config_store

    return get_config_store()


def _keep_count(value: Any) -> int:
    """``backups.folder.keep`` as a count: 0 keeps every copy; anything that is
    not a whole number is the default, never "keep none"."""
    if value is None or isinstance(value, bool):
        return DEFAULT_KEEP
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return DEFAULT_KEEP


def folder_settings() -> dict[str, Any]:
    """``{enabled, path, keep}`` as saved (never raises: a store that cannot
    answer is a folder copy that is off)."""
    from kazma_core.db.pg_helpers import store_errors

    try:
        store = _store()
        return {
            "enabled": bool(store.get(ENABLED_KEY)),
            "path": str(store.get(PATH_KEY) or "").strip(),
            "keep": _keep_count(store.get(KEEP_KEY)),
        }
    except store_errors():  # a backup must never fail on a settings read
        logger.debug("[folder-copy] settings read failed", exc_info=True)
        return {"enabled": False, "path": "", "keep": DEFAULT_KEEP}


def _data_dir() -> Path:
    from kazma_core.paths import data_dir

    return data_dir(create=False)


class _UnreachableError(Exception):
    """A path that is there to ask about but did not answer: a share whose
    host is offline, a folder this account may not read."""


def _not_made_yet(exc: OSError) -> bool:
    """True for "no such file or folder", as opposed to a host or a disk that
    cannot be reached (Windows maps an unreachable share to ENOENT too, so its
    own code decides there)."""
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return winerror in (2, 3)  # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND
    return exc.errno in (errno.ENOENT, errno.ENOTDIR)


def _identity(path: Path) -> tuple[int, int] | None:
    """*path*'s ``(st_dev, st_ino)``: the volume and the file on it, the same
    whatever name reaches it; None when it is not there. Raises
    :class:`_UnreachableError` when it could not be asked."""
    try:
        info = os.stat(path)
    except OSError as exc:
        if _not_made_yet(exc):
            return None
        raise _UnreachableError(str(path)) from exc
    except ValueError:
        return None
    return info.st_dev, info.st_ino


def _inside(child: Path, parent: Path) -> bool:
    """True when *child* is *parent* or under it, by its real path or by its
    identity. A real path keeps a second name of the same folder: a loopback
    share (``\\\\localhost\\C$\\...``) resolves to itself, so the identity of
    each of *child*'s folders that exists is compared with *parent*'s too
    (2026-11-21, the SonarCloud review of the folder's Test). The walk up
    stops at the first folder that cannot be asked: an offline share answers
    each level only after the network's timeout."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        pass
    try:
        target = _identity(parent)
        if target is None or not target[1]:
            return False  # not there, or a file system with no file ids
        here = child
        while True:
            if _identity(here) == target:
                return True
            up = here.parent
            if up == here:
                return False
            here = up
    except _UnreachableError:
        return False


#: Every code :func:`path_problem` answers, in the order it asks: the save
#: refuses each (``settings_validation``), the page words each.
PATH_PROBLEMS = ("empty", "device", "relative", "inside_data")


def path_problem(path: str) -> str | None:
    """Why *path* cannot be the backup folder, or None. A code the page
    words (:data:`PATH_PROBLEMS`): ``empty``; ``device`` (``\\\\.\\``,
    ``\\\\?\\``, ``\\??\\``: a pipe, a raw disk or a device is never a folder,
    refused on the text before anything opens it); ``relative``;
    ``inside_data`` (a copy beside the backups it protects does not survive
    their disk, under any name of that folder). A network share
    (``\\\\server\\share``) is a folder like a drive's: choosing one is the
    feature."""
    from kazma_core.workspace.remote_paths import device_path_reason

    text = (path or "").strip()
    if not text:
        return "empty"
    if device_path_reason(text):
        return "device"
    candidate = Path(text)
    if not candidate.is_absolute() and not text.startswith("\\\\"):
        return "relative"
    if _inside(candidate, _data_dir()):
        return "inside_data"
    return None


def _repo(path: str) -> Path:
    return Path(path) / REPO_FOLDER


def folder_state() -> dict[str, Any]:
    """Where the folder copy stands: ``state`` is ``off`` (not enabled or no
    folder), ``missing`` (the folder is not there), ``ready`` (its repository
    exists) or ``new`` (the folder is there; the first backup makes the
    repository). ``repo`` is the repository's path when the folder is there."""
    cfg = folder_settings()
    out: dict[str, Any] = {**cfg, "state": "off", "repo": ""}
    if not cfg["enabled"] or not cfg["path"] or path_problem(cfg["path"]):
        return out
    folder = Path(cfg["path"])
    if not folder.is_dir():
        return {**out, "state": "missing"}
    repo = _repo(cfg["path"])
    return {**out, "state": "ready" if (repo / "config").is_file() else "new", "repo": str(repo)}


def repo_for_backup() -> str:
    """The repository a backup is copied into, or "" when there is none now
    (off, or the folder is missing: :func:`note_presence` says so)."""
    state = folder_state()
    return state["repo"] if state["state"] in ("ready", "new") else ""


def keep_policy() -> tuple[str, ...] | None:
    """restic ``forget``'s policy for the folder's repository: the newest
    *keep* snapshots of each kind; None keeps every copy (keep 0)."""
    keep = folder_settings()["keep"]
    return None if keep <= 0 else ("--keep-last", str(keep))


def note_presence() -> str:
    """Say once when the chosen folder is missing, and once when it is back.

    Called by every universal backup. Returns the state (``off``, ``missing``,
    ``ready``, ``new``). The first time a run finds the folder missing it
    raises ``backup.folder_missing``; later runs while it stays missing say
    nothing more; the first run that finds it again raises
    ``backup.folder_back``."""
    from kazma_core.db.pg_helpers import store_errors

    state = folder_state()
    try:
        store = _store()
        since = store.get(MISSING_SINCE_KEY)
        if state["state"] == "missing":
            if not since:
                store.set(MISSING_SINCE_KEY, time.time(), category="system")
                _alert("backup.folder_missing", "The backup folder is not there",
                       f"{state['path']} could not be found (a USB disk not connected, a network share "
                       "offline?). Backups go on without that copy; Kazma says so again when the folder "
                       "is back.", "warn")
        elif since:
            store.delete(MISSING_SINCE_KEY)
            if state["state"] in ("ready", "new"):
                missed_h = (time.time() - float(since)) / 3600
                _alert("backup.folder_back", "The backup folder is back",
                       f"{state['path']} is there again after {missed_h:.1f} h; this backup is copied to it.",
                       "success")
    except store_errors():  # a notice must never fail a backup
        logger.warning("[folder-copy] the folder's presence could not be recorded", exc_info=True)
    return state["state"]


def _alert(key: str, title: str, detail: str, severity: str) -> None:
    from kazma_core.observability.ops_alerts import alert  # never raises

    alert(key, title, detail, severity=severity, cooldown_s=0)


def _same_disk(folder: Path, other: Path) -> bool:
    """True when *folder* and *other* are on one volume, by the volume itself
    (``st_dev``: on Windows its serial number), never by a drive letter: a
    loopback share of Kazma's own disk (``\\\\localhost\\C$\\...``) or a
    ``subst`` drive has another letter and is the same disk; a volume mounted
    in a folder of ``C:`` has its letter and is not."""
    try:
        first, second = _identity(folder), _identity(other)
    except _UnreachableError:
        return False
    return first is not None and second is not None and first[0] == second[0]


def test_folder(path: str) -> dict[str, Any]:
    """Check *path* as the backup folder without changing the setting: it is
    allowed, it exists (or its drive does, and it can be made), Kazma can
    write and remove a file there, how much room is free, whether it is the
    disk Kazma's data is on, and whether restic and its passphrase are there.
    ``{ok, problem?, checks: [{check, ok, detail}]}``; *problem* is a code."""
    from kazma_core.backup.restic_repo import ensure_password, restic_available

    checks: list[dict[str, Any]] = []
    problem = path_problem(path)
    if problem:
        return {"ok": False, "problem": problem, "checks": checks}
    folder = Path(path.strip())
    made = False
    if not folder.is_dir():
        anchor = Path(folder.anchor) if folder.anchor else None
        if anchor is not None and not anchor.exists():
            return {"ok": False, "problem": "missing", "checks": checks}
        try:
            folder.mkdir(parents=True, exist_ok=True)
            made = True
        except OSError as exc:
            return {"ok": False, "problem": "cannot_create", "detail": type(exc).__name__, "checks": checks}
    probe = folder / f".kazma-write-test-{uuid.uuid4().hex[:8]}"
    try:
        probe.write_bytes(b"kazma")
        writable = probe.read_bytes() == b"kazma"
    except OSError as exc:
        writable = False
        checks.append({"check": "writable", "ok": False, "detail": type(exc).__name__})
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    if writable:
        checks.append({"check": "writable", "ok": True, "detail": "made" if made else ""})
    try:
        free = shutil.disk_usage(folder).free
        checks.append({"check": "free", "ok": free > 1024 ** 3, "detail": f"{free / 1024 ** 3:.1f}"})
    except OSError as exc:
        checks.append({"check": "free", "ok": False, "detail": type(exc).__name__})
    same = _same_disk(folder, _data_dir())
    checks.append({"check": "other_disk", "ok": not same, "detail": "same" if same else ""})
    checks.append({"check": "restic", "ok": restic_available(), "detail": ""})
    checks.append({"check": "passphrase", "ok": bool(restic_available() and ensure_password()[0]), "detail": ""})
    blocking = {"writable", "restic", "passphrase"}
    ok = all(c["ok"] for c in checks if c["check"] in blocking)
    return {"ok": ok, "checks": checks, "repo": str(_repo(str(folder)))}


def _drives() -> list[str]:
    if os.name != "nt":
        return ["/"]
    found: list[str] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            found.append(root)
    return found


def browse(path: str = "") -> dict[str, Any]:
    """The folders under *path* (directories only, no hidden ones, at most
    :data:`BROWSE_MAX`), its parent, and this computer's drives: what the
    page's folder picker shows. An empty *path* lists the drives alone."""
    out: dict[str, Any] = {"path": "", "parent": "", "folders": [], "drives": _drives(), "more": False}
    text = (path or "").strip()
    if not text:
        return out
    folder = Path(text)
    if not folder.is_absolute() or not folder.is_dir():
        return {**out, "problem": "missing"}
    names: list[str] = []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.startswith((".", "$")) or entry.name == "System Volume Information":
                    continue
                try:
                    if entry.is_dir(follow_symlinks=False):
                        names.append(entry.name)
                except OSError:
                    continue
    except OSError as exc:
        return {**out, "path": str(folder), "problem": "unreadable", "detail": type(exc).__name__}
    names.sort(key=str.casefold)
    parent = folder.parent
    return {**out, "path": str(folder), "parent": "" if parent == folder else str(parent),
            "folders": names[:BROWSE_MAX], "more": len(names) > BROWSE_MAX}
