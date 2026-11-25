"""Moving one's own file between one's own space and a shared one (Spaces
S2-12, piece 7).

"Private means in my personal space": a person moves a file of their own
space's working folder (``<space folder>/files``) into a shared space they are
in ("Share with..."), or takes one they shared back out ("Take out"). Decided
with the main session (23 and 25 November 2026):

* **Which spaces.** The document move's rule (``spaces.moves.move_rule``) and
  both spaces plain: never the owner's space (its files are this machine's own
  workspace) nor a space with a project folder (a repository).
* **Whose file.** ``spaces.shared_files`` records who shared it and its
  SHA-256. Only they take it out, and only while its bytes are the same: a
  file someone changed since is the space's.
* **One regular file**, named as the disk spells it: no folder, no link or
  junction on its path, no second name (a hard link). The upload size limit
  and the target space's disk limit apply. A name taken in the target is
  refused: nothing is ever overwritten.
* The person's click on the Documents page, never the agent's.

**How it moves: copy aside, claim, compare, place, remove.** The copy is
written into the target's holding folder
(``<data dir>/spaces/.moving/<target>/<attempt>``). The source is then claimed:
renamed into its own space's holding folder, beside a record of the move. A
write that came before the claim is seen in what was claimed, and one that
comes after it makes a new file. What was claimed is compared with the copy.
The rule is asked again. The copy is put in place without ever replacing
anything: **that is the only commit point, and nothing a person can see in the
target is ever removed by a move.** A disk that can do neither a rename that
never replaces nor a hard link is refused before the claim.

**The holding folder is no space's** (``spaces.files.moving_dir``): it is
beside the spaces' folders, under a name no space can have, so every door that
refuses another space's folder refuses it, to every space and the owner's
too, and nothing a space's folder is read for (a list, a tool, a count, an
export, a bundle) meets it. It was inside each space's folder at first, and a
space's own file tools read and forged what lay there. What lies there is
still never believed further than its space (:func:`_believed`).

**The one ending** (:func:`_end`), for the move itself and for one a stop or a
failure cut off: the claimed bytes go only when the target, still a space,
holds the very same bytes where this move put them; otherwise they go back to
their name, or beside it as "name (2)" when that is taken. Run at every start,
by the maintenance sweep "file moves" and by the next move
(:func:`settle_file_moves`). From the claim to the end the file is in neither
list: a moment, or until the next start after a stop.

**The record is the journal**, a file beside what was claimed
(:data:`_RECORD`), never a store's row: the path in it is a person's words, so
it goes with its space's erase, and is of the same moment as the files in any
backup.

**What a move does not promise.** POSIX: a program that opened the file before
the claim can still write through its handle. Written before the comparison it
is ``changed_meanwhile``; after it, until the end, the sharer's space keeps
that version beside the one that moved (``kept``); after the end the words go
with the claimed file, as with any deleted one. Windows refuses to claim a
file another program holds open. The bytes, the POSIX permission bits and the
"came from the internet" mark of Windows travel; another stream, an extended
attribute and Windows' read-only bit do not. One process settles at a time:
the server holds the data folder alone (``runtime_ownership``), and nothing
else calls this.

An erase ends what its space's moves left, closes the space, then waits for
the moves under way (:func:`quiet`), so that none asked the rule before and
places a file after.

Blocking (the file system, SQLite, the identity store): call it off the event
loop.
"""

from __future__ import annotations

import ctypes
import errno
import functools
import hashlib
import json
import logging
import os
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any

from kazma_core.spaces import shared_files
from kazma_core.spaces.files import FILES, SpaceKindUnknown, moving_dir, space_dir
from kazma_core.spaces.identity import IdentityError, IdentityUnavailable
from kazma_core.spaces.scope import OWNER_SPACE_ID, spaces_enforced, valid_space_id
from kazma_core.workspace.real_paths import is_linked

__all__ = [
    "AT_ONCE",
    "FileMoveRefused",
    "move_file",
    "quiet",
    "settle_file_moves",
    "sweep",
]

logger = logging.getLogger(__name__)

#: Moves at once in this process: each holds a worker thread for a file's
#: copy. One more is refused, never made to wait.
AT_ONCE = 2
#: What a move keeps in its holding folders: the copy (the target's), and what
#: was claimed with the record of the move (the source's).
_COPY, _COPY_PART, _CLAIMED = "copy", "copy.part", "claimed"
_RECORD, _RECORD_PART = "move.json", "move.part"
_CHUNK = 1024 * 1024
#: A name given back beside its own: "name (2)" to "name (99)".
_BESIDE = 100
#: A record is a few names: anything longer is none this code wrote.
_RECORD_MAX = 1024 * 1024
#: The longest name a disk holds: bytes (POSIX), UTF-16 units (Windows).
_NAME_MAX = 255
#: One settling waits this long for another, which does the same work.
_SETTLING_WAIT_S = 30.0
#: A move that cannot be ended is said once in this time, not at every try.
_SAID_EVERY_S = 600.0


