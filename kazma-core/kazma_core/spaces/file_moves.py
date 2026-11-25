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
written into the target's holding folder (``spaces/<target>/moving/<attempt>``,
beside ``files``, which no list and no tool sees). The source is then claimed:
renamed into its own space's holding folder, beside a record of the move. A
write that came before the claim is seen in what was claimed, and one that
comes after it makes a new file. What was claimed is compared with the copy.
The rule is asked again. The copy is put in place without ever replacing
anything: **that is the only commit point, and nothing a person can see in the
target is ever removed by a move.**

**The one ending** (:func:`_end`), for the move itself and for one a stop or a
failure cut off: the claimed bytes go only when the target, still a space,
holds the very same bytes where this move put them; otherwise they go back to
their name, or beside it as "name (2)" when that is taken. Run at every start,
by the maintenance sweep "file moves" and by the next move
(:func:`settle_file_moves`). From the claim to the end the file is in neither
list: a moment, or until the next start after a stop.

**The record is the journal**, a file beside what was claimed
(:data:`_RECORD`), never a store's row: the path in it is a person's words, so
it is in their space's own folder, goes with that space, and is of the same
moment as the files in any backup. A folder ``moving`` never travels (an
export, a bundle for another install).

An erase waits for the moves of its space (:func:`quiet`), after it closed the
space, so that none asked the rule before and places a file after.

Blocking (the file system, SQLite, the identity store): call it off the event
loop.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from kazma_core.spaces import shared_files
from kazma_core.spaces.files import FILES, MOVING, SpaceKindUnknown, space_dir
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
_COPY, _CLAIMED, _RECORD, _RECORD_PART = "copy", "claimed", "move.json", "move.part"
_CHUNK = 1024 * 1024
#: A name given back beside its own: "name (2)" to "name (99)".
_BESIDE = 100
#: The failures a step may have that are no bug of the step: the move is ended
#: and the answer is worded.
_FAILURES = (OSError, sqlite3.Error, IdentityUnavailable, IdentityError, SpaceKindUnknown)


