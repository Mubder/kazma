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

from dataclasses import dataclass, field
from typing import Any, Literal

from kazma_core.documents.arabic import (
    direction_of,
    first_strong,
    has_rtl,
    is_rtl_dominant,
    rtl_ratio,
)
from kazma_core.documents.style_theme import THEME, localized_chrome

__all__ = [
    "Direction",
    "AlignIntent",
    "Numerals",
    "Calendar",
    "DocProfile",
    "arabic_ratio",
    "is_arabic_dominant",
    "has_rtl_text",
    "detect_direction",
]

Direction = Literal["ltr", "rtl"]
AlignIntent = Literal["start", "justify", "end"]
Numerals = Literal["latn", "arab"]
Calendar = Literal["gregory", "islamic-umalqura"]

# Direction classification lives in :mod:`kazma_core.documents.arabic`, which
# decides by Unicode **bidi class** rather than by codepoint block. The old
# block regex missed Hebrew / Syriac / Thaana / N'Ko and Arabic Extended-B,
# counted harakat and Arabic-Indic digits as letters, and carried an "any Arabic
# character in the first 200" escape hatch that made ``threshold`` dead code:
# one Arabic word in an English report flipped the whole document to RTL, which
# reverses table columns and swaps in Arabic chrome and body fonts. The four
# names below stay as thin aliases so existing callers and tests keep working.


def arabic_ratio(text: str) -> float:
    """Fraction of strong directional characters that are RTL."""
    return rtl_ratio(text)


def is_arabic_dominant(text: str, *, threshold: float = 0.35) -> bool:
    """True when RTL text dominates enough to drive RTL page layout."""
    return is_rtl_dominant(text, threshold=threshold)


def has_rtl_text(text: str) -> bool:
    """True if the string contains any strong RTL character."""
    return has_rtl(text)


def detect_direction(text: str) -> Direction:
    """Auto-detect LTR/RTL from content (first-strong, ratio as tiebreaker)."""
    return direction_of(text)  # type: ignore[return-value]


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
    # Whether visual engines (PDF/ReportLab) must pre-shape complex script.
    # Word and browsers shape via OpenType, so DOCX/HTML ignore this; ReportLab
    # needs it. True whenever the document contains ANY strong RTL character,
    # not merely when RTL dominates — a Latin-dominant document with an Arabic
    # passage still needs a real shaping engine, and gating the good render
    # route on ``rtl`` while gating shaping on this flag is what stranded mixed
    # documents on the degraded path.
    shape_arabic: bool = False
    # Locale presentation. ``numerals`` picks the digit set used in chrome and
    # generated numbering; ``calendar`` picks the date system. Both default from
    # the resolved direction/language and can be overridden per document.
    numerals: Numerals = "latn"
    calendar: Calendar = "gregory"

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
        numerals: str | None = None,
        calendar: str | None = None,
        arabic_numerals_default: bool = False,
    ) -> DocProfile:
        """Build a profile from sample text with explicit overrides.

        Resolution order (last wins): auto-detect ← ``language`` ← ``rtl``.
        ``language`` accepts a bare code or a BCP-47 tag, so a caller can pass a
        session locale straight through. Note that an explicit ``language`` now
        *pins* direction — passing ``en`` for a document that happens to quote
        Arabic no longer lets content detection override the caller.
        """
        lang = (language or "").strip().lower()
        # Accept BCP-47 tags too ("ar-SA", "en-GB") — callers threading a
        # session locale through should not have to pre-trim it.
        base_lang = lang.split("-")[0].split("_")[0]
        if rtl is True:
            direction: Direction = "rtl"
        elif rtl is False:
            direction = "ltr"
        elif base_lang in ("ar", "arabic", "rtl", "fa", "ur", "he", "ps", "ckb"):
            direction = "rtl"
        elif base_lang in ("en", "english", "ltr"):
            direction = "ltr"
        elif lang:
            direction = "ltr"
        else:
            direction = detect_direction(text)
        resolved_numerals: Numerals = (
            numerals if numerals in ("latn", "arab")
            else ("arab" if arabic_numerals_default and direction == "rtl" else "latn")
        )
        resolved_calendar: Calendar = (
            calendar if calendar in ("gregory", "islamic-umalqura") else "gregory"
        )
        return cls(
            direction=direction,
            language=lang or None,
            chrome=localized_chrome(
                rtl=(direction == "rtl"),
                numerals=resolved_numerals,
            ),
            # ANY strong RTL character means the visual engine must shape.
            shape_arabic=(direction == "rtl") or has_rtl_text(text),
            numerals=resolved_numerals,
            calendar=resolved_calendar,
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
    # per-block direction
    # ------------------------------------------------------------------ #
    def block_direction(self, text: str) -> Direction:
        """Direction for ONE block, which is not always the document's.

        ``direction`` is a whole-document property — it drives margins, the
        section, chrome and the gutter. But a document is not required to be
        all one direction, and a mixed one (a bilingual archive, a report
        quoting Arabic sources, minutes with Arabic and English speakers) has
        blocks that each need their own.

        Resolving direction only at document level meant a 50/50 document was
        wrong either way: whichever side won, every block on the other side was
        aligned backwards. A tweet archive that was 35% Arabic laid every
        Arabic tweet out left-to-right; nudge the ratio and every English tweet
        would have been laid out right-to-left instead.

        Blocks with no strong directional character of their own — a divider, a
        bare number, a date — inherit the document direction rather than
        silently defaulting to LTR.
        """
        strong = first_strong(text)
        if strong is None:
            return self.direction
        return direction_of(text)  # type: ignore[return-value]

    def block_is_rtl(self, text: str) -> bool:
        return self.block_direction(text) == "rtl"

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
