"""GitHub Git authentication without credentials in command arguments."""
from __future__ import annotations

import base64
import os

#: The machine's own ways to sign in to a git host, which are the owner's:
#: an SSH agent and askpass programs (``tool_child_env`` keeps them so the
#: owner's clones and pushes work).
_MACHINE_CREDENTIAL_VARIABLES = ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "GIT_ASKPASS", "SSH_ASKPASS")


def clone_auth_env(token: str | None) -> dict[str, str]:
    """A URL-scoped Git config header; combine with tool_child_env."""
    if not token:
        return {}
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: Basic {basic}",
    }


def clone_env(token: str | None) -> dict[str, str]:
    """The environment of a ``git clone``: a tool's child environment, the
    token as a git config header, and -- in a space other than the owner's
    once Spaces is enforced (S1-20) -- none of the machine's own credentials:
    no SSH agent, no askpass program, every credential helper reset. The
    machine's are the owner's, and a clone there signs in with the space's
    own account or with none."""
    from kazma_core.security.child_env import tool_child_env
    from kazma_core.spaces.scope import OWNER_SPACE_ID, current_scope, spaces_enforced

    env = tool_child_env(clone_auth_env(token))
    bound = current_scope()
    if not spaces_enforced() or (bound is not None and bound.space_id == OWNER_SPACE_ID):
        return env
    for name in _MACHINE_CREDENTIAL_VARIABLES:
        env.pop(name, None)
    count = int(env.get("GIT_CONFIG_COUNT") or 0)
    env[f"GIT_CONFIG_KEY_{count}"] = "credential.helper"
    env[f"GIT_CONFIG_VALUE_{count}"] = ""  # an empty value resets the helper list
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o IdentitiesOnly=yes -o IdentityFile=" + os.devnull
    return env
