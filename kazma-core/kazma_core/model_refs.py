"""A model named with its provider: ``provider:model`` (providers stage 3).

A model used to be a bare name: the first enabled provider that listed it got
the request, the same name under two providers (``gpt-5-mini`` on OpenAI and
on OpenRouter, ``llama-3.3-70b`` on Groq and Cerebras) could not be told
apart, and the pickers showed bare names. A reference names both:

    openrouter:google/gemini-2.5-flash
    Z.AI:glm-4.6
    ollama:llama3:8b

The part before the first ``:`` is a provider only when a provider row has
that name (any case). Model ids hold colons of their own (Ollama's
``llama3:8b``, OpenRouter's ``...:free``), and ``llama3`` is no provider, so
those stay bare names. A bare name still works everywhere: it is looked up
as before, preferring the provider in use when two list it.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = ["format_ref", "split_ref"]


def split_ref(ref: str, provider_names: Iterable[str]) -> tuple[str, str]:
    """``(provider, model)`` for *ref*: the provider named before the first
    ``:`` when one of *provider_names* is that name (its own spelling is
    returned), else ``("", ref)``."""
    text = str(ref or "").strip()
    if ":" not in text:
        return "", text
    prefix, _, rest = text.partition(":")
    rest = rest.strip()
    wanted = prefix.strip().lower()
    if not wanted or not rest:
        return "", text
    for name in provider_names:
        if str(name or "").strip().lower() == wanted:
            return str(name).strip(), rest
    return "", text


def format_ref(provider: str, model: str) -> str:
    """``provider:model``, or the bare model when there is no provider."""
    provider, model = str(provider or "").strip(), str(model or "").strip()
    return f"{provider}:{model}" if provider and model else model
