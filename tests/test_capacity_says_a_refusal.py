"""A refused intake check is a refusal, never "capacity unavailable; retry
shortly" (Spaces S4-01 H2, found by the documents suites run armed in step 3).

``DocumentCapacityGuard.check_intake`` turned every store error into a 503
"retry shortly"; a database refusal (``NoSpaceBound``, a policy) is no outage
and a retry cannot pass it. The control: an outage is still a 503.
"""

from __future__ import annotations

from typing import Any

import pytest
from kazma_core.documents.capacity import CapacityError
from kazma_core.spaces import scope
from kazma_core.spaces.scope import NoSpaceBound

from tests._as_request import lock_armed, not_armed


class _Jobs:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    def queue_stats(self) -> Any:
        raise self.error

    def tenant_load(self, *, tenant_id: str) -> Any:
        raise self.error


def _guard(error: BaseException, root: Any) -> Any:
    from kazma_core.documents.capacity import DocumentCapacityGuard
    from kazma_core.documents.config import DocumentConfig

    return DocumentCapacityGuard(config=DocumentConfig(storage_root=root), jobs=_Jobs(error), storage_root=root)


def test_a_refusal_is_raised_as_itself(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    guard = _guard(NoSpaceBound("refused"), tmp_path)
    with pytest.raises(NoSpaceBound):
        guard.check_intake(tenant_id="family", byte_size=1)


def test_an_outage_is_still_a_retry(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    import sqlite3

    monkeypatch.setattr(scope, "_enforced", True)
    guard = _guard(sqlite3.OperationalError("database is locked"), tmp_path)
    with pytest.raises(CapacityError) as caught:
        guard.check_intake(tenant_id="family", byte_size=1)
    assert caught.value.status == 503


def test_armed_and_not_enforced_a_refusal_is_still_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Spaces S4-01 step 3 review M3: with the lock armed and Spaces not
    enforced, a refusal read as a store failure and the SQLite fallbacks
    answered."""
    from kazma_core.db import row_security
    from kazma_core.db.pg_helpers import is_space_refusal

    monkeypatch.setattr(scope, "_enforced", False)
    lock_armed(monkeypatch, True)
    assert is_space_refusal(NoSpaceBound("refused")) and is_space_refusal(row_security._SecondTransaction("spent"))
    with pytest.raises(NoSpaceBound):
        _guard(NoSpaceBound("refused"), tmp_path).check_intake(tenant_id="family", byte_size=1)
    # The control: neither armed nor enforced, an install reads its errors as before.
    not_armed(monkeypatch)
    assert not is_space_refusal(NoSpaceBound("refused"))
