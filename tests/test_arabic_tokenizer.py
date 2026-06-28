"""Tests for Arabic Tantivy Tokenizer.

Comprehensive tests for the ArabicTantivyTokenizer including
normalization, diacritics removal, stop words filtering, and stemming.
"""

import pytest
from kazma_memory.arabic_tokenizer import ArabicTantivyTokenizer


@pytest.fixture
def tokenizer():
    """Create an Arabic tokenizer instance."""
    return ArabicTantivyTokenizer()


class TestArabicTantivyTokenizer:
    """Test suite for ArabicTantivyTokenizer."""

    def test_init(self, tokenizer):
        """Test tokenizer initialization."""
        assert tokenizer is not None
        assert tokenizer.stop_words is not None
        assert len(tokenizer.stop_words) > 0

    def test_tokenize_empty_string(self, tokenizer):
        """Test tokenizing empty string."""
        result = tokenizer.tokenize("")
        assert result == []

    def test_tokenize_simple_arabic(self, tokenizer):
        """Test tokenizing simple Arabic text."""
        result = tokenizer.tokenize("مرحبا بالعالم")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_tokenize_english(self, tokenizer):
        """Test tokenizing English text."""
        result = tokenizer.tokenize("Hello world")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_tokenize_mixed_content(self, tokenizer):
        """Test tokenizing mixed Arabic and English."""
        result = tokenizer.tokenize("مرحبا world ترحيب")
        assert isinstance(result, list)
        assert len(result) >= 2

    def test_normalize_alef_variants(self, tokenizer):
        """Test Alef variant normalization."""
        # All should normalize to ا
        assert tokenizer.normalize("أحمد") == "احمد"
        assert tokenizer.normalize("إسماعيل") == "اسماعيل"
        assert tokenizer.normalize("آدم") == "ادم"

    def test_normalize_teh_marbuta(self, tokenizer):
        """Test Teh Marbuta normalization."""
        # Should normalize to ه
        assert tokenizer.normalize("كتبة") == "كتبه"

    def test_normalize_yeh(self, tokenizer):
        """Test Yeh normalization."""
        # Should normalize to ي
        assert tokenizer.normalize("عليى") == "عليي"

    def test_normalize_kashida(self, tokenizer):
        """Test Kashida removal."""
        text = "مرحـــبا"
        normalized = tokenizer.normalize(text)
        assert "ـ" not in normalized

    def test_remove_diacritics(self, tokenizer):
        """Test diacritics removal."""
        text = "مَرْحَبًا"  # With diacritics
        cleaned = tokenizer.remove_diacritics(text)

        # Should not contain diacritics
        assert "َ" not in cleaned
        assert "ْ" not in cleaned
        assert "ً" not in cleaned

    def test_remove_diacritics_preserves_base_text(self, tokenizer):
        """Test that diacritics removal preserves base text."""
        text = "كِتَابٌ"  # "book" with diacritics
        cleaned = tokenizer.remove_diacritics(text)
        assert cleaned == "كتاب"  # Base text preserved

    def test_tokenize_removes_stop_words(self, tokenizer):
        """Test that stop words are removed."""
        text = "الكتاب في المكتبة"  # "The book in the library"
        result = tokenizer.tokenize(text)

        # Stop words like "في" and "ال" should be removed
        # (ال is prefix, في is stop word)
        assert "في" not in result

    def test_tokenize_removes_short_words(self, tokenizer):
        """Test that very short words are removed."""
        text = "أنا هو هي نحن"  # Pronouns
        result = tokenizer.tokenize(text)

        # Single char words should be removed
        for word in result:
            assert len(word) > 1

    def test_tokenize_preserves_content_words(self, tokenizer):
        """Test that content words are preserved."""
        text = "المكتبة كبيرة جداً"  # "The library is very big"
        result = tokenizer.tokenize(text)

        # Content words should be present
        assert any("مكتب" in word for word in result)  # Root of "مكتبة"

    def test_normalize_multiple_characters(self, tokenizer):
        """Test normalizing multiple characters at once."""
        text = "أحمد إبراهيم آدم"
        normalized = tokenizer.normalize(text)

        # All Alef variants should be normalized
        assert "أ" not in normalized
        assert "إ" not in normalized
        assert "آ" not in normalized

    def test_tokenize_arabic_sentence(self, tokenizer):
        """Test tokenizing a complete Arabic sentence."""
        text = "السوق السعودي يشهد ارتفاعاً في أسعار النفط"
        result = tokenizer.tokenize(text)

        assert isinstance(result, list)
        assert len(result) > 0

        # All tokens should be strings
        for token in result:
            assert isinstance(token, str)
            assert len(token) > 0

    def test_tokenize_preserves_numbers(self, tokenizer):
        """Test that numbers are preserved."""
        text = "الصفحة 42 تحتوي على 100 معلومة"
        result = tokenizer.tokenize(text)

        # Should contain numbers
        assert any("42" in token or "100" in token for token in result)