class FileMoveRefused(ValueError):
    """A move that is not the person's to make, or that could not be made;
    ``code`` says which (the page words it)."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


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

    def held(self) -> Path:
        return _holding(self.source, self.attempt) / _CLAIMED

    def copy(self) -> Path:
        return _holding(self.target, self.attempt) / _COPY

    def record(self) -> Path:
        return _holding(self.source, self.attempt) / _RECORD

    def placed_at(self) -> Path:
        return _files_root(self.target).joinpath(*PurePosixPath(self.target_path).parts)


def _holding(space: str, attempt: str) -> Path:
    return space_dir(space) / MOVING / attempt


def _is_attempt(name: str) -> bool:
    return len(name) == 32 and all(c in "0123456789abcdef" for c in name)


def _same_parts(path: str) -> bool:
    return "/".join(_parts(path)) == path


def _read_record(space: str, attempt: str) -> _Move | None:
    """The move *attempt* of *space*, or None: there is no record, or it is
    not one this code wrote."""
    try:
        raw = json.loads((_holding(space, attempt) / _RECORD).read_text(encoding="utf-8"))
        move = _Move(**raw)
        sound = (move.source == space and move.attempt == attempt and valid_space_id(move.target)
                 and all(_same_parts(path) for path in (move.path, move.target_path, *move.made)))
    except FileNotFoundError:
        return None
    except (ValueError, TypeError, AttributeError):
        sound = False
    if not sound:
        logger.warning("[spaces] the record of file move %s cannot be read; its file is given back", attempt)
        return None
    return move


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
    record.parent.mkdir(parents=True, exist_ok=True)
    partial = record.with_name(_RECORD_PART)
    with open(partial, "x", encoding="utf-8") as out:
        json.dump(asdict(move), out)
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


def _take_away(folder: Path) -> None:
    """A holding folder of one move, with what is in it (its own files, by
    name, never what was claimed: nothing else is removed)."""
    for name in (_RECORD, _RECORD_PART, _COPY):
        (folder / name).unlink(missing_ok=True)
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
    for folder in (space_dir(space) / MOVING, _files_root(space), space_dir(space)):
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


def _copy_aside(source: Path, info: os.stat_result, copy: Path, max_bytes: int) -> tuple[str, int]:
    """*source*'s bytes in the target's holding folder, on the disk: their
    SHA-256 and how many. The very file that was checked, by its identity."""
    copy.parent.mkdir(parents=True)
    sha, size = hashlib.sha256(), 0
    with open(source, "rb") as handle:
        if not os.path.samestat(os.fstat(handle.fileno()), info):
            raise _changed()
        with open(copy, "xb") as out:
            while chunk := handle.read(_CHUNK):
                size += len(chunk)
                if size > max_bytes:
                    raise FileMoveRefused("too_large", 413, "That file is larger than Kazma moves.")
                sha.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
    _sync_folder(copy.parent)
    return sha.hexdigest(), size


def _claim(source: Path, info: os.stat_result, move: _Move) -> None:
    """The source leaves its folder for the holding folder, then is compared
    with the copy: from here no write reaches it under its name."""
    held = move.held()
    _again(lambda: os.rename(source, held))
    _sync_folder(source.parent)
    _sync_folder(held.parent)
    now = os.lstat(held)
    if not os.path.samestat(now, info) or now.st_nlink != 1 or _digest(held) != move.sha256:
        raise _changed()


def _place(staged: Path, where: Path) -> bool:
    """*staged* renamed to *where*, or False when that name is taken: nothing
    is ever replaced, and no folder is made."""
    from kazma_core.spaces.space_move import _claim_and_replace

    try:
        if os.name == "nt":
            _again(lambda: os.rename(staged, where))  # Windows never renames over a file
            return True
        if not stat.S_ISREG(os.lstat(staged).st_mode):
            if os.path.lexists(where):
                return False
            os.rename(staged, where)
            return True
        try:
            os.link(staged, where, follow_symlinks=False)
        except OSError as exc:
            if exc.errno not in (errno.EPERM, errno.ENOSYS, errno.EOPNOTSUPP, errno.EMLINK):
                raise
            _claim_and_replace(staged, where)  # a disk with no hard links
            return True
    except FileExistsError:
        return False
    with suppress(OSError):  # the file is in place: its other name goes with the holding folder
        staged.unlink()
    return True


def _put_in_place(move: _Move) -> None:
    """The commit point: the copy gets its name in the target."""
    where = move.placed_at()
    here = _files_root(move.target)
    for part in PurePosixPath(move.target_path).parts[:-1]:
        here = here / part
        if is_linked(here) or not here.is_dir():
            raise _changed()
    try:
        if not _place(move.copy(), where):
            raise _name_taken()
    except FileNotFoundError:
        raise _changed() from None  # its folder went meanwhile
    _sync_folder(where.parent)


def _was_placed(move: _Move) -> bool:
    """Whether the file at the move's place is the copy this move put there:
    the copy left the holding folder, or is the same file. Another person's
    file of the same bytes is not it."""
    try:
        there = os.lstat(move.placed_at())
    except (FileNotFoundError, NotADirectoryError):
        return False
    try:
        return os.path.samestat(os.lstat(move.copy()), there)
    except FileNotFoundError:
        return True


def _give_back(space: str, held: Path, path: str) -> str:
    """What was claimed goes back to *path* of *space*, or beside it when
    that name is taken now. Returns the path it got."""
    root = _files_root(space)
    parts = PurePosixPath(path).parts
    try:
        folder, names, _made = _make_folders(root, parts[:-1])
    except (FileMoveRefused, OSError):
        root.mkdir(parents=True, exist_ok=True)
        folder, names = root, ()  # one of its folders is something else now
    name = PurePosixPath(parts[-1])
    for count in range(1, _BESIDE):
        given = str(name) if count == 1 else f"{name.stem} ({count}){name.suffix}"
        if _place(held, folder / given):
            _sync_folder(folder)
            return "/".join((*names, given))
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
        _again(held.unlink)
        outcome = "moved"
    elif not _is_open(move.source):
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
    failed: Exception | None = None
    try:
        _carry(attempt, parts, source, target, by, max_bytes, recorded)
    except FileMoveRefused as exc:
        failed = exc
    except _FAILURES as exc:
        logger.warning("[spaces] a step of file move %s failed", attempt, exc_info=True)
        failed = _worded(exc)
    if not recorded:
        _forget_copy(target, attempt)
        raise failed or _changed()
    move = recorded[0]
    try:
        outcome = _end(move)
    except _FAILURES:
        logger.warning("[spaces] file move %s is not ended yet; the next settling ends it", attempt, exc_info=True)
        raise FileMoveRefused("unfinished", 503, "The move is not finished; Kazma ends it by itself.") from None
    if outcome != "moved":
        raise failed or _changed()
    return move


def _forget_copy(target: str, attempt: str) -> None:
    try:
        _take_away(_holding(target, attempt))
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
        raise FileMoveRefused("not_yours", 403, "Only the person who shared a file takes it out.")
    over = disk_refusal(target, int(info.st_size))
    if over:
        raise FileMoveRefused("space_limit", 507, over)
    if os.path.lexists(_files_root(target).joinpath(*parts)):
        raise _name_taken()
    sha256, size = _copy_aside(where, info, _holding(target, attempt) / _COPY, max_bytes)
    if maker is not None and maker["sha256"] != sha256:
        raise FileMoveRefused("changed_since", 409, "This file changed after it was shared, so it stays in this space.")
    names, made = _planned(_files_root(target), parts[:-1])
    move = _Move(attempt=attempt, source=source, target=target, path=path, target_path="/".join((*names, parts[-1])),
                 by=by, sha256=sha256, size=size, taken_out=taken_out, made=made,
                 shared_by_attempt=maker["attempt"] if maker else "", shared_at=maker["shared_at"] if maker else 0.0)
    recorded.append(move)
    _write_record(move)
    if _make_folders(_files_root(target), names)[1] != names:
        raise _name_taken()
    _claim(where, info, move)
    if _rule(source, target, by) != taken_out or (maker is not None and shared_files.maker_of(source, path) != maker):
        raise _changed()
    _put_in_place(move)


# ── a move that did not end ──────────────────────────────────────────────


def _attempts() -> list[tuple[str, str]]:
    """``(space, attempt)`` of every holding folder on the disk."""
    found: list[tuple[str, str]] = []
    try:
        with os.scandir(space_dir(OWNER_SPACE_ID).parent) as spaces:
            names = [entry.name for entry in spaces if valid_space_id(entry.name)]
    except FileNotFoundError:
        return found
    for name in names:
        with suppress(FileNotFoundError, NotADirectoryError), os.scandir(space_dir(name) / MOVING) as held:
            found.extend((name, entry.name) for entry in held if _is_attempt(entry.name))
    return found


def _recover(space: str, attempt: str) -> str:
    """What was claimed, with no record that can be read: it goes back into
    its space's folder under a name that says so."""
    held = _holding(space, attempt) / _CLAIMED
    _give_back(space, held, f"moved file {attempt[:8]}")
    _take_away(held.parent)
    return "put_back"


