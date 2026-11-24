"""Moving one's own document between one's own space and a shared one
(Spaces S2-12, plan 4.11).

"Private means in my personal space": there is no private mark on a single
thing inside a shared space. A person moves their own document from their
personal space into a shared space they are in ("Share with the space"),
or takes it back out. Taking a thing out of a shared space is allowed only
for the person who made it, and the shared space keeps a line saying it
was taken out (the document's own record: ``DocumentIngestionService.
taken_out_documents``).

The one rule (:func:`move_rule`): Spaces enforced; the person is a member
of both spaces; one of them is the person's own personal space and the
other a shared space; the document is the person's own (checked by the
document service). A move is always the person's own click on a page,
never the agent's: a model steered by text it read must not carry a private
thing into a shared space.

A chat moves by the same rule (``spaces.chat_moves``, ``kazma_ui.chat_move``);
a file in a shared folder records no maker, so files are not moved.

Blocking (the identity store, the document store): call it off the loop.
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.spaces import identity
from kazma_core.spaces.scope import spaces_enforced

__all__ = ["MoveRefused", "move_document", "move_rule"]

logger = logging.getLogger(__name__)


class MoveRefused(ValueError):
    """A move that is not the person's to make; nothing moved."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def move_rule(*, from_space: str, to_space: str, by: str) -> tuple[MoveRefused | None, bool]:
    """Why *by* may not move a thing of theirs from *from_space* to
    *to_space*, or None; and whether the move takes it out of a shared space
    (into the person's own)."""
    if not spaces_enforced():
        return MoveRefused("spaces_off", 409, "Things move between spaces once Spaces is on."), False
    reg = identity.read_registry()
    spaces = reg["spaces"]

    def member(space: str) -> bool:
        return any(m.get("account") == by and m.get("space") == space for m in reg["memberships"])

    source, target = spaces.get(from_space) or {}, spaces.get(to_space) or {}
    if from_space == to_space or not member(from_space) or not member(to_space):
        return MoveRefused("not_yours", 403, "That is not yours to move there."), False
    if source.get("erasing_at") or target.get("erasing_at"):
        return MoveRefused("not_yours", 403, "That is not yours to move there."), False
    own_source = source.get("personal_of") == by
    own_target = target.get("personal_of") == by
    shared_source = not source.get("personal_of")
    shared_target = not target.get("personal_of")
    if own_source and shared_target:
        return None, False
    if shared_source and own_target:
        return None, True
    return MoveRefused(
        "not_between_own_and_shared", 400,
        "A thing moves between your own space and a shared space you are in.",
    ), False


def move_document(document_id: str, *, from_space: str, to_space: str, by: str,
                  workspace_id: str, service: Any) -> dict[str, Any]:
    """Move *by*'s own document from *from_space* to *to_space* through the
    running document service. Raises :class:`MoveRefused`; the service's
    own refusals (not the person's document) pass through."""
    refused, taken_out = move_rule(from_space=from_space, to_space=to_space, by=by)
    if refused is not None:
        raise refused
    moved = service.move_document(
        document_id=document_id, from_tenant=from_space, to_tenant=to_space, actor_id=by,
        workspace_id=workspace_id, taken_out=taken_out,
    )
    logger.info("[spaces] %s moved a document from %s to %s (taken out: %s)",
                by, from_space, to_space, taken_out)
    return {**moved, "taken_out": taken_out}