class FileMoveRefused(ValueError):
    """A move that is not the person's to make, or that could not be made;
    ``code`` says which (the page words it)."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


#: attempt -> when it was last said that it cannot be ended.
_said: dict[str, float] = {}


def _say(attempt: str, what: str, *, trouble: bool = True) -> None:
    """Log *what* of the file move *attempt*, once in :data:`_SAID_EVERY_S`:
    a settling runs at every move, and one that cannot be ended stays."""
    now = time.monotonic()
    if now - _said.get(attempt, -_SAID_EVERY_S) < _SAID_EVERY_S:
        logger.debug("[spaces] file move %s %s", attempt, what, exc_info=trouble)
        return
    if len(_said) > 1000:
        _said.clear()
    _said[attempt] = now
    logger.warning("[spaces] file move %s %s", attempt, what, exc_info=trouble)


def _guarded(attempt: str, what: str, step: Callable[[], Any], *, each_time: bool = False) -> tuple[Any, Exception | None]:
    """What *step* answers, or the exception it raised, said as *what*: one
    move never stops another, an erase, an export or a start, whatever it
    meets. *each_time*: said at every failure, a person is waiting for it."""
    try:
        return step(), None
    except Exception as exc:  # noqa: BLE001 -- said here, worded or tried again by the caller
        if each_time and not isinstance(exc, FileMoveRefused):
            _said.pop(attempt, None)
        if not isinstance(exc, FileMoveRefused):
            _say(attempt, what)
        return None, exc


def _changed() -> FileMoveRefused:
    return FileMoveRefused("changed_meanwhile", 409, "The file changed while it was being moved. Try again.")


def _not_found() -> FileMoveRefused:
    return FileMoveRefused("not_found", 404, "No such file in this space.")


def _name_taken() -> FileMoveRefused:
    return FileMoveRefused("name_taken", 409, "That name is already taken in the other space.")


def _worded(exc: Exception) -> FileMoveRefused:
    """What a failed step is called: no full disk, long name or open file is
    an answer without words."""
    code = getattr(exc, "errno", None)
    win = getattr(exc, "winerror", None)
    if code in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)) or win in (39, 112):
        return FileMoveRefused("disk_full", 507, "The disk is full.")
    long_path = win == 3 and len(str(getattr(exc, "filename", "") or "")) >= 248
    if code == errno.ENAMETOOLONG or win in (111, 206) or long_path:
        return FileMoveRefused("name_too_long", 409, "The file's path is too long for the other space.")
    if isinstance(exc, PermissionError) or win == 32 or code in (errno.EBUSY, errno.ETXTBSY):
        return FileMoveRefused("in_use", 409, "The file is open somewhere, or may not be read. Try again later.")
    if isinstance(exc, FileNotFoundError):
        return _not_found()
    if isinstance(exc, (IdentityUnavailable, IdentityError, SpaceKindUnknown)):
        return FileMoveRefused("identity_unavailable", 503, "Accounts cannot be read right now.")
    return FileMoveRefused("move_failed", 503, "The file could not be moved.")


# ── moves under way in this process ──────────────────────────────────────

_changing = threading.Condition()
#: attempt -> (the space it leaves, the space it goes to, the path).
_live: dict[str, tuple[str, str, str]] = {}
#: One settling at a time.
_settling = threading.Lock()


@contextmanager
def _under_way(attempt: str, source: str, target: str, path: str) -> Iterator[None]:
    """The move is one of this process's, from before its rule is asked to
    its end: an erase waits for it, a settling leaves it alone, and a second
    move of the same file is refused."""
    with _changing:
        if any(space == source and held == path for space, _to, held in _live.values()):
            raise FileMoveRefused("move_in_progress", 409, "This file is already being moved.")
        if len(_live) >= AT_ONCE:
            raise FileMoveRefused("busy", 409, "Other files are being moved. Try again in a moment.")
        _live[attempt] = (source, target, path)
    try:
        yield
    finally:
        with _changing:
            _live.pop(attempt, None)
            _changing.notify_all()


def _is_live(attempt: str) -> bool:
    with _changing:
        return attempt in _live


def _moving_spaces() -> set[str]:
    with _changing:
        return {space for source, target, _path in _live.values() for space in (source, target)}


# ── the spaces' rule ─────────────────────────────────────────────────────


def _rule(source: str, target: str, by: str) -> bool:
    """Whether the move takes the file out of a shared space, or a refusal:
    the document move's rule, and both spaces plain."""
    from kazma_core.spaces import files, moves

    refused, taken_out = moves.move_rule(from_space=source, to_space=target, by=by)
    if refused is not None:
        raise FileMoveRefused(refused.code, refused.status, str(refused))
    for space in (source, target):
        if space == OWNER_SPACE_ID:
            raise FileMoveRefused("owners_space", 409, "The owner's space keeps this computer's own files: "
                                                       "they are not moved between spaces.")
        if files.space_kind(space) != "sandboxed":
            raise FileMoveRefused("project_folder", 409, "A space with a project folder keeps a repository: "
                                                         "its files are not moved between spaces.")
    return taken_out


def _is_open(space: str) -> bool:
    """Whether *space* is a plain space of this install that is not being
    erased. Raises when the identity store cannot be read."""
    from kazma_core.spaces.identity import read_registry

    found = read_registry()["spaces"].get(space) or {}
    return found.get("kind") == "sandboxed" and not found.get("erasing_at")


# ── names as the disk spells them ────────────────────────────────────────


