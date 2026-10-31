"""Persist parsed robots policy and an unavailable receipt before lookup.

A row's owner is its ``claim``. It was ``token`` until 2026-10-31: the
settings store moves a field of that name into the vault, ``get_category``
read the pointer back, and every lookup refused its own commit -- each crawl
then read the "owned" placeholder for 30 s (live: "Robots lookup is owned or
was interrupted"). A row an older build wrote keeps its ``token`` key, which
holds no claim this build honours; it expires like any placeholder.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict

from kazma_core.web_acquire.durable_state import mutate, checked_clock
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


def identity(key):
    origin, policy, proxy = key
    return hashlib.sha256(json.dumps([origin, policy, proxy.hex()], sort_keys=True).encode()).hexdigest()


def _restore(value):
    from kazma_core.web_acquire.robots import _Rules

    if not isinstance(value, dict) or value.get('status') not in {'missing', 'denied', 'unavailable', 'available'}:
        # The parser uses "rules" for a successful parsed file.
        if not isinstance(value, dict) or value.get('status') != 'rules':
            raise ValueError('Invalid saved robots policy')
    rules = value.get('rules', [])
    if not isinstance(rules, (tuple, list)) or len(rules) > 8192:
        raise ValueError('Invalid saved robots rules')
    if any(not isinstance(rule, (tuple, list)) or len(rule) != 2 or not isinstance(rule[0], str) or type(rule[1]) is not bool for rule in rules):
        raise ValueError('Invalid saved robots rule')
    if len(json.dumps(value).encode()) > 2 * 1024**2:
        raise ValueError('Oversized saved robots policy')
    import math
    delay = value.get('delay', 0)
    if not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
        raise ValueError('Invalid saved robots delay')
    value = dict(value, rules=tuple(tuple(rule) for rule in rules))
    return _Rules(**value)


def begin(key):
    claim = uuid.uuid4().hex
    def change(table):
        row = table.get(key)
        if row is not None:
            now = checked_clock(row)
            if row['expires'] > now:
                return dict(value=row['value'], claim=None)
        if key not in table and len(table) >= 128:
            now = time.time()
            for candidate, old in list(table.items()):
                if max(old['expires'], old['clock']) <= now:
                    del table[candidate]
                    break
            else:
                raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable robots table is full of unexpired policy; crawl refused.')
        from kazma_core.web_acquire.robots import _Rules
        now = time.time()
        value = asdict(_Rules('unavailable', error='Robots lookup is owned or was interrupted; crawl refused until its retry deadline.'))
        table[key] = dict(clock=now, expires=now + 30, claim=claim, value=value)
        return dict(value=value, claim=claim)
    result = mutate('robots', change, entries=128, bytes_limit=16 * 1024**2)
    return _restore(result['value']), result['claim']


def commit(key, claim, value):
    _restore(asdict(value))
    ttl = 30 if value.status == 'unavailable' else 600 if value.status == 'denied' else 3600
    def change(table):
        row = table.get(key)
        if row is None or row.get('claim') != claim:
            raise AcquisitionError(FetchErrorCode.ROBOTS_UNAVAILABLE, 'Robots lookup ownership changed; crawl refused.', transient=True)
        now = checked_clock(row)
        row.pop('token', None)
        row.update(value=asdict(value), expires=now + ttl, claim=None)
    mutate('robots', change, entries=128, bytes_limit=16 * 1024**2)
