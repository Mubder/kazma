"""A release install: Kazma's source tree laid down from a release archive.

The Windows installer (``scripts/windows_installer``) installs Kazma without
git: it lays down the files of the release's source archive
(``kazma-<version>.tar.gz``, the same archive every release signs) and runs
``setup.cmd``. Such a folder is neither a git checkout (``kazma update`` would
pull) nor a wheel install (it would install the next wheel), so it says what it
is in :data:`MARKER` at its root: the release it holds and the files that
release shipped.

``kazma update`` updates it from the next release's archive, once the
archive's SHA-256 and Sigstore signature hold (``kazma_cli.update``):
:func:`apply_archive` replaces the shipped files and removes those the new
release no longer ships, moving every file it replaces or removes into a
backup first, so :func:`rollback` can put the tree back exactly. What the
owner made is never touched: :data:`KEPT` (settings, data, the environment,
logs), and any file the release did not ship.

Standard library only: it runs while the packages it updates may be half way.
"""

from __future__ import annotations

import json
import shutil
import tarfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

#: At the root of a release install: ``{"version": ..., "files": [...]}``.
MARKER = ".kazma-release.json"

#: Never written or removed by an update, at any depth of the root's top level:
#: the owner's settings and secrets, Kazma's data and logs, the environment.
KEPT = frozenset({
    ".env", ".env.local", ".env.production", ".kazma", "kazma-data", ".venv",
    "kazma.local.yaml", MARKER,
})

#: An archive past these is not a Kazma release (it is about 32 MB and 4,000
#: files packed, 90 MB unpacked).
MAX_MEMBERS = 50_000
MAX_UNPACKED_BYTES = 1_500_000_000

#: Where updates stage and keep their backups, inside the install.
UPDATE_DIR = Path(".kazma") / "update"


class ArchiveRefused(Exception):
    """The archive is not one this updater lays down (the reason is the message)."""


