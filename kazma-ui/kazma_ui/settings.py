"""Settings management routes for the Kazma WebUI.

Provides a comprehensive 12-tab settings UI with real API endpoints
for providers, models, agent config, connectors, MCP, skills,
appearance, shortcuts, account, tools, system, and import/export.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from kazma_ui.models import (
    AgentConfigUpdate,
    AppearanceUpdate,
    ConnectorConfigUpdate,
    ConnectorTestRequest,
    ImportConfigRequest,
    MCPServerAddRequest,
    MCPServerToggleRequest,
    ModelCompareRequest,
    ModelDefaultUpdate,
    ModelTestRequest,
    PasswordChange,
    ProviderAddRequest,
    ProviderToggleRequest,
    SettingsUpdate,
    ShortcutUpdate,
)

if TYPE_CHECKING:
    from kazma_core.agent import KazmaAgent
    from kazma_core.config_store import ConfigStore

logger = logging.getLogger(__name__)


class SettingsRouterBuilder:
    """Builder that decomposes the massive settings router into modular sub-routers."""

    def __init__(self, agent: KazmaAgent, config_store: ConfigStore, templates: Jinja2Templates) -> None:
        self.agent = agent
        self.config_store = config_store
        self.templates = templates

        self.router = APIRouter(tags=["settings"])
        self.providers_router = APIRouter()
        self.models_router = APIRouter()
        self.mcp_router = APIRouter()
        self.general_router = APIRouter()

        # Lazily initialized SettingsManager
        self._sm = None

    def _get_sm(self):
        if self._sm is None:
            from kazma_core.settings_manager import SettingsManager
            self._sm = SettingsManager(self.config_store)
        return self._sm

    def _build_general_routes(self) -> None:
        router = self.general_router
        _get_sm = self._get_sm
        agent = self.agent
        config_store = self.config_store
        templates = self.templates

        @router.get("/settings", response_class=HTMLResponse)
        async def settings_page(request: Request) -> HTMLResponse:
            """Render the settings page."""
            # Use the agent's facade method to avoid direct llm_config access.
            llm_cfg = agent.get_llm_config()
            try:
                from kazma_core.model_registry import get_model_registry
                reg = get_model_registry()
                profile = reg.get_active_profile()
                model_settings = {
                    "base_url": profile.get("base_url") or llm_cfg["base_url"],
                    "api_key": profile.get("api_key") or llm_cfg["api_key"],
                    "model": profile.get("model") or llm_cfg["model"],
                    "max_tokens": config_store.get("llm.max_tokens", llm_cfg["max_tokens"]),
                    "temperature": config_store.get("llm.temperature", llm_cfg["temperature"]),
                    "timeout": config_store.get("llm.timeout", llm_cfg["timeout"]),
                }
            except RuntimeError:
                model_settings = {
                    "base_url": config_store.get("llm.base_url", llm_cfg["base_url"]),
                    "api_key": "***" if config_store.get("llm.api_key", llm_cfg["api_key"]) else "",
                    "model": config_store.get("llm.model", llm_cfg["model"]),
                    "max_tokens": config_store.get("llm.max_tokens", llm_cfg["max_tokens"]),
                    "temperature": config_store.get("llm.temperature", llm_cfg["temperature"]),
                    "timeout": config_store.get("llm.timeout", llm_cfg["timeout"]),
                }
            agent_settings = {
                "name": config_store.get("agent.name", agent.config.name),
                "language": config_store.get("agent.language", agent.config.language),
                "system_prompt": config_store.get("agent.system_prompt", agent.system_prompt),
            }
            connector_settings = {
                "telegram_token": "***" if config_store.get("connectors.telegram.token", "") else "",
                "telegram_allowed_users": config_store.get("connectors.telegram.allowed_users", ""),
                "discord_token": "***" if config_store.get("connectors.discord.token", "") else "",
                "slack_token": "***" if config_store.get("connectors.slack.token", "") else "",
                "slack_app_token": "***" if config_store.get("connectors.slack.app_token", "") else "",
            }

            return templates.TemplateResponse(
                request,
                "settings.html",
                {
                    "model": model_settings,
                    "agent": agent_settings,
                    "connectors": connector_settings,
                    "config": agent.config,
                    "active_page": "settings",
                },
            )

        @router.get("/api/settings")
        async def api_get_all_settings() -> dict[str, dict[str, Any]]:
            """Get all settings grouped by category."""
            return config_store.get_all()

        @router.get("/api/settings/export")
        async def api_export_yaml(fmt: str = Query("yaml", alias="format")) -> Response:
            """Export settings as YAML or JSON file download."""
            sm = _get_sm()
            try:
                content = sm.export_config(fmt)
                media = "application/json" if fmt == "json" else "text/yaml"
                ext = "json" if fmt == "json" else "yaml"
                return Response(
                    content=content,
                    media_type=f"{media}; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=kazma-config.{ext}"},
                )
            except Exception as e:
                logger.error("Failed to export: %s", e)
                return Response(content="Error: Unable to export settings", media_type="text/plain", status_code=500)

        @router.put("/api/settings")
        async def api_update_settings(updates: list[SettingsUpdate]) -> dict[str, str]:
            """Update multiple settings at once (atomic batch)."""
            items = [(u.key, u.value, u.category) for u in updates]
            count = config_store.batch_set(items)
            return {"status": "ok", "updated": str(count)}

        @router.put("/api/settings/single")
        async def api_update_single(setting: SettingsUpdate) -> dict[str, str]:
            """Update a single setting."""
            config_store.set(setting.key, setting.value, category=setting.category)
            return {"status": "ok"}

        @router.delete("/api/settings/{key:path}")
        async def api_delete_setting(key: str) -> dict[str, str]:
            """Delete a setting (reverts to YAML default)."""
            config_store.delete(key)
            return {"status": "ok"}

        @router.put("/api/settings/agent")
        async def api_update_agent(req: AgentConfigUpdate) -> dict[str, str]:
            """Update agent configuration."""
            sm = _get_sm()
            data = {k: v for k, v in req.model_dump().items() if v is not None}
            sm.save_agent_config(data)
            return {"status": "ok"}

        @router.get("/api/settings/agent/personalities")
        async def api_get_personalities() -> list[dict[str, Any]]:
            """List available personality templates."""
            return _get_sm().get_personalities()

        @router.put("/api/settings/agent/safety")
        async def api_save_safety(req: dict[str, Any]) -> dict[str, str]:
            """Save safety/HITL settings."""
            _get_sm().save_safety_settings(req)
            return {"status": "ok"}

        @router.put("/api/settings/agent/context")
        async def api_save_context(req: dict[str, Any]) -> dict[str, str]:
            """Save context window settings."""
            _get_sm().save_context_settings(req)
            return {"status": "ok"}

        @router.get("/api/settings/connectors")
        async def api_get_connectors() -> dict[str, Any]:
            """Get all connector configurations."""
            return _get_sm().get_connectors()

        @router.put("/api/settings/connectors")
        async def api_save_connector(req: ConnectorConfigUpdate) -> dict[str, str]:
            """Save a connector's configuration."""
            _get_sm().save_connector(req.platform, req.settings)
            return {"status": "ok"}

        @router.post("/api/settings/connectors/test")
        async def api_test_connector(req: ConnectorTestRequest) -> dict[str, Any]:
            """Test a connector connection."""
            return await _get_sm().test_connector(req.platform)

        @router.get("/api/settings/skills")
        async def api_get_skills() -> list[dict[str, Any]]:
            """List installed skills."""
            return _get_sm().get_installed_skills()

        @router.put("/api/settings/skills/{skill_id}/toggle")
        async def api_toggle_skill(skill_id: str, req: dict[str, Any]) -> dict[str, str]:
            """Toggle skill enabled/disabled."""
            _get_sm().toggle_skill(skill_id, req.get("enabled", True))
            return {"status": "ok"}

        @router.delete("/api/settings/skills/{skill_id}")
        async def api_uninstall_skill(skill_id: str) -> dict[str, str]:
            """Uninstall a skill."""
            _get_sm().uninstall_skill(skill_id)
            return {"status": "ok"}

        @router.get("/api/settings/appearance")
        async def api_get_appearance() -> dict[str, Any]:
            """Get appearance settings."""
            return _get_sm().get_appearance()

        @router.put("/api/settings/appearance")
        async def api_save_appearance(req: AppearanceUpdate) -> dict[str, str]:
            """Save appearance settings."""
            data = {k: v for k, v in req.model_dump().items() if v is not None}
            _get_sm().save_appearance(data)
            return {"status": "ok"}

        @router.get("/api/settings/shortcuts")
        async def api_get_shortcuts() -> dict[str, str]:
            """Get all keyboard shortcuts."""
            sm = _get_sm()
            shortcuts = sm.get_shortcuts()
            logger.info("[Settings] Shortcuts: %s", shortcuts)
            return shortcuts

        @router.put("/api/settings/shortcuts")
        async def api_save_shortcut(req: ShortcutUpdate) -> dict[str, str]:
            """Update a single shortcut."""
            _get_sm().save_shortcut(req.action, req.keys)
            return {"status": "ok"}

        @router.post("/api/settings/shortcuts/reset")
        async def api_reset_shortcuts() -> dict[str, str]:
            """Reset shortcuts to defaults."""
            _get_sm().reset_shortcuts()
            return {"status": "ok"}

        @router.put("/api/settings/account/password")
        async def api_change_password(req: PasswordChange, request: Request) -> Response:
            """Change account password."""
            from fastapi.responses import JSONResponse
            result = _get_sm().change_password(req.old_password, req.new_password)
            if result.get("error"):
                return JSONResponse(result, status_code=400)
            return JSONResponse(result)

        @router.get("/api/settings/account/tokens")
        async def api_get_tokens() -> list[dict[str, Any]]:
            """List API tokens."""
            return _get_sm().get_api_tokens()

        @router.post("/api/settings/account/tokens")
        async def api_create_token(req: dict[str, Any]) -> dict[str, Any]:
            """Create an API token."""
            return _get_sm().create_api_token(req.get("name", "unnamed"))

        @router.delete("/api/settings/account/tokens/{token_id}")
        async def api_revoke_token(token_id: str) -> dict[str, str]:
            """Revoke an API token."""
            _get_sm().revoke_api_token(token_id)
            return {"status": "ok"}

        @router.get("/api/settings/account/sessions")
        async def api_get_sessions() -> list[dict[str, Any]]:
            """List active sessions."""
            return _get_sm().get_sessions()

        @router.get("/api/settings/tools")
        async def api_get_tools() -> list[dict[str, Any]]:
            """List all registered tools."""
            return _get_sm().get_tool_registry()

        @router.put("/api/settings/tools/{tool_name}/toggle")
        async def api_toggle_tool(tool_name: str, req: dict[str, Any]) -> dict[str, str]:
            """Toggle a tool enabled/disabled."""
            _get_sm().toggle_tool(tool_name, req.get("enabled", True))
            return {"status": "ok"}

        @router.post("/api/settings/tools/{tool_name}/test")
        async def api_test_tool(tool_name: str, req: dict[str, Any]) -> dict[str, Any]:
            """Test a tool with arguments."""
            return await _get_sm().test_tool(tool_name, req.get("arguments", {}))

        @router.get("/api/settings/system/logs")
        async def api_get_logs(lines: int = Query(100)) -> dict[str, Any]:
            """Get system logs."""
            return _get_sm().get_logs(lines)

        @router.get("/api/settings/system/backup")
        async def api_backup() -> Response:
            """Download a full config backup."""
            content = _get_sm().create_backup()
            return Response(
                content=content,
                media_type="text/yaml; charset=utf-8",
                headers={"Content-Disposition": "attachment; filename=kazma-backup.yaml"},
            )

        @router.post("/api/settings/system/restore")
        async def api_restore(request: Request) -> dict[str, str]:
            """Restore from backup."""
            body = await request.body()
            if len(body) > 10 * 1024 * 1024:  # 10MB limit
                return {"error": "Backup too large (max 10MB)"}
            # Validate the payload is valid YAML/JSON before restoring
            try:
                import yaml as _yaml

                _yaml.safe_load(body.decode("utf-8"))
            except Exception:
                try:
                    import json as _json

                    _json.loads(body.decode("utf-8"))
                except Exception:
                    return {"error": "Backup content is not valid YAML or JSON"}
            count = _get_sm().restore_backup(body.decode("utf-8"))
            return {"status": "ok", "restored": str(count)}

        @router.get("/api/settings/system/diagnostics")
        async def api_diagnostics() -> dict[str, Any]:
            """Get system diagnostics."""
            return _get_sm().get_diagnostics()

        @router.get("/api/settings/system/updates")
        async def api_check_updates() -> dict[str, Any]:
            """Check for updates."""
            return _get_sm().check_updates()

        @router.post("/api/settings/import")
        async def api_import_config(req: ImportConfigRequest) -> dict[str, str]:
            """Import configuration."""
            count = _get_sm().import_config(req.data, req.format, req.selective, req.sections)
            return {"status": "ok", "imported": str(count)}

        @router.post("/api/settings/reset")
        async def api_reset_settings(request: Request) -> dict[str, str]:
            """Reset all DB settings (reverts to YAML defaults).

            Requires a confirmation body ``{"confirm": "RESET"}`` to
            prevent accidental or malicious triggering.
            """
            try:
                body = await request.json()
            except Exception:
                return {"error": "Invalid JSON body. Expected {'confirm': 'RESET'}"}
            if body.get("confirm") != "RESET":
                return {"error": "Confirmation required. Send {\"confirm\": \"RESET\"} to confirm."}
            count = config_store.reset_all()
            config_store.invalidate_yaml_cache()
            return {"status": "ok", "reset": str(count)}

    def _build_providers_routes(self) -> None:
        router = self.providers_router
        _get_sm = self._get_sm

        @router.get("/api/settings/providers")
        async def api_get_providers() -> list[dict[str, Any]]:
            """List all configured providers."""
            return _get_sm().get_all_providers()

        @router.post("/api/settings/providers")
        async def api_add_provider(req: ProviderAddRequest) -> dict[str, Any]:
            """Add a new provider."""
            return _get_sm().add_provider(req.model_dump())

        @router.delete("/api/settings/providers/{name}")
        async def api_delete_provider(name: str) -> dict[str, str]:
            """Delete a provider."""
            _get_sm().delete_provider(name)
            return {"status": "ok"}

        @router.put("/api/settings/providers/{name}/toggle")
        async def api_toggle_provider(name: str, req: ProviderToggleRequest) -> dict[str, str]:
            """Toggle provider enabled/disabled."""
            _get_sm().toggle_provider(name, req.enabled)
            return {"status": "ok"}

        @router.post("/api/settings/providers/{name}/test")
        async def api_test_provider(name: str) -> dict[str, Any]:
            """Test a provider connection."""
            return await _get_sm().test_provider(name)

        @router.get("/api/settings/providers/{name}/health")
        async def api_provider_health(name: str) -> dict[str, Any]:
            """Get provider health status."""
            return _get_sm().get_provider_health(name)

    def _build_models_routes(self) -> None:
        router = self.models_router
        _get_sm = self._get_sm

        @router.post("/api/settings/test-model")
        async def api_test_model(req: ModelTestRequest) -> dict[str, Any]:
            """Test a model connection by sending a simple request."""
            import httpx

            try:
                # SSRF protection: validate the base_url
                from kazma_core.security.ssrf import SSRFError, validate_url
                validate_url(req.base_url)
            except SSRFError as exc:
                return {"success": False, "error": f"Blocked: {exc}"}
            except ValueError as exc:
                return {"success": False, "error": f"Invalid URL: {exc}"}
            except ImportError:
                return {"success": False, "error": "SSRF validation module not available"}

            try:
                headers = {
                    "Authorization": f"Bearer {req.api_key or 'not-needed'}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": req.model,
                    "messages": [{"role": "user", "content": "Say 'ok' in one word."}],
                    "max_tokens": 10,
                }
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(
                        f"{req.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    return {"success": True, "response": content, "model": data.get("model", req.model)}
            except httpx.ConnectError:
                return {"success": False, "error": f"Cannot connect to {req.base_url}"}
            except httpx.HTTPStatusError as e:
                return {"success": False, "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
            except Exception as e:
                logger.debug("Provider test failed: %s", e)
                return {"success": False, "error": "Connection test failed unexpectedly"}

        @router.get("/api/settings/models/registry")
        async def api_model_registry() -> list[dict[str, Any]]:
            """Get model registry."""
            return _get_sm().get_model_registry()

        @router.get("/api/settings/models/options")
        async def api_model_options() -> dict[str, Any]:
            """Get unified model/provider/profile options."""
            return _get_sm().get_unified_model_options()

        @router.get("/api/settings/models/defaults")
        async def api_model_defaults() -> dict[str, str]:
            """Get default models per task type."""
            return _get_sm().get_model_defaults()

        @router.put("/api/settings/models/defaults")
        async def api_set_model_default(req: ModelDefaultUpdate) -> dict[str, str]:
            """Set default model for a task type."""
            _get_sm().set_default_model(req.task_type, req.model_name)
            return {"status": "ok"}

        @router.get("/api/settings/models/usage")
        async def api_model_usage() -> dict[str, Any]:
            """Get token usage stats per model."""
            return _get_sm().get_model_usage()

        @router.post("/api/settings/models/compare")
        async def api_model_compare(req: ModelCompareRequest) -> list[dict[str, Any]]:
            """Compare models with the same prompt."""
            return await _get_sm().compare_models(req.prompt, req.models, req.temperature, req.max_tokens)

        @router.put("/api/settings/active_model")
        async def api_set_active_model(req: Request) -> dict[str, Any]:
            """Set the active chat model and persist it.

            Body: ``{"active_model": "deepseek-v4-pro"}`` or ``{"model": "..."}``
            """
            try:
                body = await req.json()
            except Exception as _e:
                logger.debug("Failed to parse model switch body: %s", _e)
                body = {}
            model = (body.get("active_model") or body.get("model") or "").strip()
            if not model:
                return {"error": "active_model is required", "status": "error"}
            try:
                from kazma_core.model_registry import get_model_registry

                registry = get_model_registry()
                registry.set_active_model(model)
                # Also persist for gateways that read registry.active_chat_model
                _get_sm()._cs.set("registry.active_chat_model", model, category="registry")
            except Exception as exc:
                logger.warning("[Settings] set_active_model failed: %s", exc)
            return {"active_model": model, "model": model, "status": "ok"}

    def _build_mcp_routes(self) -> None:
        router = self.mcp_router
        _get_sm = self._get_sm

        @router.get("/api/settings/mcp")
        async def api_get_mcp() -> list[dict[str, Any]]:
            """List all MCP servers."""
            return _get_sm().get_mcp_servers()

        @router.post("/api/settings/mcp")
        async def api_add_mcp(req: MCPServerAddRequest) -> dict[str, Any]:
            """Add an MCP server."""
            return _get_sm().add_mcp_server(req.model_dump())

        @router.delete("/api/settings/mcp/{name}")
        async def api_delete_mcp(name: str) -> dict[str, str]:
            """Delete an MCP server."""
            _get_sm().delete_mcp_server(name)
            return {"status": "ok"}

        @router.put("/api/settings/mcp/{name}/toggle")
        async def api_toggle_mcp(name: str, req: MCPServerToggleRequest) -> dict[str, str]:
            """Toggle MCP server enabled/disabled."""
            _get_sm().toggle_mcp_server(name, req.enabled)
            return {"status": "ok"}

        @router.post("/api/settings/mcp/{name}/test")
        async def api_test_mcp(name: str) -> dict[str, Any]:
            """Test an MCP server connection."""
            return await _get_sm().test_mcp_server(name)

    def build(self) -> APIRouter:
        self._build_general_routes()
        self._build_providers_routes()
        self._build_models_routes()
        self._build_mcp_routes()

        # Mount the decoupled sub-routers on the parent router
        self.router.include_router(self.general_router)
        self.router.include_router(self.providers_router)
        self.router.include_router(self.models_router)
        self.router.include_router(self.mcp_router)
        return self.router


def create_settings_router(agent: KazmaAgent, config_store: ConfigStore, templates: Jinja2Templates) -> APIRouter:
    """Create the settings router with agent and config store wired in."""
    builder = SettingsRouterBuilder(agent, config_store, templates)
    return builder.build()

