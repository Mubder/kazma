"""Where a person's code runs, and whether it reaches the network (Spaces S4-05).

Code the agent runs for anyone but the admin -- ``python_exec`` -- runs only
in a container, never on the machine Kazma runs on, and with no network.

* :func:`code_rule` is the one answer, for the work running now (its bound
  person): ``container_only`` -- no host fallback, whatever the lab switches
  say -- and ``network``.
* ``tools.code_exec`` asks it: ``local_exec_forbidden`` (no host fallback);
  the Docker jail runs with ``--network none`` for everyone; and E2B, which
  always has the internet, is never used for a person's code.
* **No network for members' code, for now** (the review of S4-05). A
  container on the engine's default network reached this computer's
  services through its LAN address, the router's admin page and, on a
  cloud machine, the metadata address -- not only the internet. Network for
  members' code comes with S4-05b, once Kazma can limit it to the public
  internet on its own engine. Until then :func:`allowed_network` answers
  False whatever is stored (a stored ``code_network`` from the first build
  of S4-05 reads as not allowed), People refuses to turn it on
  (``people.set_code_network``: ``code_network_not_yet``), and ``network``
  is always False.
* The admin's own code keeps the rules it had (the owner's space may run on
  the machine per the execution policy; every other space a container; the
  jail with no network). Work no person asked for, once Spaces is enforced,
  runs as a person: a container or nothing.
* A chat-app turn runs as the person whose linked account sent it (Spaces
  stage 3, ``spaces.chat_apps.chat_place``), so their code is held here as
  their web turns are.
* S2-12's member rules (``spaces.tools.MEMBER_DEVELOPMENT``) already keep a
  person other than the admin off every host process that runs a project's
  code (the shell, the test runner, MCP servers); this is the one code
  runner they keep, held to a container.

Only where Spaces is enforced: an install that does not enforce it reads
nothing here and its code runs as before. Blocking (the identity store):
async callers use a thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bound_asker, spaces_enforced

__all__ = ["CODE_NETWORK_POLICY", "CodeRule", "allowed_network", "code_rule"]

#: The per-person switch in ``identity`` ``policies[account]`` (S4-05b's; read
#: as not allowed until then).
CODE_NETWORK_POLICY = "code_network"


@dataclass(frozen=True)
class CodeRule:
    """How the code of the work running now runs: ``container_only`` (no
    host fallback) and ``network`` (the container reaches the network)."""

    container_only: bool
    network: bool


#: The rule while Spaces is not enforced, and the admin's: as before S4-05.
_AS_BEFORE = CodeRule(container_only=False, network=False)
#: Everyone else's: a container, no network.
_CONTAINER = CodeRule(container_only=True, network=False)


def _network_for_members() -> bool:
    """Whether a person's code may be given the network at all on this
    install. False until S4-05b: Kazma cannot yet limit a container to the
    public internet, and the engine's default network reaches this computer,
    the LAN and cloud metadata."""
    return False


def allowed_network(registry: dict[str, Any], account_id: str) -> bool:
    """Whether *account_id*'s code may use the network: never until S4-05b,
    whatever the identity store holds (*registry* is read then)."""
    if not _network_for_members():
        return False
    policy = (registry.get("policies") or {}).get(str(account_id)) or {}
    return isinstance(policy, dict) and policy.get(CODE_NETWORK_POLICY) is True


def code_rule() -> CodeRule:
    """The rule for the work running now. Reads nothing while Spaces is not
    enforced; for a person other than the admin, a container with no
    network (nothing is read: no network is allowed until S4-05b)."""
    if not spaces_enforced():
        return _AS_BEFORE
    if str(bound_asker() or "") == OWNER_ACCOUNT_ID:
        return _AS_BEFORE
    return _CONTAINER
