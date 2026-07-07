"""Commands submodule — handling swarm slash-commands and interactive model selectors."""

from __future__ import annotations

import logging
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
    """Send a model command reply through the gateway."""
    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata
    await manager.send(OutboundMessage(
        target_id=_build_target_id(msg.platform, ctx),
        text=text,
        context_metadata=ctx,
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
