"""Resume-value parity contract (PR1) — the test that would have caught the WS loop.

Every transport (HTTP / WebSocket / gateway) must produce the SAME resume shape
for a given interrupt ``kind``:

* ``semantic_clarify`` / ``semantic_confirm`` → ``{tool_call_id: option_id}``
* ``security`` (or absent)                 → ``{approved: bool, scope, ...}``

The original incident: WebSocket hardcoded ``{"approved","scope"}`` for every
card, so a semantic clarify never resolved and the model retried forever.
These tests lock the leaf contract (``build_resume_value`` / ``is_semantic_kind``)
that all transports must route through. PR2's architectural test then enforces
that they DO route through it.
"""

from __future__ import annotations

from kazma_core.safety.commitment.resume import build_resume_value, is_semantic_kind


def _semantic_payload(*, kind: str = "semantic_clarify", tcid: str = "tc1") -> dict:
    return {
        "type": "hitl_approval",
        "kind": kind,
        "items": [{
            "tool_call_id": tcid,
            "tool": "schedule_task",
            "commitment_id": "cmt_abc",
            "question": "when should I fire?",
            "options": [
                {"id": "from_now", "label": "From now",
                 "slots_patch": {"timing": "2026-08-19T05:17:00+00:00"}},
                {"id": "memory_anchor", "label": "Before the reset",
                 "slots_patch": {"timing": "2026-08-19T05:17:00+00:00"}},
                {"id": "cancel", "label": "Cancel", "slots_patch": None},
            ],
        }],
        "message": "when should I fire?",
    }


def _security_payload() -> dict:
    return {"type": "hitl_approval", "kind": "security",
            "tool": "file_write", "args": {"path": "/x"}, "message": "approve?"}


# ── is_semantic_kind ─────────────────────────────────────────────────


def test_is_semantic_kind_discriminates():
    assert is_semantic_kind(_semantic_payload()) is True
    assert is_semantic_kind(_semantic_payload(kind="semantic_confirm")) is True
    assert is_semantic_kind(_security_payload()) is False
    assert is_semantic_kind({}) is False
    assert is_semantic_kind(None) is False


# ── Semantic resume shape (the contract WS was violating) ────────────


def test_semantic_approve_picks_first_non_cancel_option():
    rv = build_resume_value(_semantic_payload(), approved=True)
    # MUST be {tcid: option_id}, NOT {approved, scope}.
    assert set(rv.keys()) == {"tc1"}
    assert rv["tc1"] == "from_now"


def test_semantic_deny_maps_to_cancel():
    rv = build_resume_value(_semantic_payload(), approved=False)
    assert rv == {"tc1": "cancel"}


def test_semantic_resume_is_never_security_shape():
    """The regression: WS used to send {approved, scope} for semantic cards."""
    rv = build_resume_value(_semantic_payload(), approved=True)
    assert "approved" not in rv
    assert "scope" not in rv


# ── Security resume shape ────────────────────────────────────────────


def test_security_resume_is_approved_plus_scope():
    rv = build_resume_value(_security_payload(), approved=True, scope="once")
    assert rv["approved"] is True
    assert rv["scope"] == "once"


def test_security_deny_carries_reason():
    rv = build_resume_value(_security_payload(), approved=False, scope="once",
                            reason="user denied")
    assert rv["approved"] is False
    assert rv["reason"] == "user denied"


# ── Parity: both kinds produce the shape the gate consumer expects ──


def test_semantic_resume_matches_gate_consumer_contract():
    """The gate (_commitment_resolve_gate) reads resume as {tcid: option_id}
    and looks up the option by id. Verify the produced dict is consumable."""
    payload = _semantic_payload(tcid="call_42")
    rv = build_resume_value(payload, approved=True)
    opt_id = rv.get("call_42")
    options = payload["items"][0]["options"]
    matched = next((o for o in options if o.get("id") == opt_id), None)
    assert matched is not None, "resume option id must exist in the payload options"
    assert matched["id"] != "cancel"
