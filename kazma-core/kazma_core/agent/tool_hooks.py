"""PreToolUse / PostToolUse lifecycle hooks.

Programmable callbacks around tool execution — Claude Code–style. This is
**not** a permission system: HITL, commitment, and the YAML allowlist stay
the only gates. Hooks cannot auto-approve a danger tool.

Pre-hooks may deny or rewrite arguments (rewritten args then flow through
commitment + HITL). Post-hooks observe the result and may append a short
note; they cannot undo a tool that already ran.

Sources (both optional):
  * In-process callables via ``register_pre_tool_hook`` / ``register_post_tool_hook``
  * Operator commands from YAML / ConfigStore ``agent.hooks.pre_tool`` /
    ``agent.hooks.post_tool`` (JSON on stdin, JSON on stdout)

Kill-switch: ``KAZMA_TOOL_HOOKS=0``. Empty config is a no-op.
Command hooks spawn via ``asyncio.to_thread(subprocess.run)`` (Windows
SelectorEventLoop cannot host asyncio subprocesses — AGENTS.md §23).
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_MAX_HOOKS = 8
_MAX_EXTRA_CHARS = 4000
_MAX_REASON_CHARS = 500
_DEFAULT_TIMEOUT = 10.0
_MAX_TIMEOUT = 30.0

HookFn = Callable[..., Any]


@dataclass
class ToolHookEvent:
    """Payload handed to a hook (and JSON-serialized for command hooks)."""

    hook_event_name: str  # PreToolUse | PostToolUse
    tool_name: str
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_response: dict[str, Any] | None = None


@dataclass
class ToolHookDecision:
    """Normalized hook result.

    ``decision`` is ``allow``, ``deny`` (pre only), or ``rewrite`` (pre only).
    ``extra`` is appended to the tool result by post-hooks.
    """

    decision: str = "allow"
    reason: str = ""
    tool_input: dict[str, Any] | None = None
    extra: str = ""


_pre_hooks: list[tuple[str, HookFn]] = []
_post_hooks: list[tuple[str, HookFn]] = []


def tool_hooks_enabled() -> bool:
    """False when the operator killed hooks (``KAZMA_TOOL_HOOKS=0``)."""
    raw = (os.environ.get("KAZMA_TOOL_HOOKS") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def register_pre_tool_hook(fn: HookFn, *, matcher: str = "*") -> None:
    """Register an in-process PreToolUse hook (tests / skills / extensions)."""
    _pre_hooks.append(((matcher or "*").strip() or "*", fn))


def register_post_tool_hook(fn: HookFn, *, matcher: str = "*") -> None:
    """Register an in-process PostToolUse hook."""
    _post_hooks.append(((matcher or "*").strip() or "*", fn))


def clear_tool_hooks() -> None:
    """Drop in-process hooks (tests). Does not touch YAML/ConfigStore."""
    _pre_hooks.clear()
    _post_hooks.clear()


def matcher_hits(pattern: str, tool_name: str) -> bool:
    """True when *pattern* matches *tool_name*.

    ``*`` matches all. ``a|b`` is alternation. Each part is an ``fnmatch``
    glob (so ``file_*`` works).
    """
    pat = (pattern or "*").strip()
    name = tool_name or ""
    if not pat or pat == "*":
        return True
    for part in (p.strip() for p in pat.split("|")):
        if not part:
            continue
        if part == "*" or fnmatch.fnmatch(name, part) or name == part:
            return True
    return False


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_hook_config() -> dict[str, Any]:
    """Live-read hook config. Never raises. Empty lists = no command hooks."""
    enabled = True
    pre: list[Any] = []
    post: list[Any] = []
    try:
        from kazma_core.config_loader import load_merged_yaml

        raw = load_merged_yaml() or {}
        hooks = (raw.get("agent") or {}).get("hooks") or {}
        if isinstance(hooks, dict):
            enabled = _as_bool(hooks.get("enabled"), True)
            pre = list(hooks.get("pre_tool") or [])
            post = list(hooks.get("post_tool") or [])
    except Exception:
        logger.debug("[tool_hooks] YAML load failed", exc_info=True)
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        cs_en = cs.get("agent.hooks.enabled")
        if cs_en is not None:
            enabled = _as_bool(cs_en, enabled)
        cs_pre = cs.get("agent.hooks.pre_tool")
        if isinstance(cs_pre, list):
            pre = list(cs_pre)
        cs_post = cs.get("agent.hooks.post_tool")
        if isinstance(cs_post, list):
            post = list(cs_post)
    except Exception:
        logger.debug("[tool_hooks] ConfigStore read failed", exc_info=True)
    return {"enabled": enabled, "pre_tool": pre, "post_tool": post}


def _normalize_decision(raw: Any) -> ToolHookDecision:
    if raw is None:
        return ToolHookDecision()
    if isinstance(raw, ToolHookDecision):
        return raw
    if isinstance(raw, str):
        d = raw.strip().lower()
        if d in ("deny", "block"):
            return ToolHookDecision(decision="deny")
        return ToolHookDecision()
    if not isinstance(raw, dict):
        return ToolHookDecision()

    # Claude Code aliases
    perm = str(raw.get("permissionDecision") or raw.get("decision") or "allow").strip().lower()
    if perm in ("block", "deny"):
        decision = "deny"
    elif perm in ("rewrite",) or isinstance(raw.get("updatedInput") or raw.get("tool_input"), dict):
        decision = "rewrite" if (raw.get("updatedInput") or raw.get("tool_input")) else "allow"
    else:
        decision = "allow"

    rewritten = raw.get("tool_input") or raw.get("updatedInput")
    if decision == "rewrite" and not isinstance(rewritten, dict):
        decision = "allow"
        rewritten = None
    if isinstance(rewritten, dict):
        rewritten = dict(rewritten)
        rewritten.pop("_hitl_approved", None)
    else:
        rewritten = None

    reason = str(
        raw.get("reason")
        or raw.get("permissionDecisionReason")
        or raw.get("systemMessage")
        or ""
    )[:_MAX_REASON_CHARS]
    extra = str(raw.get("extra") or raw.get("systemMessage") or "")[:_MAX_EXTRA_CHARS]
    return ToolHookDecision(
        decision=decision,
        reason=reason,
        tool_input=rewritten if decision == "rewrite" else None,
        extra=extra,
    )


async def _call_python_hook(fn: HookFn, event: ToolHookEvent) -> ToolHookDecision:
    try:
        result = fn(event)
        if asyncio.iscoroutine(result):
            result = await result
        return _normalize_decision(result)
    except Exception:
        logger.warning("[tool_hooks] in-process hook failed", exc_info=True)
        return ToolHookDecision()


def _parse_command(spec: Any) -> list[str] | str | None:
    if isinstance(spec, list) and spec and all(isinstance(x, (str, int, float)) for x in spec):
        return [str(x) for x in spec]
    if isinstance(spec, str) and spec.strip():
        text = spec.strip()
        try:
            parts = shlex.split(text, posix=(os.name != "nt"))
        except ValueError:
            parts = []
        return parts or text
    return None


def _run_command_sync(
    command: list[str] | str,
    stdin: str,
    timeout: float,
    cwd: str | None,
) -> subprocess.CompletedProcess[str]:
    if isinstance(command, str):
        return subprocess.run(
            command,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=True,
            cwd=cwd,
            encoding="utf-8",
            errors="replace",
        )
    return subprocess.run(
        command,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        cwd=cwd,
        encoding="utf-8",
        errors="replace",
    )


def _parse_stdout_json(stdout: str) -> dict[str, Any] | None:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    # Last JSON object in mixed output
    start = text.rfind("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


async def _call_command_hook(entry: dict[str, Any], event: ToolHookEvent) -> ToolHookDecision:
    command = _parse_command(entry.get("command"))
    if command is None:
        return ToolHookDecision()
    try:
        timeout = float(entry.get("timeout_seconds") or _DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = _DEFAULT_TIMEOUT
    timeout = max(1.0, min(timeout, _MAX_TIMEOUT))
    cwd: str | None = None
    try:
        from kazma_core.workspace.binding import resolve_active_root

        cwd = str(resolve_active_root())
    except Exception:
        cwd = None
    payload = {
        "hook_event_name": event.hook_event_name,
        "tool_name": event.tool_name,
        "tool_input": event.tool_input,
    }
    if event.tool_response is not None:
        payload["tool_response"] = event.tool_response
    stdin = json.dumps(payload, ensure_ascii=False)
    try:
        proc = await asyncio.to_thread(_run_command_sync, command, stdin, timeout, cwd)
    except subprocess.TimeoutExpired:
        logger.warning("[tool_hooks] command timed out matcher=%s", entry.get("matcher"))
        return ToolHookDecision()
    except Exception:
        logger.warning("[tool_hooks] command failed matcher=%s", entry.get("matcher"), exc_info=True)
        return ToolHookDecision()

    parsed = _parse_stdout_json(proc.stdout or "")
    if proc.returncode == 2:
        reason = ""
        if parsed:
            reason = str(parsed.get("reason") or parsed.get("permissionDecisionReason") or "")
        if not reason:
            reason = (proc.stderr or proc.stdout or "hook exit 2")[:_MAX_REASON_CHARS]
        return ToolHookDecision(decision="deny", reason=reason[:_MAX_REASON_CHARS])
    if proc.returncode not in (0, None):
        logger.warning(
            "[tool_hooks] command exit %s matcher=%s stderr=%s",
            proc.returncode,
            entry.get("matcher"),
            (proc.stderr or "")[:200],
        )
        return ToolHookDecision()
    return _normalize_decision(parsed)


def _iter_command_entries(raw: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw[:_MAX_HOOKS]:
        if not isinstance(item, dict):
            continue
        if not item.get("command"):
            continue
        out.append(item)
    return out


async def apply_pre_tool_hooks(
    tool_name: str,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Run PreToolUse hooks.

    Returns ``(deny_payload, arguments)``. *deny_payload* is an execute()
    result dict when a hook blocks; otherwise ``None`` and *arguments* may
    have been rewritten. Fail-open on hook errors. Never skips HITL.
    """
    args = dict(arguments or {})
    if not tool_hooks_enabled():
        return None, args
    cfg = load_hook_config()
    if not cfg.get("enabled", True):
        python_hooks = []
        command_entries = []
    else:
        python_hooks = list(_pre_hooks)
        command_entries = _iter_command_entries(list(cfg.get("pre_tool") or []))
    if not python_hooks and not command_entries:
        return None, args

    event = ToolHookEvent(
        hook_event_name="PreToolUse",
        tool_name=tool_name,
        tool_input=dict(args),
    )

    async def _apply(decision: ToolHookDecision) -> dict[str, Any] | None:
        nonlocal args, event
        if decision.decision == "deny":
            reason = decision.reason or "pre-tool hook denied"
            logger.info("[tool_hooks] PreToolUse deny tool=%s reason=%s", tool_name, reason)
            return {
                "content": f"[hook] blocked {tool_name}: {reason}",
                "is_error": True,
            }
        if decision.decision == "rewrite" and isinstance(decision.tool_input, dict):
            args = dict(decision.tool_input)
            args.pop("_hitl_approved", None)
            event.tool_input = dict(args)
        return None

    for matcher, fn in python_hooks:
        if not matcher_hits(matcher, tool_name):
            continue
        denied = await _apply(await _call_python_hook(fn, event))
        if denied is not None:
            return denied, args

    for entry in command_entries:
        if not matcher_hits(str(entry.get("matcher") or "*"), tool_name):
            continue
        denied = await _apply(await _call_command_hook(entry, event))
        if denied is not None:
            return denied, args

    return None, args