def _parts(path: str) -> tuple[str, ...]:
    """The names of *path*, relative to a space's folder. ``not_found`` for a
    path that is not one of the list's; ``bad_name`` for a name that stays."""
    parts = tuple(str(path or "").split("/"))
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        raise _not_found()
    if not shared_files.name_moves(path):
        raise FileMoveRefused("bad_name", 409, "A file of that name stays in its space.")
    return parts


def _spelled(folder: Path, name: str) -> str | None:
    """How *folder* spells the entry *name* names, or None when there is
    none: ``notes`` is ``Notes`` where the disk reads both as one."""
    try:
        wanted = os.lstat(folder / name)
    except (FileNotFoundError, NotADirectoryError):
        return None
    other = None
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.name == name:
                return name
            if entry.name.casefold() == name.casefold() and os.path.samestat(os.lstat(entry.path), wanted):
                other = entry.name
    return other


def _files_root(space: str) -> Path:
    return space_dir(space) / FILES


def _checked_file(root: Path, parts: tuple[str, ...]) -> tuple[Path, os.stat_result]:
    """The regular file *parts* names inside *root*, every name as the disk
    spells it, no link on its way, one name only. ``not_found`` or
    ``not_a_file``."""
    from kazma_core.store_registry import is_kazma_store
    from kazma_core.workspace import folder_identity

    def not_a_file() -> FileMoveRefused:
        return FileMoveRefused("not_a_file", 409, "Only one file of this space moves at a time: never a folder, "
                                                  "a link or a file with a second name.")

    here = root
    if is_linked(root):
        raise not_a_file()
    for part in parts:
        if not here.is_dir() or _spelled(here, part) != part:
            raise _not_found()
        here = here / part
        if is_linked(here):
            raise not_a_file()
    info = os.lstat(here)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise not_a_file()
    if not folder_identity.inside(here, root) or is_kazma_store(str(here)):
        raise not_a_file()
    return here, info


def _planned(root: Path, parts: tuple[str, ...]) -> tuple[tuple[str, ...], list[str]]:
    """How *root* spells the folders *parts* names, and which of them are
    missing: the record names those before they are made, so that a stop
    leaves no folder of a person's naming in another space."""
    here, names, missing = root, [], []
    for part in parts:
        name = _spelled(here, part) if here.is_dir() else None
        if name is None:
            missing.append("/".join((*names, part)))
            name = part
        here = here / name
        names.append(name)
    return tuple(names), missing


def _make_folders(root: Path, parts: tuple[str, ...]) -> tuple[Path, tuple[str, ...], list[str]]:
    """The folder *parts* names inside *root*, made where it is missing: the
    folder, its names as the disk spells them, and the ones made here."""
    root.mkdir(parents=True, exist_ok=True)
    if is_linked(root):
        raise _name_taken()
    here, names, made = root, [], []
    whole = False
    try:
        for part in parts:
            try:
                (here / part).mkdir()
                made.append("/".join((*names, part)))
            except FileExistsError:
                pass  # it is there; what it is comes next
            name = _spelled(here, part)
            if name is None or is_linked(here / name) or not (here / name).is_dir():
                raise _name_taken()
            here = here / name
            names.append(name)
        whole = True
    finally:
        if not whole:
            _remove_empty(root, made)
    return here, tuple(names), made


def _remove_empty(root: Path, made: list[str]) -> None:
    """The folders a move made go, while nothing is in them."""
    for folder in reversed(made):
        with suppress(OSError):  # someone's file is in it now
            root.joinpath(*PurePosixPath(folder).parts).rmdir()


# ── the holding folder ───────────────────────────────────────────────────


@dataclass(frozen=True)
class _Move:
    """One move, as its record keeps it (:data:`_RECORD`)."""

    attempt: str
    source: str
    target: str
    path: str
    target_path: str
    by: str
    sha256: str
    size: int
    taken_out: bool
    #: On a take-out: the move that shared the file, and when.
    shared_by_attempt: str = ""
    shared_at: float = 0.0
    #: The folders this move made in the target, taken away again (while they
    #: are empty) if the file does not stay there.
    made: list[str] = field(default_factory=list)
    #: Which file the copy is and which the source (:func:`_identity`): what
    #: was put in place is known from another person's file of the same bytes.
    copy_id: list[int] = field(default_factory=list)
    source_id: list[int] = field(default_factory=list)

    def held(self) -> Path:
        return _holding(self.source, self.attempt) / _CLAIMED

    def copy(self) -> Path:
        return _holding(self.target, self.attempt) / _COPY

    def record(self) -> Path:
        return _holding(self.source, self.attempt) / _RECORD

    def placed_at(self) -> Path:
        return _files_root(self.target).joinpath(*PurePosixPath(self.target_path).parts)


def _holding(space: str, attempt: str) -> Path:
    return moving_dir(space) / attempt


def _plain(folder: Path) -> bool:
    """Whether *folder* is a folder and no link: the holding folder is never
    looked for where a link leads."""
    return not is_linked(folder) and folder.is_dir()


def _make_holding(folder: Path) -> None:
    """The holding *folder* of a move, made. Nothing is put where a link
    leads, nor when its place is something else."""
    for place in (folder.parent.parent, folder.parent, folder):
        place.mkdir(parents=True, exist_ok=True)
        if not _plain(place):
            raise NotADirectoryError(errno.ENOTDIR, "the holding folder is something else", str(place))


def _is_attempt(name: str) -> bool:
    return len(name) == 32 and all(c in "0123456789abcdef" for c in name)


