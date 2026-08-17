"""Phase 7 — soul deltas under commitment (§R2.5).

Behavior mutation is a critical act: when ``soul_requires_confirm`` is ON, a
soul delta may NOT auto-apply until its commitment is ``committed`` (confirmed
via the HITL bus). Default OFF — safe rollout. The fence still applies;
commitment adds human authority on top.
"""

from __future__ import annotations

import pytest

from kazma_core.skills.self_improvement import (
    _soul_commitment_confirmed,
    _soul_requires_confirm,
    apply_agent_mutation,
    confirm_soul_delta,
    mint_soul_commitment,
)
from kazma_core.safety.commitment.store import create_commitment, get_commitment, Commitment


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Isolated ConfigStore + ops DB + SI enabled + the flag ON."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "1")
    monkeypatch.setenv("KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM", "1")
    return tmp_path


def test_flag_off_mint_returns_none(tmp_path, monkeypatch):
    """Flag OFF (default) → mint is a no-op (no commitment minted)."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
    monkeypatch.setenv("KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM", "0")
    assert _soul_requires_confirm() is False
    assert mint_soul_commitment("a delta", agent_id="a1") is None


def test_unconfirmed_soul_delta_held(isolated):
    """Flag ON + needs_confirm commitment → apply_agent_mutation holds (False)."""
    cid = mint_soul_commitment("learn to use tool X", agent_id="a1")
    assert cid is not None
    assert get_commitment(cid).status == "needs_confirm"
    # unconfirmed → held
    ok = apply_agent_mutation("a1", "learn to use tool X", commitment_id=cid)
    assert ok is False, "unconfirmed soul delta must NOT apply"


def test_confirmed_soul_delta_applies(isolated):
    """Flag ON + commitment confirmed → apply_agent_mutation proceeds."""
    cid = mint_soul_commitment("learn to use tool X", agent_id="a1")
    assert confirm_soul_delta(cid) is True
    assert get_commitment(cid).status == "committed"
    assert _soul_commitment_confirmed(cid) is True
    ok = apply_agent_mutation("a1", "learn to use tool X", commitment_id=cid)
    assert ok is True, "confirmed soul delta must apply"


def test_no_commitment_id_holds_when_flag_on(isolated):
    """Flag ON + no commitment_id → mint and hold (no legacy loophole)."""
    ok = apply_agent_mutation("a1", "a benign delta")
    assert ok is False, "unconfirmed soul delta must NOT apply without a cid"


def test_soul_commitment_is_critical_act_retention(isolated):
    """soul_delta commitments land in the critical-retention tier (§3.9)."""
    from kazma_core.safety.commitment.store import CRITICAL_ACTS
    assert "soul_delta" in CRITICAL_ACTS
    cid = mint_soul_commitment("d", agent_id="a1")
    assert get_commitment(cid).act == "soul_delta"
