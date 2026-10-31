"""Bounded runtime tables using the install's fresh-read conditional writes."""
from __future__ import annotations

import copy
import json
import time

from kazma_core import config_store
from kazma_core.config_availability import _storage_failures
from kazma_core.config_store import ConfigRevisionConflict
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


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
        for _ in range(16):
            data = store.get_category(category)
            table, revision = data.get(key, {}), data.get(revision_key, 0)
            if not isinstance(table, dict) or len(table) > entries or type(revision) is not int or revision < 0:
                raise ValueError('Invalid durable state')
            if len(json.dumps(table, allow_nan=False).encode()) > bytes_limit:
                raise ValueError('Oversized durable state')
            table = copy.deepcopy(table)
            result = change(table)
            if len(table) > entries or len(json.dumps(table, allow_nan=False).encode()) > bytes_limit:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable acquisition table is full; further dispatch refused.')
            items = [(key, table, category), (revision_key, revision + 1, category)]
            refuse_vaulted(items)
            try:
                store.batch_set(items, expected=(revision_key, revision))
                return copy.deepcopy(result)
            except ConfigRevisionConflict:
                continue
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable acquisition state is busy; retry later.', transient=True)
    except (*_storage_failures(), ValueError, TypeError, KeyError) as exc:
        raise AcquisitionError(FetchErrorCode.STORAGE, 'Durable acquisition state unavailable; dispatch refused. Check storage and saved state.') from exc


def checked_clock(row):
    now = time.time()
    if now + 1 < row.get('clock', 0):
        raise AcquisitionError(FetchErrorCode.THROTTLED, 'System clock moved backwards; acquisition refused until saved deadlines are trustworthy.', transient=True)
    row['clock'] = max(now, row.get('clock', 0))
    return now
