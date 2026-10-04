"""Per-decision X model binding without changing the global chat profile."""

from __future__ import annotations

import asyncio
import copy
import ipaddress
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import wraps
from typing import TYPE_CHECKING, Any, ParamSpec
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from kazma_core.llm_provider import LLMProvider

logger = logging.getLogger(__name__)
_P = ParamSpec("_P")
X_MODEL_ROLES = ("classification", "drafting", "verification", "post_drafting", "evidence",
                 "context_verification", "factual_verification", "safety_verification")


class XModelUnavailableError(ValueError):
    """The selected X model cannot be used; never substitute another model."""


def validate_selection(raw: Any) -> dict[str, Any]:
    """Validate stored or unsaved settings without touching any provider."""
    if raw is None:
        raw = {"selection": "global"}
    if not isinstance(raw, dict):
        raise XModelUnavailableError("X AI model settings are invalid. Review Settings → X.")
    mode = str(raw.get("selection", "global")).strip()
    if mode not in ("global", "specific"):
        raise XModelUnavailableError("X AI selection must be global or specific.")
    provider = str(raw.get("provider") or "").strip()
    model = str(raw.get("model") or "").strip()
    if mode == "specific" and (not provider or not model):
        raise XModelUnavailableError("Choose both a provider and an exact model ID for X.")
    if len(provider) > 200 or len(model) > 300:
        raise XModelUnavailableError("X provider or model ID is too long.")
    result: dict[str, Any] = {"selection": mode, "provider": provider, "model": model}
    if "local_only" in raw:
        if type(raw["local_only"]) is not bool:
            raise XModelUnavailableError("X local_only must be true or false.")
        result["local_only"] = raw["local_only"]
    if "roles" in raw:
        roles = raw["roles"]
        if not isinstance(roles, dict) or set(roles) - set(X_MODEL_ROLES):
            raise XModelUnavailableError("X model roles are invalid.")
        result["roles"] = {}
        for role, pair in roles.items():
            if not isinstance(pair, dict) or set(pair) - {"selection", "provider", "model"}:
                raise XModelUnavailableError("Each X role must contain only a model selection pair.")
            result["roles"][role] = validate_selection(pair)
    return result


