"""The S2-12 review, findings 1, 2 and 8: a person other than the admin who
holds Development in a shared space with a project folder runs no project
code on the machine Kazma runs on.

* The gate: every tool such a person is offered is a space's own tool, a
  tool of the outside world, or one of ``spaces.tools.MEMBER_DEVELOPMENT``,
  each declared with why it starts no host process that runs the project's
  code; every other tool -- the shell, the test runner, the linter, every MCP
  server the admin set up -- is refused, offered or called. Planted tools
  are the control, and so is the allowance of before (piece 3), which
  offered the shell.
* Their git runs with hooks off and the repository's programs off, on a
  real repository: a pre-commit, post-checkout and post-merge hook never
  run for them, and a repository whose configuration names a filter driver
  or includes another file is refused before anything is staged. The
  admin's own Development is unchanged: the same hooks run for them.
* They write nothing under ``.git``.
* Finding 2: outside the owner's space a git command -- the git tools' and
  the shell's -- gets none of the machine's credentials: no SSH agent, an
  ssh with no key and no configuration file, every credential helper reset.
  The owner's space keeps them.
* Finding 8: the filesystem MCP server is refused in a shared space, to the
  admin too (N27); another server of the admin's stays theirs there.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces import tools as host_tools
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, bind_scope

pytestmark = pytest.mark.real_features

CODER = "coder"

#: A space's own tool that runs code: never on the machine outside the
#: owner's space (code_exec.local_exec_forbidden, S1-19 and S1-34).
SPACE_CODE_RUNNERS = {"python_exec"}


@pytest.fixture
def dev(monkeypatch, tmp_path):
    """A shared space with a project folder holding a git repository; the
    coder is in it and holds Development."""
    from kazma_core.security.vault import reset_posture_cache

    monkeypatch.setattr(scope, "_enforced", True)
    get_config_store().delete(identity.REGISTRY_KEY)

    def accounts(reg: dict[str, Any]) -> None:
        reg["accounts"][CODER] = {"id": CODER, "name": "Coder", "role": "member", "enabled": True}

    identity.update_registry(accounts)
    folder = tmp_path / "project"
    folder.mkdir()
    space = people.create_shared_space("Dev", kind="host", project_folder=str(folder), by=OWNER_ACCOUNT_ID)
    people.add_member(space["id"], CODER, by=OWNER_ACCOUNT_ID)
    people.set_features(CODER, {"development": True}, by=OWNER_ACCOUNT_ID)
    try:
        yield {"space": space["id"], "folder": folder}
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        reset_posture_cache()


def _offered(space: str, account: str, extra: tuple[str, ...] = ()) -> set[str]:
    from kazma_core.skills.switches import offered

    from tests._chat_space_cases import _registry

    definitions = list(_registry().get_tool_definitions())
    definitions += [{"type": "function", "function": {"name": name, "description": "", "parameters": {}}}
                    for name in extra]
    with bind_scope(space, account):
        return {d["function"]["name"] for d in offered(definitions)}


def _not_declared(offered: set[str]) -> set[str]:
    """What the gate finds: offered tools not declared to start no host
    process that runs the project's code."""
    return offered - host_tools.SPACE - host_tools.OUTSIDE - set(host_tools.MEMBER_DEVELOPMENT)


PLANTED = ("run_project_build", "mcp__deploybox__run_script")


# ── The gate ─────────────────────────────────────────────────────────────


def test_every_tool_a_member_is_offered_is_declared_or_refused(dev) -> None:
    offered = _offered(dev["space"], CODER, PLANTED)
    assert _not_declared(offered) == set(), _not_declared(offered)
    for name in ("shell_exec", "run_unit_tests", "lint_code", "format_code", "repository_metrics",
                 "mcp_read_resource", *PLANTED):
        assert name not in offered, name
        with bind_scope(dev["space"], CODER):
            assert host_tools.tool_refusal(name), name
    # Each declared tool is a Development tool, with its reason.
    assert set(host_tools.MEMBER_DEVELOPMENT) <= host_tools.DEVELOPMENT
    assert all(reason.strip() for reason in host_tools.MEMBER_DEVELOPMENT.values())
    # A space's own code runner runs in a container here, never on the host.
    from kazma_core.tools.code_exec import local_exec_forbidden

    assert SPACE_CODE_RUNNERS <= host_tools.SPACE
    with bind_scope(dev["space"], CODER):
        assert local_exec_forbidden()


