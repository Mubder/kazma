"""A git command a tool runs never writes one of Kazma's own files.

``git checkout <branch>`` writes every file the branch holds differently,
an ignored file too: git takes an ignored file as expendable. On a
workspace that is Kazma's install folder, a branch tracking
``kazma.local.yaml`` replaced the owner's configuration -- the approval gate
and hook commands with it -- through ``git_checkout``, ``git_merge``,
``git_pull`` or ``shell_exec git checkout`` (2026-11-15 defensive check).
``safety.git_tree_writes`` lists the files each such command writes and
refuses it when one is Kazma's (``is_kazma_store(path, write=True)``).

Real repositories; the repository is the process's working folder, which
Kazma reads its configuration from. Each refusal has its witness: git
itself overwriting the ignored file, the check switched off.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path

import pytest
from kazma_core.agent.tool_refusal import KnownEffectError, RefusedBeforeEffect
from kazma_core.safety import git_tree_writes as gtw
from kazma_core.safety import shell_paths

ROOT = Path(__file__).resolve().parents[1]
OWNER = "safety:\n  hitl:\n    enabled: true\n"
PLANTED = "safety:\n  hitl:\n    enabled: false\n"


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                         text=True, stdin=subprocess.DEVNULL)
    return res.stdout.strip()


@pytest.fixture
def install(monkeypatch, tmp_path):
    """A repository that is Kazma's install folder, holding the owner's
    ignored ``kazma.local.yaml``, with a branch ``planted`` that tracks one."""
    global_cfg = tmp_path / "global.gitconfig"
    global_cfg.write_text("[user]\n\tname = t\n\temail = t@example.invalid\n"
                          "[init]\n\tdefaultBranch = main\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "kazma"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("kazma.local.yaml\n", encoding="utf-8")
    (repo / "README.md").write_text("Kazma\n", encoding="utf-8")
    (repo / "kazma.yaml").write_text("agent: {}\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "README.md", "kazma.yaml")
    _git(repo, "commit", "-q", "-m", "start")
    _git(repo, "branch", "-M", "main")
    # The planted branch tracks the configuration file the owner keeps ignored.
    _git(repo, "checkout", "-q", "-b", "planted")
    (repo / "kazma.local.yaml").write_text(PLANTED, encoding="utf-8")
    _git(repo, "add", "-f", "kazma.local.yaml")
    _git(repo, "commit", "-q", "-m", "planted")
    _git(repo, "checkout", "-q", "main")
    # A harmless branch.
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "README.md").write_text("Kazma, the agent\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "readme")
    _git(repo, "checkout", "-q", "main")
    (repo / "kazma.local.yaml").write_text(OWNER, encoding="utf-8")
    monkeypatch.chdir(repo)  # Kazma reads its configuration from here
    return repo


def _owner_config_intact(repo: Path) -> bool:
    return (repo / "kazma.local.yaml").read_text(encoding="utf-8") == OWNER


# ── the witness: git itself ──────────────────────────────────────────────


def test_witness_git_overwrites_the_ignored_configuration(install):
    """What the check stands between: a plain checkout replaces the file."""
    _git(install, "checkout", "-q", "planted")
    assert (install / "kazma.local.yaml").read_text(encoding="utf-8") == PLANTED


# ── the shell's git commands ─────────────────────────────────────────────


@pytest.mark.parametrize("words", [
    ["checkout", "planted"],
    ["checkout", "planted", "--"],
    ["checkout", "-m", "planted"],
    ["switch", "planted"],
    ["switch", "--detach", "planted"],
    ["merge", "planted"],
    ["merge", "--no-ff", "-m", "msg", "planted"],
    ["merge", "--", "planted"],
    ["checkout", "planted", "--", "."],
    ["restore", "--source", "planted", "."],
    ["restore", "--source=planted", "--", "kazma.local.yaml"],
    ["cherry-pick", "planted"],
    ["cherry-pick", "main..planted"],
    ["-C", ".", "checkout", "planted"],
])
def test_a_command_that_writes_the_configuration_is_refused(install, words):
    refused = gtw.git_command_refusal(words, install)
    assert refused and "kazma.local.yaml" in refused and "nothing was run" in refused
    assert _owner_config_intact(install)


@pytest.mark.parametrize("words", [
    ["checkout", "feature"],
    ["checkout", "-b", "new"],
    ["switch", "-c", "new"],
    ["checkout", "planted", "--", "README.md"],
    ["merge", "feature"],
    ["restore", "README.md"],
    ["rm", "README.md"],
    ["status"],
    ["log", "-3"],
])
def test_a_command_that_writes_only_project_files_runs(install, words):
    assert gtw.git_command_refusal(words, install) is None


def test_a_remote_branch_checkout_creates_from_is_judged(install):
    """``git checkout x`` with only ``origin/x`` creates x from it."""
    planted = _git(install, "rev-parse", "planted")
    _git(install, "update-ref", "refs/remotes/origin/fromremote", planted)
    refused = gtw.git_command_refusal(["checkout", "fromremote"], install)
    assert refused and "kazma.local.yaml" in refused


def test_the_previous_branch_dash_is_judged(install):
    _git(install, "checkout", "-q", "planted")
    (install / "kazma.local.yaml").write_text(OWNER, encoding="utf-8")
    # back on main, the owner's file in place; "-" is planted again
    _git(install, "checkout", "-q", "-f", "main")
    (install / "kazma.local.yaml").write_text(OWNER, encoding="utf-8")
    refused = gtw.git_command_refusal(["checkout", "-"], install)
    assert refused and "kazma.local.yaml" in refused


def test_removing_or_reverting_tracked_configuration_is_refused(install):
    assert "kazma.yaml" in (gtw.git_command_refusal(["rm", "-r", "."], install) or "")
    (install / "kazma.yaml").write_text("agent: {changed: true}\n", encoding="utf-8")
    assert "kazma.yaml" in (gtw.git_command_refusal(["stash"], install) or "")
    assert "kazma.yaml" in (gtw.git_command_refusal(["restore", "."], install) or "")
    assert gtw.git_command_refusal(["rm", "--cached", "kazma.yaml"], install) is None


def test_a_list_that_cannot_be_read_refuses(install, monkeypatch):
    real = gtw._read

    def failing(location, cwd, args):
        if args[:1] == ["diff"]:
            raise gtw._UnknownFilesError("fatal: broken")
        return real(location, cwd, args)

    monkeypatch.setattr(gtw, "_read", failing)
    refused = gtw.git_command_refusal(["checkout", "planted"], install)
    assert refused and "could not tell" in refused


def test_shell_exec_asks_it_before_the_card(install, monkeypatch):
    monkeypatch.setattr("kazma_core.workspace.binding.resolve_active_root", lambda: str(install))
    refused = shell_paths._shell_target_refusal_sync("git checkout planted")
    assert refused and "kazma.local.yaml" in refused
    assert shell_paths._shell_target_refusal_sync("git checkout feature") is None


def test_control_without_the_check_the_shell_lets_it_through(install, monkeypatch):
    monkeypatch.setattr("kazma_core.workspace.binding.resolve_active_root", lambda: str(install))
    monkeypatch.setattr(gtw, "git_command_refusal", lambda words, cwd: None)
    assert shell_paths._shell_target_refusal_sync("git checkout planted") is None


# ── the git tools ────────────────────────────────────────────────────────


@pytest.fixture
def tools(install, monkeypatch):
    from kazma_skills.native.git_github_manager import tools as git_tools

    monkeypatch.setattr(git_tools, "_get_workspace", lambda: str(install))
    return git_tools


def test_git_checkout_refuses_and_writes_nothing(install, tools):
    with pytest.raises(RefusedBeforeEffect, match="kazma.local.yaml"):
        asyncio.run(tools.git_checkout("planted"))
    assert _owner_config_intact(install)
    assert _git(install, "branch", "--show-current") == "main"
    asyncio.run(tools.git_checkout("feature"))
    assert _git(install, "branch", "--show-current") == "feature"


def test_git_merge_refuses_and_writes_nothing(install, tools):
    with pytest.raises(RefusedBeforeEffect, match="kazma.local.yaml"):
        asyncio.run(tools.git_merge("planted"))
    assert _owner_config_intact(install)
    asyncio.run(tools.git_merge("feature"))
    assert "the agent" in (install / "README.md").read_text(encoding="utf-8")


def test_git_pull_fetches_then_merges_only_what_it_checked(install, tools):
    """A pull fetches, then merges the upstream by its hash once checked."""
    assert tools._git_verb("pull") == "fetch" and tools._git_verb("push") == "push"
    planted = _git(install, "rev-parse", "planted")
    _git(install, "update-ref", "refs/remotes/origin/main", planted)
    _git(install, "remote", "add", "origin", str(install))
    _git(install, "config", "branch.main.remote", "origin")
    _git(install, "config", "branch.main.merge", "refs/heads/main")
    with pytest.raises(KnownEffectError, match="did not merge"):
        asyncio.run(tools._merge_upstream(str(install), "fetched"))
    assert _owner_config_intact(install)
    feature = _git(install, "rev-parse", "feature")
    _git(install, "update-ref", "refs/remotes/origin/main", feature)
    asyncio.run(tools._merge_upstream(str(install), "fetched"))
    assert "the agent" in (install / "README.md").read_text(encoding="utf-8")


def test_control_a_plain_pull_merge_takes_the_planted_file(install):
    """What ``git pull`` did: merge the upstream unchecked."""
    planted = _git(install, "rev-parse", "planted")
    _git(install, "update-ref", "refs/remotes/origin/main", planted)
    _git(install, "remote", "add", "origin", str(install))
    _git(install, "config", "branch.main.remote", "origin")
    _git(install, "config", "branch.main.merge", "refs/heads/main")
    _git(install, "merge", "-q", "--no-edit", "@{upstream}")
    assert (install / "kazma.local.yaml").read_text(encoding="utf-8") == PLANTED


# ── the class: every tree-changing git argv an agent can reach is checked ─

_TREE_WORDS = {"checkout", "switch", "merge", "pull", "rebase", "restore", "stash",
               "cherry-pick", "revert", "reset", "am", "apply"}
#: Functions that run one and are not an agent's: the owner's updater.
_DECLARED = {
    ("kazma-cli/kazma_cli/update.py", "*"): "the owner's own `kazma update`, never a tool",
}
_CHECKS = ("git_command_refusal", "switch_refusal", "_merge_upstream")


def _tree_changing_argvs():
    roots = ["kazma-core/kazma_core", "kazma-skills/kazma_skills", "kazma-ui/kazma_ui",
             "kazma-gateway/kazma_gateway", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui"]
    for root in roots:
        for path in (ROOT / root).rglob("*.py"):
            if "tests" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if not isinstance(node, ast.List) or not node.elts:
                        continue
                    first = node.elts[0]
                    if not (isinstance(first, ast.Constant) and first.value == "git"):
                        continue
                    words = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
                    if words & _TREE_WORDS:
                        yield path.relative_to(ROOT).as_posix(), func, ast.get_source_segment(path.read_text(encoding="utf-8"), func) or ""


def test_every_tree_changing_git_command_is_checked():
    unchecked = []
    for rel, func, source in _tree_changing_argvs():
        if (rel, "*") in _DECLARED or (rel, func.name) in _DECLARED:
            continue
        if not any(check in source for check in _CHECKS):
            unchecked.append(f"{rel}::{func.name}")
    assert not unchecked, f"tree-changing git commands with no check: {unchecked}"


def test_control_the_gate_finds_an_unchecked_one(tmp_path):
    planted = 'async def bring(cwd):\n    await run(["git", "checkout", "x"], cwd=cwd)\n'
    tree = ast.parse(planted)
    func = tree.body[0]
    source = ast.get_source_segment(planted, func) or ""
    assert not any(check in source for check in _CHECKS)
