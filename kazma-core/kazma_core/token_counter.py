from __future__ import annotations

import logging

__all__ = ["TokenCounter", "resolve_context_window"]

from kazma_core.summarizer import _normalize_msg


logger = logging.getLogger(__name__)

#: The shipped context window (the owner's decision, 2026-10-18; it was
#: 128,000, and a guessed table cut known model names lower).
DEFAULT_CONTEXT_WINDOW = 1_000_000


def resolve_context_window(raw_config: dict | None = None, model: str | None = None) -> int:
    """Single source of truth for the effective context window (tokens).

    Resolution ladder (first hit wins):
      1. Per-model ConfigStore override ``models.context_window.<model>``
         (``lookup_context_window``): the owner's word for that model.
      2. Settings UI global: ConfigStore ``context.max_context_tokens``.
      3. YAML: ``memory.max_context_tokens``.
      4. ``DEFAULT_CONTEXT_WINDOW`` (1,000,000).

    No guessed per-model table (removed 2026-10-18, the owner's decision).

    Never raises; always returns >= 1024. Shared by ``agent_runner`` (the
    ContextAuthority gate) and the graph's saturation routing so every
    compaction trigger reads the same window (audit: unification).
    """
    if model:
        try:
            from kazma_core.model_registry import lookup_context_window

            per_model = lookup_context_window(model)
            if per_model:
                return per_model
        except Exception:
            pass
    try:
        from kazma_core.config_store import get_config_store

        cs_val = get_config_store().get("context.max_context_tokens")
        if cs_val is not None:
            try:
                return max(1024, int(cs_val))
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    if raw_config:
        yaml_val = (raw_config.get("memory", {}) or {}).get("max_context_tokens")
        if yaml_val is not None:
            try:
                return max(1024, int(yaml_val))
            except (TypeError, ValueError):
                pass
    return DEFAULT_CONTEXT_WINDOW

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
        messages = [_normalize_msg(m) for m in messages]
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