def _is_path(value: Any) -> bool:
    try:
        return isinstance(value, str) and "/".join(_parts(value)) == value
    except FileMoveRefused:
        return False


def _identity(info: os.stat_result) -> list[int]:
    """Which file this is on this disk, through a rename: its number and when
    it was written (a number is given again to a later file). Not the disk's
    own number, which a restart may change."""
    return [int(info.st_ino), int(info.st_mtime_ns)]


#: What each field of a record is.
_FIELDS: dict[str, Callable[[Any], bool]] = {
    "attempt": lambda v: isinstance(v, str) and _is_attempt(v),
    "source": valid_space_id,
    "target": valid_space_id,
    "path": _is_path,
    "target_path": _is_path,
    "by": lambda v: isinstance(v, str) and 0 < len(v) <= 256,
    "sha256": lambda v: isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdef" for c in v),
    "size": lambda v: type(v) is int and v >= 0,
    "taken_out": lambda v: isinstance(v, bool),
    "shared_by_attempt": lambda v: isinstance(v, str) and (v == "" or _is_attempt(v)),
    "shared_at": lambda v: type(v) in (int, float) and 0 <= v < 1e12,
    "made": lambda v: isinstance(v, list) and all(_is_path(folder) for folder in v),
    "copy_id": lambda v: isinstance(v, list) and all(type(n) is int for n in v),
    "source_id": lambda v: isinstance(v, list) and all(type(n) is int for n in v),
}


def _read_record(space: str, attempt: str) -> _Move | None:
    """The move *attempt* of *space*, or None: there is no record, or it is
    not one this code wrote. Raises when it cannot be read now, or who the
    spaces' people are cannot be."""
    try:
        with open(_holding(space, attempt) / _RECORD, "rb") as record:
            text = record.read(_RECORD_MAX + 1)
    except FileNotFoundError:
        return None
    try:
        raw = json.loads(text) if len(text) <= _RECORD_MAX else None
    except (ValueError, RecursionError):
        raw = None
    if isinstance(raw, dict) and raw.keys() == _FIELDS.keys() and all(ok(raw[name]) for name, ok in _FIELDS.items()):
        move = _Move(**raw)
        if move.source == space and move.attempt == attempt and _believed(move):
            return move
    _say(attempt, "has a record that is none of Kazma's; its file is given back", trouble=False)
    return None


def _believed(move: _Move) -> bool:
    """Whether the record is of a move there can be: from the person's own
    space to a shared one, or back. Nothing is ever placed, removed or
    recorded in a space it merely names. A space that is gone says nothing,
    and nothing is done in it."""
    from kazma_core.spaces.identity import read_registry

    spaces = read_registry()["spaces"]
    own, shared = (move.target, move.source) if move.taken_out else (move.source, move.target)
    if own == shared or OWNER_SPACE_ID in (own, shared):
        return False
    own_space, shared_space = spaces.get(own), spaces.get(shared)
    if not own_space and not shared_space:
        return False
    return ((not own_space or own_space.get("personal_of") == move.by)
            and (not shared_space or not shared_space.get("personal_of")))


def _sync_folder(folder: Path) -> None:
    """A rename in *folder* is on the disk before what comes after it. POSIX:
    Windows' file system keeps renames in order itself."""
    if os.name == "nt":
        return
    with suppress(OSError):  # a file system that cannot sync a folder
        handle = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(handle)
        finally:
            os.close(handle)


def _write_record(move: _Move) -> None:
    record = move.record()
    _make_holding(record.parent)
    text = json.dumps(asdict(move)).encode("utf-8")
    if len(text) > _RECORD_MAX:
        raise OSError(errno.ENAMETOOLONG, "the file's path is too long to record")
    partial = record.with_name(_RECORD_PART)
    with open(partial, "wb") as out:
        out.write(text)
        out.flush()
        os.fsync(out.fileno())
    os.replace(partial, record)
    _sync_folder(record.parent)


def _again(step: Callable[[], Any]) -> Any:
    """*step*, tried a few times while another program (a virus scanner, the
    search index) holds the file for a moment, as Windows lets it."""
    for left in (4, 3, 2, 1, 0):
        try:
            return step()
        except PermissionError:
            if os.name != "nt" or not left:
                raise
            time.sleep(0.05)
    return None


def _unlink(path: Path) -> None:
    """*path* goes: a read-only file too, which Windows does not delete."""

    def gone() -> None:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            if os.name != "nt" or os.lstat(path).st_mode & stat.S_IWRITE:
                raise
            os.chmod(path, stat.S_IWRITE)
            path.unlink(missing_ok=True)

    _again(gone)


def _take_away(folder: Path) -> None:
    """A holding folder of one move, with what is in it (its own files, by
    name, never what was claimed: nothing else is removed)."""
    if not _plain(folder):
        return
    for name in (_RECORD, _RECORD_PART, _COPY, _COPY_PART):
        _unlink(folder / name)
    with suppress(OSError):  # not empty: something is held there
        folder.rmdir()


def _tidy_gone(space: str) -> None:
    """A space erased while a move made folders in it stays erased: its empty
    folders go. Anything else is left alone."""
    try:
        if _is_open(space):
            return
    except (IdentityUnavailable, IdentityError):
        return
    for folder in (moving_dir(space), _files_root(space), space_dir(space)):
        with suppress(OSError):  # not empty, or not there
            folder.rmdir()


