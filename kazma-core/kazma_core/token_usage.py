"""A model call's tokens, read from its usage whatever the provider's shape.

The per-call ledger (``observability.llm_ledger``) reads every answered
call's usage here (the memory plan's finding 9: the cache's share was in no
row), and so do scripts that count a model's spend themselves
(``scripts/memory_real_use.py``'s judge). Standard library only, no I/O:
importing it touches no data folder.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

__all__ = ["cache_write_tokens", "token_counts"]


class _TokenCounts(NamedTuple):
    """A model call's tokens: its prompt (cached share included), its
    completion, and the prompt tokens the provider served from its cache."""

    prompt: int
    completion: int
    cached: int


def _count(value: Any) -> int:
    """A count a usage holds, or 0 (never a bool, a negative or a text)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def token_counts(usage: Any) -> _TokenCounts:
    """The tokens of one call, read from its usage whatever the provider's
    shape. Never raises; a usage it cannot read counts nothing.

    * OpenAI-compatible (OpenAI, Qwen on DashScope, vLLM, Gemini's
      compatible API): ``prompt_tokens``, ``completion_tokens``, the cached
      share in ``prompt_tokens_details.cached_tokens``;
    * DeepSeek: the same counts, the cached share in
      ``prompt_cache_hit_tokens`` (``prompt_tokens`` includes it);
    * OpenAI's Responses API: ``input_tokens`` (cached share included),
      ``output_tokens``, ``input_tokens_details.cached_tokens``;
    * Anthropic (Kazma's client): ``input_tokens`` is the prompt AFTER the
      cache, ``cache_read_input_tokens`` (and ``cache_creation_input_tokens``)
      beside it, so the prompt is their sum;
    * an OpenAI-shaped answer naming ``cache_read_input_tokens`` too (a proxy
      in front of Anthropic): its ``prompt_tokens`` includes them.

    The cached share is never more than the prompt.
    """
    if not isinstance(usage, Mapping):
        return _TokenCounts(0, 0, 0)
    completion = _count(usage.get("completion_tokens")) or _count(usage.get("output_tokens"))
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    cached = 0
    if isinstance(details, Mapping):
        cached = _count(details.get("cached_tokens")) or _count(details.get("cache_read_input_tokens"))
    if "prompt_tokens" in usage:
        prompt = _count(usage.get("prompt_tokens"))
        cached = (cached or _count(usage.get("prompt_cache_hit_tokens")) or _count(usage.get("cached_tokens"))
                  or _count(usage.get("cache_read_input_tokens")))
    elif "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage:
        read = _count(usage.get("cache_read_input_tokens"))
        prompt = _count(usage.get("input_tokens")) + read + cache_write_tokens(usage)
        cached = cached or read
    else:
        prompt = _count(usage.get("input_tokens"))
    return _TokenCounts(prompt, completion, min(cached, prompt))


def cache_write_tokens(usage: Any) -> int:
    """The prompt tokens a call wrote to the provider's cache, billed over the
    input price: among the prompt's details (Model Studio's explicit cache)
    or beside ``input_tokens`` (Anthropic, Bedrock). Never raises."""
    if not isinstance(usage, Mapping):
        return 0
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details")
    written = _count(details.get("cache_creation_input_tokens")) if isinstance(details, Mapping) else 0
    if written or "prompt_tokens" in usage:
        return written
    return _count(usage.get("cache_creation_input_tokens"))
