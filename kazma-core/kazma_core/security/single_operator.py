"""Is this a single-operator install? One answer for every surface that
refuses multi-user mode until it is built (decision D1, audit remediation
program 2026-10-13).

Kazma is one operator per install (``docs/THREAT_MODEL.md``). Some surfaces
keep no per-user separation yet -- the swarm, chat's and the agent's document
access, and (as their audits close) the Knowledge Library and Scheduled
tasks -- so an install with platform users, or ``KAZMA_MULTI_USER``, refuses
them with a message naming why. Each surface used to ask
``multi_user_enabled()`` itself, with its own failure rule; this module is
the one rule: a mode check that cannot answer (the settings store is down)
counts as multi-user, so a surface never acts for everyone by accident.

Blocking (a settings read): async callers use ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging

__all__ = ["is_single_operator", "single_operator_refusal"]

logger = logging.getLogger(__name__)


def is_single_operator() -> bool:
    """True when no platform users exist and ``KAZMA_MULTI_USER`` is off.
    False when the check cannot answer (fail closed)."""
    from kazma_core.security.platform_rbac import multi_user_enabled

    try:
        return not multi_user_enabled()
    except Exception:
        logger.warning("[single_operator] the multi-user check failed; refusing", exc_info=True)
        return False


def single_operator_refusal(message: str) -> str | None:
    """*message* when the surface must refuse (not a single-operator
    install), else None."""
    return None if is_single_operator() else message