# ── the steps ────────────────────────────────────────────────────────────


def _digest(path: Path) -> str | None:
    """The SHA-256 of the regular file *path* is, or None when it is none."""
    try:
        info = os.lstat(path)
        if is_linked(path) or not stat.S_ISREG(info.st_mode):
            return None
        sha = hashlib.sha256()
        with open(path, "rb") as handle:
            while chunk := handle.read(_CHUNK):
                sha.update(chunk)
    except (FileNotFoundError, NotADirectoryError):
        return None
    return sha.hexdigest()


def _copy_aside(source: Path, info: os.stat_result, aside: Path, copy: Path, max_bytes: int) -> tuple[str, int]:
    """*source*'s bytes in the target's holding folder, on the disk: their
    SHA-256 and how many. The very file that was checked, by its identity.
    It is written in the source's holding folder (*aside*) and goes to the
    target's as the file will go to its place (:func:`_place`): a disk that
    cannot place without replacing, or that forgets which file it is in a
    rename, is refused here, before anything is claimed."""
    for folder in (aside, copy.parent):
        _make_holding(folder)
    partial = aside / _COPY_PART
    sha, size = hashlib.sha256(), 0
    with open(source, "rb") as handle:
        if not os.path.samestat(os.fstat(handle.fileno()), info):
            raise _changed()
        with open(partial, "xb") as out:
            while chunk := handle.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise FileMoveRefused("too_large", 413, "That file is larger than Kazma moves.")
                sha.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    if os.name == "nt":
        _copy_web_mark(source, partial)
    else:
        os.chmod(partial, stat.S_IMODE(info.st_mode) & 0o777)
    which = _identity(os.lstat(partial))
    if not _place(partial, copy) or _identity(os.lstat(copy)) != which:
        raise OSError(errno.EOPNOTSUPP, "this disk does not say which file is which after a rename")
    _sync_folder(copy.parent)
    return sha.hexdigest(), size


def _copy_web_mark(source: Path, copy: Path) -> None:
    """Windows' "came from the internet" mark: what it asks before it opens
    a downloaded file."""
    with suppress(OSError):  # no mark, or a disk that keeps none
        with open(f"{source}:Zone.Identifier", "rb") as mark, open(f"{copy}:Zone.Identifier", "wb") as out:
            out.write(mark.read(64 * 1024))


def _claim(source: Path, info: os.stat_result, move: _Move) -> None:
    """The source leaves its folder for the holding folder, then is compared
    with the copy: from here no write reaches it under its name. What is not
    the file that was checked goes back by the same path (a folder of it may
    lead elsewhere since)."""
    held = move.held()
    _again(lambda: os.rename(source, held))
    _sync_folder(source.parent)
    _sync_folder(held.parent)
    now = os.lstat(held)
    if not os.path.samestat(now, info) or now.st_nlink != 1 or _digest(held) != move.sha256:
        _put_at(held, source)
        raise _changed()


@functools.lru_cache(maxsize=1)
def _rename_call() -> Callable[[bytes, bytes], int] | None:
    """The system's rename that never replaces: Linux ``renameat2`` with
    RENAME_NOREPLACE, macOS ``renamex_np`` with RENAME_EXCL. None elsewhere."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except (OSError, TypeError):
        return None
    if hasattr(libc, "renameat2"):
        return lambda old, new: int(libc.renameat2(-100, old, -100, new, ctypes.c_uint(1)))
    if hasattr(libc, "renamex_np"):
        return lambda old, new: int(libc.renamex_np(old, new, ctypes.c_uint(4)))
    return None


#: "Not on this disk", of a rename that never replaces and of a hard link.
_NOT_HERE = frozenset({errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.EPERM, errno.EMLINK,
                       getattr(errno, "ENOTSUP", errno.EOPNOTSUPP)})


def _renamed_new(staged: Path, where: Path) -> bool | None:
    """*staged* renamed to *where* in one step, never over a file: True, or
    False when the name is taken. None: not on this system or this disk."""
    call = _rename_call()
    if call is None:
        return None
    if call(os.fsencode(staged), os.fsencode(where)) == 0:
        return True
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        return False
    if code in _NOT_HERE:
        return None
    raise OSError(code, os.strerror(code), str(staged), None, str(where))


def _linked_new(staged: Path, where: Path) -> None:
    """*staged* gets *where* as a second name, then loses its own. Cut off
    between, it is one file with two names: an ending takes the first away
    (:func:`_put_at`, :func:`_take_away`)."""
    os.link(staged, where, follow_symlinks=False)
    staged.unlink()


def _place(staged: Path, where: Path) -> bool:
    """*staged* renamed to *where*, or False when that name is taken: nothing
    is ever replaced, and no folder is made. ``OSError`` when the disk can do
    neither a rename that never replaces nor a hard link: an empty file made
    to claim the name, then replaced, could be another program's words by
    then."""
    try:
        if os.name == "nt":
            _again(lambda: os.rename(staged, where))  # Windows never renames over a file
            return True
        done = _renamed_new(staged, where)
        if done is not None:
            return done
        if stat.S_ISDIR(os.lstat(staged).st_mode):  # never a file of a move's: what was swapped for one
            if os.path.lexists(where):
                return False
            os.rename(staged, where)
            return True
        try:
            _linked_new(staged, where)
        except OSError as exc:
            if exc.errno not in _NOT_HERE or isinstance(exc, FileExistsError):
                raise
            raise OSError(errno.EOPNOTSUPP, "this disk cannot place a file without replacing one") from exc
    except FileExistsError:
        return False
    return True


def _put_at(held: Path, where: Path) -> bool:
    """*held* gets the name *where*, or False when another file has it. It
    may have it already (:func:`_linked_new` cut off): its held name goes."""
    try:
        if os.path.samestat(os.lstat(where), os.lstat(held)):
            held.unlink()
            return True
    except (FileNotFoundError, NotADirectoryError):
        pass
    return _place(held, where)


def _no_link_on(root: Path, path: str) -> bool:
    """Whether every folder of *path* inside *root* is a folder, none a link:
    a file there is the space's own."""
    here = root
    if not _plain(here):
        return False
    for part in PurePosixPath(path).parts[:-1]:
        here = here / part
        if not _plain(here):
            return False
    return True


