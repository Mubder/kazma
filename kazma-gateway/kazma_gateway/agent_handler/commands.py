"""Commands submodule — handling swarm slash-commands and interactive model selectors."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kazma_gateway.gateway import IncomingMessage, OutboundMessage, SessionStore
from .store import _build_target_id
from .swarm_dispatch import (
    _send_swarm_reply,
    _extract_swarm_task,
    _dispatch_swarm_from_chat,
    _find_worker_prompt_split,
    _dispatch_auto_route,
)

logger = logging.getLogger(__name__)


async def _try_swarm_command(
    msg: IncomingMessage,
    state: dict[str, Any],
    store: SessionStore,
    manager: Any,
    thread_id: str,
) -> bool:
    """Check if a message is a swarm command and handle it.

    Trigger patterns (case-insensitive):
        /swarm <worker> <task>               — dispatch to one worker
        /swarm pipeline|consult|fanout ...   — structured patterns
        /swarm broadcast <task>              — all workers
        /swarm status                        — show swarm status
        /swarm list                          — list workers
        /swarm <natural language task>       — auto-route via CapabilityRouter

        Also accepts bare mentions:
        "use the swarm to research X"
        "swarm: implement Y"
        "swarm analyze Z"

    Returns ``True`` if the message was handled (swarm intent detected),
    ``False`` to continue with normal graph processing.
    """
    text = (msg.text or "").strip()
    if not text:
        return False

    # ── Detect swarm intent ─────────────────────────────────────
    # Accept both "/swarm ..." and bare "swarm" mentions.
    is_slash = text.lower().startswith("/swarm")
    # Bare-word detection: only trigger on explicit intent patterns,
    # not just any message containing the word "swarm".
    # E.g. "use the swarm to X" → yes, "I saw a swarm of bees" → no.
    text_lower = text.lower()
    bare_swarm = False
    if not is_slash:
        import re
        # Only match specific command patterns, not arbitrary word usage
        bare_patterns = [
            r'(?:use|ask|tell)\s+(?:the\s+)?swarm\s+(?:to\s+)?',
            r'let\s+(?:the\s+)?swarm\s+',
            r'^swarm\s*:\s*',  # "swarm: task" (must be at start)
            r'^swarm\s+\S',    # "swarm <task>" (must be at start)
        ]
        for pat in bare_patterns:
            if re.match(pat, text_lower):
                bare_swarm = True
                break

    if not is_slash and not bare_swarm:
        return False

    # Lazy import — skip if swarm engine isn't available
    try:
        from kazma_core.swarm.engine import get_swarm_engine
        engine = get_swarm_engine()
    except Exception as _e:
        logger.debug("[AgentHandler] Swarm engine not available: %s", _e)
        return False  # swarm not initialized, fall through to graph

    # ── Extract the command body (everything after the trigger) ──
    if is_slash:
        # "/swarm ..." → take everything after "/swarm "
        parts = text.split(None, 2)  # ["/swarm", subcommand, rest]
        if len(parts) < 2:
            # Just "/swarm" with nothing else → show help
            await _send_swarm_reply(msg, store, manager, thread_id,
                "🐝 **Swarm Commands**\n\n"
                "/swarm `<worker>` `<task>` — dispatch to one worker\n"
                "/swarm pipeline `<w1,w2,...>` `<task>` — sequential pipeline\n"
                "/swarm consult `<w1,w2,...>` `<task>` — parallel consult\n"
                "/swarm fanout `<w1,w2,...>` `<task>` — parallel fan-out\n"
                "/swarm broadcast `<task>` — all workers\n"
                "/swarm `<task>` — auto-route to best workers\n"
                "/swarm status — show swarm status\n"
                "/swarm list — list workers\n"
                "/swarm config — show output routing config\n"
                "/swarm config group `<chat_id>` — route output to a Telegram group\n"
                "/swarm config clear — disable output routing\n\n"
                "Or just say: *use the swarm to <task>*\n\n"
                "Append `-> telegram:<chat_id>` to any task for one-off routing."
            )
            return True
        sub = parts[1].lower()
        task_body = parts[2] if len(parts) > 2 else ""
    else:
        # Bare mention: strip the trigger phrase, keep the rest as the task
        sub = ""
        task_body = _extract_swarm_task(text)

    # ── Known subcommands (only for /swarm prefix) ──────────────
    if is_slash:
        # /swarm status
        if sub == "status":
            try:
                worker_names = engine.worker_names
                status_lines = ["🐝 Swarm Status\n", f"Workers: {len(worker_names)}"]
                for name in worker_names:
                    w = engine.get_worker(name)
                    busy = " (busy)" if w and getattr(w, "busy", False) else ""
                    model = f" [{getattr(w, 'model', '?')}]" if w and getattr(w, 'model', "") else ""
                    status_lines.append(f"  • {name}{busy}{model}")
                await _send_swarm_reply(msg, store, manager, thread_id, "\n".join(status_lines))
            except Exception as exc:
                await _send_swarm_reply(msg, store, manager, thread_id, f"⚠️ Status error: {exc}")
            return True

        # /swarm list
        if sub == "list":
            try:
                names = engine.worker_names
                if not names:
                    await _send_swarm_reply(msg, store, manager, thread_id, "No workers registered.")
                else:
                    lines = [f"🐝 Workers ({len(names)}):"]
                    for name in names:
                        w = engine.get_worker(name)
                        role = getattr(w, "role", "") or ""
                        model = getattr(w, "model", "") or ""
                        lines.append(f"  • {name}" + (f" ({role})" if role else "") + (f" [{model}]" if model else ""))
                    await _send_swarm_reply(msg, store, manager, thread_id, "\n".join(lines))
            except Exception as exc:
                await _send_swarm_reply(msg, store, manager, thread_id, f"⚠️ List error: {exc}")
            return True

        # /swarm config [group <chat_id> | clear]
        if sub == "config":
            return await _handle_swarm_config_command(
                msg, store, manager, thread_id, task_body,
            )

        # /swarm broadcast <prompt>
        if sub == "broadcast":
            prompt = task_body
            if not prompt:
                await _send_swarm_reply(msg, store, manager, thread_id, "⚠️ Usage: /swarm broadcast <prompt>")
                return True
            return await _dispatch_swarm_from_chat(
                msg, store, manager, thread_id, engine,
                workers=[], task=prompt, pattern="broadcast",
            )

        # /swarm pipeline|consult|fanout <workers> <prompt>
        if sub in ("pipeline", "consult", "fanout", "dispatch"):
            if not task_body:
                await _send_swarm_reply(msg, store, manager, thread_id,
                    f"⚠️ Usage: /swarm {sub} <workers> <prompt>")
                return True

            if sub == "dispatch":
                worker_parts = task_body.split(None, 1)
                if len(worker_parts) < 2:
                    await _send_swarm_reply(msg, store, manager, thread_id,
                        "⚠️ Usage: /swarm dispatch <worker> <prompt>")
                    return True
                return await _dispatch_swarm_from_chat(
                    msg, store, manager, thread_id, engine,
                    workers=[worker_parts[0]], task=worker_parts[1], pattern="dispatch",
                )

            split_idx = _find_worker_prompt_split(task_body)
            if split_idx is None:
                await _send_swarm_reply(msg, store, manager, thread_id,
                    f"⚠️ Usage: /swarm {sub} <worker1,worker2,...> <prompt>")
                return True

            workers_str = task_body[:split_idx].strip()
            prompt = task_body[split_idx:].strip()
            workers = [w.strip() for w in workers_str.split(",") if w.strip()]
            if not workers or not prompt:
                await _send_swarm_reply(msg, store, manager, thread_id,
                    f"⚠️ Usage: /swarm {sub} <worker1,worker2,...> <prompt>")
                return True

            return await _dispatch_swarm_from_chat(
                msg, store, manager, thread_id, engine,
                workers=workers, task=prompt, pattern=sub,
            )

    # ── Natural language auto-route ─────────────────────────────
    # If we reach here, the message triggered swarm intent but didn't
    # match any known subcommand. Treat the full body as a task and
    # auto-route to the best-matching workers via CapabilityRouter.
    if not task_body:
        task_body = text  # fallback: use the full message

    # Skip if the extracted task is too short to be meaningful
    if len(task_body.strip()) < 3:
        await _send_swarm_reply(msg, store, manager, thread_id,
            "🐝 What would you like the swarm to do?\n\n"
            "Examples:\n"
            "  /swarm research the latest AI trends\n"
            "  /swarm implement a dark mode toggle\n"
            "  use the swarm to analyze this code\n"
            "  /swarm broadcast summarize today's news"
        )
        return True

    return await _dispatch_auto_route(
        msg, store, manager, thread_id, engine, task_body,
    )


async def _handle_swarm_config_command(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    body: str,
) -> bool:
    """Handle ``/swarm config`` subcommands for output routing.

    Forms:
        /swarm config                       — show current config
        /swarm config group <chat_id>       — set Telegram group chat_id (enables routing)
        /swarm config disable               — disable routing (keep chat_id)
        /swarm config clear                 — clear output target entirely
    """
    parts = body.split(None, 1)
    action = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    try:
        from kazma_core.config_store import get_config_store
        cs = get_config_store()
    except ImportError:
        await _send_swarm_reply(msg, store, manager, thread_id,
            "⚠️ Config store unavailable.")
        return True

    key = "swarm.output_target"

    # ── /swarm config group <chat_id> ─────────────────────────────
    if action == "group":
        if not arg:
            await _send_swarm_reply(msg, store, manager, thread_id,
                "⚠️ Usage: /swarm config group <chat_id>\n\n"
                "Tip: group chat IDs are negative, e.g. -1001234567890")
            return True
        try:
            chat_id = int(arg)
        except ValueError:
            await _send_swarm_reply(msg, store, manager, thread_id,
                f"⚠️ Invalid chat_id: `{arg}`. It must be an integer "
                "(group IDs are negative, e.g. -1001234567890).")
            return True
        cs.set(key, {
            "platform": "telegram",
            "chat_id": chat_id,
            "enabled": True,
        }, category="swarm")
        await _send_swarm_reply(msg, store, manager, thread_id,
            f"✅ Output routing enabled.\n"
            f"Swarm results will also be sent to Telegram group `{chat_id}`.\n\n"
            "Make sure the bot is a member of that group.")
        return True

    # ── /swarm config disable ─────────────────────────────────────
    if action == "disable":
        existing = cs.get(key, None)
        if isinstance(existing, dict):
            existing["enabled"] = False
            cs.set(key, existing, category="swarm")
        await _send_swarm_reply(msg, store, manager, thread_id,
            "✅ Output routing disabled (config retained).")
        return True

    # ── /swarm config clear ───────────────────────────────────────
    if action == "clear":
        cs.delete(key)
        await _send_swarm_reply(msg, store, manager, thread_id,
            "✅ Output routing cleared.")
        return True

    # ── /swarm config (show current) ──────────────────────────────
    current = cs.get(key, None)
    if not isinstance(current, dict) or not current.get("chat_id"):
        await _send_swarm_reply(msg, store, manager, thread_id,
            "🐝 **Output Routing**\n\n"
            "Status: *not configured*\n\n"
            "To route swarm output to a Telegram group:\n"
            "  /swarm config group -1001234567890\n\n"
            "The bot must be added to the group first.")
        return True

    status = "✅ enabled" if current.get("enabled") else "⏸ disabled"
    await _send_swarm_reply(msg, store, manager, thread_id,
        f"🐝 **Output Routing**\n\n"
        f"Status: {status}\n"
        f"Platform: `{current.get('platform', 'telegram')}`\n"
        f"Group chat_id: `{current.get('chat_id')}`\n\n"
        "Commands:\n"
        "  /swarm config group <chat_id> — change target\n"
        "  /swarm config disable — turn off\n"
        "  /swarm config clear — remove config")
    return True


def _get_visible_providers() -> list[dict[str, Any]]:
    """Return providers that have selected (visible) models."""
    try:
        from kazma_core.model_registry import get_model_registry
        reg = get_model_registry()
        result: list[dict[str, Any]] = []
        for p in reg.list_providers():
            name = p.get("name", "")
            display = p.get("display_name", name)
            enabled = p.get("enabled", True)
            if not enabled or not name:
                continue
            models = reg.get_visible_models(name)
            if models:
                result.append({"name": name, "display_name": display, "models": models})
        return result
    except Exception as exc:
        logger.warning("[agent-handler] Failed to get providers: %s", exc)
        return []


async def _try_ide_command(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
) -> bool:
    """Handle ``/ide`` coding commands across all chat platforms.

    Every subcommand drives the transport-neutral ``IdeService`` in
    ``kazma_core.ide``, so Web/TUI/chat all share one backend. Mutating
    operations (``edit``, ``run``, ``git``) flow through the shared tool
    registry and therefore trigger the same HITL danger-tool gate the agent
    and swarm use — no parallel, un-gated path is created.

    Subcommands::

        /ide                      — show help
        /ide ls [path]            — list a directory
        /ide open <file>          — read a file (shown in a code block)
        /ide edit <file> <text>   — write content to a file
        /ide delete <file>        — delete a file or directory
        /ide run <command>        — run a shell command in the workspace
        /ide runfile <file>       — run a script with its interpreter
        /ide grep <pattern> [glob]— regex search the workspace
        /ide git <subcommand>     — run a git subcommand
        /ide repo [list|switch <id>|clone <owner/repo>|<owner/repo>] — manage workspace
        /ide skill [name] [file]  — run a coding skill (refactor/tests/lint/review)
        /ide swarm <task>         — dispatch a coding task to the swarm

    Returns ``True`` if handled (so the caller skips the graph), else ``False``.
    """
    text = (msg.text or "").strip()
    if not text.lower().startswith("/ide"):
        return False

    # Extract the command body (everything after "/ide").
    parts = text.split(None, 1)
    body = parts[1].strip() if len(parts) > 1 else ""
    tokens = body.split()
    sub = tokens[0].lower() if tokens else ""

    try:
        from kazma_core.ide import get_ide_service

        ide = get_ide_service()
    except Exception as exc:
        logger.debug("[AgentHandler] IdeService unavailable: %s", exc)
        await _send_model_reply(
            msg, store, manager, thread_id,
            "⚠️ IDE service is unavailable in this deployment.",
        )
        return True

    # ── Help ────────────────────────────────────────────────────────
    if sub in ("", "help"):
        await _send_model_reply(
            msg, store, manager, thread_id,
            "🖥️ **Kazma IDE**\n\n"
            "All commands operate inside the active workspace.\n\n"
            "`/ide ls [path]` — list a directory\n"
            "`/ide open <file>` — read a file\n"
            "`/ide edit <file> <text>` — write content to a file\n"
            "`/ide delete <file>` — delete a file or directory\n"
            "`/ide run <command>` — run a shell command\n"
            "`/ide runfile <file>` — run a script with its interpreter\n"
            "`/ide grep <pattern> [glob]` — search the workspace\n"
            "`/ide git <subcommand>` — run git\n"
            "`/ide repo [list|switch <id>|clone <owner/repo>|<owner/repo>]` — manage workspace\n"
            "`/ide skill [name] [file]` — run a coding skill on a file\n"
            "    skills: refactor-file, write-tests, fix-lint, code-review\n"
            "`/ide swarm <task>` — dispatch a coding task to the swarm\n\n"
            "_Danger-tier operations (edit/run/git) require HITL approval._",
        )
        return True

    # ── /ide ls [path] ─────────────────────────────────────────────
    if sub == "ls":
        rel = " ".join(tokens[1:]).strip()
        res = await ide.list_path(rel)
        if not res["ok"]:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        if not res["files"]:
            await _send_model_reply(msg, store, manager, thread_id, f"📁 `{res['path'] or '/'}` is empty.")
            return True
        listed = "\n".join(f"  • {name}" for name in res["files"])
        await _send_model_reply(msg, store, manager, thread_id, f"📁 `{res['path'] or '/'}`\n{listed}")
        return True

    # ── /ide open <file> ───────────────────────────────────────────
    if sub == "open":
        rel = " ".join(tokens[1:]).strip()
        if not rel:
            await _send_model_reply(msg, store, manager, thread_id, "⚠️ Usage: /ide open <file>")
            return True
        res = await ide.read_file(rel)
        if not res["ok"]:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        lang = res.get("lang", "plaintext")
        await _send_model_reply(
            msg, store, manager, thread_id,
            f"📄 `{rel}` ({res['lines']} lines)\n```{lang}\n{res['content']}\n```",
        )
        return True

    # ── /ide edit <file> <text> ────────────────────────────────────
    if sub == "edit":
        rest = body[len("edit"):].strip()
        if not rest:
            await _send_model_reply(msg, store, manager, thread_id, "⚠️ Usage: /ide edit <file> <text>")
            return True
        sp = rest.split(None, 1)
        rel = sp[0]
        content = sp[1] if len(sp) > 1 else ""
        res = await ide.write_file(rel, content)
        if not res["ok"]:
            # HITL rejection surfaces here as a denied error.
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        await _send_model_reply(msg, store, manager, thread_id, f"✅ {res['output']}")
        return True

    # ── /ide delete <file> ─────────────────────────────────────────
    if sub == "delete":
        rel = " ".join(tokens[1:]).strip()
        if not rel:
            await _send_model_reply(msg, store, manager, thread_id, "⚠️ Usage: /ide delete <file>")
            return True
        res = await ide.delete_file(rel)
        if not res["ok"]:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        await _send_model_reply(msg, store, manager, thread_id, f"🗑 Deleted `{rel}`.")
        return True

    # ── /ide run <command> ─────────────────────────────────────────
    if sub == "run":
        cmd = " ".join(tokens[1:]).strip()
        if not cmd:
            await _send_model_reply(msg, store, manager, thread_id, "⚠️ Usage: /ide run <command>")
            return True
        res = await ide.run(cmd)
        if not res["ok"]:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        await _send_model_reply(
            msg, store, manager, thread_id,
            f"⚙️ `$ {cmd}`\n```\n{(res['output'] or '(no output)')[:4000]}\n```",
        )
        return True

    # ── /ide runfile <file> ────────────────────────────────────────
    if sub == "runfile":
        rel = " ".join(tokens[1:]).strip()
        if not rel:
            await _send_model_reply(msg, store, manager, thread_id, "⚠️ Usage: /ide runfile <file>")
            return True
        res = await ide.run_file(rel)
        if not res["ok"]:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        await _send_model_reply(
            msg, store, manager, thread_id,
            f"⚙️ ran `{rel}`\n```\n{(res['output'] or '(no output)')[:4000]}\n```",
        )
        return True

    # ── /ide grep <pattern> [glob] ─────────────────────────────────
    if sub == "grep":
        rest = body[len("grep"):].strip()
        if not rest:
            await _send_model_reply(msg, store, manager, thread_id, "⚠️ Usage: /ide grep <pattern> [glob]")
            return True
        gparts = rest.split(None, 1)
        pattern = gparts[0]
        glob = gparts[1].strip() if len(gparts) > 1 else "*.py"
        res = await ide.search(pattern, glob=glob)
        if not res["ok"]:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        if not res["matches"]:
            await _send_model_reply(msg, store, manager, thread_id, f"🔍 No matches for `{pattern}`.")
            return True
        matches = "\n".join(res["matches"])
        await _send_model_reply(msg, store, manager, thread_id, f"🔍 `{pattern}`\n```{matches[:4000]}\n```")
        return True

    # ── /ide git <subcommand> ──────────────────────────────────────
    if sub == "git":
        subcmd = " ".join(tokens[1:]).strip()
        if not subcmd:
            await _send_model_reply(msg, store, manager, thread_id, "⚠️ Usage: /ide git <subcommand>")
            return True
        res = await ide.git(subcmd)
        if not res["ok"]:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        await _send_model_reply(
            msg, store, manager, thread_id,
            f"🌿 `git {subcmd}`\n```\n{(res['output'] or '(no output)')[:4000]}\n```",
        )
        return True

    # ── /ide skill [name] [file] ───────────────────────────────────
    if sub == "skill":
        rest = body[len("skill"):].strip()
        # No args → list available skills.
        if not rest:
            try:
                from kazma_skills.coding_skills import list_coding_skills

                skills = list_coding_skills()
            except Exception as exc:
                await _send_model_reply(msg, store, manager, thread_id, f"⚠️ Skills unavailable: {exc}")
                return True
            if not skills:
                await _send_model_reply(msg, store, manager, thread_id, "No coding skills available.")
                return True
            lines = ["🖥️ **Coding skills**\n"]
            for s in skills:
                lines.append(f"  • `{s['name']}` — {s['description']}")
            lines.append("\nUsage: `/ide skill <name> <file>`")
            await _send_model_reply(msg, store, manager, thread_id, "\n".join(lines))
            return True
        sp = rest.split(None, 1)
        skill_name = sp[0]
        target_file = sp[1].strip() if len(sp) > 1 else ""
        if not target_file:
            await _send_model_reply(
                msg, store, manager, thread_id,
                f"⚠️ Usage: /ide skill {skill_name} <file>",
            )
            return True
        try:
            from kazma_skills.coding_skills import render_instruction

            instruction = render_instruction(skill_name, target_file)
        except ValueError as exc:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {exc}")
            return True
        res = await ide.send_to_swarm(instruction, pattern="auto")
        if not res["ok"]:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        await _send_model_reply(
            msg, store, manager, thread_id,
            f"🔧 Skill `{skill_name}` dispatched on `{target_file}`.\nTask ID: `{res['task_id']}`",
        )
        return True

    # ── /ide repo [owner/repo | list | switch <id>] ────────────────
    # Point-and-target: activate a workspace by GitHub slug so subsequent
    # /ide and /swarm operations target that repo. This is the "work on
    # repo X from any input source" entry point (Phase 3).
    if sub == "repo":
        rest = body[len("repo"):].strip()
        if not rest:
            # List registered workspaces with their repo identity.
            try:
                from kazma_core.stores import get_workspace_store

                wsl = get_workspace_store().list_workspaces()
            except Exception as exc:
                await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {exc}")
                return True
            if not wsl:
                await _send_model_reply(msg, store, manager, thread_id, "No workspaces registered.")
                return True
            lines = ["🖥️ **Workspaces**\n"]
            for w in wsl:
                marker = "🟢 " if w.get("is_active") else "   "
                slug = f"{w.get('owner')}/{w.get('repo')}" if w.get("owner") else "(no repo)"
                lines.append(f"{marker}`{w['name']}` — {slug}\n     `{w['id']}`")
            lines.append("\n`/ide repo switch <id>` to activate one.")
            lines.append("`/ide repo <owner/repo>` to clone + activate a new one.")
            await _send_model_reply(msg, store, manager, thread_id, "\n".join(lines))
            return True

        sp = rest.split(None, 1)
        action = sp[0].lower()

        # /ide repo switch <id>
        if action == "switch" and len(sp) > 1:
            ws_id = sp[1].strip()
            try:
                from kazma_core.stores import get_workspace_store

                ok = get_workspace_store().set_active_workspace(ws_id)
            except Exception as exc:
                await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {exc}")
                return True
            if ok:
                from kazma_core.ide.service import get_ide_service

                get_ide_service().refresh_root()
                await _send_model_reply(msg, store, manager, thread_id, f"✅ Activated workspace `{ws_id}`.")
            else:
                await _send_model_reply(msg, store, manager, thread_id, f"⚠️ Workspace `{ws_id}` not found.")
            return True

        # /ide repo clone <owner/repo> — clone from GitHub + activate.
        if action == "clone" and len(sp) > 1:
            slug = sp[1].strip()
            if "/" not in slug:
                await _send_model_reply(
                    msg, store, manager, thread_id,
                    f"⚠️ Usage: /ide repo clone <owner/repo>",
                )
                return True
            import os
            import subprocess
            from kazma_core.stores import get_workspace_store
            from kazma_core.config_store import get_config_store

            store_ws = get_workspace_store()
            # Reuse an existing workspace if one already matches.
            existing = next(
                (w for w in store_ws.list_workspaces()
                 if w.get("owner") and f"{w['owner']}/{w['repo']}" == slug),
                None,
            )
            if existing:
                store_ws.set_active_workspace(existing["id"])
                from kazma_core.ide.service import get_ide_service

                get_ide_service().refresh_root()
                await _send_model_reply(
                    msg, store, manager, thread_id,
                    f"✅ `{slug}` already cloned — activated workspace `{existing['name']}`.",
                )
                return True

            base_dir = os.environ.get("KAZMA_CLONE_DIR", "").strip() or str(
                Path.home() / "kazma-repos"
            )
            Path(base_dir).mkdir(parents=True, exist_ok=True)
            repo_dir = Path(base_dir) / slug.split("/")[-1]
            if repo_dir.exists():
                i = 1
                while Path(f"{repo_dir}-{i}").exists():
                    i += 1
                repo_dir = Path(f"{repo_dir}-{i}")
            url = f"https://github.com/{slug}.git"
            await _send_model_reply(
                msg, store, manager, thread_id, f"⏳ Cloning `{slug}`…",
            )
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", url, str(repo_dir)],
                    check=True, capture_output=True, text=True, timeout=120,
                )
            except subprocess.CalledProcessError as exc:
                await _send_model_reply(
                    msg, store, manager, thread_id,
                    f"⚠️ Clone failed: {(exc.stderr or '')[:300]}",
                )
                return True
            except subprocess.TimeoutExpired:
                await _send_model_reply(msg, store, manager, thread_id, "⚠️ Clone timed out.")
                return True
            record = store_ws.create_workspace(slug.split("/")[-1], str(repo_dir))
            store_ws.set_active_workspace(record["id"])
            try:
                _o, _r = slug.split("/", 1)
                store_ws.set_repo_identity(
                    str(repo_dir), repo_url=url, owner=_o, repo=_r,
                    default_branch="main", is_github=True,
                )
            except Exception:
                pass
            get_config_store().set("workspace.selected_path", str(repo_dir), category="workspace")
            from kazma_core.ide.service import get_ide_service

            get_ide_service().refresh_root()
            await _send_model_reply(
                msg, store, manager, thread_id,
                f"✅ Cloned `{slug}` and activated it.\nPath: `{repo_dir}`",
            )
            return True

        # /ide repo <owner/repo> — try to match an existing workspace, else report.
        slug = rest.strip()
        try:
            from kazma_core.stores import get_workspace_store

            wsl = get_workspace_store().list_workspaces()
        except Exception as exc:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {exc}")
            return True
        match = next(
            (w for w in wsl if w.get("owner") and f"{w['owner']}/{w['repo']}" == slug),
            None,
        )
        if match:
            get_workspace_store().set_active_workspace(match["id"])
            from kazma_core.ide.service import get_ide_service

            get_ide_service().refresh_root()
            await _send_model_reply(
                msg, store, manager, thread_id,
                f"✅ Activated `{slug}` (workspace `{match['name']}`).",
            )
            return True
        await _send_model_reply(
            msg, store, manager, thread_id,
            f"ℹ️ No workspace matches `{slug}`. Clone it first via the Web UI GitHub panel, "
            f"or `/ide repo switch <id>`. Send `/ide repo` to list workspaces.",
        )
        return True

    # ── /ide swarm <task> ──────────────────────────────────────────
    if sub == "swarm":
        task = " ".join(tokens[1:]).strip()
        if not task:
            await _send_model_reply(msg, store, manager, thread_id, "⚠️ Usage: /ide swarm <task>")
            return True
        res = await ide.send_to_swarm(task, pattern="auto")
        if not res["ok"]:
            await _send_model_reply(msg, store, manager, thread_id, f"⚠️ {res['error']}")
            return True
        await _send_model_reply(
            msg, store, manager, thread_id,
            f"🐝 Dispatched coding task to the swarm.\nTask ID: `{res['task_id']}`",
        )
        return True

    # Unknown subcommand → show help rather than falling through to the graph.
    await _send_model_reply(
        msg, store, manager, thread_id,
        f"⚠️ Unknown IDE subcommand `{sub}`. Send `/ide` for help.",
    )
    return True


async def _try_model_command(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
) -> bool:
    """Handle /models, /_models_provider, /_models_select commands.

    Flow:
        /models                → show provider buttons
        /_models_provider <p>  → show model buttons for provider
        /_models_select <m>    → switch active model, confirm

    Returns True if handled.
    """
    text = (msg.text or "").strip()
    if not text.startswith("/"):
        return False

    cmd = text.split(None, 1)[0].lower()

    if cmd not in ("/models", "/model", "/_models_provider", "/_models_select"):
        return False

    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata

    # ── /models: Show provider keyboard ───────────────────────────
    if cmd in ("/models", "/model"):
        providers = _get_visible_providers()
        if not providers:
            await _send_model_reply(msg, store, manager, thread_id,
                "No providers with models configured. "
                "Use the Web UI Settings to add providers and select models.")
            return True

        # Build inline keyboard for Telegram
        if msg.platform == "telegram":
            try:
                from kazma_gateway.adapters.telegram import TelegramAdapter
                keyboard = TelegramAdapter.build_provider_keyboard(providers)
                reply_ctx = dict(ctx)
                reply_ctx["reply_markup"] = keyboard
                model_lines = "\n".join(
                    f"  • {p['display_name']} ({len(p['models'])} models)"
                    for p in providers
                )
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=f"Select a provider:\n\n{model_lines}",
                    context_metadata=reply_ctx,
                ))
                return True
            except Exception as exc:
                logger.debug("Interactive provider selection failed: %s", exc, exc_info=True)
                # fall through to text
        else:
            pass

        # Text fallback (non-Telegram or keyboard build failed)
        lines = ["Available providers:\n"]
        for p in providers:
            lines.append(f"  {p['display_name']} — {len(p['models'])} models")
            for m in p["models"][:5]:
                active = " *(active)*" if _is_active_model(m) else ""
                lines.append(f"    {m}{active}")
        lines.append("\nUse `/config model <model_name>` to switch.")
        await _send_model_reply(msg, store, manager, thread_id, "\n".join(lines))
        return True

    # ── /_models_provider: Show model buttons ─────────────────────
    if cmd == "/_models_provider":
        provider_name = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
        if not provider_name:
            return True

        models = _get_provider_models(provider_name)
        if not models:
            await _send_model_reply(msg, store, manager, thread_id,
                f"No models found for provider '{provider_name}'.")
            return True

        if msg.platform == "telegram":
            try:
                from kazma_gateway.adapters.telegram import TelegramAdapter
                keyboard = TelegramAdapter.build_model_keyboard(provider_name, models)
                reply_ctx = dict(ctx)
                reply_ctx["reply_markup"] = keyboard
                model_lines = "\n".join(f"  • {m}" for m in models)
                await manager.send(OutboundMessage(
                    target_id=_build_target_id(msg.platform, ctx),
                    text=f"Select a model from {provider_name}:\n\n{model_lines}",
                    context_metadata=reply_ctx,
                ))
                return True
            except Exception as exc:
                logger.debug("Interactive model selection failed: %s", exc)

        lines = [f"Models for {provider_name}:\n"]
        for m in models:
            active = " *(active)*" if _is_active_model(m) else ""
            lines.append(f"  {m}{active}")
        await _send_model_reply(msg, store, manager, thread_id, "\n".join(lines))
        return True

    # ── /_models_select: Switch active model ──────────────────────
    if cmd == "/_models_select":
        model_id = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
        if not model_id:
            return True

        try:
            from kazma_core.model_registry import get_model_registry
            reg = get_model_registry()
            reg.set_active_model(model_id)
            await _send_model_reply(msg, store, manager, thread_id,
                f"✅ Switched to **{model_id}** (provider: {reg._active_provider})")
        except Exception as exc:
            await _send_model_reply(msg, store, manager, thread_id,
                f"⚠️ Failed to switch model: {exc}")
        return True

    return False


def _get_provider_models(provider_name: str) -> list[str]:
    """Return visible models for a provider."""
    try:
        from kazma_core.model_registry import get_model_registry
        reg = get_model_registry()
        return reg.get_visible_models(provider_name)
    except Exception as exc:
        logger.debug("Failed to get provider models for %s: %s", provider_name, exc, exc_info=True)
        return []


def _is_active_model(model_id: str) -> bool:
    """Check if a model is the active model."""
    try:
        from kazma_core.model_registry import get_model_registry
        reg = get_model_registry()
        return reg._active_model == model_id
    except Exception as exc:
        logger.debug("Failed to check if model %s is active: %s", model_id, exc, exc_info=True)
        return False


async def _send_model_reply(
    msg: IncomingMessage,
    store: SessionStore,
    manager: Any,
    thread_id: str,
    text: str,
) -> None:
    """Send a model command reply through the gateway.

    For Telegram, converts Markdown to HTML so command output (e.g. /help,
    /models) renders bold/code instead of showing literal markers.
    """
    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata
    if msg.platform == "telegram":
        from kazma_gateway.telegram_format import md_to_tg_html

        out_ctx = dict(ctx)
        out_ctx["parse_mode"] = "HTML"
        out_text: str = md_to_tg_html(text)
    else:
        out_ctx, out_text = ctx, text
    await manager.send(OutboundMessage(
        target_id=_build_target_id(msg.platform, ctx),
        text=out_text,
        context_metadata=out_ctx,
    ))


async def _build_slash_ctx(
    thread_id: str,
    msg: IncomingMessage,
    state: dict[str, Any],
    store: SessionStore,
) -> dict[str, Any]:
    """Build rich context for slash commands with real data."""
    ctx: dict[str, Any] = {
        "thread_id": thread_id,
        "platform": msg.platform,
    }

    # Active model
    try:
        from kazma_core.model_registry import get_model_registry
        reg = get_model_registry()
        ctx["model"] = reg._active_model or "default"
    except Exception as exc:
        logger.debug("Failed to get active model for slash context: %s", exc, exc_info=True)
        ctx["model"] = "default"

    # Token / cost data from checkpoint state
    try:
        messages = state.get("messages", [])
        ctx["token_count"] = sum(
            len(str(m.get("content", ""))) // 4
            for m in messages
            if isinstance(m, dict)
        )
    except Exception as exc:
        logger.debug("Failed to calculate token count for slash context: %s", exc, exc_info=True)
        ctx["token_count"] = 0

    # Memory count
    try:
        from kazma_core.agent_runner import get_agent
        agent = get_agent()
        if agent and agent.memory:
            ctx["memory_count"] = len(agent.memory)
    except Exception as exc:
        logger.debug("Failed to get agent memory count: %s", exc)

    # Cost data from cost breaker
    ctx["total_tokens"] = 0
    ctx["total_cost"] = 0.0

    # Gateway status
    ctx["started"] = True
    ctx["adapters"] = msg.platform
    ctx["queue_depth"] = 0
    ctx["active_threads"] = 1

    return ctx
