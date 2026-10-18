"""A tool's refusal made before it changed anything.

A mutating tool that fails after it was invoked leaves its effects unknown,
and the effect journal (``agent/effect_journal.py``) ends the turn rather
than risk repeating them. A refusal the tool makes from its arguments or its
policy, BEFORE it starts anything, is not such a failure: nothing ran, the
model can correct the call, and the turn goes on. Live 2026-10-18: an
approved ``shell_exec`` refused by the host-shell policy was recorded as
"effects unknown; this call must not be repeated" and the turn failed.

Raise :class:`RefusedBeforeEffect` only from code that has not yet written,
sent or started anything. ``LocalToolRegistry.execute`` turns it into an
ordinary ``Error:`` result without the uncertainty mark. A swarm dispatch's
own tracker (``dispatch_effects``) still counts the call: it stays
conservative and only costs a retry.
"""

from __future__ import annotations

__all__ = ["RefusedBeforeEffect"]


class RefusedBeforeEffect(Exception):
    """The tool refused before doing anything; the message is its answer."""
