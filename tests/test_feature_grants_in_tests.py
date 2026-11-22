"""What the root conftest gives a test's people (Spaces S2-11 review, fix 9).

Every test but a test of the features themselves (marked ``real_features``)
runs with its people holding every feature, so a test of another part does
not need to grant the part it uses. Only people: an asker that is no account
of the install holds nothing, in a test as in production -- a test binding
a made-up asker would otherwise have every feature where the product gives
none.
"""

from __future__ import annotations

import pytest
from kazma_core.spaces import features, identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._space_matrix import Matrix


@pytest.fixture
def matrix(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setattr(scope, "_enforced", True)
    with Matrix() as made:
        yield made


def test_a_test_s_people_hold_every_feature_and_no_one_else_does(matrix) -> None:
    registry = identity.read_registry()
    assert features.grants_of(registry, matrix.account_b).on == frozenset(features.FEATURES)
    assert features.grants_of(registry, "not-an-account").on == frozenset()
    assert features.grants_of(registry, OWNER_ACCOUNT_ID).admin
    with scope.bind_scope(Matrix.SPACE_B, "not-an-account"):
        grants = features.grants_here()
    assert grants is not None and features.tool_refusal("x_status", grants)
