"""One server process owns the local stores, even with a Postgres backend.

An OS-held lock is released on process death without a TTL or stale-owner
guess. This fences shared local volumes; it is not a cross-host lease for NFS.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import BinaryIO

_guard = threading.Lock()
_owners: dict[Path, tuple[BinaryIO, int, int]] = {}


def _reset_fork_guard() -> None:
    """A child cannot reuse a threading lock held by a vanished parent thread."""
    global _guard
    _guard = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_fork_guard)


class RuntimeWriterBusy(RuntimeError):
    """Another server process still owns the local state."""


class RuntimeWriterLease:
    """Reference-counted process ownership of a local data directory."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._held = False
        self._pid = os.getpid()

    def acquire(self) -> None:
        """Acquire before server construction; never steal a live writer's lock."""
        with _guard:
            if self._held and self._pid == os.getpid():
                return
            self._held = False
            self._pid = os.getpid()
            existing = _owners.get(self.root)
            if existing is not None:
                stream, count, owner_pid = existing
                if owner_pid == self._pid:
                    _owners[self.root] = stream, count + 1, owner_pid
                    self._held = True
                    return
                # A fork inherited the parent's descriptor and Python registry.
                # Close only the child's reference, then acquire independently.
                stream.close()
                del _owners[self.root]
            self.root.mkdir(parents=True, exist_ok=True)
            stream = (self.root / ".runtime-writer.lock").open("a+b")
            try:
                if os.name == "nt":
                    import msvcrt

                    if stream.seek(0, os.SEEK_END) == 0:
                        stream.write(b"\0")
                        stream.flush()
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                stream.close()
                raise RuntimeWriterBusy(
                    f"Another Kazma server owns {self.root}. Local stores require one writer; wait for it to stop before failover."
                ) from exc
            _owners[self.root] = stream, 1, self._pid
            self._held = True

    def release(self) -> None:
        """Release only after workers and stores stop; retain the lock file inode."""
        with _guard:
            if not self._held or self._pid != os.getpid():
                return
            stream, count, owner_pid = _owners[self.root]
            self._held = False
            if count > 1:
                _owners[self.root] = stream, count - 1, owner_pid
                return
            del _owners[self.root]
            try:
                if os.name == "nt":
                    import msvcrt

                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            finally:
                stream.close()
