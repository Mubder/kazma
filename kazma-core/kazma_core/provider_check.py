"""One check of a provider, saved or only typed, through the client chat uses.

Providers stage 1 (2026-11-08). Settings' Test probed every provider with
OpenAI's request (``Authorization: Bearer`` to ``/models``, then
``/chat/completions``), so Anthropic, Azure and Bedrock failed Test while
chat worked, and a provider that answered ``/models`` but not chat in its own
format passed; the setup guide used the real client, so the two could
disagree. And both saved the key before checking it: Settings' dialog wrote
the provider, typed key and all, before testing, and the guide saved the
key before listing the models -- a refused key stayed saved.

Here: a *candidate* is built from what was typed and what is stored
(:func:`candidate_entry`); its models are listed (:func:`list_models`) and
one short message is sent (:func:`check_model`) through the client class
chat would build for it (``provider_adapters.build_client``). Nothing is
saved: the caller saves only after the check answered.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CHECK_MAX_TOKENS",
    "CHECK_PROMPT",
    "CHECK_TIMEOUT_S",
    "LOCAL_NAMES",
    "CandidateRefused",
    "candidate_entry",
    "check_model",
    "list_models",
]

#: Providers that run on this computer under their own names.
LOCAL_NAMES = frozenset({"ollama", "lm-studio", "lmstudio", "local"})

#: How long one check may wait for the model's answer.
CHECK_TIMEOUT_S = 60.0

#: The one message a check sends.
CHECK_PROMPT = "Reply with the word OK."

_MASKS = {"***", "****", "••••"}


class CandidateRefused(Exception):
    """A provider that cannot be checked, with a code the pages word:
    ``unknown_provider``, ``no_base_url``, ``api_key_required``,
    ``key_undecryptable``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _masked(key: str) -> bool:
    return key in _MASKS or "****" in key or key.startswith("vault://")


def candidate_entry(registry: Any, name: str, *, base_url: str = "", api_key: str = "") -> dict[str, Any]:
    """The provider to check: the stored row (if any) with what was typed
    over it -- an empty or masked key means the stored one. Blocking
    (settings and the vault): async callers use ``asyncio.to_thread``.

    Raises :class:`CandidateRefused` when there is nothing to check.
    """
    from kazma_core.providers import PROVIDER_PRESETS
    from kazma_core.runtime.live_llm import coerce_api_key, key_is_usable, url_is_local

    pid = str(name or "").strip()
    stored = registry.get_provider(pid) if pid else None
    preset = PROVIDER_PRESETS.get(pid.lower()) if pid else None
    if not pid or (stored is None and preset is None and not base_url.strip()):
        raise CandidateRefused("unknown_provider", f"There is no provider named {pid!r}.")
    base = (base_url.strip() or str((stored or {}).get("base_url") or "")
            or str((preset or {}).get("base_url") or ""))
    key = coerce_api_key(api_key)
    if key and _masked(key):
        key = ""
    if not key and stored is not None:
        _resolved_url, resolved = registry.resolve_provider_credentials(pid)
        resolved = coerce_api_key(resolved)
        key = resolved if key_is_usable(resolved) else ""
    local = pid.lower() in LOCAL_NAMES or url_is_local(base)
    if not base and pid.lower() != "google":
        raise CandidateRefused("no_base_url", "The provider has no address.")
    if not key and not local:
        if stored is not None and registry.stored_key_is_undecryptable(pid):
            raise CandidateRefused(
                "key_undecryptable",
                "A key is saved for this provider, but the vault cannot decrypt it: KAZMA_VAULT_KEY is "
                "missing from this process, or is not the value the key was encrypted with.",
            )
        raise CandidateRefused("api_key_required", "This provider needs an API key.")
    entry = dict(stored or {})
    entry.update({"name": pid, "base_url": base, "api_key": key})
    entry.setdefault("display_name", str((preset or {}).get("name") or pid))
    return entry


async def list_models(registry: Any, entry: dict[str, Any]) -> Any:
    """The candidate's models (a ``DiscoveryResult``), asked with what was
    typed; nothing is cached or saved."""
    return await registry.discover(str(entry["name"]), candidate=entry)


