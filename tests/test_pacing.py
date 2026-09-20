"""Tests for ConversationPacing — Flow management and timing."""

from __future__ import annotations

import pytest
from kazma_core.pacing import (
    ConversationPacing,
    Intent,
    TransitionDecision,
)


class TestIntentDetection:
    """Test detect_intent() pattern matching."""

    def setup_method(self):
        self.pacing = ConversationPacing()

    def test_greeting_detection(self):
        assert self.pacing.detect_intent("السلام عليكم") == Intent.GREETING

    def test_warm_greeting(self):
        assert self.pacing.detect_intent("هلا والله") == Intent.GREETING

    def test_how_are_you(self):
        assert self.pacing.detect_intent("شلونك") == Intent.GREETING

    def test_farewell_detection(self):
        assert self.pacing.detect_intent("في أمان الله") == Intent.FAREWELL

    def test_farewell_slama(self):
        assert self.pacing.detect_intent("مع السلامة") == Intent.FAREWELL

    def test_transaction_want(self):
        assert self.pacing.detect_intent("أبي أسوي طلب") == Intent.TRANSACTION

    def test_transaction_request(self):
        assert self.pacing.detect_intent("ممكن تسوي لي") == Intent.TRANSACTION

    def test_transaction_price(self):
        assert self.pacing.detect_intent("كم سعر هذا؟") == Intent.TRANSACTION

    def test_inquiry_question(self):
        assert self.pacing.detect_intent("وين المطعم؟") == Intent.INQUIRY

    def test_inquiry_how(self):
        assert self.pacing.detect_intent("كيف أصل للمطعم") == Intent.INQUIRY

    def test_unknown_text(self):
        assert self.pacing.detect_intent("تمام") == Intent.UNKNOWN

    def test_empty_text(self):
        assert self.pacing.detect_intent("") == Intent.UNKNOWN


class TestTransitionDecision:
    """Test should_transition_to_transaction() logic."""

    def setup_method(self):
        self.pacing = ConversationPacing(min_greetings=2)

    @pytest.mark.asyncio
    async def test_explicit_transaction_skips(self):
        """User requesting transaction skips greeting phase."""
        conv = [
            {"role": "user", "text": "السلام عليكم"},
            {"role": "assistant", "text": "وعليكم السلام"},
        ]
        decision = await self.pacing.should_transition_to_transaction(conv, user_intent=Intent.TRANSACTION)
        assert decision == TransitionDecision.SKIP_TO_TRANSACTION

    @pytest.mark.asyncio
    async def test_stay_social_when_not_enough_greetings(self):
        """Stay social if greeting count is below minimum."""
        conv = [
            {"role": "user", "text": "السلام عليكم"},
            {"role": "assistant", "text": "وعليكم السلام"},
        ]
        decision = await self.pacing.should_transition_to_transaction(conv, user_intent=Intent.GREETING)
        assert decision == TransitionDecision.STAY_SOCIAL

    @pytest.mark.asyncio
    async def test_ready_when_enough_greetings(self):
        """Transition ready when greeting count meets minimum."""
        conv = [
            {"role": "user", "text": "السلام عليكم"},
            {"role": "assistant", "text": "وعليكم السلام"},
            {"role": "user", "text": "شلونك"},
            {"role": "assistant", "text": "الحمد لله بخير"},
        ]
        decision = await self.pacing.should_transition_to_transaction(conv, user_intent=Intent.UNKNOWN)
        assert decision == TransitionDecision.READY_TO_TRANSITION

    @pytest.mark.asyncio
    async def test_extension_raises_minimum(self):
        """Greeting extension from cultural context raises the minimum."""
        conv = [
            {"role": "user", "text": "السلام عليكم"},
            {"role": "assistant", "text": "وعليكم السلام"},
            {"role": "user", "text": "شلونك"},
            {"role": "assistant", "text": "تمام الحمد لله"},
        ]
        # With extension=2, need 4 greetings total
        decision = await self.pacing.should_transition_to_transaction(conv, greeting_extension=2)
        assert decision == TransitionDecision.STAY_SOCIAL

    @pytest.mark.asyncio
    async def test_extension_satisfied(self):
        """With extension, enough greetings allows transition."""
        conv = [
            {"role": "user", "text": "السلام عليكم"},
            {"role": "assistant", "text": "وعليكم السلام"},
            {"role": "user", "text": "شلونك"},
            {"role": "assistant", "text": "تمام الحمد لله"},
            {"role": "user", "text": "هلا والله"},
            {"role": "assistant", "text": "هلا فيك"},
            {"role": "user", "text": "الحمد لله"},
            {"role": "assistant", "text": "الله يسلمك"},
        ]
        decision = await self.pacing.should_transition_to_transaction(conv, greeting_extension=2)
        assert decision == TransitionDecision.READY_TO_TRANSITION

    @pytest.mark.asyncio
    async def test_empty_conversation(self):
        """Empty conversation stays social."""
        decision = await self.pacing.should_transition_to_transaction([])
        assert decision == TransitionDecision.STAY_SOCIAL