async def apply_post_tool_hooks(
    tool_name: str,
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Run PostToolUse hooks. Appends ``extra`` notes. Never raises."""
    if not isinstance(result, dict):
        return result
    if not tool_hooks_enabled():
        return result
    try:
        cfg = load_hook_config()
    except Exception:
        return result
    if not cfg.get("enabled", True):
        python_hooks = []
        command_entries = []
    else:
        python_hooks = list(_post_hooks)
        command_entries = _iter_command_entries(list(cfg.get("post_tool") or []))
    if not python_hooks and not command_entries:
        return result

    event = ToolHookEvent(
        hook_event_name="PostToolUse",
        tool_name=tool_name,
        tool_input=dict(arguments or {}),
        tool_response={
            "content": str(result.get("content") or "")[:8000],
            "is_error": bool(result.get("is_error")),
        },
    )
    extras: list[str] = []

    async def _collect(decision: ToolHookDecision) -> None:
        note = (decision.extra or "").strip()
        if note:
            extras.append(note[:_MAX_EXTRA_CHARS])

    for matcher, fn in python_hooks:
        if not matcher_hits(matcher, tool_name):
            continue
        await _collect(await _call_python_hook(fn, event))

    for entry in command_entries:
        if not matcher_hits(str(entry.get("matcher") or "*"), tool_name):
            continue
        await _collect(await _call_command_hook(entry, event))

    if not extras:
        return result
    out = dict(result)
    blob = "\n".join(extras)[:_MAX_EXTRA_CHARS]
    body = str(out.get("content") or "")
    out["content"] = f"{body}\n\n[hook] {blob}" if body else f"[hook] {blob}"
    return out
