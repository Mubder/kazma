"""Unified Providers & Connectors management router.

Provides a single hub for LLM providers, saved model profiles, and platform
connector tokens (Telegram, Discord, Slack, etc.) with consistent masking,
CRUD, and test-before-save semantics.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from kazma_core.config_store import ConfigStore, is_vault_ref
from kazma_core.env_files import env_credential
from kazma_core.model_registry import get_model_registry
from kazma_core.security.url_credentials import (
    mask_url_credentials,
    restore_masked_url,
    url_password_is_masked,
)

from kazma_ui.models import (
    ConnectorTestResponse,
    ConnectorUpdateRequest,
    ModelProfileUpdateRequest,
    ProviderTestResponse,
    ProviderToggleRequest,
    ProviderUpdateRequest,
)
from kazma_ui.json_body import InvalidJsonBody, invalid_json_response, read_json

logger = logging.getLogger(__name__)

__all__ = ["create_providers_router"]

# Connectors whose primary secret is stored under ``connectors.{name}.token``.
_CONNECTOR_PLATFORMS = ("telegram", "discord", "slack", "email", "webhook")

# Keys that are considered secrets and must be masked before leaving the backend.
_SECRET_KEY_HINTS = ("token", "secret", "password", "key", "api_key")


def _normalize_telegram_bot_token(raw: str) -> str:
    """Light cleanup only — never invent/reject token shapes.

    Strips surrounding whitespace/quotes. If the user pasted a full API path
    fragment like ``bot123:AA…`` (docs style), drop the leading ``bot`` so
    ``/bot{token}/getMe`` is not doubled. Do **not** regex-reject tokens:
    BotFather formats vary and vault/env values must pass through as-is.
    """
    token = (raw or "").strip().strip("\"'")
    # Only strip a literal docs-style prefix: bot + digits…
    if len(token) > 4 and token[:3].lower() == "bot" and token[3].isdigit():
        token = token[3:]
    return token


def _mask_secret(value: str) -> str:
    """Mask a secret for safe UI display.

    Returns ``****XXXX`` where ``XXXX`` is the last 4 characters, or ``***``
    if the value is shorter than 4 characters.
    """
    if not value:
        return ""
    if len(value) < 4:
        return "***"
    return f"****{value[-4:]}"


def _is_masked_placeholder(value: str) -> bool:
    """Return True if *value* is a masked placeholder from the UI."""
    if not value:
        return False
    # Common placeholders: "***", "****", "sk-****1234", "xoxb-****abcd"
    if value == "***" or value == "****" or "****" in value:
        return True
    return False


def _sanitize_api_key(raw: str) -> str:
    """Strip BOM, whitespace, and wrapping quotes from a pasted API key."""
    key = (raw or "").replace("\ufeff", "").strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {'"', "'"}:
        key = key[1:-1].strip()
    return key


def _is_secret_key(key: str) -> bool:
    """Return True if a config key looks like it holds a secret."""
    lower = key.lower()
    return any(hint in lower for hint in _SECRET_KEY_HINTS)


from kazma_core.providers import capabilities as _provider_capabilities


def _activate_tested_provider(registry: Any, name: str) -> None:
    """Point chat at *name* when the active profile has no API key.

    Test-connection success must not leave chat on the empty OpenAI default.
    Never raises — a failed switch must not fail the Test response.
    """
    try:
        from kazma_core.runtime.model_switch import maybe_activate_provider_for_chat

        maybe_activate_provider_for_chat(name, registry=registry)
    except Exception:
        logger.debug("[providers] chat activate after Test failed", exc_info=True)


def _can_be_called(registry: Any, name: str) -> bool:
    """A saved provider chat can call: on this computer or its network, or
    with what its sign-in needs -- a key Kazma would send (the vault,
    ``<PROVIDER>_API_KEY``), Bedrock's AWS keys, a Vertex project
    (``provider_check.credentials_problem``, one rule with the check). A
    cloud provider switched on with no key was a provider that failed every
    message and that Kazma might fall back to (providers stage 1)."""
    from kazma_core.provider_check import credentials_problem

    entry = registry.get_provider(name) or {"name": name}
    return credentials_problem(registry, entry) is None


def _same_address(providers: list[dict[str, Any]], name: str, base_url: str) -> list[str]:
    """The other providers at *base_url* (normalized): a second row for one
    service (Z.AI and zai on the live install) -- said, not refused, since a
    second account of a vendor is saved this way until providers have
    several keys."""
    from kazma_core.url_utils import normalize_provider_url

    def address(url: str) -> str:
        try:
            return normalize_provider_url(url).rstrip("/").lower()
        except ValueError:  # a port out of range: compared as typed
            return url.strip().rstrip("/").lower()

    if not base_url:
        return []
    here = address(base_url)
    return sorted(
        str(p.get("name") or "") for p in providers
        if str(p.get("name") or "") != name and p.get("base_url")
        and address(str(p["base_url"])) == here
    )


def _mask_provider_entry(provider: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a provider entry with every secret masked: the API
    key, any password in its base URL (the save restores an unchanged masked
    URL), each header's value, the AWS secret and token, each extra key
    (providers stages 2 and 4). The save keeps a masked value as stored."""
    safe = dict(provider)
    if safe.get("api_key"):
        safe["api_key"] = _mask_secret(str(safe["api_key"]))
    if safe.get("base_url"):
        safe["base_url"] = mask_url_credentials(safe["base_url"])
    headers = safe.pop("secret_headers", None)
    if isinstance(headers, dict):
        safe["headers"] = {k: _mask_secret(str(v)) for k, v in headers.items()}
    for field in ("aws_secret_access_key", "aws_session_token"):
        if safe.get(field):
            safe[field] = _mask_secret(str(safe[field]))
    if isinstance(safe.get("api_keys"), list):
        safe["api_keys"] = [
            {"id": str(k.get("id") or ""), "label": str(k.get("label") or ""),
             "api_key": _mask_secret(str(k.get("api_key") or ""))}
            for k in safe["api_keys"] if isinstance(k, dict)
        ]
    return safe


