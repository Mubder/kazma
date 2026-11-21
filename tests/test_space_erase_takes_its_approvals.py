"""A space's swarm approval requests are its own: counted, exported and
erased with it.

A swarm request's shared approval (``swarm.approval.<id>``) is a settings row
that records its space, its asker, the tool and the arguments' hash
(S1-15). ``space_data._settings_keys`` reads every settings row that records
a space (``_SPACE_RECORDING_PREFIXES``) beside the space's own
``tenant.<id>.`` keys and its chats' state.

Control: the key walk of before, by key name alone, leaves the space's
approval rows after an erase.
"""

from __future__ import annotations

import uuid

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)
    person = people.create_person("approvals-one", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    other = people.create_person("approvals-two", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    store = get_config_store()
    mine = f"swarm.approval.{uuid.uuid4().hex}"
    theirs = f"swarm.approval.{uuid.uuid4().hex}"
    store.set(mine, {"status": "pending", "tool": "file_write", "space": person["personal_space"],
                     "asker": person["id"]}, category="swarm")
    store.set(theirs, {"status": "pending", "tool": "file_write", "space": other["personal_space"],
                       "asker": other["id"]}, category="swarm")
    try:
        yield {"space": person["personal_space"], "mine": mine, "theirs": theirs}
    finally:
        store.delete(mine)
        store.delete(theirs)
        store.delete(identity.REGISTRY_KEY)


def test_a_spaces_approval_rows_are_its_settings(world) -> None:
    from kazma_core.spaces import space_data

    keys = space_data._settings_keys(world["space"], [])
    assert world["mine"] in keys and world["theirs"] not in keys


def test_control_the_key_walk_of_before_misses_them(world, monkeypatch) -> None:
    from kazma_core.spaces import space_data

    monkeypatch.setattr(space_data, "_SPACE_RECORDING_PREFIXES", ("never-a-prefix.",))
    assert world["mine"] not in space_data._settings_keys(world["space"], [])