def _connection_failed(exc: BaseException) -> bool:
    """True when *exc* (or what caused it) is a failure to reach the
    provider at all, not an answer from it."""
    import httpx

    seen: BaseException | None = exc
    for _ in range(6):
        if seen is None:
            return False
        if isinstance(seen, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionRefusedError)):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


#: Generous on purpose (the old Bearer probe's lesson, kept): a reasoning
#: model spends tokens thinking before it writes anything, so a tight cap
#: returns an EMPTY answer and blames the provider for the check's own
#: budget. Measured: glm-5.3 used 16 reasoning tokens before its first one.
CHECK_MAX_TOKENS = 160

#: What a failed check says, by what failed: a code the pages word in their
#: language, and the HTTP status -- never the provider's reply text, which can
#: carry what an internal proxy or error page holds (the Test route's rule).
_STATUS_CODES = {401: "key_refused", 403: "key_refused", 404: "not_found", 429: "rate_limited"}


def _check_error(exc: BaseException) -> tuple[str, str]:
    """``(code, English sentence)`` for a check that raised *exc*."""
    from kazma_core.retry import _extract_http_status_code

    if _connection_failed(exc):
        return "unreachable", "The provider could not be reached."
    kind = str(getattr(exc, "kind", "") or "")
    if kind in ("unusable_answer", "stream_truncated"):
        return "unusable_answer", "The provider answered, but not with a model answer."
    if kind == "rate_limit_exhausted":
        return "rate_limited", "The provider is limiting how often it may be called (HTTP 429)."
    status = _extract_http_status_code(exc if isinstance(exc, Exception) else Exception(str(exc)))
    if status:
        code = _STATUS_CODES.get(status) or ("provider_error" if status >= 500 else "request_refused")
        words = {
            "key_refused": "The provider refused the key",
            "not_found": "The provider has no such model, or the address is wrong",
            "rate_limited": "The provider is limiting how often it may be called",
            "provider_error": "The provider failed to answer",
            "request_refused": "The provider refused the request",
        }[code]
        return code, f"{words} (HTTP {status})."
    return "failed", f"The check failed ({type(exc).__name__})."


async def check_model(entry: dict[str, Any], model: str, *, timeout: float = CHECK_TIMEOUT_S) -> dict[str, Any]:
    """One short message to *model* through the client chat would build for
    *entry*: ``{"ok", "ms", "model", "reachable", "error_code", "error"}``.
    An answer with no text and no tool call is a failure (``empty``): a 200
    that says nothing is not a working provider."""
    import httpx

    from kazma_core.bounded_cancel import wait_for_bounded
    from kazma_core.llm_provider import LLMConfig, LLMError
    from kazma_core.provider_adapters import build_client

    config = LLMConfig.from_dict({
        "base_url": str(entry.get("base_url") or ""),
        "api_key": str(entry.get("api_key") or ""),
        "model": model,
    })
    # A Google client may read Application Default Credentials: off the loop.
    client = await asyncio.to_thread(build_client, str(entry["name"]), config, entry)
    kwargs: dict[str, Any] = {"max_tokens": CHECK_MAX_TOKENS, "model": model}
    can_off = getattr(client, "can_turn_off_reasoning", None)
    if callable(can_off) and can_off():
        kwargs["reasoning"] = "off"
    started = time.monotonic()
    try:
        resp = await wait_for_bounded(
            client.chat([{"role": "user", "content": CHECK_PROMPT}], **kwargs), timeout, what="provider check",
        )
    except TimeoutError:
        return {"ok": False, "model": model, "reachable": True, "error_code": "timeout",
                "error": f"The model did not answer within {int(timeout)} seconds."}
    except (LLMError, httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
        code, sentence = _check_error(exc)
        return {"ok": False, "model": model, "reachable": code != "unreachable",
                "error_code": code, "error": sentence}
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            try:
                await closer()
            except Exception:  # noqa: BLE001 - a client that cannot close changes no answer
                logger.debug("[provider-check] client close failed", exc_info=True)
    if not str(getattr(resp, "content", "") or "").strip() and not getattr(resp, "tool_calls", None):
        return {"ok": False, "model": model, "reachable": True, "error_code": "empty",
                "error": "The provider returned an empty completion."}
    return {"ok": True, "ms": int((time.monotonic() - started) * 1000), "model": model, "reachable": True}
