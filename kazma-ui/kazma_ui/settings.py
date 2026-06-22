"""Settings management routes for the Kazma WebUI.

Provides a full settings UI with live configuration updates,
model testing, and YAML import/export.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

from kazma_ui.models import MCPServerTestRequest, ModelTestRequest, SettingsUpdate

if TYPE_CHECKING:
    from kazma_core.agent import KazmaAgent
    from kazma_core.config_store import ConfigStore

logger = logging.getLogger(__name__)


def create_settings_router(agent: KazmaAgent, config_store: ConfigStore, templates: Jinja2Templates) -> APIRouter:
    """Create the settings router with agent and config store wired in."""

    router = APIRouter(tags=["settings"])

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        """Render the settings page."""
        # Gather current settings from config store
        model_settings = {
            "base_url": config_store.get("llm.base_url", agent.llm_config.base_url),
            "api_key": config_store.get("llm.api_key", agent.llm_config.api_key),
            "model": config_store.get("llm.model", agent.llm_config.model),
            "max_tokens": config_store.get("llm.max_tokens", agent.llm_config.max_tokens),
            "temperature": config_store.get("llm.temperature", agent.llm_config.temperature),
            "timeout": config_store.get("llm.timeout", agent.llm_config.timeout),
        }
        agent_settings = {
            "name": config_store.get("agent.name", agent.config.name),
            "language": config_store.get("agent.language", agent.config.language),
            "system_prompt": config_store.get("agent.system_prompt", agent.system_prompt),
        }
        cost_settings = {
            "max_cost": config_store.get("cost.max_cost", 0.50),
            "silence_window": config_store.get("cost.silence_window", 300),
        }

        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "model": model_settings,
                "agent": agent_settings,
                "cost": cost_settings,
                "config": agent.config,
            },
        )

    # ── Settings CRUD ─────────────────────────────────────────────────

    @router.get("/api/settings")
    async def api_get_all_settings() -> dict[str, dict[str, Any]]:
        """Get all settings grouped by category."""
        return config_store.get_all()

    @router.get("/api/settings/export")
    async def api_export_yaml() -> Response:
        """Export settings as YAML file download."""
        try:
            yaml_content = config_store.export_yaml()
            return Response(
                content=yaml_content,
                media_type="text/yaml; charset=utf-8",
                headers={"Content-Disposition": "attachment; filename=kazma.yaml"},
            )
        except Exception as e:
            logger.error(f"Failed to export YAML: {e}")
            return Response(
                content=f"Error: {str(e)}",
                media_type="text/plain",
                status_code=500,
            )

    @router.get("/api/settings/{category}")
    async def api_get_category(category: str) -> dict[str, Any]:
        """Get all settings for a category."""
        return config_store.get_category(category)

    @router.put("/api/settings")
    async def api_update_settings(updates: list[SettingsUpdate]) -> dict[str, str]:
        """Update multiple settings at once."""
        for update in updates:
            config_store.set(update.key, update.value, category=update.category)
        return {"status": "ok", "updated": str(len(updates))}

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

    # ── Model testing ─────────────────────────────────────────────────

    @router.post("/api/settings/test-model")
    async def api_test_model(req: ModelTestRequest) -> dict[str, Any]:
        """Test a model connection by sending a simple request."""
        import httpx

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
            return {"success": False, "error": str(e)}

    # ── MCP testing ───────────────────────────────────────────────────

    @router.post("/api/settings/test-mcp")
    async def api_test_mcp(req: MCPServerTestRequest) -> dict[str, Any]:
        """Test an MCP server connection."""
        from kazma_core.mcp_client import MCPClient, MCPServerConfig

        try:
            config = MCPServerConfig(
                name=req.name,
                transport=req.transport,
                command=req.command,
                url=req.url,
                env=req.env,
            )
            client = MCPClient()
            await client.connect(config)
            tools = await client.list_tools()
            await client.disconnect()
            return {
                "success": True,
                "tool_count": len(tools),
                "tools": [t.get("name", "") for t in tools[:10]],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── YAML import/export ────────────────────────────────────────────

    @router.post("/api/settings/import")
    async def api_import_yaml(request: Request) -> dict[str, str]:
        """Import settings from YAML."""
        body = await request.body()
        count = config_store.import_yaml(body.decode("utf-8"))
        return {"status": "ok", "imported": str(count)}

    @router.post("/api/settings/reset")
    async def api_reset_settings() -> dict[str, str]:
        """Reset all DB settings (reverts to YAML defaults)."""
        count = config_store.reset_all()
        config_store.invalidate_yaml_cache()
        return {"status": "ok", "reset": str(count)}

    return router
