"""Plan-fence contract — UI workbench vs user-facing prose.

The supervisor nudges models to open a tool-using turn with a `````plan``
fence so the Web workbench can pin a checklist. Providers (DeepSeek v4
flash in particular) then:

  1. Stream the fence as the first hop's ``content`` (with or without
     ``tool_calls``).
  2. Glue the real answer onto the closing backticks: ````Saved. …``.
  3. Or emit the fence and **stop** (no tools) — the UI shows a plan and
     the turn looks dead.

CommonMark never closes a fence unless ````` is alone on a line, so the
glued answer is swallowed into a code block. This module is the single
source of truth for splitting / normalizing that payload. SSE, WS, the
supervisor, and ``respond_node`` must all go through it.

Kill-switch: none — this is a presentation invariant, not a feature flag.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "AUTO_CONTINUE_PROMPT",
    "PLAN_EXECUTE_CONTINUE",
    "PLAN_EXECUTE_FINAL",
    "has_plan_fence",
    "is_plan_only",
    "normalize_plan_fence",
    "pick_user_facing_text",
    "prose_for_user",
    "rewrite_terminal_assistant_message",
    "should_execute_plan_only_hop",
    "split_plan_and_prose",
    "tools_ran_this_turn",
    "user_reply_text",
]

# Injected as a synthetic user turn when the model wrote a plan and stopped
# without tools. Two chances (see ``plan_only_continues``): deepseek-v4-flash
# was observed re-emitting the identical plan after the first nudge and the
# turn then died with the task silently dropped (2026-08-26 X-post incident).
PLAN_EXECUTE_CONTINUE = (
    "[KAZMA_PLAN_EXECUTE_CONTINUE] You wrote a ```plan but did not call any "
    "tools. Plan mode is NOT on. Execute that plan NOW: call the tools. "
    "Do not emit another plan fence. After tools finish, write the "
    "user-facing result. Put the closing ``` of any fence on its own line, "
    "then a blank line, then the answer."
)

# Second and final nudge — the model already ignored one execute instruction.
PLAN_EXECUTE_FINAL = (
    "[KAZMA_PLAN_EXECUTE_FINAL] You have now written that plan TWICE without "
    "calling any tools. Do NOT write another plan fence. Your next message "
    "MUST either (a) contain the actual tool_calls from the plan, or (b) "
    "directly answer the user stating exactly what stopped you. NEVER claim "
    "an action completed unless its tool result is already in this "
    "conversation."
)

# Closed fence: ```plan … ```  (closing ticks may be glued to following prose).
# ``\b`` keeps ```plantuml / ```planning blocks out of the plan split.
_CLOSED_FENCE_RE = re.compile(
    r"```plan\b[^\n]*\n?(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
# Unclosed fence: ```plan … (EOF, no closing ticks).
_OPEN_FENCE_RE = re.compile(
    r"```plan\b[^\n]*\n?(.*)$",
    re.IGNORECASE | re.DOTALL,
)
# Markdown plan heading — ONLY valid as the first line of the message (the
# supervisor nudge asks for the plan "before or alongside tool_calls", i.e.
# at the top). A "## Plan" section deep inside a rewritten document is
# content, not a workbench checklist.
_MD_PLAN_RE = re.compile(
    r"^(?:#{1,3}\s*plan\b|\*\*plan\*\*)[^\n]*\n(.*)$",
    re.IGNORECASE | re.DOTALL,
)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+\S")


def split_plan_and_prose(text: str | None) -> tuple[str, str]:
    """Return ``(plan_body, prose)`` from assistant text.

    *plan_body* is the inner list (no fence ticks). *prose* is everything
    the user should read. A glued closer (````Saved.``) is split: the
    ticks close the fence, ``Saved.`` is prose.
    """
    s = str(text or "")
    if not s.strip():
        return "", ""

    closed = _CLOSED_FENCE_RE.search(s)
    if closed:
        plan = (closed.group(1) or "").strip()
        before = s[: closed.start()].strip()
        after = s[closed.end() :]
        # Glue: ```Saved.  → strip nothing but keep the word.
        after = after.lstrip(" \t")
        if after.startswith("\n"):
            after = after.lstrip("\n")
        after = after.strip()
        prose_parts = [p for p in (before, after) if p]
        return plan, "\n\n".join(prose_parts).strip()

    opened = _OPEN_FENCE_RE.search(s)
    if opened:
        before = s[: opened.start()].strip()
        plan, rest = _split_list_then_prose(opened.group(1) or "")
        prose_parts = [p for p in (before, rest) if p]
        return plan, "\n\n".join(prose_parts).strip()

    md = _MD_PLAN_RE.match(s.lstrip())
    if md:
        plan, rest = _split_list_then_prose(md.group(1) or "")
        return plan, rest.strip()

    return "", s.strip()


def _split_list_then_prose(body: str) -> tuple[str, str]:
    """Unclosed / markdown plan: list lines stay in the plan; the rest is prose."""
    plan_lines: list[str] = []
    rest_lines: list[str] = []
    in_rest = False
    for line in (body or "").split("\n"):
        if in_rest:
            rest_lines.append(line)
            continue
        if _LIST_ITEM_RE.match(line) or not line.strip():
            plan_lines.append(line)
            continue
        in_rest = True
        rest_lines.append(line)
    return "\n".join(plan_lines).strip(), "\n".join(rest_lines).strip()


def has_plan_fence(text: str | None) -> bool:
    """True when *text* contains a workbench plan fence / Plan heading."""
    plan, _prose = split_plan_and_prose(text)
    return bool(plan)


def is_plan_only(text: str | None) -> bool:
    """True when the payload is a plan with no user-facing prose."""
    plan, prose = split_plan_and_prose(text)
    return bool(plan) and not bool(prose)


def prose_for_user(text: str | None) -> str:
    """User-facing answer with the plan fence stripped. Empty if plan-only."""
    _plan, prose = split_plan_and_prose(text)
    return prose


def user_reply_text(text: str | None) -> str:
    """Outbound reply payload for chat platforms (Telegram/Discord/Slack).

    Strips the ```plan workbench fence — platform chats cannot render it
    (Telegram showed a raw code block glued to every tool-turn reply).
    A plan-only payload (no prose) is returned as-is so the caller's
    fallback text logic still sees content. The Arabic brand spelling
    (كاظمه, never كازما) is enforced on the prose.
    """
    prose = prose_for_user(text)
    if prose.strip():
        return _brand(prose)
    return str(text or "").strip()


def normalize_plan_fence(text: str | None) -> str:
    """Canonical form: fence (if any) on its own lines, blank line, then prose.

    Closing ````` is always alone on a line. Never glue prose onto the fence.
    No-op (stripped original) when there is no plan. Also enforces the Arabic
    brand spelling (كاظمه, never كازما) — every reply boundary (respond
    persist, SSE/WS done.content, gateway) flows through here.
    """
    raw = str(text or "")
    plan, prose = split_plan_and_prose(raw)
    if not plan:
        return _brand((prose or raw).strip())
    body = plan.strip("\n")
    fence = f"```plan\n{body}\n```"
    if prose:
        return f"{fence}\n\n{_brand(prose.strip())}"
    return fence


def _brand(text: str) -> str:
    """Apply the Arabic brand-spelling guarantee (fail-open)."""
    try:
        from kazma_core.product_knowledge import enforce_arabic_brand

        return enforce_arabic_brand(text)
    except Exception:  # never break a reply over branding
        return text


def pick_user_facing_text(*candidates: str | None) -> str:
    """Choose the best user-facing assistant payload, then normalize it.

    Prefers the candidate with the most prose after the split. Ties prefer
    a still-present plan fence (workbench restore on reload). Empty if none.
    """
    scored: list[tuple[int, int, int, str]] = []
    for raw in candidates:
        if raw is None:
            continue
        s = str(raw)
        if not s.strip():
            continue
        plan, prose = split_plan_and_prose(s)
        scored.append((len(prose), 1 if plan else 0, len(s), s))
    if not scored:
        return ""
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return normalize_plan_fence(scored[0][3])


#: The supervisor's generic auto-continue prompt. A user-role message the
#: graph writes, not the user -- see ``tools_ran_this_turn``.
AUTO_CONTINUE_PROMPT = (
    "Please proceed automatically with the remaining steps and complete the task."
)


def _is_graph_nudge(text: str) -> bool:
    t = (text or "").strip()
    return t.startswith("[KAZMA_") or t == AUTO_CONTINUE_PROMPT


def tools_ran_this_turn(messages: list[dict[str, Any]] | None) -> bool:
    """True when a tool result sits after the user's last real message.

    The execute nudges are user-role messages too (``[KAZMA_...]`` and the
    auto-continue prompt), and they are not where the turn began: counting
    one as the boundary would make a turn that already acted look untouched.
    """
    for m in reversed(list(messages or [])):
        if not isinstance(m, dict):
            continue
        role = m.get("role") or m.get("type")
        if role in ("tool", "function"):
            return True
        if role in ("user", "human"):
            content = m.get("content")
            if isinstance(content, str) and _is_graph_nudge(content):
                continue
            return False
    return False


def should_execute_plan_only_hop(
    *,
    content: str,
    has_tool_calls: bool,
    tools_available: bool,
    plan_mode_kind: str,
    plan_only_continues: int,
    iteration: int,
    max_iterations: int,
    tools_ran: bool = False,
) -> bool:
    """True when the model wrote a workbench plan and stopped without acting.

    Plan mode (``plan_mode_kind=='plan'``) is inspect-only — do not execute.
    Two auto-continues per turn (``plan_only_continues``): providers that
    re-emit the identical plan after the first nudge used to end the turn
    with the task silently dropped (2026-08-26).

    A plan with nothing after it is a stall at any point. A plan WITH prose
    is a stall only before anything was done (``tools_ran`` False: "Posting
    now." with no tool call). After tools ran it is the report, often
    restating the plan it just carried out. This tested ``has_plan_fence``
    alone, and on 2026-09-24 it forced a finished X-post turn to continue
    twice: the model re-checked X, then answered three times in different
    words, the last one "Nothing is stopped — the task is already complete".
    """
    if has_tool_calls:
        return False
    if not tools_available:
        return False
    if (plan_mode_kind or "off") == "plan":
        return False
    if int(plan_only_continues or 0) >= 2:
        return False
    if int(iteration) + 1 >= max(1, int(max_iterations or 15)):
        return False
    if is_plan_only(content):
        return True
    return has_plan_fence(content) and not tools_ran


def rewrite_terminal_assistant_message(
    messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Normalize the last tool-free assistant message (copy-on-write)."""
    msgs = list(messages or [])
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if not isinstance(m, dict):
            continue
        if m.get("role") not in ("assistant", "ai"):
            continue
        if m.get("tool_calls"):
            continue
        content = m.get("content") or ""
        if not isinstance(content, str) or not content.strip():
            break
        normalized = normalize_plan_fence(content)
        if normalized != content:
            msgs[i] = {**m, "content": normalized}
        break
    return msgs
