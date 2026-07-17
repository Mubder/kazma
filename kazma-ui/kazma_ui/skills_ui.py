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
        """Get list of installed skills from native skills dir + ToolRegistry + hub."""
        skills: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        # ── 1. Native skills (kazma_skills/native/*) — the primary skill source.
        # Each skill has a skill_manifest.yaml with rich metadata.
        try:
            import yaml
            from pathlib import Path

            try:
                from kazma_skills.native_loader import NativeSkillLoader
                native_dir = Path(NativeSkillLoader.__module__).resolve()
                # Resolve the actual native/ directory from the loader's source.
                import kazma_skills.native_loader as _nsm
                native_dir = Path(_nsm.__file__).resolve().parent / "native"
            except Exception:
                native_dir = None

            if native_dir and native_dir.is_dir():
                for skill_dir in sorted(native_dir.iterdir()):
                    if not skill_dir.is_dir() or skill_dir.name.startswith("_"):
                        continue
                    manifest_path = skill_dir / "skill_manifest.yaml"
                    if not manifest_path.exists():
                        continue
                    try:
                        manifest = yaml.safe_load(
                            manifest_path.read_text(encoding="utf-8")
                        ) or {}
                    except Exception:
                        continue
                    name = manifest.get("name", skill_dir.name)
                    if name in seen_names:
                        continue
                    seen_names.add(name)
                    skills.append({
                        "id": f"native:{skill_dir.name}",
                        "name": name,
                        "version": manifest.get("version", "1.0.0"),
                        "description": manifest.get("description", ""),
                        "author": manifest.get("author", "kazma"),
                        "enabled": True,
                        "security_score": manifest.get("security_score", 100),
                        "certification_level": manifest.get("certification_level", "native"),
                        "capabilities": manifest.get("capabilities", []),
                        "tags": manifest.get("tags", ["native"]),
                        "icon": manifest.get("icon", ""),
                        "arabic_name": manifest.get("arabic_name", ""),
                    })
        except Exception as exc:
            logger.warning("Native skills scan failed: %s", exc)

        # ── 2. Agent LocalToolRegistry — built-in tools (file_read, web_search, …)
        try:
            from kazma_core.agent.tool_registry import get_tool_registry
            reg = get_tool_registry()
            tools = reg.list_tools()
            for t in tools:
                name = t.get("name") or t.get("id") or "unknown"
                if name in seen_names:
                    continue
                seen_names.add(name)
                skills.append({
                    "id": f"tool:{name}",
                    "name": name,
                    "version": "0.1.0",
                    "description": t.get("description", ""),
                    "author": "kazma-core",
                    "enabled": t.get("enabled", True),
                    "security_score": t.get("security_score", 100),
                    "certification_level": t.get("certification_level", "built-in"),
                    "capabilities": t.get("capabilities", []),
                    "tags": t.get("tags", ["built-in"]),
                })
        except Exception as exc:
            logger.debug("Tool registry skills load failed: %s", exc)

        # ── 3. Hub-registered skills (remote marketplace installs)
        try:
            from kazma_core.hub.registry import KazmaHub

            hub = KazmaHub()
            manifests = await hub.list_installed()
            await hub.close()
            for m in manifests:
                name = m.data.get("name", "")
                if name in seen_names:
                    continue
                seen_names.add(name)
                skills.append({
                    "id": f"kazma-hub://{m.data.get('author', '')}/{name}@{m.data.get('version', '')}",
                    "name": name,
                    "version": m.data.get("version", ""),
                    "description": m.data.get("description", ""),
                    "author": m.data.get("author", ""),
                    "enabled": True,
                    "security_score": 100,
                    "certification_level": "basic",
                    "capabilities": m.data.get("capabilities", []),
                    "tags": m.data.get("tags", ["hub"]),
                })
        except Exception as exc:
            logger.debug("Hub skills load failed: %s", exc)
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
        except Exception:
            return {"status": "error", "error": "Internal error"}

    @router.post("/api/skills/uninstall")
    async def api_uninstall_skill(req: SkillInstallRequest) -> dict[str, str]:
        """Uninstall a skill."""
        try:
            from kazma_core.hub.registry import KazmaHub

            hub = KazmaHub()
            removed = await hub.unregister(req.skill_id)
            await hub.close()
            return {"status": "ok" if removed else "not_found"}
        except Exception:
            return {"status": "error", "error": "Internal error"}

    @router.post("/api/skills/toggle")
    async def api_toggle_skill(req: SkillToggleRequest) -> dict[str, str]:
        """Enable or disable a skill."""
        # Store toggle state in config
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        store.set(f"skills.enabled.{req.skill_id}", req.enabled, category="skills")
        # Do NOT close the shared ConfigStore singleton — it would break
        # all other components that use it.
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

            # Restrict to skills directory to prevent path traversal
            skills_root = Path("skills").resolve()
            candidate = Path(skill_path).resolve()
            try:
                candidate.relative_to(skills_root)
            except ValueError:
                return {"error": "Path must be within the skills directory"}

            validator = SkillValidator()
            result = await validator.validate(candidate)
            return {
                "passed": result.passed,
                "score": result.score,
                "errors": result.errors,
                "warnings": result.warnings,
            }
        except Exception:
            return {"error": "Internal error"}

    return router
