"""Second of a pair (tests/test_order_independence.py runs it in a child).

Runs after ``_probe_file_vault_first.py`` in the same process: the vault
key and the vault that file's setup made must not reach this file. Not
collected on its own: the name does not start with ``test_``.
"""

from __future__ import annotations

import os

import pytest

PROBE = "KAZMA_FILE_VAULT_PROBE"


def test_the_next_file_has_neither_the_key_nor_the_vault():
    if os.environ.get(PROBE) != "1":
        pytest.skip("only in the order-independence child run")
    from kazma_core.security.vault import get_vault

    assert os.environ.get("KAZMA_VAULT_KEY") is None
    assert get_vault() is None