def _provider_fields(data: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    """A save's fields as a provider row holds them: ``headers`` stored as
    ``secret_headers`` (every value vaulted), masked secrets kept as stored
    (``provider_check.merge_typed_secrets``), the preset named, the kind
    checked. Raises 400 for a header that could split a request line."""
    from kazma_core.provider_check import merge_typed_secrets
    from kazma_core.providers import PROVIDER_PRESETS

    headers = data.pop("headers", None) or {}
    for name, value in headers.items():
        if any(c in str(name) for c in "\r\n:") or any(c in str(value) for c in "\r\n"):
            raise HTTPException(status_code=400, detail="headers: a name holds a colon or line break, or a value a line break")
    data["secret_headers"] = headers
    if data.get("api_keys") is None:
        data.pop("api_keys", None)
    data = merge_typed_secrets(data, existing)
    kind = str(data.get("kind") or "").strip().lower()
    if kind and kind not in ("chat", "speech"):
        raise HTTPException(status_code=400, detail="kind: chat or speech")
    preset = str(data.get("preset") or "").strip().lower()
    if not preset and data["name"].lower() in PROVIDER_PRESETS:
        preset = data["name"].lower()
    if preset and preset not in PROVIDER_PRESETS:
        raise HTTPException(status_code=400, detail=f"preset: no such preset {preset!r}")
    data["preset"] = preset
    return data


def _restore_url(field: str, posted: Any, stored: Any) -> Any:
    """*posted* with a masked URL password restored from *stored*, or a 400.

    The display masks a password inside a URL. Posted back unchanged, the
    stored URL is kept; posted with the stars but another host, user or path,
    it is refused -- the stored password is never carried to a new server,
    and stars are never stored as a password.
    """
    value = restore_masked_url(posted, stored)
    if url_password_is_masked(value):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{field}: the password in this URL is masked. Enter it again -- "
                "the saved one is kept only when the URL itself is unchanged."
            ),
        )
    return value


def _mask_connector_entry(name: str, config: dict[str, Any]) -> dict[str, Any]:
    """Build a connector entry with all secret fields masked."""
    token = str(config.get("token", ""))
    extras: dict[str, str] = {}
    endpoint = ""
    for key, value in config.items():
        if key == "token":
            continue
        if key == "enabled":
            continue
        str_value = str(value)
        if _is_secret_key(key):
            str_value = _mask_secret(str_value)
        else:
            # A webhook or endpoint URL can carry a password of its own.
            str_value = mask_url_credentials(str_value)
        if key in ("incoming_url", "outgoing_url", "webhook_url", "base_url"):
            endpoint = str_value
        extras[key] = str_value

    return {
        "name": name,
        "platform": name,
        "type": "connector",
        "enabled": bool(config.get("enabled", True)),
        "token": _mask_secret(token),
        "endpoint": endpoint,
        "extras": extras,
        "status": "configured" if token else "missing_token",
    }


