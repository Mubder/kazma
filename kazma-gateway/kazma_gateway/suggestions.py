"""Proactive Suggestions + Automatic Tool Suggestions.

Two subsystems in one module:

1. **PostTaskSuggester** — After the agent completes a task, analyze the
   action history and emit 1-2 non-intrusive next-step suggestions.

2. **detect_tool_intent** — Given the user's raw message text, detect if
   they *implied* a tool usage without actually using one (e.g. pasted a
   URL but no read_url was called). Returns a list of hint strings.

Usage:
    from kazma_gateway.suggestions import PostTaskSuggester, detect_tool_intent

    suggester = PostTaskSuggester(enabled=True)
    hints = suggester.suggest(actions=["file_write", "git_commit"])
    # ["💡 Run tests to verify: python -m pytest tests/"]

    tool_hints = detect_tool_intent("search for best python frameworks")
    # ["💡 You can use the web_search tool to find that."]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SUGGESTIONS = 2

# Action → suggestion mapping (ordered by priority, highest first).
_ACTION_SUGGESTIONS: list[tuple[str, str]] = [
    ("file_write", "💡 Run tests to verify: python -m pytest tests/"),
    (
        "git_commit",
        "💡 Consider pushing: git push origin main",
    ),
    (
        "git_push",
        "💡 Want me to open a PR or create a release tag?",
    ),
    (
        "code_change",
        "💡 Want me to review the changes?",
    ),
    ("search", "💡 I can extract content from any of these URLs"),
    (
        "git_add",
        "💡 Consider committing: git add -A && git commit -m '...'",
    ),
]

# Tool names that imply code was modified.
_CODE_CHANGE_ACTIONS = frozenset(
    {"file_write", "file_patch", "code_exec", "shell_exec"}
)

# Tool names that are git-related.
_GIT_ACTIONS = frozenset({"git_add", "git_commit", "git_push", "git_pull"})

# Tool names related to searching / fetching.
_SEARCH_ACTIONS = frozenset({"search", "web_search", "read_url", "fetch_url"})


# ---------------------------------------------------------------------------
# PostTaskSuggester
# ---------------------------------------------------------------------------


@dataclass
class Suggestion:
    """A single suggestion with optional metadata."""

    text: str
    source_action: str = ""
    priority: int = 0  # higher = more important


class PostTaskSuggester:
    """Analyze completed actions and suggest next steps.

    Args:
        enabled: Master toggle. When False, ``suggest()`` always returns [].
        max_suggestions: Hard cap on returned suggestions (default 2).
    """

    def __init__(
        self,
        enabled: bool = True,
        max_suggestions: int = MAX_SUGGESTIONS,
    ) -> None:
        self._enabled = enabled
        self._max = max_suggestions

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def suggest(self, actions: list[str]) -> list[str]:
        """Return up to ``max_suggestions`` suggestion strings.

        Args:
            actions: Ordered list of tool/action names the agent just
                     performed (e.g. ["file_write", "git_commit"]).

        Returns:
            List of suggestion strings (empty if disabled or no matches).
        """
        if not self._enabled or not actions:
            return []

        seen_keys: set[str] = set()
        suggestions: list[Suggestion] = []

        # Normalize action names: strip prefixes like "tool_" etc.
        normalized = [self._normalize(a) for a in actions]

        for action in normalized:
            for pattern, text in _ACTION_SUGGESTIONS:
                if self._matches(action, pattern, normalized):
                    if pattern not in seen_keys:
                        seen_keys.add(pattern)
                        priority = self._priority(pattern, normalized)
                        suggestions.append(
                            Suggestion(
                                text=text,
                                source_action=pattern,
                                priority=priority,
                            )
                        )

        # Sort by priority descending, take top N.
        suggestions.sort(key=lambda s: s.priority, reverse=True)
        return [s.text for s in suggestions[: self._max]]

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    def _normalize(action: str) -> str:
        """Strip common prefixes to get the core action name."""
        for prefix in ("tool_", "mcp_", "plugin_"):
            if action.startswith(prefix):
                return action[len(prefix) :]
        return action

    @staticmethod
    def _matches(action: str, pattern: str, all_actions: list[str]) -> bool:
        """Check if an action matches a suggestion pattern.

        Supports both direct match and group match (e.g. any git action
        triggers git suggestions).
        """
        if action == pattern:
            return True
        # Any code-change action triggers "code_change" suggestions.
        if pattern == "code_change" and action in _CODE_CHANGE_ACTIONS:
            return True
        # Any git action triggers generic git suggestions.
        if pattern in ("git_commit", "git_add") and action in _GIT_ACTIONS:
            return True
        # Any search action triggers search suggestions.
        if pattern == "search" and action in _SEARCH_ACTIONS:
            return True
        return False

    @staticmethod
    def _priority(pattern: str, all_actions: list[str]) -> int:
        """Assign priority — test-after-code is highest, then git, etc."""
        if pattern == "file_write" and any(
            a in _CODE_CHANGE_ACTIONS for a in all_actions
        ):
            return 100
        if pattern in ("git_commit", "git_push"):
            return 80
        if pattern == "code_change":
            return 70
        if pattern == "git_add":
            return 60
        if pattern == "search":
            return 50
        return 40


# ---------------------------------------------------------------------------
# Automatic Tool Suggestions (detect_tool_intent)
# ---------------------------------------------------------------------------

# Compiled patterns: (regex, suggestion, implied_tool)
_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # "search for X" / "look up X" / "find X" / "google X"
    (
        re.compile(
            r"\b(?:search|look\s*up|find|google|查询|搜索)\s+(?:for\s+)?(.+)",
            re.IGNORECASE,
        ),
        "💡 You can use the web_search tool to find that.",
        "web_search",
    ),
    # URL pasted without a tool
    (
        re.compile(
            r"https?://[^\s<>\"']+",
            re.IGNORECASE,
        ),
        "💡 I can read that URL for you with the read_url tool.",
        "read_url",
    ),
    # "run this code" / "execute this" / "run the script"
    (
        re.compile(
            r"\b(?:run|execute|exec)\s+(?:this|the|my)?\s*(?:code|script|program|snippet)",
            re.IGNORECASE,
        ),
        "💡 You can use python_exec to run code safely.",
        "python_exec",
    ),
    # "install X" / "pip install X"
    (
        re.compile(
            r"\b(?:pip|npm|apt)?\s*install\s+(\S+)",
            re.IGNORECASE,
        ),
        "💡 I can run that install command via shell_exec.",
        "shell_exec",
    ),
    # "summarize this" referring to a document / file
    (
        re.compile(
            r"\b(?:summarize|summarise|tl;?dr)\s+(?:this|the|that)\s*(?:document|file|page|article|url)?",
            re.IGNORECASE,
        ),
        "💡 I can read and summarize that — point me to the URL or file path.",
        "read_url",
    ),
]


def detect_tool_intent(
    message_text: str,
    used_tools: list[str] | None = None,
) -> list[str]:
    """Detect implied tool usage from the user's message text.

    Analyzes the message for patterns that suggest a tool should be used,
    but wasn't. Returns hint strings — never auto-executes.

    Args:
        message_text: The raw user message.
        used_tools:   Optional list of tool names already invoked in this
                      turn. If a tool was already used, its hint is skipped.

    Returns:
        List of suggestion strings (may be empty).
    """
    if not message_text or not message_text.strip():
        return []

    used = set(used_tools or [])
    hints: list[str] = []
    seen_tools: set[str] = set()

    for pattern, suggestion, implied_tool in _PATTERNS:
        # Skip if the user already used the implied tool.
        if implied_tool in used:
            continue
        # Skip duplicate hints for the same implied tool.
        if implied_tool in seen_tools:
            continue

        if pattern.search(message_text):
            hints.append(suggestion)
            seen_tools.add(implied_tool)

    return hints


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------


def suggestions_from_config(config: dict[str, Any]) -> PostTaskSuggester:
    """Build a PostTaskSuggester from a kazma.yaml config dict.

    Looks for ``gateway.suggestions.enabled`` (default True).
    """
    gw = config.get("gateway", {})
    suggestions_cfg = gw.get("suggestions", {})
    enabled = suggestions_cfg.get("enabled", True)
    return PostTaskSuggester(enabled=enabled)
