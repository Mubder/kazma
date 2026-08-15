"""Intent engine policy tests — execute allowlist empty in Phase 0."""
from __future__ import annotations

import pytest

from kazma_core.agent.intent.entities import resolve_entities
from kazma_core.agent.intent.heuristics import detect_acts
from kazma_core.agent.intent.policy import decide
from kazma_core.agent.intent.registry import IntentHandler, IntentRegistry
from kazma_core.agent.intent.types import ActKind, EntitySet, IntentAct, RouteKind


def _decide(text, *, attachments=None, focus="normal", registry=None):
    acts = detect_acts(text, attachments)
    entities = resolve_entities(text=text, attachments=attachments, acts=acts)
    if registry is None:
        registry = IntentRegistry()
    return decide(focus=focus, acts=acts, entities=entities, registry=registry)


class TestPhase0NeverExecutes:
    """Phase 0: execute allowlist is EMPTY — nothing should execute."""

    @pytest.mark.parametrize("text", [
        "reproduce this PDF with better templates",
        "create a Word document with notes",
        "generate an Excel spreadsheet",
    ])
    def test_document_never_executes_phase0(self, text):
        route, _, reason, _ = _decide(text)
        assert route != RouteKind.EXECUTE, f"Phase 0 must not execute: {text} → {route}/{reason}"

    def test_research_not_executes(self):
        route, _, _, _ = _decide("research the impact of AI on Kuwait")
        assert route != RouteKind.EXECUTE

    def test_build_parser_not_executes(self):
        route, _, _, _ = _decide("build a PDF parser in Python")
        assert route != RouteKind.EXECUTE


class TestEntityGating:
    def test_missing_source_not_execute(self):
        """Reproduce language + no attachment → unresolved → constrain."""
        route, _, reason, _ = _decide("reproduce this PDF with better templates")
        assert route != RouteKind.EXECUTE
        assert reason in ("unresolved", "phase_allowlist", "multi_act")

    def test_ambiguous_not_execute(self):
        atts = [
            {"kind": "file", "mime": "application/pdf", "path": "a.pdf", "filename": "a.pdf"},
            {"kind": "file", "mime": "application/pdf", "path": "b.pdf", "filename": "b.pdf"},
        ]
        route, _, reason, _ = _decide("reproduce this PDF", attachments=atts)
        assert route != RouteKind.EXECUTE
        # Non-resolvable paths → unresolved; resolvable but N>1 → ambiguous.
        # Either way, not execute.
        assert reason in ("unresolved", "ambiguous", "phase_allowlist", "multi_act")


class TestKillSwitches:
    def test_execute_disabled(self, monkeypatch):
        monkeypatch.setenv("KAZMA_INTENT_EXECUTE", "0")
        route, _, reason, _ = _decide("reproduce this PDF")
        assert route != RouteKind.EXECUTE

    def test_engine_disabled(self, monkeypatch):
        monkeypatch.setenv("KAZMA_INTENT_ENGINE", "0")
        route, _, reason, _ = _decide("reproduce this PDF")
        assert route == RouteKind.LOOP
        assert reason == "engine_disabled"


class TestFocusGating:
    def test_continue_never_executes(self):
        route, _, reason, _ = _decide("reproduce this PDF", focus="continue")
        assert route != RouteKind.EXECUTE
        assert reason == "focus_continue"

    def test_cleanup_never_executes(self):
        route, _, reason, _ = _decide("reproduce this PDF", focus="cleanup")
        assert route != RouteKind.EXECUTE


class TestRegistry:
    def test_mutating_handler_without_execute_raises(self):
        registry = IntentRegistry()
        with pytest.raises(RuntimeError):
            registry.register(IntentHandler(
                name="bad",
                act="document_generate",
                required_slots=("format",),
                uses_execute=False,
                mutating=True,
                timeout_seconds=60.0,
                run=lambda *a, **k: None,
            ))

    def test_no_handler_not_execute(self):
        route, _, reason, _ = _decide("reproduce this PDF")
        assert route != RouteKind.EXECUTE
