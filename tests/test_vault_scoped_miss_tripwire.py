"""A vault miss that is really a missing tenant context says so, once.

``SecretVault.retrieve`` returns ``None`` for "absent" and for "stored one
scope over" alike, and every caller reads ``None`` as "not configured". Three
incidents came from that one ambiguity, each diagnosed far from its cause:
09:00 reminders failing ``no usable API key`` (2026-09-12), a DeepSeek key
read as absent and Z.AI substituted (2026-09-16), and ``kazma doctor``
blaming another install's vault for a key that decrypted fine. Each was
fixed at its own call site; the class stayed open because nothing noticed
the next caller that forgot to bind a tenant.

The tripwire sits at the miss, where every caller passes: a read with NO
tenant bound that misses a name stored under some tenant logs one WARNING
for that name, naming the tenant(s), never the value. It first landed
2026-09-17 and was reverted when CI hung; the hang turned out to be
``DocumentWorker.stop()``'s unbounded wait (fixed 2026-09-20), so this is
the re-land. See ``docs/KNOWN_GAPS.md``.
"""

from __future__ import annotations

import logging

import pytest
from kazma_core.security import vault as vault_mod
from kazma_core.security.vault import SecretVault
from kazma_core.tenant_context import tenant_scope

# A per-tenant name. Provider keys and mail tokens are install-scoped since
# 2026-09-25 (INSTALL_SCOPED_SECRETS): the vault never stores them per tenant,
# so they cannot be the example of a tenant-scoped miss any more.
NAME = "cfg:connectors.x.api_key"
SECRET = "sk-tripwire-not-a-real-key-4242"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "t" * 64)
    v = SecretVault(db_path=str(tmp_path / "vault.db"))
    yield v
    v.close()


def _tripwire_lines(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == vault_mod.__name__
        and r.levelno == logging.WARNING
        and "no tenant bound" in r.getMessage()
    ]


def test_a_contextless_miss_on_a_tenant_scoped_name_is_named_once(vault, caplog):
    with tenant_scope("default"):
        vault.store(NAME, SECRET, category="config")

    caplog.set_level(logging.WARNING, logger=vault_mod.__name__)
    assert vault.retrieve(NAME) is None, "the lookup itself stays strict"
    assert vault.retrieve(NAME) is None

    lines = _tripwire_lines(caplog)
    assert len(lines) == 1, f"once per name per process, got {lines}"
    assert NAME in lines[0] and "default" in lines[0]
    assert "tenant_scope" in lines[0], "the line must say how to fix the caller"
    assert SECRET not in caplog.text, "a value must never reach the log"


def test_each_name_is_reported_on_its_own(vault, caplog):
    other = "cfg:connectors.x.api_secret"
    with tenant_scope("default"):
        vault.store(NAME, SECRET)
        vault.store(other, SECRET + "-2")

    caplog.set_level(logging.WARNING, logger=vault_mod.__name__)
    vault.retrieve(NAME)
    vault.retrieve(other)
    vault.retrieve(NAME)

    lines = _tripwire_lines(caplog)
    assert len(lines) == 2
    assert any(NAME in ln for ln in lines) and any(other in ln for ln in lines)


def test_a_name_stored_nowhere_is_not_reported(vault, caplog):
    caplog.set_level(logging.WARNING, logger=vault_mod.__name__)
    assert vault.retrieve("cfg:never.stored") is None
    assert _tripwire_lines(caplog) == []


def test_a_caller_with_its_own_tenant_is_isolation_not_a_bug(vault, caplog):
    """Tenant B missing tenant A's key is the boundary working. Silence."""
    with tenant_scope("acme"):
        vault.store(NAME, SECRET)

    caplog.set_level(logging.WARNING, logger=vault_mod.__name__)
    with tenant_scope("beta"):
        assert vault.retrieve(NAME) is None
    assert vault.retrieve(NAME, tenant_id="beta") is None
    assert _tripwire_lines(caplog) == []


def test_a_hit_is_not_reported(vault, caplog):
    vault.store(NAME, SECRET)  # global
    with tenant_scope("default"):
        vault.store(NAME, SECRET + "-scoped")

    caplog.set_level(logging.WARNING, logger=vault_mod.__name__)
    assert vault.retrieve(NAME) == SECRET
    with tenant_scope("default"):
        assert vault.retrieve(NAME) == SECRET + "-scoped"
    assert _tripwire_lines(caplog) == []


def test_many_tenants_are_capped_in_the_line(vault, caplog):
    for tid in ("t1", "t2", "t3", "t4", "t5"):
        with tenant_scope(tid):
            vault.store(NAME, SECRET)

    caplog.set_level(logging.WARNING, logger=vault_mod.__name__)
    vault.retrieve(NAME)
    (line,) = _tripwire_lines(caplog)
    assert "t1, t2, t3 (+2 more)" in line


def test_the_operator_ladder_does_not_trip_it(vault, caplog, monkeypatch):
    """``retrieve_scoped``'s ``default`` rung is the sanctioned context-less read.

    On a single-operator box the ladder reads ``default`` first and hits, so
    the strict global read never runs and nothing is reported.
    """
    monkeypatch.setattr(vault_mod, "_operator_default_rung_allowed", lambda: True)
    with tenant_scope("default"):
        vault.store(NAME, SECRET)

    caplog.set_level(logging.WARNING, logger=vault_mod.__name__)
    assert vault_mod.retrieve_scoped(NAME, vault=vault) == SECRET
    assert _tripwire_lines(caplog) == []


def test_a_multi_user_contextless_reader_is_reported(vault, caplog, monkeypatch):
    """Where the rung is refused, the context-less reader is exactly the bug."""
    monkeypatch.setattr(vault_mod, "_operator_default_rung_allowed", lambda: False)
    with tenant_scope("default"):
        vault.store(NAME, SECRET)

    caplog.set_level(logging.WARNING, logger=vault_mod.__name__)
    assert vault_mod.retrieve_scoped(NAME, vault=vault) is None
    assert len(_tripwire_lines(caplog)) == 1


def test_negative_control_without_the_tripwire_the_miss_is_silent(vault, caplog, monkeypatch):
    """The assertions above detect the tripwire, not some other warning.

    With the probe replaced by a no-op, the identical scenario logs nothing,
    which is the pre-2026-09-25 behaviour every one of the incidents had.
    """
    monkeypatch.setattr(SecretVault, "_note_scoped_miss", lambda self, name, tid: None)
    with tenant_scope("default"):
        vault.store(NAME, SECRET)

    caplog.set_level(logging.WARNING, logger=vault_mod.__name__)
    assert vault.retrieve(NAME) is None
    assert _tripwire_lines(caplog) == []
