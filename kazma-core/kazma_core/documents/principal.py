"""Who a document surface acts as: the asker, in the asker's space (S1-12).

**History.** A document belongs to whoever ingested it, and only its owner or
an ACL row may read it. Until 2026-10-13 the Web page acted as the signed-in
name and chat ``/documents`` and the agent's tools as ``agent``, so a page
upload was invisible to chat and the agent (DOC-A3). From then until 2026-11
every surface of a single-operator install acted as one stand-in principal,
``operator``, and chat and the agent refused on an install with platform
users (decision D1).

**Now (Spaces S1-12).** A document's tenant is its space and its owner is the
account that ingested it. Every surface -- the page, chat ``/documents``, the
agent's document tools -- asks :func:`surface_scope` and acts as the scope's
asker inside the scope's space:

* **Spaces enforced:** the bound scope (``kazma_core.spaces.scope``), whose
  asker must be a member of its space. Inside a space members share everything
  (plan rule 1): each member is given an ``owner`` ACL row on the space's
  documents it lacks (``DocumentIngestionService.grant_member_access``, made by
  each document's own owner and audited; ownership never changes). Nothing
  ever crosses spaces: a document is read by its tenant first.
* **One operator, not enforced:** the owner's account, in the bound tenant as
  before (``default`` with nothing bound). The documents an install made
  before Spaces -- owned by ``operator``, ``auto-cookie``, ``agent`` -- are
  the owner's through the same grant, once, with no step for the owner to run.
* **Platform users, not enforced (the older multi-user mode):** exactly as
  before Spaces. The page acts as each signed-in user's name; chat and the
  agent refuse (:data:`MULTI_USER_REFUSAL`), because which user a chat message
  is has not been built for that mode. Spaces lifts the refusal: once it is
  enforced the asker is known on every surface.

Everything here may read the settings store (the identity store, the
platform-user list): blocking, call it off the event loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from kazma_core.db.pg_helpers import store_errors

logger = logging.getLogger(__name__)

__all__ = [
    "MULTI_USER_REFUSAL",
    "NO_SPACE_REFUSAL",
    "DocumentScope",
    "DocumentsRefused",
    "owner_actor",
    "surface_scope",
]

MULTI_USER_REFUSAL = (
    "Documents from chat and the agent work on a single-operator install, or "
    "once Spaces is on. This install has platform users, so documents belong to "
    "each user: use the Documents page."
)

NO_SPACE_REFUSAL = (
    "Documents need to know whose space this is, and no space is bound to this "
    "chat. Open Documents from a space you are a member of."
)


class DocumentsRefused(Exception):
    """A surface may not act on documents here; ``str(exc)`` says why, in
    words a person can act on."""


@dataclass(frozen=True)
class DocumentScope:
    """The space a surface reads and writes (``tenant_id``) and who it acts
    as (``actor_id``: an account id, or a user's name on an older multi-user
    install's page)."""

    tenant_id: str
    actor_id: str


def _single_operator() -> bool:
    """True unless the install has platform users (or ``KAZMA_MULTI_USER``):
    the one rule every single-operator surface asks
    (``security.single_operator``, decision D1). A check that cannot answer
    means False, so chat and the agent refuse rather than act for everyone."""
    from kazma_core.security.single_operator import is_single_operator

    return is_single_operator()


def _give_access(service: Any, tenant_id: str, member: str) -> None:
    """The member's share of the space's documents (``grant_member_access``).
    A grant that fails is logged and the surface still acts: the member reads
    the documents it owns either way."""
    from .repository import DocumentAccessError

    grant = getattr(service, "grant_member_access", None)
    if not callable(grant):
        return
    try:
        grant(tenant_id=tenant_id, member=member)
    except (DocumentAccessError, *store_errors()):
        logger.warning("[documents.principal] the member's access grant failed", exc_info=True)


def surface_scope(service: Any, *, page_user: Callable[[], str] | None = None) -> DocumentScope:
    """The space and the actor of a document surface, or :class:`DocumentsRefused`.

    *service* is the running ``DocumentIngestionService`` (or None before it
    starts: nothing is granted then). *page_user* answers the signed-in name;
    the Web page alone passes it, and it is asked only on an install of the
    older multi-user mode, where it is the page's actor. The one copy for the
    page, the chat command and the agent's tools.
    """
    from kazma_core.spaces import identity
    from kazma_core.spaces.scope import (
        NoSpaceBound,
        require_scope,
        require_space_id,
        spaces_enforced,
    )

    if spaces_enforced():
        try:
            bound = require_scope()
        except NoSpaceBound as exc:
            raise DocumentsRefused(NO_SPACE_REFUSAL) from exc
        try:
            member = identity.is_member(bound.asker, bound.space_id)
        except identity.IdentityUnavailable as exc:
            raise DocumentsRefused(
                "Accounts cannot be read right now, so documents cannot be "
                "opened. Try again in a moment."
            ) from exc
        if not member:
            # The request boundary checked membership already; a scope that
            # names a space its asker is not in never reads that space.
            raise DocumentsRefused(NO_SPACE_REFUSAL)
        scope = DocumentScope(tenant_id=bound.space_id, actor_id=bound.asker)
    elif _single_operator():
        scope = DocumentScope(tenant_id=require_space_id(), actor_id=require_scope().asker)
    elif page_user is not None:
        # The older multi-user mode's page: each user's own documents, as
        # before Spaces. Nothing is granted: users of that mode may share a
        # tenant, and sharing it was never their documents' rule.
        return DocumentScope(tenant_id=require_space_id(), actor_id=page_user())
    else:
        raise DocumentsRefused(MULTI_USER_REFUSAL)
    _give_access(service, scope.tenant_id, scope.actor_id)
    return scope


def owner_actor(service: Any, tenant_id: str) -> str | None:
    """The owner's account, acting on *tenant_id*'s documents, on a
    single-operator install that does not enforce Spaces; else None.

    For work the install's operator decides on outside a request -- a stuck
    document job's button in the run supervisor -- where no scope is bound.
    With Spaces enforced, or platform users, it is None: that work carries
    its own asker once its item of stage 1 is built (S1-21).
    """
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, spaces_enforced

    if spaces_enforced() or not _single_operator():
        return None
    _give_access(service, tenant_id, OWNER_ACCOUNT_ID)
    return OWNER_ACCOUNT_ID
