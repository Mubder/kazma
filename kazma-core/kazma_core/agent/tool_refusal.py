"""A tool's refusal made before it changed anything.

A mutating tool that fails after it was invoked leaves its effects unknown,
and the effect journal (``agent/effect_journal.py``) ends the turn rather
than risk repeating them. A refusal the tool makes from its arguments, its
policy or a missing connection, BEFORE it writes, sends or starts anything,
is not such a failure: nothing ran, the model can correct the call, and the
turn goes on. Live 2026-10-18, twice: an approved ``shell_exec`` refused by
the host-shell policy, then an approved ``browser_navigate`` refused because
this host cannot isolate a browser, each recorded "effects unknown; this call
must not be repeated", and each turn failed.

Raise :class:`RefusedBeforeEffect` only from code that has not yet written,
sent or started anything. ``LocalToolRegistry.execute`` answers it as an
ordinary failed call (:meth:`RefusedBeforeEffect.content`) without the
uncertainty mark. A failure after the action began stays a returned error
(inside the handler that caught it), which the registry marks uncertain.
``tests/test_tools_refuse_before_effect.py`` holds every tool that changes
something to that rule. A swarm dispatch's own tracker (``dispatch_effects``)
still counts the call: it stays conservative and only costs a retry.
"""

from __future__ import annotations

__all__ = ["KnownEffectError", "RefusedBeforeEffect", "effect_unknown"]


def effect_unknown(message: str) -> str:
    """A failure the tool returns AFTER its action began (a send the platform
    refused, a crawl that failed part way): returned as it is, and the registry
    marks it uncertain. Naming it lets the refusal gate tell it from a refusal
    made before anything ran."""
    return message


class RefusedBeforeEffect(Exception):
    """The tool refused before doing anything; the message is its answer."""

    def content(self) -> str:
        """The answer the model reads: the message as the tool would have
        returned it (an ``Error:``/``Safety:`` line or a JSON object kept as
        it is, anything else prefixed ``Error:``)."""
        text = str(self)
        if text.startswith(("Error", "Safety:", "{", "[", "⚠")):
            return text
        return f"Error: {text}"


class KnownEffectError(RefusedBeforeEffect):
    """The tool's action ran, the tool knows exactly what it did, and calling
    again is safe: an ordinary, correctable error, never "effects unknown".

    ``generate_pdf``'s check that the requested text reached the PDF: the
    file is written either way, a new call writes a new file, and the error
    asks the model to call again. Returned as ``effect_unknown`` it failed
    the turn and forbade the very retry it asked for (live 2026-10-20). Use
    it only where the effect is named in the message and a repeat duplicates
    nothing anyone sees (a send, a post, a payment never qualify).
    """
