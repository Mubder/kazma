"""Majlis Protocol — Cultural conversational protocol for Gulf Arabic interactions.

The Majlis (مجلس) is the traditional Gulf gathering space where conversation
follows specific cultural rhythms: greetings first, then social talk, then
business. This protocol enforces that pattern for AI conversations.

**Status: UNWIRED orchestrator.** Building blocks (pacing, tone, cultural
context) are live in the gateway graph; this composite is library-only until
wired. See ``docs/audits/UNWIRED_INVENTORY.md``.

Orchestrates:
- Dialect detection → cultural context
- Conversation pacing → greeting phase enforcement
- Tone adaptation → formality matching
- Cultural awareness → Ramadan, Eid, National Day modifiers
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from kazma_core.cultural_context import CulturalContext
from kazma_core.pacing import (
    ConversationPacing,
    Intent,
    TransitionDecision,
)
from kazma_core.tone_adapter import (
    FormalityLevel,
    ToneAdapter,
)

__all__ = ["ConversationPhase", "ConversationState", "MajlisProtocol", "MajlisResponse"]

logger = logging.getLogger(__name__)


# ── Conversation state ────────────────────────────────────────────────


class ConversationPhase(Enum):
    """Current phase of the conversation."""

    GREETING = "greeting"
    SOCIAL = "social"
    TRANSACTION = "transaction"
    FAREWELL = "farewell"


@dataclass
class ConversationState:
    """Tracks the full state of a Majlis conversation."""

    phase: ConversationPhase = ConversationPhase.GREETING
    greeting_count: int = 0
    formality_level: FormalityLevel = FormalityLevel.NORMAL
    dialect: str = "kw"
    turn_count: int = 0
    user_explicit_transaction: bool = False
    cultural_events: list[str] = field(default_factory=list)

    def record_greeting(self) -> None:
        """Record a greeting exchange."""
        self.greeting_count += 1

    def transition_to(self, phase: ConversationPhase) -> None:
        """Transition to a new conversation phase."""
        old = self.phase
        self.phase = phase
        logger.info("Phase transition: %s → %s", old.value, phase.value)


# ── Response data models ──────────────────────────────────────────────


@dataclass
class MajlisResponse:
    """Response from the Majlis protocol."""

    text: str
    phase: ConversationPhase
    formality: FormalityLevel
    tone_profile: str
    greeting_count: int
    should_execute_workflow: bool
    response_delay: float
    cultural_context: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


# ── Public API ────────────────────────────────────────────────────────


class MajlisProtocol:
    """Cultural conversational protocol for Gulf Arabic interactions.

    The Majlis protocol ensures conversations follow cultural norms:
    1. Greeting phase — exchange pleasantries
    2. Social phase — light conversation
    3. Transaction phase — execute workflows
    4. Farewell phase — proper goodbyes

    Cultural modifiers (Ramadan, Eid, National Day) extend greeting phases
    and adjust tone appropriately.
    """

    # Kuwaiti conversational patterns
    GREETING_PATTERNS: list[str] = [
        "السلام عليكم",
        "هلا والله",
        "شلونك",
        "كيف حالك",
        "ها شلونك",
        "صباح الخير",
        "مساء الخير",
    ]

    FAREWELL_PATTERNS: list[str] = [
        "في أمان الله",
        "الله يحفظك",
        "مع السلامة",
        "يله مع السلامة",
    ]

    def __init__(
        self,
        dialect: str = "kw",
        cultural_context: CulturalContext | None = None,
    ) -> None:
        self.dialect = dialect
        self.cultural_context = cultural_context or CulturalContext()
        self.pacing = ConversationPacing()
        self.tone_adapter = ToneAdapter()
        self.conversation_state = ConversationState(dialect=dialect)
        self._conversation_history: list[dict[str, Any]] = []

    def _detect_intent(self, text: str) -> Intent:
        """Detect intent from user text."""
        return self.pacing.detect_intent(text)

    def _determine_formality(self, text: str, user_history: list[dict[str, Any]] | None = None) -> FormalityLevel:
        """Determine formality level based on text and history.

        Considers:
        - User's language choices (text analysis)
        - Previous interactions (history)
        - Cultural context (events)
        """
        # Text-based formality detection
        text_formality = self.tone_adapter.determine_formality_from_text(text)

        # Cultural event boost
        cultural_mods = self.cultural_context.get_conversation_modifiers()
        boost = cultural_mods.get("formality_boost", 0)

        # Apply boost if cultural event
        if boost > 0:
            levels = list(FormalityLevel)
            current_idx = levels.index(text_formality)
            boosted_idx = min(current_idx + boost, len(levels) - 1)
            return levels[boosted_idx]

        return text_formality

    async def process_input(
        self,
        text: str,
        context: dict[str, Any] | None = None,
    ) -> MajlisResponse:
        """Process user input through the Majlis protocol.

        Pipeline:
        1. Detect conversational intent
        2. Determine formality level
        3. Check cultural context
        4. Apply pacing rules
        5. Adapt tone
        6. Generate response

        Args:
            text: User input text.
            context: Optional context dict with user_id, session_id, etc.

        Returns:
            MajlisResponse with adapted text and metadata.
        """
        ctx = context or {}

        # 1. Detect intent
        intent = self._detect_intent(text)

        # 2. Determine formality
        formality = self._determine_formality(text, ctx.get("user_history"))

        # 3. Cultural context
        cultural_mods = self.cultural_context.get_conversation_modifiers()
        greeting_ext = cultural_mods.get("greeting_extension", 0)

        # 4. Pacing check
        self.conversation_state.turn_count += 1

        if intent == Intent.GREETING:
            self.conversation_state.record_greeting()

        if intent == Intent.FAREWELL:
            self.conversation_state.transition_to(ConversationPhase.FAREWELL)
            farewell = self._generate_farewell(formality)
            return MajlisResponse(
                text=farewell,
                phase=ConversationPhase.FAREWELL,
                formality=formality,
                tone_profile="general_polite",
                greeting_count=self.conversation_state.greeting_count,
                should_execute_workflow=False,
                response_delay=self.pacing.calculate_response_delay(formality),
                cultural_context=cultural_mods,
            )

        # Check if we should transition to transaction
        transition = await self.pacing.should_transition_to_transaction(
            self._conversation_history,
            intent,
            greeting_extension=greeting_ext,
        )

        if intent == Intent.TRANSACTION:
            self.conversation_state.user_explicit_transaction = True

        # 5. Determine response based on phase and transition decision
        if transition == TransitionDecision.SKIP_TO_TRANSACTION:
            self.conversation_state.transition_to(ConversationPhase.TRANSACTION)
            response_text = self._acknowledge_transaction(text, formality)
            should_execute = True
        elif transition == TransitionDecision.READY_TO_TRANSITION:
            self.conversation_state.transition_to(ConversationPhase.TRANSACTION)
            response_text = self._transition_to_transaction(text, formality)
            should_execute = True
        elif intent == Intent.GREETING:
            response_text = self._generate_greeting_response(formality, cultural_mods)
            should_execute = False
        else:
            # Still in social phase
            response_text = self._generate_social_response(text, formality)
            should_execute = False

        # 6. Select tone profile and adapt
        tone_profile = self.tone_adapter.select_profile(
            formality=formality,
            dialect=self.dialect,
            is_ramadan=self.cultural_context.state.is_ramadan,
            is_eid=self.cultural_context.state.is_eid,
            is_national_day=self.cultural_context.state.is_national_day,
        )

        adapted_response = self.tone_adapter.adapt_response(
            response_text,
            profile=tone_profile,
            dialect=self.dialect,
        )

        # Record in history
        self._conversation_history.append({"role": "user", "text": text})
        self._conversation_history.append({"role": "assistant", "text": adapted_response})

        # Update formality state
        self.conversation_state.formality_level = formality

        return MajlisResponse(
            text=adapted_response,
            phase=self.conversation_state.phase,
            formality=formality,
            tone_profile=tone_profile.name,
            greeting_count=self.conversation_state.greeting_count,
            should_execute_workflow=should_execute,
            response_delay=self.pacing.calculate_response_delay(formality),
            cultural_context=cultural_mods,
            metadata={
                "intent": intent.value,
                "transition": transition.value,
                "turn_count": self.conversation_state.turn_count,
            },
        )

    def _generate_greeting_response(
        self,
        formality: FormalityLevel,
        cultural_mods: dict[str, Any],
    ) -> str:
        """Generate a greeting response based on context."""
        is_ramadan = self.cultural_context.state.is_ramadan
        is_eid = self.cultural_context.state.is_eid

        return self.pacing.get_greeting_response(
            dialect=self.dialect,
            is_ramadan=is_ramadan,
            is_eid=is_eid,
            greeting_number=self.conversation_state.greeting_count,
        )

    def _generate_social_response(self, text: str, formality: FormalityLevel) -> str:
        """Generate a social phase response."""
        # In social phase, respond warmly but don't rush to transaction
        if formality == FormalityLevel.CASUAL:
            return "هلا والله! شخبارك؟ عساك طيب"
        elif formality in (FormalityLevel.FORMAL, FormalityLevel.VERY_FORMAL):
            return "أهلاً وسهلاً بكم. كيف حالكم؟ عساكم بخير"
        return "الحمد لله بخير. شخبارك؟"

    def _acknowledge_transaction(self, text: str, formality: FormalityLevel) -> str:
        """Acknowledge that user wants to start a transaction."""
        if formality == FormalityLevel.VERY_FORMAL:
            return "سمو، نعم تفضلوا. جاهزين لخدمتكم"
        elif formality == FormalityLevel.FORMAL:
            return "سيدي/سيدتي، تفضلوا. أنا جاهز لمساعدتكم"
        return "أهلاً! تفضل، شسوي لك؟"

    def _transition_to_transaction(self, text: str, formality: FormalityLevel) -> str:
        """Transition from social to transactional phase."""
        if formality == FormalityLevel.VERY_FORMAL:
            return "تفضلوا، نحن جاهزين لخدمتكم. كيف نقدر نساعدكم؟"
        elif formality == FormalityLevel.FORMAL:
            return "تفضلوا، أنا جاهز. شتسوى؟"
        return "تمام، تفضل! شسوي لك؟"

    def _generate_farewell(self, formality: FormalityLevel) -> str:
        """Generate a farewell response."""
        if formality == FormalityLevel.VERY_FORMAL:
            return "في أمان الله. شكراً لزيارتكم الكريمة"
        elif formality == FormalityLevel.FORMAL:
            return "في أمان الله. شكراً لكم"
        return "يله مع السلامة! الله يحفظك"

    def reset(self) -> None:
        """Reset the protocol for a new conversation."""
        self.conversation_state = ConversationState(dialect=self.dialect)
        self._conversation_history = []
        self.pacing.reset()

    def get_state_summary(self) -> dict[str, Any]:
        """Return a summary of the current conversation state."""
        return {
            "phase": self.conversation_state.phase.value,
            "greeting_count": self.conversation_state.greeting_count,
            "formality": self.conversation_state.formality_level.value,
            "dialect": self.conversation_state.dialect,
            "turn_count": self.conversation_state.turn_count,
            "cultural_events": self.cultural_context.state.active_events,
            "is_ramadan": self.cultural_context.state.is_ramadan,
            "is_eid": self.cultural_context.state.is_eid,
        }
