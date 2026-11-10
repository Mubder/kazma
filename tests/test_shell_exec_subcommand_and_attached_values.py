"""shell_exec reads git's and uv's subcommand after their global options,
refuses the options that run a program, and path-checks attached values.

2026-11-10 audit, three holes in the post-approval argument policy:

* The subcommand was the first word not starting with "-", and git's
  denylist read ``args[1]``. A global option with a separate value shifted
  it: ``git -C . config alias.x "!cmd"`` passed "git config is read-only",
  ``git -C . fetch`` passed the blocked-subcommand list, and
  ``uv --directory . run python ...`` / ``uv --color never run ...`` passed
  "uv run is refused".
* ``git grep --open-files-in-pager=<cmd>`` / ``-O<cmd>``, ``git difftool -x``,
  ``git bisect run``, ``find -fprint0`` and ``sort --compress-program`` run or
  write through options the list lacked; GNU and git also take an
  abbreviated long option (``--open-files=``).
* An attached short value (``sort -oC:\\x.bat``, ``date -f<file>``) was
  path-checked only for the few binaries with an option table.

Every refusal here is ``RefusedBeforeEffect``: nothing has run. The runner is
replaced by a recorder, so "allowed" means the policy let the command through.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.safety.shell_arguments import (
    attached_values,
    matches_blocked,
    split_subcommand,
)


@pytest.fixture
def shell(monkeypatch, tmp_path):
    """shell_exec in a workspace, with a runner that records instead of running."""
    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.safety import post_hitl
    from kazma_core.security import process_budget

    real_resolve = post_hitl.resolve_shell_binary
    # The policy is about the arguments, not what this machine has installed.
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
    (workspace / "in.txt").write_text("b\na\n", encoding="utf-8")

    async def run(command: str) -> str:
        async with workspace_path_scope(workspace):
            return await registry._tools["shell_exec"].func(command, timeout=10)

    run.ran = ran  # type: ignore[attr-defined]
    run.workspace = workspace  # type: ignore[attr-defined]
    return run


@pytest.mark.parametrize(
    "command,said",
    [
        # (a) the subcommand after git's / uv's global options
        ('git -C . config alias.x "!whoami"', "git config"),
        ("git --git-dir=.git config core.pager whoami", "git config"),
        ("git -C . fetch", "git subcommand"),
        ("git --no-pager -C . clone x", "git subcommand"),
        ("git -C . -c core.pager=whoami log", "-c"),
        ("git -ccore.pager=whoami log", "-c"),
        ("git --frobnicate config alias.x y", "Unknown git option"),
        ("uv --directory . run python -V", "uv run"),
        ("uv --color never run python -V", "uv run"),
        ("uv -qq --project . tool run cowsay", "uv tool"),
        ("uv --mystery x run python", "Unknown uv option"),
        # (b) options that run or write through another program
        ("git grep --open-files-in-pager=whoami foo", "--open-files-in-pager"),
        ("git grep --open-files=whoami foo", "--open-files"),
        ("git grep -Owhoami foo", "git grep"),
        ("git grep -iOwhoami foo", "git grep"),
        ("git difftool -x whoami", "git subcommand"),
        ("git difftool --extcmd=whoami", "--extcmd"),
        ("git bisect run whoami", "git bisect"),
        ("find . -fprint0 out.txt", "-fprint0"),
        ("sort --compress-program=whoami in.txt", "--compress-program"),
        ("sort --compress=whoami in.txt", "--compress"),
        ("sort -o out.txt in.txt", "-o"),
        ("sort -ro out.txt in.txt", "-o"),
        ("sort --output=out.txt in.txt", "--output"),
    ],
)
async def test_refused_before_anything_runs(shell, command, said):
    with pytest.raises(RefusedBeforeEffect) as refused:
        await shell(command)
    assert said in str(refused.value)
    assert shell.ran == []


async def test_an_attached_value_outside_the_workspace_is_refused(shell, tmp_path):
    outside = tmp_path / "elsewhere" / "secret.txt"
    for command in (f"date -f{outside}", f"head -c{outside}", f"wc -L{outside}"):
        with pytest.raises(RefusedBeforeEffect) as refused:
            await shell(command)
        assert "outside" in str(refused.value), command
    assert shell.ran == []


async def test_an_attached_network_path_is_refused_before_it_is_resolved(shell, monkeypatch):
    import os

    touched: list[str] = []
    real = os.path.realpath
    monkeypatch.setattr(os.path, "realpath",
                        lambda p, *a, **k: touched.append(str(p)) or real(p, *a, **k))
    with pytest.raises(RefusedBeforeEffect) as refused:
        await shell("date -f//attacker/share/x")
    assert "network" in str(refused.value)
    assert not any("attacker" in p for p in touched)


@pytest.mark.parametrize(
    "command",
    [
        "git log -3 --oneline",
        "git status --short",
        "git -C . status --short",
        "git --no-pager diff --stat",
        "git config --get user.name",
        "git -C . config --list",
        "git grep -n foo",
        "grep -n foo in.txt",
        "ls -la",
        "head -n5 in.txt",
        "cut -d, -f1 in.txt",
        "cut -d/ -f1 in.txt",
        "sort -t/ -k2 in.txt",
        "sort -r in.txt",
        "uv --directory . pip list",
        "uv -q sync",
    ],
)
async def test_ordinary_commands_still_run(shell, command):
    out = await shell(command)
    assert not out.startswith("Error:"), out
    assert len(shell.ran) == 1


def test_split_subcommand_reads_past_the_global_options():
    assert split_subcommand("git", ["-C", ".", "config", "x"]) == (2, "config")
    assert split_subcommand("git", ["--git-dir=.git", "--no-pager", "log"]) == (2, "log")
    assert split_subcommand("git", ["--work-tree", "w", "status"]) == (2, "status")
    assert split_subcommand("uv", ["--color", "never", "run"]) == (2, "run")
    assert split_subcommand("uv", ["-qq", "--directory=x", "run"]) == (2, "run")
    assert split_subcommand("git", ["--version"]) == (1, "")
    for binary, args in (("git", ["-c", "a=b", "x"]), ("git", ["--zzz", "log"]),
                         ("uv", ["--zzz", "run"])):
        with pytest.raises(ValueError):
            split_subcommand(binary, args)


def test_abbreviated_long_options_match_and_ordinary_ones_do_not():
    blocked = ("--open-files-in-pager", "--upload-pack", "-c")
    assert matches_blocked("--open-files", blocked)
    assert matches_blocked("--upload", blocked)
    assert not matches_blocked("--oneline", blocked)
    assert not matches_blocked("--", blocked)
    assert not matches_blocked("-cfoo", blocked)
    assert "C:\\x.bat" in attached_values("-oC:\\x.bat")
    assert attached_values("-la") == ["a"]
    assert attached_values("--file=x") == []


# ── Negative controls: the old rules accept each attack ─────────────────────

def _old_subcommand(args: list[str]) -> str:
    """The rule before 2026-11-10: the first word not starting with "-"."""
    return next((a for a in args[1:] if not a.startswith("-")), "")


def _old_git_denylist_word(args: list[str]) -> str:
    """git's old denylist read args[1]."""
    return args[1].lstrip("-")


