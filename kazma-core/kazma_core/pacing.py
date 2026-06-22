"""Conversation Pacing — Manages conversational flow and timing.

Enforces cultural norms around greeting phases, transition timing,
and response delays to create natural-feeling conversations.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ── Intent detection ──────────────────────────────────────────────────


class Intent(Enum):
    """Detected conversational intent."""

    GREETING = "greeting"
    INQUIRY = "inquiry"
    TRANSACTION = "transaction"
    FAREWELL = "farewell"
    UNKNOWN = "unknown"


class TransitionDecision(Enum):
    """Decision on whether to transition from social to transactional phase."""

    STAY_SOCIAL = "stay_social"
    READY_TO_TRANSITION = "ready_to_transition"
    SKIP_TO_TRANSACTION = "skip_to_transaction"


# ── Kuwaiti greeting patterns ─────────────────────────────────────────

_GREETING_PATTERNS: list[str] = [
    "السلام عليكم",
    "هلا والله",
    "شلونك",
    "كيف حالك",
    "ها شلونك",
    "هلا والله هلا",
    "صباح الخير",
    "مساء الخير",
    "هلا فيك",
    "أهلاً وسهلاً",
    "كيفك",
    "شخبارك",
    "عساك طيب",
    "الحمد لله",
    "الله يسلمك",
    "تمام الحمد لله",
]

_FAREWELL_PATTERNS: list[str] = [
    "في أمان الله",
    "الله يحفظك",
    "مع السلامة",
    "يله مع السلامة",
    "باي",
    "إلى اللقاء",
    "الله معك",
    "تصبحون على خير",
    "يالله مع السلامة",
]

_TRANSACTION_PATTERNS: list[str] = [
    "أبي",
    "أريد",
    "أبغى",
    "ابغى",
    "ممكن",
    "هل يمكن",
    "عندي طلب",
    "ابي اسوي",
    "أبي أسوي",
    "ممكن تسوي",
    "ممكن تسوي لي",
    "شلون أسوي",
    "كيف أقدر",
    "وين أقدر",
    "بغيت",
    "أبي أسأل عن",
    "عندي استفسار",
    "الطلب",
    "الفاتورة",
    "السعر",
    "كم سعر",
]


# ── Data models ───────────────────────────────────────────────────────


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""

    role: str  # "user" or "assistant"
    text: str
    intent: Intent = Intent.UNKNOWN
    timestamp: float | None = None


@dataclass
class PacingState:
    """Tracks pacing state for a conversation."""

    greeting_count: int = 0
    transaction_attempted: bool = False
    social_phase_complete: bool = False
    last_intent: Intent = Intent.UNKNOWN
    turn_count: int = 0

    @property
    def is_in_social_phase(self) -> bool:
        """True if still in greeting/social phase."""
        return not self.social_phase_complete


# ── Public API ────────────────────────────────────────────────────────


class ConversationPacing:
    """Manages conversational flow and timing.

    Enforces:
    - Minimum greeting exchanges before transactions
    - Appropriate response delays based on formality
    - Cultural event awareness (Ramadan extends social phase)
    - User override support (user can skip to transaction)
    """

    def __init__(
        self,
        min_greetings: int = 2,
        max_greeting_tokens: int = 500,
    ) -> None:
        self.min_greetings = min_greetings
        self.max_greeting_tokens = max_greeting_tokens
        self._state = PacingState()

    @property
    def state(self) -> PacingState:
        """Current pacing state."""
        return self._state

    def reset(self) -> None:
        """Reset pacing state for a new conversation."""
        self._state = PacingState()

    def detect_intent(self, text: str) -> Intent:
        """Detect the intent of a user message.

        Returns the most likely intent based on pattern matching.
        """
        text = text.strip()

        # Check for farewells first
        for pattern in _FAREWELL_PATTERNS:
            if pattern in text:
                return Intent.FAREWELL

        # Check for transaction intent
        for pattern in _TRANSACTION_PATTERNS:
            if pattern in text:
                return Intent.TRANSACTION

        # Check for greeting
        for pattern in _GREETING_PATTERNS:
            if pattern in text:
                return Intent.GREETING

        # Check for inquiry (question marks or question words)
        question_markers = ["?", "؟", "شلون", "كيف", "وين", "ليش", "شنو", "هل"]
        for marker in question_markers:
            if marker in text:
                return Intent.INQUIRY

        return Intent.UNKNOWN

    async def should_transition_to_transaction(
        self,
        conversation: list[dict[str, Any]],
        user_intent: Intent | None = None,
        greeting_extension: int = 0,
    ) -> TransitionDecision:
        """Determine whether to transition from social to transactional phase.

        Rules:
        - Minimum greeting exchanges before any transaction
        - If user explicitly requests transaction, skip to it
        - During Ramadan/Eid, extend greeting phase (greeting_extension)
        - Respect user's pace (don't rush)

        Args:
            conversation: List of {"role": "user"|"assistant", "text": "..."} dicts.
            user_intent: Detected intent of the latest user message.
            greeting_extension: Extra greetings required (from cultural context).

        Returns:
            TransitionDecision indicating what to do.
        """
        # If user explicitly wants a transaction, allow it immediately
        if user_intent == Intent.TRANSACTION:
            logger.info("User explicitly requested transaction — skipping social phase")
            self._state.transaction_attempted = True
            return TransitionDecision.SKIP_TO_TRANSACTION

        # Count greeting turns
        greeting_count = 0
        for turn in conversation:
            if turn.get("role") == "user":
                intent = self.detect_intent(turn.get("text", ""))
                if intent == Intent.GREETING:
                    greeting_count += 1

        self._state.greeting_count = greeting_count
        self._state.turn_count = len(conversation)

        required_greetings = self.min_greetings + greeting_extension

        if greeting_count < required_greetings:
            logger.info(
                "Greeting phase: %d/%d (extension=%d) — staying social",
                greeting_count,
                required_greetings,
                greeting_extension,
            )
            return TransitionDecision.STAY_SOCIAL

        # Enough greetings — ready to transition
        self._state.social_phase_complete = True
        logger.info(
            "Greeting phase complete: %d/%d greetings — ready for transaction",
            greeting_count,
            required_greetings,
        )
        return TransitionDecision.READY_TO_TRANSITION

    def calculate_response_delay(self, formality: Any = None) -> float:
        """Calculate appropriate response delay in seconds.

        More formal responses are slightly slower (not instant).
        This creates a natural conversational rhythm.

        Args:
            formality: FormalityLevel enum or string. If None, uses normal delay.

        Returns:
            Delay in seconds (0.3 – 1.5 range).
        """
        # Base delay range
        min_delay = 0.3
        max_delay = 1.5

        # Determine formality factor
        formality_str = ""
        if hasattr(formality, "value"):
            formality_str = formality.value
        elif isinstance(formality, str):
            formality_str = formality

        if "very_formal" in formality_str:
            # Very formal: slightly slower
            return random.uniform(0.8, max_delay)
        elif "formal" in formality_str:
            # Formal: moderate delay
            return random.uniform(0.6, 1.2)
        elif "casual" in formality_str:
            # Casual: quicker
            return random.uniform(min_delay, 0.6)
        else:
            # Normal: standard range
            return random.uniform(0.4, 1.0)

    def get_greeting_response(
        self,
        dialect: str = "kw",
        is_ramadan: bool = False,
        is_eid: bool = False,
        greeting_number: int = 1,
    ) -> str:
        """Generate an appropriate greeting response.

        Args:
            dialect: Dialect code for response style.
            is_ramadan: Whether it's Ramadan.
            is_eid: Whether it's Eid.
            greeting_number: Which greeting exchange this is (1-based).

        Returns:
            Culturally appropriate greeting response.
        """
        if is_eid:
            if greeting_number == 1:
                return "عيد مبارك عليكم! كل عام وأنتم بخير"
            return "تقبل الله منا ومنكم"

        if is_ramadan:
            if greeting_number == 1:
                return "رمضان كريم! عساك من عواده"
            return "اللهم بلغنا رمضان"

        # Default Kuwaiti greetings
        responses = [
            "الحمد لله بخير",
            "الله يسلمك",
            "تمام الحمد لله",
            "بخير الله يخليك",
        ]
        idx = min(greeting_number - 1, len(responses) - 1)
        return responses[idx]

    def should_allow_transaction(self, text: str) -> bool:
        """Check if the user is trying to initiate a transaction.

        Used to detect when the user wants to skip social pleasantries.
        """
        intent = self.detect_intent(text)
        return intent == Intent.TRANSACTION
