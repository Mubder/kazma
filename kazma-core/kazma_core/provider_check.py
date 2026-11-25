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
    "known_price",
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

#: How much of the model's answer the page shows.
ANSWER_MAX = 400

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
    "aws_secret_access_key", "aws_session_token", "google_mode", "project_id", "location", "kind",
)

#: Typed fields that hold a secret: masked or empty means the stored one.
_SECRET_FIELDS = ("aws_secret_access_key", "aws_session_token")

#: Every field of a stored provider row that holds a secret.
STORED_SECRET_FIELDS = (
    "api_key", "api_keys", "secret_headers", "aws_access_key_id", *_SECRET_FIELDS,
)


def address_moved(typed_base_url: str, stored: dict[str, Any] | None) -> bool:
    """True when a form names another address than the stored row's.

    A stored secret is kept only for the address it was saved with
    (``url_credentials.same_address``, 2026-11-15): posted masked beside
    another address, the stored key, headers and extra keys would go to a
    server someone just typed in. An empty typed address is the stored (or
    the preset's) one, not a move.
    """
    from kazma_core.security.url_credentials import same_address

    typed = str(typed_base_url or "").strip()
    return bool(stored) and bool(typed) and not same_address(typed, (stored or {}).get("base_url"))


MOVED_KEY_MESSAGE = (
    "Enter the API key again: the saved key is kept only for the address it was saved with."
)


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


def _refuse_fields(entry: dict[str, Any], stored: dict[str, Any] | None) -> None:
    """Refuse a candidate whose own fields are missing or malformed
    (:func:`kazma_core.provider_setup.field_problems`), before anything is
    sent to the address they would make."""
    from kazma_core.provider_setup import field_problems

    problems = field_problems(entry, stored)
    if problems:
        first = problems[0]
        words = "is needed" if first["code"] == "missing" else "does not look right"
        raise CandidateRefused(f"field_{first['code']}", f"{first['field']} {words}.")


def _refuse_credentials(problem: str, registry: Any, pid: str, *, stored: bool, moved: bool) -> None:
    """Raise the :class:`CandidateRefused` for a credentials *problem*."""
    if problem == "api_key_required" and moved:
        raise CandidateRefused("api_key_required", MOVED_KEY_MESSAGE)
    if problem == "api_key_required" and stored and registry.stored_key_is_undecryptable(pid):
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
    # Another address than the stored row's: nothing stored goes to it.
    moved = address_moved(base_url, stored)
    if moved:
        stored = {k: v for k, v in (stored or {}).items() if k not in STORED_SECRET_FIELDS}
    key = coerce_api_key(api_key)
    if key and _masked(key):
        key = ""
    if not key and stored is not None and not moved:
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
    _refuse_fields(entry, stored)
    # Moved: the stored key is not this address's, so it cannot count.
    problem = credentials_problem(None if moved else registry, entry, key)
    if problem is not None:
        _refuse_credentials(problem, registry, pid, stored=stored is not None, moved=moved)
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
    if kind == "content_refused":
        return "content_refused", "The provider's content filter refused the message."
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


