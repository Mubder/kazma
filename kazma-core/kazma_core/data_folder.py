"""Kazma's data off Windows' own drive: the data folder (2026-11-21).

The 19 Nov PC reset: ``kazma setup postgres`` had put the database in
``C:\\Program Files\\PostgreSQL\\18\\data`` and its WAL archive in
``C:\\ProgramData\\Kazma``, and Kazma's container engine (the ``kazma-engine``
WSL distro) lived in ``%LOCALAPPDATA%\\Kazma\\engine``. A reset of C: lost the
database even though the install folder -- ``.env``, ``.kazma``, ``kazma-data``
with its 6-hourly dumps -- was on another drive, and it came back only from
those dumps.

The owner chooses one folder on another drive (``kazma setup data-folder
D:\\Kazma``, or ``kazma setup install --data-folder D:\\Kazma``), and each part
lives in it (:class:`Layout`)::

    D:\\Kazma\\postgres-18\\data          the database's data folder
    D:\\Kazma\\postgres-18\\wal_archive   its WAL archive
    D:\\Kazma\\postgres-18\\base          its base backups
    D:\\Kazma\\engine                     the kazma-engine distro's disk

The choice is recorded in ``setup_record.json`` (:data:`RECORD_KEY`), beside
what setup installed, so a second run and a repair keep it, and a run after a
reset finds the parts again: setup attaches the kept database to a freshly
installed service and registers the kept engine disk in place, instead of
starting empty (``kazma_cli.setup``, ``container_engine_setup``). An install
that already has them elsewhere moves them (:mod:`kazma_core.data_folder_move`).

Every refusal comes before anything changes (:func:`folder_problem`): not
Windows, not an absolute local path, a share or a device path, characters the
WAL archive's copy command or the installer cannot take, Windows' own drive
(a reset wipes it), a drive that is not a fixed disk (removable, network,
optical, a RAM disk), a ``subst`` drive (a folder of another drive, often
C:), a folder inside Kazma's install or data folder (Kazma's own file tools
work there), too little free space.

Blocking (disk and registry reads): off the loop.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

__all__ = [
    "JOURNAL_KEY",
    "RECORD_KEY",
    "Disk",
    "Layout",
    "chosen",
    "clear_journal",
    "folder_problem",
    "journal",
    "kept_cluster_major",
    "normalized",
    "record_choice",
    "write_journal",
]

#: The choice's key in ``setup_record.json``: ``{"path": ..., "chosen_at": ...}``.
RECORD_KEY = "data_folder"
#: A move in progress (``data_folder_move``): which part, at what stage.
JOURNAL_KEY = "data_folder_move"
#: What a drive root alone ("D:") means: this folder on it.
DEFAULT_NAME = "Kazma"
#: Free space a first install's parts ask for in the folder: the database,
#: its archive and base backups, and the engine's images and volumes.
FRESH_FREE_BYTES = 20 * 2**30
#: Room left over after a move, on top of what moves.
MOVE_MARGIN_BYTES = 2 * 2**30
#: Windows' GetDriveTypeW answers.
DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE, DRIVE_CDROM, DRIVE_RAMDISK = 2, 3, 4, 5, 6
_DRIVE_WORDS = {0: "of a kind Windows cannot tell", 1: "not there", DRIVE_REMOVABLE: "a removable drive",
                DRIVE_REMOTE: "a network drive", DRIVE_CDROM: "an optical drive", DRIVE_RAMDISK: "a RAM disk"}
#: One part of a folder's path: what the archive command (``copy /Y "%p"
#: "<folder>\\%f.part"``, run by cmd for the server, where ``%`` is
#: Postgres's own escape), the installer's ``--datadir`` and a service's
#: command line all take as written.
_PART = re.compile(r"^[A-Za-z0-9 _.()\-]+$")


@dataclass(frozen=True)
class Disk:
    """What the checks read of the machine's drives (tests pass fakes)."""

    drive_type: Callable[[str], int]
    dos_device: Callable[[str], str]
    free_bytes: Callable[[Path], int | None]
    system_drive: str = "C:"

    @classmethod
    def this_machine(cls) -> Disk:
        return cls(drive_type=_drive_type, dos_device=_dos_device, free_bytes=_free_bytes,
                   system_drive=(os.environ.get("SystemDrive") or "C:").rstrip("\\").upper())


def _drive_type(root: str) -> int:
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(root))
    except (AttributeError, OSError):
        return 0


def _dos_device(drive: str) -> str:
    """What *drive* (``D:``) names: ``\\Device\\HarddiskVolume3`` for a
    volume, ``\\??\\C:\\some\\folder`` for a ``subst`` drive."""
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(1024)
        if ctypes.windll.kernel32.QueryDosDeviceW(drive, buffer, len(buffer)):
            return buffer.value
    except (AttributeError, OSError):
        pass
    return ""


