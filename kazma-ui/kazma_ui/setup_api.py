"""Hands 0.11 first-run wall — status + bootstrap. No secrets in responses."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from kazma_core.errors import safe_error
from kazma_core.providers import PROVIDER_PRESETS

logger = logging.getLogger(__name__)

__all__ = ["compute_setup_status", "create_setup_router"]

_LOCAL = frozenset({"ollama", "lm-studio", "lmstudio"})
#: Providers that need more than a key (an endpoint, AWS credentials): Settings.
_NOT_GUIDED = frozenset({"custom", "azure", "bedrock"})


def _has_key(value: Any) -> bool:
    # Masked "***" means a real key exists (get_active_profile never echoes it).
    return bool(str(value or "").strip())


def compute_setup_status(registry: Any | None = None) -> dict[str, Any]:
    """Booleans only — never echo api_key material."""
    if registry is None:
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()

    profile = registry.get_active_profile() if hasattr(registry, "get_active_profile") else {}
    provider = str(profile.get("provider") or "").strip().lower()
    has_model = bool(str(profile.get("model") or "").strip())
    has_provider = _has_key(profile.get("api_key")) or provider in _LOCAL

    # Another provider's key counts (Kazma falls back to a provider that has
    # one, and says so). A local server counts only as the provider in use:
    # every install lists Ollama and LM Studio, and counting them made every
    # fresh install "ready", so the chat page never showed the way to the
    # setup guide (the clean Windows VM, 2026-11-07).
    if not has_provider and hasattr(registry, "list_providers"):
        for entry in registry.list_providers() or []:
            if _has_key(entry.get("api_key")):
                has_provider = True
                break

    vault_configured = False
    try:
        from kazma_core.security.vault import get_vault

        vault_configured = get_vault() is not None
    except Exception:
        vault_configured = False

    from kazma_core.providers import default_model_for

    presets = [
        {
            "id": pid,
            "name": str(meta.get("name") or pid),
            "local": pid in _LOCAL,
            # Where the provider hands out keys (the preset's own page).
            "key_url": str(meta.get("docs") or "") if pid not in _LOCAL else "",
            "default_model": default_model_for(pid) or "",
            # A key is all the guide asks for: an endpoint of one's own (custom,
            # Azure) or AWS credentials (Bedrock) are set up in Settings.
            "guided": pid not in _LOCAL and pid not in _NOT_GUIDED,
        }
        for pid, meta in PROVIDER_PRESETS.items()
    ]
    return {
        "ready": bool(has_provider and has_model),
        "has_provider": bool(has_provider),
        "has_model": has_model,
        # Names, never keys: the guide says what Kazma uses now.
        "active_provider": provider,
        "active_model": str(profile.get("model") or "").strip(),
        "vault_configured": vault_configured,
        "presets": presets,
    }


#: The local model servers the guide looks for, and where each is downloaded.
_LOCAL_SERVERS = (
    ("ollama", "Ollama", "https://ollama.com/download"),
    ("lm-studio", "LM Studio", "https://lmstudio.ai/download"),
)


def _find_local_servers(get=None) -> list[dict[str, Any]]:
    """Ollama and LM Studio on this machine: running or not, and how many
    models each offers (their OpenAI-compatible ``/models``). Nothing is
    saved. *get* replaces the HTTP call in tests."""
    import httpx

    def fetch(url: str) -> Any:
        # This machine's own servers: no proxy from the environment, a short wait.
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            return client.get(url)

    get = get or fetch
    found = []
    for pid, name, download in _LOCAL_SERVERS:
        base = str(PROVIDER_PRESETS.get(pid, {}).get("base_url") or "").rstrip("/")
        entry: dict[str, Any] = {"id": pid, "name": name, "download": download,
                                 "online": False, "models": 0}
        try:
            resp = get(f"{base}/models")
            if resp.status_code == 200:
                data = resp.json().get("data") or []
                entry.update(online=True, models=len([m for m in data if isinstance(m, dict)]))
            else:
                entry["error"] = f"http_{resp.status_code}"
        except (httpx.HTTPError, OSError, ValueError) as exc:  # not running: what the guide says
            entry["error"] = type(exc).__name__
        found.append(entry)
    return found


class _ProviderBody(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    api_key: str = Field(default="", max_length=4096)


def _save_provider(body: _ProviderBody, registry: Any | None = None) -> dict[str, Any]:
    """Save a provider and its key, without making it the one Kazma uses: the
    guide lists its models and tests one before switching to it. A key left
    empty keeps the one stored; a cloud provider with none is refused."""
    pid = body.provider.strip().lower()
    preset = PROVIDER_PRESETS.get(pid)
    if preset is None:
        return {"ok": False, "error": "unknown_provider"}
    if registry is None:
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
    api_key = body.api_key.strip()
    if api_key in {"***", "••••", "****"}:
        api_key = ""
    stored = registry.get_provider(pid) if hasattr(registry, "get_provider") else None
    if pid not in _LOCAL and not api_key and not _has_key((stored or {}).get("api_key")):
        return {"ok": False, "error": "api_key_required"}
    entry: dict[str, Any] = {"name": pid, "display_name": str(preset.get("name") or pid), "enabled": True}
    if not str((stored or {}).get("base_url") or "").strip():  # an address set in Settings stays
        entry["base_url"] = str(preset.get("base_url") or "")
    if api_key or pid in _LOCAL:
        entry["api_key"] = api_key or "local"
    result = registry.upsert_provider(entry)
    if isinstance(result, dict) and result.get("error"):
        return {"ok": False, "error": "save_failed"}
    return {"ok": True, "provider": pid}


class BootstrapBody(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    api_key: str = ""
    model: str = Field(min_length=1, max_length=200)


def create_setup_router() -> APIRouter:
    router = APIRouter(tags=["setup"])

    @router.get("/api/setup/status")
    def setup_status() -> dict[str, Any]:
        return compute_setup_status()

    @router.get("/api/setup/local")
    def setup_local() -> dict[str, Any]:
        """The guide's "a model on this computer": which servers run here."""
        return {"servers": _find_local_servers()}

    @router.post("/api/setup/provider")
    def setup_provider(body: _ProviderBody) -> dict[str, Any]:
        """Save a provider (and its key) for the guide to list and test its models.
        A store that cannot write answers through the app's own error handler."""
        return _save_provider(body)

    @router.post("/api/setup/bootstrap")
    def setup_bootstrap(body: BootstrapBody, request: Request) -> dict[str, Any]:
        del request  # auth is middleware; CSRF is middleware
        pid = body.provider.strip().lower()
        preset = PROVIDER_PRESETS.get(pid)
        if preset is None:
            return {"error": "unknown_provider", "ready": False}

        api_key = body.api_key.strip()
        if api_key in {"***", "••••", "****"}:
            api_key = ""

        try:
            from kazma_core.model_registry import get_model_registry
            from kazma_core.runtime.model_switch import (
                switch_active_model,
                switch_active_provider,
            )

            registry = get_model_registry()
            # The provider and its key as the guide saves them: a key left
            # empty keeps the stored one (the guide run again to change the
            # model), an address set in Settings stays.
            saved = _save_provider(_ProviderBody(provider=pid, api_key=api_key), registry=registry)
            if not saved.get("ok"):
                return {"error": str(saved.get("error") or "save_failed"), "ready": False}
            if body.model.strip():
                result = registry.upsert_provider({"name": pid, "models": [body.model.strip()]})
                if isinstance(result, dict) and result.get("error"):
                    return {"error": str(result["error"]), "ready": False}

            switched = switch_active_provider(
                pid,
                model=body.model.strip(),
                api_key=api_key,
                registry=registry,
            )
            if not switched.ok and body.model.strip():
                switched = switch_active_model(body.model.strip(), registry=registry)
            if not switched.ok:
                return {
                    "error": switched.error or "switch_failed",
                    "ready": False,
                }
        except Exception as exc:
            logger.warning("[setup] bootstrap failed: %s", exc)
            return {"error": str(safe_error(exc)), "ready": False}

        # compute_setup_status returns booleans and presets, never key
        # material; the filter for an "api_key" it cannot hold is gone
        # (audit 2026-10-26 AUD-082). tests/test_setup_api.py holds it.
        return compute_setup_status()

    @router.post("/api/setup/check")
    async def setup_check(body: _CheckBody) -> dict[str, Any]:
        """One short message to the model the guide just chose, through the
        client Kazma's chat uses for it (each provider's own API style)."""
        return await _check_model(body.provider.strip().lower(), body.model.strip())

    return router


