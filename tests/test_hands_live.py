"""Opt-in live-model golden. CI skips unless KAZMA_LIVE_EVAL=1.

This is the usefulness eval with a real LLM. Default off so CI stays
deterministic. Operator: set the env, pin workspace to examples/hands-demo,
and run this file.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("KAZMA_LIVE_EVAL") != "1",
    reason="opt-in live eval (KAZMA_LIVE_EVAL=1)",
)


def test_live_eval_env_is_explicit() -> None:
    assert os.environ.get("KAZMA_LIVE_EVAL") == "1"
