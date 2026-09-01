"""Semantic approval-card payload (Bug 3 fix).

The WS permission-loop incident rendered semantic clarify cards as generic
Approve/YOLO because ``_scan_and_emit_hitl_interrupt`` →
``create_approval_event`` dropped the ``kind``/``items``/``options`` fields, so
chat.js's ``renderHitlCard`` ``_semCard`` branch (which gates on
``data.kind``) never fired. These tests lock that the event now carries the
semantic fields when present, and stays backward-compatible for security cards.
"""

from __future__ import annotations

from kazma_core.tracing.events import EventBridge


def test_security_approval_event_has_no_kind_items():
    ev = EventBridge.create_approval_event(
        thread_id="t1", tool_name="file_write",
        args={"path": "/x"}, message="approve?",
    )
    assert ev.data["status"] == "paused_for_approval"
    assert ev.data["tool"] == "file_write"
    assert "kind" not in ev.data
    assert "items" not in ev.data


def test_semantic_approval_event_carries_kind_and_items():
    items = [{
        "tool_call_id": "tc1", "tool": "schedule_task",
        "question": "when to fire?",
        "options": [
            {"id": "from_now", "label": "From now", "slots_patch": {"timing": "2026-08-19T05:17:00+00:00"}},
            {"id": "cancel", "label": "Cancel", "slots_patch": None},
        ],
    }]
    ev = EventBridge.create_approval_event(
        thread_id="t1", tool_name="", args={},
        message="when to fire?", kind="semantic_clarify", items=items,
    )
    # chat.js renderHitlCard gates on data.kind starting with 'semantic_'.
    assert ev.data.get("kind") == "semantic_clarify"
    # the option buttons are built from data.items[0].options.
    assert ev.data.get("items") == items
    assert [o["id"] for o in ev.data["items"][0]["options"]] == ["from_now", "cancel"]


def test_approval_event_carries_interrupt_id_when_set():
    ev = EventBridge.create_approval_event(
        thread_id="t1", tool_name="file_write",
        args={"path": "/x"}, interrupt_id="intr-1",
    )
    assert ev.data.get("interrupt_id") == "intr-1"
    bare = EventBridge.create_approval_event(
        thread_id="t1", tool_name="file_write", args={},
    )
    assert "interrupt_id" not in bare.data


def test_kind_only_included_when_truthy():
    ev = EventBridge.create_approval_event(
        thread_id="t1", tool_name="x", args={}, kind="", items=None,
    )
    assert "kind" not in ev.data
    assert "items" not in ev.data
