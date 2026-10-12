"""Conservative retained publication allowance; failed writes retain reservations."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from kazma_core.web_acquire.durable_state import mutate
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

_LIMIT = 512 * 1024**2
_MAX_FILE = 24 * 1024**2


def reserve(target, size):
    if type(size) is not int or not 0 <= size <= _MAX_FILE:
        raise AcquisitionError(FetchErrorCode.LIMIT, 'Managed output exceeds its 24MiB file allowance.')
    path = os.path.normcase(str(Path(target).resolve()))
    key = hashlib.sha256(path.encode()).hexdigest()
    def change(table):
        if key in table:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Managed output already has a retained or uncertain reservation; choose a new path.')
        if len(table) >= 256 or sum(row['bytes'] for row in table.values()) + size > _LIMIT:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Managed standalone output allowance exhausted (256 files/512MiB); inspect retained output before operator maintenance.')
        table[key] = dict(bytes=size)
    mutate('outputs', change, entries=256)