def _load_connector_config(config_store: ConfigStore, name: str) -> dict[str, Any]:
    """Load all config keys under ``connectors.{name}.*``.

    Uses ``config_store.get()`` (vault-aware) rather than raw ``get_all()``
    values. Sensitive tokens are stored as ``vault://…`` pointers in the DB;
    ``get_all()`` returns those pointers unresolved, which broke connector
    Test (Telegram saw ``/botvault://…/getMe`` → HTTP 404, or later a false
    "format looks wrong" after a regex gate).
    """
    prefix = f"connectors.{name}."
    all_settings = config_store.get_all()
    config: dict[str, Any] = {}
    # Flatten DB settings grouped by category
    for category_values in all_settings.values():
        for key, value in category_values.items():
            if not key.startswith(prefix):
                continue
            # Always re-fetch via get() so vault secrets decrypt.
            resolved = config_store.get(key, value)
            if resolved is None or (isinstance(resolved, str) and is_vault_ref(resolved)):
                # Vault disabled / decrypt failed — treat as missing secret.
                if key.endswith(".token") or key.rsplit(".", 1)[-1] in _SECRET_KEY_HINTS:
                    resolved = ""
            config[key[len(prefix):]] = resolved
    return config


def _discord_test_settings(config_store: ConfigStore) -> dict[str, Any]:
    """What the Discord Test checks against (store reads: run off the loop):
    the delivery channel, the Guild ID and the allowed users, as saved."""
    from kazma_gateway.allowlists import split_ids

    return {
        "channel_id": str(config_store.get("connectors.discord.swarm_channel_id", "") or "").strip(),
        "guild_ids": split_ids(config_store.get("connectors.discord.guild_id", "")),
        "allowed_users": split_ids(config_store.get("connectors.discord.allowed_users", "")),
    }


def _telegram_test_settings(config_store: ConfigStore) -> dict[str, Any]:
    """What the Telegram Test checks against (store reads: off the loop):
    the delivery chat, the group route and the allowed users, as saved."""
    from kazma_gateway.allowlists import split_ids

    group = config_store.get("swarm.output_target", None)
    return {
        "chat_id": str(config_store.get("connectors.telegram.swarm_chat_id", "") or "").strip(),
        "group": group if isinstance(group, dict) else None,
        "allowed_users": split_ids(config_store.get("connectors.telegram.allowed_users", "")),
    }


def _slack_test_settings(config_store: ConfigStore) -> dict[str, Any]:
    """What the Slack Test checks against (store reads: off the loop). The app
    token resolves like the gateway's: a stored mask or nothing falls back
    to SLACK_APP_TOKEN."""
    from kazma_gateway.allowlists import split_ids

    app_token = str(config_store.get("connectors.slack.app_token", "") or "").strip()
    if not app_token or _is_masked_placeholder(app_token) or is_vault_ref(app_token):
        app_token = env_credential("SLACK_APP_TOKEN")
    return {
        "app_token": app_token,
        "channel_id": str(config_store.get("connectors.slack.swarm_channel_id", "") or "").strip(),
        "allowed_users": split_ids(config_store.get("connectors.slack.allowed_users", "")),
    }


def _live_adapter_diagnostics(platform: str) -> dict[str, Any] | None:
    """The running adapter's own record (its ``diagnostics()``), or None when
    the gateway has no such adapter running. The Test route's handler logs a
    failure here with the rest of the Test's."""
    from kazma_core.service_container import get_container
    from kazma_gateway.gateway import GatewayManager

    container = get_container()
    if not container.has(GatewayManager):
        return None
    for adapter in getattr(container.get(GatewayManager), "adapters", None) or []:
        if getattr(adapter, "name", "") == platform and hasattr(adapter, "diagnostics"):
            return adapter.diagnostics()
    return None


async def _adapter_from_saved_settings(platform: str) -> None:
    """Before a chat app's Test: the running adapter built from the saved
    settings, and its first connection attempt made.

    The card saves, then tests. A token saved while the adapter ran reached
    no running adapter until a separate Refresh, and the Test reported the
    revoked token's ``invalid_auth`` for a token that worked (2026-10-01).
    """
    from kazma_core.service_container import get_container
    from kazma_gateway.chat_adapters import ChatAdapters

    container = get_container()
    if not container.has(ChatAdapters):
        return
    adapters = container.get(ChatAdapters)
    await adapters.apply([platform])
    await adapters.first_outcome(platform)


