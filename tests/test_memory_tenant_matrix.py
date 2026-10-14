"""Memory surfaces and turns agree on the tenant (Memory audit M07, MEM-S03).

Turns resolved their tenant one way (``memory.config.resolve_tenant_id``: the
mode, "default" in shared mode even under enforcement) and the Memory page
another (``memory_api._memory_tenant_id``: "default", or the principal's
tenant under enforcement). So in per-user mode "About me" was saved where no
turn read it; in per-platform mode "Don't remember this chat" was written
where the post-turn worker did not ask; and under enforcement a tenant-bound
user's turns went to "default" while their page, Forget and Export looked in
their own tenant.

Now: under enforcement both are the principal's tenant, and only "shared" can
be saved; not enforced, the page keeps the install's own view, and its ledger
and About me reach every tenant's turns. The matrix runs every cell: mode x
enforcement x principal -- About me saved through the route is what the turn
is shown, a "Don't remember" through the route stops the turn's writer, and a
Forget through the route finds the turn's memory. Negative control: the old
turn resolver in the cell the audit found.
"""

from __future__ import annotations

import itertools
import sqlite3
from types import SimpleNamespace

import pytest

MODES = ("shared", "per_platform", "per_user")


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    for var in ("KAZMA_MEMORY_ENFORCE_TENANT", "KAZMA_PRODUCTION", "KAZMA_MULTI_USER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr("kazma_core.memory.forget.chat_keys", lambda key: [key] if key else [])
    monkeypatch.setattr("kazma_core.tenant_isolation.multi_user_or_production", lambda: False)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, mp=monkeypatch)
    dual_write._reset_mirror()
    ops.close()
    db.close()


def _cell(mem, mode: str, enforced: bool):
    mem.mp.setattr("kazma_core.memory.config.memory_tenant_mode", lambda cfg=None: mode)
    if enforced:
        mem.mp.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    else:
        mem.mp.delenv("KAZMA_MEMORY_ENFORCE_TENANT", raising=False)


class _Principal:
    def __init__(self, tenant: str | None):
        self.tenant = tenant
        self.token = None

    def __enter__(self):
        if self.tenant:
            from kazma_core.tenant_context import set_current_tenant_id

            self.token = set_current_tenant_id(self.tenant)
        return self

    def __exit__(self, *exc):
        if self.token is not None:
            from kazma_core.tenant_context import reset_current_tenant_id

            reset_current_tenant_id(self.token)


CELLS = [
    (mode, enforced, principal)
    for mode, enforced, principal in itertools.product(MODES, (False, True), (None, "acme"))
    # Under enforcement only "shared" can be saved (the next test).
    if not (enforced and mode != "shared")
]


@pytest.mark.parametrize(("mode", "enforced", "principal"), CELLS)
def test_every_surface_reaches_the_turns_own_tenant(mem, mode, enforced, principal):
    from kazma_core.memory import forget
    from kazma_core.memory.config import resolve_tenant_id
    from kazma_core.memory.dual_write import mirror_episode
    from kazma_core.memory.profile import about_for_turn
    from kazma_ui import memory_api

    _cell(mem, mode, enforced)
    with _Principal(principal):
        turn_tenant = resolve_tenant_id("web", "", "sess-1")
        # About me saved through the page's route ...
        assert memory_api._set_about_me_sync({"about": "I am Sara."})["ok"]
        # ... is what the turn is shown.
        assert about_for_turn(turn_tenant)["about"] == "I am Sara."
        # A turn's memory, written under the turn's tenant ...
        eid = mirror_episode(session_id="sess-1", turn_number=1, user_text="what is my favourite colour",
                             assistant_text="Teal.", tenant_id=turn_tenant)
        assert eid
        # ... is found by Forget through the page.
        out = forget.forget_episode(eid, tenant_id=memory_api._memory_tenant_id())
        assert out["ok"], (mode, enforced, principal, turn_tenant, out)
        # "Don't remember this chat" through the page stops the turn's writer.
        assert memory_api._set_chat_memory_sync("sess-1", {"remember": False})["ok"]
        assert not forget.chat_remembered("sess-1", tenant_id=turn_tenant)
        assert mirror_episode(session_id="sess-1", turn_number=2, user_text="and my city?",
                              assistant_text="Lisbon.", tenant_id=turn_tenant) is None


@pytest.mark.parametrize("mode", ["per_platform", "per_user"])
def test_under_enforcement_only_shared_can_be_saved(mem, mode):
    from kazma_core.settings_validation import SettingRejected, validate_setting

    mem.mp.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    with pytest.raises(SettingRejected):
        validate_setting("memory.tenant_mode", mode)
    with pytest.raises(SettingRejected):
        validate_setting("memory", {"tenant_mode": mode})
    assert validate_setting("memory.tenant_mode", "shared")[0] == "shared"
    mem.mp.delenv("KAZMA_MEMORY_ENFORCE_TENANT")
    assert validate_setting("memory.tenant_mode", mode)[0] == mode  # allowed when not enforced


def test_the_old_turn_resolver_split_an_enforced_users_memory(mem):
    """Negative control: shared mode under enforcement, a bound principal --
    the old resolver put the turn in "default", the page looked in "acme"."""
    from kazma_core.memory import forget
    from kazma_core.memory.dual_write import mirror_episode
    from kazma_ui import memory_api

    _cell(mem, "shared", True)
    with _Principal("acme"):
        old_turn_tenant = "default"  # what resolve_tenant_id returned in shared mode
        eid = mirror_episode(session_id="sess-9", turn_number=1, user_text="a fact", assistant_text="noted",
                             tenant_id=old_turn_tenant)
        assert forget.forget_episode(eid, tenant_id=memory_api._memory_tenant_id()) == {
            "ok": False, "error": "not_found"}
