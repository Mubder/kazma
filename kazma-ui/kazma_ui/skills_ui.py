"""Skills management UI routes for the Kazma WebUI.

Provides a visual interface for browsing, installing, enabling/disabling,
and validating skills from the Kazma Hub.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from kazma_ui.models import SkillInstallRequest, SkillToggleRequest

if TYPE_CHECKING:
    from kazma_core.agent import KazmaAgent

logger = logging.getLogger(__name__)


def create_skills_router(agent: KazmaAgent, templates: Jinja2Templates) -> APIRouter:
    """Create the skills management router."""

    router = APIRouter(tags=["skills"])

    async def _get_installed_skills() -> list[dict[str, Any]]:
        """Get list of installed skills from hub registry + local ToolRegistry."""
        skills: list[dict[str, Any]] = []

        # Local tools from ToolRegistry
        try:
            from kazma_core.tools.registry import get_tool_registry
            reg = get_tool_registry()
            tools = reg.list_tools()
            for t in tools:
                skills.append({
                    "id": t["id"],
                    "name": t["name"],
                    "version": "0.1.0",
                    "description": t.get("description", ""),
                    "author": "kazma-core",
                    "enabled": t.get("enabled", True),
                    "security_score": t.get("security_score", 100),
                    "certification_level": t.get("certification_level", "basic"),
                    "capabilities": t.get("capabilities", []),
                    "tags": t.get("tags", []),
                })
        except Exception:
            pass

        # Hub-registered skills
        try:
            from kazma_core.hub.registry import KazmaHub

            hub = KazmaHub()
            manifests = await hub.list_installed()
            await hub.close()
            for m in manifests:
                skills.append({
                    "id": f"kazma-hub://{m.data.get('author', '')}/{m.data.get('name', '')}@{m.data.get('version', '')}",
                    "name": m.data.get("name", ""),
                    "version": m.data.get("version", ""),
                    "description": m.data.get("description", ""),
                    "author": m.data.get("author", ""),
                    "enabled": True,
                    "security_score": 100,
                    "certification_level": "basic",
                    "capabilities": m.data.get("capabilities", []),
                    "tags": m.data.get("tags", []),
                })
        except Exception:
            pass
        return skills

    async def _search_hub(query: str = "") -> list[dict[str, Any]]:
        """Search the Kazma Hub for skills."""
        try:
            from kazma_core.hub.registry import KazmaHub

            hub = KazmaHub()
            manifests = await hub.search(query=query if query else None)
            await hub.close()
            return [
                {
                    "id": f"kazma-hub://{m.data.get('author', '')}/{m.data.get('name', '')}@{m.data.get('version', '')}",
                    "name": m.data.get("name", ""),
                    "version": m.data.get("version", ""),
                    "description": m.data.get("description", ""),
                    "author": m.data.get("author", ""),
                    "capabilities": m.data.get("capabilities", []),
                }
                for m in manifests
            ]
        except Exception as e:
            logger.warning("Failed to search hub: %s", e)
            return []

    @router.get("/skills", response_class=HTMLResponse)
    async def skills_page(request: Request) -> HTMLResponse:
        """Render the skills management page."""
        installed = await _get_installed_skills()
        return templates.TemplateResponse(
            request,
            "skills.html",
            {
                "installed_skills": installed,
                "hub_results": [],
                "config": agent.config,
                "active_page": "skills",
            },
        )

    @router.get("/api/skills")
    async def api_list_skills() -> list[dict[str, Any]]:
        """List installed skills."""
        return await _get_installed_skills()

    @router.get("/api/skills/hub/search")
    async def api_search_hub(q: str = "") -> list[dict[str, Any]]:
        """Search the Kazma Hub."""
        return await _search_hub(q)

    @router.post("/api/skills/install")
    async def api_install_skill(req: SkillInstallRequest) -> dict[str, str]:
        """Install a skill from the hub."""
        try:
            from kazma_core.hub.registry import KazmaHub

            hub = KazmaHub()
            path = await hub.install(req.skill_id)
            await hub.close()
            return {"status": "ok", "path": str(path)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @router.post("/api/skills/uninstall")
    async def api_uninstall_skill(req: SkillInstallRequest) -> dict[str, str]:
        """Uninstall a skill."""
        try:
            from kazma_core.hub.registry import KazmaHub

            hub = KazmaHub()
            removed = await hub.unregister(req.skill_id)
            await hub.close()
            return {"status": "ok" if removed else "not_found"}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @router.post("/api/skills/toggle")
    async def api_toggle_skill(req: SkillToggleRequest) -> dict[str, str]:
        """Enable or disable a skill."""
        # Store toggle state in config
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        store.set(f"skills.enabled.{req.skill_id}", req.enabled, category="skills")
        store.close()
        return {"status": "ok", "enabled": str(req.enabled)}

    @router.post("/api/skills/validate")
    async def api_validate_skill(request: Request) -> dict[str, Any]:
        """Validate a local skill directory."""
        body = await request.json()
        skill_path = body.get("path", "")
        if not skill_path:
            return {"error": "No path provided"}

        try:
            from pathlib import Path

            from kazma_core.hub.validator import SkillValidator

            validator = SkillValidator()
            result = await validator.validate(Path(skill_path))
            return {
                "passed": result.passed,
                "score": result.score,
                "errors": result.errors,
                "warnings": result.warnings,
            }
        except Exception as e:
            return {"error": str(e)}

    return router
