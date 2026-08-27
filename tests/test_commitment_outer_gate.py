"""Commitment outer-gate posture (audit MED): batch-wide fail-OPEN hole.

Regression: the outer ``try`` in ``_commitment_resolve_gate`` wrapped the WHOLE
semantic batch; any exception during setup or per-tool classification logged
"commitment gate skipped" and let ALL remaining tools through ungated.

Contract pinned here:
  1. A profile/classifier explosion on ONE tool denies THAT tool fail-closed
     and does NOT un-gate the rest of the batch.
  2. A genuinely structural failure (shared-setup exception) blocks the still-
     unresolved SEMANTIC tools (fail-closed) while read-only tools continue —
     never a blanket skip.
  3. The semantic probe itself exploding classifies the tool as SEMANTIC
     (gated), never as an ungated read.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import kazma_core.safety.commitment as commitment_pkg
import kazma_core.safety.side_effects as side_effects_mod
from kazma_core.agent.graph_tool_worker import _commitment_resolve_gate

SEMANTIC_TOOLS = ("schedule_task", "shell_exec")


def _allow() -> Any:
    return SimpleNamespace(
        decision="allow", rewritten_args=None, commitment_id=None,
        clarify_question=None, reason=None, options=None,
    )


def _tc(name: str, idx: int) -> dict[str, Any]:
    return {"id": f"call-{idx}", "name": name, "arguments": {}}


@pytest.fixture()
def gate_env(monkeypatch):
    """Force the commitment layer ON with fully injectable collaborators."""
    calls: list[str] = []

    def fake_needs_sem(name: str, *, _calls=calls) -> bool:
        return name in SEMANTIC_TOOLS

    def fake_authz(name, args, **kwargs):
        calls.append(name)
        if isinstance(kwargs.get("_raise_for"), str):
            pass  # unused; raise behavior set via raises attr below
        raise_for = getattr(fake_authz, "raise_for", None)
        if raise_for is not None and name == raise_for:
            raise RuntimeError(f"policy engine exploded for {name}")
        return _allow()

    def fake_beliefs(tenant: str):
        return []

    monkeypatch.setattr(side_effects_mod, "requires_semantic_check", fake_needs_sem)
    monkeypatch.setattr(
        "kazma_core.safety.commitment.constraints.is_commitment_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "kazma_core.safety.commitment.constraints.load_constraint_beliefs",
        fake_beliefs,
    )
    env = SimpleNamespace(calls=calls)
    env.fake_needs_sem = fake_needs_sem
    monkeypatch.setattr(commitment_pkg, "authorize_effect", fake_authz)
    env.fake_authz = fake_authz
    yield env


def _state() -> dict[str, Any]:
    return {"tenant_id": "t1", "thread_id": "thr-gate", "messages": []}


def test_one_raising_tool_denies_only_that_tool(gate_env, monkeypatch) -> None:
    gate_env.fake_authz.raise_for = "shell_exec"
    pending = [
        _tc("schedule_task", 0),   # semantic → allowed by fake engine
        _tc("shell_exec", 1),      # semantic → policy engine explodes
        _tc("file_read", 2),       # non-semantic → untouched passthrough
    ]
    out_pending, blocked = _commitment_resolve_gate(_state(), pending)

    names_kept = [tc["name"] for tc in out_pending]
    assert "shell_exec" not in names_kept, "raising tool must NOT execute"
    assert "schedule_task" in names_kept, "other semantic tools must stay gated+allowed"
    assert "file_read" in names_kept

    assert len(blocked) == 1
    bad = blocked[0]
    assert bad["name"] == "shell_exec"
    assert bad["is_error"] is True
    assert bad.get("outcome") == "terminal"
    assert "unavailable" in bad["content"]
    # The loop continued past the failure: both other tools were processed.
    assert gate_env.calls == ["schedule_task", "shell_exec"]


def test_setup_explosion_fails_closed_for_semantics_passes_reads(
    gate_env,
) -> None:
    """Outer-except coverage: structural breakage blocks semantics, not reads."""

    class BoomState(dict):
        def get(self, key: str, default: Any = None) -> Any:
            if key == "messages":
                raise RuntimeError("state store unavailable")
            return super().get(key, default)

    pending = [
        _tc("schedule_task", 0),
        _tc("shell_exec", 1),
        _tc("file_read", 2),
    ]
    out_pending, blocked = _commitment_resolve_gate(BoomState(**_state()), pending)

    kept = sorted(tc["name"] for tc in out_pending)
    assert kept == ["file_read"], "only non-semantic tools may continue"
    blocked_names = sorted(r["name"] for r in blocked)
    assert blocked_names == ["schedule_task", "shell_exec"]
    for r in blocked:
        assert r["is_error"] is True
        assert r.get("outcome") == "terminal"


def test_classifier_explosion_still_gates_tool(gate_env, monkeypatch) -> None:
    def exploding_needs_sem(name: str) -> bool:
        raise RuntimeError(f"classifier down for {name}")

    monkeypatch.setattr(side_effects_mod, "requires_semantic_check", exploding_needs_sem)

    pending = [_tc("mystery_write", 0), _tc("list_widgets", 1)]
    out_pending, blocked = _commitment_resolve_gate(_state(), pending)

    # Every tool was individually gated via authorize_effect despite the
    # classifier explosion (fail-closed probe → treated as semantic).
    assert sorted(gate_env.calls) == ["list_widgets", "mystery_write"]
    assert blocked == []
    assert {tc["name"] for tc in out_pending} == {"mystery_write", "list_widgets"}


def test_healthy_batch_unaffected(gate_env) -> None:
    pending = [_tc("schedule_task", 0), _tc("file_read", 1)]
    out_pending, blocked = _commitment_resolve_gate(_state(), pending)
    assert [tc["name"] for tc in out_pending] == ["file_read", "schedule_task"]
    assert blocked == []
    assert gate_env.calls == ["schedule_task"]
