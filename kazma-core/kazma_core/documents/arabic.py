"""Arabic (and general RTL) text policy — the single home for every transform.

Before this module the codebase held four partial notions of "what Arabic is":
a block regex in :mod:`~kazma_core.documents.profile`, a presentation-form
regex in :mod:`~kazma_core.documents.quality`, a bidi-class probe in
:mod:`~kazma_core.documents.ocr.tesseract`, and a hand-rolled shape/wrap in
:mod:`~kazma_core.documents.rich_render`. They disagreed. Everything Arabic
now routes through the four public operations here:

``direction_of`` / ``has_rtl`` / ``rtl_ratio``
    Direction classification by **Unicode bidi class** (``R``/``AL``), not by
    codepoint block. Covers Hebrew, Syriac, Thaana, N'Ko, Adlam and Arabic
    Extended-B, which a block regex silently missed.

``to_logical``
    Repair an extracted text layer: NFKC folds Arabic Presentation Forms-A/B
    (``U+FB50–FDFF`` / ``U+FE70–FEFF``) and the lam-alef ligatures back to
    logical base letters, and explicit bidi controls are dropped. A legacy
    "visual glyph dump" PDF becomes searchable text without an OCR round trip.

``fold_for_search``
    The search normal form: NFKC, harakat/tatweel removal, alef-hamza /
    taa-marbuta / alef-maqsura / hamza-carrier folding, and Arabic-Indic and
    Eastern Arabic-Indic digits mapped to ASCII. Applied at BOTH index time
    and query time — a fold applied to only one side is worse than none.

``shape_spans``
    Paragraph-level shaping for visual drawing engines (ReportLab). The
    Unicode BiDi algorithm is defined over a *paragraph*; shaping each styled
    markdown span on its own reorders each piece internally and then emits the
    pieces in logical order, so the sentence reads inside-out and words jam at
    every style boundary. ``shape_spans`` resolves embedding levels once over
    the whole paragraph, splits the styled spans at level boundaries, applies
    the UBA rule L2 reordering to those segments, and only then reshapes each
    one. Style survives the reorder because it rides on the segment.

Word (DOCX) and browsers do their own shaping; they must never call into the
shaping half of this module. They may still use the classification and folding
halves, which are engine-independent.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "Direction",
    "ShapedSegment",
    "direction_of",
    "first_strong",
    "has_rtl",
    "rtl_ratio",
    "is_rtl_dominant",
    "to_logical",
    "fold_for_search",
    "strip_bidi_controls",
    "shape_spans",
    "shape_text",
    "to_arabic_numerals",
    "PRESENTATION_FORMS_RE",
]

Direction = str  # "ltr" | "rtl"

# ── character classes ────────────────────────────────────────────────────

# Explicit bidi formatting controls. They carry no content and corrupt both
# search folding and the shaping pipeline's index alignment.
_BIDI_CONTROLS = "‎‏؜‪‫‬‭‮⁦⁧⁨⁩"
_BIDI_CONTROL_RE = re.compile(f"[{_BIDI_CONTROLS}]")

# Arabic Presentation Forms-A/B — what a "visual glyph dump" extractor emits.
PRESENTATION_FORMS_RE = re.compile(r"[ﭐ-﷿ﹰ-﻿]")

# Harakat (short vowels), Quranic annotation marks, and the superscript alef.
# All are Unicode category Mn, so SQLite's unicode61 tokenizer treats them as
# separators and shatters a vocalized word into individual letters.
_HARAKAT_RE = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")

_TATWEEL = "ـ"  # kashida — pure justification filler, never semantic

# Orthographic variants that must collapse for search. A user typing "احمد"
# must find "أحمد"; a document set using "المكتبه" must be found by "المكتبة".
_SEARCH_FOLD_MAP = {
    "آ": "ا",  # آ alef madda      → ا
    "أ": "ا",  # أ alef hamza above→ ا
    "إ": "ا",  # إ alef hamza below→ ا
    "ٱ": "ا",  # ٱ alef wasla      → ا
    "ى": "ي",  # ى alef maqsura    → ي
    "ة": "ه",  # ة taa marbuta     → ه
    "ؤ": "و",  # ؤ waw hamza       → و
    "ئ": "ي",  # ئ yeh hamza       → ي
    "ك": "ك",  # ك keheh normalised below
    "ک": "ك",  # ک farsi keheh     → ك
    "ی": "ي",  # ی farsi yeh       → ي
    "ھ": "ه",  # ھ heh doachashmee → ه
}

# Arabic-Indic (٠-٩) and Eastern Arabic-Indic (۰-۹) digits.
_ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_EASTERN_INDIC = "۰۱۲۳۴۵۶۷۸۹"
_DIGIT_FOLD = {ord(c): str(i) for i, c in enumerate(_ARABIC_INDIC)}
_DIGIT_FOLD.update({ord(c): str(i) for i, c in enumerate(_EASTERN_INDIC)})
_ASCII_TO_ARABIC = {ord(str(i)): _ARABIC_INDIC[i] for i in range(10)}

_SEARCH_TRANSLATE = {ord(k): v for k, v in _SEARCH_FOLD_MAP.items()}
_SEARCH_TRANSLATE.update(_DIGIT_FOLD)
_SEARCH_TRANSLATE[ord(_TATWEEL)] = None  # type: ignore[assignment]
for _c in _BIDI_CONTROLS:
    _SEARCH_TRANSLATE[ord(_c)] = None  # type: ignore[assignment]

_WS_RE = re.compile(r"\s+")


# ── classification (bidi-class based, never a codepoint block) ────────────


def _is_strong_rtl(char: str) -> bool:
    return unicodedata.bidirectional(char) in ("R", "AL")


def _is_strong_ltr(char: str) -> bool:
    return unicodedata.bidirectional(char) == "L"


def has_rtl(text: str) -> bool:
    """True when *text* contains any strong right-to-left character."""
    return any(_is_strong_rtl(c) for c in text or "")


def first_strong(text: str) -> Direction | None:
    """Direction of the first strong character, or ``None`` if there is none.

    ``None`` is the meaningful case: a divider rule, a bare number, a date or
    an empty line has no direction of its own and must inherit the document's
    rather than defaulting to LTR.
    """
    for char in text or "":
        if _is_strong_rtl(char):
            return "rtl"
        if _is_strong_ltr(char):
            return "ltr"
    return None


def rtl_ratio(text: str) -> float:
    """Fraction of *strong directional* characters that are RTL.

    Combining marks (``Mn``) and digits are excluded from the denominator:
    counting harakat as letters inflated the ratio on vocalized Arabic, and
    counting Arabic-Indic digits inflated it on numeric tables.
    """
    if not text:
        return 0.0
    strong = [c for c in text if _is_strong_rtl(c) or _is_strong_ltr(c)]
    if not strong:
        return 0.0
    return sum(1 for c in strong if _is_strong_rtl(c)) / len(strong)


def is_rtl_dominant(text: str, *, threshold: float = 0.35) -> bool:
    """True when RTL text dominates enough to drive the page layout.

    Deliberately has **no** "any RTL character near the start" escape hatch.
    The previous ``or _AR_RE.search(text[:200])`` clause made ``threshold``
    dead code and flipped an English report to RTL — reversed table columns,
    Arabic chrome and all — because a product name appeared in the first
    paragraph.
    """
    return bool(text) and has_rtl(text) and rtl_ratio(text) >= threshold


def direction_of(text: str, *, threshold: float = 0.35) -> Direction:
    """Resolve paragraph direction the way ``dir=auto`` and the UBA do.

    First-strong wins when it agrees with a non-trivial share of the text;
    otherwise the ratio decides. This keeps a mostly-Arabic document RTL even
    when it opens with a Latin heading, and keeps a mostly-English document
    LTR even when it opens with an Arabic quotation.
    """
    if not text:
        return "ltr"
    ratio = rtl_ratio(text)
    for char in text:
        if _is_strong_rtl(char):
            return "rtl" if ratio >= threshold * 0.5 else "ltr"
        if _is_strong_ltr(char):
            return "rtl" if ratio >= (1.0 - threshold) else "ltr"
    return "rtl" if ratio >= threshold else "ltr"


# ── repair / folding ─────────────────────────────────────────────────────


def strip_bidi_controls(text: str) -> str:
    """Remove explicit bidi formatting controls (LRM/RLM/isolates/embeds)."""
    return _BIDI_CONTROL_RE.sub("", text or "")


def to_logical(text: str) -> str:
    """Repair an extracted text layer into logical-order base letters.

    NFKC decomposes Arabic Presentation Forms-A/B — the joining forms and the
    lam-alef ligatures a visual-dump extractor emits — back to their base
    letters, so the text becomes searchable, chunkable and re-renderable.

    This does **not** repair *ordering*: an extractor that also reverses the
    character stream still needs OCR. Callers should re-measure
    :func:`~kazma_core.documents.quality.presentation_form_ratio` afterwards
    and only escalate to OCR for pages that are still bad.
    """
    if not text:
        return text
    if not PRESENTATION_FORMS_RE.search(text) and not _BIDI_CONTROL_RE.search(text):
        return text
    return strip_bidi_controls(unicodedata.normalize("NFKC", text))


def fold_for_search(text: str) -> str:
    """The search normal form. Apply at index time AND at query time.

    NFKC → drop harakat/tatweel/bidi controls → fold alef-hamza, taa-marbuta,
    alef-maqsura, hamza carriers and the Farsi/Urdu letter variants → map
    Arabic-Indic and Eastern Arabic-Indic digits to ASCII → collapse
    whitespace → casefold (a no-op for Arabic, correct for the Latin that
    shares the field).
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", text)
    folded = _HARAKAT_RE.sub("", folded)
    folded = folded.translate(_SEARCH_TRANSLATE)
    return _WS_RE.sub(" ", folded).strip().casefold()


