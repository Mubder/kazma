"""Settings Manager — Comprehensive configuration management for Kazma.

Provides a high-level API for the 12-tab settings system, wrapping ConfigStore
for persistence and integrating with providers, personalities, MCP, and tools.

Usage:
    from kazma_core.settings_manager import SettingsManager
    sm = SettingsManager(config_store)
    providers = sm.get_all_providers()
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import UTC, datetime
from typing import Any
from kazma_core.http_tls import shared_ssl_context
from kazma_core.token_counter import DEFAULT_CONTEXT_WINDOW

logger = logging.getLogger(__name__)

# Where Kazma learns of a newer version: the canonical repo's newest release.
# ``kazma update`` holds the same URL in kazma_cli/update.py (it must run while
# this package may not import); tests/test_no_pypi_kazma.py keeps them one.
_RELEASE_REPO = "Mubder/kazma"
_RELEASE_API = f"https://api.github.com/repos/{_RELEASE_REPO}/releases/latest"


def _release_key(version: str) -> tuple[int, ...]:
    """A version's numbers, pre-release and ``+local`` parts ignored.

    ``0.11.0+g1a2b3c4`` -> ``(0, 11)``: trailing zeros go, so 0.11 == 0.11.0.
    """
    parts: list[int] = []
    for part in version.split("+", 1)[0].split("."):
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


# ── Default shortcuts ─────────────────────────────────────────────────

DEFAULT_SHORTCUTS: dict[str, str] = {
    "send_message": "Enter",
    "new_line": "Shift+Enter",
    "new_chat": "Ctrl+N",
    "search_chats": "Ctrl+K",
    "toggle_sidebar": "Ctrl+B",
    "go_to_settings": "Ctrl+,",
    "go_to_chat": "Ctrl+1",
    "go_to_skills": "Ctrl+2",
    "go_to_mcp": "Ctrl+3",
    "go_to_swarm": "Ctrl+4",
    "toggle_theme": "Ctrl+Shift+T",
    "clear_chat": "Ctrl+Shift+X",
    "focus_input": "Ctrl+L",
    "close_modal": "Escape",
}

# ── Default appearance ────────────────────────────────────────────────

#: The logging settings Kazma reads (``logging_config``): the page saves
#: these and nothing else.
_LOGGING_FIELDS = ("level", "format", "retention_days")
#: The safety and proxy fields the Settings page saves. Their writers took any
#: key from the request and wrote ``safety.<key>`` / ``proxy.<key>``: a request
#: could write a key nothing reads, or one the page never offers.
_SAFETY_FIELDS = ("hitl_enabled", "require_approval_for", "approval_timeout", "auto_deny_on_timeout")
_PROXY_FIELDS = ("provider", "network", "host", "port", "username", "password", "session_sticky",
                 "country")

DEFAULT_APPEARANCE: dict[str, Any] = {
    # "light" is the safe default; the frontend makes this server-stored
    # value authoritative on load so a user's choice persists across
    # devices/browsers (not just one browser's localStorage). Users who
    # prefer dark toggle once and it sticks everywhere.
    "theme": "light",
    "accent_color": "#5e6ad2",
    "font_size": 14,
}

# ── Default model per task ────────────────────────────────────────────

DEFAULT_MODEL_DEFAULTS: dict[str, str] = {
    "chat": "",
    "code": "",
    "summarize": "",
    "translate": "",
    "research": "",
    "vision": "",
    "general": "",
    "fast": "",
}

_START_TIME = time.monotonic()


from kazma_core.settings_mcp import MCPSettingsService  # re-export

__all__ = ["DEFAULT_APPEARANCE", "DEFAULT_MODEL_DEFAULTS", "DEFAULT_SHORTCUTS", "SettingsManager"]


def _effective_tool_timeout() -> float:
    """The per-tool bound the tool worker applies (``agent.tool_timeout_seconds``,
    else ``KAZMA_TOOL_TIMEOUT_SECONDS``, else 120)."""
    from kazma_core.agent.graph_helpers import _configured_tool_timeout

    return _configured_tool_timeout()


class SettingsManager:
    """Comprehensive settings manager wrapping ConfigStore."""

    def __init__(self, config_store: Any) -> None:
        self._cs = config_store
        # Always create a local ModelRegistry from the provided config_store.
        # The global singleton may point to a different ConfigStore instance
        # (e.g. in tests), so we must not use it here.
        from kazma_core.model_registry import ModelRegistry

        self._registry = ModelRegistry(config_store)

        # Instantiate modular services
        self.mcp_service = MCPSettingsService(config_store)

        # Register services on global Dependency Injection container
        from kazma_core.service_container import get_container
        try:
            get_container().register(MCPSettingsService, self.mcp_service)
        except Exception as exc:
            logger.debug("[SettingsManager] Container registration skipped: %s", exc)

    # ══════════════════════════════════════════════════════════════════
    # PROVIDERS -- not here any more
    # ══════════════════════════════════════════════════════════════════
    #
    # get_all_providers / add_provider / delete_provider / toggle_provider /
    # test_provider / get_provider_health used to delegate to a
    # ProviderSettingsService, which delegated to ModelRegistry. Their only
    # callers were the /api/settings/providers/* routes, a duplicate of
    # /api/providers/* that nothing in the product used. Talk to
    # ModelRegistry directly.

    # ══════════════════════════════════════════════════════════════════
    # MODELS
    # ══════════════════════════════════════════════════════════════════

    def get_model_registry(self) -> list[dict[str, Any]]:
        """Get model registry with metadata."""
        registry = self._cs.get("models.registry", [])
        if isinstance(registry, str):
            try:
                registry = json.loads(registry)
            except (json.JSONDecodeError, TypeError):
                registry = []
        return registry if isinstance(registry, list) else []

    def get_unified_model_options(self) -> dict[str, Any]:
        """Return unified model/provider/profile options from the registry."""
        return self._registry.list_unified_options()

    def set_default_model(self, task_type: str, model_name: str) -> None:
        """Set default model for a task type."""
        self._cs.set(f"models.defaults.{task_type}", model_name, category="models")

    def get_model_defaults(self) -> dict[str, str]:
        """Get default model per task type."""
        result = dict(DEFAULT_MODEL_DEFAULTS)
        for task in result:
            val = self._cs.get(f"models.defaults.{task}", "")
            if val:
                result[task] = val
        return result

    def get_model_usage(self) -> dict[str, Any]:
        """Get token usage stats per model."""
        usage = self._cs.get("models.usage", "{}")
        if isinstance(usage, str):
            try:
                return json.loads(usage)
            except (json.JSONDecodeError, TypeError):
                return {}
        return usage if isinstance(usage, dict) else {}

    async def compare_models(
        self,
        prompt: str,
        models: list[str],
        temperature: float = 0.7,
        max_tokens: int = 256,
    ) -> list[dict[str, Any]]:
        """Run the same prompt across multiple models and return results."""
        import httpx

        base_url = self._cs.get("llm.base_url", "")
        api_key = self._cs.get("llm.api_key", "")

        if not base_url:
            return [{"model": m, "error": "No base URL configured"} for m in models]

        results: list[dict[str, Any]] = []
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        for model_name in models:
            start = time.monotonic()
            try:
                payload = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                async with httpx.AsyncClient(timeout=30.0, verify=shared_ssl_context()) as client:
                    resp = await client.post(
                        f"{base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    latency = int((time.monotonic() - start) * 1000)
                    resp.raise_for_status()
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    token_usage = data.get("usage", {})
                    results.append({
                        "model": model_name,
                        "response": content,
                        "latency_ms": latency,
                        "tokens": token_usage,
                    })
            except Exception as e:
                latency = int((time.monotonic() - start) * 1000)
                results.append({
                    "model": model_name,
                    "error": str(e),
                    "latency_ms": latency,
                })
        return results

    # ══════════════════════════════════════════════════════════════════
    # SAVED MODEL PROFILES
    # ══════════════════════════════════════════════════════════════════

    _SAVED_PROFILE_FIELDS = ("base_url", "api_key", "model", "provider")

    def save_model_profile(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        """Save a named model profile to ConfigStore.

        Stores the profile under ``models.saved.{name}`` with the fields
        ``base_url``, ``api_key``, ``model``, and ``provider``.

        Args:
            name: Profile name (sanitized, must be non-empty).
            data: Dict containing at least ``base_url``, ``api_key``,
                ``model``, and optionally ``provider``.

        Returns:
            The saved profile dict (without the raw api_key).
        """
        result = self._registry.save_model_profile(name, data)
        if "error" not in result:
            logger.info("Saved model profile: %s", (name or "").strip())
        return result

    def get_saved_model_profiles(self) -> list[dict[str, Any]]:
        """Return all saved model profiles.

        Reads all keys in the ``models`` category that start with
        ``models.saved.`` and returns a list of profile dicts with
        masked api_key values.

        Returns:
            List of profile dicts, each with keys: name, base_url,
            api_key (masked), model, provider.
        """
        return self._registry.list_model_profiles(mask_api_key=True)

    def get_model_profile(self, name: str) -> dict[str, Any] | None:
        """Return a single saved model profile by name (with raw api_key).

        Args:
            name: Profile name.

        Returns:
            Profile dict or ``None`` if not found.
        """
        return self._registry.get_model_profile(name)

    def delete_model_profile(self, name: str) -> bool:
        """Delete a saved model profile.

        Args:
            name: Profile name.

        Returns:
            True if a profile was deleted, False otherwise.
        """
        deleted = self._registry.delete_model_profile(name)
        if deleted:
            logger.info("Deleted model profile: %s", name)
        return deleted

    # ══════════════════════════════════════════════════════════════════
    # AGENT
    # ══════════════════════════════════════════════════════════════════

    def get_agent_config(self) -> dict[str, Any]:
        """Get current agent settings."""
        raw_max = self._cs.get("agent.max_iterations", 15)
        try:
            max_iter = int(raw_max)
        except (TypeError, ValueError):
            max_iter = 15
        max_iter = max(5, min(100, max_iter))
        default_on = self._cs.get("agent.long_task.default_enabled") in (
            True, 1, "1", "true", "yes", "on",
        )
        default_preset = str(
            self._cs.get("agent.long_task.default_preset") or "research"
        ).lower()
        if default_preset not in ("chat", "deep", "research"):
            default_preset = "research"
        try:
            from kazma_core.agent.long_task import derive_recursion_limit

            recursion = derive_recursion_limit(max_iter)
        except Exception:
            recursion = max(100, max_iter * 5 + 20)
        return {
            "name": self._cs.get("agent.name", "kazma"),
            "language": self._cs.get("agent.language", "ar"),
            "system_prompt": self._cs.get("agent.system_prompt", ""),
            "personality": self._cs.get("agent.personality", "default"),
            # ReAct tool-round ceiling (supervisor loop)
            "max_iterations": max_iter,
            # Effective graph step budget (derived; shown in UI for honesty)
            "recursion_limit": recursion,
            "long_task_default_enabled": default_on,
            "long_task_default_preset": default_preset,
        }

    def save_agent_config(self, data: dict[str, Any]) -> None:
        """Save agent configuration."""
        # Nested long-task defaults (not agent.* flat keys for every field)
        if "long_task_default_enabled" in data:
            en = data.pop("long_task_default_enabled")
            self._cs.set(
                "agent.long_task.default_enabled",
                bool(en) if not isinstance(en, str)
                else en.strip().lower() in ("1", "true", "yes", "on"),
                category="agent",
            )
        if "long_task_default_preset" in data:
            preset = str(data.pop("long_task_default_preset") or "research").lower()
            if preset not in ("chat", "deep", "research"):
                preset = "research"
            self._cs.set(
                "agent.long_task.default_preset", preset, category="agent"
            )
        # recursion_limit is derived — never persist a user-forged value as SoT
        data.pop("recursion_limit", None)

        # Personality must apply the server runtime override, not only the
        # ConfigStore key — otherwise Web/TUI Settings write a value the
        # next turn still ignores if /personality set _runtime_override.
        if "personality" in data:
            name = data.pop("personality")
            if name is not None and str(name).strip():
                self.set_personality(str(name).strip())

        for key, value in data.items():
            if value is None:
                continue
            if key == "max_iterations":
                try:
                    value = max(5, min(100, int(value)))
                except (TypeError, ValueError):
                    value = 15
            self._cs.set(f"agent.{key}", value, category="agent")

    def get_personalities(self) -> list[dict[str, Any]]:
        """List available personality templates."""
        try:
            from kazma_core.personalities import list_personalities
            return [
                {
                    "name": p.name,
                    "emoji": p.emoji,
                    "description": p.description,
                    "description_ar": p.get("description_ar", ""),
                    "display_name_ar": p.get("display_name_ar", ""),
                    "system_prompt": p.system_prompt,
                }
                for p in list_personalities()
            ]
        except ImportError:
            return []

    def set_personality(self, name: str) -> None:
        """Switch active personality."""
        try:
            from kazma_core.personalities import set_runtime_personality
            set_runtime_personality(name)
        except (ImportError, ValueError) as e:
            logger.warning("Failed to set personality: %s", e)
        self._cs.set("agent.personality", name, category="agent")

    def get_safety_settings(self) -> dict[str, Any]:
        """Get HITL safety settings.

        Default ``require_approval_for`` mirrors
        :data:`kazma_core.safety.hitl.CANONICAL_DANGER_TOOLS` so the Settings
        UI does not show a truncated 3-tool list that disagrees with runtime.
        """
        try:
            from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS

            _default_danger = list(CANONICAL_DANGER_TOOLS)
        except Exception:
            _default_danger = ["file_write", "file_delete", "shell_exec", "code_exec", "python_exec"]
        soul_flag = False
        outbound: list[str] = []
        try:
            from kazma_core.safety.commitment.config import get_commitment_config

            _cc = get_commitment_config()
            soul_flag = bool(_cc.get("soul_requires_confirm"))
            raw_oat = _cc.get("outbound_allowed_targets") or []
            if isinstance(raw_oat, str):
                outbound = [t.strip() for t in raw_oat.split(",") if t.strip()]
            elif isinstance(raw_oat, (list, tuple)):
                outbound = [str(t).strip() for t in raw_oat if str(t).strip()]
        except Exception:
            soul_flag = bool(self._cs.get("agent.commitment.soul_requires_confirm", False))
            raw_oat = self._cs.get("agent.commitment.outbound_allowed_targets", [])
            if isinstance(raw_oat, str):
                outbound = [t.strip() for t in raw_oat.split(",") if t.strip()]
            elif isinstance(raw_oat, (list, tuple)):
                outbound = [str(t).strip() for t in raw_oat if str(t).strip()]
        return {
            "hitl_enabled": self._cs.get("safety.hitl_enabled", True),
            "require_approval_for": self._cs.get(
                "safety.require_approval_for", _default_danger
            ),
            "approval_timeout": self._cs.get("safety.approval_timeout", 300),
            "auto_deny_on_timeout": self._cs.get("safety.auto_deny_on_timeout", True),
            "soul_requires_confirm": soul_flag,
            "outbound_allowed_targets": outbound,
        }

    def save_safety_settings(self, data: dict[str, Any]) -> None:
        """Update HITL safety settings."""
        payload = dict(data or {})
        if "soul_requires_confirm" in payload:
            soul = payload.pop("soul_requires_confirm")
            self._cs.set(
                "agent.commitment.soul_requires_confirm",
                bool(soul),
                category="agent",
            )
        if "outbound_allowed_targets" in payload:
            oat = payload.pop("outbound_allowed_targets")
            if isinstance(oat, str):
                oat_list = [t.strip() for t in oat.split(",") if t.strip()]
            elif isinstance(oat, (list, tuple)):
                oat_list = [str(t).strip() for t in oat if str(t).strip()]
            else:
                oat_list = []
            self._cs.set(
                "agent.commitment.outbound_allowed_targets",
                oat_list,
                category="agent",
            )
        for key, value in payload.items():
            if key in _SAFETY_FIELDS:
                self._cs.set(f"safety.{key}", value, category="safety")

    # ══════════════════════════════════════════════════════════════════
    # LOGGING
    # ══════════════════════════════════════════════════════════════════

    def get_logging_settings(self) -> dict[str, Any]:
        """Get logging settings (level, format, rotation retention).

        These persist immediately but log *level* is the only one that can
        hot-apply — rotation/retention params take effect on the next server
        boot (Python logging handlers are configured once at startup).
        """
        from kazma_core.logging_config import resolve_retention_days, _resolve_level_with_config

        return {
            "level": self._cs.get("logging.level", _resolve_level_with_config(None)),
            "format": self._cs.get("logging.format", "text"),
            "retention_days": self._cs.get("logging.retention_days", resolve_retention_days()),
        }

    def save_logging_settings(self, data: dict[str, Any]) -> None:
        """Update logging settings.

        Persists to ConfigStore. The level applies now (``logging_config.
        apply_level``); rotation/retention changes require a server restart
        (handlers are set at boot).
        """
        allowed_levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        for key, value in data.items():
            if key not in _LOGGING_FIELDS:
                continue  # a field logging_config does not read
            if key == "level" and isinstance(value, str):
                value = value.strip().upper()
                if value not in allowed_levels:
                    continue
            elif key == "retention_days":
                try:
                    n = int(value)
                except (TypeError, ValueError):
                    continue
                if n < 1:
                    continue  # reject invalid; keep existing value
                value = n
            elif key == "format" and isinstance(value, str):
                value = "json" if value.strip().lower() == "json" else "text"
            self._cs.set(f"logging.{key}", value, category="logging")

        # The level applies now, everywhere boot set it: Kazma's loggers, the
        # root and its file and console handlers (FA-114: the root alone was
        # set, so DEBUG reached the log only after a restart). Rotation
        # changes are deferred to the next boot.
        level_name = data.get("level")
        if isinstance(level_name, str) and level_name.strip().upper() in allowed_levels:
            from kazma_core.logging_config import apply_level

            apply_level(level_name)

    # ══════════════════════════════════════════════════════════════════
    # PROXY PROVIDER (opt-in scraping resilience addon)
    # ══════════════════════════════════════════════════════════════════

    def get_proxy_settings(self) -> dict[str, Any]:
        """Get proxy provider config (opt-in scraping proxy).

        ``password`` is vault-encrypted at rest and answered masked: ``****``
        when one is stored, ``""`` when not -- the route returned it decrypted
        (2026-11, FA-129). A ``****`` posted back keeps the stored one.
        """
        from kazma_core.security.config_mask import MASK

        from kazma_core.web_acquire.policy import proxy_policy_state

        stored = self._cs.get("proxy.password", "")
        return {
            # Whether the acquisition policy lets Kazma use it at all (FA-115).
            "policy": proxy_policy_state(),
            "provider": self._cs.get("proxy.provider", "none"),
            "host": self._cs.get("proxy.host", "portal.anyip.io"),
            "port": self._cs.get("proxy.port", "1080"),
            "username": self._cs.get("proxy.username", ""),
            "password": MASK if stored else "",
            "network": self._cs.get("proxy.network", "mixed"),
            "country": self._cs.get("proxy.country", ""),
            "session_sticky": self._cs.get("proxy.session_sticky", False),
        }

    def save_proxy_settings(self, data: dict[str, Any]) -> None:
        """Save proxy provider config.

        ``password`` auto-encrypts via the vault (the key matches the sensitive
        rule). Provider changes take effect live (no restart) because the
        registry re-reads ``proxy.provider`` on each call.
        """
        from kazma_core.config_store import is_masked_secret_placeholder

        allowed_networks = ("residential", "mobile", "mixed")
        # Collect validated items and write them atomically (AGENTS.md §8).
        items: list[tuple[str, Any, str]] = []
        for key, value in data.items():
            if key not in _PROXY_FIELDS:
                continue
            if key == "password" and is_masked_secret_placeholder(value):
                continue  # the mask the page was shown: keep the stored password
            if key == "provider" and isinstance(value, str):
                value = value.strip().lower()
                if value not in ("none", "anyip"):
                    continue
            elif key == "network" and isinstance(value, str):
                value = value.strip().lower()
                if value not in allowed_networks:
                    continue
            elif key == "port":
                try:
                    value = str(int(value))
                except (TypeError, ValueError):
                    continue
            elif key == "session_sticky":
                value = bool(value)
            elif key == "country" and isinstance(value, str):
                value = value.strip().upper()[:3]  # ISO code, max 3 chars
            items.append((f"proxy.{key}", value, "proxy"))
        if items:
            self._cs.batch_set(items)

    async def test_proxy(self) -> dict[str, Any]:
        """Health-check the active proxy provider. Returns {success, exit_ip?}."""
        import asyncio

        from kazma_core.proxy.base import probe_proxy
        from kazma_core.proxy.client import get_active_proxy_url
        from kazma_core.web_acquire.policy import acquisition_policy
        from kazma_core.web_acquire.result import AcquisitionError

        from kazma_core.web_acquire.policy import (
            PROXY_POLICY_STEP,
            AcquisitionPolicyError,
            proxy_policy_state,
        )

        try:
            allowed = acquisition_policy().permits('proxy')
        except AcquisitionPolicyError as exc:
            return {'success': False, 'error': 'policy_invalid', 'message': str(exc),
                    'policy': proxy_policy_state()}
        if not allowed:
            # The one step that changes it (FA-115): "disabled by acquisition
            # policy" said nothing about which variables decide.
            return {'success': False, 'error': 'proxy_not_allowed', 'message': PROXY_POLICY_STEP,
                    'policy': proxy_policy_state()}
        try:
            proxy_url = await asyncio.to_thread(get_active_proxy_url)
        except AcquisitionError as exc:
            return {'success': False, 'error': str(exc)}
        return await probe_proxy(proxy_url)


    # ══════════════════════════════════════════════════════════════════
    # EMBEDDER (memory vector model — Web UI Embedder settings page)
    # ══════════════════════════════════════════════════════════════════

    def get_embedder_settings(self) -> dict[str, Any]:
        """Get the ConfigStore override for the memory embedder.

        Returns only what is persisted in the store (empty strings when
        unset). The *effective* config (store > kazma.yaml > defaults) is
        resolved by ``kazma_core.memory.embedder.get_embedding_config()``.
        """
        return {
            "provider": self._cs.get("embedding.provider", ""),
            "model": self._cs.get("embedding.model", ""),
            "dim": self._cs.get("embedding.dim", ""),
            "base_url": self._cs.get("embedding.base_url", ""),
            "api_key_env": self._cs.get("embedding.api_key_env", ""),
        }

    def save_embedder_settings(self, data: dict[str, Any]) -> None:
        """Save the embedder override to ConfigStore (``embedding.*`` keys).

        The embedder singleton is per-process, so the new model takes effect
        on the next server restart — saving never hot-swaps the live model
        (stamping + vector-space consistency require a clean boot).
        """
        provider = str(data.get("provider") or "").strip().lower()
        if provider not in ("local", "openai-compatible"):
            raise ValueError("Provider must be 'local' or 'openai-compatible'.")
        model = str(data.get("model") or "").strip()
        if not model:
            raise ValueError("Model is required.")
        try:
            dim = int(data.get("dim") or 0)
        except (TypeError, ValueError):
            raise ValueError("Dimension must be an integer.")
        if dim < 1:
            raise ValueError("Dimension must be a positive integer.")
        base_url = str(data.get("base_url") or "").strip()
        api_key_env = str(data.get("api_key_env") or "KAZMA_EMBED_API_KEY").strip() or "KAZMA_EMBED_API_KEY"

        # Atomic multi-key write (AGENTS.md §8): embedding.model/dim MUST stay
        # paired — a crash between independent sets left a half-updated block
        # that silently corrupted every future vector recall (dim mismatch).
        self._cs.batch_set([
            ("embedding.provider", provider, "embedding"),
            ("embedding.model", model, "embedding"),
            ("embedding.dim", int(dim), "embedding"),
            ("embedding.base_url", base_url, "embedding"),
            ("embedding.api_key_env", api_key_env, "embedding"),
        ])

    def get_time_travel_settings(self) -> dict[str, Any]:
        """Get the ConfigStore override for time travel (replay/fork).

        ``retention_days`` / ``auto_maintain`` are read live by the daily
        maintenance loop (no restart needed); ``max_snapshots`` is read at
        recorder creation (restart needed).
        """
        return {
            "max_snapshots": self._cs.get("time_travel.max_snapshots", None),
            "retention_days": self._cs.get("time_travel.retention_days", None),
            "auto_maintain": self._cs.get("time_travel.auto_maintain", None),
        }

    def save_time_travel_settings(self, data: dict[str, Any]) -> None:
        """Save the time-travel override to ConfigStore (``time_travel.*`` keys).

        The SnapshotRecorder is created per-agent at server startup, so
        ``max_snapshots`` takes effect on the next restart — saving never
        hot-swaps the running recorder (per-thread eviction bounds rely on
        it). ``retention_days`` / ``auto_maintain`` are live-read by the
        daily maintenance loop and apply on its next run.
        """
        raw = data.get("max_snapshots")
        if raw is None or str(raw).strip() == "":
            raise ValueError("Snapshots per thread is required.")
        try:
            max_snapshots = int(raw)
        except (TypeError, ValueError):
            raise ValueError("Snapshots per thread must be an integer.")
        if max_snapshots < 1:
            raise ValueError("Snapshots per thread must be at least 1.")
        if max_snapshots > 1000:
            raise ValueError("Snapshots per thread must be at most 1000.")
        self._cs.set("time_travel.max_snapshots", max_snapshots, category="time_travel")

        if data.get("retention_days") is not None:
            try:
                retention_days = int(data["retention_days"])
            except (TypeError, ValueError):
                raise ValueError("Retention days must be an integer.")
            if retention_days < 1 or retention_days > 3650:
                raise ValueError("Retention days must be between 1 and 3650.")
            self._cs.set("time_travel.retention_days", retention_days, category="time_travel")
        if data.get("auto_maintain") is not None:
            self._cs.set("time_travel.auto_maintain", bool(data["auto_maintain"]), category="time_travel")

    def get_context_settings(self) -> dict[str, Any]:
        """Get context window settings."""
        return {
            "max_context_tokens": self._cs.get("context.max_context_tokens", DEFAULT_CONTEXT_WINDOW),
        }

    # ── Non-Stop & Self-Healing (agent.nonstop.*) ─────────────────────
    # (and the per-tool bound, agent.tool_timeout_seconds, shown with them)

    def get_nonstop_settings(self) -> dict[str, Any]:
        """Get the non-stop / self-healing settings (agent.nonstop.* keys).

        Mirrors the defaults in ``kazma_core.agent.nonstop.NonStopConfig`` so
        the UI shows effective values even when nothing is stored yet.
        """
        from kazma_core.agent.nonstop import NonStopConfig

        d = NonStopConfig()
        return {
            "enabled": self._cs.get("agent.nonstop.enabled", d.enabled),
            "stall_threshold_seconds": self._cs.get(
                "agent.nonstop.watchdog.stall_threshold_seconds", d.watchdog.stall_threshold_seconds
            ),
            # The bound every tool call gets (graph_helpers), Non-Stop on or
            # off; it was saved to a Non-Stop key nothing read (FA-125).
            "tool_timeout_seconds": _effective_tool_timeout(),
            "max_recovery_attempts": self._cs.get(
                "agent.nonstop.healing.max_recovery_attempts", d.healing.max_recovery_attempts
            ),
            "backoff_base_seconds": self._cs.get(
                "agent.nonstop.healing.backoff_base_seconds", d.healing.backoff_base_seconds
            ),
            "backoff_max_seconds": self._cs.get(
                "agent.nonstop.healing.backoff_max_seconds", d.healing.backoff_max_seconds
            ),
            "failover_enabled": self._cs.get("agent.nonstop.failover.enabled", d.failover.enabled),
            "failover_chain": self._cs.get("agent.nonstop.failover.chain", ""),
            "failover_cooldown_seconds": self._cs.get(
                "agent.nonstop.failover.cooldown_seconds", d.failover.cooldown_seconds
            ),
            "ledger_enabled": self._cs.get("agent.nonstop.ledger.enabled", d.ledger_enabled),
        }

    def save_nonstop_settings(self, data: dict[str, Any]) -> None:
        """Save non-stop settings to ConfigStore (explicit key whitelist).

        ``failover_chain`` accepts a comma-separated string or a list of
        model ids. All values are validated/clamped before persisting.
        Writes are atomic via ``batch_set`` (AGENTS.md §8): a watchdog /
        failover policy block must not be left half-updated by a crash.
        """
        items: list[tuple[str, Any, str]] = []
        if "enabled" in data:
            items.append(("agent.nonstop.enabled", bool(data["enabled"]), "agent"))
        if "stall_threshold_seconds" in data:
            v = float(data["stall_threshold_seconds"])
            if v < 10 or v > 3600:
                raise ValueError("Stall threshold must be between 10 and 3600 seconds.")
            items.append(("agent.nonstop.watchdog.stall_threshold_seconds", v, "agent"))
        if "tool_timeout_seconds" in data:
            v = float(data["tool_timeout_seconds"])
            if v != 0 and (v < 5 or v > 3600):
                raise ValueError("Tool timeout must be 0 (disabled) or between 5 and 3600 seconds.")
            items.append(("agent.tool_timeout_seconds", v, "agent"))
        if "max_recovery_attempts" in data:
            v = int(data["max_recovery_attempts"])
            if v < 0 or v > 10:
                raise ValueError("Max recovery attempts must be between 0 and 10.")
            items.append(("agent.nonstop.healing.max_recovery_attempts", v, "agent"))
        if "backoff_base_seconds" in data:
            v = float(data["backoff_base_seconds"])
            if v < 0.5 or v > 600:
                raise ValueError("Backoff base must be between 0.5 and 600 seconds.")
            items.append(("agent.nonstop.healing.backoff_base_seconds", v, "agent"))
        if "backoff_max_seconds" in data:
            v = float(data["backoff_max_seconds"])
            if v < 1 or v > 3600:
                raise ValueError("Backoff max must be between 1 and 3600 seconds.")
            items.append(("agent.nonstop.healing.backoff_max_seconds", v, "agent"))
        if "failover_enabled" in data:
            items.append(("agent.nonstop.failover.enabled", bool(data["failover_enabled"]), "agent"))
        if "failover_chain" in data:
            chain = data["failover_chain"]
            if isinstance(chain, str):
                chain = [p.strip() for p in chain.split(",") if p.strip()]
            if not isinstance(chain, list) or len(chain) > 8:
                raise ValueError("Failover chain must be a list of at most 8 model ids.")
            # A backup nothing can serve is refused (providers stage 1): the
            # chain named Google with no Google key, and the failover drill
            # failed for a month with OpenRouter and Groq both working.
            from kazma_core.failover_backup import unusable_backups
            from kazma_core.model_registry import get_model_registry

            bad = unusable_backups(get_model_registry(), [str(m).strip() for m in chain if str(m).strip()])
            if bad:
                raise ValueError(
                    "No provider that can be called serves "
                    + ", ".join(repr(row["model"]) for row in bad)
                    + " (a provider switched on, with a key or on this network, listing the model)."
                )
            items.append(("agent.nonstop.failover.chain", ",".join(str(m) for m in chain), "agent"))
        if "failover_cooldown_seconds" in data:
            v = float(data["failover_cooldown_seconds"])
            if v < 10 or v > 86400:
                raise ValueError("Failover cooldown must be between 10 and 86400 seconds.")
            items.append(("agent.nonstop.failover.cooldown_seconds", v, "agent"))
        if "ledger_enabled" in data:
            items.append(("agent.nonstop.ledger.enabled", bool(data["ledger_enabled"]), "agent"))
        if items:
            self._cs.batch_set(items)

    def save_context_settings(self, data: dict[str, Any]) -> None:
        """Update context window settings.

        Writes only the 3 known keys explicitly — prevents the nested-prefix
        explosion that happens when the Alpine state object accumulates
        dotted ConfigStore keys from get_all() and then resends them.
        Atomic via ``batch_set`` (AGENTS.md §8).
        """
        items: list[tuple[str, Any, str]] = []
        if "max_context_tokens" in data:
            items.append(("context.max_context_tokens", data["max_context_tokens"], "context"))
        if items:
            self._cs.batch_set(items)

    # ══════════════════════════════════════════════════════════════════
    # MCP (Delegated to MCPSettingsService)
    # ══════════════════════════════════════════════════════════════════

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        """List all MCP servers with status."""
        return self.mcp_service.get_mcp_servers()

    def add_mcp_server(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new MCP server."""
        return self.mcp_service.add_mcp_server(data)

    def delete_mcp_server(self, name: str) -> dict[str, Any]:
        """Remove an MCP server. Returns the service result dict ({"status": ...})."""
        return self.mcp_service.delete_mcp_server(name)

    def toggle_mcp_server(self, name: str, enabled: bool) -> bool:
        """Switch an MCP server on or off; False when there is no such server."""
        return self.mcp_service.toggle_mcp_server(name, enabled)

    # ══════════════════════════════════════════════════════════════════
    # APPEARANCE
    # ══════════════════════════════════════════════════════════════════

    def get_appearance(self) -> dict[str, Any]:
        """Get appearance settings. ``accent_color_chosen``: the owner picked
        the accent (every page applies it then; the default is the theme's)."""
        result = dict(DEFAULT_APPEARANCE)
        result["accent_color_chosen"] = False
        for key in DEFAULT_APPEARANCE:
            val = self._cs.get(f"appearance.{key}", None)
            if val is not None:
                result[key] = val
                if key == "accent_color":
                    result["accent_color_chosen"] = True
        return result

    def save_appearance(self, data: dict[str, Any]) -> None:
        """Update appearance settings: the fields every page applies
        (``DEFAULT_APPEARANCE``). Anything else is not saved -- the page's
        sidebar position and custom CSS were, and took effect nowhere."""
        for key, value in data.items():
            if key in DEFAULT_APPEARANCE and value is not None:
                self._cs.set(f"appearance.{key}", value, category="appearance")

    # ══════════════════════════════════════════════════════════════════
    # SHORTCUTS
    # ══════════════════════════════════════════════════════════════════

    def get_shortcuts(self) -> dict[str, str]:
        """Get current key bindings."""
        result = dict(DEFAULT_SHORTCUTS)
        stored = self._cs.get("shortcuts", None)
        if stored and isinstance(stored, dict):
            result.update(stored)
        return result

    def save_shortcut(self, action: str, keys: str) -> None:
        """Update a single shortcut."""
        current = self.get_shortcuts()
        current[action] = keys
        self._cs.set("shortcuts", current, category="shortcuts")

    def reset_shortcuts(self) -> None:
        """Reset shortcuts to defaults."""
        self._cs.set("shortcuts", DEFAULT_SHORTCUTS, category="shortcuts")

    def detect_conflicts(self, shortcuts: dict[str, str] | None = None) -> list[dict[str, str]]:
        """Find conflicting key bindings."""
        if shortcuts is None:
            shortcuts = self.get_shortcuts()
        conflicts: list[dict[str, str]] = []
        items = list(shortcuts.items())
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[i][1] and items[j][1] and items[i][1] == items[j][1]:
                    conflicts.append({
                        "action1": items[i][0],
                        "action2": items[j][0],
                        "keys": items[i][1],
                    })
        return conflicts

    # ══════════════════════════════════════════════════════════════════
    # ACCOUNT
    # ══════════════════════════════════════════════════════════════════


    def get_api_tokens(self) -> list[dict[str, Any]]:
        """List API tokens (metadata only; raw secret is never stored)."""
        tokens = self._cs.get("account.tokens", [])
        # ConfigStore json.dumps on write; older code double-encoded with
        # json.dumps before set(), so peel nested JSON strings.
        for _ in range(3):
            if isinstance(tokens, str):
                try:
                    tokens = json.loads(tokens)
                except (json.JSONDecodeError, TypeError):
                    tokens = []
                    break
            else:
                break
        if not isinstance(tokens, list):
            return []
        return [t for t in tokens if isinstance(t, dict)]

    def create_api_token(self, name: str) -> dict[str, Any]:
        """Generate a new API token."""
        token = f"kazma_{secrets.token_hex(32)}"
        import hashlib
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        token_entry = {
            "id": secrets.token_hex(8),
            "name": (name or "unnamed").strip() or "unnamed",
            "token_prefix": token[:16],
            "token_hash": token_hash,
            "created_at": datetime.now(UTC).isoformat(),
            "expires_days": 90,
        }
        tokens = self.get_api_tokens()
        tokens.append(token_entry)
        # Pass a list — ConfigStore already json.dumps; do NOT double-encode.
        self._cs.set("account.tokens", tokens, category="account")
        return {"status": "ok", "token": token, "id": token_entry["id"]}

    def revoke_api_token(self, token_id: str) -> bool:
        """Revoke an API token by id. Returns True if a token was removed."""
        tid = (token_id or "").strip()
        if not tid:
            return False
        tokens = self.get_api_tokens()
        kept = [t for t in tokens if str(t.get("id", "")) != tid]
        if len(kept) == len(tokens):
            return False
        self._cs.set("account.tokens", kept, category="account")
        return True


    # ══════════════════════════════════════════════════════════════════
    # TOOLS
    # ══════════════════════════════════════════════════════════════════

    def get_tool_registry(self) -> list[dict[str, Any]]:
        """Get all registered tools with metadata."""
        tools: list[dict[str, Any]] = []
        from kazma_core.skills.switches import tool_key

        # Try to get from the local tool registry
        try:
            from kazma_core.agent.tool_registry import LocalToolRegistry
            registry = LocalToolRegistry(include_builtins=True)
            for tool in registry.list_tools():
                tool_obj = registry.get_tool(tool["name"])
                entry = {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "category": tool.get("category", "general"),
                    "enabled": self._cs.get(tool_key(tool["name"]), True),
                    "parameters": tool_obj.input_schema if tool_obj else {},
                }
                tools.append(entry)
        except ImportError:
            logger.warning("Could not import LocalToolRegistry")
        return tools

    def toggle_tool(self, tool_name: str, enabled: bool) -> None:
        """Switch a tool on or off: offered and run, or neither, from the next
        model call (``kazma_core.skills.switches``, FA-250)."""
        from kazma_core.skills.switches import set_tool_enabled

        set_tool_enabled(tool_name, bool(enabled), store=self._cs)

    async def test_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run a tool with test arguments."""
        try:
            from kazma_core.agent.tool_registry import LocalToolRegistry
            registry = LocalToolRegistry(include_builtins=True)
            result = await registry.execute(tool_name, arguments)
            return result
        except Exception as e:
            return {"content": f"Error: {e}", "is_error": True}


    # ══════════════════════════════════════════════════════════════════
    # SYSTEM
    # ══════════════════════════════════════════════════════════════════

    def get_logs(self, lines: int = 100) -> dict[str, Any]:
        """Read recent log entries using bounded tail read."""
        lines = max(1, min(lines, 5000))
        # The file the server writes (``paths.log_file()``: ``KAZMA_LOG_FILE``,
        # else ``<install>/.kazma/kazma.log``). It looked in the data dir and
        # the process's working folder, where no log is written, and showed
        # "No log file found" on every install (2026-10-16).
        from kazma_core.paths import log_file

        for log_path in (log_file(),):
            if log_path.exists():
                try:
                    size = log_path.stat().st_size
                    read_bytes = min(size, max(65536, lines * 512))
                    with open(log_path, "rb") as f:
                        if size > read_bytes:
                            f.seek(size - read_bytes)
                        chunk = f.read(read_bytes).decode("utf-8", errors="replace")
                    tail_lines = chunk.splitlines()
                    if size > read_bytes and tail_lines:
                        tail_lines = tail_lines[1:]
                    return {
                        "lines": tail_lines[-lines:],
                        "total": len(tail_lines),
                        "path": str(log_path),
                    }
                except Exception as e:
                    return {"lines": [f"Error reading log: {e}"], "total": 0}
        return {"lines": ["No log file found"], "total": 0}

    def create_backup(self, fmt: str = "yaml") -> str:
        """A settings backup (``kazma_core.settings_restore``): every stored
        setting, keys as vault references; no credential, no running state."""
        from kazma_core.settings_restore import backup_text

        return backup_text(self._cs, fmt)

    def restore_backup(
        self,
        data: str,
        *,
        dry_run: bool = False,
        sections: list[str] | None = None,
        expect: str | None = None,
    ) -> dict[str, Any]:
        """Preview or apply a restore of the backup *data*; keys held now are
        kept (``kazma_core.settings_restore.restore``). Raises ``NotABackup``
        and ``PlanChanged`` from that module."""
        from kazma_core.settings_restore import restore

        return restore(data, self._cs, dry_run=dry_run, sections=sections, expect=expect)

    def undo_restore(self, *, dry_run: bool = False, expect: str | None = None) -> dict[str, Any]:
        """Preview or apply the undo of the last restore."""
        from kazma_core.settings_restore import undo

        return undo(self._cs, dry_run=dry_run, expect=expect)

    def system_reset(self) -> int:
        """Full reset of all settings."""
        return self._cs.reset_all()

    def check_updates(self) -> dict[str, Any]:
        """Is a newer Kazma release out? Asks GitHub, the one channel Kazma ships through.

        Never PyPI: no Kazma package is published there, so ``kazma`` is
        anyone's name to register, and a version read from it could announce
        an "update" that installing ``kazma`` by name would fetch from whoever
        registered it (2026-09-30: this read PyPI's ``kazma`` and, when that
        failed, said the install was current). The running version is the one
        the rest of Kazma shows (``kazma_core.version``); it used to read a
        ``kazma_core.__version__`` that does not exist, so it said "0.5.0".
        A check that cannot read GitHub says so in ``error``.
        """
        from kazma_core.version import get_base_version, get_version

        result: dict[str, Any] = {
            "current_version": get_version(),
            "latest_version": None,
            "update_available": False,
        }
        try:
            import httpx

            resp = httpx.get(
                _RELEASE_API,
                timeout=10.0,
                headers={"Accept": "application/vnd.github+json"},
                follow_redirects=True,
                verify=shared_ssl_context(),
            )
        except Exception as exc:
            logger.warning("Update check could not reach GitHub: %s", exc)
            result["error"] = f"GitHub could not be reached ({type(exc).__name__})"
            return result
        if resp.status_code != 200:
            result["error"] = f"GitHub answered {resp.status_code}"
            return result
        try:
            data = resp.json()
        except ValueError:
            data = None
        tag = str(data.get("tag_name") or "").strip() if isinstance(data, dict) else ""
        latest = tag[1:] if tag.startswith("v") else tag
        if not latest:
            result["error"] = "GitHub listed no release"
            return result
        result["latest_version"] = latest
        result["update_available"] = _release_key(latest) > _release_key(get_base_version())
        result["release_url"] = f"https://github.com/{_RELEASE_REPO}/releases/tag/{tag}"
        return result

    def get_diagnostics(self) -> dict[str, Any]:
        """Get system health and diagnostics."""
        import platform as platform_mod

        uptime_seconds = int(time.monotonic() - _START_TIME)
        hours, remainder = divmod(uptime_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        result: dict[str, Any] = {
            "uptime": f"{hours}h {minutes}m {seconds}s",
            "python_version": platform_mod.python_version(),
            "os": platform_mod.system(),
            "os_version": platform_mod.release(),
            "hostname": platform_mod.node(),
        }

        # Try psutil
        try:
            import psutil
            result["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            result["memory_mb"] = round(mem.used / 1024 / 1024)
            result["memory_total_mb"] = round(mem.total / 1024 / 1024)
            result["memory_percent"] = mem.percent
            disk = psutil.disk_usage("/")
            result["disk_free_gb"] = round(disk.free / 1024 / 1024 / 1024, 1)
            result["disk_total_gb"] = round(disk.total / 1024 / 1024 / 1024, 1)
        except ImportError:
            # Fallback without psutil
            try:
                import shutil
                total, used, free = shutil.disk_usage("/")
                result["disk_free_gb"] = round(free / 1024 / 1024 / 1024, 1)
                result["disk_total_gb"] = round(total / 1024 / 1024 / 1024, 1)
            except Exception as _e:
                logger.debug("disk_usage fallback failed: %s", _e)
                result["disk_free_gb"] = "N/A"
            result["cpu_percent"] = "N/A (install psutil)"
            result["memory_mb"] = "N/A (install psutil)"

        return result

    # ══════════════════════════════════════════════════════════════════
    # IMPORT / EXPORT
    # ══════════════════════════════════════════════════════════════════

    # Backup and restore: create_backup / restore_backup / undo_restore above,
    # through kazma_core.settings_restore. The raw export (every row, keys
    # masked by name, grouped by category) and import (every value written,
    # credentials and Kazma's own state included) are gone: an import could
    # not read its own export, and wrote what a restore must never write.

    def get_config_diff(self, old_config: dict[str, Any], new_config: dict[str, Any]) -> dict[str, Any]:
        """Compare two config dicts and return differences."""
        diff: dict[str, Any] = {"added": {}, "removed": {}, "changed": {}}

        def _flatten_to_dict(d: dict, prefix: str = "") -> dict[str, Any]:
            result: dict[str, Any] = {}
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    result.update(_flatten_to_dict(v, full_key))
                else:
                    result[full_key] = v
            return result

        old_flat = _flatten_to_dict(old_config)
        new_flat = _flatten_to_dict(new_config)

        for key, value in new_flat.items():
            if key not in old_flat:
                diff["added"][key] = value
            elif old_flat[key] != value:
                diff["changed"][key] = {"old": old_flat[key], "new": value}

        for key, value in old_flat.items():
            if key not in new_flat:
                diff["removed"][key] = value

        return diff

    def reset_to_defaults(self) -> int:
        """Reset all settings to defaults."""
        return self._cs.reset_all()