def _put_in_place(move: _Move) -> None:
    """The commit point: the copy gets its name in the target."""
    where, root = move.placed_at(), _files_root(move.target)
    if not _no_link_on(root, move.target_path):
        raise _changed()
    try:
        if not _place(move.copy(), where):
            raise _name_taken()
    except FileNotFoundError:
        raise _changed() from None  # its folder went meanwhile
    if not _no_link_on(root, move.target_path):
        # A folder of it became a link in that moment, and the copy is where
        # it leads: it comes back the same way.
        with suppress(OSError):
            if _identity(os.lstat(where)) == move.copy_id:
                _place(where, move.copy())
        raise _changed()
    _sync_folder(where.parent)


def _was_placed(move: _Move) -> bool:
    """Whether the file at the move's place is the copy this move put there,
    by which file it is: another person's of the same bytes is not it, and a
    copy that is gone proves nothing (a virus scanner takes one away). Files
    that came back from a backup are other files on the disk, the held one
    too: then the rows written when it was placed answer."""
    if not _no_link_on(_files_root(move.target), move.target_path):
        return False
    try:
        there = os.lstat(move.placed_at())
    except (FileNotFoundError, NotADirectoryError):
        return False
    if _identity(there) == move.copy_id:
        return True
    try:
        restored = _identity(os.lstat(move.held())) != move.source_id
    except FileNotFoundError:
        restored = True
    return restored and shared_files.was_recorded(asdict(move))


def _units(name: str) -> int:
    return len(os.fsencode(name)) if os.name != "nt" else len(name.encode("utf-16-le", "surrogatepass")) // 2


def _beside(name: str, count: int) -> str:
    """*name*, or "name (2)" beside it: never longer than a disk holds."""
    if count == 1:
        return name
    part = PurePosixPath(name)
    stem, end = part.stem, f" ({count}){part.suffix}"
    while len(stem) > 1 and _units(stem + end) > _NAME_MAX:
        stem = stem[:-1]
    return stem + end


def _too_long(exc: OSError) -> bool:
    return exc.errno == errno.ENAMETOOLONG or getattr(exc, "winerror", None) in (3, 111, 123, 206)


def _give_back(space: str, held: Path, path: str) -> str:
    """What was claimed goes back to *path* of *space*, or beside it when
    that name is taken now; under a short name in the space's folder when the
    one beside it would be too long. Returns the path it got."""
    root = _files_root(space)
    parts = PurePosixPath(path).parts
    try:
        folder, names, _made = _make_folders(root, parts[:-1])
    except (FileMoveRefused, OSError):
        root.mkdir(parents=True, exist_ok=True)
        folder, names = root, ()  # one of its folders is something else now
    short = f"moved file {held.parent.name[:8]}"
    for place, inside, name in ((folder, names, parts[-1]), (root, (), short)):
        for count in range(1, _BESIDE):
            given = _beside(name, count)
            try:
                put = _put_at(held, place / given)
            except OSError as exc:
                if name is short or not _too_long(exc):
                    raise
                break
            if put:
                _sync_folder(place)
                return "/".join((*inside, given))
    raise OSError(errno.EEXIST, "no free name to give a file back under")


# ── the one ending ───────────────────────────────────────────────────────


def _target_has_it(move: _Move) -> bool:
    """Whether the target, still a space, holds the moved bytes where this
    move put them."""
    return _is_open(move.target) and _was_placed(move) and _digest(move.placed_at()) == move.sha256