def to_arabic_numerals(text: str) -> str:
    """Render ASCII digits as Arabic-Indic (٠-٩) for Arabic document chrome."""
    return (text or "").translate(_ASCII_TO_ARABIC)


# ── paragraph-level shaping for visual (ReportLab) engines ───────────────


@dataclass(frozen=True, slots=True)
class ShapedSegment:
    """One visually-ordered, already-reshaped run of text plus its style."""

    text: str
    style: Any
    level: int

    @property
    def rtl(self) -> bool:
        return self.level % 2 == 1


def _resolve_levels(text: str, base_dir: str) -> list[int]:
    """Per-character bidi embedding levels for *text* under *base_dir*.

    Runs the reference pipeline from ``python-bidi`` up to (but not
    including) the reordering step, so we keep a 1:1 character↔level map that
    style spans can be split against. Falls back to a flat base level when the
    library is unavailable or its internals move.
    """
    base_level = 1 if base_dir == "R" else 0
    try:
        from bidi import algorithm as _bidi

        storage = _bidi.get_empty_storage()
        storage["base_level"] = base_level
        storage["base_dir"] = base_dir
        _bidi.get_embedding_levels(text, storage, False, False)
        _bidi.explicit_embed_and_overrides(storage, False)
        _bidi.resolve_weak_types(storage, False)
        _bidi.resolve_neutral_types(storage, False)
        _bidi.resolve_implicit_levels(storage, False)
        chars = storage["chars"]
        if len(chars) == len(text):
            return [int(c["level"]) for c in chars]
        logger.debug("[arabic] bidi pipeline changed length; using flat levels")
    except Exception:  # pragma: no cover - defensive, exercised by the fallback test
        logger.debug("[arabic] bidi level resolution unavailable", exc_info=True)
    return [base_level] * len(text)


