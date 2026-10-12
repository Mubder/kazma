"""Durable cross-process origin ownership, spacing and server cooldowns."""
from __future__ import annotations

import hashlib
import json
import math
import os
import uuid

import psutil

from kazma_core.web_acquire.crawl_jobs import _alive
from kazma_core.web_acquire.durable_state import mutate, checked_clock
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


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
            if not _alive(identity):
                del row[field][token]
    return row


def queue(origin):
    token = uuid.uuid4().hex
    owner = [os.getpid(), psutil.Process().create_time()]
    def change(table):
        row = _row(table, origin)
        if len(row['waiting']) >= 32:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable HTTP origin waiter limit reached (32).', transient=True)
        row['waiting'][token] = owner
        return token
    return mutate('origins', change)


def start(origin, token, delay):
    def change(table):
        row = _row(table, origin)
        now = checked_clock(row)
        if token not in row['waiting']:
            raise ValueError('Origin ownership was lost')
        if row['cooldown'] is None or row['cooldown'] > now:
            raise AcquisitionError(FetchErrorCode.THROTTLED, 'Durable HTTP origin cooldown active; no request or recovery backend dispatched.', transient=True)
        if len(row['active']) >= 2:
            return .05
        if delay:
            row['interval'] = max(delay, row['interval'] if row['until'] > now else 0)
            row['until'] = now + 60
        wait = row['next'] - now
        if wait > 0:
            return min(wait, 30.)
        row['next'] = now + max(delay, row['interval'] if row['until'] > now else 0)
        row['active'][token] = row['waiting'].pop(token)
        return 0.
    return mutate('origins', change)


def release(origin, token):
    def change(table):
        row = table.get(_key(origin))
        if row is not None:
            row['active'].pop(token, None)
            row['waiting'].pop(token, None)
    mutate('origins', change)


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
