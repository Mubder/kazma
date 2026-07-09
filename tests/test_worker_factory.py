"""Unit tests for swarm.worker_factory."""

from __future__ import annotations

import pytest

from kazma_core.swarm.config import WorkerConfig
from kazma_core.swarm.worker import InProcessWorker
from kazma_core.swarm.worker_factory import create_worker, register_worker, unregister_worker


def test_create_in_process_worker():
    cfg = WorkerConfig(name="coder", type="in_process", role="coder", model="gpt-4o-mini")
    w = create_worker(cfg)
    assert isinstance(w, InProcessWorker)
    assert w.name == "coder"


def test_create_telegram_bot_maps_to_in_process():
    cfg = WorkerConfig(name="tg", type="telegram_bot", role="assistant", model="m")
    w = create_worker(cfg)
    assert isinstance(w, InProcessWorker)


def test_unknown_type_raises():
    cfg = WorkerConfig(name="x", type="remote_rpc", role="r", model="m")
    with pytest.raises(ValueError, match="Unknown worker type"):
        create_worker(cfg)


def test_register_and_unregister():
    workers: dict = {}
    cfg = WorkerConfig(name="a", type="in_process", role="r", model="m")
    w = register_worker(workers, cfg)
    assert "a" in workers
    cleaned = []
    out = unregister_worker(workers, "a", on_removed=cleaned.append)
    assert out is w
    assert "a" not in workers
    assert cleaned == ["a"]


def test_register_duplicate_raises():
    workers: dict = {}
    cfg = WorkerConfig(name="dup", type="in_process", role="r", model="m")
    register_worker(workers, cfg)
    with pytest.raises(ValueError, match="already registered"):
        register_worker(workers, cfg)


def test_unregister_missing_raises():
    with pytest.raises(KeyError):
        unregister_worker({}, "nope")
