"""Commitment Layer — Intent → Resolve → Act (plan: INTELLIGENT_AGENT_COMMITMENT_LAYER).

Phase 0 (this package): the *candidate* relative-time resolver + conflict
detection (plan §R2.4). This is the code G1 (latency) and G2 (accuracy)
measure — the same algorithms Phase 2's ``authorize_effect`` will call for
the ``remind`` act.

Phase 1/2 will add: ``side_effects`` registry, ``store`` (commitments table
+ TTL/GC), ``authorize`` (the policy gate). They land here as the plan ships.
"""

from __future__ import annotations

from .relative_time import (
    TimeExpression,
    RemindResolution,
    parse_time_expressions,
    resolve_remind,
    detect_conflicts,
    normalize_digits,
)
from .authorize import EffectDecision, authorize_effect

__all__ = [
    "TimeExpression",
    "RemindResolution",
    "parse_time_expressions",
    "resolve_remind",
    "detect_conflicts",
    "normalize_digits",
    "EffectDecision",
    "authorize_effect",
]
