"""Work slashes become graph turns — control slashes stay instant.

A *control* slash (help / list / status) is answered without the LLM.
A *work* slash (research a topic, dispatch a swarm, …) is rewritten into
plain user text so the supervisor + HITL + memory run. Fast because the
rewrite is precise, not because we skip the brain.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["is_control_slash", "rewrite_work_slash"]

_SWARM_CONTROL: Final[frozenset[str]] = frozenset({
    "", "help", "status", "list", "ls", "config",
})
_RESEARCH_CONTROL: Final[frozenset[str]] = frozenset({
    "", "help", "status",
})
_IDE_WORK: Final[frozenset[str]] = frozenset({"swarm"})
_KB_WORK: Final[frozenset[str]] = frozenset({
    "add", "crawl", "refresh", "delete", "ingest",
})
_SKILL_WORK: Final[frozenset[str]] = frozenset({
    "install", "uninstall", "activate",
})

_BARE_SWARM_RE = re.compile(
    r"(?:(?:use|ask|tell)\s+(?:the\s+)?swarm\s+(?:to\s+)?|"
    r"let\s+(?:the\s+)?swarm\s+|"
    r"^swarm\s*:\s*|"
    r"^swarm\s+\S)",
    re.IGNORECASE,
)


def _first_token(text: str) -> tuple[str, str, str]:
    raw = (text or "").strip()
    parts = raw.split(None, 2)
    cmd = parts[0].lower() if parts else ""
    sub = parts[1].lower() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""
    return cmd, sub, rest


def is_control_slash(text: str) -> bool:
    """True when an existing intercept should stay (help/list/status)."""
    cmd, sub, _rest = _first_token(text)
    if cmd == "/swarm":
        return sub in _SWARM_CONTROL
    if cmd == "/research":
        return not _research_topic(text)
    if cmd in ("/documents", "/docs"):
        return True  # current surface is read-only inventory
    if cmd == "/kb":
        return sub not in _KB_WORK
    if cmd == "/skill":
        return sub not in _SKILL_WORK
    if cmd == "/ide":
        return sub not in _IDE_WORK
    return False


def _research_topic(text: str) -> str:
    raw = (text or "").strip()
    parts = raw.split(maxsplit=2)
    if len(parts) < 2:
        return ""
    if parts[1].lower() in ("deep", "full", "paper", "comprehensive"):
        return parts[2].strip() if len(parts) > 2 else ""
    if parts[1].lower() in _RESEARCH_CONTROL:
        return ""
    return raw[len("/research") :].strip()


def rewrite_work_slash(text: str) -> str | None:
    """Rewrite a work slash (or bare swarm mention) into a graph user turn.

    Returns the new user text, or None if the caller should leave the
    message alone (control slash, or not a slash/work pattern).
    """
    raw = (text or "").strip()
    if not raw:
        return None
    cmd, sub, rest = _first_token(raw)

    if cmd == "/research":
        topic = _research_topic(raw)
        if not topic:
            return None
        return (
            f"Do a thorough deep research report on: {topic}. "
            "Use the research pipeline / run_research_pipeline (or start_deep_research). "
            "Do not claim thorough research from snippets alone."
        )

    if cmd == "/swarm":
        if sub in _SWARM_CONTROL:
            return None
        if sub in ("pipeline", "consult", "fanout", "broadcast"):
            task = rest or ""
            if not task:
                return None
            return (
                f"Dispatch a swarm {sub} on this task: {task}. "
                "Use the swarm dispatch tools. Do not invent workers that are not registered."
            )
        # /swarm <worker> <task>  or  /swarm <natural language>
        body = raw[len("/swarm") :].strip()
        if not body:
            return None
        return (
            f"Dispatch the swarm on this task: {body}. "
            "Use swarm dispatch tools (named worker if the first token is a registered worker). "
            "Do not skip HITL."
        )

    if cmd == "/ide" and sub == "swarm":
        task = rest
        if not task:
            return None
        return (
            f"Dispatch a coding swarm task: {task}. "
            "Use the workspace-aware swarm send path / swarm dispatch tools."
        )

    if cmd == "/kb" and sub in _KB_WORK:
        body = raw[len("/kb") :].strip()
        return (
            f"Knowledge library request: {body}. "
            "Use the knowledge / crawl / ingest tools. Confirm before delete."
        )

    if cmd == "/skill" and sub in _SKILL_WORK:
        body = raw[len("/skill") :].strip()
        return (
            f"Agent-skill request: {body}. "
            "Use the skill install/activate tools. Install requires HITL."
        )

    if not raw.startswith("/") and _BARE_SWARM_RE.search(raw):
        cleaned = re.sub(
            r"\b(?:use|ask|tell)\s+(?:the\s+)?swarm\s+(?:to\s+)?",
            "",
            raw,
            count=1,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^\s*swarm\s*:?\s*",
            "",
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        ).strip()
        if not cleaned:
            cleaned = raw
        return (
            f"Dispatch the swarm on this task: {cleaned}. "
            "Use swarm dispatch tools. Do not invent a dispatch without a clear task."
        )

    return None