class _CheckBody(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=200)


#: How long the guide's check waits for the model's answer.
_CHECK_TIMEOUT_S = 60.0


async def _check_model(provider: str, model: str, registry: Any | None = None) -> dict[str, Any]:
    """Send "Reply with the word OK." to *model* of *provider*: ``{ok, ms}``,
    or ``{ok: false, error}`` in words the page shows as they are."""
    import asyncio
    import time

    import httpx

    from kazma_core.llm_provider import LLMError
    from kazma_core.retry import friendly_llm_error

    if registry is None:
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
    started = time.monotonic()
    try:
        client = await asyncio.to_thread(registry.get_client_by_provider, provider, model)
        if client is None:
            return {"ok": False, "error": "not_configured"}
        kwargs: dict[str, Any] = {"max_tokens": 32, "model": model}
        can_turn_off = getattr(client, "can_turn_off_reasoning", None)
        if callable(can_turn_off) and can_turn_off():
            kwargs["reasoning"] = "off"  # a thinking model spends a small limit thinking
        await asyncio.wait_for(
            client.chat([{"role": "user", "content": "Reply with the word OK."}], **kwargs),
            timeout=_CHECK_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return {"ok": False, "error": "timeout"}  # worded by the page, in its language
    except (LLMError, httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:  # the check's answer
        logger.info("[setup] model check failed: %s", type(exc).__name__, exc_info=True)
        return {"ok": False, "error": friendly_llm_error(exc, model=model)}
    return {"ok": True, "ms": round((time.monotonic() - started) * 1000)}
