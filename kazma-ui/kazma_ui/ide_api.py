"""IDE API — Web transport for the transport-agnostic ``IdeService``.

Thin FastAPI layer that delegates every operation to
``kazma_core.ide.get_ide_service()``. It adds **zero** business logic and
**no** parallel write/exec path: all mutating operations flow through the
same ``IdeService`` -> ``LocalToolRegistry`` -> HITL/safety chain used by the
agent and swarm.

Endpoints (all under ``/api/ide``):
  GET  /read?path=...              -> read_file
  POST /write   {path, content}    -> write_file  (HITL-gated)
  GET  /list?path=...              -> list_path
  GET  /grep?pattern=&glob=&limit= -> search
  POST /run     {command, timeout} -> run         (HITL-gated)
  POST /runfile {path, timeout}    -> run_file     (HITL-gated)
  POST /diff    {path, old, new}   -> diff
  POST /git     {subcommand, ...}  -> git          (HITL-gated)
  POST /swarm   {instruction, ...} -> send_to_swarm

Security:
  - Path traversal safety is enforced by ``IdeService.resolve()`` and the
    underlying tools; this layer additionally rejects empty/missing inputs
    and never constructs filesystem paths itself.
  - Failures return HTTP 200 with ``{"ok": false, "error": ...}`` so the UI
    can render the message uniformly.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Query

logger = logging.getLogger(__name__)


def create_ide_router() -> APIRouter:
    """Create and return the IDE API router."""

    router = APIRouter(prefix="/api/ide", tags=["ide"])

    def _service():
        from kazma_core.ide import get_ide_service

        svc = get_ide_service()
        svc.refresh_root()
        return svc

    # ── GET /api/ide/read ──────────────────────────────────────────────
    @router.get("/read")
    async def read_file(
        path: str = Query("", description="File path relative to workspace root"),
    ) -> dict[str, Any]:
        if not path or not path.strip():
            return {"ok": False, "error": "Missing 'path'", "content": ""}
        try:
            return await _service().read_file(path)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] read failed: %s", exc)
            return {"ok": False, "error": str(exc), "content": ""}

    # ── POST /api/ide/write ────────────────────────────────────────────
    @router.post("/write")
    async def write_file(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        path = str(payload.get("path", "")).strip()
        content = payload.get("content", "")
        if not path:
            return {"ok": False, "error": "Missing 'path'"}
        if content is None:
            content = ""
        try:
            return await _service().write_file(path, str(content))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] write failed: %s", exc)
            return {"ok": False, "error": str(exc), "path": path}

    # ── POST /api/ide/delete ───────────────────────────────────────────
    @router.post("/delete")
    async def delete_file(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        path = str(payload.get("path", "")).strip()
        if not path:
            return {"ok": False, "error": "Missing 'path'"}
        try:
            return await _service().delete_file(path)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] delete failed: %s", exc)
            return {"ok": False, "error": str(exc), "path": path}

    # ── GET /api/ide/list ──────────────────────────────────────────────
    @router.get("/list")
    async def list_path(
        path: str = Query("", description="Directory path relative to workspace root"),
    ) -> dict[str, Any]:
        try:
            return await _service().list_path(path or "")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] list failed: %s", exc)
            return {"ok": False, "error": str(exc), "files": []}

    # ── GET /api/ide/grep ──────────────────────────────────────────────
    @router.get("/grep")
    async def grep(
        pattern: str = Query("", description="Regex pattern to search for"),
        glob: str = Query("*.py", description="Filename glob filter"),
        limit: int = Query(50, ge=1, le=500),
    ) -> dict[str, Any]:
        if not pattern or not pattern.strip():
            return {"ok": False, "error": "Missing 'pattern'", "matches": []}
        try:
            return await _service().search(pattern, glob=glob or "*", limit=limit)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] grep failed: %s", exc)
            return {"ok": False, "error": str(exc), "matches": []}

    # ── POST /api/ide/run ──────────────────────────────────────────────
    @router.post("/run")
    async def run(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        command = str(payload.get("command", "")).strip()
        timeout = _coerce_timeout(payload.get("timeout"))
        if not command:
            return {"ok": False, "error": "Missing 'command'", "output": ""}
        try:
            return await _service().run(command, timeout=timeout)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] run failed: %s", exc)
            return {"ok": False, "error": str(exc), "output": ""}

    # ── POST /api/ide/runfile ──────────────────────────────────────────
    @router.post("/runfile")
    async def run_file(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        path = str(payload.get("path", "")).strip()
        timeout = _coerce_timeout(payload.get("timeout"))
        if not path:
            return {"ok": False, "error": "Missing 'path'", "output": ""}
        try:
            return await _service().run_file(path, timeout=timeout)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] runfile failed: %s", exc)
            return {"ok": False, "error": str(exc), "output": ""}

    # ── POST /api/ide/diff ─────────────────────────────────────────────
    @router.post("/diff")
    async def diff(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        path = str(payload.get("path", "")).strip()
        old = payload.get("old", "")
        new = payload.get("new", "")
        if not path:
            return {"ok": False, "error": "Missing 'path'", "diff": "", "changed": False}
        try:
            return await _service().diff(path, str(old or ""), str(new or ""))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] diff failed: %s", exc)
            return {"ok": False, "error": str(exc), "diff": "", "changed": False}

    # ── POST /api/ide/git ──────────────────────────────────────────────
    @router.post("/git")
    async def git(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        subcommand = str(payload.get("subcommand", "")).strip()
        timeout = _coerce_timeout(payload.get("timeout"))
        if not subcommand:
            return {"ok": False, "error": "Missing 'subcommand'", "output": ""}
        try:
            return await _service().git(subcommand, timeout=timeout)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] git failed: %s", exc)
            return {"ok": False, "error": str(exc), "output": ""}

    # ── POST /api/ide/swarm ────────────────────────────────────────────
    @router.post("/swarm")
    async def swarm(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        instruction = str(payload.get("instruction", "")).strip()
        if not instruction:
            return {"ok": False, "error": "Missing 'instruction'", "task_id": None}
        workers = payload.get("workers") or None
        if workers is not None and not isinstance(workers, list):
            workers = None
        pattern = str(payload.get("pattern", "auto")).strip() or "auto"
        context = str(payload.get("context", "") or "")
        workspace_id = str(payload.get("workspace_id", "") or "").strip() or None
        try:
            return await _service().send_to_swarm(
                instruction, workers=workers, pattern=pattern,
                context=context, workspace_id=workspace_id,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] swarm failed: %s", exc)
            return {"ok": False, "error": str(exc), "task_id": None}

    # ── GET /api/ide/skills ────────────────────────────────────────────
    @router.get("/skills")
    async def list_skills() -> dict[str, Any]:
        """List the swarm-native coding skills (refactor/write-tests/...)."""
        try:
            from kazma_skills.coding_skills import list_coding_skills

            return {"ok": True, "skills": list_coding_skills()}
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] list skills failed: %s", exc)
            return {"ok": False, "error": str(exc), "skills": []}

    # ── POST /api/ide/skill ────────────────────────────────────────────
    @router.post("/skill")
    async def run_skill(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Render a coding skill instruction for a path and dispatch to swarm."""
        skill_name = str(payload.get("skill", "")).strip()
        path = str(payload.get("path", "")).strip()
        if not skill_name:
            return {"ok": False, "error": "Missing 'skill'", "task_id": None}
        if not path:
            return {"ok": False, "error": "Missing 'path'", "task_id": None}
        try:
            from kazma_skills.coding_skills import render_instruction

            instruction = render_instruction(skill_name, path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "task_id": None}
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] render skill failed: %s", exc)
            return {"ok": False, "error": str(exc), "task_id": None}
        try:
            return await _service().send_to_swarm(instruction, pattern="auto")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[ide_api] skill dispatch failed: %s", exc)
            return {"ok": False, "error": str(exc), "task_id": None}

    return router


def _coerce_timeout(value: Any, default: int = 60) -> int:
    """Best-effort convert a request timeout to a bounded int."""
    try:
        t = int(value)
    except (TypeError, ValueError):
        return default
    if t <= 0:
        return default
    return min(t, 600)
