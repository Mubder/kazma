"""Tone Adapter — Cultural tone profiles for Arabic conversational responses.

Adapts response tone based on formality level, dialect, and cultural context.
Preserves dialect authenticity while applying appropriate cultural markers.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# ── Formality levels ──────────────────────────────────────────────────


class FormalityLevel(Enum):
    """Formality levels for conversational responses."""

    CASUAL = "casual"  # Family, close friends
    NORMAL = "normal"  # General conversation
    FORMAL = "formal"  # Business, colleagues
    VERY_FORMAL = "very_formal"  # Government, official


# ── Tone profiles ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ToneProfile:
    """Configuration for a specific tone profile."""

    name: str
    prefix: str
    suffix: str
    formality: FormalityLevel
    avoid_slang: bool
    use_please_form: bool
    response_prefix: str  # How to start responses


TONE_PROFILES: dict[str, ToneProfile] = {
    "formal_business": ToneProfile(
        name="formal_business",
        prefix="سيدي/سيدتي",
        suffix="مع خالص التقدير",
        formality=FormalityLevel.FORMAL,
        avoid_slang=True,
        use_please_form=True,
        response_prefix="سيدي/سيدتي، ",
    ),
    "casual_family": ToneProfile(
        name="casual_family",
        prefix="يا غالي",
        suffix="",
        formality=FormalityLevel.CASUAL,
        avoid_slang=False,
        use_please_form=False,
        response_prefix="",
    ),
    "government_official": ToneProfile(
        name="government_official",
        prefix="سعادة/سمو",
        suffix="وتفضلوا بقبول فائق الاحترام والتقدير",
        formality=FormalityLevel.VERY_FORMAL,
        avoid_slang=True,
        use_please_form=True,
        response_prefix="سعادة/سمو، ",
    ),
    "general_polite": ToneProfile(
        name="general_polite",
        prefix="أخي/أختي",
        suffix="",
        formality=FormalityLevel.NORMAL,
        avoid_slang=False,
        use_please_form=False,
        response_prefix="",
    ),
    "ramadan_warm": ToneProfile(
        name="ramadan_warm",
        prefix="أخي/أختي",
        suffix="رمضان كريم",
        formality=FormalityLevel.NORMAL,
        avoid_slang=False,
        use_please_form=False,
        response_prefix="",
    ),
    "eid_celebratory": ToneProfile(
        name="eid_celebratory",
        prefix="",
        suffix="عيد مبارك عليكم",
        formality=FormalityLevel.NORMAL,
        avoid_slang=False,
        use_please_form=False,
        response_prefix="",
    ),
    "national_pride": ToneProfile(
        name="national_pride",
        prefix="",
        suffix="عشت الكويت",
        formality=FormalityLevel.NORMAL,
        avoid_slang=False,
        use_please_form=False,
        response_prefix="",
    ),
}


# ── Slang map (Kuwaiti informal → formal) ────────────────────────────

_KUWAITI_FORMAL_MAP: dict[str, str] = {
    "شلونك": "كيف حالك",
    "وين": "أين",
    "ليش": "لماذا",
    "هلا": "مرحباً",
    "تمام": "جيد",
    "اخوي": "صديقي",
    "شنو": "ماذا",
    "خوش": "جيد",
    "واجد": "كثير",
    "يالله": "هيا بنا",
}

# Precompiled once at import time — _formalize_text used to re-run
# re.compile() for every slang term on every single call.
# Unicode-aware word boundary: (?<!\w) and (?!\w) support Arabic.
_KUWAITI_FORMAL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?<!\w)" + re.escape(informal) + r"(?!\w)"), formal)
    for informal, formal in _KUWAITI_FORMAL_MAP.items()
]


# ── Public API ────────────────────────────────────────────────────────


class ToneAdapter:
    """Adapts response tone based on cultural context.

    Features:
    - Apply appropriate prefix/suffix based on formality
    - Adjust response style for cultural events
    - Preserve dialect authenticity when desired
    - Formalize casual expressions when formality requires
    """

    def __init__(self) -> None:
        self.profiles = dict(TONE_PROFILES)

    def get_profile(self, name: str) -> ToneProfile | None:
        """Get a tone profile by name."""
        return self.profiles.get(name)

    def list_profiles(self) -> list[str]:
        """List available profile names."""
        return list(self.profiles.keys())

    def select_profile(
        self,
        formality: FormalityLevel,
        dialect: str = "kw",
        is_ramadan: bool = False,
        is_eid: bool = False,
        is_national_day: bool = False,
    ) -> ToneProfile:
        """Select the best tone profile given context.

        Priority:
        1. Cultural event overrides (Ramadan, Eid, National Day)
        2. Formality level match
        3. Dialect-based defaults
        """
        # Cultural event overrides
        if is_eid:
            return self.profiles["eid_celebratory"]
        if is_ramadan:
            return self.profiles["ramadan_warm"]
        if is_national_day:
            return self.profiles["national_pride"]

        # Formality-based selection
        if formality == FormalityLevel.VERY_FORMAL:
            return self.profiles["government_official"]
        if formality == FormalityLevel.FORMAL:
            return self.profiles["formal_business"]
        if formality == FormalityLevel.CASUAL:
            return self.profiles["casual_family"]

        # Default: general polite
        return self.profiles["general_polite"]

    def adapt_response(
        self,
        response: str,
        profile_name: str | None = None,
        profile: ToneProfile | None = None,
        dialect: str = "kw",
        formalize: bool = False,
    ) -> str:
        """Adapt a response string to match the given tone profile.

        Args:
            response: Raw response text.
            profile_name: Name of profile to use (looked up internally).
            profile: Direct ToneProfile instance (overrides profile_name).
            dialect: Dialect code for dialect-specific adaptations.
            formalize: If True, replace slang with formal equivalents.

        Returns:
            Adapted response with appropriate prefix/suffix.
        """
        if profile is None and profile_name is not None:
            profile = self.profiles.get(profile_name)
        if profile is None:
            profile = self.profiles["general_polite"]

        # Formalize slang if requested or profile prefers formal language
        if formalize or profile.avoid_slang:
            response = self._formalize_text(response, dialect)

        # Apply prefix
        if profile.response_prefix and not response.startswith(profile.prefix):
            # Only add prefix if the response doesn't already start with it
            if not any(response.startswith(p) for p in [profile.prefix, profile.response_prefix]):
                response = profile.response_prefix + response

        # Apply suffix
        if profile.suffix and not response.endswith(profile.suffix):
            response = response.rstrip() + "\n" + profile.suffix

        return response

    def _formalize_text(self, text: str, dialect: str) -> str:
        """Replace informal dialect expressions with formal equivalents."""
        if dialect != "kw":
            return text  # Only Kuwaiti formalization implemented

        result = text
        for pattern, formal in _KUWAITI_FORMAL_PATTERNS:
            result = pattern.sub(formal, result)

        return result

    def determine_formality_from_text(self, text: str) -> FormalityLevel:
        """Infer formality level from text characteristics.

        Heuristics:
        - Very short, informal markers → CASUAL
        - Formal constructions (الذي, هذه, بناءً على) → FORMAL
        - Official language (سعادة, وزارة) → VERY_FORMAL
        - Default → NORMAL
        """
        text = text.strip()

        # Very formal indicators
        very_formal_markers = [
            "سعادة",
            "سمو",
            "فخامة",
            " Excellency",
            "وزارة",
            "حكومة",
            "رسمي",
        ]
        for marker in very_formal_markers:
            if marker in text:
                return FormalityLevel.VERY_FORMAL

        # Formal indicators
        formal_markers = [
            "سيدي",
            "سيدتي",
            "أخي الكريم",
            "أختي الكريمة",
            "بناءً على",
            "وفقاً",
            "أود أن",
            "أرجو",
            "التقرير",
            "المذكرة",
            "الخطاب",
        ]
        for marker in formal_markers:
            if marker in text:
                return FormalityLevel.FORMAL

        # Casual indicators
        casual_markers = [
            "شلونك",
            "هلا",
            "وين",
            "ليش",
            "شنو",
            "تمام",
            "خوش",
            "يالله",
            "اخوي",
        ]
        casual_count = sum(1 for m in casual_markers if m in text)
        if casual_count >= 2:
            return FormalityLevel.CASUAL

        return FormalityLevel.NORMAL

    def get_seasonal_prefix(
        self,
        is_ramadan: bool = False,
        is_eid: bool = False,
        is_national_day: bool = False,
    ) -> str:
        """Return a seasonal greeting prefix."""
        if is_eid:
            return "عيد مبارك! "
        if is_ramadan:
            return "رمضان كريم! "
        if is_national_day:
            return "يوم الكويت الوطني سعيد! "
        return ""