def test_control_the_allowance_of_before_offers_the_shell_and_the_planted(dev, monkeypatch) -> None:
    real = host_tools.host_allowance

    def as_before() -> host_tools.HostAllowance:
        allowed = real()
        return host_tools.HostAllowance(development=allowed.development, admin=allowed.admin,
                                        why_not=allowed.why_not, why_not_admin=allowed.why_not_admin)

    monkeypatch.setattr(host_tools, "host_allowance", as_before)
    leaked = _not_declared(_offered(dev["space"], CODER, PLANTED))
    assert {"shell_exec", "run_unit_tests", *PLANTED} <= leaked, leaked


def test_the_admins_own_development_is_unchanged(dev) -> None:
    offered = _offered(dev["space"], OWNER_ACCOUNT_ID, PLANTED)
    assert {"shell_exec", "run_unit_tests", *PLANTED} <= offered
    assert not _offered(dev["space"], OWNER_ACCOUNT_ID) & host_tools.ADMIN


# ── Git, on a real repository ────────────────────────────────────────────


def _git(folder: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=folder, env=env, check=True, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout


def _repository(folder: Path, marker: Path) -> None:
    _git(folder, "init", "-q", "-b", "main")
    _git(folder, "config", "user.name", "Tester")
    _git(folder, "config", "user.email", "tester@example.invalid")
    (folder / "a.txt").write_text("one\n", encoding="utf-8")
    _git(folder, "add", "a.txt")
    _git(folder, "commit", "-q", "-m", "first")
    hooks = folder / ".git" / "hooks"
    for name in ("pre-commit", "post-commit", "post-checkout", "post-merge"):
        hook = hooks / name
        hook.write_text(f'#!/bin/sh\necho {name} >> "{marker.as_posix()}"\n', encoding="utf-8", newline="\n")
        hook.chmod(0o755)


def _work_as(space: str, account: str, folder: Path) -> str:
    from kazma_skills.native.git_github_manager import tools as git_tools

    async def work() -> str:
        (folder / "b.txt").write_text("two\n", encoding="utf-8")
        answer = await git_tools.git_commit("second", files=["b.txt"])
        await git_tools.git_checkout("side", create=True)
        (folder / "c.txt").write_text("three\n", encoding="utf-8")
        await git_tools.git_commit("third", files=["c.txt"])
        await git_tools.git_checkout("main")
        return answer + "\n" + await git_tools.git_merge("side")

    with bind_scope(space, account):
        return asyncio.run(work())


@pytest.fixture
def no_global_git(monkeypatch, tmp_path):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def test_a_members_git_runs_no_hook(dev, tmp_path, no_global_git) -> None:
    marker = tmp_path / "hooks-ran.txt"
    _repository(dev["folder"], marker)
    answer = _work_as(dev["space"], CODER, dev["folder"])
    log = _git(dev["folder"], "log", "--format=%s", "main")
    assert log.split() == ["third", "second", "first"], (log, answer)
    assert not marker.exists(), marker.read_text(encoding="utf-8")


def test_control_the_admins_git_runs_the_hooks(dev, tmp_path, no_global_git) -> None:
    marker = tmp_path / "hooks-ran.txt"
    _repository(dev["folder"], marker)
    _work_as(dev["space"], OWNER_ACCOUNT_ID, dev["folder"])
    ran = marker.read_text(encoding="utf-8").split()
    assert {"pre-commit", "post-checkout", "post-merge"} <= set(ran), ran


@pytest.mark.parametrize("setting", [
    ("filter.planted.clean", "sh -c 'echo ran'"),
    ("merge.planted.driver", "sh -c 'echo ran'"),
    ("include.path", "../project-git.conf"),
])
def test_a_repository_that_names_a_program_is_refused_to_a_member(dev, tmp_path, no_global_git, setting) -> None:
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect
    from kazma_skills.native.git_github_manager import tools as git_tools

    _repository(dev["folder"], tmp_path / "hooks-ran.txt")
    _git(dev["folder"], "config", *setting)
    (dev["folder"] / "b.txt").write_text("two\n", encoding="utf-8")
    with bind_scope(dev["space"], CODER), pytest.raises(RefusedBeforeEffect) as refused:
        asyncio.run(git_tools.git_commit("second", files=["b.txt"]))
    assert setting[0] in str(refused.value)
    assert _git(dev["folder"], "status", "--porcelain").strip() == "?? b.txt", "nothing staged"
    # Control: the admin's commit goes on.
    with bind_scope(dev["space"], OWNER_ACCOUNT_ID):
        asyncio.run(git_tools.git_commit("second", files=["b.txt"]))
    assert _git(dev["folder"], "log", "--format=%s", "-1").strip() == "second"


def test_a_member_writes_nothing_under_git(dev, tmp_path, no_global_git) -> None:
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect
    from kazma_core.tools.file_write import file_write
    from kazma_core.workspace.path_policy import check_path_access

    _repository(dev["folder"], tmp_path / "hooks-ran.txt")
    hook = dev["folder"] / ".git" / "hooks" / "pre-push"
    with bind_scope(dev["space"], CODER):
        assert not check_path_access(hook, "write").allowed
        assert not check_path_access(dev["folder"] / ".GIT" / "config", "write").allowed
        assert check_path_access(dev["folder"] / ".git" / "HEAD", "read").allowed
        assert check_path_access(dev["folder"] / "notes.txt", "write").allowed
        with pytest.raises(RefusedBeforeEffect) as refused:
            asyncio.run(file_write(str(hook), "#!/bin/sh\necho planted\n"))
    assert ".git folder" in str(refused.value) and "request_path_access" not in str(refused.value)
    assert not hook.exists()
    # Control: the admin may.
    with bind_scope(dev["space"], OWNER_ACCOUNT_ID):
        assert check_path_access(hook, "write").allowed


# ── Finding 2: none of the machine's credentials outside the owner's space ──


def _config(env: dict[str, str]) -> dict[str, str]:
    return {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"]
            for i in range(int(env.get("GIT_CONFIG_COUNT") or 0))}


def test_git_outside_the_owners_space_signs_in_with_nothing_of_the_machines(dev, monkeypatch) -> None:
    from kazma_core.safety.post_hitl import restricted_child_env
    from kazma_core.security import git_auth
    from kazma_core.security.child_env import tool_child_env

    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/owners-agent.sock")
    with bind_scope(dev["space"], OWNER_ACCOUNT_ID):
        for env in (git_auth.git_command_env(tool_child_env(), dev["folder"]),
                    restricted_child_env(cwd=str(dev["folder"]))):
            assert "SSH_AUTH_SOCK" not in env
            assert env["GIT_SSH_COMMAND"] == git_auth.NO_KEY_SSH
            assert _config(env).get("credential.helper") == ""
            assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "-F" in git_auth.NO_KEY_SSH and "IdentitiesOnly=yes" in git_auth.NO_KEY_SSH
    # Control: the owner's space keeps the machine's.
    with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        env = git_auth.git_command_env(tool_child_env(), dev["folder"])
        assert env.get("SSH_AUTH_SOCK") == "/tmp/owners-agent.sock" and "GIT_SSH_COMMAND" not in env
        assert "GIT_SSH_COMMAND" not in restricted_child_env(cwd=str(dev["folder"]))


def test_a_push_outside_the_owners_space_carries_none_of_the_machines(dev, monkeypatch) -> None:
    from kazma_core.security import git_auth
    from kazma_skills.native.git_github_manager import tools as git_tools

    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/owners-agent.sock")
    seen: list[dict[str, str]] = []

    async def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        seen.append(dict(kwargs.get("env") or {}))
        if "config" in argv:
            return subprocess.CompletedProcess(argv, 0, "git@github.com:octo/repo.git\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    async def token() -> str:
        return "the-spaces-own-token"

    monkeypatch.setattr(git_tools, "run_off_loop", run)
    monkeypatch.setattr(git_tools, "_git_token", token)
    monkeypatch.setattr(git_tools, "_get_workspace", lambda: str(dev["folder"]))
    with bind_scope(dev["space"], OWNER_ACCOUNT_ID):
        asyncio.run(git_tools.git_push(branch="main"))
    assert seen, "git ran"
    for env in seen:
        assert "SSH_AUTH_SOCK" not in env and env.get("GIT_SSH_COMMAND") == git_auth.NO_KEY_SSH, env


def test_install_without_spaces_is_unchanged(monkeypatch, tmp_path) -> None:
    from kazma_core.security import git_auth
    from kazma_core.security.child_env import tool_child_env

    monkeypatch.setattr(scope, "_enforced", False)
    base = tool_child_env()
    assert git_auth.git_command_env(base, tmp_path) == base
    assert git_auth.no_verify(base) == []


# ── Finding 8: the filesystem MCP server ─────────────────────────────────


def test_the_filesystem_server_is_refused_in_a_shared_space(dev, monkeypatch) -> None:
    import kazma_core.mcp_servers_store as store

    monkeypatch.setattr(store, "list_mcp_servers", lambda *a, **k: [
        {"name": "filesystem", "workspace_bound": True}, {"name": "notes"}])
    with bind_scope(dev["space"], OWNER_ACCOUNT_ID):
        assert host_tools.tool_refusal("mcp__filesystem__read_file")
        assert host_tools.tool_refusal("mcp__notes__search") is None, "another server stays the admin's"
    with bind_scope(dev["space"], CODER):
        assert host_tools.tool_refusal("mcp__notes__search"), "no server of the admin's for a member"
    # Control: the admin's own space keeps the filesystem server.
    with bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert host_tools.tool_refusal("mcp__filesystem__read_file") is None
