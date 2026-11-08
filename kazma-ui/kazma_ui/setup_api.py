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


class _AddressBody(BaseModel):
    address: str = Field(min_length=1, max_length=300)


def _network_server(address: str, get=None, own_network=None) -> dict[str, Any]:
    """A model server at *address* on this computer's network: its
    OpenAI-compatible base address and how many models it lists, or a code
    the guide words (``bad_address``, ``not_own_network``, ``no_server``).

    Only an address on the owner's own network is asked
    (kazma_core.security.ssrf.resolves_to_own_network: never link-local or a
    cloud metadata address) -- the guide is no way to probe the internet. The
    address as typed, then with ``/v1`` (vLLM, llama.cpp, LocalAI and LM
    Studio serve there; Ollama too). Nothing is saved. *get* and
    *own_network* replace the HTTP call and the DNS check in tests.
    """
    import re
    from urllib.parse import urlsplit

    import httpx

    from kazma_core.security.ssrf import resolves_to_own_network

    raw = address.strip()
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parts = urlsplit(raw)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return {"ok": False, "error": "bad_address"}
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        return {"ok": False, "error": "bad_address"}
    base = raw.rstrip("/")
    if not (own_network or resolves_to_own_network)(base):
        return {"ok": False, "error": "not_own_network"}

    def fetch(url: str) -> Any:
        with httpx.Client(timeout=3.0, trust_env=False, follow_redirects=False) as client:
            return client.get(url)

    get = get or fetch
    candidates = [base] if re.search(r"/v\d", parts.path or "") else [base, f"{base}/v1"]
    for candidate in candidates:
        try:
            resp = get(f"{candidate}/models")
            if resp.status_code != 200:
                continue
            data = resp.json().get("data")
        except (httpx.HTTPError, OSError, ValueError, AttributeError):
            continue
        if isinstance(data, list):
            label = f"{host}:{port}" if port else host
            return {"ok": True, "base_url": candidate, "label": label,
                    "models": len([m for m in data if isinstance(m, dict)])}
    return {"ok": False, "error": "no_server"}


def _register_network_server(found: dict[str, Any], registry: Any | None = None) -> str:
    """Save the server :func:`_network_server` found as a provider of its
    own (``server-<host>-<port>``), switched on, with no key. Returns its name."""
    import re

    if registry is None:
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
    label = str(found["label"])
    name = "server-" + re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    registry.upsert_provider({
        "name": name,
        "display_name": label,
        "base_url": str(found["base_url"]),
        "api_key": "local",
        "enabled": True,
    })
    return name


class _ProviderBody(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    api_key: str = Field(default="", max_length=4096)


def _save_provider(body: _ProviderBody, registry: Any | None = None) -> dict[str, Any]:
    """Save a provider and its key, without making it the one Kazma uses: the
    guide lists its models and tests one before switching to it. A key left
    empty keeps the one stored; a cloud provider with none is refused."""
    from kazma_core.runtime.live_llm import url_is_local

    pid = body.provider.strip().lower()
    preset = PROVIDER_PRESETS.get(pid)
    if registry is None:
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
    stored = registry.get_provider(pid) if hasattr(registry, "get_provider") else None
    if preset is None and stored is None:
        return {"ok": False, "error": "unknown_provider"}
    api_key = body.api_key.strip()
    if api_key in {"***", "••••", "****"}:
        api_key = ""
    # Needs no key: Ollama and LM Studio, or a server the guide found on this
    # computer's network (stored, its address on the owner's network).
    local = pid in _LOCAL or url_is_local(str((stored or {}).get("base_url") or ""))
    if not local and not api_key and not _has_key((stored or {}).get("api_key")):
        return {"ok": False, "error": "api_key_required"}
    display = str((preset or {}).get("name") or (stored or {}).get("display_name") or pid)
    entry: dict[str, Any] = {"name": pid, "display_name": display, "enabled": True}
    if not str((stored or {}).get("base_url") or "").strip():  # an address set in Settings stays
        entry["base_url"] = str((preset or {}).get("base_url") or "")
    if api_key or local:
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

    @router.post("/api/setup/local/address")
    def setup_network_server(body: _AddressBody) -> dict[str, Any]:
        """A model server on another computer of this network: found and
        saved as a provider, or a code the page words. Admin only (it saves
        a provider), like the rest of the guide."""
        found = _network_server(body.address)
        if not found.get("ok"):
            return found
        return {"ok": True, "provider": _register_network_server(found), "label": found["label"],
                "models": found["models"]}

    @router.get("/api/setup/local")
    def setup_local() -> dict[str, Any]:
        """The guide's "a model on this computer": which servers run here."""
        return {"servers": _find_local_servers()}

    @router.post("/api/setup/models")
    async def setup_models(body: _CandidateBody) -> dict[str, Any]:
        """The provider's chat models, asked with the key as typed -- nothing
        saved (providers stage 1: the guide saved the key before listing, and
        a refused key stayed saved). ``{ok, models}``, or ``{ok: false, error,
        detail}`` with a code the page words."""
        from kazma_core.models.modality import chat_models
        from kazma_core.provider_check import list_models

        registry, entry = await _candidate(body)
        if registry is None:
            return entry
        found = await list_models(registry, entry)
        if not found.ok:
            return {"ok": False, "error": str(found.error or "list_failed"), "detail": str(found.detail or "")}
        return {"ok": True, "models": chat_models(found.models)}

    @router.post("/api/setup/bootstrap")
    def setup_bootstrap(body: BootstrapBody, request: Request) -> dict[str, Any]:
        del request  # auth is middleware; CSRF is middleware
        pid = body.provider.strip().lower()
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
    async def setup_check(body: _CandidateBody) -> dict[str, Any]:
        """One short message to the model the guide is about to choose,
        through the client chat would build for it (kazma_core.provider_check)
        -- with the key as typed, before anything is saved: the page saves
        and switches (``/api/setup/bootstrap``) only when this answered.
        ``{ok, ms}``, or ``{ok: false, error, detail}`` with a code."""
        from kazma_core.provider_check import check_model

        model = body.model.strip()
        if not model:
            return {"ok": False, "error": "no_model"}
        registry, entry = await _candidate(body)
        if registry is None:
            return entry
        check = await check_model(entry, model)
        if check["ok"]:
            return {"ok": True, "ms": check["ms"]}
        logger.info("[setup] model check failed: %s", check.get("error_code"))
        return {"ok": False, "error": str(check.get("error_code") or "failed"), "detail": str(check.get("error") or "")}

    return router


class _CandidateBody(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    api_key: str = Field(default="", max_length=4096)
    model: str = Field(default="", max_length=200)


async def _candidate(body: _CandidateBody) -> tuple[Any, dict[str, Any]]:
    """``(registry, candidate entry)``, or ``(None, refusal)`` with the
    refusal's code (kazma_core.provider_check.candidate_entry)."""
    import asyncio

    from kazma_core.model_registry import get_model_registry
    from kazma_core.provider_check import CandidateRefused, candidate_entry

    registry = await asyncio.to_thread(get_model_registry)
    try:
        entry = await asyncio.to_thread(
            candidate_entry, registry, body.provider.strip().lower(), api_key=body.api_key.strip())
    except CandidateRefused as exc:
        return None, {"ok": False, "error": exc.code}
    return registry, entry
