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
}

_START_TIME = time.monotonic()


class SettingsManager:
    """Comprehensive settings manager wrapping ConfigStore."""

    def __init__(self, config_store: Any) -> None:
        self._cs = config_store
        # Always create a local ModelRegistry from the provided config_store.
        # The global singleton may point to a different ConfigStore instance
        # (e.g. in tests), so we must not use it here.
        from kazma_core.model_registry import ModelRegistry

        self._registry = ModelRegistry(config_store)

    # ══════════════════════════════════════════════════════════════════
    # PROVIDERS
    # ══════════════════════════════════════════════════════════════════

    def get_all_providers(self) -> list[dict[str, Any]]:
        """List all configured providers."""
        return self._registry.list_providers()

    def add_provider(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new provider."""
        return self._registry.upsert_provider(data)

    def delete_provider(self, name: str) -> None:
        """Delete a provider by name."""
        self._registry.delete_provider(name)

    def toggle_provider(self, name: str, enabled: bool) -> None:
        """Enable/disable a provider."""
        self._registry.toggle_provider(name, enabled)

    async def test_provider(self, name: str) -> dict[str, Any]:
        """Test a provider connection with a real HTTP call."""
        providers = self.get_all_providers()
        provider = None
        for p in providers:
            if p.get("name") == name:
                provider = p
                break
        if not provider:
            return {"success": False, "error": f"Provider '{name}' not found"}

        base_url = provider.get("base_url", "")
        api_key = provider.get("api_key", "")
        if not base_url:
            return {"success": False, "error": "No base URL configured"}

        import httpx

        start = time.monotonic()
        try:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{base_url}/models", headers=headers)
                latency = int((time.monotonic() - start) * 1000)
                if resp.status_code == 200:
                    # Update health
                    self._update_provider_health(name, "healthy")
                    return {"success": True, "latency_ms": latency, "status_code": resp.status_code}
                else:
                    self._update_provider_health(name, "degraded")
                    return {"success": False, "error": f"HTTP {resp.status_code}", "latency_ms": latency}
        except httpx.ConnectError:
            self._update_provider_health(name, "down")
            return {"success": False, "error": f"Cannot connect to {base_url}"}
        except Exception as e:
            self._update_provider_health(name, "down")
            return {"success": False, "error": str(e)}

    def get_provider_health(self, name: str) -> dict[str, Any]:
        """Get health status for a provider."""
        health = self._cs.get(f"providers.health.{name}", "unknown")
        last_check = self._cs.get(f"providers.health.{name}.last_check", "")
        return {"name": name, "health": health, "last_check": last_check}

    def _update_provider_health(self, name: str, status: str) -> None:
        """Update provider health status in store."""
        self._cs.set(f"providers.health.{name}", status, category="providers")
        self._cs.set(f"providers.health.{name}.last_check", datetime.now(UTC).isoformat(), category="providers")
        self._registry.set_provider_health(name, status)

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
        return {
            "name": self._cs.get("agent.name", "kazma"),
            "language": self._cs.get("agent.language", "ar"),
            "system_prompt": self._cs.get("agent.system_prompt", ""),
            "personality": self._cs.get("agent.personality", "default"),
        }

    def save_agent_config(self, data: dict[str, Any]) -> None:
        """Save agent configuration."""
        for key, value in data.items():
            if value is not None:
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
        """Get HITL safety settings."""
        return {
            "hitl_enabled": self._cs.get("safety.hitl_enabled", True),
            "require_approval_for": self._cs.get("safety.require_approval_for", ["file_write", "file_delete", "shell_exec"]),
            "approval_timeout": self._cs.get("safety.approval_timeout", 60),
            "auto_deny_on_timeout": self._cs.get("safety.auto_deny_on_timeout", True),
        }

    def save_safety_settings(self, data: dict[str, Any]) -> None:
        """Update HITL safety settings."""
        for key, value in data.items():
            self._cs.set(f"safety.{key}", value, category="safety")

    def get_context_settings(self) -> dict[str, Any]:
        """Get context window settings."""
        return {
            "max_context_tokens": self._cs.get("context.max_context_tokens", 128000),
            "context_strategy": self._cs.get("context.context_strategy", "sliding_window"),
            "summarization_threshold": self._cs.get("context.summarization_threshold", 0.8),
        }

    def save_context_settings(self, data: dict[str, Any]) -> None:
        """Update context window settings."""
        for key, value in data.items():
            self._cs.set(f"context.{key}", value, category="context")

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
        """Test a connector connection."""
        token = self._cs.get(f"connectors.{platform_name}.token", "")
        if not token:
            return {"success": False, "error": f"No token configured for {platform_name}"}

        if platform_name == "telegram":
            import httpx
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
                    if resp.status_code == 200:
                        data = resp.json()
                        return {"success": True, "bot_name": data.get("result", {}).get("username", "")}
                    return {"success": False, "error": f"HTTP {resp.status_code}"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        elif platform_name == "discord":
            import httpx
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        "https://discord.com/api/v10/users/@me",
                        headers={"Authorization": f"Bot {token}"},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        return {"success": True, "bot_name": data.get("username", "")}
                    return {"success": False, "error": f"HTTP {resp.status_code}"}
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            return {"success": True, "message": f"Token configured for {platform_name}. Restart gateway to verify."}

    def get_connector_status(self, platform_name: str) -> dict[str, Any]:
        """Check if a connector is configured and has a token."""
        token = self._cs.get(f"connectors.{platform_name}.token", "")
        return {"platform": platform_name, "configured": bool(token)}

    # ══════════════════════════════════════════════════════════════════
    # MCP
    # ══════════════════════════════════════════════════════════════════

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        """List all MCP servers with status."""
        servers = self._cs.get("mcp.servers", [])
        if isinstance(servers, str):
            try:
                servers = json.loads(servers)
            except (json.JSONDecodeError, TypeError):
                servers = []
        return servers if isinstance(servers, list) else []

    def add_mcp_server(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new MCP server."""
        servers = self.get_mcp_servers()
        name = data.get("name", "")
        if not name:
            return {"error": "Server name is required"}
        # Check for duplicates
        for s in servers:
            if s.get("name") == name:
                s.update(data)
                self._cs.set("mcp.servers", json.dumps(servers), category="mcp")
                return s
        server = {
            "name": name,
            "transport": data.get("transport", "stdio"),
            "command": data.get("command", []),
            "url": data.get("url", ""),
            "env": data.get("env", {}),
            "enabled": True,
            "connected": False,
            "tool_count": 0,
            "tools": [],
        }
        servers.append(server)
        self._cs.set("mcp.servers", json.dumps(servers), category="mcp")
        return server

    def delete_mcp_server(self, name: str) -> None:
        """Remove an MCP server."""
        servers = self.get_mcp_servers()
        servers = [s for s in servers if s.get("name") != name]
        self._cs.set("mcp.servers", json.dumps(servers), category="mcp")

    def toggle_mcp_server(self, name: str, enabled: bool) -> None:
        """Enable/disable an MCP server."""
        servers = self.get_mcp_servers()
        for s in servers:
            if s.get("name") == name:
                s["enabled"] = enabled
                break
        self._cs.set("mcp.servers", json.dumps(servers), category="mcp")

    async def test_mcp_server(self, name: str) -> dict[str, Any]:
        """Test an MCP server connection."""
        servers = self.get_mcp_servers()
        server = None
        for s in servers:
            if s.get("name") == name:
                server = s
                break
        if not server:
            return {"success": False, "error": f"Server '{name}' not found"}

        try:
            from kazma_core.mcp.manager import AsyncMCPManager
            manager = AsyncMCPManager()
            count = await manager.connect_from_config([server])
            tool_schemas = manager.get_all_tool_schemas()
            tool_names = [t.get("function", {}).get("name", "") for t in tool_schemas]
            await manager.shutdown()
            return {"success": True, "tool_count": count, "tools": tool_names[:20]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_mcp_tools(self, server_name: str) -> list[dict[str, Any]]:
        """List tools for an MCP server."""
        servers = self.get_mcp_servers()
        for s in servers:
            if s.get("name") == server_name:
                return s.get("tools", [])
        return []

    # ══════════════════════════════════════════════════════════════════
    # SKILLS
    # ══════════════════════════════════════════════════════════════════

    def get_installed_skills(self) -> list[dict[str, Any]]:
        """List installed skills from the filesystem."""
        skills: list[dict[str, Any]] = []
        # Check skill directories
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
        except Exception:
            return {"name": path.parent.name, "description": ""}

    def install_skill(self, skill_id: str) -> dict[str, Any]:
        """Install a skill (stub — marketplace integration pending)."""
        return {"status": "pending", "message": f"Skill marketplace for '{skill_id}' not yet implemented"}

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
        for action in DEFAULT_SHORTCUTS:
            self._cs.delete(f"shortcuts.{action}")
        self._cs.set("shortcuts", json.dumps(DEFAULT_SHORTCUTS), category="shortcuts")

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
        """List API tokens."""
        tokens = self._cs.get("account.tokens", [])
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except (json.JSONDecodeError, TypeError):
                tokens = []
        return tokens if isinstance(tokens, list) else []

    def create_api_token(self, name: str) -> dict[str, Any]:
        """Generate a new API token."""
        token = f"kazma_{secrets.token_hex(32)}"
        import hashlib
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        token_entry = {
            "id": secrets.token_hex(8),
            "name": name,
            "token_prefix": token[:16],
            "token_hash": token_hash,
            "created_at": datetime.now(UTC).isoformat(),
            "expires_days": 90,
        }
        tokens = self.get_api_tokens()
        tokens.append(token_entry)
        self._cs.set("account.tokens", json.dumps(tokens), category="account")
        return {"status": "ok", "token": token, "id": token_entry["id"]}

    def revoke_api_token(self, token_id: str) -> None:
        """Revoke an API token."""
        tokens = self.get_api_tokens()
        tokens = [t for t in tokens if t.get("id") != token_id]
        self._cs.set("account.tokens", json.dumps(tokens), category="account")

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
            Path.home() / ".kazma" / "kazma.log",
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
        current_version = "0.2.0"
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
            except Exception:
                result["disk_free_gb"] = "N/A"
            result["cpu_percent"] = "N/A (install psutil)"
            result["memory_mb"] = "N/A (install psutil)"

        return result

    # ══════════════════════════════════════════════════════════════════
    # IMPORT / EXPORT
    # ══════════════════════════════════════════════════════════════════

    def export_config(self, fmt: str = "yaml") -> str:
        """Export configuration as YAML or JSON."""
        if fmt == "json":
            all_settings = self._cs.get_all()
            return json.dumps(all_settings, indent=2, ensure_ascii=False)
        return self._cs.export_yaml()

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

        def _flatten(d: dict, prefix: str = "") -> None:
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _flatten(v, full_key)
                else:
                    cat = prefix.split(".")[0] if prefix else "general"
                    items.append((full_key, v, cat))

        _flatten(parsed)
        return self._cs.batch_set(items)

    def get_config_diff(self, old_config: dict[str, Any], new_config: dict[str, Any]) -> dict[str, Any]:
        """Compare two config dicts and return differences."""
        diff: dict[str, Any] = {"added": {}, "removed": {}, "changed": {}}

        def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
            result: dict[str, Any] = {}
            for k, v in d.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    result.update(_flatten(v, full_key))
                else:
                    result[full_key] = v
            return result

        old_flat = _flatten(old_config)
        new_flat = _flatten(new_config)

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
