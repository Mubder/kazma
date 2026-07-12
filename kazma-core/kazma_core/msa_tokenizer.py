"""Modern Standard Arabic (MSA) tokenizer.

Handles formal Arabic with:
1. Character-class tokenization (Arabic word / punctuation / number / whitespace)
2. Diacritics handling (Tashkeel)
3. Alef unification (أ, إ, آ → ا)
"""

from __future__ import annotations

from kazma_core.kuwaiti_tokenizer import Token, TokenType

# ── Alef normalization map ────────────────────────────────────────────
_ALEF_VARIANTS = {
    "\u0622": "\u0627",  # آ → ا
    "\u0623": "\u0627",  # أ → ا
    "\u0625": "\u0627",  # إ → ا
    "\u0671": "\u0627",  # ٱ → ا  (alef wasla)
}

# ── Diacritics (Tashkeel) ────────────────────────────────────────────
# Arabic diacritics / short vowels
_DIACRITICS: set[str] = {
    "\u064b",  # Fathatan  ً
    "\u064c",  # Dammatan  ٌ
    "\u064d",  # Kasratan  ٍ
    "\u064e",  # Fatha     َ
    "\u064f",  # Damma     ُ
    "\u0650",  # Kasra     ِ
    "\u0651",  # Shadda    ّ
    "\u0652",  # Sukun     ْ
    "\u0653",  # Maddah    ٓ
    "\u0654",  # Hamza     ٔ
    "\u0655",  # Subscript Hamza  ٕ
    "\u0610",  # Dots above  ֑
    "\u0611",  # Small High Sad  ֒
    "\u0612",  # Small High Ain  ֓
    "\u0613",  # Dots above  ֔
    "\u0614",  # Small High   ֕
    "\u0615",  # Small Low   ֖
    "\u0616",  # Small High   ֗
    "\u0617",  # Small Low   ֘
    "\u0618",  # Small High   ֙
    "\u0619",  # Small Low   ֚
    "\u061a",  # Small High   ֛
}

# Common MSA stop words (for potential future use in morphological analysis)
_MSA_STOP_WORDS: set[str] = {
    "في",
    "من",
    "إلى",
    "على",
    "عن",
    "مع",
    "بين",
    "حتى",
    "أن",
    "إن",
    "لا",
    "ما",
    "هل",
    "قد",
    "لم",
    "لن",
    "هذا",
    "هذه",
    "ذلك",
    "تلك",
    "الذي",
    "التي",
    "كان",
    "يكون",
    "قد",
    "ليس",
    "لم",
    "لن",
}


def _strip_diacritics(text: str) -> str:
    """Remove Arabic diacritics from text."""
    return "".join(c for c in text if c not in _DIACRITICS)


def _unify_alef(text: str) -> str:
    """Normalize Alef variants to bare Alef (ا)."""
    result = []
    for char in text:
        result.append(_ALEF_VARIANTS.get(char, char))
    return "".join(result)


def _normalize(text: str) -> str:
    """Apply the MSA normalization pipeline (diacritics + Alef unification).

    Note: taa marbuta (ة → ه) and alef maksura/yaa (ى → ي) normalization
    are NOT implemented here — they're common additions for fuzzy-matching
    use cases but would need their own toggle (like ``unify_alef``) since
    they're not always desirable (e.g. they lose grammatical gender info).
    """
    text = _strip_diacritics(text)
    text = _unify_alef(text)
    return text


class MSATokenizer:
    """Handles Modern Standard Arabic tokenization.

    Features:
    1. Character-class-based word/punctuation/number/whitespace splitting
    2. Diacritics handling (preserves or strips, per ``strip_diacritics``)
    3. Alef unification (أ, إ, آ → ا, per ``unify_alef``)

    Does not perform morphological analysis (stemming/root extraction) —
    that would require a dedicated Arabic morphological analyzer.
    """

    def __init__(self, strip_diacritics: bool = True, unify_alef: bool = True) -> None:
        """
        Args:
            strip_diacritics: If True, removes diacritics during normalization.
            unify_alef: If True, normalizes Alef variants to bare Alef.
        """
        self.strip_diacritics = strip_diacritics
        self.unify_alef = unify_alef

    def tokenize(self, text: str) -> list[Token]:
        """Tokenize MSA text.

        Returns a list of Token objects. Words are normalized according
        to the tokenizer settings.
        """
        if not text:
            return []

        tokens: list[Token] = []
        pos = 0

        while pos < len(text):
            char = text[pos]

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
            if char in "،.؟!؛:«»\"'()[]{}-/\\":
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

            # Arabic word
            if "\u0600" <= char <= "\u06ff" or "\u0750" <= char <= "\u077f":
                end = pos
                while end < len(text) and (
                    "\u0600" <= text[end] <= "\u06ff"
                    or "\u0750" <= text[end] <= "\u077f"
                    or text[end] == "\u0640"  # tatweel
                    or text[end] in _DIACRITICS
                ):
                    end += 1
                raw_text = text[pos:end]

                # Apply normalization
                normalized = raw_text
                if self.strip_diacritics:
                    normalized = _strip_diacritics(normalized)
                if self.unify_alef:
                    normalized = _unify_alef(normalized)

                tokens.append(
                    Token(
                        text=raw_text,
                        start=pos,
                        end=end,
                        token_type=TokenType.WORD,
                        dialect_meaning=normalized if normalized != raw_text else None,
                    )
                )
                pos = end
                continue

            # English word
            if char.isalpha() and ord(char) < 0x080:
                end = pos
                while end < len(text) and text[end].isalpha() and ord(text[end]) < 0x080:
                    end += 1
                tokens.append(
                    Token(
                        text=text[pos:end],
                        start=pos,
                        end=end,
                        token_type=TokenType.CODE_SWITCH,
                        language="en",
                    )
                )
                pos = end
                continue

            # Unknown
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

    def normalize(self, text: str) -> str:
        """Apply full MSA normalization to text."""
        return _normalize(text)
