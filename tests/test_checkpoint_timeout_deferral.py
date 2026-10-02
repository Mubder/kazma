"""A paused pipeline restored at boot gets its timeout later, without an
orphaned coroutine (2026-10-02).

``CheckpointManager._arm_checkpoint_timeout`` called
``asyncio.create_task(self._checkpoint_timeout_reject(...))`` and caught the
RuntimeError a sync boot raises. The coroutine already existed by then, so
every deferral left "coroutine '_checkpoint_timeout_reject' was never
awaited" (printed in the full suite's warnings). The loop is checked first
now. The deferred arm itself is unchanged and drained at startup
(``tests/test_swarm_paused_task_endings.py`` exercises it).
"""

from __future__ import annotations

import asyncio
import gc
import warnings
from collections.abc import Callable
from unittest.mock import MagicMock

from kazma_core.swarm.checkpoint_manager import CheckpointManager


def _never_awaited(fn: Callable[[], None]) -> list[str]:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fn()
        gc.collect()
    return [str(w.message) for w in caught if "never awaited" in str(w.message)]


def test_a_deferral_outside_a_loop_leaves_no_coroutine() -> None:
    mgr = CheckpointManager(checkpoint_handler=MagicMock())
    assert _never_awaited(lambda: mgr._arm_checkpoint_timeout("t1", 30.0)) == []
    assert mgr._pending_timeout_arms == [("t1", 30.0)]


def test_negative_control_the_old_order_leaves_one() -> None:
    mgr = CheckpointManager(checkpoint_handler=MagicMock())

    def old_order() -> None:
        try:
            asyncio.create_task(mgr._checkpoint_timeout_reject("t1", 30.0))
        except RuntimeError:
            pass

    assert _never_awaited(old_order)


async def test_inside_a_loop_it_arms_at_once() -> None:
    handler = MagicMock()
    mgr = CheckpointManager(checkpoint_handler=handler)
    mgr._arm_checkpoint_timeout("t2", 3600.0)
    (task_id, timer), _kwargs = handler.set_timeout_task.call_args
    assert task_id == "t2" and not timer.done()
    assert mgr._pending_timeout_arms == []
    timer.cancel()
    await asyncio.gather(timer, return_exceptions=True)
