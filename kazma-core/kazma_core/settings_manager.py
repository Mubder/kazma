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
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

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

DEFAULT_APPEARANCE: dict[str, Any] = {
    "theme": "dark",
    "accent_color": "#5e6ad2",
    "font_size": 14,
    "sidebar_position": "left",
    "custom_css": "",
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


from kazma_core.settings_providers import ProviderSettingsService  # re-export
from kazma_core.settings_mcp import MCPSettingsService  # re-export

__all__ = ["DEFAULT_APPEARANCE", "DEFAULT_MODEL_DEFAULTS", "DEFAULT_SHORTCUTS", "SettingsManager"]


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
        self.providers_service = ProviderSettingsService(config_store, self._registry)
        self.mcp_service = MCPSettingsService(config_store)

        # Register services on global Dependency Injection container
        from kazma_core.service_container import get_container
        try:
            get_container().register(ProviderSettingsService, self.providers_service)
            get_container().register(MCPSettingsService, self.mcp_service)
        except Exception as exc:
            logger.debug("[SettingsManager] Container registration skipped: %s", exc)

    # ══════════════════════════════════════════════════════════════════
    # PROVIDERS (Delegated to ProviderSettingsService)
    # ══════════════════════════════════════════════════════════════════

    def get_all_providers(self) -> list[dict[str, Any]]:
        """List all configured providers."""
        return self.providers_service.get_all_providers()

    def add_provider(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new provider."""
        return self.providers_service.add_provider(data)

    def delete_provider(self, name: str) -> None:
        """Delete a provider by name."""
        self.providers_service.delete_provider(name)

    def toggle_provider(self, name: str, enabled: bool) -> None:
        """Enable/disable a provider."""
        self.providers_service.toggle_provider(name, enabled)

    async def test_provider(self, name: str) -> dict[str, Any]:
        """Test a provider connection with a real HTTP call."""
        return await self.providers_service.test_provider(name)

    def get_provider_health(self, name: str) -> dict[str, Any]:
        """Get health status for a provider."""
        return self.providers_service.get_provider_health(name)

    def _update_provider_health(self, name: str, status: str) -> None:
        """Update provider health status in store."""
        self.providers_service._update_provider_health(name, status)

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
                async with httpx.AsyncClient(timeout=30.0) as client:
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
        return {
            "hitl_enabled": self._cs.get("safety.hitl_enabled", True),
            "require_approval_for": self._cs.get(
                "safety.require_approval_for", _default_danger
            ),
            "approval_timeout": self._cs.get("safety.approval_timeout", 60),
            "auto_deny_on_timeout": self._cs.get("safety.auto_deny_on_timeout", True),
        }

    def save_safety_settings(self, data: dict[str, Any]) -> None:
        """Update HITL safety settings."""
        for key, value in data.items():
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

        Persists to ConfigStore. The level can hot-apply; rotation/retention
        changes require a server restart (handlers are set at boot).
        """
        allowed_levels = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
        for key, value in data.items():
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

        # Hot-apply the level if possible (mutate existing loggers/handlers —
        # safe, unlike handler reconfiguration). Rotation changes are deferred
        # to next boot.
        try:
            level_name = data.get("level")
            if isinstance(level_name, str) and level_name.strip().upper() in allowed_levels:
                import logging as _logging

                _logging.getLogger().setLevel(level_name.strip().upper())
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    # PROXY PROVIDER (opt-in scraping resilience addon)
    # ══════════════════════════════════════════════════════════════════

    def get_proxy_settings(self) -> dict[str, Any]:
        """Get proxy provider config (opt-in scraping proxy).

        ``password`` is vault-encrypted at rest; ``get()`` decrypts it here.
        """
        return {
            "provider": self._cs.get("proxy.provider", "none"),
            "host": self._cs.get("proxy.host", "portal.anyip.io"),
            "port": self._cs.get("proxy.port", "1080"),
            "username": self._cs.get("proxy.username", ""),
            "password": self._cs.get("proxy.password", ""),
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
        allowed_networks = ("residential", "mobile", "mixed")
        for key, value in data.items():
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
            self._cs.set(f"proxy.{key}", value, category="proxy")

    async def test_proxy(self) -> dict[str, Any]:
        """Health-check the active proxy provider. Returns {success, exit_ip?}."""
        from kazma_core.proxy.registry import get_proxy_provider

        # Force a fresh provider read so a just-saved config is picked up.
        try:
            provider = get_proxy_provider()
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"Provider init failed: {exc}"}
        return await provider.test()

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

        self._cs.set("embedding.provider", provider, category="embedding")
        self._cs.set("embedding.model", model, category="embedding")
        self._cs.set("embedding.dim", int(dim), category="embedding")
        self._cs.set("embedding.base_url", base_url, category="embedding")
        self._cs.set("embedding.api_key_env", api_key_env, category="embedding")

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
            "max_context_tokens": self._cs.get("context.max_context_tokens", 128000),
            "context_strategy": self._cs.get("context.context_strategy", "sliding_window"),
            "summarization_threshold": self._cs.get("context.summarization_threshold", 0.8),
        }

    # ── Non-Stop & Self-Healing (agent.nonstop.*) ─────────────────────

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
            "tool_timeout_seconds": self._cs.get(
                "agent.nonstop.watchdog.tool_timeout_seconds", d.watchdog.tool_timeout_seconds
            ),
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
        """
        if "enabled" in data:
            self._cs.set("agent.nonstop.enabled", bool(data["enabled"]), category="agent")
        if "stall_threshold_seconds" in data:
            v = float(data["stall_threshold_seconds"])
            if v < 10 or v > 3600:
                raise ValueError("Stall threshold must be between 10 and 3600 seconds.")
            self._cs.set("agent.nonstop.watchdog.stall_threshold_seconds", v, category="agent")
        if "tool_timeout_seconds" in data:
            v = float(data["tool_timeout_seconds"])
            if v != 0 and (v < 5 or v > 3600):
                raise ValueError("Tool timeout must be 0 (disabled) or between 5 and 3600 seconds.")
            self._cs.set("agent.nonstop.watchdog.tool_timeout_seconds", v, category="agent")
        if "max_recovery_attempts" in data:
            v = int(data["max_recovery_attempts"])
            if v < 0 or v > 10:
                raise ValueError("Max recovery attempts must be between 0 and 10.")
            self._cs.set("agent.nonstop.healing.max_recovery_attempts", v, category="agent")
        if "backoff_base_seconds" in data:
            v = float(data["backoff_base_seconds"])
            if v < 0.5 or v > 600:
                raise ValueError("Backoff base must be between 0.5 and 600 seconds.")
            self._cs.set("agent.nonstop.healing.backoff_base_seconds", v, category="agent")
        if "backoff_max_seconds" in data:
            v = float(data["backoff_max_seconds"])
            if v < 1 or v > 3600:
                raise ValueError("Backoff max must be between 1 and 3600 seconds.")
            self._cs.set("agent.nonstop.healing.backoff_max_seconds", v, category="agent")
        if "failover_enabled" in data:
            self._cs.set("agent.nonstop.failover.enabled", bool(data["failover_enabled"]), category="agent")
        if "failover_chain" in data:
            chain = data["failover_chain"]
            if isinstance(chain, str):
                chain = [p.strip() for p in chain.split(",") if p.strip()]
            if not isinstance(chain, list) or len(chain) > 8:
                raise ValueError("Failover chain must be a list of at most 8 model ids.")
            self._cs.set("agent.nonstop.failover.chain", ",".join(str(m) for m in chain), category="agent")
        if "failover_cooldown_seconds" in data:
            v = float(data["failover_cooldown_seconds"])
            if v < 10 or v > 86400:
                raise ValueError("Failover cooldown must be between 10 and 86400 seconds.")
            self._cs.set("agent.nonstop.failover.cooldown_seconds", v, category="agent")
        if "ledger_enabled" in data:
            self._cs.set("agent.nonstop.ledger.enabled", bool(data["ledger_enabled"]), category="agent")

    def save_context_settings(self, data: dict[str, Any]) -> None:
        """Update context window settings.

        Writes only the 3 known keys explicitly — prevents the nested-prefix
        explosion that happens when the Alpine state object accumulates
        dotted ConfigStore keys from get_all() and then resends them.
        """
        if "max_context_tokens" in data:
            self._cs.set("context.max_context_tokens", data["max_context_tokens"], category="context")
        if "context_strategy" in data:
            self._cs.set("context.context_strategy", data["context_strategy"], category="context")
        if "summarization_threshold" in data:
            self._cs.set("context.summarization_threshold", data["summarization_threshold"], category="context")

    # ══════════════════════════════════════════════════════════════════
    # CONNECTORS
    # ══════════════════════════════════════════════════════════════════

    def get_connectors(self) -> dict[str, Any]:
        """Get all connector configurations."""
        platforms = ["telegram", "discord", "slack", "email", "webhook"]
        result: dict[str, Any] = {}
        for platform_name in platforms:
            result[platform_name] = self._cs.get_category(platform_name) if hasattr(self._cs, 'get_category') else {}
            # Also try the dotted key approach
            cat_data = {}
            for key_suffix in self._get_connector_keys(platform_name):
                val = self._cs.get(f"connectors.{platform_name}.{key_suffix}", "")
                if val:
                    cat_data[key_suffix] = val
            if cat_data:
                result[platform_name] = cat_data
        return result

    def _get_connector_keys(self, platform_name: str) -> list[str]:
        """Get the config keys for a connector platform."""
        key_map = {
            "telegram": ["token", "allowed_users", "webhook_url"],
            "discord": ["token", "guild_id"],
            "slack": ["token", "app_token", "workspace"],
            "email": ["smtp_host", "smtp_port", "username", "password", "imap_host"],
            "webhook": ["incoming_url", "outgoing_url", "secret"],
        }
        return key_map.get(platform_name, [])

    def save_connector(self, platform_name: str, data: dict[str, Any]) -> None:
        """Save a connector's configuration."""
        for key, value in data.items():
            self._cs.set(f"connectors.{platform_name}.{key}", value, category="connectors")

    async def test_connector(self, platform_name: str) -> dict[str, Any]:
        """Test a connector connection.

        Uses ConfigStore.get() so vault-backed secrets decrypt (get_all raw
        pointers must never be sent to Telegram/Discord).
        """
        import os

        token = str(self._cs.get(f"connectors.{platform_name}.token", "") or "").strip()
        if not token and platform_name == "telegram":
            token = (
                os.environ.get("TELEGRAM_BOT_TOKEN", "")
                or os.environ.get("TELEGRAM_TOKEN", "")
            ).strip()
        if not token:
            return {
                "success": False,
                "error": (
                    f"No token configured for {platform_name} "
                    f"(or vault could not decrypt connectors.{platform_name}.token)."
                ),
            }

        if platform_name == "telegram":
            token = token.strip().strip("\"'")
            # Docs-style paste only: leading "bot" + digits.
            if len(token) > 4 and token[:3].lower() == "bot" and token[3].isdigit():
                token = token[3:]
            return await self._test_http_connector(
                f"https://api.telegram.org/bot{token}/getMe",
                headers=None,
                name_key="result.username",
            )
        elif platform_name == "discord":
            return await self._test_http_connector(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {token}"},
                name_key="username",
            )
        else:
            return {"success": True, "message": f"Token configured for {platform_name}. Restart gateway to verify."}

    @staticmethod
    async def _test_http_connector(
        url: str,
        headers: dict[str, str] | None,
        name_key: str,
    ) -> dict[str, Any]:
        """Shared HTTP connector test for Telegram/Discord."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    # Navigate dotted key path (e.g. "result.username")
                    val: Any = data
                    for part in name_key.split("."):
                        val = val.get(part, "") if isinstance(val, dict) else ""
                    return {"success": True, "bot_name": val}
                return {"success": False, "error": f"HTTP {resp.status_code}"}
        except httpx.ConnectError:
            return {"success": False, "error": "Cannot connect to service"}
        except Exception as e:
            logger.debug("Connector test failed: %s", e)
            return {"success": False, "error": "Connection test failed"}

    def get_connector_status(self, platform_name: str) -> dict[str, Any]:
        """Check if a connector is configured and has a token."""
        token = self._cs.get(f"connectors.{platform_name}.token", "")
        return {"platform": platform_name, "configured": bool(token)}

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

    def toggle_mcp_server(self, name: str, enabled: bool) -> None:
        """Enable/disable an MCP server."""
        self.mcp_service.toggle_mcp_server(name, enabled)

    async def test_mcp_server(self, name: str) -> dict[str, Any]:
        """Test an MCP server connection."""
        return await self.mcp_service.test_mcp_server(name)

    def get_mcp_tools(self, server_name: str) -> list[dict[str, Any]]:
        """List tools for an MCP server."""
        return self.mcp_service.get_mcp_tools(server_name)

    # ══════════════════════════════════════════════════════════════════
    # SKILLS
    # ══════════════════════════════════════════════════════════════════

    def get_installed_skills(self) -> list[dict[str, Any]]:
        """List installed skills from the filesystem."""
        skills: list[dict[str, Any]] = []
        # Check skill directories
        try:
            from kazma_core.paths import installed_skills_dir, legacy_user_home

            skill_dirs = [
                installed_skills_dir(),
                legacy_user_home() / "skills",
                Path("skills"),
            ]
        except Exception:
            skill_dirs = [
                Path.home() / ".kazma" / "skills",
                Path("skills"),
            ]
        for skill_dir in skill_dirs:
            if not skill_dir.exists():
                continue
            for item in sorted(skill_dir.iterdir()):
                if item.is_dir() and (item / "SKILL.md").exists():
                    meta = self._parse_skill_meta(item / "SKILL.md")
                    meta["path"] = str(item)
                    enabled = self._cs.get(f"skills.{item.name}.enabled", True)
                    meta["enabled"] = enabled
                    skills.append(meta)
        # Also check config store for skill states
        return skills

    def _parse_skill_meta(self, path: Path) -> dict[str, Any]:
        """Parse SKILL.md frontmatter for metadata."""
        try:
            content = path.read_text()
            meta: dict[str, Any] = {"name": path.parent.name, "version": "", "description": "", "category": "", "author": ""}
            if content.startswith("---"):
                end = content.find("---", 3)
                if end > 0:
                    import yaml
                    frontmatter = yaml.safe_load(content[3:end])
                    if isinstance(frontmatter, dict):
                        meta.update(frontmatter)
            return meta
        except Exception as _e:
            logger.debug("Failed to parse skill frontmatter for %s: %s", path, _e)
            return {"name": path.parent.name, "description": ""}

    def uninstall_skill(self, skill_id: str) -> None:
        """Uninstall a skill."""
        self._cs.set(f"skills.{skill_id}.enabled", False, category="skills")

    def toggle_skill(self, skill_id: str, enabled: bool) -> None:
        """Enable/disable a skill."""
        self._cs.set(f"skills.{skill_id}.enabled", enabled, category="skills")

    def get_skill_config(self, skill_id: str) -> dict[str, Any]:
        """Get skill-specific settings."""
        return self._cs.get(f"skills.{skill_id}.config", {})

    def save_skill_config(self, skill_id: str, data: dict[str, Any]) -> None:
        """Update skill settings."""
        for key, value in data.items():
            self._cs.set(f"skills.{skill_id}.config.{key}", value, category="skills")

    # ══════════════════════════════════════════════════════════════════
    # APPEARANCE
    # ══════════════════════════════════════════════════════════════════

    def get_appearance(self) -> dict[str, Any]:
        """Get appearance settings."""
        result = dict(DEFAULT_APPEARANCE)
        for key in result:
            val = self._cs.get(f"appearance.{key}", None)
            if val is not None:
                result[key] = val
        return result

    def save_appearance(self, data: dict[str, Any]) -> None:
        """Update appearance settings."""
        for key, value in data.items():
            if value is not None:
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

    def get_account_info(self) -> dict[str, Any]:
        """Get account info."""
        return {
            "username": self._cs.get("account.username", "admin"),
            "created_at": self._cs.get("account.created_at", ""),
        }

    def change_password(self, old_password: str, new_password: str) -> dict[str, Any]:
        """Validate and update password using PBKDF2-SHA256 with salt."""
        stored_hash = self._cs.get("account.password_hash", "")
        if stored_hash:
            import hashlib
            import hmac as _hmac
            # Support both legacy SHA-256 and new PBKDF2 format
            if stored_hash.startswith("pbkdf2:"):
                # New format: pbkdf2:iterations:salt:hash
                parts = stored_hash.split(":")
                iterations = int(parts[1])
                salt = bytes.fromhex(parts[2])
                expected = bytes.fromhex(parts[3])
                derived = hashlib.pbkdf2_hmac("sha256", old_password.encode(), salt, iterations)
                if not _hmac.compare_digest(derived, expected):
                    return {"error": "Current password is incorrect"}
            else:
                # Legacy SHA-256 — verify then migrate
                import hashlib
                old_hash = hashlib.sha256(old_password.encode()).hexdigest()
                if not _hmac.compare_digest(old_hash, stored_hash):
                    return {"error": "Current password is incorrect"}
        if len(new_password) < 8:
            return {"error": "Password must be at least 8 characters"}
        # PBKDF2-SHA256 with random 16-byte salt, 600k iterations
        import hashlib
        salt = secrets.token_bytes(16)
        iterations = 600_000
        derived = hashlib.pbkdf2_hmac("sha256", new_password.encode(), salt, iterations)
        new_hash = f"pbkdf2:{iterations}:{salt.hex()}:{derived.hex()}"
        self._cs.set("account.password_hash", new_hash, category="account")
        return {"status": "ok"}

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

    def get_sessions(self) -> list[dict[str, Any]]:
        """List active sessions."""
        sessions = self._cs.get("account.sessions", [])
        if isinstance(sessions, str):
            try:
                sessions = json.loads(sessions)
            except (json.JSONDecodeError, TypeError):
                sessions = []
        return sessions if isinstance(sessions, list) else []

    # ══════════════════════════════════════════════════════════════════
    # TOOLS
    # ══════════════════════════════════════════════════════════════════

    def get_tool_registry(self) -> list[dict[str, Any]]:
        """Get all registered tools with metadata."""
        tools: list[dict[str, Any]] = []
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
                    "enabled": self._cs.get(f"tools.{tool['name']}.enabled", True),
                    "parameters": tool_obj.input_schema if tool_obj else {},
                }
                tools.append(entry)
        except ImportError:
            logger.warning("Could not import LocalToolRegistry")
        return tools

    def toggle_tool(self, tool_name: str, enabled: bool) -> None:
        """Enable/disable a tool."""
        self._cs.set(f"tools.{tool_name}.enabled", enabled, category="tools")

    async def test_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run a tool with test arguments."""
        try:
            from kazma_core.agent.tool_registry import LocalToolRegistry
            registry = LocalToolRegistry(include_builtins=True)
            result = await registry.execute(tool_name, arguments)
            return result
        except Exception as e:
            return {"content": f"Error: {e}", "is_error": True}

    def get_tool_config(self, tool_name: str) -> dict[str, Any]:
        """Get tool-specific settings."""
        return self._cs.get(f"tools.{tool_name}.config", {})

    def save_tool_config(self, tool_name: str, data: dict[str, Any]) -> None:
        """Update tool settings."""
        for key, value in data.items():
            self._cs.set(f"tools.{tool_name}.config.{key}", value, category="tools")

    # ══════════════════════════════════════════════════════════════════
    # SYSTEM
    # ══════════════════════════════════════════════════════════════════

    def get_logs(self, lines: int = 100) -> dict[str, Any]:
        """Read recent log entries."""
        log_paths = [
            Path("kazma-data/kazma.log"),
            Path.cwd() / "kazma.log",
        ]
        for log_path in log_paths:
            if log_path.exists():
                try:
                    all_lines = log_path.read_text(errors="replace").splitlines()
                    return {"lines": all_lines[-lines:], "total": len(all_lines), "path": str(log_path)}
                except Exception as e:
                    return {"lines": [f"Error reading log: {e}"], "total": 0}
        return {"lines": ["No log file found"], "total": 0}

    def create_backup(self) -> str:
        """Export full config as YAML."""
        return self.export_config("yaml")

    def restore_backup(self, data: str) -> int:
        """Import backup data."""
        return self.import_config(data, "yaml")

    def system_reset(self) -> int:
        """Full reset of all settings."""
        return self._cs.reset_all()

    def check_updates(self) -> dict[str, Any]:
        """Check for new Kazma versions."""
        current_version = "0.5.0"
        try:
            import kazma_core
            current_version = getattr(kazma_core, "__version__", current_version)
        except (ImportError, AttributeError):
            pass

        # Try PyPI
        try:
            import httpx
            resp = httpx.get("https://pypi.org/pypi/kazma/json", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json()
                latest = data.get("info", {}).get("version", current_version)
                return {
                    "current_version": current_version,
                    "latest_version": latest,
                    "update_available": latest != current_version,
                }
        except Exception as exc:
            logger.debug("PyPI version check failed: %s", exc)

        return {
            "current_version": current_version,
            "latest_version": current_version,
            "update_available": False,
        }

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

    def export_config(self, fmt: str = "yaml", mask_secrets: bool = False) -> str:
        """Export configuration as YAML or JSON.

        When ``mask_secrets=True``, sensitive values (api keys, tokens,
        passwords) are replaced with ``***`` to prevent leaking through
        browser download dialogs or network logs.
        """
        if fmt == "json":
            all_settings = self._cs.get_all()
            if mask_secrets:
                self._mask_secrets_in_dict(all_settings)
            return json.dumps(all_settings, indent=2, ensure_ascii=False)
        # YAML export
        if mask_secrets:
            all_settings = self._cs.get_all()
            self._mask_secrets_in_dict(all_settings)
            import yaml as _yaml
            return _yaml.dump(all_settings, default_flow_style=False, allow_unicode=True)
        return self._cs.export_yaml()

    @staticmethod
    def _mask_secrets_in_dict(data: dict) -> None:
        """Recursively replace values whose key looks like a secret with '***'."""
        _SENSITIVE = ("api_key", "token", "secret", "password", "passphrase")
        for category, settings_dict in data.items():
            if not isinstance(settings_dict, dict):
                continue
            for key in list(settings_dict.keys()):
                if any(frag in key.lower() for frag in _SENSITIVE):
                    raw = settings_dict[key]
                    if isinstance(raw, str) and raw.strip():
                        try:
                            import json as _json
                            parsed = _json.loads(raw)
                            if isinstance(parsed, dict):
                                for sub_k in list(parsed.keys()):
                                    if isinstance(parsed[sub_k], str) and parsed[sub_k].strip():
                                        parsed[sub_k] = "***"
                                settings_dict[key] = _json.dumps(parsed)
                                continue
                        except (ValueError, TypeError):
                            pass
                        settings_dict[key] = "***"

    def import_config(self, data: str, fmt: str = "yaml", selective: bool = False, sections: list[str] | None = None) -> int:
        """Import configuration from YAML or JSON string."""
        if fmt == "json":
            try:
                parsed = json.loads(data)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON: %s", e)
                return 0
        else:
            import yaml
            parsed = yaml.safe_load(data)

        if not isinstance(parsed, dict):
            return 0

        if selective and sections:
            # Filter to only selected sections
            filtered = {}
            for section in sections:
                if section in parsed:
                    filtered[section] = parsed[section]
            parsed = filtered

        items: list[tuple[str, Any, str]] = []

        def _flatten_to_items(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _flatten_to_items(v, full_key)
                else:
                    cat = prefix.split(".")[0] if prefix else "general"
                    items.append((full_key, v, cat))

        _flatten_to_items(parsed)
        return self._cs.batch_set(items)

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
