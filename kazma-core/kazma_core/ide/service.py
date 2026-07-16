"""IDE Service — transport-agnostic coding backend for Kazma.

This module is the single source of truth for all "IDE" capabilities
(file read/write, run/exec, diff, grep, git, swarm dispatch). It is
deliberately **transport-neutral**: it contains zero platform/UI logic
and never sees Telegram/Discord/Slack/Web/TUI identifiers. Every channel
(Web, TUI, Telegram, Slack, Discord) and the swarm drive the *same*
service, exactly like the supervisor graph and SwarmEngine stay
platform-neutral.

Safety model
-----------
All mutating/executing operations are delegated to the existing
``LocalToolRegistry`` tools (``file_write``, ``shell_exec``,
``python_exec``). Those tools already enforce:

  * workspace scoping (fail-closed path-traversal guard in
    ``tool_registry._workspace_scope_error`` and ``tools/file_write``), and
  * the HITL danger-tool gate via ``kazma_core.swarm.safety.SafetyMiddleware``
    (which posts an approval request to the SwarmMessageBus and awaits the
    operator's decision).

So the IDE reuses the *exact same* safety layer the agent and swarm use —
no parallel, un-gated write/exec path is ever created.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_workspace_root() -> Path:
    """Resolve the active workspace root, mirroring ``workspace_api`` precedence.

    Order:
      1. Per-task ``workspace_scope`` ContextVar (Phase 3 — concurrent multi-repo).
      2. ``KAZMA_WORKSPACE`` env var.
      3. Active workspace from ``WorkspaceStore``.
      4. ``kazma_core.tools.file_write`` configured workspace (cwd-based default).
    """
    import os

    # 1. Per-task scope takes top precedence (matches file_write._get_workspace).
    try:
        from kazma_core.ide.workspace_scope import resolve_workspace_root

        scoped = resolve_workspace_root()
        if scoped is not None:
            return scoped
    except Exception:
        pass

    env_ws = os.environ.get("KAZMA_WORKSPACE", "").strip()
    if env_ws:
        return Path(env_ws).expanduser().resolve()

    try:
        from kazma_core.stores import get_workspace_store

        active = get_workspace_store().get_active_workspace()
        if active and active.get("root_path"):
            return Path(active["root_path"]).resolve()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[IdeService] WorkspaceStore lookup failed: %s", exc)

    # Fall back to the file_write module's own resolution (cwd/KAZMA_WORKSPACE).
    try:
        from kazma_core.tools.file_write import _get_workspace

        return _get_workspace().resolve()
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[IdeService] file_write workspace lookup failed: %s", exc)
        return (Path.cwd() / "kazma-data" / "workspace").resolve()


def _lang_for_path(path: Path) -> str:
    """Best-effort language hint for an editor (used by UI highlighting)."""
    ext = path.suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".html": "html",
        ".css": "css",
        ".json": "json",
        ".md": "markdown",
        ".sh": "bash",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".toml": "toml",
        ".sql": "sql",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".rb": "ruby",
        ".php": "php",
    }.get(ext, "plaintext")


class IdeService:
    """Transport-neutral coding backend.

    All methods are coroutines and return plain ``dict`` structures so they
    serialize cleanly to JSON (Web) and to markdown (chat platforms).
    """

    def __init__(self) -> None:
        self._root = _resolve_workspace_root()

    # ── Path helpers ───────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        """The workspace root, re-resolved to honor any active workspace_scope."""
        self._root = _resolve_workspace_root()
        return self._root

    def refresh_root(self) -> Path:
        """Re-resolve the workspace root (call after a workspace switch).

        Kept for explicit callers (commands.py, mcp_server.py) that want
        to force a re-resolution. The ``root`` property also re-resolves
        automatically, so this is mainly for API compatibility.
        """
        self._root = _resolve_workspace_root()
        return self._root

    def resolve(self, rel_path: str) -> Path:
        """Resolve a (possibly relative) path against the workspace root.

        Raises ``ValueError`` if the result escapes the workspace (path
        traversal protection). Absolute paths inside the root are allowed.
        Uses ``os.path.realpath`` (fully symlink/junction aware) so the
        check is robust on Windows where ``%LOCALAPPDATA%\\Temp`` is often a
        junction.

        The root is re-resolved on each call so per-task ``workspace_scope``
        (Phase 3) is honored — the IDE layer no longer caches a stale root
        across workspace switches.
        """
        import os

        ws_root = self.root  # re-resolves, honoring workspace_scope
        if not rel_path:
            return ws_root
        rel = rel_path.strip().lstrip("/\\")
        norm = os.path.normpath(rel)

        # A normalized relative path that still starts with ".." escapes the
        # workspace. This string-level check is immune to symlinks/junctions
        # (e.g. pytest's ``pytest-of-*`` temp dir on Windows), unlike a
        # symlink-aware ``realpath`` comparison which can be fooled by the
        # way ``..`` cancels a symlinked ancestor.
        if not os.path.isabs(rel) and (norm == ".." or norm.startswith(".." + os.path.sep)):
            raise ValueError(
                f"Path '{rel_path}' escapes the workspace root "
                f"({ws_root}); access denied."
            )

        if os.path.isabs(rel):
            # Absolute inputs are NOT blindly trusted; we resolve them and
            # let the containment check below decide whether they fall inside
            # the workspace root.
            target = Path(rel).resolve()
        else:
            target = (ws_root / norm).resolve()

        # Backstop containment check via pure string normalization (no
        # symlink resolution), so symlinked temp dirs can't mask an escape.
        root_norm = os.path.normpath(str(ws_root))
        target_norm = os.path.normpath(str(target))
        if target_norm != root_norm and not target_norm.startswith(root_norm + os.path.sep):
            raise ValueError(
                f"Path '{rel_path}' is outside the workspace root "
                f"({ws_root}); access denied."
            )
        # Return the original (non-realpath) resolved path for display.
        return target

    # ── Tool delegation (safety reused) ───────────────────────────────

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute a built-in tool through the shared registry.

        The registry enforces workspace scoping + HITL gating, so this is
        the ONLY execution path used by the IDE. Never call the underlying
        tool functions directly from the IDE layer.
        """
        from kazma_core.agent.tool_registry import get_tool_registry

        result = await get_tool_registry().execute(tool_name, arguments)
        ok = bool(result.get("is_error")) is False
        return {
            "ok": ok,
            "tool": tool_name,
            "output": result.get("content", ""),
            "error": None if ok else result.get("content", "Tool execution failed"),
        }

    # ── IDE operations ─────────────────────────────────────────────────

    async def read_file(self, rel_path: str) -> dict[str, Any]:
        """Read a file from the workspace. Returns content + language hint."""
        try:
            target = self.resolve(rel_path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "content": "", "lang": "plaintext", "lines": 0}
        if not target.exists() or not target.is_file():
            return {
                "ok": False,
                "error": f"File not found: {rel_path}",
                "content": "",
                "lang": _lang_for_path(target),
                "lines": 0,
            }
        res = await self._call_tool("file_read", {"path": str(target)})
        if not res["ok"]:
            return {"ok": False, "error": res["error"], "content": "", "lang": _lang_for_path(target), "lines": 0}
        content = res["output"]
        return {
            "ok": True,
            "error": None,
            "path": rel_path,
            "content": content,
            "lang": _lang_for_path(target),
            "lines": content.count("\n") + (1 if content and not content.endswith("\n") else 0),
        }

    async def write_file(self, rel_path: str, content: str) -> dict[str, Any]:
        """Write content to a workspace file.

        Delegates to ``file_write`` -> triggers the HITL danger-tool gate when
        safety is enabled and no approval has been granted. Returns the tool's
        success/error message.
        """
        try:
            target = self.resolve(rel_path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "path": rel_path}
        res = await self._call_tool("file_write", {"path": str(target), "content": content})
        res["path"] = rel_path
        return res

    async def delete_file(self, rel_path: str) -> dict[str, Any]:
        """Delete a file or directory from the workspace.

        Delegates to the ``file_delete`` tool so the HITL danger-tool gate
        applies (deletion is in the danger tier). Directories are removed
        recursively. The path is traversal-checked via :meth:`resolve`.
        """
        try:
            target = self.resolve(rel_path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "path": rel_path}
        if not target.exists():
            return {"ok": False, "error": f"Path not found: {rel_path}", "path": rel_path}
        res = await self._call_tool("file_delete", {"path": str(target)})
        res["path"] = rel_path
        return res

    async def list_path(self, rel_path: str = "") -> dict[str, Any]:
        """List a directory inside the workspace.

        Returns ``files`` as a list of entry *names* (strings) for backward
        compatibility with the ``/ide ls`` chat command, plus an ``entries``
        key with structured ``{name, path, is_dir}`` objects for forward-
        compatible callers (TUI/Web). The path is resolved and
        traversal-checked via :meth:`resolve` first.
        """
        try:
            target = self.resolve(rel_path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "files": [], "entries": [], "path": rel_path}
        if not target.exists():
            return {"ok": False, "error": f"Path not found: {rel_path}", "files": [], "entries": [], "path": rel_path}
        if not target.is_dir():
            return {"ok": False, "error": f"Not a directory: {rel_path}", "files": [], "entries": [], "path": rel_path}
        entries: list[dict[str, Any]] = []
        try:
            for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
                # Skip dotfiles/hidden and common noise at the root.
                if child.name.startswith("."):
                    continue
                try:
                    rel = str(child.relative_to(self._root)).replace("\\", "/")
                except ValueError:
                    rel = child.name
                entries.append({"name": child.name, "path": rel, "is_dir": child.is_dir()})
        except Exception as exc:
            return {"ok": False, "error": f"List failed: {exc}", "files": [], "entries": [], "path": rel_path}
        return {
            "ok": True,
            "error": None,
            "path": rel_path,
            "root": str(self._root),
            "files": [e["name"] for e in entries],
            "entries": entries,
        }

    async def search(self, pattern: str, glob: str = "*.py", limit: int = 50) -> dict[str, Any]:
        """Regex search across the workspace."""
        res = await self._call_tool(
            "file_search",
            {"pattern": pattern, "path": str(self._root), "glob": glob, "limit": limit},
        )
        if not res["ok"]:
            return {"ok": False, "error": res["error"], "matches": []}
        matches = [line for line in res["output"].splitlines() if line.strip()]
        return {"ok": True, "error": None, "pattern": pattern, "matches": matches}

    async def run(self, command: str, timeout: int = 60) -> dict[str, Any]:
        """Execute a shell command inside the workspace (scoped + HITL gated).

        Use this for running tests, linters, builds, or a script file, e.g.
        ``run("pytest -q")`` or ``run("python main.py")``.
        """
        if not command or not command.strip():
            return {"ok": False, "error": "Empty command", "output": ""}
        return await self._call_tool("shell_exec", {"command": command, "timeout": timeout})

    async def run_file(self, rel_path: str, timeout: int = 60) -> dict[str, Any]:
        """Convenience: run a script file with its inferred interpreter."""
        try:
            target = self.resolve(rel_path)
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "output": ""}
        ext = target.suffix.lower()
        runner = {
            ".py": "python",
            ".js": "node",
            ".ts": "npx ts-node",
            ".sh": "bash",
            ".rb": "ruby",
            ".go": "go run",
        }.get(ext)
        if not runner:
            return {
                "ok": False,
                "error": f"No interpreter known for {ext} files.",
                "output": "",
            }
        return await self.run(f"{runner} {target}", timeout=timeout)

    async def diff(self, rel_path: str, old_content: str, new_content: str) -> dict[str, Any]:
        """Produce a unified diff between two versions of a file (no tool call)."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        unified = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
            lineterm="",
        )
        text = "\n".join(unified)
        return {
            "ok": True,
            "error": None,
            "path": rel_path,
            "diff": text,
            "changed": bool(text),
        }

    async def git(self, subcommand: str, timeout: int = 60) -> dict[str, Any]:
        """Run a git subcommand inside the workspace (scoped + HITL gated).

        .. note::
            ``subcommand`` is intentionally unfiltered — this is an operator
            tool and the whole path is HITL-gated (``shell_exec`` is a
            danger tool), so destructive ops like ``push --force`` or
            ``clean -fdx`` surface an approval prompt before running. The
            workspace scoping in ``shell_exec`` pins the cwd to the root.
        """
        if not subcommand or not subcommand.strip():
            return {"ok": False, "error": "Empty git subcommand", "output": ""}
        return await self.run(f"git {subcommand}", timeout=timeout)

    async def send_to_swarm(
        self,
        instruction: str,
        workers: list[str] | None = None,
        pattern: str = "auto",
        context: str = "",
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch a coding task to the swarm, reusing the existing engine.

        The swarm's workers already call the same file/exec tools over the
        workspace, so a dispatched agent can read/edit/run files seamlessly.
        Returns the created task id (or an error if the engine is unavailable).

        ``pattern`` is one of: ``auto``, ``dispatch``, ``pipeline``,
        ``consult``, ``fanout``, ``broadcast``.

        ``workspace_id`` (optional, Phase 3) targets a specific WorkspaceStore
        row so the dispatched workers operate on that repo rather than the
        process-wide active one.

        The workspace/repo/tool environment facts are attached to the task
        ``context`` so the worker's prompt-assembly path
        (``InProcessWorker.dispatch``) surfaces them — this closes the gap
        where ``IdeService`` knew the root but dropped it at the swarm
        boundary.
        """
        try:
            from kazma_core.swarm.engine import get_swarm_engine
            from kazma_core.swarm.task import SwarmTask, TaskType

            engine = get_swarm_engine()
        except Exception as exc:
            logger.debug("[IdeService] Swarm engine unavailable: %s", exc)
            return {
                "ok": False,
                "error": "Swarm engine is not available in this deployment.",
                "task_id": None,
            }

        type_map = {
            "auto": TaskType.DISPATCH,
            "dispatch": TaskType.DISPATCH,
            "pipeline": TaskType.PIPELINE,
            "consult": TaskType.CONSULT,
            "fanout": TaskType.FAN_OUT,
            "broadcast": TaskType.BROADCAST,
        }
        task_type = type_map.get(pattern, TaskType.DISPATCH)

        # Build the environment context block so the worker knows where it
        # is and what tools it has. Caller-supplied context (e.g. file
        # content from the Web IDE) is appended after the env block.
        full_context = ""
        try:
            from kazma_core.ide.env_context import build_env_context

            full_context = build_env_context(workspace_id=workspace_id)
        except Exception:
            logger.warning("[IdeService] env_context build failed — dispatched worker may lack workspace awareness", exc_info=True)
        if context:
            full_context = f"{full_context}\n\n--- Task context ---\n{context}"

        meta: dict[str, Any] = {"source": "ide"}
        if workspace_id:
            meta["workspace_id"] = workspace_id

        task = SwarmTask(
            prompt=instruction,
            type=task_type,
            workers=list(workers or []),
            context=full_context,
            timeout=300.0,
            metadata=meta,
            workspace_id=workspace_id,
        )
        try:
            await engine.dispatch(task)
        except Exception as exc:
            logger.warning("[IdeService] Swarm dispatch failed: %s", exc)
            return {"ok": False, "error": f"Swarm dispatch failed: {exc}", "task_id": None}
        return {"ok": True, "error": None, "task_id": task.id}


# ══════════════════════════════════════════════════════════════════════════
# Process-wide singleton
# ══════════════════════════════════════════════════════════════════════════

_ide_service: IdeService | None = None


def get_ide_service() -> IdeService:
    """Return the shared IdeService singleton.

    Use this instead of constructing ``IdeService()`` directly so the whole
    process shares one workspace-root resolution.
    """
    global _ide_service
    if _ide_service is None:
        _ide_service = IdeService()
    return _ide_service


def reset_ide_service() -> None:
    """Drop the singleton reference (used by test teardown)."""
    global _ide_service
    _ide_service = None
