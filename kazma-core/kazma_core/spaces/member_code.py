"""Where a person's code runs, and whether it reaches the network (Spaces S4-05).

Code the agent runs for anyone but the admin -- ``python_exec`` -- runs only
in a container (or E2B's microVM), never on the machine Kazma runs on, and
with no network unless the admin allowed it for that person (People ->
"Code may use the network": ``identity`` ``policies[account]["code_network"]``).

* :func:`code_rule` is the one answer, for the work running now (its bound
  person): ``container_only`` -- no host fallback, whatever the lab switches
  say -- and ``network``.
* ``tools.code_exec`` asks it: ``local_exec_forbidden`` (no host fallback),
  the Docker jail's ``--network`` (``none``, or the engine's default network
  when allowed), and E2B, which always has the internet, so a person
  without the network never runs there.
* The admin's own code keeps the rules it had (the owner's space may run on
  the machine per the execution policy; every other space a container; the
  jail with no network). Work no person asked for, once Spaces is enforced,
  runs as a person with no network allowance: a container or nothing.
* S2-12's member rules (``spaces.tools.MEMBER_DEVELOPMENT``) already keep a
  person other than the admin off every host process that runs a project's
  code (the shell, the test runner, MCP servers); this is the one code
  runner they keep, held to a container.

Only where Spaces is enforced: an install that does not enforce it reads
nothing here and its code runs as before. Blocking (the identity store):
async callers use a thread.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bound_asker, spaces_enforced

__all__ = ["CODE_NETWORK_POLICY", "CodeRule", "allowed_network", "code_rule"]

logger = logging.getLogger(__name__)

#: The per-person switch in ``identity`` ``policies[account]``.
CODE_NETWORK_POLICY = "code_network"


@dataclass(frozen=True)
class CodeRule:
    """How the code of the work running now runs: ``container_only`` (no
    host fallback) and ``network`` (the container reaches the network)."""

    container_only: bool
    network: bool


#: The rule while Spaces is not enforced, and the admin's: as before S4-05.
_AS_BEFORE = CodeRule(container_only=False, network=False)
#: A person whose allowance cannot be read, or work no person asked for.
_NONE = CodeRule(container_only=True, network=False)


def allowed_network(registry: dict[str, Any], account_id: str) -> bool:
    """Whether the admin allowed *account_id*'s code the network."""
    policy = (registry.get("policies") or {}).get(str(account_id)) or {}
    return isinstance(policy, dict) and policy.get(CODE_NETWORK_POLICY) is True


def code_rule() -> CodeRule:
    """The rule for the work running now. Blocking (the identity store)."""
    if not spaces_enforced():
        return _AS_BEFORE
    person = str(bound_asker() or "")
    if person == OWNER_ACCOUNT_ID:
        return _AS_BEFORE
    if not person:
        return _NONE
    from kazma_core.spaces import identity

    try:
        registry = identity.read_registry()
    except identity.IdentityUnavailable:
        logger.warning("[member-code] whether %s's code may use the network cannot be read: "
                       "a container with no network", person)
        return _NONE
    return CodeRule(container_only=True, network=allowed_network(registry, person))