def _free_bytes(path: Path) -> int | None:
    for candidate in (path, *path.parents):
        try:
            if candidate.exists():
                return int(shutil.disk_usage(candidate).free)
        except OSError:
            continue
    return None


@dataclass(frozen=True)
class Layout:
    """Where each part lives in the data folder."""

    root: Path

    def postgres(self, major: int) -> Path:
        return self.root / f"postgres-{int(major)}"

    def postgres_data(self, major: int) -> Path:
        return self.postgres(major) / "data"

    def wal_archive(self, major: int) -> Path:
        return self.postgres(major) / "wal_archive"

    def base_backups(self, major: int) -> Path:
        return self.postgres(major) / "base"

    @property
    def engine(self) -> Path:
        return self.root / "engine"


# -- the choice --------------------------------------------------------------------------------


def normalized(text: str) -> str:
    """The folder *text* names: a drive root alone (``D:``, ``D:\\``) means
    ``D:\\Kazma``; a trailing separator goes; forward slashes become
    backslashes. Nothing on the disk is read."""
    value = (text or "").strip().strip('"').strip("'").strip().replace("/", "\\")
    if re.fullmatch(r"[A-Za-z]:\\?", value):
        return f"{value[0].upper()}:\\{DEFAULT_NAME}"
    if len(value) > 3:
        value = value.rstrip("\\")
    if re.match(r"^[a-z]:", value):
        value = value[0].upper() + value[1:]
    return value


def _inside(child: PureWindowsPath, parent: Path | None) -> bool:
    if parent is None:
        return False
    try:
        inner = PureWindowsPath(str(parent))
    except (TypeError, ValueError):
        return False
    child_parts = [p.lower() for p in child.parts]
    parent_parts = [p.lower() for p in inner.parts]
    return len(child_parts) >= len(parent_parts) and child_parts[:len(parent_parts)] == parent_parts


def folder_problem(text: str, *, disk: Disk, os_name: str = "windows", install_root: Path | None = None,
                   data_dir: Path | None = None, need_bytes: int = 0) -> str | None:
    """Why *text* (already :func:`normalized`) cannot be the data folder, in
    a sentence, or None. Reads the drive's kind and free space; changes
    nothing."""
    if os_name != "windows":
        return ("a data folder is for Windows, where setup chooses where PostgreSQL and the engine keep their "
                "data. Elsewhere, mount the drive you want where the package keeps it (Debian: "
                "/var/lib/postgresql)")
    from kazma_core.workspace.remote_paths import remote_path_reason

    if not text:
        return "no folder was named"
    remote = remote_path_reason(text)
    if remote:
        return f"{text} is a network or device path: the data folder is on a local fixed drive"
    path = PureWindowsPath(text)
    drive = path.drive
    if len(drive) != 2 or drive[1] != ":" or not path.is_absolute():
        return f"{text} is not a full path on a drive (write it like D:\\Kazma)"
    for part in path.parts[1:]:
        if not _PART.match(part) or part.endswith((" ", ".")):
            return (f"{text}: a folder's name here may hold letters, digits, spaces and . _ - ( ) only, "
                    "and not end with a space or a dot")
    if drive.upper() == disk.system_drive.upper():
        return (f"{text} is on {drive}, Windows' own drive, which a reset of Windows wipes: choose a folder "
                "on another drive")
    kind = disk.drive_type(f"{drive.upper()}\\")
    if kind != DRIVE_FIXED:
        return (f"{drive} is {_DRIVE_WORDS.get(kind, 'not a fixed disk')}: the database and the engine need a "
                "fixed disk that is always there when Windows starts")
    device = disk.dos_device(drive.upper())
    if device.startswith("\\??\\"):
        return (f"{drive} is a subst drive (a folder of {device[4:]}): choose a folder on the drive itself, "
                "not C:")
    if _inside(path, install_root):
        return (f"{text} is inside Kazma's install folder ({install_root}): Kazma's own file tools work there. "
                "Choose a folder outside it")
    if _inside(path, data_dir):
        return f"{text} is inside Kazma's data folder ({data_dir}): choose a folder outside it"
    if need_bytes:
        free = disk.free_bytes(Path(str(path)))
        if free is not None and free < need_bytes:
            return (f"{drive} has {free / 2**30:.1f} GB free; this needs about {need_bytes / 2**30:.1f} GB "
                    "there")
    return None


# -- the record ----------------------------------------------------------------------------------


def _record_path(path: Path | None = None) -> Path:
    from kazma_core.postgres_service import _record_path as record_path

    return path or record_path()


def _read(path: Path | None = None) -> dict[str, Any]:
    from kazma_core.postgres_service import read_record

    return read_record(_record_path(path))


