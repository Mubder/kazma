"""Arabic Tokenizer — Custom Arabic tokenizer for Tantivy indexing.

Handles Arabic-specific text normalization including Alef unification,
diacritics removal, stop words filtering, and stemming.
"""

from __future__ import annotations

import re


class ArabicTantivyTokenizer:
    """Custom Arabic tokenizer for Tantivy.

    Provides comprehensive Arabic text processing including:
    - Unicode normalization (Alef, Teh Marbuta)
    - Diacritics (tashkeel) removal
    - Stop words filtering
    - Basic stemming
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
            "قد",
            "كان",
            "يكون",
            "تكون",
            "كانت",
            "كانوا",
            # Pronouns
            "أنا",
            "أنت",
            "أنتِ",
            "هو",
            "هي",
            "نحن",
            "أنتم",
            "هم",
            "هن",
            "إياي",
            "إياك",
            "إياكِ",
            "إياه",
            "إياها",
            "إيانا",
            "إياكم",
            "إياهم",
            # Demonstratives
            "ذلك",
            "تلك",
            "هؤلاء",
            "هذا",
            "هذه",
            "هذان",
            "هاتان",
            # Prepositions
            "حتى",
            "منذ",
            "خلال",
            "دون",
            "بعد",
            "قبل",
            "تحت",
            "فوق",
            "أمام",
            "خلف",
            "بين",
            "نحو",
            "عبر",
            "حول",
            "ضد",
            # Conjunctions
            "و",
            "ف",
            "ثم",
            "أو",
            "أم",
            "بل",
            "لكن",
            "غير",
            "إلا",
            "سوى",
            "لولا",
            "لوما",
            "عل",
            "كي",
            "لما",
            # Relative pronouns
            "الذي",
            "التي",
            "الذين",
            "اللذين",
            "اللتين",
            "اللواتي",
            # Interrogatives
            "من",
            "ما",
            "متى",
            "أين",
            "كيف",
            "لماذا",
            "كم",
            "أي",
            # Common verbs (auxiliary)
            "ليس",
            "ليست",
            "ليسوا",
            "ليسن",
            "ما",
            "لا",
            "لم",
            "لن",
            # Numbers (as words)
            "واحد",
            "اثنان",
            "ثلاثة",
            "أربعة",
            "خمسة",
            # Other common words
            "بل",
            "بلى",
            "إذا",
            "إذ",
            "حين",
            "وقت",
            "مرة",
            "بعض",
            "كل",
            "جميع",
            "أجمع",
            "كافة",
            "عموم",
        }

    def _init_stemmer(self):
        """Initialize Arabic stemmer.

        Returns:
            Stemmer instance or None if not available.
        """
        # Basic Arabic stemmer using suffix/prefix removal
        # Can be enhanced with libraries like arabic-stemmer
        return None

    def tokenize(self, text: str) -> list[str]:
        """Tokenize Arabic text for Tantivy indexing.

        Processing steps:
        1. Normalize Unicode (Alef unification)
        2. Remove diacritics
        3. Remove stop words
        4. Apply stemming (optional)
        5. Return tokens

        Args:
            text: Input Arabic text.

        Returns:
            List of processed tokens.
        """
        if not text:
            return []

        # Step 1: Normalize text
        normalized = self.normalize(text)

        # Step 2: Remove diacritics
        cleaned = self.remove_diacritics(normalized)

        # Step 3: Tokenize into words
        words = self._split_words(cleaned)

        # Step 4: Remove stop words and short words
        filtered = [w for w in words if w not in self.stop_words and len(w) > 1]

        # Step 5: Apply stemming
        if self.stemmer:
            filtered = [self.stemmer.stem(w) for w in filtered]

        return filtered

    def normalize(self, text: str) -> str:
        """Normalize Arabic text (Alef, Teh Marbuta, etc.).

        Handles:
        - Alef variants (أ, إ, آ) → ا
        - Teh Marbuta (ة) → ه
        - Yeh (ى) → ي
        - Alef Maqsura (ى) → ي

        Args:
            text: Input Arabic text.

        Returns:
            Normalized text.
        """
        # Normalize Alef variants
        text = re.sub(r"[أإآ]", "ا", text)

        # Normalize Teh Marbuta
        text = re.sub(r"ة", "ه", text)

        # Normalize Yeh/Alef Maqsura
        text = re.sub(r"ى", "ي", text)

        # Normalize Hamza variants
        text = re.sub(r"[ؤئ]", "ء", text)

        # Remove tatweel (kashida)
        text = re.sub(r"ـ", "", text)

        return text

    def remove_diacritics(self, text: str) -> str:
        """Remove Arabic diacritics (tashkeel).

        Removes:
        - Fatha (َ)
        - Damma (ُ)
        - Kasra (ِ)
        - Sukun (ْ)
        - Shadda (ّ)
        - Tanwin (ً، ٌ، ٍ)

        Args:
            text: Input Arabic text.

        Returns:
            Text without diacritics.
        """
        # Arabic diacritics Unicode range: \u064B-\u065F
        text = re.sub(r"[\u064B-\u065F]", "", text)

        # Also remove other vocalization marks
        text = re.sub(r"[\u0670]", "", text)  # Superscript Alef

        return text

    def _split_words(self, text: str) -> list[str]:
        """Split text into words.

        Args:
            text: Input text.

        Returns:
            List of words.
        """
        # Split on whitespace and punctuation
        words = re.findall(r"[\w\u0600-\u06FF]+", text)
        return words

    def stem(self, word: str) -> str:
        """Apply basic Arabic stemming.

        This is a simple suffix/prefix removal stemmer.
        For production use, consider a dedicated Arabic stemmer.

        Args:
            word: Input Arabic word.

        Returns:
            Stemmed word.
        """
        if not word or len(word) <= 3:
            return word

        # Common prefixes
        prefixes = ["ال", "و", "ب", "ل", "ك", "سي", "ي", "ن", "ت"]

        # Common suffixes
        suffixes = ["ون", "ين", "ات", "ية", "ية", "نا", "كم", "هم", "ها", "ه", "ي", "ك", "تم"]

        stemmed = word

        # Remove prefixes (but keep at least 3 chars)
        for prefix in prefixes:
            if stemmed.startswith(prefix) and len(stemmed) > len(prefix) + 2:
                stemmed = stemmed[len(prefix) :]
                break

        # Remove suffixes (but keep at least 3 chars)
        for suffix in suffixes:
            if stemmed.endswith(suffix) and len(stemmed) > len(suffix) + 2:
                stemmed = stemmed[: -len(suffix)]
                break

        return stemmed