def create_providers_router(config_store: ConfigStore) -> APIRouter:
    """Create the unified providers & connectors router."""
    router = APIRouter(tags=["providers"])

    # ── LLM Providers ──────────────────────────────────────────────────

    @router.get("/api/providers")
    def list_providers() -> list[dict[str, Any]]:
        """List all LLM providers with masked API keys and discovered models."""
        registry = get_model_registry()
        providers = []
        all_rows = registry.list_providers()
        for p in all_rows:
            entry = _mask_provider_entry(p)
            name = p.get("name", "")
            from kazma_core.models.modality import chat_models, speech_models

            discovered = registry.get_discovered_models(name)
            selected = registry.get_selected_models(name)
            chat_discovered = chat_models(discovered)
            speech_hidden = speech_models(discovered)
            if discovered:
                manual = set(chat_models(entry.get("models", [])))
                entry["discovered_models"] = chat_discovered
                entry["all_models"] = sorted(manual | set(chat_discovered))
            else:
                entry["discovered_models"] = []
                entry["all_models"] = chat_models(entry.get("models", []))
            entry["selected_models"] = chat_models(selected)
            entry["visible_models"] = registry.get_visible_models(name)
            if speech_hidden:
                entry["speech_models_omitted"] = len(speech_hidden)
            # What the provider layer *declares* about this provider, so the
            # page can show it instead of the operator finding out from a 400.
            # `None` inside `supports` means NOT VERIFIED and the UI renders it
            # as such — it is not a quiet "no".
            entry["capabilities"] = _provider_capabilities(name)
            # On this computer or its network: needs no key (url_is_local).
            # The pages decided it by name, so a keyless vLLM at 192.168.1.20
            # was hidden from the chat's model picker (providers stage 1).
            from kazma_core.runtime.live_llm import url_is_local

            from kazma_core.models.modality import row_is_speech
            from kazma_core.providers import PROVIDER_PRESETS, is_local_preset

            preset_id = str(p.get("preset") or "").strip().lower() or name.lower()
            entry["local"] = is_local_preset(preset_id) or url_is_local(str(p.get("base_url") or ""))
            entry["same_address_as"] = _same_address(all_rows, name, str(p.get("base_url") or ""))
            # Providers stage 2: what it serves (speech is kept out of every
            # chat model list) and which form the dialog shows for it.
            entry["kind"] = "speech" if row_is_speech(p, discovered) else "chat"
            entry["form"] = str((PROVIDER_PRESETS.get(preset_id) or {}).get("form") or "key")
            providers.append(entry)
        return providers

    @router.get("/api/models/options")
    def model_choice_options() -> dict[str, Any]:
        """Every model a person can choose, named ``provider:model``, with
        what is known about it, and the default (kazma_core.models.options,
        providers stage 3): the one list the chat's picker, the sidebar's,
        the chat apps' /model menu and the terminal app read."""
        from kazma_core.models.options import model_options

        return model_options(get_model_registry())

    @router.get("/api/providers/catalog")
    def provider_catalog() -> list[dict[str, Any]]:
        """Every provider Kazma knows (kazma_core.providers.catalog): the one
        list the Settings dialog and the setup guide read (providers stage
        2). The page kept ten presets of its own, already different from the
        server's twenty."""
        from kazma_core.providers import catalog

        return catalog()

    @router.post("/api/providers")
    def upsert_provider(req: ProviderUpdateRequest) -> dict[str, Any]:
        """Add or update an LLM provider.

        If the request contains a masked API key placeholder, the existing key
        is preserved instead of overwriting it with the placeholder.
        """
        registry = get_model_registry()
        data = req.model_dump()
        data["api_key"] = _sanitize_api_key(str(data.get("api_key") or ""))

        existing = registry.get_provider(data["name"]) or {}
        if _is_masked_placeholder(data.get("api_key", "")) and existing.get("api_key"):
            data["api_key"] = existing["api_key"]
        data["base_url"] = _restore_url("base_url", data.get("base_url"), existing.get("base_url"))
        data = _provider_fields(data, existing)

        result = registry.upsert_provider(data)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        answer = _mask_provider_entry(result)
        # Saved, but not switched on with no key: it would fail every message.
        if result.get("enabled", True) and not _can_be_called(registry, data["name"]):
            registry.toggle_provider(data["name"], False)
            answer["enabled"] = False
            answer["switched_off"] = "no_key"
        twins = _same_address(registry.list_providers(), data["name"], str(result.get("base_url") or ""))
        if twins:
            answer["same_address_as"] = twins
        return answer

    @router.delete("/api/providers/{name}")
    def delete_provider(name: str) -> dict[str, str]:
        """Delete an LLM provider by name."""
        registry = get_model_registry()
        registry.delete_provider(name)
        return {"status": "ok"}

    @router.post("/api/providers/{name}/toggle")
    def toggle_provider(name: str, req: ProviderToggleRequest) -> dict[str, str]:
        """Enable or disable an LLM provider. A cloud provider with no key is
        not switched on (409 ``no_key``): it would fail every message."""
        registry = get_model_registry()
        if req.enabled and not _can_be_called(registry, name):
            raise HTTPException(status_code=409, detail={
                "code": "no_key", "message": "This provider has no API key: add its key before switching it on."})
        registry.toggle_provider(name, req.enabled)
        return {"status": "ok"}

    async def _model_to_check(
        registry: Any, entry: dict[str, Any], typed_model: str,
    ) -> tuple[str, list[str] | None, dict[str, Any] | None]:
        """``(model, models listed or None, refusal or None)``: the model typed,
        else the provider's own (``probe_model_for``), else the first chat
        model its list offers -- listed with what was typed, not saved."""
        from kazma_core.models.modality import chat_models, speech_models
        from kazma_core.provider_check import list_models
        from kazma_core.providers import default_model_for

        model = str(typed_model or "").strip()
        if model:
            return model, None, None
        name = str(entry["name"])
        if await asyncio.to_thread(registry.get_provider, name) is not None:
            model = await asyncio.to_thread(registry.probe_model_for, name)
            if model:
                return model, None, None
        found = await list_models(registry, entry)
        chats = chat_models(found.models) if found.ok else []
        if chats:
            preferred = default_model_for(name)
            return (preferred if preferred in chats else chats[0]), chats, None
        if found.ok and speech_models(found.models):
            return "", [], {"error_code": "speech_only", "reachable": True, "error": (
                "Reachable, but the listed models are speech (Whisper/TTS), not chat. "
                "Configure speech under Settings -> Voice.")}
        if not found.ok:
            return "", None, {"error_code": "list_failed", "reachable": found.error != "unreachable",
                              "error": str(found.detail or found.error)}
        return "", [], {"error_code": "no_model", "reachable": True,
                        "error": "The provider lists no model to try. Type one and test again."}

    def _check_answer(check: dict[str, Any], models: list[str] | None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "success": bool(check["ok"]), "chat_ok": bool(check["ok"]),
            "reachable": bool(check.get("reachable", True)), "chat_model": check.get("model"),
        }
        if check["ok"]:
            out["chat_ms"] = out["latency_ms"] = int(check.get("ms") or 0)
        else:
            out["error_code"] = check.get("error_code")
            out["error"] = check.get("error")
        if models is not None:
            out["models"] = models
        return out

    async def _typed_fields(request: Request) -> dict[str, Any] | JSONResponse:
        body: Any = {}
        ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype == "application/json":
            try:
                body = await read_json(request, allow_empty=True)
            except InvalidJsonBody:
                return invalid_json_response()
        return body if isinstance(body, dict) else {}

    @router.post("/api/providers/check", response_model=ProviderTestResponse)
    async def check_typed_provider(request: Request) -> dict[str, Any] | JSONResponse:
        """Check a provider as the Settings dialog has it, WITHOUT saving it
        (providers stage 1, kazma_core.provider_check): ``{name, base_url,
        api_key, model}`` -- an empty or masked key means the stored one. One
        short message through the client chat would build. The dialog saves
        only after this answered: it wrote the provider, typed key and all,
        before testing, and a refused key stayed saved."""
        from kazma_core.provider_check import CandidateRefused, candidate_entry, check_model

        fields = await _typed_fields(request)
        if isinstance(fields, JSONResponse):
            return fields
        registry = get_model_registry()
        typed = {k: v for k, v in fields.items() if k not in ("name", "base_url", "api_key", "model")}
        if isinstance(typed.get("headers"), dict):
            typed["secret_headers"] = typed.pop("headers")
        try:
            entry = await asyncio.to_thread(
                candidate_entry, registry, str(fields.get("name") or ""),
                base_url=str(fields.get("base_url") or ""),
                api_key=_sanitize_api_key(str(fields.get("api_key") or "")),
                typed=typed,
            )
        except CandidateRefused as exc:
            return {"success": False, "error_code": exc.code, "error": exc.message}
        model, models, refusal = await _model_to_check(registry, entry, str(fields.get("model") or ""))
        if refusal is not None:
            return {"success": False, "chat_ok": False, **refusal, "models": models}
        return _check_answer(await check_model(entry, model), models)

    @router.post("/api/providers/{name}/test", response_model=ProviderTestResponse)
    async def test_provider(name: str, request: Request) -> dict[str, Any] | JSONResponse:
        """Test a SAVED provider through the client chat uses for it, and
        record its health (providers stage 1, kazma_core.provider_check).

        Optional JSON body ``{"api_key": "..."}`` tests the typed key instead
        of the stored one (kept only when the test answered). It probed
        every provider with OpenAI's request -- ``Authorization: Bearer`` to
        ``/models`` and ``/chat/completions`` -- so Anthropic, Azure and
        Bedrock failed Test while chat worked.
        """
        from kazma_core.provider_check import CandidateRefused, candidate_entry, check_model

        registry = get_model_registry()
        if await asyncio.to_thread(registry.get_provider, name) is None:
            return {"success": False, "error_code": "unknown_provider", "error": f"Provider '{name}' not found"}
        fields = await _typed_fields(request)
        if isinstance(fields, JSONResponse):
            return fields
        typed_key = _sanitize_api_key(str(fields.get("api_key") or ""))
        if typed_key and _is_masked_placeholder(typed_key):
            typed_key = ""
        try:
            entry = await asyncio.to_thread(candidate_entry, registry, name, api_key=typed_key)
        except CandidateRefused as exc:
            return {"success": False, "error_code": exc.code, "error": exc.message}
        model, models, refusal = await _model_to_check(registry, entry, "")
        if refusal is not None:
            if refusal.get("reachable") is False:
                await asyncio.to_thread(registry.set_provider_health, name, "down")
            return {"success": False, "chat_ok": False, **refusal, "models": models}
        check = await check_model(entry, model)
        # Its own health values: "chat_failing" (it answered, not as a model)
        # is not "down" (it could not be reached), and the page keeps both.
        health = "healthy" if check["ok"] else ("chat_failing" if check.get("reachable") else "down")
        await asyncio.to_thread(registry.set_provider_health, name, health)
        if check["ok"]:
            if typed_key:
                await asyncio.to_thread(registry.upsert_provider, {"name": name, "api_key": typed_key})
            await asyncio.to_thread(_activate_tested_provider, registry, name)
        return _check_answer(check, models)

    @router.post("/api/providers/{name}/discover", response_model=None)
    async def discover_provider_models(name: str) -> dict[str, Any] | JSONResponse:
        """Discover models available for a provider and persist them.

        A provider that could not be asked answers 502 ``{ok: false, error,
        detail, cached}`` (FA-107): it answered with the list an earlier
        discovery had found, and the page said "Found 2 models".
        """
        from kazma_core.models.modality import chat_models, speech_models

        registry = get_model_registry()
        found = await registry.discover(name)
        if not found.ok:
            return JSONResponse(status_code=502, content={
                "ok": False, "name": name, "error": found.error, "detail": found.detail,
                "cached": chat_models(found.cached), "count": 0,
            })
        await asyncio.to_thread(registry.serialize)  # persist discovered models to ConfigStore
        chat = chat_models(found.models)
        omitted = speech_models(found.models)
        return {
            "ok": True,
            "name": name,
            "models": chat,
            "count": len(chat),
            "speech_omitted": len(omitted),
            "source": found.source,
        }

    @router.delete("/api/providers/{name}/models/{model_id:path}")
    def delete_provider_model(name: str, model_id: str) -> dict[str, Any]:
        """Delete a specific model from a provider's discovered, selected, and manual lists."""
        registry = get_model_registry()
        deleted = registry.remove_provider_model(name, model_id)
        return {
            "status": "ok" if deleted else "not_found",
            "name": name,
            "model": model_id,
            "discovered_models": registry.get_discovered_models(name),
            "selected_models": registry.get_selected_models(name),
            "visible_models": registry.get_visible_models(name),
        }

    @router.post("/api/providers/{name}/clear-discovered")
    def clear_discovered_provider_models(name: str) -> dict[str, Any]:
        """Clear cached discovered models and selection for a provider."""
        registry = get_model_registry()
        registry.clear_discovered_models(name)
        return {
            "status": "ok",
            "name": name,
            "discovered_models": registry.get_discovered_models(name),
            "selected_models": registry.get_selected_models(name),
            "visible_models": registry.get_visible_models(name),
        }

    @router.post("/api/providers/{name}/select-models")
    def set_selected_models(name: str, req: dict[str, Any]) -> dict[str, Any]:
        """Set which discovered models should appear in dropdowns.

        Body: ``{"models": ["model-a", "model-b"]}``
        """
        registry = get_model_registry()
        models = req.get("models", []) if isinstance(req, dict) else []
        if not isinstance(models, list):
            models = []
        registry.set_selected_models(name, [str(m) for m in models])
        return {"status": "ok", "selected_models": registry.get_selected_models(name)}

    # ── Saved Model Profiles ────────────────────────────────────────────

    @router.get("/api/models/profiles")
    def list_model_profiles() -> list[dict[str, Any]]:
        """List all saved model profiles with masked API keys."""
        registry = get_model_registry()
        profiles: list[dict[str, Any]] = registry.list_model_profiles(mask_api_key=True)
        return profiles

    @router.post("/api/models/profiles")
    def save_model_profile(req: ModelProfileUpdateRequest) -> dict[str, Any]:
        """Save a named model profile.

        Stores under ``models.saved.{name}`` with the provider, base URL, API key,
        and model name.
        """
        registry = get_model_registry()
        data = req.model_dump()
        profile_name = data.get("name", "").strip()
        if not profile_name:
            raise HTTPException(status_code=400, detail="Profile name is required")

        # A masked key or URL password posted back is restored (or refused)
        # by ``registry.save_model_profile`` itself: one rule for this route
        # and POST /api/models/saved (AUD-105).
        profile: dict[str, Any] = {
            "provider": data.get("provider", "custom"),
            "base_url": data.get("base_url", ""),
            "api_key": data.get("api_key", ""),
            "model": data.get("model", ""),
        }
        result: dict[str, Any] = registry.save_model_profile(profile_name, profile)
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @router.delete("/api/models/profiles/{name}")
    def delete_model_profile(name: str) -> dict[str, str]:
        """Delete a saved model profile by name."""
        registry = get_model_registry()
        if not registry.delete_model_profile(name):
            raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
        return {"status": "ok"}

    # ── Platform Connectors ───────────────────────────────────────────

    @router.get("/api/connectors")
    def list_connectors() -> list[dict[str, Any]]:
        """List all platform connectors with masked tokens."""
        entries: list[dict[str, Any]] = []
        for platform in _CONNECTOR_PLATFORMS:
            config = _load_connector_config(config_store, platform)
            entries.append(_mask_connector_entry(platform, config))
        return entries

    @router.post("/api/connectors")
    def upsert_connector(req: ConnectorUpdateRequest) -> dict[str, Any]:
        """Add or update a platform connector token.

        If the request contains a masked token placeholder, the existing token is
        preserved instead of overwriting it with the placeholder.
        """
        name = req.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Connector name is required")

        existing_config = _load_connector_config(config_store, name)
        token = req.token
        if _is_masked_placeholder(token) and existing_config.get("token"):
            token = str(existing_config["token"])
        elif name == "telegram" and token and not _is_masked_placeholder(token):
            # Normalize on save so a later Test never builds /botbot…/getMe.
            token = _normalize_telegram_bot_token(token)

        if token:
            config_store.set(f"connectors.{name}.token", token, category="connectors")
        config_store.set(f"connectors.{name}.enabled", req.enabled, category="connectors")
        for key, value in req.extras.items():
            # A URL with a masked password: unchanged, the stored URL stays;
            # changed, the save is refused rather than moving the stored
            # password to the new URL or storing the stars.
            if url_password_is_masked(str(value)):
                if _restore_url(key, str(value), existing_config.get(key)) != str(value):
                    continue
            # Masked-extras round-trip (2026-09-03 v4): GET /api/connectors
            # masks secret extras (e.g. slack app_token → "****abcd"). A save
            # that did not touch that field sends the mask back — writing it
            # would clobber the stored credential with the mask string (the
            # old per-platform dialog silently did this to app_token).
            if (
                _is_masked_placeholder(str(value))
                and existing_config.get(key)
            ):
                continue
            config_store.set(f"connectors.{name}.{key}", value, category="connectors")

        # Allowlists onto the running adapters now. The rest of a saved
        # connector (token, switch, swarm channel) reaches them through the
        # settings store's change notice (kazma_gateway.chat_adapters).
        try:
            from kazma_core.service_container import get_container
            from kazma_gateway.allowlists import apply_gateway_allowlists
            from kazma_gateway.gateway import GatewayManager

            container = get_container()
            if container.has(GatewayManager):
                apply_gateway_allowlists(container.get(GatewayManager), config_store)
        except Exception:
            logger.debug("[connectors] live allowlist apply skipped", exc_info=True)

        updated = _load_connector_config(config_store, name)
        return _mask_connector_entry(name, updated)

    @router.delete("/api/connectors/{name}")
    async def delete_connector(name: str) -> dict[str, str]:
        """Remove a connector from ConfigStore."""
        # Delete all keys under connectors.{name}.* that exist in the DB.
        #
        # The scan AND the deletes go to one thread together. Splitting them
        # (scan off-loop, delete inline) would put an unbounded number of
        # writes back on the loop, which is the same stall with extra steps.
        def _purge() -> bool:
            all_settings = config_store.get_all()
            prefix = f"connectors.{name}."
            found = False
            for category_values in all_settings.values():
                for key in list(category_values.keys()):
                    if key.startswith(prefix):
                        config_store.delete(key)
                        found = True
            return found

        deleted = await asyncio.to_thread(_purge)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Connector '{name}' not found")
        return {"status": "ok"}

    @router.post("/api/connectors/{name}/test", response_model=ConnectorTestResponse)
    async def test_connector(name: str) -> dict[str, Any]:
        """Run a platform-specific health check for a connector."""
        name = name.strip()
        # Vault-aware direct read (do not rely on get_all raw values); a
        # store read, so off the event loop.
        token = str(
            await asyncio.to_thread(config_store.get, f"connectors.{name}.token", "") or ""
        ).strip()
        # Match gateway boot: fall back to env when ConfigStore has no token.
        if not token and name == "telegram":
            token = env_credential("TELEGRAM_BOT_TOKEN") or env_credential("TELEGRAM_TOKEN")
        if not token and name == "discord":
            token = env_credential("DISCORD_BOT_TOKEN")
        if not token and name == "slack":
            token = env_credential("SLACK_BOT_TOKEN")
        if not token:
            return {
                "success": False,
                "error": (
                    f"No token configured for {name} "
                    "(or vault could not decrypt connectors.{name}.token)."
                ),
            }

        if name == "telegram":
            # Every chat app's Test diagnoses since 2026-09-29 (the owner
            # asked for Discord's on every adapter): see telegram_diagnose.
            from kazma_gateway.adapters.telegram_diagnose import diagnose as diagnose_telegram

            token = _normalize_telegram_bot_token(token)
            settings = await asyncio.to_thread(_telegram_test_settings, config_store)
            try:
                await _adapter_from_saved_settings("telegram")
                return await diagnose_telegram(token, live=_live_adapter_diagnostics("telegram"), **settings)
            except Exception:
                logger.warning("[connectors] Telegram test failed", exc_info=True)
                return {"success": False, "error": "Connection test failed"}

        if name == "discord":
            # Not "does the token sign in" alone: that answered "Connected"
            # while no message from the owner had reached Kazma in eight days
            # (2026-09-29). See discord_diagnose for each check.
            from kazma_gateway.adapters.discord_diagnose import diagnose

            settings = await asyncio.to_thread(_discord_test_settings, config_store)
            try:
                await _adapter_from_saved_settings("discord")
                return await diagnose(token, live=_live_adapter_diagnostics("discord"), **settings)
            except Exception:
                logger.warning("[connectors] Discord test failed", exc_info=True)
                return {"success": False, "error": "Connection test failed"}

        if name == "slack":
            from kazma_gateway.adapters.slack_diagnose import diagnose as diagnose_slack

            settings = await asyncio.to_thread(_slack_test_settings, config_store)
            try:
                await _adapter_from_saved_settings("slack")
                return await diagnose_slack(token, live=_live_adapter_diagnostics("slack"), **settings)
            except Exception:
                logger.warning("[connectors] Slack test failed", exc_info=True)
                return {"success": False, "error": "Connection test failed"}

        # Generic connectors cannot be tested remotely; report token presence.
        return {"success": True, "message": f"Token configured for {name}"}

    @router.post("/api/connectors/{name}/toggle")
    def toggle_connector(name: str, req: ProviderToggleRequest) -> dict[str, str]:
        """Enable or disable a connector."""
        name = name.strip()
        config_store.set(f"connectors.{name}.enabled", req.enabled, category="connectors")
        return {"status": "ok"}

    return router
