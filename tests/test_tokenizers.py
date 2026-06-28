"""Tests for KuwaitiTokenizer and MSATokenizer."""

from __future__ import annotations

from kazma_core.kuwaiti_tokenizer import KuwaitiTokenizer, TokenType
from kazma_core.msa_tokenizer import MSATokenizer, _strip_diacritics, _unify_alef


class TestKuwaitiTokenizer:
    """Test Kuwaiti dialect tokenizer."""

    def setup_method(self):
        self.tok = KuwaitiTokenizer()

    def test_empty_input(self):
        assert self.tok.tokenize("") == []

    def test_none_like_input(self):
        assert self.tok.tokenize("") == []

    def test_basic_tokenization(self):
        tokens = self.tok.tokenize("الحمد لله")
        # "الحمد" and "لله" are regular words
        words = [t for t in tokens if t.token_type == TokenType.WORD]
        assert len(words) >= 1

    def test_dialect_marker_preserved(self):
        tokens = self.tok.tokenize("شلونك")
        dialect_tokens = [t for t in tokens if t.token_type == TokenType.DIALECT]
        assert len(dialect_tokens) == 1
        assert dialect_tokens[0].text == "شلونك"
        assert dialect_tokens[0].dialect_meaning is not None

    def test_dialect_markers_not_translated(self):
        """Dialect tokens should keep their original text, not be replaced."""
        text = "وين ليش"
        tokens = self.tok.tokenize(text)
        all_text = " ".join(t.text for t in tokens if t.token_type != TokenType.WHITESPACE)
        # Original text should be preserved
        assert "وين" in all_text
        assert "ليش" in all_text

    def test_multiple_dialect_markers(self):
        tokens = self.tok.tokenize("شلونك وين ليش هلا تمام")
        dialect_tokens = [t for t in tokens if t.token_type == TokenType.DIALECT]
        assert len(dialect_tokens) >= 4  # at least 4 Kuwaiti markers

    def test_whitespace_preserved(self):
        tokens = self.tok.tokenize("وين ليش")
        ws = [t for t in tokens if t.token_type == TokenType.WHITESPACE]
        assert len(ws) >= 1

    def test_punctuation(self):
        tokens = self.tok.tokenize("هلا؟")
        punct = [t for t in tokens if t.token_type == TokenType.PUNCTUATION]
        assert len(punct) == 1
        assert punct[0].text == "؟"

    def test_numbers(self):
        tokens = self.tok.tokenize("عندي 5 كتب")
        nums = [t for t in tokens if t.token_type == TokenType.NUMBER]
        assert len(nums) == 1
        assert nums[0].text == "5"

    def test_emoji(self):
        tokens = self.tok.tokenize("هلا 👋")
        emoji = [t for t in tokens if t.token_type == TokenType.EMOJI]
        assert len(emoji) == 1

    def test_code_switching_english(self):
        tokens = self.tok.tokenize(" Meeting الحين ")
        code_switch = [t for t in tokens if t.token_type == TokenType.CODE_SWITCH]
        assert len(code_switch) >= 1
        assert code_switch[0].language == "en"

    def test_arabic_word_token_type(self):
        tokens = self.tok.tokenize("الحمد لله")
        words = [t for t in tokens if t.token_type == TokenType.WORD]
        assert len(words) >= 2

    def test_token_positions(self):
        tokens = self.tok.tokenize("وين")
        assert tokens[0].start == 0
        assert tokens[0].end == len("وين")

    def test_token_positions_with_prefix(self):
        tokens = self.tok.tokenize("هلا وين")
        # First token is dialect at position 0
        # Whitespace at position after "هلا"
        # Second dialect token after whitespace
        dialect = [t for t in tokens if t.token_type == TokenType.DIALECT]
        assert len(dialect) == 2
        assert dialect[0].start == 0
        assert dialect[1].start > dialect[0].end


class TestMSATokenizer:
    """Test MSA tokenizer."""

    def setup_method(self):
        self.tok = MSATokenizer()

    def test_empty_input(self):
        assert self.tok.tokenize("") == []

    def test_basic_tokenization(self):
        tokens = self.tok.tokenize("هذا تقرير")
        words = [t for t in tokens if t.token_type == TokenType.WORD]
        assert len(words) == 2

    def test_diacritics_stripping(self):
        # Text with diacritics
        text = "بِسْمِ ٱللَّهِ"
        tokens = self.tok.tokenize(text)
        words = [t for t in tokens if t.token_type == TokenType.WORD]
        assert len(words) >= 1
        # Diacritics should be stripped in normalization
        for w in words:
            if w.dialect_meaning:
                assert "\u064b" not in w.dialect_meaning  # Fathatan
                assert "\u064e" not in w.dialect_meaning  # Fatha

    def test_alef_unification(self):
        text = "أحمد إبراهيم آمن"
        tokens = self.tok.tokenize(text)
        words = [t for t in tokens if t.token_type == TokenType.WORD]
        for w in words:
            if w.dialect_meaning:
                # All alef variants should be unified
                assert "أ" not in w.dialect_meaning
                assert "إ" not in w.dialect_meaning
                assert "آ" not in w.dialect_meaning

    def test_normalize_method(self):
        text = "أحمد"
        normalized = self.tok.normalize(text)
        assert normalized == "احمد"

    def test_strip_diacritics_function(self):
        assert _strip_diacritics("بِسْمِ") == "بسم"

    def test_unify_alef_function(self):
        assert _unify_alef("أحمد") == "احمد"
        assert _unify_alef("إبراهيم") == "ابراهيم"
        assert _unify_alef("آمن") == "امن"

    def test_english_code_switch(self):
        tokens = self.tok.tokenize("التقرير report جاهز")
        cs = [t for t in tokens if t.token_type == TokenType.CODE_SWITCH]
        assert len(cs) == 1
        assert cs[0].text == "report"

    def test_numbers(self):
        tokens = self.tok.tokenize("في 1445 هجرياً")
        nums = [t for t in tokens if t.token_type == TokenType.NUMBER]
        assert len(nums) == 1

    def test_no_diacritics_mode(self):
        tok = MSATokenizer(strip_diacritics=False)
        text = "بِسْمِ"
        tokens = tok.tokenize(text)
        words = [t for t in tokens if t.token_type == TokenType.WORD]
        # When strip_diacritics=False, no normalization is applied
        for w in words:
            # dialect_meaning should be None since no normalization happened
            assert w.dialect_meaning is None

    def test_performance_batch(self):
        """Tokenization should be fast for batch processing."""
        import time

        texts = ["هذا تقرير رسمي"] * 100
        start = time.perf_counter()
        for text in texts:
            self.tok.tokenize(text)
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"Batch tokenization took {elapsed:.2f}s, expected <1s"
