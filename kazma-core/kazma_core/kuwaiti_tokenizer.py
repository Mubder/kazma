"""Kuwaiti dialect tokenizer.

Handles Gulf Arabic tokenization with special attention to:
- Dialect token preservation (no translation to MSA)
- Code-switching (Arabic-English mixing)
- Proper noun preservation (names, companies)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class TokenType(Enum):
    """Token classification."""

    DIALECT = "dialect"
    WORD = "word"
    NUMBER = "number"
    PUNCTUATION = "punctuation"
    CODE_SWITCH = "code_switch"  # English in Arabic context
    PROPER_NOUN = "proper_noun"
    EMOJI = "emoji"
    WHITESPACE = "whitespace"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Token:
    """A single token from tokenization."""

    text: str
    start: int
    end: int
    token_type: TokenType
    dialect_meaning: str | None = None  # MSA meaning for dialect tokens
    language: str = "ar"  # "ar" or "en" for code-switched tokens


# ── Kuwaiti dialect markers ───────────────────────────────────────────
# Maps Kuwaiti word → MSA meaning (for metadata, NOT for replacement)

DIALECT_MARKERS: dict[str, str] = {
    # Core conversational
    "شلونك": "كيف حالك",
    "شلون": "كيف",
    "وين": "أين",
    "ليش": "لماذا",
    "هلا": "الآن",
    "تمام": "جيد",
    "شنو": "ماذا",
    "اخوي": "أخي",
    "ياخوي": "يا أخي",
    "هجم": "تعال",
    "يالله": "هيا",
    # Informal address
    "اخو": "أخ",
    "اخوات": "إخوة",
    "اخوكم": "أخوك",
    "اخويا": "أخي",
    "اختك": "أختك",
    # Descriptions / adjectives
    "خوش": "جيد",
    "زينة": "جميلة",
    "حلو": "جميل",
    "حلوه": "جميلة",
    "كبير": "كبير",
    "صغير": "صغير",
    "قديم": "قديم",
    "جديد": "جديد",
    # Verbs / actions
    " gal ": "قال",
    " agool ": "أقول",
    " aruh ": "أذهب",
    " areed ": "أريد",
    " arid ": "أريد",
    " yishtgil ": "يشتغل",
    " ayesh ": "عايش",
    # Prepositions / particles
    " مكو ": "ليس هناك",
    " اكو ": "يوجد",
    " ماف ": "لا يوجد",
    " وايد ": "كثير",
    " واجد ": "كثير",
    " بس ": "فقط",
    " عسب ": "حتى",
    " عشان ": "من أجل",
    " زاي ": "مثل",
    " هيج ": "هكذا",
    # Common Gulf expressions
    " بالعافية ": "بالصحة",
    " يعطيك العافية ": "الله يعطيك العافية",
    " تسلم ": "الله يسلمك",
    " الله يسلمك ": "وأنت بخير",
    " ما شاء الله ": "ما شاء الله",
    " ان شاء الله ": "إن شاء الله",
    " الحمد لله ": "الحمد لله",
    " سبحان الله ": "سبحان الله",
    # Numbers / time
    " buckra ": "غداً",
    " ibaarak ": "مبروك",
}


# Emoji pattern
_EMOJI_RE = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map
    "\U0001f1e0-\U0001f1ff"  # flags
    "\U00002702-\U000027b0"
    "\U000024c2-\U0001f251"
    "\U0001f900-\U0001f9ff"  # supplemental
    "\U0001fa00-\U0001fa6f"  # chess symbols
    "\U0001fa70-\U0001faff"  # symbols extended
    "]+",
    flags=re.UNICODE,
)


def _is_arabic(text: str) -> bool:
    """Check if text is predominantly Arabic."""
    if not text:
        return False
    arabic_chars = sum(1 for c in text if "\u0600" <= c <= "\u06ff" or "\u0750" <= c <= "\u077f")
    return arabic_chars > len(text) * 0.3


def _is_english_word(text: str) -> bool:
    """Check if a word is English (ASCII letters only)."""
    return bool(re.match(r"^[a-zA-Z]+$", text))


class KuwaitiTokenizer:
    """Handles Kuwaiti dialect tokenization.

    Key behaviors:
    1. Preserves dialect tokens with their MSA meaning as metadata
    2. Handles code-switching (Arabic-English mixing)
    3. Preserves proper nouns
    """

    def __init__(self) -> None:
        # Build a lookup for dialect marker detection
        self._dialect_set = set(DIALECT_MARKERS.keys())

    def tokenize(self, text: str) -> list[Token]:
        """Tokenize Kuwaiti Arabic text.

        Returns a list of Token objects with dialect metadata preserved.
        """
        if not text:
            return []

        tokens: list[Token] = []
        pos = 0

        # First pass: find dialect markers (greedy longest match)
        dialect_positions: set[int] = set()

        # Sort markers by length (longest first) for greedy matching
        sorted_markers = sorted(self._dialect_set, key=len, reverse=True)
        for marker in sorted_markers:
            start = 0
            while True:
                idx = text.find(marker, start)
                if idx == -1:
                    break
                for i in range(idx, idx + len(marker)):
                    dialect_positions.add(i)
                start = idx + 1

        # Second pass: tokenize
        while pos < len(text):
            # Skip already-consumed dialect positions
            if pos in dialect_positions:
                # Check if this position starts a dialect marker
                found_marker = False
                for marker in sorted_markers:
                    if text.startswith(marker, pos):
                        tokens.append(
                            Token(
                                text=marker,
                                start=pos,
                                end=pos + len(marker),
                                token_type=TokenType.DIALECT,
                                dialect_meaning=DIALECT_MARKERS.get(marker),
                            )
                        )
                        pos += len(marker)
                        found_marker = True
                        break
                if found_marker:
                    continue
                # Edge case: skip position
                pos += 1
                continue

            char = text[pos]

            # Emoji
            emoji_match = _EMOJI_RE.match(text, pos)
            if emoji_match:
                tokens.append(
                    Token(
                        text=emoji_match.group(),
                        start=pos,
                        end=emoji_match.end(),
                        token_type=TokenType.EMOJI,
                    )
                )
                pos = emoji_match.end()
                continue

            # Whitespace
            if char in (" ", "\t", "\n", "\r"):
                end = pos
                while end < len(text) and text[end] in (" ", "\t", "\n", "\r"):
                    end += 1
                tokens.append(
                    Token(
                        text=text[pos:end],
                        start=pos,
                        end=end,
                        token_type=TokenType.WHITESPACE,
                    )
                )
                pos = end
                continue

            # Punctuation
            if char in "،.؟!؛:،،«»\"'()[]{}-/\\":
                tokens.append(
                    Token(
                        text=char,
                        start=pos,
                        end=pos + 1,
                        token_type=TokenType.PUNCTUATION,
                    )
                )
                pos += 1
                continue

            # Number
            if char.isdigit():
                end = pos
                while end < len(text) and (text[end].isdigit() or text[end] in ".,"):
                    end += 1
                tokens.append(
                    Token(
                        text=text[pos:end],
                        start=pos,
                        end=end,
                        token_type=TokenType.NUMBER,
                    )
                )
                pos = end
                continue

            # Word (Arabic or English / code-switch)
            if _is_arabic(char) or _is_english_word(char):
                end = pos
                # Consecutive same-script characters
                if _is_english_word(char):
                    while end < len(text) and _is_english_word(text[end]):
                        end += 1
                    token_text = text[pos:end]
                    token_type = TokenType.CODE_SWITCH if _is_english_word(token_text) else TokenType.WORD
                    tokens.append(
                        Token(
                            text=token_text,
                            start=pos,
                            end=end,
                            token_type=token_type,
                            language="en" if token_type == TokenType.CODE_SWITCH else "ar",
                        )
                    )
                else:
                    # Arabic word: consume Arabic chars + tatweel + diacritics
                    while end < len(text) and (
                        "\u0600" <= text[end] <= "\u06ff"
                        or "\u0750" <= text[end] <= "\u077f"
                        or text[end] == "\u0640"  # tatweel
                        or "\u0610" <= text[end] <= "\u061a"  # diacritics
                        or "\u064b" <= text[end] <= "\u065f"  # tashkeel
                        or text[end] in "\u0621\u0622\u0623\u0625\u0627"  # alef variants
                    ):
                        end += 1
                    token_text = text[pos:end]
                    tokens.append(
                        Token(
                            text=token_text,
                            start=pos,
                            end=end,
                            token_type=TokenType.WORD,
                        )
                    )
                pos = end
                continue

            # Unknown character
            tokens.append(
                Token(
                    text=char,
                    start=pos,
                    end=pos + 1,
                    token_type=TokenType.UNKNOWN,
                )
            )
            pos += 1

        return tokens