class TestArabicTantivyTokenizerStemmer:
    """Test stemming functionality."""

    def test_stem_basic(self, tokenizer):
        """Test basic stemming."""
        # These are simple cases
        words = ["كتاب", "مكتب", "مكتبة"]
        stemmed = [tokenizer.stem(w) for w in words]

        # All should be stemmed versions
        assert all(isinstance(s, str) for s in stemmed)

    def test_stem_short_words(self, tokenizer):
        """Test that short words are not stemmed."""
        short_words = ["من", "على", "في"]
        stemmed = [tokenizer.stem(w) for w in short_words]

        # Short words should remain unchanged
        for original, stemmed_word in zip(short_words, stemmed):
            assert stemmed_word == original

    def test_stem_empty_string(self, tokenizer):
        """Test stemming empty string."""
        result = tokenizer.stem("")
        assert result == ""

    def test_stem_single_character(self, tokenizer):
        """Test stemming single character."""
        result = tokenizer.stem("ك")
        assert result == "ك"


class TestArabicTantivyTokenizerEdgeCases:
    """Test edge cases and error handling."""

    def test_tokenize_none_like_empty(self, tokenizer):
        """Test tokenizing None-like empty string."""
        result = tokenizer.tokenize("")
        assert result == []

    def test_tokenize_whitespace_only(self, tokenizer):
        """Test tokenizing whitespace only."""
        result = tokenizer.tokenize("   ")
        assert result == []

    def test_tokenize_punctuation_only(self, tokenizer):
        """Test tokenizing punctuation only."""
        result = tokenizer.tokenize("!@#$%^&*()")
        # Should return empty or very few tokens
        assert len(result) <= 2

    def test_normalize_empty_string(self, tokenizer):
        """Test normalizing empty string."""
        result = tokenizer.normalize("")
        assert result == ""

    def test_remove_diacritics_empty_string(self, tokenizer):
        """Test removing diacritics from empty string."""
        result = tokenizer.remove_diacritics("")
        assert result == ""

    def test_tokenize_very_long_text(self, tokenizer):
        """Test tokenizing very long text."""
        # Create long text
        text = " ".join(["كلمة"] * 10000)
        result = tokenizer.tokenize(text)

        assert isinstance(result, list)
        assert len(result) > 0

    def test_tokenize_unicode_edge_cases(self, tokenizer):
        """Test tokenizing text with Unicode edge cases."""
        text = "مرحبا\u200bبالعالم"  # With zero-width space
        result = tokenizer.tokenize(text)

        assert isinstance(result, list)


class TestArabicTantivyTokenizerPerformance:
    """Test tokenizer performance characteristics."""

    def test_tokenize_speed(self, tokenizer):
        """Test that tokenization is reasonably fast."""
        import time

        text = "السوق السعودي يشهد ارتفاعاً في أسعار النفط اليوم"

        start = time.time()
        for _ in range(1000):
            tokenizer.tokenize(text)
        duration = time.time() - start

        # Should process 1000 tokenizations in under 1 second
        assert duration < 1.0

    def test_normalize_speed(self, tokenizer):
        """Test that normalization is reasonably fast."""
        import time

        text = "أحمد إبراهيم آدم كتب رسالة"

        start = time.time()
        for _ in range(1000):
            tokenizer.normalize(text)
        duration = time.time() - start

        # Should process 1000 normalizations in under 1 second
        assert duration < 1.0

    def test_remove_diacritics_speed(self, tokenizer):
        """Test that diacritics removal is reasonably fast."""
        import time

        text = "مَرْحَبًا بِالعَالَمِ"

        start = time.time()
        for _ in range(1000):
            tokenizer.remove_diacritics(text)
        duration = time.time() - start

        # Should process 1000 removals in under 1 second
        assert duration < 1.0
