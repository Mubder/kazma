"""File read/write/search/patch tools and workspace path grants.

Extracted from the former ``tool_builtins.py`` god module (2,833
lines) — audit O5. Tool bodies are unchanged; registration order
within this group is preserved.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kazma_core.agent.tool_scope import _workspace_scope_error

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    pass

def _qnorm(q: str) -> str:
    """Normalize a memory q-filter: underscores/hyphens -> single spaces,
    lowercased. Paired with REPLACE(...) in SQL so 'memory system' matches
    user_memory_system (2026-08-27 report — the literal LIKE filter missed
    it while FTS memory_search matched fine)."""
    import re as _re

    return _re.sub(r"[_\-\s]+", " ", str(q or "").strip().lower()).strip()




def register_filesystem_tools(registry: Any) -> None:
    """Register the filesystem tools onto *registry*."""
    from kazma_core.agent.tool_registry import _pending_dispatch_tasks  # noqa: F401

    @registry.register(
        description=(
            "Read a file from the local filesystem. Returns line-numbered text. "
            "Supports line range slicing via start_line/end_line or offset/limit."
        ),
        category="filesystem",
    )
    async def file_read(
        path: str,
        offset: int = 0,
        limit: int = 2000,
        start_line: int | None = None,
        end_line: int | None = None,
        encoding: str = "utf-8",
    ) -> str:
        from kazma_core.tools.file_read import file_read as _fr_tool

        if start_line is not None:
            offset = start_line
        if end_line is not None:
            start = start_line if start_line is not None else offset
            limit = max(1, end_line - start + 1)
        return await _fr_tool(path, offset=offset, limit=limit)
    @registry.register(
        description=(
            "Write content to a local file (full overwrite). Creates parent directories "
            "if needed. Prefer file_apply_patch for edits to files that already exist."
        ),
        category="filesystem",
    )
    async def file_write(path: str, content: str, encoding: str = "utf-8") -> str:
        from kazma_core.tools.file_write import file_write as _fw_tool
        return await _fw_tool(path, content)
    @registry.register(
        description=(
            "Surgically edit an existing workspace file. Prefer this over file_write "
            "for changes to files that already exist — send a unique old_string plus "
            "new_string (Aider-style), or a unified diff / Morph Begin Patch in "
            "patch=. HITL danger-tier like file_write."
        ),
        category="filesystem",
    )
    async def file_apply_patch(
        path: str,
        old_string: str = "",
        new_string: str = "",
        patch: str = "",
        replace_all: bool = False,
    ) -> str:
        from kazma_core.tools.file_apply_patch import file_apply_patch as _fp_tool

        return await _fp_tool(
            path,
            old_string=old_string,
            new_string=new_string,
            patch=patch,
            replace_all=replace_all,
        )
    @registry.register(
        description=(
            "Append content to the end of a local file. Creates the file and parent "
            "directories if needed. Use this to build LARGE files in chunks — one "
            "file_write to create, then file_append for each subsequent section — "
            "instead of one giant write that can exceed the model's output limit."
        ),
        category="filesystem",
    )
    async def file_append(path: str, content: str, encoding: str = "utf-8") -> str:
        p = Path(path).expanduser().resolve()
        scope_err = _workspace_scope_error(p, path, "writes")
        if scope_err:
            return scope_err
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding=encoding) as f:
                f.write(content)
            try:
                from kazma_core.code_index.indexer import notify_file_changed

                notify_file_changed(p)
            except Exception:
                pass
            return f"Appended {len(content)} chars to: {path}"
        except Exception as exc:
            return f"Error appending to {path}: {exc}"
    @registry.register(
        description=(
            "Delete a file or directory. Directories are removed recursively. "
            "Restricted to the workspace. Danger-tier (requires HITL approval)."
        ),
        category="filesystem",
    )
    async def file_delete(path: str) -> str:
        import shutil as _shutil

        p = Path(path).expanduser().resolve()
        scope_err = _workspace_scope_error(p, path, "deletions")
        if scope_err:
            return scope_err
        if not p.exists():
            return f"Error: Path not found: {path}"
        try:
            if p.is_dir():
                _shutil.rmtree(p)
            else:
                p.unlink()
            try:
                from kazma_core.code_index.indexer import notify_file_changed

                notify_file_changed(p, deleted=True)
            except Exception:
                pass
            return f"Deleted: {path}"
        except Exception as exc:
            return f"Error deleting {path}: {exc}"
    @registry.register(
        description="List files and directories at a path. Returns names sorted alphabetically.",
        category="filesystem",
    )
    async def file_list(path: str = ".", pattern: str = "*") -> str:
        p = Path(path).expanduser().resolve()
        # Workspace scoping — block listing outside workspace (fail-closed)
        scope_err = _workspace_scope_error(p, path, "listings")
        if scope_err:
            return scope_err
        if not p.exists():
            return f"Error: Path not found: {path}"
        if not p.is_dir():
            return f"Error: Not a directory: {path}"
        entries = sorted(str(child.name) for child in p.glob(pattern))
        if not entries:
            return f"No files matching '{pattern}' in {path}"
        return "\n".join(entries[:200])  # cap at 200 entries
    @registry.register(
        description=(
            "Request permission to read or write a path OUTSIDE the active "
            "workspace. Requires human approval (HITL). On approve, grants "
            "session access to that folder (or parent of a file) so "
            "file_read/file_list/file_search (and write tools if mode=write) "
            "can use it. Prefer durable Extra folders in Settings for "
            "permanent access. Args: path (required), mode='read'|'write', "
            "scope='session' (default) or 'durable' (adds to Settings "
            "extra roots — write mode needs write grant)."
        ),
        category="filesystem",
    )
    async def request_path_access(
        path: str,
        mode: str = "read",
        scope: str = "session",
        label: str = "",
    ) -> str:
        """HITL-gated path grant for external folders/files."""
        from kazma_core.safety.hitl import get_current_thread_id
        from kazma_core.workspace.path_grants import (
            grant_session_path,
            list_durable_roots,
            set_durable_roots,
        )
        from kazma_core.workspace.path_policy import check_path_access

        if not path or not str(path).strip():
            return "Error: path is required."
        mode_n = "write" if str(mode).lower() in ("write", "rw", "readwrite") else "read"
        scope_n = "durable" if str(scope).lower() in ("durable", "permanent", "always") else "session"

        # Already allowed?
        existing = check_path_access(path, mode_n)
        if existing.allowed and existing.via != "absolute":
            return (
                f"Already allowed via {existing.via}: {existing.grant_path or existing.resolved} "
                f"(mode ≥ {mode_n}). Retry your file tool."
            )

        if scope_n == "durable":
            roots = [g.to_dict() for g in list_durable_roots()]
            try:
                resolved = str(Path(path).expanduser().resolve())
            except OSError as exc:
                return f"Error: invalid path: {exc}"
            p = Path(resolved)
            root = resolved if p.is_dir() or not p.suffix else str(p.parent)
            # Upsert
            roots = [r for r in roots if r.get("path") != root]
            roots.append(
                {
                    "path": root,
                    "mode": mode_n,
                    "label": label or Path(root).name,
                }
            )
            set_durable_roots(roots)
            return (
                f"Durable extra root granted: {root} (mode={mode_n}). "
                "Retry file_read / file_list / file_write as needed."
            )

        tid = get_current_thread_id()
        if not tid:
            return (
                "Error: no active chat thread for a session grant. "
                "Use scope='durable' or open this from a chat turn."
            )
        try:
            grant = grant_session_path(
                tid,
                path,
                mode=mode_n,
                label=label,
                actor="hitl",
            )
        except ValueError as exc:
            return f"Error: {exc}"
        return (
            f"Session path grant active: {grant.path} (mode={grant.mode}, "
            f"id={grant.grant_id}). Retry the file tool now. "
            "Grant expires in ~1 hour or when the process clears safety keys."
        )
    @registry.register(
        description=(
            "Search for text inside files using regex. Returns matching lines with file paths and line numbers."
        ),
        category="filesystem",
    )
    async def file_search(
        pattern: str,
        path: str = ".",
        glob: str = "*.py",
        limit: int = 20,
    ) -> str:
        import re

        # Topic-shift / audit quarantine: block broad documents/ gold corpus
        try:
            from kazma_core.agent.turn_input import filter_file_search_path

            qerr = filter_file_search_path(path)
            if qerr:
                return qerr
        except Exception:
            pass

        root = Path(path).expanduser().resolve()
        if not root.exists():
            return f"Error: Path not found: {path}"
        # Workspace scoping — block searches outside workspace (fail-closed)
        scope_err = _workspace_scope_error(root, path, "searches")
        if scope_err:
            return scope_err

        # Skip directories that would make the search catastrophically slow
        # (e.g. 1.6 GB .venv, .git internals, node_modules, build artifacts,
        # data dirs). Without this, rglob walks the entire venv reading
        # every .py — a 212-second operation on a standard install.
        _SKIP_DIRS = frozenset({
            ".venv", "venv", ".git", "node_modules", "__pycache__",
            ".kazma", "kazma-data", ".pytest_cache", ".mypy_cache",
            ".ruff_cache", "build", "dist", ".tox", ".eggs",
            "vector_memory", "site-packages",
        })

        def _should_skip(p: Path) -> bool:
            """True if any path component is in the skip set."""
            return any(part in _SKIP_DIRS for part in p.parts)

        regex = re.compile(pattern)
        results: list[str] = []
        files_scanned = 0
        _MAX_FILES = 5000  # hard cap so a huge tree can't run for minutes

        for file_path in root.rglob(glob):
            if _should_skip(file_path):
                continue
            if not file_path.is_file():
                continue
            if files_scanned >= _MAX_FILES:
                results.append(
                    f"... (search stopped after scanning {_MAX_FILES} files; "
                    f"narrow the path or glob to find more matches)"
                )
                break
            files_scanned += 1
            if file_path.stat().st_size < 500_000:
                try:
                    for i, line in enumerate(file_path.read_text(errors="replace").splitlines(), 1):
                        if regex.search(line):
                            results.append(f"{file_path}:{i}: {line.strip()}")
                            if len(results) >= limit:
                                return "\n".join(results)
                except Exception as exc:
                    logger.debug("[ToolRegistry] Failed to read %s in search: %s", file_path, exc)
                    continue

        return "\n".join(results) if results else f"No matches for '{pattern}' in {path}/{glob}"
    @registry.register(
        description=(
            "Search the workspace codebase by symbol name and/or text. "
            "Uses a tree-sitter/regex definition index plus live ripgrep. "
            "Prefer this over file_search when looking for a function, class, "
            "or identifier. mode: auto | symbol | text."
        ),
        category="filesystem",
    )
    async def codebase_search(
        query: str,
        mode: str = "auto",
        glob: str = "",
        limit: int = 20,
    ) -> str:
        import asyncio

        from kazma_core.code_index.search import format_search, search_codebase

        def _run() -> str:
            return format_search(
                search_codebase(query, mode=mode, glob=glob, limit=int(limit or 20))
            )

        return await asyncio.to_thread(_run)
    @registry.register(
        description=(
            "Codebase index health: files/symbols indexed, whether ripgrep and "
            "tree-sitter are available. Read-only."
        ),
        category="filesystem",
    )
    async def codebase_status() -> str:
        import asyncio
        import json

        from kazma_core.code_index.indexer import ensure_index, status

        def _run() -> str:
            ensure_index()
            return json.dumps(status(), ensure_ascii=False)

        return await asyncio.to_thread(_run)
    @registry.register(
        description=(
            "Send a file from the workspace to the user's chat (Telegram/Discord/Slack). "
            "Use this when the user asks for a file, document, PDF, or download. "
            "The file is delivered as an attachment alongside the text caption. "
            "After calling send_file, ALWAYS output a clear confirmation message "
            "in your final text response in the active chat session."
        ),
        category="filesystem",
    )
    async def send_file(
        file_path: str,
        caption: str = "",
    ) -> str:
        from pathlib import Path

        p = Path(file_path).expanduser().resolve()
        if not p.exists():
            return f"Error: file not found: {file_path}"
        # Workspace scoping — block sends outside workspace (fail-closed)
        scope_err = _workspace_scope_error(p, file_path, "file sends")
        if scope_err:
            return scope_err
        if not p.is_file():
            return f"Error: not a file: {file_path}"
        if p.stat().st_size > 50 * 1024 * 1024:
            return f"Error: file too large ({p.stat().st_size // 1024 // 1024} MB; max 50 MB)"

        # Resolve the target chat from the gateway ContextVar (set by the
        # agent handler on every turn from the inbound message's sender).
        try:
            from kazma_core.tools.send_message import get_current_delivery_target, send_file_message
        except ImportError:
            return "Error: send_file requires the chat-platform dispatcher (not available in CLI mode)"

        target_id = get_current_delivery_target()
        backend = "telegram"
        if target_id:
            # Respect the session's platform: the gateway binds
            # delivery_target per platform (discord:/slack:/telegram:), so
            # route to that backend. Previously ANY non-telegram target
            # was discarded and rerouted to the configured Telegram chat,
            # delivering files to the wrong platform's users.
            backend = str(target_id).split(":", 1)[0].strip().lower() or "telegram"
        else:
            # No bound target (e.g. cron/CLI context) — fall back to the
            # configured Telegram swarm chat / first allowed user.
            try:
                from kazma_core.config_store import get_config_store

                store = get_config_store()
                tg_id = store.get("connectors.telegram.swarm_chat_id")
                if not tg_id:
                    allowed = store.get("connectors.telegram.allowed_users")
                    # ConfigStore may return a string ("1804015016" or
                    # "123,456") or a list — normalize. The old code did
                    # allowed[0] on a string, taking the FIRST CHARACTER
                    # ("1") → chat_id 1 → "chat not found" 400.
                    if isinstance(allowed, str):
                        allowed = [u.strip() for u in allowed.replace(",", " ").split() if u.strip()]
                    elif not isinstance(allowed, list):
                        allowed = []
                    if allowed:
                        tg_id = str(allowed[0])
                if tg_id:
                    target_id = f"telegram:{tg_id}"
            except Exception as exc:
                logger.debug("[ToolRegistry] Telegram chat target fallback failed: %s", exc)

        if not target_id:
            return f"File saved in workspace at {p}. (No active Telegram/chat channel configured)"

        try:
            result = await send_file_message(
                target_id=target_id,
                text=caption or f"📎 {p.name}",
                file_path=str(p),
                backend=backend,
            )
            return f"File sent: {p.name} ({p.stat().st_size // 1024} KB) → {result}"
        except Exception as exc:
            logger.warning("[ToolRegistry] send_file failed: %s", exc)
            return f"Error sending file: {exc}"