def _old_path_candidates(binary: str, argument: str) -> list[str]:
    """The old candidates: the token, a known binary's short values, "=" values."""
    from kazma_core.safety.shell_arguments import short_option_values

    out = [argument]
    # The old option table knew these binaries only.
    if binary in ("grep", "jq", "zip", "unzip", "tar"):
        try:
            out += [value for _flag, value in short_option_values(binary, argument)]
        except ValueError:
            pass
    if argument.startswith("-") and "=" in argument:
        out.append(argument.split("=", 1)[1])
    return [c for c in out if c and not c.startswith("-")]


def test_control_the_old_rules_let_each_attack_through(tmp_path):
    assert _old_subcommand(["git", "-C", ".", "config", "alias.x", "!whoami"]) == "."
    assert _old_git_denylist_word(["git", "-C", ".", "fetch"]) == "C"
    assert _old_subcommand(["uv", "--directory", ".", "run", "python"]) == "."
    assert _old_subcommand(["uv", "--color", "never", "run", "python"]) == "never"
    old_exec = {"find": ("-exec", "-execdir", "-ok", "-okdir", "-delete",
                         "-fprintf", "-fprint", "-fls"),
                "git": ("--upload-pack", "--receive-pack", "--exec-path",
                        "-c", "--config-env", "--upload-archive")}
    assert "-fprint0" not in old_exec["find"]
    assert "--open-files-in-pager" not in old_exec["git"]
    outside = str(tmp_path / "elsewhere" / "x.bat")
    assert _old_path_candidates("date", f"-f{outside}") == []
    assert _old_path_candidates("sort", f"-o{outside}") == []
    # And the new reading finds the path in both.
    assert outside in attached_values(f"-f{outside}")
    assert outside in attached_values(f"-o{outside}")
    assert Path(outside).is_absolute()
