"""``shell_exec`` git: the fsmonitor off, and the card names what the repository runs.

Behind its approval card, ``shell_exec`` git honoured a repository's own
configuration: a hook, a filter or diff driver, an ssh command, a pager, the
fsmonitor. The owner's decision (2026-11-11): the card stays the gate, but
every git command ``shell_exec`` runs gets ``-c core.fsmonitor=false`` before
its subcommand, and the card for a ``shell_exec`` git command names what the
repository configures that git would run (``safety.git_repo_programs``). Read
with ``git config`` and ``git rev-parse``, which run none of them. LFS and the
commit hooks the owner wants keep working: nothing else is switched off.

Real git repositories in a temp folder; a global configuration of the test's
own (``GIT_CONFIG_GLOBAL``), never the user's.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from kazma_core.safety import git_repo_programs as grp


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                   stdin=subprocess.DEVNULL)


@pytest.fixture
def isolated_git(monkeypatch, tmp_path):
    """git reads a global configuration of this test's own, which sets a
    filter of the user's (that must not be named: it is not the repository's)."""
    global_cfg = tmp_path / "global.gitconfig"
    global_cfg.write_text('[filter "userwide"]\n\tclean = user-filter\n', encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return tmp_path


def _repo(base: Path, name: str) -> Path:
    repo = base / name
    repo.mkdir()
    _git(repo, "init", "-q")
    return repo


@pytest.fixture
def configured_repo(isolated_git):
    repo = _repo(isolated_git, "configured")
    hooks = repo / ".git" / "hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    _git(repo, "config", "filter.secretfilter.clean", "secret-clean %f")
    _git(repo, "config", "filter.secretfilter.smudge", "secret-smudge %f")
    _git(repo, "config", "diff.pdf.textconv", "pdftotext")
    _git(repo, "config", "core.sshCommand", "ssh -i key")
    return repo


def test_a_repository_with_a_hook_and_a_filter_shows_both(configured_repo):
    items = grp._repository_programs(["status"], str(configured_repo))
    assert "hook pre-commit" in items
    assert "filter.secretfilter.clean = secret-clean %f" in items
    assert "filter.secretfilter.smudge = secret-smudge %f" in items
    assert "diff.pdf.textconv = pdftotext" in items
    assert "core.sshcommand = ssh -i key" in items
    assert not any(".sample" in i for i in items)
    assert not any("userwide" in i for i in items), "the user's global settings are not the repository's"


def test_a_plain_repository_shows_nothing(isolated_git):
    repo = _repo(isolated_git, "plain")
    assert grp._repository_programs(["status"], str(repo)) == []


def test_dash_c_reads_the_repository_the_command_names(isolated_git, configured_repo):
    outer = _repo(isolated_git, "outer")
    items = grp._repository_programs(["-C", str(configured_repo), "log", "-1"], str(outer))
    assert "hook pre-commit" in items


def test_a_shell_alias_is_named_only_when_it_is_the_command(isolated_git):
    repo = _repo(isolated_git, "aliased")
    _git(repo, "config", "alias.st", "!echo hi")
    assert grp._repository_programs(["status"], str(repo)) == []
    assert grp._repository_programs(["st"], str(repo)) == ["alias.st = !echo hi"]


# ── the card ────────────────────────────────────────────────────────────────


class _Asked(Exception):
    pass


async def _card_for(monkeypatch, command: str, workspace: Path) -> list[dict]:
    import langgraph.types as lg_types

    from kazma_core.agent.graph_tool_worker import tool_worker_node
    from kazma_core.agent.state import PendingToolCall
    from kazma_core.ide.workspace_scope import workspace_path_scope

    cards: list[dict] = []

    def interrupt(payload):
        cards.append(payload)
        raise _Asked

    class _Exec:
        async def execute(self, name, args):
            raise AssertionError("ran without a card")

    class _Tracer:
        def trace_tool_execution(self, **kwargs):
            pass

    monkeypatch.setattr(lg_types, "interrupt", interrupt)
    state = {
        "thread_id": "t-git-card",
        "messages": [{"role": "user", "content": "show me the status"}],
        "tool_calls_pending": [PendingToolCall(id="c1", name="shell_exec", arguments={"command": command})],
        "tool_calls_done": [], "tool_results": {}, "consecutive_tool_failures": 0,
    }
    async with workspace_path_scope(workspace):
        try:
            await tool_worker_node(state, tool_executor=_Exec(), tracer=_Tracer(),
                                   hitl_config={"enabled": True})
        except _Asked:
            pass
    return cards


@pytest.fixture
def no_yolo(monkeypatch):
    from kazma_core.safety.yolo import disable_yolo

    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    disable_yolo("t-git-card")
    yield
    disable_yolo("t-git-card")


def test_the_card_names_what_the_repository_runs(monkeypatch, configured_repo, no_yolo):
    cards = asyncio.run(_card_for(monkeypatch, "git status", configured_repo))
    assert len(cards) == 1
    notes = [n for n in cards[0]["card_notes"] if n["code"] == "git_programs"]
    assert notes and "hook pre-commit" in notes[0]["items"]
    assert "filter.secretfilter.clean = secret-clean %f" in notes[0]["items"]


def test_a_plain_repository_adds_no_note(monkeypatch, isolated_git, no_yolo):
    repo = _repo(isolated_git, "plain-card")
    cards = asyncio.run(_card_for(monkeypatch, "git status", repo))
    assert len(cards) == 1 and cards[0]["card_notes"] == []


def test_the_chat_app_card_shows_the_note_in_both_languages():
    from kazma_gateway.agent_handler.hitl import _build_approval_prompt

    note = {"code": "git_programs", "items": ["hook pre-commit", "filter.x.clean = c"]}
    base = {"tool": "shell_exec", "args": {"command": "git status"}, "interrupt_id": "g",
            "card_notes": [note]}
    english = _build_approval_prompt({**base, "card_lang": "en"}, "t", platform="telegram")["text"]
    arabic = _build_approval_prompt({**base, "card_lang": "ar"}, "t", platform="telegram")["text"]
    assert "programs git runs for this command: hook pre-commit; filter.x.clean = c" in english
    assert "برامج يشغّلها git لهذا الأمر: hook pre-commit" in arabic


# ── the command shell_exec runs carries the override ────────────────────────


@pytest.fixture
def shell(monkeypatch, tmp_path):
    """shell_exec with a runner that records instead of running."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.safety import post_hitl
    from kazma_core.security import process_budget

    real_resolve = post_hitl.resolve_shell_binary
    monkeypatch.setattr(post_hitl, "resolve_shell_binary",
                        lambda argv0, **kw: real_resolve(argv0, **kw) or argv0)
    ran: list[list[str]] = []

    async def record(args, **_kwargs):
        ran.append(list(args))
        return subprocess.CompletedProcess(args, 0, b"ok", b"")

    monkeypatch.setattr(process_budget, "run_bounded_async", record)
    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    async def run(command: str) -> str:
        async with workspace_path_scope(workspace):
            return await registry._tools["shell_exec"].func(command, timeout=10)

    run.ran = ran  # type: ignore[attr-defined]
    return run


@pytest.mark.parametrize("command", ["git status --short", "git -C . log -1 --oneline",
                                     "git --no-pager diff --stat"])
def test_every_git_command_runs_with_the_fsmonitor_off(shell, command):
    asyncio.run(shell(command))
    argv = shell.ran[0]
    assert argv[1:3] == ["-c", "core.fsmonitor=false"], argv
    assert argv[3:] == command.split()[1:]


def test_other_commands_are_unchanged(shell):
    asyncio.run(shell("ls -la"))
    assert shell.ran[0][1:] == ["-la"]


def test_control_without_the_override_the_repository_fsmonitor_would_run(shell, monkeypatch):
    monkeypatch.setattr(grp, "shell_git_argv", lambda args: list(args))
    asyncio.run(shell("git status --short"))
    assert "core.fsmonitor=false" not in shell.ran[0]


def test_the_override_does_not_open_the_dash_c_door(shell):
    """The policy still refuses a ``-c`` the call itself names."""
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect

    with pytest.raises(RefusedBeforeEffect):
        asyncio.run(shell("git -c core.pager=whoami log"))
    assert shell.ran == []
