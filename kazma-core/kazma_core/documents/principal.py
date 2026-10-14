"""Who a document surface acts as: on a single-operator install, the operator.

2026-10-13 (DOC-A3, found by the Documents documentation review). A document
belongs to whoever ingested it, and only its owner or an ACL row may read it.
The Web page acted as the signed-in name (``auto-cookie``, a local user,
``operator`` for the shared secret, ``local`` with no sign-in); chat
``/documents`` and the agent's document tools acted as ``agent``. So a document
uploaded on the Web page was invisible to chat and the agent, and theirs to
the page, while the gateway's help said every platform sees the same
``document_id``.

Kazma is one operator per install (``docs/THREAT_MODEL.md``). On such an
install every surface acts as :data:`OPERATOR` -- the name the shared-secret
principal already had -- and documents ingested under the older names get an
``owner`` ACL row for it (``DocumentIngestionService.grant_operator_access``:
a grant by the document's owner, audited, never an ownership change). With
platform users (multi-user mode) the Web page keeps each user's own name, and
chat and the agent refuse: which user a chat message is has not been built
(decision D1 of the 2026-10-13 audit programme).
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.db.pg_helpers import store_errors

logger = logging.getLogger(__name__)

__all__ = ["MULTI_USER_REFUSAL", "OPERATOR", "operator_for"]

#: The principal every surface acts as on a single-operator install.
OPERATOR = "operator"

MULTI_USER_REFUSAL = (
    "Documents from chat and the agent work on a single-operator install. "
    "This install has platform users, so documents belong to each user: "
    "use the Documents page."
)


def _single_operator() -> bool:
    """True unless the install has platform users (or ``KAZMA_MULTI_USER``):
    the one rule every single-operator surface asks
    (``security.single_operator``, decision D1). A check that cannot answer
    means False, so chat and the agent refuse rather than act for everyone.
    Blocking: async callers use ``asyncio.to_thread``."""
    from kazma_core.security.single_operator import is_single_operator

    return is_single_operator()


def operator_for(service: Any, tenant_id: str) -> str | None:
    """:data:`OPERATOR` for a surface on a single-operator install, else None.

    First gives the operator the tenant's documents ingested under another
    name (``DocumentIngestionService.grant_operator_access``; a grant that
    fails is logged and the surface still acts as the operator -- it reads
    its own documents either way). The one copy for the Web page, the chat
    command and the agent's tools. Blocking.
    """
    from .repository import DocumentAccessError

    if not _single_operator():
        return None
    grant = getattr(service, "grant_operator_access", None)
    if callable(grant):
        try:
            grant(tenant_id=tenant_id)
        except (DocumentAccessError, *store_errors()):
            logger.warning("[documents.principal] operator access grant failed", exc_info=True)
    return OPERATOR