async def check_model(
    entry: dict[str, Any],
    model: str,
    *,
    timeout: float = CHECK_TIMEOUT_S,
    own_key_of: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """One short message to *model* through the client chat would build for
    *entry*: ``{"ok", "ms", "model", "reachable", "error_code", "error"}``.
    An answer with no text and no tool call is a failure (``empty``): a 200
    that says nothing is not a working provider.

    ``own_key_of`` (space, account): the check of a person's own key
    (Spaces S2-04). It goes to the provider's own address with their key
    alone, never through the install's egress proxy -- whose own key would
    answer in its place, so a key that does not work was kept and the
    install paid for the check (the review of S2-04) -- and its row in the
    per-call ledger is theirs, ``own_key`` only when their key answered."""
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
    price = await asyncio.to_thread(known_price, entry, model)
    if price is not None:
        config.input_cost_per_1m, config.output_cost_per_1m = price["input"], price["output"]
        config.cached_input_cost_per_1m = price["cached_input"]
    direct_url = str(config.base_url or "")
    typed_key = str(config.api_key or "")
    # A Google client may read Application Default Credentials: off the loop.
    client = await asyncio.to_thread(build_client, str(entry["name"]), config, entry)
    # The key checked is the one typed: never a person's kept key in its
    # place (Spaces S2-04, ``LLMProvider._person_call``).
    client._key_is_given = True
    if own_key_of is not None:
        _go_direct(client, direct_url, typed_key)
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
        await _ledger_own_key_check(own_key_of, entry, model, None, started, "timeout")
        return {"ok": False, "model": model, "reachable": True, "error_code": "timeout",
                "error": f"The model did not answer within {int(timeout)} seconds."}
    except (LLMError, httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
        code, sentence = _check_error(exc)
        await _ledger_own_key_check(own_key_of, entry, model, None, started, code)
        return {"ok": False, "model": model, "reachable": code != "unreachable",
                "error_code": code, "error": sentence}
    finally:
        closer = getattr(client, "close", None)
        if callable(closer):
            try:
                await closer()
            except Exception:  # noqa: BLE001 - a client that cannot close changes no answer
                logger.debug("[provider-check] client close failed", exc_info=True)
    await _ledger_own_key_check(own_key_of, entry, model, resp, started, "")
    empty = not str(getattr(resp, "content", "") or "").strip() and not getattr(resp, "tool_calls", None)
    thinking = empty and int(getattr(resp, "reasoning_chars", 0) or 0) > 0
    if empty and not thinking:
        return {"ok": False, "model": model, "reachable": True, "error_code": "empty",
                "error": "The provider returned an empty completion."}
    return {"ok": True, "ms": int((time.monotonic() - started) * 1000), "model": model, "reachable": True,
            # A model cut at the check's own limit while it was thinking works.
            "thinking": thinking,
            "answer": str(getattr(resp, "content", "") or "").strip()[:ANSWER_MAX], "price": price,
            "cost_usd": float(resp.cost_usd) if getattr(resp, "cost_known", False) else None}


def known_price(entry: dict[str, Any], model: str, listed: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """What *model* costs at the candidate *entry*, as the pages show it
    (dollars per million tokens, and where the price is from), or None: the
    owner's price, free on this network, what its own list said (*listed*),
    Kazma's dated catalog. Reads the settings store: off the event loop."""
    from kazma_core.model_details import ModelDetails, details_for
    from kazma_core.model_prices import price_for

    name = str(entry.get("name") or "")
    details = ModelDetails()
    details.fill(listed or {}, "provider")
    known = details_for(name, model, preset=str(entry.get("preset") or ""))
    details.fill(known.as_dict(), "catalog")
    details.sources = {**known.sources, **{k: "provider" for k in (listed or {})}}
    price = price_for(name, str(entry.get("base_url") or ""), model, details=details)
    if price is None:
        return None
    return {"input": price.input_per_1m, "output": price.output_per_1m,
            "cached_input": price.cached_input_per_1m, "source": price.source}


def _go_direct(client: Any, url: str, key: str) -> None:
    """Pin *client* to the provider's own *url* and *key*: no egress proxy
    now or at its call (Spaces S2-04). Its constructor may have routed it
    through the proxy already, so the answer is compared with the strings
    taken before it was built. No I/O."""
    client._egress_frozen = True
    client._via_gateway = False
    config = getattr(client, "config", None)
    if config is not None and (str(config.base_url or "") != url or str(config.api_key or "") != key):
        client.reconfigure(base_url=url, api_key=key)


async def _ledger_own_key_check(
    own_key_of: tuple[str, str] | None,
    entry: dict[str, Any],
    model: str,
    response: Any,
    started: float,
    error_code: str,
) -> None:
    """The check of a person's own key in the per-call ledger, under their
    name (Spaces S2-04): a call they asked for, on their key when it
    answered (``own_key``), so no limit counts it; a refused one cost
    nothing and is an error row. Nothing for an install's check. Never
    raises but a cancellation."""
    if own_key_of is None:
        return
    from kazma_core.observability.llm_ledger import arecord_llm_call

    space, account = own_key_of
    usage = getattr(response, "usage", None)
    await arecord_llm_call(
        thread_id="own-key-check",
        provider=str(entry.get("name") or ""),
        model=str(getattr(response, "model", "") or model or ""),
        usage=usage if isinstance(usage, dict) else {},
        cost_usd=float(getattr(response, "cost_usd", 0.0) or 0.0) if response is not None else 0.0,
        duration_ms=(time.monotonic() - started) * 1000,
        status="ok" if response is not None else "error",
        error_kind=error_code,
        priced=bool(getattr(response, "cost_known", True)) if response is not None else True,
        space=space,
        asker=account,
        own_key=response is not None,
    )
