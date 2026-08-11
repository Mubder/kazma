"""Unified document profile — the single source of truth for design + direction.

Every format engine (DOCX, PDF, XLSX, PPTX, HTML/Markdown) consumes a
:class:`DocProfile` so that any file Kazma creates / converts / edits / exports
shares one look regardless of extension. Direction (LTR/RTL) is a profile
property for *any* language — Arabic is one value, English another, mixed
handled by the run-script policy.

The alignment policy is the critical piece. It is the **single place** where the
Word BiDi ``w:jc`` inversion is encoded:

    Under ``w:bidi``, Word interprets ``w:jc="left"``/``"right"`` as the
    *logical* reading start/end, not the physical page edge. So for an RTL
    paragraph "pin to the reading start" (physical right) must be encoded as
    ``w:jc="start"`` (or ``"left"``) — **never** ``w:jc="right"``, which Word
    maps to the physical LEFT under BiDi.

Call sites ask for an *intent* (``start`` / ``justify`` / ``end``) and the
engine adapts it to the format-native value via this policy. No call site ever
picks a raw ``RIGHT``/``LEFT`` again, so the inversion cannot be reintroduced.

Empirically validated in desktop Word (Word→PDF pixel measurement):

    bidi only           → RIGHT
    bidi + jc=left      → RIGHT
    bidi + jc=start     → RIGHT
    bidi + jc=both      → RIGHT (short text) / justified
    bidi + jc=right     → LEFT   ← the bug this design eliminates
    bidi + jc=end       → LEFT
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from kazma_core.documents.style_theme import THEME, localized_chrome

__all__ = [
    "Direction",
    "AlignIntent",
    "DocProfile",
    "arabic_ratio",
    "is_arabic_dominant",
    "has_rtl_text",
    "detect_direction",
]

Direction = Literal["ltr", "rtl"]
AlignIntent = Literal["start", "justify", "end"]

# Arabic / RTL Unicode blocks: Arabic, Arabic Supplement, Arabic Extended-A,
# Arabic Presentation Forms-A/B. Used everywhere direction is decided.
_AR_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)


def arabic_ratio(text: str) -> float:
    """Fraction of alpha chars that are Arabic script (0.0 for non-Arabic text)."""
    if not text:
        return 0.0
    letters = [c for c in text if c.isalpha() or _AR_RE.match(c)]
    if not letters:
        return 0.0
    ar = sum(1 for c in letters if _AR_RE.match(c))
    return ar / len(letters)


def is_arabic_dominant(text: str, *, threshold: float = 0.35) -> bool:
    """True when enough Arabic letters are present to drive RTL layout."""
    if not text or not _AR_RE.search(text):
        return False
    return arabic_ratio(text) >= threshold or bool(_AR_RE.search(text[:200]))


def has_rtl_text(text: str) -> bool:
    """True if the string contains any RTL (Arabic) codepoint."""
    return bool(text) and bool(_AR_RE.search(text))


def detect_direction(text: str) -> Direction:
    """Auto-detect LTR/RTL from content."""
    return "rtl" if is_arabic_dominant(text) else "ltr"


@dataclass
class DocProfile:
    """Design + direction profile consumed by every format engine.

    Build via :meth:`for_content` (auto-detects direction from text, honours
    explicit ``lang``/``rtl`` overrides) rather than constructing directly.
    """

    direction: Direction = "ltr"
    language: str | None = None
    theme: dict[str, Any] = field(default_factory=lambda: dict(THEME))
    chrome: dict[str, str] = field(default_factory=dict)
    # Whether visual engines (PDF/ReportLab) must pre-shape Arabic. Word shapes
    # via OpenType, so DOCX ignores this; PDF needs it. Derived in
    # :meth:`for_content` as ``rtl or is_arabic_dominant(text)`` so an LTR doc
    # that still contains Arabic fragments gets shaped too.
    shape_arabic: bool = False

    # ------------------------------------------------------------------ #
    # convenience
    # ------------------------------------------------------------------ #
    @property
    def rtl(self) -> bool:
        return self.direction == "rtl"

    @property
    def lang_code(self) -> str:
        """BCP-47 tag for OOXML lang attributes (``ar-SA`` / ``en-US``)."""
        return "ar-SA" if self.rtl else "en-US"

    @classmethod
    def for_content(
        cls,
        text: str,
        *,
        language: str | None = None,
        rtl: bool | None = None,
    ) -> "DocProfile":
        """Build a profile from sample text with explicit overrides.

        Resolution order (last wins): auto-detect ← ``language`` ← ``rtl``.
        """
        lang = (language or "").strip().lower()
        if rtl is True:
            direction: Direction = "rtl"
        elif rtl is False:
            direction = "ltr"
        elif lang in ("ar", "arabic", "rtl"):
            direction = "rtl"
        elif lang in ("en", "english", "ltr"):
            direction = "ltr"
        else:
            direction = detect_direction(text)
        return cls(
            direction=direction,
            language=lang or None,
            chrome=localized_chrome(rtl=(direction == "rtl")),
            shape_arabic=(direction == "rtl") or is_arabic_dominant(text),
        )

    # ------------------------------------------------------------------ #
    # alignment policy — THE bidi/jc inversion lives here, once per format
    # ------------------------------------------------------------------ #
    def docx_jc(self, intent: AlignIntent) -> str:
        """Return the OOXML ``w:jc`` value for a paragraph *intent*.

        Using the bidi-logical keywords ``start``/``end`` is portable and
        unambiguous: ``start`` is the reading-start edge (physical right under
        RTL, physical left under LTR). ``justify`` is ``both``.
        """
        if intent == "justify":
            return "both"
        return "start" if intent == "start" else "end"

    def pdf_align(self, intent: AlignIntent) -> str:
        """ReportLab ``TA_*`` for an intent (visual engine; reshaping is external)."""
        if intent == "justify":
            return "TA_JUSTIFY"
        if self.rtl:
            return "TA_RIGHT" if intent == "start" else "TA_LEFT"
        return "TA_LEFT" if intent == "start" else "TA_RIGHT"

    def html_text_align(self, intent: AlignIntent) -> str:
        """CSS ``text-align`` for an intent under this direction."""
        if intent == "justify":
            return "justify"
        if self.rtl:
            return "right" if intent == "start" else "left"
        return "left" if intent == "start" else "right"

    @property
    def html_dir(self) -> str:
        return "rtl" if self.rtl else "ltr"

    # ------------------------------------------------------------------ #
    # run-script policy
    # ------------------------------------------------------------------ #
    def run_is_rtl(self, text: str) -> bool:
        """True when a run should carry RTL complex-script marks.

        Only runs that actually contain Arabic script are marked RTL; Latin
        runs embedded in an Arabic document are left neutral so Word's BiDi
        algorithm handles them naturally (no forced-reversal artifacts).
        """
        return self.rtl and has_rtl_text(text)
