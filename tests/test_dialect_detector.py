"""Tests for DialectDetector — rule-based and fasttext dialect detection."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest
from kazma_core.dialect_detector import (
    DialectDetector,
    DialectResult,
    _rule_based_detect,
)


class TestDialectResult:
    """Test the DialectResult dataclass."""

    def test_basic_creation(self):
        r = DialectResult(dialect="kw", confidence=0.95)
        assert r.dialect == "kw"
        assert r.confidence == 0.95
        assert r.alternatives == []

    def test_with_alternatives(self):
        r = DialectResult(
            dialect="kw",
            confidence=0.8,
            alternatives=[("msa", 0.15), ("eg", 0.05)],
        )
        assert len(r.alternatives) == 2
        assert r.alternatives[0] == ("msa", 0.15)

    def test_is_frozen(self):
        r = DialectResult(dialect="kw", confidence=0.9)
        with pytest.raises(AttributeError):
            r.dialect = "msa"


class TestRuleBasedDetection:
    """Test rule-based dialect detection without fasttext."""

    def test_empty_text_returns_msa(self):
        r = _rule_based_detect("")
        assert r.dialect == "msa"
        assert r.confidence >= 0.5

    def test_kuwaiti_markers_detected(self):
        r = _rule_based_detect("شلونك وينك ليش تأخرت")
        assert r.dialect == "kw"
        assert r.confidence > 0.5

    def test_single_kuwaiti_word(self):
        r = _rule_based_detect("وين")
        assert r.dialect == "kw"
        assert r.confidence >= 0.5

    def test_multiple_kuwaiti_words_boost_confidence(self):
        r1 = _rule_based_detect("وين")
        r2 = _rule_based_detect("شلونك وين ليش هلا تمام")
        # More markers should give higher or equal confidence
        assert r2.confidence >= r1.confidence

    def test_msa_formal_text(self):
        r = _rule_based_detect("بناءً على التقارير الرسمية، أعلنت الدولة عن")
        assert r.dialect == "msa"

    def test_egyptian_markers(self):
        r = _rule_based_detect("أنا عايز أعرف إيه اللي حصل ليه")
        assert r.dialect == "eg"
        assert r.confidence > 0.5

    def test_levantine_markers(self):
        r = _rule_based_detect("شو عم صار هيك بكرا")
        assert r.dialect == "lb"
        assert r.confidence > 0.5

    def test_maghrebi_markers(self):
        r = _rule_based_detect("واش كاين شي بزاف حوايج")
        assert r.dialect == "ma"
        assert r.confidence > 0.5

    def test_no_markers_defaults_to_msa(self):
        r = _rule_based_detect("اللغة العربية جميلة")
        assert r.dialect == "msa"


class TestDialectDetector:
    """Test the DialectDetector class."""

    def test_supported_dialects(self):
        d = DialectDetector()
        assert "kw" in d.SUPPORTED_DIALECTS
        assert "eg" in d.SUPPORTED_DIALECTS
        assert "lb" in d.SUPPORTED_DIALECTS
        assert "ma" in d.SUPPORTED_DIALECTS
        assert "msa" in d.SUPPORTED_DIALECTS

    def test_detect_without_fasttext(self):
        d = DialectDetector()
        result = d.detect("شلونك وين ليش")
        assert isinstance(result, DialectResult)
        assert result.dialect == "kw"
        assert result.confidence > 0.5

    def test_detect_empty(self):
        d = DialectDetector()
        result = d.detect("")
        assert result.dialect == "msa"
        assert result.confidence == 0.5

    def test_detect_batch(self):
        d = DialectDetector()
        results = d.detect_batch([
            "شلونك وين",
            "هذا تقرير رسمي",
            "أنا عايز أعرف",
        ])
        assert len(results) == 3
        assert results[0].dialect == "kw"
        assert results[2].dialect == "eg"

    def test_detect_batch_empty(self):
        d = DialectDetector()
        results = d.detect_batch([])
        assert results == []

    def test_performance_under_50ms(self):
        """Dialect detection should be under 50ms for typical input."""
        d = DialectDetector()
        text = "شلونك وين ليش تأخرت اليوم هلا تمام"
        start = time.perf_counter()
        for _ in range(100):
            d.detect(text)
        elapsed_ms = (time.perf_counter() - start) * 1000 / 100
        assert elapsed_ms < 50, f"Detection took {elapsed_ms:.1f}ms, expected <50ms"

    def test_fasttext_fallback(self):
        """When fasttext model fails to load, falls back to rule-based."""
        with patch.dict("sys.modules", {"fasttext": None}):
            d = DialectDetector(model_path="/nonexistent/model.bin")
            result = d.detect("شلونك")
            # Should still work via rule-based fallback
            assert result.dialect == "kw"

    def test_fasttext_model_integration(self):
        """Test fasttext path when model is mockable."""
        mock_model = MagicMock()
        mock_model.predict.return_value = (
            ["__label__kw", "__label__msa"],
            [0.92, 0.08],
        )

        d = DialectDetector()
        d._loaded = True
        d._model = mock_model

        result = d.detect("شلونك وين")
        assert result.dialect == "kw"
        assert result.confidence == 0.92
        assert len(result.alternatives) == 1

    def test_deterministic_results(self):
        """Same input should produce same output."""
        d = DialectDetector()
        r1 = d.detect("شلونك وين ليش")
        r2 = d.detect("شلونك وين ليش")
        assert r1.dialect == r2.dialect
        assert r1.confidence == r2.confidence