def _end(move: _Move) -> str:
    """End *move*, whatever step it reached. ``moved``: the target holds it
    and the source is gone. ``put_back``: it is where it was. ``kept``: both
    hold a version, the source's written while it moved. ``left``: its space
    is being erased, and what was claimed goes with it. Raises when a step
    cannot be done now; run again, it ends the same."""
    held = move.held()
    here = _target_has_it(move)
    if here:
        _sync_folder(move.placed_at().parent)
        shared_files.record_move(asdict(move))
    else:
        shared_files.undo_move(asdict(move))
    if not os.path.lexists(held):
        outcome = "moved" if here else "put_back"
    elif here and _digest(held) == move.sha256 and _is_open(move.target):
        _unlink(held)
        outcome = "moved"
    elif not _is_open(move.source):
        # Its space is being erased: what was claimed is left to that. The
        # target keeps nothing of a file that did not move; the record stays
        # with the claimed bytes, which a space opened again gets back.
        _take_away(_holding(move.target, move.attempt))
        if not here:
            _remove_empty(_files_root(move.target), move.made)
            _tidy_gone(move.target)
        if os.name == "nt":
            with suppress(OSError):  # a read-only file would stop the erase
                os.chmod(held, stat.S_IWRITE)
        _say(move.attempt, "was cut off and its space is being erased; its file goes with that space", trouble=False)
        return "left"
    else:
        if here and not _is_open(move.target):  # closed for its erase since
            shared_files.undo_move(asdict(move))
            here = False
        given = _give_back(move.source, held, move.path)
        outcome = "kept" if here else "put_back"
        if given != move.path:
            logger.warning("[spaces] the file of move %s went back beside its name, which was taken", move.attempt)
    _drop(move, stays=here)
    return outcome


def _drop(move: _Move, *, stays: bool) -> None:
    """What the move kept goes: the record first (a copy with no record is
    never taken for the file), then the copy, then the folders it made in the
    target while they are empty."""
    _take_away(_holding(move.source, move.attempt))
    _take_away(_holding(move.target, move.attempt))
    if stays:
        return
    _remove_empty(_files_root(move.target), move.made)
    _tidy_gone(move.target)


# ── the move ─────────────────────────────────────────────────────────────


def move_file(path: str, *, from_space: str, to_space: str, by: str, max_bytes: int) -> dict[str, Any]:
    """Move *by*'s own file *path* (relative to *from_space*'s folder) to the
    same place in *to_space*'s folder. Raises :class:`FileMoveRefused`: the
    file is where it was, unless its code says otherwise."""
    _rule(from_space, to_space, by)
    parts = _parts(path)
    settle_file_moves()
    attempt = uuid.uuid4().hex
    with _under_way(attempt, from_space, to_space, path):
        moved = _move(attempt, parts, from_space, to_space, by, max_bytes)
    logger.info("[spaces] %s moved a file from %s to %s (taken out: %s)", by, from_space, to_space, moved.taken_out)
    return {"path": moved.target_path, "space": to_space, "taken_out": moved.taken_out}


def _move(attempt: str, parts: tuple[str, ...], source: str, target: str, by: str, max_bytes: int) -> _Move:
    """The move itself, and its ending whatever happened."""
    recorded: list[_Move] = []
    _none, failed = _guarded(attempt, "has a step that failed",
                             lambda: _carry(attempt, parts, source, target, by, max_bytes, recorded), each_time=True)
    if failed is not None and not isinstance(failed, FileMoveRefused):
        failed = _worded(failed)
    if not recorded:
        _forget_copy(source, target, attempt)
        raise failed or _changed()
    move = recorded[0]
    outcome, unended = _guarded(attempt, "is not ended yet; the next settling ends it", lambda: _end(move), each_time=True)
    if unended is not None:
        raise FileMoveRefused("unfinished", 503, "The move is not finished; Kazma ends it by itself.") from None
    if outcome != "moved":
        raise failed or _changed()
    return move


def _forget_copy(source: str, target: str, attempt: str) -> None:
    try:
        for space in (source, target):
            _take_away(_holding(space, attempt))
        _tidy_gone(target)
    except OSError:
        logger.info("[spaces] the copy of file move %s stays until the next settling", attempt, exc_info=True)


def _carry(attempt: str, parts: tuple[str, ...], source: str, target: str, by: str, max_bytes: int,
           recorded: list[_Move]) -> None:
    """Every step up to the commit point. *recorded* gets the move before its
    record is written: from there its ending decides."""
    from kazma_core.spaces.space_limits import disk_refusal

    taken_out = _rule(source, target, by)
    where, info = _checked_file(_files_root(source), parts)
    if info.st_size > max_bytes:
        raise FileMoveRefused("too_large", 413, "That file is larger than Kazma moves.")
    path = "/".join(parts)
    maker = shared_files.maker_of(source, path) if taken_out else None
    if taken_out and (maker is None or maker["maker"] != by):
        raise FileMoveRefused("not_the_sharer", 403, "Only the person who shared a file takes it out.")
    over = disk_refusal(target, int(info.st_size))
    if over:
        raise FileMoveRefused("space_limit", 507, over)
    if os.path.lexists(_files_root(target).joinpath(*parts)):
        raise _name_taken()
    copy = _holding(target, attempt) / _COPY
    sha256, size = _copy_aside(where, info, _holding(source, attempt), copy, max_bytes)
    if maker is not None and maker["sha256"] != sha256:
        raise FileMoveRefused("changed_since", 409, "This file changed after it was shared, so it stays in this space.")
    names, made = _planned(_files_root(target), parts[:-1])
    move = _Move(attempt=attempt, source=source, target=target, path=path, target_path="/".join((*names, parts[-1])),
                 by=by, sha256=sha256, size=size, taken_out=taken_out, made=made,
                 copy_id=_identity(os.lstat(copy)), source_id=_identity(info),
                 shared_by_attempt=maker["attempt"] if maker else "", shared_at=maker["shared_at"] if maker else 0.0)
    recorded.append(move)
    _write_record(move)
    try:
        _folder, spelled, made = _make_folders(_files_root(target), names)
    except (FileMoveRefused, OSError):
        recorded[0] = replace(move, made=[])  # it took away what it made
        raise
    # Only what this move made is ever taken away: a folder of the same name
    # another member made in between is theirs.
    move = recorded[0] = replace(move, made=made)
    _write_record(move)
    if spelled != names:
        raise _name_taken()
    _claim(where, info, move)
    if _rule(source, target, by) != taken_out or (maker is not None and shared_files.maker_of(source, path) != maker):
        raise _changed()
    _put_in_place(move)


