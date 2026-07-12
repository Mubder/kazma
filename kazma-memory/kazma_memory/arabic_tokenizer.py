"""Arabic Tokenizer — Enhanced Arabic text processing for search indexing.

Handles Arabic-specific text normalization including Alef unification,
diacritics removal, stop words filtering, and stemming for enhanced search.
Works with both Tantivy and direct string processing.
"""

from __future__ import annotations

import re

# Arabic-Indic (U+0660-0669) and Extended Arabic-Indic/Persian (U+06F0-06F9)
# digits map to ASCII so numbers are searchable/matchable regardless of which
# digit script the user typed them in.
_ARABIC_DIGIT_TRANSLATION = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹",
    "0123456789" "0123456789",
)


class ArabicTokenizer:
    """Enhanced Arabic tokenizer for general Arabic text processing.

    Provides comprehensive Arabic text processing including:
    - Unicode normalization (Alef, Teh Marbuta, Yeh variants)
    - Diacritics (tashkeel) removal
    - Stop words filtering
    - Basic stemming
    - Kuwaiti dialect handling
    """

    def __init__(self):
        """Initialize tokenizer with Arabic linguistic resources."""
        self.stop_words = self._load_stop_words()
        self.stemmer = self._init_stemmer()

    def _load_stop_words(self) -> set[str]:
        """Load Arabic stop words (deduplicated).

        Returns:
            Set of common Arabic stop words.
        """
        return {
            # Particles
            "في", "من", "على", "الى", "عن", "مع",
            "هذا", "هذه", "التي", "الذي", "ان", "كان",
            "لا", "ما", "لم", "لن", "لكن", "كما", "كلما",
            # Pronouns
            "انا", "انت", "انتما", "انتم", "هم", "هن",
            "نحن", "هو", "هي", "هيا",
            # Common connectors
            "او", "ثم", "حتى", "عندما", "حين", "هناك",
            # Kuwaiti dialect terms
            "يلا", "يا", "شلون", "عشان", "مو", "ليه",
            "لازم", "شخ", "ماكو", "فد",
        }

    def _init_stemmer(self):
        """Initialize basic Arabic stemmer.

        Returns:
            Simple stemmer function or None.
        """
        # Suffix stripping rules (raw strings, applied after normalization).
        # Note: the feminine marker ة$ is NOT here because normalize() already
        # converts Teh Marbuta (ة) to Heh (ه). Stripping ه$ would wrongly
        # truncate words whose final letter happens to be ه.
        stem_rules = {
            r"ات$": "",    # feminine plural
            r"ون$": "",    # masculine plural
            r"ين$": "",    # dual / masculine plural (genitive)
            r"ان$": "",    # dual (nominative)
            r"نا$": "",    # first person plural
            r"^ال": "",    # definite article
        }

        def stem(word: str) -> str:
            for pattern, replacement in stem_rules.items():
                word = re.sub(pattern, replacement, word)
            return word

        return stem

    def normalize(self, text: str) -> str:
        """Normalize Arabic text for better search.

        Args:
            text: Arabic text to normalize.

        Returns:
            Normalized Arabic text.
        """
        # Normalize Arabic-Indic digits to ASCII (٠-٩ / ۰-۹ → 0-9)
        text = text.translate(_ARABIC_DIGIT_TRANSLATION)

        # Remove diacritics (tashkeel)
        text = self._remove_diacritics(text)

        # Normalize Alef variants (أ إ آ → ا)
        text = self._normalize_alef(text)

        # Normalize Teh Marbuta to Heh (ة → ه)
        text = text.replace("ة", "ه")

        # Normalize Yeh variants — must run BEFORE Waw Hamza / Ya Hamza
        # to avoid the conflicting rules that previously dead-coded each other.
        # _normalize_yeh converts: ئ→ي, ى→ي (and removed the old ؤ→ي conflict).
        text = self._normalize_yeh(text)

        # Normalize Waw Hamza (ؤ → و) — no longer conflicts with _normalize_yeh
        text = text.replace("ؤ", "و")

        # Ya Hamza (ئ) is already handled by _normalize_yeh above; the old
        # redundant text.replace("ئ", "ي") is removed.

        # Remove Kashida (tatweel)
        text = text.replace("ـ", "")

        # Remove extra spaces
        text = re.sub(r"\s+", " ", text).strip()

        return text

    def _split_arabic_clitics(self, text: str) -> str:
        """Split attached Arabic clitic prefixes for better token matching.

        Arabic commonly attaches the conjunction ``و`` (and) to the
        following word (e.g. ``وسلام`` = wa-salām = "and peace"). This
        function separates ONLY the waw-conjunction case, and ONLY when the
        resulting stem is 4+ characters — this avoids corrupting legitimate
        Arabic words that start with ``و`` (like ``واصل``, ``وجه``, ``وفق``)
        or ``ل`` (like ``لابد``) or ``ب`` (like ``بريد``).

        Note: ``ل`` and ``ب`` prefixes are NOT split at all because the
        false-positive rate is too high (many common words start with these
        letters). Only ``و`` is split, and conservatively.

        Args:
            text: Normalized Arabic text.

        Returns:
            Text with waw-conjunction prefixes separated by spaces.
        """
        # و (wāw al-ʿaṭf — "and") at the start of a 5+ char word.
        # Minimum 5 chars (و + 4 stem chars) avoids splitting real words
        # like واصل(3), ورد(2), وفق(2), وجه(2) while still splitting
        # conjunctions like وسلام(5), والكتاب(6), فالمشروع(7).
        text = re.sub(r"(?<![^\s])و([\u0600-\u06FF]{4,})(?![^\s])", r"و \1", text)
        return text

    def tokenize(self, text: str) -> str:
        """Tokenize Arabic text for search indexing.

        Args:
            text: Arabic text to tokenize.

        Returns:
            Processed Arabic text suitable for search indexing.
        """
        if not text:
            return text

        # Normalize text
        processed = self.normalize(text)

        # Split attached clitics (و, ل, ب prefixes) for better recall
        processed = self._split_arabic_clitics(processed)

        # Remove stop words
        words = processed.split()
        filtered = [word for word in words if word not in self.stop_words]

        # Apply stemming if available
        if self.stemmer:
            filtered = [self.stemmer(word) for word in filtered]

        # Reconstruct processed text
        return " ".join(filtered)

    def _remove_diacritics(self, text: str) -> str:
        """Remove Arabic diacritics (harakat).

        Args:
            text: Arabic text with diacritics.

        Returns:
            Text without diacritics.
        """
        # Arabic diacritics range: U+064B to U+065F
        diacritics = re.compile(r"[\u064B-\u065F\u0670]")
        return diacritics.sub("", text)

    def _normalize_alef(self, text: str) -> str:
        """Normalize Alef variants to ا.

        Args:
            text: Arabic text with Alef variants.

        Returns:
            Text with normalized Alef.
        """
        alef_variants = ["أ", "إ", "آ"]
        for variant in alef_variants:
            text = text.replace(variant, "ا")
        return text

    def _normalize_yeh(self, text: str) -> str:
        """Normalize Yeh variants to ي.

        Note: ``ؤ`` (Waw Hamza) is NOT included here — it is normalized to
        ``و`` in ``normalize()``. The old code included ``ؤ`` in the Yeh
        variants, which converted it to ``ي`` and dead-coded the ``ؤ→و``
        rule. The dead ``إي`` entry is also removed (Alef is already
        normalized to ``ا`` by ``_normalize_alef`` before this runs, so
        ``إي`` can never match).

        Args:
            text: Arabic text with Yeh variants.

        Returns:
            Text with normalized Yeh.
        """
        # ئ (Ya Hamza) → ي, ى (Alef Maqsura) → ي
        yeh_variants = ["ئ", "ى"]
        for variant in yeh_variants:
            text = text.replace(variant, "ي")
        return text