def settle_file_moves() -> list[dict[str, str]]:
    """End every file move a stop or a failure left (:func:`_end`), each on
    its own: one that cannot be ended now is said and tried again. Nothing on
    an install that does not enforce Spaces, and nothing is made. Run at
    start, by the maintenance sweep and by the next move. Blocking."""
    if not spaces_enforced():
        return []
    settled: list[dict[str, str]] = []
    with _settling:
        try:
            found = _attempts()
        except OSError:
            logger.warning("[spaces] the file moves under way could not be read", exc_info=True)
            return []
        copies: list[tuple[str, str]] = []
        for space, attempt in found:
            if _is_live(attempt):
                continue
            try:
                move = _read_record(space, attempt)
                if move is not None:
                    outcome = _end(move)
                elif os.path.lexists(_holding(space, attempt) / _CLAIMED):
                    outcome = _recover(space, attempt) if _is_open(space) else "left"
                else:
                    copies.append((space, attempt))
                    continue
            except _FAILURES:
                logger.warning("[spaces] file move %s could not be ended; it is tried again", attempt, exc_info=True)
                continue
            if outcome != "left":
                settled.append({"attempt": attempt, "outcome": outcome})
                logger.warning("[spaces] a file move that was cut off was ended: %s", outcome)
        _forget_orphans(copies)
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