def _write(data: dict[str, Any], path: Path | None = None) -> None:
    from kazma_core.atomic_files import atomic_write_text

    target = _record_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(data, indent=2))


def chosen(path: Path | None = None) -> Path | None:
    """The data folder the record names, or None (no choice: each part lives
    where its package puts it)."""
    entry = _read(path).get(RECORD_KEY)
    value = entry.get("path") if isinstance(entry, dict) else None
    return Path(str(value)) if isinstance(value, str) and value.strip() else None


def record_choice(folder: str | Path, path: Path | None = None, *, now: float | None = None) -> None:
    """Record *folder* as the data folder (kept by every later run)."""
    data = _read(path)
    previous = data.get(RECORD_KEY) if isinstance(data.get(RECORD_KEY), dict) else {}
    same = str(previous.get("path") or "") == str(folder)
    data.update({"version": 1, RECORD_KEY: {
        "path": str(folder),
        "chosen_at": float(previous.get("chosen_at") or 0.0) if same and previous.get("chosen_at")
        else (time.time() if now is None else now)}})
    _write(data, path)


def journal(path: Path | None = None) -> dict[str, Any]:
    """The move in progress (empty: none)."""
    entry = _read(path).get(JOURNAL_KEY)
    return entry if isinstance(entry, dict) else {}


def write_journal(entry: dict[str, Any], path: Path | None = None, *, now: float | None = None) -> None:
    """The journal written whole, before the stage it names begins. ``since``
    is when the stage began: the guard leaves the part alone for a bounded
    time from it."""
    data = _read(path)
    data[JOURNAL_KEY] = {**entry, "since": time.time() if now is None else now}
    _write(data, path)


def clear_journal(path: Path | None = None) -> None:
    data = _read(path)
    if data.pop(JOURNAL_KEY, None) is not None:
        _write(data, path)


# -- what is kept in it ------------------------------------------------------------------------------


def _cluster_major(data_dir: Path) -> int | None:
    """The major of the Postgres cluster in *data_dir* (its ``PG_VERSION``),
    or None when there is none."""
    try:
        text = (data_dir / "PG_VERSION").read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() else None


def kept_cluster_major(folder: Path | None, prefer: int | None = None) -> int | None:
    """The major of a database kept in the data folder (after a reset of
    Windows: the service is gone, the data is not), *prefer*'s first; None
    when it holds none."""
    if folder is None:
        return None
    layout = Layout(folder)
    if prefer and _cluster_major(layout.postgres_data(prefer)) == prefer:
        return prefer
    found = []
    try:
        for child in folder.iterdir():
            match = re.fullmatch(r"postgres-(\d+)", child.name)
            if match and _cluster_major(child / "data") == int(match.group(1)):
                found.append(int(match.group(1)))
    except OSError:
        return None
    return max(found) if found else None


#: SYSTEM and the administrators: every folder of the data folder.
SYSTEM_AND_ADMINS = ("*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F")


def prepare_steps(layout: Layout, major: int, user: str, *, python: str | None = None) -> list[Any]:
    """The data folder's folders made and restricted, before setup puts
    anything in them: the folder itself (this account too), PostgreSQL's
    (SYSTEM and the administrators only: the installer gives the data folder
    to the service's account), its base backups' (this account too: Kazma
    takes them). The WAL archive's is ``wal_archive.prepare_steps``'. A second
    drive's root lets every signed-in user change files; these hold the whole
    database."""
    import sys

    from kazma_core.postgres_service import Step

    mine = (f"{user}:(OI)(CI)F",) if user else ()
    steps: list[Any] = []
    for title, path, grants in (
            ("Make the data folder (this account, SYSTEM and the administrators only)", layout.root,
             (*SYSTEM_AND_ADMINS, *mine)),
            (f"Make PostgreSQL {major}'s folder in it", layout.postgres(major), SYSTEM_AND_ADMINS),
            ("Make its base backups' folder", layout.base_backups(major), (*SYSTEM_AND_ADMINS, *mine))):
        folder = str(path)
        steps.append(Step(title, (python or sys.executable, "-c",
                                  "import os, sys; os.makedirs(sys.argv[1], exist_ok=True)", folder), admin=True))
        argv: list[str] = ["icacls", folder, "/inheritance:r"]
        for grant in grants:
            argv += ["/grant", grant]
        steps.append(Step("", tuple(argv), admin=True))
    return steps


def same_folder(a: str | Path | None, b: str | Path | None) -> bool:
    """Whether two Windows paths name one folder (case, separators and a
    ``\\\\?\\`` prefix aside). Text only."""
    def flat(value: str | Path | None) -> str:
        text = str(value or "").strip().replace("/", "\\")
        if text.startswith("\\\\?\\"):
            text = text[4:]
        return text.rstrip("\\").lower()
    return bool(a) and bool(b) and flat(a) == flat(b)