# ── a move that did not end ──────────────────────────────────────────────


def _attempts() -> list[tuple[str, str]]:
    """``(space, attempt)`` of every holding folder on the disk, none of them
    a link."""
    root = moving_dir(OWNER_SPACE_ID).parent
    if not os.path.lexists(root):
        return []
    if not _plain(root):
        _say("(any)", f"cannot be looked for: {root} is not a folder of Kazma's", trouble=False)
        return []
    found: list[tuple[str, str]] = []
    with os.scandir(root) as spaces:
        names = sorted(entry.name for entry in spaces if valid_space_id(entry.name) and _plain(Path(entry.path)))
    for name in names:
        with suppress(FileNotFoundError), os.scandir(moving_dir(name)) as held:
            found.extend((name, entry.name) for entry in held
                         if _is_attempt(entry.name) and _plain(Path(entry.path)))
    return found


def _recover(space: str, attempt: str) -> str:
    """What was claimed, with no record that can be read: it goes back into
    its space's folder under a name that says so."""
    held = _holding(space, attempt) / _CLAIMED
    _give_back(space, held, f"moved file {attempt[:8]}")
    _take_away(held.parent)
    return "put_back"


def _settle_one(space: str, attempt: str) -> str:
    """How the holding folder *attempt* of *space* was ended: an outcome of
    :func:`_end`, or ``a copy`` (nothing but a copy, or nothing, is in it)."""
    move = _read_record(space, attempt)
    if move is not None:
        return _end(move)
    if not os.path.lexists(_holding(space, attempt) / _CLAIMED):
        return "a copy"
    if not _is_open(space):
        _say(attempt, "is held for a space that is being erased or gone", trouble=False)
        return "left"
    return _recover(space, attempt)


def settle_file_moves(*, leaving: str | None = None) -> list[dict[str, str]]:
    """End every file move a stop or a failure left (:func:`_end`), each on
    its own: one that cannot be ended now is said and tried again. Nothing on
    an install that does not enforce Spaces, and nothing is made. Run at
    start, by the maintenance sweep and by the next move; with *leaving*, only
    the moves out of that space (before it is closed for its erase, while a
    file that did not move can still go back to it). Blocking."""
    if not spaces_enforced():
        return []
    settled: list[dict[str, str]] = []
    if not _settling.acquire(timeout=_SETTLING_WAIT_S):
        logger.warning("[spaces] the file moves cut off are being ended elsewhere; not waited for any longer")
        return []
    try:
        try:
            found = _attempts()
        except OSError:
            logger.warning("[spaces] the file moves under way could not be read", exc_info=True)
            return []
        copies: list[tuple[str, str]] = []
        for space, attempt in found:
            if _is_live(attempt) or leaving not in (None, space):
                continue
            outcome, failed = _guarded(attempt, "could not be ended; it is tried again",
                                       functools.partial(_settle_one, space, attempt))
            if failed is not None or outcome == "left":
                continue  # said, or its space is being erased
            if outcome == "a copy":
                copies.append((space, attempt))
                continue
            _said.pop(attempt, None)
            settled.append({"attempt": attempt, "outcome": outcome})
            logger.warning("[spaces] a file move that was cut off was ended: %s", outcome)
        if leaving is None:
            _forget_orphans(copies)
    finally:
        _settling.release()
    return settled


def _forget_orphans(copies: list[tuple[str, str]]) -> None:
    """A copy whose move has no record (it stopped before its claim, or it is
    ended) goes: the file itself is elsewhere."""
    if not copies:
        return
    try:
        waiting = {attempt for space, attempt in _attempts()
                   if any(os.path.lexists(_holding(space, attempt) / name) for name in (_RECORD, _CLAIMED))}
    except OSError:
        return
    for space, attempt in copies:
        if attempt in waiting or _is_live(attempt):
            continue
        try:
            _take_away(_holding(space, attempt))
        except OSError:
            logger.info("[spaces] the copy of file move %s stays until the next settling", attempt, exc_info=True)


def quiet(space: str, *, timeout: float = 60.0) -> None:
    """Wait for the moves of *space* under way here, then end what a stop
    left: before its files are exported, and after it was closed for its
    erase (a move that asked the rule before is over; one that asks it now is
    refused)."""
    with _changing:
        over = _changing.wait_for(
            lambda: not any(space in (source, target) for source, target, _path in _live.values()), timeout)
    if not over:
        logger.warning("[spaces] a file move of space %s is still under way", space)
    settle_file_moves()


def sweep() -> None:
    """The maintenance sweep "file moves": end what is left, and forget the
    makers of files that are gone."""
    if not spaces_enforced():
        return
    settle_file_moves()
    try:
        waiting = {space for space, _attempt in _attempts()}
    except OSError:
        return
    shared_files.forget_gone(waiting | _moving_spaces())
