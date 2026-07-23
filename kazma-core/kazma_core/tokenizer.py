"""Dual-engine tokenizer that routes text based on dialect detection."""

from __future__ import annotations

from dataclasses import dataclass

from kazma_core.dialect_detector import DialectDetector, DialectResult
from kazma_core.kuwaiti_tokenizer import KuwaitiTokenizer, Token
from kazma_core.msa_tokenizer import MSATokenizer

__all__ = ["DualEngineTokenizer", "TokenResult"]


@dataclass(frozen=True, slots=True)
class TokenResult:
    """Result of tokenization including dialect metadata."""

    tokens: list[Token]
    dialect: DialectResult
    text: str = ""  # original input text

    @property
    def token_texts(self) -> list[str]:
        """Extract just the token strings."""
        return [t.text for t in self.tokens if t.token_type.value != "whitespace"]

    @property
    def dialect_tokens(self) -> list[Token]:
        """Get only dialect-classified tokens."""
        return [t for t in self.tokens if t.token_type.value == "dialect"]

    @property
    def code_switch_tokens(self) -> list[Token]:
        """Get only code-switched (English) tokens."""
        return [t for t in self.tokens if t.token_type.value == "code_switch"]

    @property
    def is_kuwaiti(self) -> bool:
        return self.dialect.dialect == "kw"

    @property
    def is_msa(self) -> bool:
        return self.dialect.dialect == "msa"


class DualEngineTokenizer:
    """Routes text to appropriate tokenizer based on dialect.

    The detection happens first, then text is dispatched to either
    the KuwaitiTokenizer (for Gulf Arabic) or MSATokenizer (for formal Arabic).
    """

    def __init__(self) -> None:
        self.kuwaiti_tokenizer = KuwaitiTokenizer()
        self.msa_tokenizer = MSATokenizer()
        self.detector = DialectDetector()

    def tokenize(self, text: str) -> TokenResult:
        """Tokenize text with dialect-aware routing.

        1. Detect dialect
        2. Route to appropriate tokenizer
        3. Return tokens + dialect metadata
        """
        if not text or not text.strip():
            return TokenResult(
                tokens=[],
                dialect=DialectResult(dialect="msa", confidence=0.5, alternatives=[]),
                text=text,
            )

        result = self.detector.detect(text)

        if result.dialect == "kw":
            tokens = self.kuwaiti_tokenizer.tokenize(text)
        else:
            tokens = self.msa_tokenizer.tokenize(text)

        return TokenResult(tokens=tokens, dialect=result, text=text)

    def tokenize_batch(self, texts: list[str]) -> list[TokenResult]:
        """Batch tokenization for efficiency."""
        # Batch detect dialects
        dialect_results = self.detector.detect_batch(texts)

        results: list[TokenResult] = []
        for text, dialect in zip(texts, dialect_results):
            if dialect.dialect == "kw":
                tokens = self.kuwaiti_tokenizer.tokenize(text)
            else:
                tokens = self.msa_tokenizer.tokenize(text)
            results.append(TokenResult(tokens=tokens, dialect=dialect, text=text))

        return results
