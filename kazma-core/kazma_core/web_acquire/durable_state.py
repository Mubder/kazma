"""Bounded runtime tables using the install's fresh-read conditional writes."""
from __future__ import annotations

import copy
import json
import logging
import random
import threading
import time

from kazma_core import config_store
from kazma_core.config_availability import _storage_failures
from kazma_core.config_store import ConfigRevisionConflict
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

logger = logging.getLogger(__name__)

#: How many times a write is tried before the table is reported busy.
_ATTEMPTS = 16

#: One lock per table: this process's writers of a table take turns, so
#: they never race each other's compare-and-set. Live 2026-11-05 a deep
#: research's parallel searches, each polling its origin every 50 ms with a
#: write, gave up as "Durable acquisition state is busy" -- and a release
#: that gave up left its lease held by the live process for good, so every
#: later search of that origin waited 30 s and failed. The compare-and-set
#: stays for other processes, with a pause between tries.
_TABLE_LOCKS: dict[str, threading.RLock] = {}
_TABLE_LOCKS_GUARD = threading.Lock()


class Unchanged:
    """What a change returns when it left the table as it was.

    ``mutate`` returns ``value`` and writes nothing: a poll that only waits
    is a read, not a write every 50 ms.
    """

    __slots__ = ('value',)

    def __init__(self, value):
        self.value = value


def _table_lock(name: str) -> threading.RLock:
    """The lock this process's writers of table *name* take in turn."""
    with _TABLE_LOCKS_GUARD:
        lock = _TABLE_LOCKS.get(name)
        if lock is None:
            lock = _TABLE_LOCKS[name] = threading.RLock()
        return lock


def _retry_pause(attempt: int) -> None:
    """Before trying a write again: another process moved the revision. A
    short random wait, growing, so the two do not collide again."""
    time.sleep(random.uniform(0, min(0.4, 0.02 * 2 ** attempt)))


def refuse_vaulted(items):
    """Refuse a write the settings store would partly move into the vault.

    *items* are ``batch_set``'s ``(key, value, category)``. A credential-named
    field reads back from ``get_category`` as its ``vault://`` pointer, never
    as what was written: the state would be wrong with no error anywhere.
    """
    for key, value, _category in items:
        paths = config_store.vaulted_paths(key, value)
        if paths:
            raise AcquisitionError(FetchErrorCode.STORAGE,
                f'Durable acquisition field {paths[0]} is named like a credential; the settings store '
                'would move it into the vault and read it back as a pointer. Write refused.')


def mutate(name, change, *, entries=512, bytes_limit=4 * 1024**2):
    key, revision_key, category = 'acquisition.' + name, 'acquisition.' + name + '_revision', 'acquisition_' + name
    try:
        store = config_store.get_config_store()
        if not isinstance(store, config_store.ConfigStore):
            raise ValueError('No durable settings backend')
        with _table_lock(name):
            for attempt in range(_ATTEMPTS):
                if attempt:
                    _retry_pause(attempt)
                data = store.get_category(category)
                table, revision = data.get(key, {}), data.get(revision_key, 0)
                if not isinstance(table, dict) or len(table) > entries or type(revision) is not int or revision < 0:
                    raise ValueError('Invalid durable state')
                if len(json.dumps(table, allow_nan=False).encode()) > bytes_limit:
                    raise ValueError('Oversized durable state')
                table = copy.deepcopy(table)
                result = change(table)
                if isinstance(result, Unchanged):
                    return copy.deepcopy(result.value)
                if len(table) > entries or len(json.dumps(table, allow_nan=False).encode()) > bytes_limit:
                    raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable acquisition table is full; further dispatch refused.')
                items = [(key, table, category), (revision_key, revision + 1, category)]
                refuse_vaulted(items)
                try:
                    store.batch_set(items, expected=(revision_key, revision))
                    return copy.deepcopy(result)
                except ConfigRevisionConflict:
                    continue
        logger.warning('[acquisition] durable table %s still changed by another process after %d tries; '
                       'the write was given up', name, _ATTEMPTS)
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable acquisition state is busy; retry later.', transient=True)
    except (*_storage_failures(), ValueError, TypeError, KeyError) as exc:
        raise AcquisitionError(FetchErrorCode.STORAGE, 'Durable acquisition state unavailable; dispatch refused. Check storage and saved state.') from exc


def checked_clock(row):
    now = time.time()
    if now + 1 < row.get('clock', 0):
        raise AcquisitionError(FetchErrorCode.THROTTLED, 'System clock moved backwards; acquisition refused until saved deadlines are trustworthy.', transient=True)
    row['clock'] = max(now, row.get('clock', 0))
    return now