# Maintain backward compatibility with existing code
class ArabicTantivyTokenizer(ArabicTokenizer):
    """Backward-compatible wrapper for existing Tantivy code.

    This class now provides the same functionality as ArabicTokenizer
    but maintains the original class name for compatibility.
    """

    def tokenize(self, text: str) -> list[str]:
        """Tokenize Arabic text for search indexing (returns list for backward compat).

        Args:
            text: Arabic text to tokenize.

        Returns:
            List of processed tokens.
        """
        if not text:
            return []

        # Normalize text
        processed = self.normalize(text)

        # Remove stop words
        words = processed.split()
        filtered = [word for word in words if word not in self.stop_words and len(word) > 1]

        # Apply stemming if available
        if self.stemmer:
            filtered = [self.stemmer(word) for word in filtered]

        return filtered

    def remove_diacritics(self, text: str) -> str:
        """Remove Arabic diacritics (harakat) - public backward compat method.

        Args:
            text: Arabic text with diacritics.

        Returns:
            Text without diacritics.
        """
        return self._remove_diacritics(text)

    def stem(self, word: str) -> str:
        """Apply stemming to a single word.

        Args:
            word: Arabic word to stem.

        Returns:
            Stemmed word.
        """
        if not word or len(word) <= 1:
            return word
        if self.stemmer:
            return self.stemmer(word)
        return word
