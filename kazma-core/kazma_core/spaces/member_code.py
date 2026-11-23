"""Where a person's code runs, and whether it reaches the network (Spaces
S4-05, S4-05b).

Code the agent runs for anyone but the admin -- ``python_exec`` -- runs only
in a container, never on the machine Kazma runs on, and with no network
unless the admin allowed it for them, where Kazma can hold it to the public
internet.

* :func:`code_rule` is the one answer, for the work running now (its bound
  person): ``container_only`` -- no host fallback, whatever the lab switches
  say -- and ``network``.
* ``tools.code_exec`` asks it: ``local_exec_forbidden`` (no host fallback);
  the Docker jail runs with ``--network none``, or on the ``kazma-egress``
  network for a person allowed it (``container_engine_setup.EGRESS_NETWORK``,
  checked as setup's before every run); and E2B is never used for a person's
  code (it is the install's paid key, and its internet is not this filter's).
* **The network, filtered** (S4-05b, after the review of S4-05: a container
  on the engine's default network reached this computer's services through
  its LAN address, the router's admin page and a cloud machine's metadata
  address). Only on Kazma's own engine on Windows, where setup controls
  Docker and puts in ``egress-filter.sh``: a container on ``kazma-egress``
  reaches the public internet and nothing else (no private range, no shared
  space, no link-local or metadata address, none of this computer's own
  addresses, no IPv6). :func:`network_refusal` says why it cannot be given
  here, else "": another engine (Docker Desktop, a system Docker: Kazma does
  not control their rules), the filter not put in, a public install (the
  cloud profile) or ``KAZMA_TRUST_LAN`` on. Refused, :func:`allowed_network`
  answers False whatever is stored and People refuses to turn it on
  (``people.set_code_network``, the refusal's code).
* The admin's own code keeps the rules it had (the owner's space may run on
  the machine per the execution policy; every other space a container; the
  jail with no network). Work no person asked for, once Spaces is enforced,
  runs as a person: a container or nothing, with no network.
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

__all__ = ["CODE_NETWORK_POLICY", "NETWORK_REFUSALS", "CodeRule", "allowed_network", "code_rule",
           "network_given", "network_refusal"]

#: The per-person switch in ``identity`` ``policies[account]``: read as not
#: allowed wherever :func:`network_refusal` refuses.
CODE_NETWORK_POLICY = "code_network"
#: Why a person's code cannot be given the network here, each worded on the
#: People page (``settings.people_err.<code>``).
NETWORK_REFUSALS = ("code_network_cloud", "code_network_trust_lan", "code_network_engine",
                    "code_network_no_filter")


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


def network_refusal() -> str:
    """Why a person's code cannot be given the network on this install, or
    "" when it can (:data:`NETWORK_REFUSALS`). Reads the environment and the
    setup's stamp: no call to WSL or Docker."""
    from kazma_core.security.boot_guard import env_flag
    from kazma_core.security.cloud_profile import profile_on

    if profile_on():
        return "code_network_cloud"
    if env_flag("KAZMA_TRUST_LAN"):
        return "code_network_trust_lan"
    from kazma_core.docker_cli import engine_in_force

    if engine_in_force() != "kazma":
        return "code_network_engine"
    from kazma_core.container_engine_setup import egress_installed

    if not egress_installed():
        return "code_network_no_filter"
    return ""


def allowed_network(registry: dict[str, Any], account_id: str) -> bool:
    """Whether *account_id*'s code may use the network: the admin allowed it
    (*registry*'s policy) and this install can hold it to the public
    internet (:func:`network_refusal`)."""
    policy = (registry.get("policies") or {}).get(str(account_id)) or {}
    if not (isinstance(policy, dict) and policy.get(CODE_NETWORK_POLICY) is True):
        return False
    return network_refusal() == ""


def network_given(registry: dict[str, Any] | None = None) -> bool:
    """Whether the admin gave anyone's code the network (a stored
    :data:`CODE_NETWORK_POLICY` of a person other than the admin): the one
    thing the egress filter is needed for. Until then an engine without the
    filter is whole (``container_engine_setup.check``): nothing pages, no
    check is red, ``kazma setup install`` repairs nothing. An identity store
    that cannot be read counts as nobody."""
    if registry is None:
        from kazma_core.spaces import identity

        try:
            registry = identity.read_registry()
        except identity.IdentityUnavailable:
            return False
    policies = registry.get("policies") or {}
    if not isinstance(policies, dict):
        return False
    return any(str(account) != OWNER_ACCOUNT_ID and isinstance(policy, dict)
               and policy.get(CODE_NETWORK_POLICY) is True for account, policy in policies.items())


def code_rule() -> CodeRule:
    """The rule for the work running now. Reads nothing while Spaces is not
    enforced; for a person other than the admin, a container, with the
    network where the admin allowed it and this install can filter it. An
    identity store that cannot be read, or work no person asked for: no
    network."""
    if not spaces_enforced():
        return _AS_BEFORE
    asker = str(bound_asker() or "")
    if asker == OWNER_ACCOUNT_ID:
        return _AS_BEFORE
    if not asker:
        return _CONTAINER
    from kazma_core.spaces.identity import IdentityUnavailable, read_registry

    try:
        registry = read_registry()
    except IdentityUnavailable:
        return _CONTAINER
    return CodeRule(container_only=True, network=allowed_network(registry, asker))
