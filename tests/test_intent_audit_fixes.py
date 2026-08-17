"""Intent engine audit-fix tests (F1–F15, 2026-08-15).

Covers the behaviors introduced/changed by the intent-router audit fixes:
composer dispatch (F1), Tier-2 slot sanitization (F2), metrics on both paths
(F3), positive-signal gate (F5), focus dedupe (F10), positive EXECUTE
reachability, confidence boundary, sync/async parity, and focus=shift gating.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kazma_core.agent.intent.classify import (
    _sanitize_slots,
    classify_turn,
    classify_turn_sync,
)
from kazma_core.agent.intent.entities import resolve_entities
from kazma_core.agent.intent.heuristics import detect_acts
from kazma_core.agent.intent.policy import decide
from kazma_core.agent.intent.registry import IntentHandler, IntentRegistry
from kazma_core.agent.intent.types import ActKind, EntitySet, IntentAct, RouteKind


# ─── Helpers ────────────────────────────────────────────────────────────


def _registry_with_doc_handler() -> IntentRegistry:
    """Registry with a mutating document handler that uses execute()."""
    reg = IntentRegistry()
    reg.register(IntentHandler(
        name="document_generate",
        act="document_generate",
        required_slots=("format",),
        uses_execute=True,
        mutating=True,
        timeout_seconds=180.0,
        run=lambda *a, **k: None,
    ))
    return reg


def _registry_with_research_handler() -> IntentRegistry:
    reg = IntentRegistry()
    reg.register(IntentHandler(
        name="research_deep",
        act="research_deep",
        required_slots=("topic",),
        uses_execute=False,
        mutating=False,
        timeout_seconds=60.0,
        run=lambda *a, **k: None,
    ))
    return reg


def _decide(text, *, attachments=None, focus="normal", registry=None):
    acts = detect_acts(text, attachments)
    entities = resolve_entities(text=text, attachments=attachments, acts=acts)
    if registry is None:
        registry = IntentRegistry()
    return decide(focus=focus, acts=acts, entities=entities, registry=registry)


class FakeLLM:
    """Fake LLM that returns canned content or raises; records calls."""

    def __init__(self, response_content=None, raise_exc=None):
        self.response_content = response_content
        self.raise_exc = raise_exc
        self.called = False
        self.last_prompt = None

    async def chat(self, messages, tools=None, **kwargs):
        self.called = True
        self.last_prompt = messages[0]["content"] if messages else ""
        if self.raise_exc:
            raise self.raise_exc
        resp = MagicMock()
        resp.content = self.response_content
        return resp


# ─── F9.1 Positive EXECUTE reachable ───────────────────────────────────


class TestExecuteReachable:
    def test_document_generate_executes_with_inline_content(self):
        """document_generate is constrain — writes go through tool_worker HITL."""
        text = "create a PDF report from the following detailed meeting notes about the quarterly budget review"
        route, handler, reason, _ = _decide(text, registry=_registry_with_doc_handler())
        assert route == RouteKind.CONSTRAIN
        assert reason == "phase_allowlist"

    def test_research_deep_executes_with_topic(self):
        """A research_deep with a topic reaches EXECUTE."""
        route, handler, reason, _ = _decide(
            "deep research on renewable energy adoption",
            registry=_registry_with_research_handler(),
        )
        assert route == RouteKind.EXECUTE
        assert handler == "research_deep"
        assert reason == "checklist_passed"

    def test_document_without_source_or_inline_constrains(self):
        """document_generate with no source and short text → unresolved → constrain."""
        route, _, reason, _ = _decide(
            "create a PDF", registry=_registry_with_doc_handler()
        )
        assert route == RouteKind.CONSTRAIN
        assert reason == "unresolved"


# ─── F9.2 Confidence boundary ──────────────────────────────────────────


class TestConfidenceBoundary:
    def test_at_threshold_executes(self):
        """Confidence exactly at EXECUTE_MIN (0.86) → not low_confidence."""
        acts = (
            IntentAct(
                kind=ActKind.DOCUMENT_GENERATE, confidence=0.86,
                slots={"format": "pdf", "inline_content": True},
            ),
        )
        route, _, reason, _ = decide(
            focus="normal",
            acts=acts,
            entities=EntitySet(),
            registry=_registry_with_doc_handler(),
        )
        assert route == RouteKind.CONSTRAIN
        assert reason == "phase_allowlist"

    def test_below_threshold_constrains(self):
        """Confidence just below EXECUTE_MIN → low_confidence constrain."""
        acts = (
            IntentAct(
                kind=ActKind.DOCUMENT_GENERATE, confidence=0.85,
                slots={"format": "pdf", "inline_content": True},
            ),
        )
        route, _, reason, _ = decide(
            focus="normal",
            acts=acts,
            entities=EntitySet(),
            registry=_registry_with_doc_handler(),
        )
        assert route == RouteKind.CONSTRAIN
        assert reason == "low_confidence"


# ─── F1 Composer dispatch ──────────────────────────────────────────────


class TestComposerDispatch:
    def _registry_with_composer(self) -> IntentRegistry:
        reg = IntentRegistry()
        reg.register(IntentHandler(
            name="research_deep",
            act="research_deep",
            required_slots=("topic",),
            uses_execute=False,
            mutating=False,
            timeout_seconds=60.0,
            run=lambda *a, **k: None,
        ))
        reg.register(IntentHandler(
            name="document_generate",
            act="document_generate",
            required_slots=("format",),
            uses_execute=True,
            mutating=True,
            timeout_seconds=180.0,
            run=lambda *a, **k: None,
        ))
        reg.register_composer(
            frozenset({ActKind.RESEARCH_DEEP, ActKind.DOCUMENT_GENERATE}),
            IntentHandler(
                name="research_then_document",
                act="research_deep+document_generate",
                required_slots=("topic", "format"),
                uses_execute=False,
                mutating=False,
                timeout_seconds=120.0,
                run=lambda *a, **k: None,
            ),
        )
        return reg

    def test_multi_act_dispatches_composer(self):
        """research_deep + document_generate (both confident) → composer EXECUTE."""
        from kazma_core.agent.intent.types import EntitySet, IntentAct

        acts = (
            IntentAct(kind=ActKind.RESEARCH_DEEP, confidence=0.90, slots={"topic": "AI in Kuwait"}),
            IntentAct(kind=ActKind.DOCUMENT_GENERATE, confidence=0.86, slots={"format": "pdf"}),
        )
        route, handler, reason, _ = decide(
            focus="normal",
            acts=acts,
            entities=EntitySet(),
            registry=self._registry_with_composer(),
        )
        assert route == RouteKind.CONSTRAIN
        assert reason == "phase_allowlist"

    def test_composer_missing_slot_constrains(self):
        """Composer with a missing required slot → CONSTRAIN/missing_slot."""
        from kazma_core.agent.intent.types import EntitySet, IntentAct

        # No topic slot on research_deep → composer's ("topic","format") unmet.
        acts = (
            IntentAct(kind=ActKind.RESEARCH_DEEP, confidence=0.90, slots={}),
            IntentAct(kind=ActKind.DOCUMENT_GENERATE, confidence=0.86, slots={"format": "pdf"}),
        )
        route, _, reason, _ = decide(
            focus="normal",
            acts=acts,
            entities=EntitySet(),
            registry=self._registry_with_composer(),
        )
        assert route == RouteKind.CONSTRAIN
        # document_generate is no longer execute-allowlisted, so the
        # composer fails the phase gate before slot checks.
        assert reason in ("missing_slot", "phase_allowlist")

    def test_composer_low_confidence_constrains(self):
        """A participating act below EXECUTE_MIN → CONSTRAIN/low_confidence."""
        from kazma_core.agent.intent.types import EntitySet, IntentAct

        acts = (
            IntentAct(kind=ActKind.RESEARCH_DEEP, confidence=0.70, slots={"topic": "t"}),
            IntentAct(kind=ActKind.DOCUMENT_GENERATE, confidence=0.86, slots={"format": "pdf"}),
        )
        route, _, reason, _ = decide(
            focus="normal",
            acts=acts,
            entities=EntitySet(),
            registry=self._registry_with_composer(),
        )
        assert route == RouteKind.CONSTRAIN
        assert reason == "low_confidence"

    def test_multi_act_without_composer_constrains(self):
        """Multi-act with no matching composer → CONSTRAIN/multi_act."""
        from kazma_core.agent.intent.types import EntitySet, IntentAct

        reg = IntentRegistry()  # no handlers, no composers
        acts = (
            IntentAct(kind=ActKind.SWARM, confidence=0.80),
            IntentAct(kind=ActKind.REMIND, confidence=0.70),
        )
        route, _, reason, _ = decide(
            focus="normal", acts=acts, entities=EntitySet(), registry=reg
        )
        assert route == RouteKind.CONSTRAIN
        assert reason == "multi_act"

    def test_registry_resolve_finds_composer(self):
        """registry.resolve() returns a composer by name."""
        reg = self._registry_with_composer()
        h = reg.resolve("research_then_document")
        assert h is not None
        assert h.name == "research_then_document"


# ─── F2 Slot sanitization / injection ──────────────────────────────────


class TestSlotSanitization:
    def test_unknown_slots_dropped(self):
        clean = _sanitize_slots(
            ActKind.DOCUMENT_GENERATE,
            {"format": "pdf", "evil_flag": True, "path": "/etc/passwd"},
        )
        assert clean == {"format": "pdf"}

    def test_invalid_format_rejected(self):
        clean = _sanitize_slots(ActKind.DOCUMENT_GENERATE, {"format": "../../evil"})
        assert clean == {}

    def test_invalid_delivery_rejected(self):
        clean = _sanitize_slots(
            ActKind.DOCUMENT_GENERATE, {"format": "pdf", "deliver_to": "attacker.com"}
        )
        assert clean == {"format": "pdf"}

    def test_valid_delivery_kept(self):
        clean = _sanitize_slots(
            ActKind.DOCUMENT_GENERATE, {"format": "pdf", "deliver_to": "Telegram"}
        )
        assert clean == {"format": "pdf", "deliver_to": "telegram"}

    def test_topic_capped_and_cleaned(self):
        clean = _sanitize_slots(ActKind.RESEARCH_DEEP, {"topic": "x" * 1000})
        assert len(clean["topic"]) <= 300

    def test_no_slots_for_kind_without_allowlist(self):
        clean = _sanitize_slots(ActKind.SWARM, {"anything": "here"})
        assert clean == {}

    def test_non_dict_slots_rejected(self):
        assert _sanitize_slots(ActKind.DOCUMENT_GENERATE, "not-a-dict") == {}
        assert _sanitize_slots(ActKind.DOCUMENT_GENERATE, None) == {}

    @pytest.mark.asyncio
    async def test_injection_slots_never_reach_execute(self):
        """LLM returns malicious slots; they are sanitized before routing."""
        llm = FakeLLM(
            response_content=(
                '{"acts": [{"kind": "document_generate", "confidence": 1.0, '
                '"slots": {"format": "pdf", "inline_content": true, '
                '"__import__": "os", "path": "/etc/shadow"}}]}'
            )
        )
        text = "please help me with the quarterly budget review document and the meeting notes summary today"
        d = await classify_turn(text, llm=llm)
        doc_acts = [a for a in d.acts if a.kind == ActKind.DOCUMENT_GENERATE]
        for a in doc_acts:
            assert "__import__" not in a.slots
            assert "path" not in a.slots
            if "format" in a.slots:
                assert a.slots["format"] in ("pdf", "docx", "xlsx", "pptx")


# ─── F3 Metrics on both paths ──────────────────────────────────────────


class TestMetricsBothPaths:
    def test_sync_records_metrics(self):
        from kazma_core.agent.intent.metrics import get_intent_counters

        before = dict(get_intent_counters())
        classify_turn_sync("create a PDF report from these detailed notes about the review")
        after = get_intent_counters()
        # At least one counter changed.
        assert sum(after.values()) > sum(before.values())

    @pytest.mark.asyncio
    async def test_async_records_metrics(self):
        from kazma_core.agent.intent.metrics import get_intent_counters

        before = dict(get_intent_counters())
        await classify_turn("create a PDF report from these detailed notes about the review")
        after = get_intent_counters()
        assert sum(after.values()) > sum(before.values())


# ─── F5 Positive-signal gate ───────────────────────────────────────────


class TestPositiveSignalGate:
    @pytest.mark.asyncio
    async def test_plain_chat_does_not_call_llm(self):
        """A GENERAL-only turn (no heuristic act) skips Tier-2 entirely."""
        llm = FakeLLM(response_content='{"acts": []}')
        # Long plain-chat message with no act keywords.
        text = "I was thinking about how nice the weather has been lately and whether I should go for a walk this afternoon"
        d = await classify_turn(text, llm=llm)
        assert not llm.called, "GENERAL-only turn must not invoke the Tier-2 LLM"
        assert d.route in (RouteKind.LOOP, RouteKind.CONSTRAIN)

    @pytest.mark.asyncio
    async def test_positive_signal_still_refines(self):
        """A turn with a detected act but ambiguous still calls the LLM."""
        llm = FakeLLM(
            response_content='{"acts": [{"kind": "research", "confidence": 0.7, "slots": {}}]}'
        )
        # 'research' keyword triggers a 0.70 act (single act but < 0.80 → gray).
        text = "can you research the impact of recent policy changes on small businesses in the region"
        d = await classify_turn(text, llm=llm)
        assert llm.called


# ─── F10 Focus dedupe ──────────────────────────────────────────────────


class TestFocusDedupe:
    def test_precomputed_focus_accepted_sync(self):
        d = classify_turn_sync("hello there", focus="normal")
        assert d.focus == "normal"

    def test_precomputed_focus_gates_route(self):
        """Passing focus='continue' forces the focus gate even on a doc ask."""
        route, _, reason, _ = _decide("reproduce this PDF", focus="continue")
        assert route == RouteKind.LOOP
        assert reason == "focus_continue"

    @pytest.mark.asyncio
    async def test_precomputed_focus_accepted_async(self):
        d = await classify_turn("hello there", focus="normal")
        assert d.focus == "normal"


# ─── F9.6 focus=shift gate ─────────────────────────────────────────────


class TestFocusShiftGate:
    def test_focus_shift_loops(self):
        route, _, reason, _ = _decide("reproduce this PDF", focus="shift")
        assert route == RouteKind.LOOP
        assert reason == "focus_shift"

    def test_focus_cleanup_loops(self):
        route, _, reason, _ = _decide("reproduce this PDF", focus="cleanup")
        assert route == RouteKind.LOOP
        assert reason == "focus_cleanup"


# ─── command= merge path (audit follow-up: previously untested) ────────


class TestCommandMergeActs:
    def test_command_seeds_act_at_high_confidence(self):
        """A bare chat text + command=research seeds a research act @0.95."""
        d = classify_turn_sync("hello there", command="research", use_embedding_drift=False)
        seeded = [a for a in d.acts if a.kind == ActKind.RESEARCH]
        assert seeded, "command=research must seed a RESEARCH act"
        assert seeded[0].confidence == 0.95
        assert seeded[0].source == "command"

    def test_command_documents_maps_to_document_intel(self):
        d = classify_turn_sync("hello there", command="documents", use_embedding_drift=False)
        seeded = [a for a in d.acts if a.kind == ActKind.DOCUMENT_INTEL]
        assert seeded
        assert seeded[0].source == "command"

    def test_command_does_not_duplicate_existing_kind(self):
        """Text already detected as research + command=research → one act."""
        d = classify_turn_sync(
            "research the topic thoroughly",
            command="research",
            use_embedding_drift=False,
        )
        research_acts = [a for a in d.acts if a.kind == ActKind.RESEARCH]
        assert len(research_acts) == 1

    def test_unknown_command_is_ignored(self):
        d = classify_turn_sync("hello there", command="no_such_cmd", use_embedding_drift=False)
        assert [a.kind for a in d.acts] == [ActKind.GENERAL]

    @pytest.mark.asyncio
    async def test_command_accepted_on_async_path(self):
        d = await classify_turn("hello there", command="swarm", use_embedding_drift=False)
        seeded = [a for a in d.acts if a.kind == ActKind.SWARM]
        assert seeded
        assert seeded[0].confidence == 0.95
        assert seeded[0].source == "command"


# ─── F9.5 Sync/async parity (Tier-2 disabled) ─────────────────────────


class TestSyncAsyncParity:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("text", [
        "create a PDF report from these detailed notes about the quarterly budget review meeting",
        "deep research on solar energy trends",
        "what is the capital of France",
    ])
    async def test_sync_async_same_route(self, monkeypatch, text):
        monkeypatch.setenv("KAZMA_INTENT_TIER2", "0")
        sync_d = classify_turn_sync(text, use_embedding_drift=False)
        async_d = await classify_turn(text, llm=None, use_embedding_drift=False)
        assert sync_d.route == async_d.route
        assert {a.kind for a in sync_d.acts} == {a.kind for a in async_d.acts}