def _local_endpoint(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or not parsed.hostname:
            return False
        if parsed.hostname.lower() == "localhost":
            return True
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _require_local(registry: Any, provider: str, *, client: Any = None) -> None:
    entry = next((p for p in registry.list_providers() if str(p.get("name") or "").casefold() == provider.casefold()), None)
    if provider.casefold() in ("google", "anthropic", "azure", "bedrock") or entry is None:
        raise XModelUnavailableError("X local-only mode requires a configured local OpenAI-compatible endpoint.")
    resolver = getattr(registry, "resolve_provider_credentials", None)
    endpoint = resolver(provider)[0] if resolver else entry.get("base_url", "")
    if not _local_endpoint(str(endpoint)):
        raise XModelUnavailableError("X local-only mode blocks this provider: its endpoint is not loopback.")
    if client is not None and not _local_endpoint(str(getattr(getattr(client, "config", None), "base_url", ""))):
        raise XModelUnavailableError("The resolved X client violates the local-only endpoint boundary.")


def model_options() -> list[dict[str, Any]]:
    """Configured enabled providers and cached models; never expose keys."""
    from kazma_core.model_registry import get_model_registry

    registry = get_model_registry()
    options = []
    for entry in registry.list_providers():
        name = str(entry.get("name") or "")
        if not name or not entry.get("enabled", True):
            continue
        models = registry.get_visible_models(name)
        pinned = str(entry.get("model") or "")
        if pinned and pinned not in models:
            models = [pinned, *models]
        options.append({"provider": name, "models": models,
                        "local_capable": _local_endpoint(str(entry.get("base_url") or "")) and name.casefold() not in ("google", "anthropic", "azure", "bedrock")})
    return options


def validate_provider(selection: dict[str, Any]) -> None:
    """Require an exact enabled provider, rather than fuzzy registry lookup."""
    from kazma_core.model_registry import get_model_registry

    for pair in selection.get("roles", {}).values():
        validate_provider({**pair, "local_only": selection.get("local_only", False)})
    if selection["selection"] == "global":
        if selection.get("local_only"):
            registry = get_model_registry()
            _require_local(registry, str(registry.get_active_profile().get("provider") or ""))
        return
    entries = get_model_registry().list_providers()
    entry = next((p for p in entries if str(p.get("name") or "").lower()
                  == selection["provider"].lower()), None)
    if entry is None or not entry.get("enabled", True):
        raise XModelUnavailableError("The selected X provider is missing or disabled. Review Settings → X.")
    if selection.get("local_only"):
        _require_local(get_model_registry(), selection["provider"])


@dataclass
class _Session:
    selection: Any
    client: Any = None
    identity: dict[str, str] = field(default_factory=dict)
    roles: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    registry: Any = None
    one_off: bool = False
    bindings: dict[str, _Session] = field(default_factory=dict)
    resolved: bool = False
    error: Exception | None = None
    started: float = field(default_factory=time.monotonic)
    calls: int = 0
    reserved_output_tokens: int = 0
    input_bytes: int = 0
    limits: dict[str, int] | None = None


_session: ContextVar[_Session | None] = ContextVar("x_model_session", default=None)


def _read_selection() -> Any:
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.ownership import x_config_key

    return get_config_store().get(x_config_key("connectors.x.ai"))


def _resolve(session: _Session) -> None:
    from kazma_core.model_registry import get_model_registry
    from kazma_core.tenant_context import get_current_tenant_id, tenant_scope

    def build() -> None:
        selection = validate_selection(session.selection)
        registry = get_model_registry()
        validate_provider(selection)
        if selection["selection"] == "global":
            client = registry.get_client()
            if client is None:
                return
            profile_reader = getattr(registry, "get_active_profile", None)
            profile = profile_reader() if profile_reader else {}
            provider = profile.get("provider", "")
            model = profile.get("model", "")
            provider_reader = getattr(registry, "provider_for_client", None)
            if provider_reader:
                provider = provider_reader(client) or provider
        else:
            validate_provider(selection)
            provider, model = selection["provider"], selection["model"]
            client = registry.get_client_by_provider(provider, model)
            session.one_off = True
        if client is None:
            raise XModelUnavailableError("The selected X model is unavailable. Review Settings → X.")
        session.client = client
        session.registry = registry
        config = getattr(client, "config", None)
        if selection.get("local_only"):
            _require_local(registry, provider, client=client)
        session.identity = {"provider": provider, "model": str(getattr(config, "model", model))}

    if get_current_tenant_id():
        build()
    else:
        from kazma_core.x_api.ownership import x_tenant_id

        with tenant_scope(x_tenant_id()):
            build()


@asynccontextmanager
async def x_model_scope(selection: Any = None) -> AsyncIterator[_Session]:
    """Snapshot settings and reuse one client across a complete X decision."""
    if _session.get() is not None:
        yield _session.get()
        return
    raw = selection if selection is not None else await asyncio.to_thread(_read_selection)
    session = _Session(copy.deepcopy(raw))
    token = _session.set(session)
    try:
        yield session
    finally:
        _session.reset(token)
        released = set()
        for binding in session.bindings.values():
            if not binding.one_off or binding.client is None or id(binding.client) in released:
                continue
            released.add(id(binding.client))
            release = getattr(binding.registry, "release_client", None)
            if release is not None:
                try:
                    await release(binding.client)
                except Exception:
                    logger.warning("[x-model] one-off client cleanup failed", exc_info=True)


async def get_x_client(role: str) -> LLMProvider | None:
    """Resolve X's pinned client off the loop; no fallback for an override."""
    session = _session.get()
    if session is None:
        raise RuntimeError("X client use requires an active x_model_scope.")
    if role not in X_MODEL_ROLES:
        raise XModelUnavailableError("Unknown X model role.")
    async with session.lock:
        settings = validate_selection(session.selection)
        pair = settings.get("roles", {}).get(role, {key: settings[key] for key in ("selection", "provider", "model")})
        selected = {**pair, "local_only": settings.get("local_only", False)}
        key = json.dumps(selected, sort_keys=True)
        binding = session.bindings.setdefault(key, _Session(selected))
        if binding.error is not None:
            raise binding.error
        if not binding.resolved:
            resolving = asyncio.create_task(asyncio.to_thread(_resolve, binding))
            try:
                await asyncio.shield(resolving)
            except asyncio.CancelledError:
                # Let construction finish so scope cleanup can close the
                # one-off client it creates; never abandon that resource.
                try:
                    await resolving
                except Exception:
                    pass
                raise
            except Exception as exc:
                binding.error = exc
                raise
            finally:
                binding.resolved = True
        if session.client is None and binding.client is not None:
            session.client, session.identity = binding.client, binding.identity
        if role not in binding.roles:
            binding.roles.append(role)
        return binding.client


def x_local_only() -> bool:
    """The complete pipeline boundary, also checked by evidence retrieval."""
    session = _session.get()
    return bool(validate_selection(session.selection if session else _read_selection()).get("local_only", False))


def current_x_models() -> tuple[dict[str, Any], ...]:
    """Public model identities for a persisted decision; never credentials."""
    session = _session.get()
    return tuple({**binding.identity, "roles": list(binding.roles)} for binding in session.bindings.values()
                 if binding.client is not None) if session else ()


def current_x_selection() -> dict[str, Any] | None:
    """The immutable selection used by this decision, for approval binding."""
    session = _session.get()
    return copy.deepcopy(validate_selection(session.selection)) if session else None


def current_x_usage() -> dict[str, Any]:
    session = _session.get()
    return {"calls": session.calls, "reserved_output_tokens": session.reserved_output_tokens,
            "input_bytes": session.input_bytes, "duration_ms": int((time.monotonic() - session.started) * 1000)} if session else {}


async def x_chat(role: str, messages: list[dict[str, Any]], *, max_tokens: int, **kwargs: Any) -> Any:
    """Budgeted call through the selected provider's existing chat implementation."""
    from kazma_core.x_api.ai_budget import CALL_SLOTS, XBudgetUnavailable, read_limits, reserve_daily

    session = _session.get()
    if session is None:
        raise RuntimeError("X chat requires a model scope")
    client = await get_x_client(role)
    if client is None:
        raise XModelUnavailableError("No X model is available for this role.")
    size = len(json.dumps(messages, ensure_ascii=False).encode("utf-8"))
    if type(max_tokens) is not int or max_tokens < 1:
        raise XBudgetUnavailable("X output token ceiling is invalid.")
    async with session.lock:
        if session.limits is None:
            session.limits = await asyncio.to_thread(read_limits)
        limits = session.limits
        remaining = limits["deadline_seconds"] - (time.monotonic() - session.started)
        if (session.calls >= limits["max_calls_per_decision"] or remaining <= 0
                or session.reserved_output_tokens + max_tokens > limits["max_output_tokens_per_decision"]
                or size > limits["max_input_bytes_per_call"]
                or session.input_bytes + size > limits["max_input_bytes_per_decision"]):
            raise XBudgetUnavailable("X decision budget reached. Split the request or review the held draft.")
        reserving = asyncio.create_task(asyncio.to_thread(reserve_daily, max_tokens, limits))
        try:
            await asyncio.shield(reserving)
        except asyncio.CancelledError:
            try:
                await reserving
            except Exception:
                pass
            raise
        session.calls += 1
        session.reserved_output_tokens += max_tokens
        session.input_bytes += size
    acquiring = asyncio.create_task(asyncio.to_thread(CALL_SLOTS.acquire, True, 5))
    try:
        acquired = await asyncio.shield(acquiring)
    except asyncio.CancelledError:
        if await acquiring:
            CALL_SLOTS.release()
        raise
    if not acquired:
        raise XBudgetUnavailable("X AI concurrency limit reached. Work is held for retry or review.")
    try:
        remaining = limits["deadline_seconds"] - (time.monotonic() - session.started)
        if remaining <= 0:
            raise XBudgetUnavailable("X decision deadline reached. Work is held.")
        return await asyncio.wait_for(client.chat(messages, max_tokens=max_tokens, **kwargs),
                                      timeout=min(remaining, limits["chat_timeout_seconds"]))
    finally:
        CALL_SLOTS.release()


def x_model_call(function: Callable[_P, Awaitable[Any]]) -> Callable[_P, Awaitable[Any]]:
    """Keep standalone classification/drafting/checking clients alive during use."""
    @wraps(function)
    async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> Any:
        async with x_model_scope():
            return await function(*args, **kwargs)
    return wrapped


def x_model_turn(function: Callable[_P, Awaitable[Any]]) -> Callable[_P, Awaitable[Any]]:
    """Bind drafting/checking to one snapshot and expose only model identity."""
    @wraps(function)
    async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> Any:
        async with x_model_scope() as session:
            result = await function(*args, **kwargs)
            identities = tuple({**binding.identity, "roles": list(binding.roles)} for binding in session.bindings.values() if binding.client is not None)
            if identities:
                changes = {"models": identities}
                if hasattr(result, "usage"):
                    changes["usage"] = current_x_usage()
                return replace(result, **changes)
            return result
    return wrapped
