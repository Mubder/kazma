"""Arabic Tokenizer — Enhanced Arabic text processing for search indexing.

Handles Arabic-specific text normalization including Alef unification,
diacritics removal, stop words filtering, and stemming for enhanced search.
Works with both Tantivy and direct string processing.
"""

from __future__ import annotations

import re


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
        """Load Arabic stop words.

        Returns:
            Set of common Arabic stop words.
        """
        return {
            # Particles
            "في",
            "من",
            "على",
            "إلى",
            "عن",
            "مع",
            "هذا",
            "هذه",
            "التي",
            "الذي",
            "أن",
            "كان",
            "هو",
            "هي",
            "لا",
            "ما",
            "لم",
            "لن",
            "لكن",
            "كما",
            "كلما",
            # Pronouns (with and without hamza for normalization compatibility)
            "أنا",
            "انت",
            "انتما",
            "هم",
            "هن",
            "نحن",
            "انتم",
            "هو",
            "هي",
            "هيا",
            "انا",
            "انت",
            "انتما",
            "هم",
            "هن",
            "نحن",
            "انتم",
            "هو",
            "هي",
            "هيا",
            # Common connectors
            "و",
            "أو",
            "ثم",
            "حتى",
            "عندما",
            "حين",
            "هناك",
            # Kuwaiti dialect terms
            "يلا",
            "يا",
            "شلون",
            "عشان",
            "مو",
            "ليه",
            "لازم",
            "شخ",
            "ماكو",
            "فد",
        }

    def _init_stemmer(self):
        """Initialize basic Arabic stemmer.

        Returns:
            Simple stemmer function or None.
        """
        # Basic stemming rules - can be enhanced
        stem_rules = {
            # Common suffixes
            r"ات$": "",  # feminine plural
            r"ون$": "",  # masculine plural
            "ين$": "",  # dual/masculine plural
            r"ة$": "",  # feminine marker
            r"ان$": "",  # dual
            r"نا$": "",  # first person plural
            # Common prefixes
            r"^ال": "",  # definite article
            r"^بـ": "",  # prefixing B
            r"^كـ": "",  # prefixing K
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
        # Remove diacritics (tashkeel)
        text = self._remove_diacritics(text)

        # Normalize Alef variants
        text = self._normalize_alef(text)

        # normalize Teh Marbuta to Heh
        text = text.replace("ة", "ه")

        # Normalize Yeh variants
        text = self._normalize_yeh(text)

        # Normalize Waw Hamza
        text = text.replace("ؤ", "و")

        # Normalize Ya Hamza
        text = text.replace("ئ", "ي")

        # Remove Kashida (tatweel)
        text = text.replace("ـ", "")

        # Remove extra spaces
        text = re.sub(r"\s+", " ", text).strip()

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

        Args:
            text: Arabic text with Yeh variants.

        Returns:
            Text with normalized Yeh.
        """
        yeh_variants = ["ئ", "ؤ", "إي", "ى"]
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
