"""Tool-loop circuit breaker — typed outcomes + per-round hard-failure credit.

Design goals:
  - Policy / user-deny / empty results do **not** trip the loop.
  - Parallel batch of N hard errors in one ToolWorker node credits **+1**,
    not +N (prevents instant trip on 3 sibling MCP path denials).
  - Shared by graph_builder and swarm InProcessWorker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = [
    "ToolOutcome",
    "BreakerState",
    "classify_tool_result",
    "classify_mcp_error",
    "detect_stagnation",
    "tool_signature",
    "update_breaker",
    "HARD_FAILURE_THRESHOLD",
    "STAGNATION_REPEAT_THRESHOLD",
    "STAGNATION_WINDOW",
]

HARD_FAILURE_THRESHOLD = 3

# Semantic stagnation: identical (tool, canonical-args) calls repeating without
# an OK outcome. Catches loops the hard-failure breaker misses — e.g. the model
# re-issuing the same no-op edit, or the same policy-denied path (policy/empty
# rounds never trip the hard breaker by design).
STAGNATION_WINDOW = 8
STAGNATION_REPEAT_THRESHOLD = 3

# Silent Windows SelectorEventLoop / Playwright deaths (AGENTS.md §23).
# These used to return is_error=False in 0ms and never trip the breaker.
_SILENT_DEATH = re.compile(
    r"NotImplementedError|"
    r"Future exception was never retrieved|"
    r"event loop (does not|cannot) implement|"
    r"SelectorEventLoop",
    re.IGNORECASE,
)

# Content patterns that indicate policy / sandbox (not tool death)
_POLICY_PATTERNS = re.compile(
    r"(outside\s+(the\s+)?(allowed|workspace)|"
    r"access\s+denied|"
    r"permission\s+denied|"
    r"not\s+allowed|"
    r"Safety:\s|"
    r"path\s+traversal|"
    r"reads?\s+outside|"
    r"writes?\s+outside|"
    r"EACCES|"
    r"ENOENT|"
    r"no\s+such\s+file|"
    r"does\s+not\s+exist|"
    r"invalid\s+path|"
    r"is\s+not\s+in\s+the\s+allowed)",
    re.IGNORECASE,
)

_USER_DENY_PATTERNS = re.compile(
    r"(denied\s+by\s+user|denied\s+by\s+hitl|hitl\s+approval\s+gate|"
    r"user\s+denied|approval\s+denied|not\s+approved)",
    re.IGNORECASE,
)

_EMPTY_PATTERNS = re.compile(
    r"(^\s*\[\]\s*$|no\s+results|no\s+matches|not\s+found\s*$|empty)",
    re.IGNORECASE,
)


class ToolOutcome(StrEnum):
    """Structured tool execution outcome for breaker policy."""

    OK = "ok"
    EMPTY = "empty"
    POLICY = "policy"
    USER_DENY = "user_deny"
    TRANSIENT = "transient"
    HARD = "hard"
    # Control-plane terminal: the gate ended the turn deliberately (e.g. an
    # unresolved/cancelled/denied commitment clarify). Not a tool death — must
    # NOT credit the hard-failure breaker (would poison the next turn) and the
    # orchestrator must route straight to RESPOND instead of handing the model
    # a retryable error. See PR3 (incident 2026-08-12 loop kill).
    TERMINAL = "terminal"


@dataclass
class BreakerState:
    consecutive_hard_rounds: int = 0
    tripped: bool = False
    last_round_hard: bool = False


def classify_mcp_error(content: str, *, is_error: bool) -> ToolOutcome:
    """Map MCP isError + text into a ToolOutcome."""
    text = (content or "").strip()
    if not is_error:
        if not text or _EMPTY_PATTERNS.search(text):
            return ToolOutcome.EMPTY
        return ToolOutcome.OK
    if _USER_DENY_PATTERNS.search(text):
        return ToolOutcome.USER_DENY
    if _POLICY_PATTERNS.search(text):
        return ToolOutcome.POLICY
    # Default MCP isError → hard (transport / unknown failure)
    # But many filesystem MCP denials are policy-worded — already caught above.
    return ToolOutcome.HARD


def classify_tool_result(result: dict[str, Any]) -> ToolOutcome:
    """Classify a tool result dict into a ToolOutcome.

    Honors explicit ``outcome`` / ``tool_outcome`` if present; otherwise
    infers from ``is_error`` + content.
    """
    explicit = result.get("outcome") or result.get("tool_outcome")
    if explicit:
        try:
            return ToolOutcome(str(explicit))
        except ValueError:
            pass

    content = str(result.get("content", "") or "")
    is_error = bool(result.get("is_error", False))
    name = str(result.get("name") or result.get("tool_name") or "")
    try:
        duration_ms = float(result.get("duration_ms")) if result.get("duration_ms") is not None else None
    except (TypeError, ValueError):
        duration_ms = None

    if _USER_DENY_PATTERNS.search(content) or "denied by user" in content.lower():
        return ToolOutcome.USER_DENY

    if _SILENT_DEATH.search(content):
        return ToolOutcome.HARD

    # Browser tools that no-op in 0ms (Playwright never spawned) used to
    # classify EMPTY and reset the breaker — a 70-round probe loop.
    if (
        name.startswith("browser_")
        and duration_ms is not None
        and duration_ms < 1.0
        and (
            is_error
            or not content.strip()
            or content.strip().lower() in ("none", "ok", "null")
        )
    ):
        return ToolOutcome.HARD

    if is_error:
        return classify_mcp_error(content, is_error=True)

    # Soft local failures often return is_error=False with Safety/Error text
    if content.startswith("Safety:") or _POLICY_PATTERNS.search(content):
        return ToolOutcome.POLICY

    if not content.strip() or content.strip() == "[]":
        return ToolOutcome.EMPTY

    return ToolOutcome.OK


def tool_signature(name: str, arguments: Any) -> str:
    """Canonical signature for a tool call (name + normalized arguments).

    Arguments are JSON-serialized with sorted keys so semantically identical
    calls with different key order produce the same signature. Serialization
    failures degrade to a repr — a unique-ish string is fine (it just won't
    match anything).
    """
    import hashlib
    import json as _json

    try:
        canon = _json.dumps(arguments or {}, sort_keys=True, default=str)
    except Exception:
        canon = repr(arguments)
    digest = hashlib.sha1(f"{name}:{canon}".encode("utf-8", "replace")).hexdigest()
    return digest[:16]


def detect_stagnation(
    signatures: list[str],
    *,
    window: int = STAGNATION_WINDOW,
    threshold: int = STAGNATION_REPEAT_THRESHOLD,
) -> str | None:
    """Detect a repeated-identical-call loop within the recent window.

    Args:
        signatures: Most recent per-call signatures (oldest → newest).
        window:     How many trailing signatures to inspect.
        threshold:  A signature occurring >= threshold times within the
                    window counts as stagnation.

    Returns:
        The offending signature if stagnant, else ``None``.
    """
    if len(signatures) < threshold:
        return None
    tail = signatures[-window:]
    counts: dict[str, int] = {}
    for sig in tail:
        counts[sig] = counts.get(sig, 0) + 1
        if counts[sig] >= threshold:
            return sig
    return None


def update_breaker(
    previous_consecutive: int,
    results: list[dict[str, Any]],
    *,
    threshold: int = HARD_FAILURE_THRESHOLD,
) -> tuple[BreakerState, list[dict[str, Any]]]:
    """Update breaker from one ToolWorker batch (one round).

    Returns:
        (new_state, results_with_outcome_stamped)

    Credit rules:
      - Stamp each result with ``outcome``.
      - If **any** result is HARD and **no** result is OK → +1 hard round.
      - If any OK → reset consecutive to 0.
      - Pure policy / empty / user_deny rounds → reset consecutive to 0
        (control-plane signals, not tool health death).
      - Cap +1 per call regardless of parallel sibling count.
    """
    stamped: list[dict[str, Any]] = []
    outcomes: list[ToolOutcome] = []
    for tr in results:
        tr2 = dict(tr)
        outcome = classify_tool_result(tr2)
        tr2["outcome"] = outcome.value
        stamped.append(tr2)
        outcomes.append(outcome)

    has_ok = ToolOutcome.OK in outcomes
    has_hard = ToolOutcome.HARD in outcomes

    consecutive = previous_consecutive
    last_hard = False

    if has_ok:
        consecutive = 0
    elif has_hard:
        consecutive = previous_consecutive + 1
        last_hard = True
    else:
        # policy / empty / user_deny only — not tool death
        consecutive = 0

    tripped = consecutive >= threshold

    if tripped:
        # Rewrite remaining messaging for the model (schema-valid tool msgs)
        for tr2 in stamped:
            if classify_tool_result(tr2) == ToolOutcome.HARD or tr2.get("is_error"):
                tr2["content"] = (
                    "SYSTEM OVERRIDE: Tool blocked due to consecutive hard tool "
                    "failures. Synthesize final answer now."
                )
                tr2["is_error"] = True
                tr2["outcome"] = ToolOutcome.HARD.value

    return (
        BreakerState(
            consecutive_hard_rounds=consecutive,
            tripped=tripped,
            last_round_hard=last_hard,
        ),
        stamped,
    )
