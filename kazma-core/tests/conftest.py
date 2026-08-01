"""Conftest — shared pytest fixtures for kazma-core tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolated_memory_dbs(tmp_path):
    """Redirect the V2 memory DBs to a temp location for every test.

    Mirrors the root ``tests/conftest.py`` fixture. Any test exercising the
    swarm engine dispatch / memory write paths (``store_swarm_result``,
    ``mutate_belief``, …) would otherwise write into the production
    ``kazma-data/memory_state.db`` and pollute the user's Beliefs UI.
    """
    state = str(tmp_path / "test_memory_state.db")
    ops = str(tmp_path / "test_memory_ops.db")
    prev_state = os.environ.get("KAZMA_MEMORY_STATE_DB")
    prev_ops = os.environ.get("KAZMA_MEMORY_OPS_DB")
    os.environ["KAZMA_MEMORY_STATE_DB"] = state
    os.environ["KAZMA_MEMORY_OPS_DB"] = ops
    yield
    for key, prev in (("KAZMA_MEMORY_STATE_DB", prev_state), ("KAZMA_MEMORY_OPS_DB", prev_ops)):
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev
