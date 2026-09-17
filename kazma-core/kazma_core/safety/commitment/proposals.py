"""Proposal-backed outbound tools.

``x_post`` / ``x_schedule_post`` / ``book_x_post`` must not be invoked as
ordinary tool calls: the commitment resolver rewrites ``text`` from the
stored proposal.

This module did not exist until 2026-09-17. The tool-worker imported it
inside ``except Exception: pass`` from 2026-09-04, so the import raised
``ImportError`` on every turn with pending tools and the filter never
ran. Commitment-authorize was the only remaining choke.
"""

from __future__ import annotations

__all__ = ["PROPOSAL_TOOLS", "is_proposal_tool"]

PROPOSAL_TOOLS: frozenset[str] = frozenset(
    {
        "x_post",
        "x_schedule_post",
        "book_x_post",
    }
)


def is_proposal_tool(name: str) -> bool:
    """True when *name* is an outbound publish that requires a proposal_id."""
    return str(name or "").strip() in PROPOSAL_TOOLS
