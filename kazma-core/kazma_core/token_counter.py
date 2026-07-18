from __future__ import annotations

import logging

__all__ = ["TokenCounter"]

logger = logging.getLogger(__name__)

# Try to import tiktoken; fall back to None if not installed
try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False
    logger.debug("tiktoken not installed; using heuristic token counting")


class TokenCounter:
    """Counts tokens in conversation messages and determines compaction thresholds."""

    def __init__(self, model: str, window: int = 128000) -> None:
        self.model = model
        self.window = window
        self.threshold = int(window * 0.8)  # hardcoded 80%
        self._encoder = None

        if _TIKTOKEN_AVAILABLE:
            try:
                self._encoder = tiktoken.encoding_for_model(model)
                logger.debug("Using tiktoken encoder for model %s", model)
            except KeyError:
                # Model not found; fall back to heuristic
                logger.debug("tiktoken has no encoder for model %s; using heuristic", model)

    def count(self, messages: list[dict]) -> int:
        """Return total token count for a list of messages."""
        total = 0
        for msg in messages:
            # 4 tokens overhead per message for role/formatting
            total += 4
            content = msg.get("content", "")
            if isinstance(content, str):
                if self._encoder is not None:
                    total += len(self._encoder.encode(content))
                else:
                    # Heuristic: ~1 token per 4 characters
                    total += (len(content) + 3) // 4
            elif isinstance(content, list):
                # Handle content arrays (e.g., multimodal messages)
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        text = part["text"]
                        if self._encoder is not None:
                            total += len(self._encoder.encode(text))
                        else:
                            total += (len(text) + 3) // 4
        return total

    def should_compact(self, messages: list[dict]) -> bool:
        """Return True if token count has reached the compaction threshold."""
        return self.count(messages) >= self.threshold
