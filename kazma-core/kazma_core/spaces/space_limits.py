"""Limits per space (Spaces S2-08).

One space at its limit does not slow or stop another. Each space has its own
limits, beside the install's own (which still bound the whole install):

* **disk** (``disk_bytes``): the space's own folder, ``spaces/<id>/``
  (``spaces.files``) -- its working files, uploads, images, documents,
  exports. The owner's space has no folder of its own (it keeps the
  install's), so it has no disk limit;
* **documents** (``documents_bytes``): the content of the space's
  documents, as the documents store counts it (each blob once). Until a
  limit is set for the space or for every space, the install's one value
  for every tenant (``documents.quotas.tenant_bytes``), as before;
* **knowledge** (``knowledge_bytes``): the text of the space's libraries'
  active chunks. Until one is set, the share S1-13 shipped (256 MiB);
* **work running at once**: Knowledge crawls (``crawls``), research runs
  (``research``) and swarm tasks (``swarm``) -- a space's share of the
  install's slots, so one space's work never takes them all -- and its
  turns (``turns``) and scheduled jobs (``scheduled``, S4-04). In a shared
  space each person holds at most half its share (``spaces.fairness``).

Whose limits
------------
A space's own (``identity`` ``spaces[id]["limits"]``), each limit that is
set; else the limits for spaces (``identity`` ``defaults["space_limits"]``);
else :data:`SHIPPED`. The admin sets both on the People page.

Only where Spaces is enforced: an install that does not enforce it reads
nothing here and is bounded by the install's own limits, as before. A
check is an admission check, as the install's quotas are: work admitted
under the limit may carry its space past it, and the next is refused.

Blocking (the identity store, a folder's size): async callers use
``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from kazma_core.spaces.scope import OWNER_SPACE_ID, spaces_enforced

__all__ = [
    "LIMIT_NAMES",
    "RUNNING_KINDS",
    "SHIPPED",
    "LimitsRefused",
    "SpaceLimits",
    "SpaceOverLimit",
    "disk_refusal",
    "documents_bytes",
    "knowledge_bytes",
    "limits_for_spaces",
    "parse_limits",
    "refuse_write_for_disk",
    "running_refusal",
    "share_refusal",
    "usage_of",
]

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024
_GIB = 1024 * _MIB


@dataclass(frozen=True)
class SpaceLimits:
    """A space's limits: bytes for its folder, documents and libraries, and
    how many of its crawls, research runs and swarm tasks run at once."""

    disk_bytes: int
    documents_bytes: int
    knowledge_bytes: int
    crawls: int
    research: int
    swarm: int
    #: Turns running at once (Spaces S4-04, ``spaces.fairness.turn_slots``).
    turns: int
    #: Scheduled jobs firing at once (S4-04, ``fairness.scheduled_slots``).
    scheduled: int

    def as_dict(self) -> dict[str, int]:
        return {f.name: int(getattr(self, f.name)) for f in fields(self)}


#: The names of the limits, in the order the People page shows them.
LIMIT_NAMES: tuple[str, ...] = tuple(f.name for f in fields(SpaceLimits))
#: The kinds of work a space runs at once, each a share of the install's.
RUNNING_KINDS: tuple[str, ...] = ("crawls", "research", "swarm", "turns", "scheduled")


def _shipped() -> SpaceLimits:
    """Kazma's own limits for a space: 2 GiB of files, and half of each of
    the install's slots -- two of its four crawls, two of its four research
    runs, five of its ten swarm tasks, two of the scheduler's four fires --
    so two spaces at their limit still leave none waiting on the other; and
    eight turns at once (the install has no cap of its own on turns: a
    space's is what keeps one from crowding the model provider for all). Documents and knowledge follow the
    install's own values until a limit is set for a space or for every space
    (:func:`documents_bytes`, :func:`knowledge_bytes`): the documents value
    for each tenant (``documents.quotas.tenant_bytes``, 10 GiB unless
    changed) and the knowledge share S1-13 shipped (256 MiB), which are the
    numbers here."""
    return SpaceLimits(disk_bytes=2 * _GIB, documents_bytes=10 * _GIB, knowledge_bytes=256 * _MIB,
                       crawls=2, research=2, swarm=5, turns=8, scheduled=2)


#: The limits for a space when the admin has set none.
SHIPPED = _shipped()

#: The most each limit may be set to: bytes up to 1 TiB, work up to 100 at once.
_MAX = {"disk_bytes": 1024 * _GIB, "documents_bytes": 1024 * _GIB, "knowledge_bytes": 1024 * _GIB,
        "crawls": 100, "research": 100, "swarm": 100, "turns": 100, "scheduled": 100}


class LimitsRefused(ValueError):
    """Limits that are not limits; nothing was changed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SpaceOverLimit(Exception):  # noqa: N818 -- named for the state, as the stores' own are
    """A space at one of its limits (:func:`running_refusal`,
    :func:`disk_refusal`): ``code`` ``space_limit``, with the limit's name."""

    code = "space_limit"

    def __init__(self, limit: str, message: str) -> None:
        super().__init__(message)
        self.limit = limit


def parse_limits(raw: Any) -> dict[str, int]:
    """The limits set in *raw* (``{name: value}``; a value that is None or
    empty is not set). Raises :class:`LimitsRefused`: an unknown name, a
    value that is not a whole number from 1 to its most."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise LimitsRefused("bad_limits", "Limits are a number for each limit.")
    out: dict[str, int] = {}
    for name, value in raw.items():
        if name not in LIMIT_NAMES:
            raise LimitsRefused("unknown_limit", f"There is no limit called {name!r}.")
        if value is None or str(value).strip() == "":
            continue
        try:
            if isinstance(value, bool):
                raise ValueError
            number = int(str(value).strip())
        except (TypeError, ValueError):
            raise LimitsRefused("bad_limit", f"{name} is a whole number.") from None
        if not 1 <= number <= _MAX[name]:
            raise LimitsRefused("bad_limit", f"{name} is from 1 to {_MAX[name]}.")
        out[name] = number
    return out


def _set_in(raw: Any) -> dict[str, int]:
    """The valid limits a stored mapping sets; a value stored wrong is
    left out (never a limit on a guess), said once."""
    try:
        return parse_limits(raw)
    except LimitsRefused as exc:
        logger.warning("[space-limits] stored limits are not limits (%s): Kazma's own apply", exc.code)
        return {}


def limits_for_spaces(reg: dict[str, Any] | None = None) -> SpaceLimits:
    """The limits for a space that has none of its own: the admin's, over
    :data:`SHIPPED`."""
    if reg is None:
        from kazma_core.spaces.identity import read_registry

        reg = read_registry()
    return replace(SHIPPED, **_set_in((reg.get("defaults") or {}).get("space_limits")))


def _limits_of(space_id: str, reg: dict[str, Any] | None = None) -> SpaceLimits:
    """*space_id*'s limits: its own, over the limits for spaces."""
    if reg is None:
        from kazma_core.spaces.identity import read_registry

        reg = read_registry()
    space = (reg.get("spaces") or {}).get(str(space_id or "")) or {}
    return replace(limits_for_spaces(reg), **_set_in(space.get("limits")))


# ── bytes ──────────────────────────────────────────────────────────────


def _set_for(space_id: str, name: str) -> int | None:
    """*name*'s limit set for *space_id* -- its own, else the limits for
    spaces the admin set -- or None when neither sets it."""
    from kazma_core.spaces.identity import read_registry

    reg = read_registry()
    space = (reg.get("spaces") or {}).get(str(space_id or "")) or {}
    own = _set_in(space.get("limits"))
    if name in own:
        return own[name]
    return _set_in((reg.get("defaults") or {}).get("space_limits")).get(name)


def documents_bytes(space_id: str, install_value: int) -> int:
    """The documents limit of *space_id*: once Spaces is enforced, the one
    set for it or for every space; else, and when none is set, the install's
    one value for every tenant (*install_value*, ``documents.quotas.tenant_bytes``),
    as before."""
    if not spaces_enforced():
        return int(install_value)
    set_here = _set_for(space_id, "documents_bytes")
    return int(install_value) if set_here is None else set_here


def knowledge_bytes(space_id: str, install_default: int) -> int:
    """The knowledge limit of *space_id*: once Spaces is enforced, the one
    set for it or for every space; else, and when none is set,
    *install_default*, as before."""
    if not spaces_enforced():
        return int(install_default)
    set_here = _set_for(space_id, "knowledge_bytes")
    return int(install_default) if set_here is None else set_here


#: A space folder's size is measured at most this often.
_SIZE_TTL_S = 60.0
_sizes: dict[str, tuple[float, int]] = {}
_sizes_lock = threading.Lock()


def _folder_bytes(folder: Path) -> int:
    from kazma_core.spaces.files import own_files

    total = 0
    for path in own_files(folder, unreadable=[]):
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def _space_folder_bytes(space_id: str, *, fresh: bool = False) -> int:
    """The bytes in *space_id*'s own folder, measured at most once a
    minute (a write admitted under the limit may carry it past; the next is
    refused)."""
    from kazma_core.spaces.files import space_dir

    now = time.monotonic()
    with _sizes_lock:
        held = _sizes.get(space_id)
    if held is not None and not fresh and now - held[0] < _SIZE_TTL_S:
        return held[1]
    folder = space_dir(space_id)
    size = _folder_bytes(folder) if folder.is_dir() else 0
    with _sizes_lock:
        _sizes[space_id] = (now, size)
    return size


def disk_refusal(space_id: str, incoming: int = 0) -> str | None:
    """Why a write of *incoming* bytes into *space_id*'s folder is refused,
    or None: its folder is at its disk limit. None while the install does
    not enforce Spaces, and for the owner's space (no folder of its own)."""
    space = str(space_id or "")
    if not spaces_enforced() or not space or space == OWNER_SPACE_ID:
        return None
    limit = _limits_of(space).disk_bytes
    used = _space_folder_bytes(space)
    if used + max(0, int(incoming)) <= limit:
        return None
    return (f"This space's files have reached their limit ({_size_words(limit)}). "
            "Remove some, or ask the admin for more.")


def refuse_write_for_disk(incoming: int = 0) -> None:
    """Raise :class:`SpaceOverLimit` when the bound space's folder is at its
    disk limit (:func:`disk_refusal`): for a writer that puts a file into
    the space's own folder itself -- an upload, a shared copy -- before it
    writes. Nothing while the install does not enforce Spaces."""
    from kazma_core.spaces.scope import current_scope

    if not spaces_enforced():
        return
    bound = current_scope()
    refused = disk_refusal(bound.space_id if bound is not None else "", incoming)
    if refused:
        raise SpaceOverLimit("disk_bytes", refused)


# ── work running at once ───────────────────────────────────────────────


def running_refusal(kind: str, space_id: str, running: int, *, person: str = "",
                    person_running: int = 0, shares: tuple[int, int] | None = None) -> str | None:
    """Why one more of *kind* (:data:`RUNNING_KINDS`) in *space_id* is
    refused when the space already runs *running* of them and *person*
    *person_running*, or None: the space holds its share of the install's
    slots, or the person holds theirs of the space's (S4-04,
    ``spaces.fairness.person_share``). *shares* is ``(space's, person's)``
    when the caller has read them. None while the install does not enforce
    Spaces. Blocking when *shares* is not given (the identity store)."""
    if shares is None:
        from kazma_core.spaces.fairness import shares as read_shares

        shares = read_shares(kind, space_id)
    if shares is None:
        return None
    return share_refusal(kind, running, shares, person=person, person_running=person_running)


def share_refusal(kind: str, running: int, shares: tuple[int, int], *, person: str = "",
                  person_running: int = 0) -> str | None:
    """:func:`running_refusal` for *shares* already read (``(space's,
    person's)``, ``spaces.fairness.shares``). Reads nothing."""
    space_share, person_share = shares
    if running >= space_share:
        return (f"This space already runs {space_share} {_KIND_WORDS[kind]} at once, its share; "
                "try again when one ends.")
    if person and person_running >= person_share:
        from kazma_core.spaces.fairness import person_refusal_text

        return person_refusal_text(kind, person_share)
    return None


_KIND_WORDS = {"crawls": "Knowledge crawls", "research": "research runs", "swarm": "swarm tasks",
               "turns": "turns", "scheduled": "scheduled jobs"}


def _size_words(size: int) -> str:
    if size >= _GIB:
        return f"{size / _GIB:g} GiB"
    return f"{size / _MIB:g} MiB"


# ── usage ──────────────────────────────────────────────────────────────


def usage_of(space_id: str) -> dict[str, Any]:
    """What *space_id* holds and runs now, beside its limits, for the
    admin's page: bytes in its folder, its documents and its libraries, and
    its crawls, research runs and swarm tasks running. Counts and sizes,
    never a word of what the space holds. A part that cannot be read is
    None."""
    limits = _limits_of(space_id).as_dict()
    limits["documents_bytes"] = _measured(lambda: documents_bytes(space_id, _install_documents_bytes()))
    limits["knowledge_bytes"] = knowledge_bytes(space_id, _install_knowledge_bytes())
    usage: dict[str, Any] = {"limits": limits,
                             # The owner's space keeps the install's folders.
                             "has_folder": space_id != OWNER_SPACE_ID}
    usage["disk_bytes"] = (None if space_id == OWNER_SPACE_ID
                           else _measured(lambda: _space_folder_bytes(space_id, fresh=True)))
    usage["documents_bytes"] = _measured(lambda: _documents_used(space_id))
    usage["knowledge_bytes"] = _measured(lambda: _knowledge_used(space_id))
    running = _measured(lambda: _running_of(space_id)) or {}
    for kind in RUNNING_KINDS:
        usage[kind] = running.get(kind) if running else None
    return usage


def _measured(read: Any) -> Any:
    """*read*'s answer, or None when its store cannot answer now (one part
    that cannot be read says so; the others still show)."""
    import sqlite3

    try:
        return read()
    except (sqlite3.Error, OSError, RuntimeError, ValueError, LookupError):
        logger.warning("[space-limits] a part of a space's usage could not be read", exc_info=True)
        return None


def _install_documents_bytes() -> int:
    """The install's documents value for every tenant."""
    from kazma_core.documents.config import get_document_config

    return int(get_document_config().quota_tenant_bytes)


def _install_knowledge_bytes() -> int:
    from kazma_core.stores.knowledge import _SPACE_QUOTA_BYTES

    return int(_SPACE_QUOTA_BYTES)


def _documents_used(space_id: str) -> int | None:
    """None when no documents service runs in this process (nothing is
    made for a read)."""
    from kazma_core.documents.ingestion import peek_ingestion_service

    service = peek_ingestion_service()
    if service is None:
        return None
    return int(service.repository.tenant_referenced_blob_bytes(tenant_id=space_id))


def _knowledge_used(space_id: str) -> int:
    from kazma_core.stores.knowledge import get_knowledge_store

    return int(get_knowledge_store().space_used_bytes(space_id))


def _running_of(space_id: str) -> dict[str, int | None]:
    """The space's crawls, research runs, swarm tasks, turns and scheduled
    jobs running now (the swarm's None when no engine runs in this
    process; turns and scheduled jobs this process counts)."""
    from kazma_core.spaces.fairness import scheduled_slots, turn_slots
    from kazma_core.swarm import get_swarm_engine
    from kazma_core.web_acquire import crawl_jobs
    from kazma_core.web_acquire.research_runtime import research_running_in

    engine = get_swarm_engine()
    return {"crawls": crawl_jobs.running_in(space_id),
            "research": research_running_in(space_id),
            "swarm": engine.running_in(space_id) if engine is not None else None,
            "turns": turn_slots.running(space_id),
            "scheduled": scheduled_slots.running(space_id)}