def _reorder_l2(segments: list[ShapedSegment]) -> list[ShapedSegment]:
    """UBA rule L2 over level-uniform segments.

    "From the highest level down to the lowest odd level, reverse any
    contiguous sequence of characters at that level or higher." Applied to
    segments rather than characters, which is equivalent when every segment
    holds a single level.
    """
    if not segments:
        return segments
    levels = [s.level for s in segments]
    highest = max(levels)
    lowest_odd = min((lv for lv in levels if lv % 2), default=highest + 1)
    ordered = list(segments)
    for level in range(highest, lowest_odd - 1, -1):
        start: int | None = None
        for index in range(len(ordered) + 1):
            at_or_above = index < len(ordered) and ordered[index].level >= level
            if at_or_above and start is None:
                start = index
            elif not at_or_above and start is not None:
                ordered[start:index] = reversed(ordered[start:index])
                start = None
    return ordered


def _reshape(text: str) -> str:
    """Apply Arabic joining forms (no bidi reordering)."""
    if not text or not has_rtl(text):
        return text
    try:
        import arabic_reshaper
    except ImportError:
        return text
    try:
        reshaper = arabic_reshaper.ArabicReshaper(
            configuration={"delete_harakat": False, "support_ligatures": True}
        )
        return reshaper.reshape(text)
    except Exception:  # pragma: no cover - defensive
        logger.debug("[arabic] reshape failed", exc_info=True)
        return text


