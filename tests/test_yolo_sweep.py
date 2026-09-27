"""Ended YOLO windows are removed on the maintenance cadence, whatever chat they belong to.

``yolo_status`` removed an ended window only when its own chat asked again, so
the windows of chats nobody reopened stayed in the settings for good: 90 on the
live install on 2026-09-28, one of them a pre-TTL ``true`` in the ``general``
category that still counted as active. ``purge_expired_yolo`` runs every 15
minutes (``worker_bootstrap._MAINTENANCE_SWEEPS``). Runs on the real settings
store (the test data folder), so the categories are the ones the writers use.
"""

from __future__ import annotations

import time
import uuid

import pytest

from kazma_core.config_store import get_config_store
from kazma_core.memory import worker_bootstrap as wb
from kazma_core.safety.yolo import enable_yolo, is_yolo_active, purge_expired_yolo


@pytest.fixture
def threads():
    """Unique chat ids, their rows removed afterwards whatever happened."""
    made: list[str] = []

    def new() -> str:
        made.append(f"sweep-{uuid.uuid4().hex[:8]}")
        return made[-1]

    yield new
    cs = get_config_store()
    for tid in made:
        cs.delete(f"yolo.{tid}")


def _held(tid: str) -> bool:
    return get_config_store().get(f"yolo.{tid}") is not None


def test_ended_windows_go_and_live_ones_stay(threads):
    cs = get_config_store()
    ended, live, no_end, legacy, disabled = (threads() for _ in range(5))
    enable_yolo(ended, actor="test", force=True, ttl_seconds=60)
    enable_yolo(live, actor="test", force=True, ttl_seconds=3600)
    enable_yolo(no_end, actor="test", force=True, ttl_seconds=0)  # TTL off: no end, on purpose
    cs.set(f"yolo.{legacy}", True)  # the pre-TTL writer: default category "general"
    cs.set(f"yolo.{disabled}", {"enabled": False}, category="safety")

    # Nobody asks about these chats again: without the sweep they all stay.
    later = time.time() + 120
    assert all(_held(t) for t in (ended, live, no_end, legacy, disabled))

    assert purge_expired_yolo(now=later) == 3
    assert not _held(ended) and not _held(legacy) and not _held(disabled)
    assert _held(live) and _held(no_end)
    assert is_yolo_active(live) and is_yolo_active(no_end)


def test_a_window_is_not_removed_before_it_ends(threads):
    """Negative control: the sweep must never cut a running window short."""
    tid = threads()
    enable_yolo(tid, actor="test", force=True, ttl_seconds=600)
    assert purge_expired_yolo(now=time.time() + 60) == 0
    assert is_yolo_active(tid)


def test_the_sweep_runs_on_the_maintenance_cadence():
    assert ("ended YOLO windows", wb._purge_ended_yolo) in wb._MAINTENANCE_SWEEPS
    labels = [label for label, _ in wb._MAINTENANCE_SWEEPS]
    assert labels[-1] == "knowledge vector repair", "the knowledge repair stays last"
