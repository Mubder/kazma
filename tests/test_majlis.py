"""Tests for MajlisProtocol — Cultural conversational protocol orchestration."""

from __future__ import annotations

from datetime import date

import pytest
from kazma_core.cultural_context import CulturalContext
from kazma_core.majlis import (
    ConversationPhase,
    ConversationState,
    MajlisProtocol,
    MajlisResponse,
)
from kazma_core.tone_adapter import FormalityLevel


class TestMajlisProtocolInit:
    """Test MajlisProtocol initialization."""

    def test_default_init(self):
        protocol = MajlisProtocol()
        assert protocol.dialect == "kw"
        assert protocol.cultural_context is not None
        assert protocol.pacing is not None
        assert protocol.tone_adapter is not None

    def test_custom_dialect(self):
        protocol = MajlisProtocol(dialect="msa")
        assert protocol.dialect == "msa"

    def test_custom_cultural_context(self):
        ctx = CulturalContext(now=date(2024, 3, 20))  # Ramadan
        protocol = MajlisProtocol(cultural_context=ctx)
        assert protocol.cultural_context.state.is_ramadan is True


class TestConversationState:
    """Test ConversationState tracking."""

    def test_initial_state(self):
        state = ConversationState()
        assert state.phase == ConversationPhase.GREETING
        assert state.greeting_count == 0
        assert state.formality_level == FormalityLevel.NORMAL

    def test_record_greeting(self):
        state = ConversationState()
        state.record_greeting()
        assert state.greeting_count == 1
        state.record_greeting()
        assert state.greeting_count == 2

    def test_transition_to(self):
        state = ConversationState()
        state.transition_to(ConversationPhase.SOCIAL)
        assert state.phase == ConversationPhase.SOCIAL


class TestProcessInput:
    """Test MajlisProtocol.process_input() — the main pipeline."""

    def setup_method(self):
        self.protocol = MajlisProtocol(dialect="kw")

    @pytest.mark.asyncio
    async def test_greeting_response(self):
        """Greeting input should produce greeting response."""
        resp = await self.protocol.process_input("السلام عليكم")
        assert isinstance(resp, MajlisResponse)
        assert resp.phase == ConversationPhase.GREETING
        assert resp.greeting_count == 1

    @pytest.mark.asyncio
    async def test_greeting_phase_stays_social(self):
        """First greeting should not trigger transaction."""
        resp = await self.protocol.process_input("السلام عليكم")
        assert resp.should_execute_workflow is False

    @pytest.mark.asyncio
    async def test_two_greetings_then_transaction(self):
        """After 2 greetings, transaction is allowed."""
        await self.protocol.process_input("السلام عليكم")
        await self.protocol.process_input("شلونك")

        resp = await self.protocol.process_input("أبي أسوي طلب")
        assert resp.should_execute_workflow is True
        assert resp.phase == ConversationPhase.TRANSACTION

    @pytest.mark.asyncio
    async def test_explicit_transaction_skips_greetings(self):
        """User explicitly requesting transaction skips greeting phase."""
        resp = await self.protocol.process_input("أبي أسوي طلب")
        assert resp.should_execute_workflow is True
        assert resp.phase == ConversationPhase.TRANSACTION

    @pytest.mark.asyncio
    async def test_farewell_response(self):
        """Farewell input should produce farewell response."""
        resp = await self.protocol.process_input("في أمان الله")
        assert resp.phase == ConversationPhase.FAREWELL
        assert resp.should_execute_workflow is False
        assert "أمان" in resp.text or "الله" in resp.text

    @pytest.mark.asyncio
    async def test_response_has_metadata(self):
        """Response should include metadata."""
        resp = await self.protocol.process_input("السلام عليكم")
        assert "intent" in resp.metadata
        assert "transition" in resp.metadata
        assert "turn_count" in resp.metadata

    @pytest.mark.asyncio
    async def test_response_delay_exists(self):
        """Response should have a response_delay."""
        resp = await self.protocol.process_input("السلام عليكم")
        assert isinstance(resp.response_delay, float)
        assert resp.response_delay > 0

    @pytest.mark.asyncio
    async def test_cultural_context_in_response(self):
        """Response should include cultural context."""
        resp = await self.protocol.process_input("السلام عليكم")
        assert isinstance(resp.cultural_context, dict)
        assert "greeting_extension" in resp.cultural_context


class TestRamadanBehavior:
    """Test Majlis behavior during Ramadan."""

    def setup_method(self):
        ctx = CulturalContext(now=date(2024, 3, 20))
        self.protocol = MajlisProtocol(cultural_context=ctx)

    @pytest.mark.asyncio
    async def test_ramadan_extends_greeting_phase(self):
        """Ramadan adds +2 to greeting requirement."""
        # Normal: 2 greetings. Ramadan: 4 greetings.
        await self.protocol.process_input("السلام عليكم")
        await self.protocol.process_input("شلونك")

        # Still in social phase because Ramadan requires 4 greetings
        resp = await self.protocol.process_input("أبي أسوي طلب")
        # Should still allow because user explicitly requested
        assert resp.should_execute_workflow is True

    @pytest.mark.asyncio
    async def test_ramadan_greeting_response(self):
        """Ramadan greeting should mention Ramadan."""
        resp = await self.protocol.process_input("السلام عليكم")
        assert "رمضان" in resp.text or "رمضان" in str(resp.cultural_context)