@dataclass
class Applied:
    """What :func:`apply_archive` did, enough to undo it."""

    root: Path
    backup: Path
    old_version: str
    new_version: str
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def find_release_root(start: Path) -> Path | None:
    """The release install *start* is inside (the folder holding :data:`MARKER`)."""
    current = start.resolve()
    for _ in range(8):
        if (current / MARKER).is_file():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def read_marker(root: Path) -> dict:
    """The marker's content; ``{}`` when it is missing or unreadable."""
    try:
        data = json.loads((root / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_marker(root: Path, version: str, files: list[str]) -> None:
    """Say what *root* holds: release *version*, which shipped *files*."""
    tmp = root / (MARKER + ".tmp")
    tmp.write_text(json.dumps({"version": version, "files": sorted(files)}, indent=1), encoding="utf-8")
    tmp.replace(root / MARKER)


def _is_kept(rel: str) -> bool:
    return PurePosixPath(rel).parts[0] in KEPT


def shipped_files(archive: Path, version: str) -> list[str]:
    """The files *archive* ships, relative to its one top folder ``kazma-<version>/``.

    Refused (:class:`ArchiveRefused`): another top folder, a member outside it,
    an absolute or ``..`` path, a link, a device or anything but a file or a
    folder, a path inside :data:`KEPT`, more than :data:`MAX_MEMBERS` members
    or :data:`MAX_UNPACKED_BYTES` bytes.
    """
    top = f"kazma-{version}"
    files: list[str] = []
    total = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for count, member in enumerate(tar, start=1):
                if count > MAX_MEMBERS:
                    raise ArchiveRefused(f"more entries than the limit ({MAX_MEMBERS})")
                name = member.name
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name:
                    raise ArchiveRefused(f"a member outside the archive's folder: {name[:120]!r}")
                if not path.parts or path.parts[0] != top:
                    raise ArchiveRefused(f"a member outside {top}/: {name[:120]!r}")
                if member.isdir():
                    continue
                if not member.isreg():
                    raise ArchiveRefused(f"a link or special file: {name[:120]!r}")
                rel = PurePosixPath(*path.parts[1:]).as_posix()
                if not rel or rel == ".":
                    continue
                if _is_kept(rel):
                    raise ArchiveRefused(f"a file where the owner's own files live: {rel[:120]!r}")
                total += member.size
                if total > MAX_UNPACKED_BYTES:
                    raise ArchiveRefused("larger unpacked than any Kazma release")
                files.append(rel)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveRefused(f"not a readable archive ({type(exc).__name__})") from None
    if "pyproject.toml" not in files:
        raise ArchiveRefused("no pyproject.toml: not Kazma's source")
    return files


def _move(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _prune_empty_parents(root: Path, rel: str) -> None:
    parent = (root / rel).parent
    while parent != root and parent.is_dir():
        try:
            parent.rmdir()  # only an empty folder
        except OSError:
            return
        parent = parent.parent


def apply_archive(root: Path, archive: Path, version: str) -> Applied:
    """Lay release *version*'s *archive* over the release install at *root*.

    Unpacks into ``.kazma/update/staging-<version>`` (Python's ``data`` filter
    as well as :func:`shipped_files`' checks), then for each shipped file moves
    the one in place, if any, into ``.kazma/update/backup-<old>-<time>`` and
    moves the new one in; every file the old release shipped and this one does
    not goes to the backup too. The marker is written last. Any failure on the
    way puts back what was moved (:func:`rollback`) and raises.
    """
    root = root.resolve()
    marker = read_marker(root)
    old_version = str(marker.get("version") or "")
    old_files = [f for f in marker.get("files") or [] if isinstance(f, str)]
    new_files = shipped_files(archive, version)

    work = root / UPDATE_DIR
    staging = work / f"staging-{version}"
    backup = work / f"backup-{old_version or 'unknown'}-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(staging, filter="data")
    source = staging / f"kazma-{version}"

    applied = Applied(root=root, backup=backup, old_version=old_version, new_version=version)
    new_set = set(new_files)
    laid_down = False
    try:
        backup.mkdir(parents=True)
        for rel in new_files:
            target = root / rel
            if target.exists():
                _move(target, backup / "files" / rel)
                applied.replaced.append(rel)
            else:
                applied.added.append(rel)
            _move(source / rel, target)
        for rel in old_files:
            if rel in new_set or _is_kept(rel) or PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts:
                continue
            target = root / rel
            if target.is_file():
                _move(target, backup / "files" / rel)
                applied.removed.append(rel)
                _prune_empty_parents(root, rel)
        if (root / MARKER).is_file():
            shutil.copy2(root / MARKER, backup / MARKER)
        _write_journal(applied)
        write_marker(root, version, new_files)
        laid_down = True
    finally:
        # Anything that stopped it half way -- an error, Ctrl+C -- puts the
        # tree back before the error goes on.
        if not laid_down:
            rollback(applied)
        shutil.rmtree(staging, ignore_errors=True)
    return applied


def _write_journal(applied: Applied) -> None:
    (applied.backup / "applied.json").write_text(json.dumps({
        "old_version": applied.old_version, "new_version": applied.new_version,
        "added": applied.added, "replaced": applied.replaced, "removed": applied.removed,
    }, indent=1), encoding="utf-8")


def rollback(applied: Applied) -> None:
    """Put the tree back as :func:`apply_archive` found it: the files it added
    are removed, those it replaced or removed come back, and the old marker."""
    root, files = applied.root, applied.backup / "files"
    for rel in applied.added:
        (root / rel).unlink(missing_ok=True)
        _prune_empty_parents(root, rel)
    for rel in [*applied.replaced, *applied.removed]:
        saved = files / rel
        if saved.exists():
            (root / rel).unlink(missing_ok=True)
            _move(saved, root / rel)
    old_marker = applied.backup / MARKER
    if old_marker.is_file():
        shutil.copy2(old_marker, root / MARKER)


def prune_backups(root: Path, keep: Path | None = None) -> None:
    """Remove every update backup but *keep* (the newest is kept to roll back by hand)."""
    work = root / UPDATE_DIR
    if not work.is_dir():
        return
    for entry in work.iterdir():
        if entry.is_dir() and entry.name.startswith("backup-") and (keep is None or entry != keep):
            shutil.rmtree(entry, ignore_errors=True)
