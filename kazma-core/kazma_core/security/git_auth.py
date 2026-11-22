"""GitHub Git authentication without credentials in command arguments,
and the environment every git command a git tool runs gets.

:func:`clone_env` is a clone's; :func:`git_command_env` is every other git
command's (the git skill's commit, push, pull, fetch, checkout and merge).
Both take the machine's own credentials away outside the owner's space once
Spaces is enforced: the machine's SSH agent and askpass programs are the
owner's (S2-12 review, finding 2). :func:`git_command_env` also runs a git
command of a person other than the admin in a shared space with every
program the repository could name switched off (finding 1): an empty hooks
folder, no fsmonitor, pager, editor, signing, ``ext::`` or ``file://``
transport; and refuses before anything runs when the repository's own
configuration names a program no setting can switch off (a filter or merge
driver, a configuration include).
"""
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

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

    env = tool_child_env(clone_auth_env(token))
    if not machine_credentials_kept():
        drop_machine_credentials(env)
    return env


def machine_credentials_kept() -> bool:
    """Whether the work bound may use the machine's own ways to sign in to
    a git host: on an install that does not enforce Spaces, and in the
    owner's space. Anywhere else they are the owner's, never the space's."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID, current_scope, spaces_enforced

    if not spaces_enforced():
        return True
    bound = current_scope()
    return bound is not None and bound.space_id == OWNER_SPACE_ID


def _add_config(env: dict[str, str], key: str, value: str) -> None:
    """One more ``git -c key=value`` through the environment (command scope:
    it wins over the repository's own configuration)."""
    count = int(env.get("GIT_CONFIG_COUNT") or 0)
    env[f"GIT_CONFIG_KEY_{count}"] = key
    env[f"GIT_CONFIG_VALUE_{count}"] = value
    env["GIT_CONFIG_COUNT"] = str(count + 1)


def drop_machine_credentials(env: dict[str, str]) -> None:
    """No SSH agent, askpass program or credential helper of the machine's
    (Git for Windows' own configuration names one), and an ssh that reads no
    configuration file, signs in with no key at all and never asks: an
    agent that needs no variable (Windows' own) offers it nothing."""
    for name in _MACHINE_CREDENTIAL_VARIABLES:
        env.pop(name, None)
    _add_config(env, "credential.helper", "")  # an empty value resets the helper list
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = NO_KEY_SSH


#: The ssh a git command outside the owner's space runs: no configuration
#: file (one may name the owner's key for the host), one identity that is
#: no key (so ssh adds none of its default key files) and only it (so an
#: agent's keys are never offered), never a prompt.
NO_KEY_SSH = (
    f"ssh -F {os.devnull} -o BatchMode=yes -o IdentitiesOnly=yes -o IdentityFile={os.devnull}"
)


def set_token_header(env: dict[str, str], token: str) -> None:
    """Put *token*'s header in *env*: in the entry :func:`clone_auth_env`
    made, or as one more (a retry with a fresh token keeps every other
    setting the environment holds)."""
    header = clone_auth_env(token)
    if not header:
        return
    count = int(env.get("GIT_CONFIG_COUNT") or 0)
    for index in range(count):
        if env.get(f"GIT_CONFIG_KEY_{index}") == header["GIT_CONFIG_KEY_0"]:
            env[f"GIT_CONFIG_VALUE_{index}"] = header["GIT_CONFIG_VALUE_0"]
            return
    _add_config(env, header["GIT_CONFIG_KEY_0"], header["GIT_CONFIG_VALUE_0"])


#: What a git command of a person other than the admin runs with in a shared
#: space: none of them can start a program of the project's (or any), and
#: neither transport that runs a program on this machine is allowed.
_MEMBER_SETTINGS = (
    ("core.fsmonitor", "false"),
    ("core.pager", "cat"),
    ("core.askPass", ""),
    ("commit.gpgSign", "false"),
    ("tag.gpgSign", "false"),
    ("push.gpgSign", "false"),
    ("log.showSignature", "false"),
    ("protocol.ext.allow", "never"),
    ("protocol.file.allow", "never"),
)

#: The repository's own settings that name a program no setting above can
#: switch off: a filter driver runs on the files a command reads, a merge
#: driver on a merge, an include brings in a file the project folder holds.
#: Read in the repository's own scopes (local, worktree); a program the
#: machine's own configuration names is the machine's.
MEMBER_REFUSED_SETTINGS = (
    r"^(filter\..+\.(clean|smudge|process)"
    r"|merge\..+\.driver"
    r"|diff\..+\.(textconv|command)|diff\.external"
    r"|core\.gitproxy|interactive\.difffilter"
    r"|include\.path|includeif\..+\.path)$"
)


def _member_git_refusal(cwd: str | os.PathLike[str]) -> str | None:
    """Why a person other than the admin cannot run git in *cwd*, or None.

    Blocking (``git config``, which starts none of the programs it lists).
    A repository whose configuration cannot be read is refused: it is never
    taken for one that names nothing.
    """
    from kazma_core.safety.git_read import read_env

    try:
        res = subprocess.run(  # noqa: S603 -- git, reading its configuration
            ["git", "-c", "core.fsmonitor=false", "config", "--show-scope", "--includes",
             "--get-regexp", MEMBER_REFUSED_SETTINGS],
            cwd=str(cwd), env=read_env(), capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"git could not read this project's configuration ({type(exc).__name__})."
    if res.returncode not in (0, 1):  # 1: nothing matched
        return "git could not read this project's configuration."
    named = []
    for line in (res.stdout or "").splitlines():
        scope, _, rest = line.partition("\t")
        if scope in ("local", "worktree"):
            named.append(rest.split(" ", 1)[0])
    if not named:
        return None
    return (
        "This project's git configuration names a program git would run ("
        + ", ".join(sorted(set(named))[:6])
        + "). Only the admin runs git here while it does: ask them to remove it from "
        ".git/config, or to run this step."
    )


def git_command_env(env: dict[str, str], cwd: str | os.PathLike[str]) -> dict[str, str]:
    """The environment a git tool runs a command in, from *env*. A person
    other than the admin's command in a shared space also gets
    ``--no-verify`` (:func:`no_verify`, read from the environment).

    Blocking (the identity store, ``git config``): call it in a thread.
    Raises ``RefusedBeforeEffect`` when such a person's repository names a
    program (:func:`_member_git_refusal`). An install that does not enforce
    Spaces gets *env* back unchanged.
    """
    from kazma_core.spaces.scope import spaces_enforced

    env = dict(env)
    if not spaces_enforced():
        return env
    if not machine_credentials_kept():
        drop_machine_credentials(env)
    from kazma_core.spaces.tools import host_allowance

    if not host_allowance().member:
        return env
    refused = _member_git_refusal(cwd)
    if refused:
        from kazma_core.agent.tool_refusal import RefusedBeforeEffect

        raise RefusedBeforeEffect(refused)
    from kazma_core.safety.git_read import PROGRAM_ENV, no_hooks_dir

    _add_config(env, "core.hooksPath", no_hooks_dir())
    for key, value in _MEMBER_SETTINGS:
        _add_config(env, key, value)
    for name in PROGRAM_ENV:
        env.pop(name, None)
    # ":" is git's own "no editor": a merge or commit never opens one.
    env["GIT_EDITOR"] = ":"
    env["GIT_SEQUENCE_EDITOR"] = ":"
    return env


def no_verify(env: dict[str, str]) -> list[str]:
    """``["--no-verify"]`` for a command whose environment switches hooks off
    (:func:`git_command_env`'s, for a person other than the admin), else
    nothing: the flag and the empty hooks folder go together."""
    from kazma_core.safety.git_read import no_hooks_dir

    for index in range(int(env.get("GIT_CONFIG_COUNT") or 0)):
        if (env.get(f"GIT_CONFIG_KEY_{index}") == "core.hooksPath"
                and env.get(f"GIT_CONFIG_VALUE_{index}") == no_hooks_dir()):
            return ["--no-verify"]
    return []


def under_git_folder(path: str | os.PathLike[str]) -> bool:
    """Whether *path* is a repository's own folder or inside one (a part
    named ``.git``, any case)."""
    return any(part.lower() == ".git" for part in Path(path).parts)
