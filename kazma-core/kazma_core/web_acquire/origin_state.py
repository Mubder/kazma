"""Durable cross-process origin ownership, spacing and server cooldowns."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import threading
import uuid

import psutil

from kazma_core.web_acquire.crawl_jobs import _alive
from kazma_core.web_acquire.durable_state import Unchanged, checked_clock, mutate
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

logger = logging.getLogger(__name__)

#: The lease tokens this process holds: queued and not yet released. A token
#: in the table that names this process and is not here was left by a
#: release whose write failed; the origin's next admission takes it back. A
#: live process's leases were never otherwise reaped (its process is alive),
#: so on 2026-11-05 two leaked leases of the running server held the local
#: SearXNG's two slots and every later search waited out its 30 seconds
#: until a restart.
_HELD: set[str] = set()
_HELD_LOCK = threading.Lock()
_OWN: list | None = None


def _own_identity() -> list:
    """This process as a lease records it: [pid, process start time]."""
    global _OWN
    pid = os.getpid()
    own = _OWN
    if own is None or own[0] != pid:
        own = _OWN = [pid, psutil.Process(pid).create_time()]
    return list(own)


def _holds(token) -> bool:
    with _HELD_LOCK:
        return token in _HELD


def _left_behind(identity, token) -> bool:
    """A lease this process recorded and no longer holds."""
    if not isinstance(identity, list) or len(identity) != 2:
        return False
    own = _own_identity()
    return identity[0] == own[0] and identity[1] == own[1] and not _holds(token)


def _key(origin):
    return hashlib.sha256(json.dumps(origin).encode()).hexdigest()


def _row(table, origin):
    key = _key(origin)
    if key not in table:
        if len(table) >= 512:
            import time
            now = time.time()
            for candidate, row in list(table.items()):
                if not row['active'] and not row['waiting'] and row['cooldown'] is not None and max(row['next'], row['cooldown'], row['until'], row['clock']) <= now:
                    del table[candidate]
                    break
            else:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable origin table is full of owned or cooling origins; dispatch refused.')
        table[key] = dict(next=0., cooldown=0., interval=0., until=0., failures=0, active={}, waiting={}, clock=0.)
    row = table[key]
    checked_clock(row)
    for field in ('active', 'waiting'):
        if not isinstance(row[field], dict) or len(row[field]) > (2 if field == 'active' else 32):
            raise ValueError('Invalid origin leases')
        for token, identity in list(row[field].items()):
            if _left_behind(identity, token) or not _alive(identity):
                del row[field][token]
    return row


def _waited(before, row, now, wait):
    """A poll's wait, written only when the poll changed what another reader
    needs: a lease taken back, or a spacing window opened. A poll that only
    waits wrote the whole table every 50 ms per waiter, and those writes
    were what made the table busy."""
    if before is None:
        return wait
    for field, value in row.items():
        if field not in ('clock', 'until') and before.get(field) != value:
            return wait
    if row['until'] != before.get('until') and not before.get('until', 0) > now:
        return wait
    return Unchanged(wait)


def queue(origin):
    token = uuid.uuid4().hex
    owner = _own_identity()
    def change(table):
        row = _row(table, origin)
        if len(row['waiting']) >= 32:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable HTTP origin waiter limit reached (32).', transient=True)
        row['waiting'][token] = owner
        return token
    with _HELD_LOCK:
        _HELD.add(token)
    queued = False
    try:
        result = mutate('origins', change)
        queued = True
        return result
    finally:
        if not queued:
            with _HELD_LOCK:
                _HELD.discard(token)


def start(origin, token, delay):
    key = _key(origin)
    def change(table):
        before = copy.deepcopy(table.get(key))
        row = _row(table, origin)
        now = checked_clock(row)
        if token not in row['waiting']:
            raise ValueError('Origin ownership was lost')
        if row['cooldown'] is None or row['cooldown'] > now:
            raise AcquisitionError(FetchErrorCode.THROTTLED, 'Durable HTTP origin cooldown active; no request or recovery backend dispatched.', transient=True)
        if len(row['active']) >= 2:
            return _waited(before, row, now, .05)
        if delay:
            row['interval'] = max(delay, row['interval'] if row['until'] > now else 0)
            row['until'] = now + 60
        wait = row['next'] - now
        if wait > 0:
            return _waited(before, row, now, min(wait, 30.))
        row['next'] = now + max(delay, row['interval'] if row['until'] > now else 0)
        row['active'][token] = row['waiting'].pop(token)
        return 0.
    return mutate('origins', change)


def release(origin, token):
    """Give a lease back. Never raises: a release that cannot be written is
    said, and the origin's next admission takes the lease back (it names
    this process and is no longer held). Raised from a request's
    ``finally``, it replaced the request's own result and left the lease
    held for the life of the process."""
    def change(table):
        row = table.get(_key(origin))
        if row is not None:
            row['active'].pop(token, None)
            row['waiting'].pop(token, None)
    try:
        mutate('origins', change)
    except AcquisitionError as exc:
        logger.warning('[acquisition] origin lease %s not released (%s); the '
                       'next admission of that origin takes it back',
                       str(token)[:8], exc)
    finally:
        with _HELD_LOCK:
            _HELD.discard(token)


def observe(origin, status, delay, destination=None):
    def change(table):
        row = _row(table, origin)
        value = delay
        if status in (429, 503):
            row['failures'] = min(row['failures'] + 1, 6)
            if value is None:
                value = min(60, 2 ** row['failures'])
        elif 200 <= status < 300:
            row['failures'] = 0
        if value is not None:
            deadline = checked_clock(row) + value
            for target in (origin, destination) if destination else (origin,):
                state = _row(table, target)
                if state['cooldown'] is None:
                    continue
                state['cooldown'] = max(state['cooldown'], deadline) if math.isfinite(deadline) else None
    mutate('origins', change)
