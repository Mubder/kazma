"""Audit C remaining: soul confirm honesty, cancel/scope fail-closed, tenant memory."""

from __future__ import annotations

from unittest.mock import patch

from kazma_core.safety.commitment.authorize import authorize_effect
from kazma_core.safety.commitment.config import get_commitment_config
from kazma_core.safety.commitment.scope import ScopeToken, _swarm_scope_ctx
from kazma_core.skills.self_improvement import apply_agent_mutation


def test_soul_confirm_auto_on_in_production(monkeypatch):
    monkeypatch.delenv("KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM", raising=False)
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_MULTI_USER", "0")
    assert get_commitment_config()["soul_requires_confirm"] is True


def test_soul_confirm_env_off_wins_in_production(monkeypatch):
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.setenv("KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM", "0")
    assert get_commitment_config()["soul_requires_confirm"] is False


def test_soul_apply_without_cid_holds_when_on(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "1")
    monkeypatch.setenv("KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM", "1")
    assert apply_agent_mutation("a1", "a benign delta") is False


def test_swarm_scope_check_failure_denies(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE", "1")
    token = ScopeToken(thread_id="t1")
    reset = _swarm_scope_ctx.set(token)
    try:
        with patch(
            "kazma_core.safety.commitment.scope.is_act_within_scope",
            side_effect=RuntimeError("boom"),
        ):
            d = authorize_effect("file_write", {"path": "x", "op": "write"})
    finally:
        _swarm_scope_ctx.reset(reset)
    assert d.decision == "deny"
    assert "failed closed" in (d.reason or "")


def test_memory_tenant_fail_closed_when_enforced(monkeypatch):
    monkeypatch.setenv("KAZMA_MEMORY_ENFORCE_TENANT", "1")
    from kazma_ui.memory_api import _memory_tenant_id

    with patch(
        "kazma_core.tenant_isolation.require_tenant_id",
        side_effect=RuntimeError("no tenant"),
    ):
        assert _memory_tenant_id() == "__unscoped__"


def test_memory_tenant_enforced_when_multi_user(monkeypatch):
    monkeypatch.delenv("KAZMA_MEMORY_ENFORCE_TENANT", raising=False)
    monkeypatch.setenv("KAZMA_MULTI_USER", "1")
    from kazma_ui.memory_api import _memory_tenant_id

    with patch(
        "kazma_core.tenant_isolation.require_tenant_id",
        return_value="tenant-z",
    ):
        assert _memory_tenant_id() == "tenant-z"
