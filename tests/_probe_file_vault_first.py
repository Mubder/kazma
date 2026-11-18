"""First of a pair (tests/test_order_independence.py runs it in a child).

A file-wide fixture that does what building the app does at a first boot:
it writes a generated KAZMA_VAULT_KEY into the environment and makes the
vault. Not collected on its own: the name does not start with ``test_``.
"""

from __future__ import annotations

import os

import pytest

PROBE = "KAZMA_FILE_VAULT_PROBE"


@pytest.fixture(scope="module", autouse=True)
def _an_app_built_for_this_file():
    if os.environ.get(PROBE) != "1":
        pytest.skip("only in the order-independence child run")
    from cryptography.fernet import Fernet

    from kazma_core.security.vault import get_vault

    os.environ["KAZMA_VAULT_KEY"] = Fernet.generate_key().decode()
    assert get_vault() is not None
    yield


def test_the_file_has_a_vault():
    from kazma_core.security.vault import get_vault

    assert get_vault() is not None