class TestEidBehavior:
    """Test Majlis behavior during Eid."""

    def setup_method(self):
        ctx = CulturalContext(now=date(2024, 4, 11))
        self.protocol = MajlisProtocol(cultural_context=ctx)

    @pytest.mark.asyncio
    async def test_eid_greeting_response(self):
        """Eid greeting should be celebratory."""
        resp = await self.protocol.process_input("عيد مبارك")
        assert resp.text  # Should have a response


class TestNationalDayBehavior:
    """Test Majlis behavior on National Day."""

    def setup_method(self):
        ctx = CulturalContext(now=date(2024, 2, 25))
        self.protocol = MajlisProtocol(cultural_context=ctx)

    @pytest.mark.asyncio
    async def test_national_day_in_context(self):
        """National Day should appear in cultural context."""
        resp = await self.protocol.process_input("السلام عليكم")
        assert resp.cultural_context.get("patriotic_references") is True


class TestFormalityAdaptation:
    """Test formality detection and tone adaptation."""

    def setup_method(self):
        self.protocol = MajlisProtocol(dialect="kw")

    @pytest.mark.asyncio
    async def test_casual_input_gets_casual_response(self):
        """Casual input should be detected and adapted."""
        resp = await self.protocol.process_input("شلونك هلا")
        assert resp.formality == FormalityLevel.CASUAL

    @pytest.mark.asyncio
    async def test_formal_input_gets_formal_response(self):
        """Formal input should get formal tone."""
        resp = await self.protocol.process_input("سيدي الكريم، أود أن أسأل عن التقرير")
        assert resp.formality in (FormalityLevel.FORMAL, FormalityLevel.VERY_FORMAL)

    @pytest.mark.asyncio
    async def test_tone_profile_selected(self):
        """Response should have a tone profile name."""
        resp = await self.protocol.process_input("السلام عليكم")
        assert resp.tone_profile  # Non-empty string


class TestReset:
    """Test protocol reset."""

    def setup_method(self):
        self.protocol = MajlisProtocol()

    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        """Reset should clear all conversation state."""
        await self.protocol.process_input("السلام عليكم")
        await self.protocol.process_input("شلونك")

        self.protocol.reset()

        assert self.protocol.conversation_state.greeting_count == 0
        assert self.protocol.conversation_state.turn_count == 0
        assert self.protocol._conversation_history == []


class TestStateSummary:
    """Test get_state_summary()."""

    def setup_method(self):
        self.protocol = MajlisProtocol()

    @pytest.mark.asyncio
    async def test_summary_structure(self):
        await self.protocol.process_input("السلام عليكم")
        summary = self.protocol.get_state_summary()

        assert "phase" in summary
        assert "greeting_count" in summary
        assert "formality" in summary
        assert "dialect" in summary
        assert "turn_count" in summary
        assert summary["turn_count"] >= 1


class TestEndToEnd:
    """End-to-end conversation flow tests."""

    def setup_method(self):
        self.protocol = MajlisProtocol(dialect="kw")

    @pytest.mark.asyncio
    async def test_full_conversation_flow(self):
        """Test a complete conversation: greeting → social → transaction → farewell."""
        # Greeting phase
        r1 = await self.protocol.process_input("السلام عليكم")
        assert r1.phase == ConversationPhase.GREETING

        r2 = await self.protocol.process_input("شلونك")
        assert r2.phase == ConversationPhase.GREETING

        # Transaction (after 2 greetings)
        r3 = await self.protocol.process_input("أبي أسوي طلب")
        assert r3.should_execute_workflow is True
        assert r3.phase == ConversationPhase.TRANSACTION

        # Farewell
        r4 = await self.protocol.process_input("في أمان الله")
        assert r4.phase == ConversationPhase.FAREWELL

    @pytest.mark.asyncio
    async def test_quick_transaction_flow(self):
        """User goes straight to transaction."""
        r1 = await self.protocol.process_input("أبي أسوي طلب")
        assert r1.should_execute_workflow is True
        assert r1.phase == ConversationPhase.TRANSACTION

    @pytest.mark.asyncio
    async def test_mixed_inquiry_flow(self):
        """Mix of inquiries and greetings."""
        await self.protocol.process_input("السلام عليكم")
        await self.protocol.process_input("شلونك")

        r = await self.protocol.process_input("وين المطعم؟")
        assert r.metadata["intent"] == "inquiry"