def shape_spans(
    spans: Sequence[tuple[str, Any]],
    *,
    base_dir: Direction | None = None,
) -> list[ShapedSegment]:
    """Shape a styled paragraph as ONE bidi paragraph.

    *spans* is the paragraph in **logical** order as ``(text, style)`` pairs,
    where ``style`` is any opaque marker the caller uses to re-apply markup
    (``"b"``, ``"i"``, a tuple, ``None`` for plain). The returned segments are
    in **visual** order, already reshaped, each still carrying the style of the
    span it came from — so a caller can wrap them in ``<b>``/``<i>``/``<font>``
    and concatenate them left to right for a visual drawing engine.

    Segments are split wherever the embedding level changes, which is exactly
    where a Latin word, a number or a bracket sits inside Arabic text.
    """
    pairs = [(str(t or ""), s) for t, s in spans if str(t or "")]
    if not pairs:
        return []
    logical = "".join(t for t, _ in pairs)
    if not has_rtl(logical):
        # No RTL anywhere: nothing to reorder and nothing to reshape.
        return [ShapedSegment(text=t, style=s, level=0) for t, s in pairs]

    direction = base_dir or direction_of(logical)
    base = "R" if direction == "rtl" else "L"
    levels = _resolve_levels(logical, base)

    # Split the styled spans at every embedding-level change.
    segments: list[ShapedSegment] = []
    cursor = 0
    for text, style in pairs:
        start = 0
        for offset in range(1, len(text) + 1):
            boundary = offset == len(text) or levels[cursor + offset] != levels[cursor + start]
            if boundary:
                chunk = text[start:offset]
                if chunk:
                    segments.append(
                        ShapedSegment(chunk, style, int(levels[cursor + start]))
                    )
                start = offset
        cursor += len(text)

    ordered = _reorder_l2(segments)
    out: list[ShapedSegment] = []
    for seg in ordered:
        shaped = _reshape(seg.text)
        # Within a level-uniform run the bidi algorithm's reordering reduces to
        # a reversal for odd (RTL) levels.
        visual = shaped[::-1] if seg.rtl else shaped
        out.append(ShapedSegment(visual, seg.style, seg.level))
    return out


def shape_text(text: str, *, base_dir: Direction | None = None) -> str:
    """Shape an unstyled paragraph for a visual engine (convenience wrapper)."""
    return "".join(seg.text for seg in shape_spans([(text, None)], base_dir=base_dir))


def iter_style_runs(segments: Iterable[ShapedSegment]) -> list[tuple[str, Any]]:
    """Merge adjacent visual segments that share a style, preserving order."""
    merged: list[list[Any]] = []
    for seg in segments:
        if merged and merged[-1][1] == seg.style:
            merged[-1][0] += seg.text
        else:
            merged.append([seg.text, seg.style])
    return [(text, style) for text, style in merged]