class TestResponseDelay:
    """Test calculate_response_delay()."""

    def setup_method(self):
        self.pacing = ConversationPacing()

    def test_casual_delay_range(self):
        delay = self.pacing.calculate_response_delay("casual")
        assert 0.3 <= delay <= 0.6

    def test_normal_delay_range(self):
        delay = self.pacing.calculate_response_delay("normal")
        assert 0.4 <= delay <= 1.0

    def test_formal_delay_range(self):
        delay = self.pacing.calculate_response_delay("formal")
        assert 0.6 <= delay <= 1.2

    def test_very_formal_delay_range(self):
        delay = self.pacing.calculate_response_delay("very_formal")
        assert 0.8 <= delay <= 1.5

    def test_none_delay(self):
        """None formality uses normal range."""
        delay = self.pacing.calculate_response_delay(None)
        assert 0.4 <= delay <= 1.0

    def test_enum_formality(self):
        """FormalityLevel enum works."""
        from kazma_core.tone_adapter import FormalityLevel

        delay = self.pacing.calculate_response_delay(FormalityLevel.CASUAL)
        assert 0.3 <= delay <= 0.6


class TestGreetingResponse:
    """Test get_greeting_response()."""

    def setup_method(self):
        self.pacing = ConversationPacing()

    def test_default_kuwaiti_greeting(self):
        resp = self.pacing.get_greeting_response(dialect="kw", greeting_number=1)
        assert isinstance(resp, str)
        assert len(resp) > 0

    def test_ramadan_greeting(self):
        resp = self.pacing.get_greeting_response(dialect="kw", is_ramadan=True, greeting_number=1)
        assert "رمضان" in resp

    def test_eid_greeting(self):
        resp = self.pacing.get_greeting_response(dialect="kw", is_eid=True, greeting_number=1)
        assert "عيد" in resp

    def test_eid_second_greeting(self):
        resp = self.pacing.get_greeting_response(dialect="kw", is_eid=True, greeting_number=2)
        assert "تقبل" in resp

    def test_greeting_number_clamps(self):
        """High greeting numbers clamp to last response."""
        resp = self.pacing.get_greeting_response(dialect="kw", greeting_number=100)
        assert isinstance(resp, str)


class TestPacingState:
    """Test PacingState tracking."""

    def setup_method(self):
        self.pacing = ConversationPacing()

    def test_initial_state(self):
        state = self.pacing.state
        assert state.greeting_count == 0
        assert state.is_in_social_phase is True
        assert state.turn_count == 0

    def test_reset(self):
        self.pacing._state.greeting_count = 5
        self.pacing.reset()
        assert self.pacing.state.greeting_count == 0


class TestShouldAllowTransaction:
    """Test should_allow_transaction()."""

    def setup_method(self):
        self.pacing = ConversationPacing()

    def test_transaction_text(self):
        assert self.pacing.should_allow_transaction("أبي أسوي طلب") is True

    def test_non_transaction_text(self):
        assert self.pacing.should_allow_transaction("السلام عليكم") is False
