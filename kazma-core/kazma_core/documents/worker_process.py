"""Whether this process is a document worker.

Every worker's entry point (the parser, mutation and renderer workers, run as
``python -m`` in the local profile, and the worker image's ``worker_entry``)
calls :func:`mark` first. Inside a worker the engines are the worker's own,
whatever Kazma's document worker profile is, and a worker that has just
opened an untrusted document must not open Kazma's settings store to find out
which profile that is (``worker_container.worker_isolation_mode``). The local
profile scrubs the worker's environment, so such a read would reach the
default data folder's store.

A module of its own, with nothing to import: the workers load it before
anything else.
"""

from __future__ import annotations

__all__ = ["is_worker", "mark"]

_worker = False


def mark() -> None:
    """This process is a document worker, for the rest of its life."""

    global _worker
    _worker = True


def is_worker() -> bool:
    return _worker
