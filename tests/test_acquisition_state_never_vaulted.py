"""Acquisition state written through the settings store reads back as written.

Live 2026-10-31: every Knowledge crawl of rfc-editor.org failed with "Robots
lookup is owned or was interrupted". The robots claim was stored as a field
named ``token``; the settings store moves a string under a credential-named
field into the vault, and ``get_category`` (which the acquisition tables read)
returns the ``vault://`` pointer raw. The owner's commit compared its claim
with the pointer, refused, and left the 30 s "owned" placeholder that the
page fetch then read.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from kazma_core import config_store
from kazma_core.config_store import is_vault_ref, vaulted_paths
from kazma_core.security import vault as vault_mod
from kazma_core.web_acquire import durable_state, robots, robots_state
from kazma_core.web_acquire.result import AcquisitionError

_WEB_ACQUIRE = Path(__file__).resolve().parents[1] / 'kazma-core' / 'kazma_core' / 'web_acquire'


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A settings store with the vault on, as on every live install."""
    monkeypatch.setenv('KAZMA_VAULT_KEY', 'unit-test-vault-key-32bytes-long!!')
    vault_mod.reset_vault()
    monkeypatch.setattr(vault_mod, '_vault', vault_mod.SecretVault(db_path=str(tmp_path / 'vault.db')))
    monkeypatch.setattr(vault_mod, '_vault_init_attempted', True)
    settings = config_store.ConfigStore(db_path=str(tmp_path / 'settings.db'), yaml_path=str(tmp_path / 'none.yaml'))
    monkeypatch.setattr(config_store, 'get_config_store', lambda: settings)
    yield settings
    settings.close()
    vault_mod.reset_vault()


def test_a_robots_claim_is_committed_and_read_by_the_next_lookup(store):
    key = robots_state.identity((('https', 'www.rfc-editor.org', 443), 'local_only', b''))
    first, claim = robots_state.begin(key)
    assert claim and first.status == 'unavailable'
    robots_state.commit(key, claim, robots._Rules('missing', 404))
    later, again = robots_state.begin(key)
    assert again is None and later.status == 'missing'
    raw = store.get_category('acquisition_robots')['acquisition.robots']
    assert not any(is_vault_ref(v) for row in raw.values() for v in row.values())


def test_the_manager_reads_its_own_lookup_back(store, monkeypatch):
    """The crawl's shape: one origin asked twice, the second from storage."""
    loads = []

    async def load(self, url, proxy):
        loads.append(url)
        return robots._Rules('missing', 404)

    monkeypatch.setattr(robots._RobotsManager, '_load', load)

    async def two_lookups():
        url = 'https://www.rfc-editor.org:443/robots.txt'
        first, _ = await robots._RobotsManager(durable=True).get(url, None)
        second, cached = await robots._RobotsManager(durable=True).get(url, None)
        return first, second, cached

    first, second, cached = asyncio.run(two_lookups())
    assert (first.status, second.status, cached) == ('missing', 'missing', True)
    assert len(loads) == 1


def test_the_old_field_name_reads_back_as_a_pointer(store):
    """Negative control: what the robots table held until 2026-10-31."""
    row = {'k': {'clock': 1.0, 'expires': 2.0, 'token': 'a1b2c3d4e5f6', 'value': {}}}
    store.batch_set([('acquisition.robots', row, 'acquisition_robots')])
    back = store.get_category('acquisition_robots')['acquisition.robots']['k']['token']
    assert back != 'a1b2c3d4e5f6' and is_vault_ref(back)
    with pytest.raises(AcquisitionError, match='named like a credential'):
        durable_state.refuse_vaulted([('acquisition.robots', row, 'acquisition_robots')])


def test_the_walk_matches_what_the_store_moves(store):
    value = {'a': {'secret': 'x', 'note': 'y', 'items': [{'name': 'p', 'api_key': 'z'}, {'password': ''}]},
             'b': {'token': None, 'claim': 'c'}}
    assert sorted(vaulted_paths('acquisition.t', value)) == ['acquisition.t.a.items.p.api_key', 'acquisition.t.a.secret']
    store.batch_set([('acquisition.t', value, 'acquisition_t')])
    raw = store.get_category('acquisition_t')['acquisition.t']
    assert is_vault_ref(raw['a']['secret']) and is_vault_ref(raw['a']['items'][0]['api_key'])
    assert raw['a']['note'] == 'y' and raw['b'] == {'token': None, 'claim': 'c'}


def _unguarded_writes(source: str) -> list[str]:
    """Functions that call ``.batch_set(`` without calling ``refuse_vaulted``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [c for c in ast.walk(node) if isinstance(c, ast.Call)]
        names = {getattr(c.func, 'attr', getattr(c.func, 'id', None)) for c in calls}
        if 'batch_set' in names and 'refuse_vaulted' not in names:
            found.append(node.name)
    return found


def test_every_acquisition_state_write_is_guarded():
    missing = [f'{path.name}:{name}' for path in sorted(_WEB_ACQUIRE.glob('*.py'))
               for name in _unguarded_writes(path.read_text(encoding='utf-8'))]
    assert not missing, f'batch_set without refuse_vaulted: {missing}'


def test_the_gate_sees_an_unguarded_write():
    planted = 'def write(store, items):\n    store.batch_set(items)\n'
    assert _unguarded_writes(planted) == ['write']
