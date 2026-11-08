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
    "TYPED_FIELDS",
    "credentials_problem",
    "merge_typed_secrets",
    "CHECK_MAX_TOKENS",
    "CHECK_PROMPT",
    "CHECK_TIMEOUT_S",
    "LOCAL_NAMES",
    "CandidateRefused",
    "candidate_entry",
    "check_model",
    "list_models",
]

def _local_names() -> frozenset[str]:
    from kazma_core.providers import PROVIDER_PRESETS

    return frozenset({pid for pid, meta in PROVIDER_PRESETS.items() if meta.get("local")} | {"lmstudio", "local"})


#: Providers that run on this computer under their own names: the
#: catalog's local presets (providers stage 2).
LOCAL_NAMES = _local_names()

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


#: Fields a check may be given as typed, beside the address and key
#: (providers stage 2: the dialog's Azure, Bedrock, Google and header fields).
TYPED_FIELDS: tuple[str, ...] = (
    "preset", "url_as_typed", "secret_headers", "api_version", "region", "aws_access_key_id",
    "aws_secret_access_key", "aws_session_token", "google_mode", "project_id", "location",
)

#: Typed fields that hold a secret: masked or empty means the stored one.
_SECRET_FIELDS = ("aws_secret_access_key", "aws_session_token")


def merge_typed_secrets(typed: dict[str, Any], stored: dict[str, Any] | None) -> dict[str, Any]:
    """*typed* with each masked secret replaced by the stored one: an AWS
    secret, a header's value, an extra key (by id). The pages show every
    secret masked, and a form posted back unchanged keeps what was saved."""
    stored = stored or {}
    out = dict(typed)
    for field in _SECRET_FIELDS:
        if field in out and (not str(out[field] or "").strip() or _masked(str(out[field]))):
            out[field] = stored.get(field, "")
    if isinstance(out.get("secret_headers"), dict):
        kept = stored.get("secret_headers") if isinstance(stored.get("secret_headers"), dict) else {}
        out["secret_headers"] = {
            str(k).strip(): (kept.get(k, "") if _masked(str(v)) else str(v))
            for k, v in out["secret_headers"].items() if str(k).strip()
        }
    if isinstance(out.get("api_keys"), list):
        by_id = {str(k.get("id")): k for k in (stored.get("api_keys") or []) if isinstance(k, dict)}
        keys = []
        for k in out["api_keys"]:
            if not isinstance(k, dict) or not str(k.get("id") or "").strip():
                continue
            value = str(k.get("api_key") or "")
            if not value.strip() or _masked(value):
                value = str((by_id.get(str(k["id"])) or {}).get("api_key") or "")
            if value.strip():
                keys.append({"id": str(k["id"]), "label": str(k.get("label") or ""), "api_key": value})
        out["api_keys"] = keys
    return out


def credentials_problem(registry: Any, entry: dict[str, Any], key: str = "") -> str | None:
    """None when *entry* has what its sign-in needs, else a refusal code:
    a server on this network needs nothing; Bedrock its AWS keys (or the
    AWS chain of this computer); Google Vertex a project (Application
    Default Credentials); every other provider a key (*key*, else what the
    runtime would send). One answer for the check, the save and the backup."""
    import os

    from kazma_core.providers import PROVIDER_PRESETS, is_local_preset
    from kazma_core.runtime.live_llm import coerce_api_key, key_is_usable, url_is_local

    name = str(entry.get("name") or "")
    preset_id = str(entry.get("preset") or "").strip().lower() or name.lower()
    preset = PROVIDER_PRESETS.get(preset_id) or {}
    if is_local_preset(preset_id) or name.lower() in LOCAL_NAMES or url_is_local(str(entry.get("base_url") or "")):
        return None
    auth = str(preset.get("auth") or "bearer")
    if auth == "aws":
        typed = entry.get("aws_access_key_id") and entry.get("aws_secret_access_key")
        chain = os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE") or (
            os.path.exists(os.path.join(os.path.expanduser("~"), ".aws", "credentials")))
        return None if (typed or chain) else "aws_credentials_required"
    if auth == "google" and str(entry.get("google_mode") or "") == "vertex_ai":
        return None if str(entry.get("project_id") or "").strip() else "project_required"
    if key_is_usable(coerce_api_key(key)):
        return None
    if registry is not None and registry.get_provider(name) is not None:
        _url, resolved = registry.resolve_provider_credentials(name)
        if key_is_usable(coerce_api_key(resolved)):
            return None
    return "api_key_required"


def candidate_entry(
    registry: Any, name: str, *, base_url: str = "", api_key: str = "",
    typed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The provider to check: the stored row (if any) with what was typed
    over it -- an empty or masked key or secret means the stored one.
    *typed* carries the dialog's other fields (:data:`TYPED_FIELDS`).
    Blocking (settings and the vault): async callers use
    ``asyncio.to_thread``.

    Raises :class:`CandidateRefused` when there is nothing to check.
    """
    from kazma_core.providers import PROVIDER_PRESETS
    from kazma_core.runtime.live_llm import coerce_api_key, key_is_usable

    pid = str(name or "").strip()
    stored = registry.get_provider(pid) if pid else None
    extra = {k: v for k, v in (typed or {}).items() if k in TYPED_FIELDS}
    preset_id = str(extra.get("preset") or (stored or {}).get("preset") or pid).strip().lower()
    preset = PROVIDER_PRESETS.get(preset_id) if preset_id else None
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
    form = str((preset or {}).get("form") or "key")
    if not base and form not in ("google", "bedrock"):
        raise CandidateRefused("no_base_url", "The provider has no address.")
    if "ACCOUNT_ID" in base:
        raise CandidateRefused("url_placeholder", str((preset or {}).get("url_hint") or
                                                      "Replace the placeholder in the address."))
    entry = dict(stored or {})
    entry.update(merge_typed_secrets(extra, stored))
    entry.update({"name": pid, "base_url": base, "api_key": key})
    if preset is not None and preset_id != pid.lower():
        entry["preset"] = preset_id
    entry.setdefault("display_name", str((preset or {}).get("name") or pid))
    problem = credentials_problem(registry, entry, key)
    if problem is not None:
        if problem == "api_key_required" and stored is not None and registry.stored_key_is_undecryptable(pid):
            raise CandidateRefused(
                "key_undecryptable",
                "A key is saved for this provider, but the vault cannot decrypt it: KAZMA_VAULT_KEY is "
                "missing from this process, or is not the value the key was encrypted with.",
            )
        words = {
            "api_key_required": "This provider needs an API key.",
            "aws_credentials_required": "Bedrock needs an AWS access key id and secret (or AWS credentials on this computer).",
            "project_required": "Vertex AI needs a Google Cloud project.",
        }
        raise CandidateRefused(problem, words[problem])
    if entry.get("url_as_typed") and base:
        from kazma_core.url_utils import remember_as_typed

        remember_as_typed(base)
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
        # The row's own settings, as chat sends them (providers stage 2).
        "extra_headers": entry.get("secret_headers") or {},
        "api_version": str(entry.get("api_version") or ""),
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
