"""Processes the tools start do not inherit the server's secrets.

``shell_exec`` and ``python_exec`` always started their children from a
minimal environment. The other tools that start a process -- pytest
(``run_unit_tests``, the verify run of ``file_apply_patch_set``), ``pip`` /
``npm`` installs, ``ruff``, git, the IDE's read-only git -- passed the
server's whole environment: ``KAZMA_VAULT_KEY`` (which decrypts every stored
credential), the database URL with its password, provider API keys. Each of
those children runs code nobody reviewed: a repository's ``conftest.py`` and
tests, a package's install script, a clone's hooks. ``security/child_env.py``
is the one builder; the gate at the bottom finds every process a tool starts.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from kazma_core.security.child_env import ALLOW_ENV, _is_secret_env, tool_child_env

REPO = Path(__file__).resolve().parents[1]

# What a live server's environment carries (values are fakes).
SECRETS = {
    "KAZMA_VAULT_KEY": "vault-key-fake",
    # The suite's DB shield strips KAZMA_DATABASE_URL / DATABASE_URL as soon as
    # a test sets them, so the DSN shapes are probed under other names.
    "KAZMA_STATE_DSN": "postgresql://kazma:pw@127.0.0.1:5433/kazma",
    "KAZMA_SECRET": "secret-fake",
    "OPENAI_API_KEY": "sk-fake",
    "ANTHROPIC_API_KEY": "sk-ant-fake",
    "GITHUB_TOKEN": "ghp_fake",
    "GITHUB_PAT": "github_pat_fake",
    "NPM_TOKEN": "npm_fake",
    "AWS_SECRET_ACCESS_KEY": "aws-fake",
    "AWS_ACCESS_KEY_ID": "AKIAFAKE",
    "PGPASSWORD": "pg-fake",
    "ANALYTICS_DATABASE_URL": "postgres://u@db/x",
    "SLACK_WEBHOOK_URL": "https://hooks.slack.com/services/T/B/fake",
    "GOOGLE_APPLICATION_CREDENTIALS": "C:/keys/sa.json",
    "HTTPS_PROXY": "http://user:pw@proxy.example:8080",
    "SOME_SERVICE_URL": "https://admin:hunter2@svc.example/",
}
# What the children need, and must keep.
KEEP = {
    "PATH": os.environ.get("PATH", ""),
    "SSH_AUTH_SOCK": "/tmp/agent.sock",
    "GIT_ASKPASS": "echo",
    "HTTP_PROXY": "http://proxy.example:8080",
    "LANG": "C.UTF-8",
    "PWD": "/work",
    "NODE_ENV": "development",
}


@pytest.fixture
def server_env(monkeypatch):
    for k, v in {**SECRETS, **KEEP}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv(ALLOW_ENV, raising=False)


def test_secrets_are_dropped_and_the_rest_kept(server_env):
    env = tool_child_env()
    assert not set(SECRETS) & set(env), sorted(set(SECRETS) & set(env))
    for k in KEEP:
        assert k in env, k


def test_extra_entries_are_the_callers_and_pass_unfiltered(server_env):
    env = tool_child_env({"GIT_AUTHOR_NAME": "Kazma", "GIT_CONFIG_GLOBAL": os.devnull})
    assert env["GIT_AUTHOR_NAME"] == "Kazma"
    assert env["GIT_CONFIG_GLOBAL"] == os.devnull


def test_the_operator_can_let_a_name_through_but_never_a_kazma_one(server_env, monkeypatch):
    monkeypatch.setenv(ALLOW_ENV, "NPM_TOKEN, KAZMA_VAULT_KEY")
    env = tool_child_env()
    assert env["NPM_TOKEN"] == "npm_fake"
    assert "KAZMA_VAULT_KEY" not in env
    assert ALLOW_ENV not in env  # a KAZMA_* name itself


@pytest.mark.parametrize(
    ("name", "secret"),
    [
        ("OLDPWD", False),
        ("GIT_ASKPASS", False),
        ("SSH_ASKPASS", False),
        ("KEYBOARD_LAYOUT", False),
        ("TOKENIZERS_PARALLELISM", False),
        ("DB_PWD", True),
        ("RESTIC_PASS", True),
        ("HF_TOKEN", True),
        ("MY_CLIENT_SECRET", True),
    ],
)
def test_name_rules(name, secret):
    assert _is_secret_env(name, "x") is secret


def _child_env_names(runner) -> set[str]:
    code = "import json, os; print(json.dumps(sorted(os.environ)))"
    return set(json.loads(runner([sys.executable, "-c", code]).strip().splitlines()[-1]))


async def test_run_off_loop_defaults_to_the_secret_free_environment(server_env):
    from kazma_skills.native._subprocess import run_off_loop

    res = await run_off_loop(
        [sys.executable, "-c", "import json, os; print(json.dumps(sorted(os.environ)))"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    names = set(json.loads(res.stdout))
    assert not names & set(SECRETS), sorted(names & set(SECRETS))
    assert "PATH" in names


async def test_run_unit_tests_runs_the_workspaces_tests_without_secrets(server_env, tmp_path):
    """A repository's tests are code nobody reviewed."""
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_skills.native.code_analyzer_linter.tools import run_unit_tests

    names = sorted(SECRETS)
    (tmp_path / "test_probe.py").write_text(
        textwrap.dedent(
            f"""
            import os

            def test_no_server_secrets_here():
                leaked = [n for n in {names!r} if n in os.environ]
                assert not leaked, leaked
            """
        ),
        encoding="utf-8",
    )
    async with workspace_path_scope(tmp_path):
        out = await run_unit_tests("test_probe.py")
    assert "1 passed" in out, out


def test_the_patch_set_verify_run_has_no_secrets(server_env, tmp_path):
    from kazma_core.tools.file_apply_patch import _run_pytest

    (tmp_path / "test_probe.py").write_text(
        "import os\n\ndef test_x():\n    assert 'KAZMA_VAULT_KEY' not in os.environ\n",
        encoding="utf-8",
    )
    out = _run_pytest([str(tmp_path / "test_probe.py")], tmp_path)
    assert out.startswith("TESTS PASSED"), out


def test_commit_env_carries_the_identity_not_the_secrets(server_env, monkeypatch):
    from kazma_core import git_identity

    monkeypatch.setattr(
        git_identity, "get_bot_identity", lambda: {"name": "Kazma Agent", "email": "k@x"}
    )
    env = git_identity.get_commit_env()
    assert env["GIT_AUTHOR_NAME"] == "Kazma Agent"
    assert not set(SECRETS) & set(env)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
async def test_a_repositorys_git_hook_does_not_see_the_secrets(server_env, tmp_path, monkeypatch):
    """The real route: a clone's pre-commit hook runs on git_commit."""
    from kazma_core import git_identity
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_skills.native.git_github_manager.tools import git_commit

    monkeypatch.setattr(git_identity, "get_bot_identity", lambda: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    base = tool_child_env()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, env=base)
    dump = tmp_path / "hook-env.txt"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\nenv > '{dump.as_posix()}'\nexit 0\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    (repo / "a.txt").write_text("x", encoding="utf-8")
    async with workspace_path_scope(repo):
        await git_commit("probe", files=["a.txt"])
    seen = dump.read_text(encoding="utf-8", errors="replace")
    assert "PATH=" in seen, "the hook did not run; the test proves nothing"
    leaked = [k for k in SECRETS if f"{k}=" in seen]
    assert not leaked, leaked


# ── the gate ─────────────────────────────────────────────────────────────

_SPAWN_DIRS = (
    "kazma-core/kazma_core/tools",
    "kazma-core/kazma_core/agent/tool_builtins",
    "kazma-core/kazma_core/ide",
    "kazma-skills/kazma_skills/native",
)
_SPAWNERS = {
    "subprocess.run", "subprocess.Popen", "subprocess.check_output",
    "subprocess.check_call", "subprocess.call",
    "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell",
}
_DEFAULTS_SAFE = {"run_off_loop"}  # fills env= with tool_child_env() itself
_BUILDERS = {"tool_child_env", "restricted_child_env", "get_commit_env"}
# Spawns whose env reaches them some other way, each with how.
_ENV_BY_OTHER_ROUTE = {
    ("kazma-core/kazma_core/tools/code_exec.py", "_run_local_subprocess"):
        "child_env is a minimal dict literal passed through **subprocess_kwargs",
    ("kazma-core/kazma_core/tools/code_exec.py", "_run_sync"):
        "the same subprocess_kwargs, copied",
    ("kazma-core/kazma_core/tools/code_exec.py", "_run_docker_jail"):
        "the docker CLI itself; the container gets no environment",
    ("kazma-core/kazma_core/tools/code_exec.py", "_run_docker_sync"):
        "the docker CLI itself; the container gets no environment",
    ("kazma-core/kazma_core/agent/tool_builtins/system.py", "_run_shell_capped"):
        "env is its parameter; shell_exec passes restricted_child_env",
}


def _is_builder_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and ast.unparse(node.func).split(".")[-1] in _BUILDERS
    )


def _env_ok(expr: ast.AST, assigned: dict[str, list[ast.AST]]) -> bool:
    if _is_builder_call(expr) or isinstance(expr, ast.Dict):
        return True
    if isinstance(expr, ast.Name):
        values = assigned.get(expr.id, [])
        return bool(values) and all(_is_builder_call(v) or isinstance(v, ast.Dict) for v in values)
    return False


def _unsafe_spawns(source: str) -> list[tuple[str, int, str]]:
    """``(function, line, why)`` for each process started with an unvetted env."""
    out: list[tuple[str, int, str]] = []
    tree = ast.parse(source)
    funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    parent: dict[ast.AST, ast.AST] = {}
    for fn in funcs:
        for child in ast.walk(fn):
            if child is not fn and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent.setdefault(child, fn)  # ast.walk visits the nearest parent first
    for fn in funcs:
        # A closure reads its enclosing functions' names: collect up the chain,
        # innermost binding first.
        assigned: dict[str, list[ast.AST]] = {}
        scope: ast.AST | None = fn
        while scope is not None:
            local: dict[str, list[ast.AST]] = {}
            for node in ast.walk(scope):
                if isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            local.setdefault(tgt.id, []).append(node.value)
            for name, values in local.items():
                assigned.setdefault(name, values)
            scope = parent.get(scope)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = ast.unparse(node.func)
            short = name.split(".")[-1]
            if name not in _SPAWNERS and short not in _DEFAULTS_SAFE:
                continue
            env = next((k.value for k in node.keywords if k.arg == "env"), None)
            if env is None:
                if short in _DEFAULTS_SAFE:
                    continue
                why = "no env= (inherits the server's)" if not any(
                    k.arg is None for k in node.keywords
                ) else "env via **kwargs"
                out.append((fn.name, node.lineno, why))
            elif not _env_ok(env, assigned):
                out.append((fn.name, node.lineno, f"env={ast.unparse(env)}"))
    # A nested function is walked by its parent too; keep the innermost name.
    seen: dict[int, tuple[str, int, str]] = {}
    for fn_name, line, why in out:
        seen[line] = (fn_name, line, why)
    return sorted(seen.values(), key=lambda t: t[1])


def test_every_process_a_tool_starts_gets_a_secret_free_environment():
    offenders = []
    for d in _SPAWN_DIRS:
        for f in sorted((REPO / d).rglob("*.py")):
            rel = f.relative_to(REPO).as_posix()
            for fn, line, why in _unsafe_spawns(f.read_text(encoding="utf-8")):
                if (rel, fn) not in _ENV_BY_OTHER_ROUTE:
                    offenders.append(f"{rel}:{line} {fn}: {why}")
    assert not offenders, (
        "A tool starts a process whose environment is not built by "
        "kazma_core.security.child_env.tool_child_env (or a minimal builder), so "
        "the child inherits KAZMA_VAULT_KEY, the database password and API keys:\n  "
        + "\n  ".join(offenders)
    )


def test_the_route_exemptions_still_exist():
    for (rel, fn), _how in _ENV_BY_OTHER_ROUTE.items():
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert fn in names, f"{rel}: {fn} is exempted but no longer defined"


def test_the_gate_sees_an_inherited_environment():
    """Negative control: the shapes these tools had are all flagged."""
    bare = "import subprocess\ndef f(cmd):\n    subprocess.run(cmd, cwd='.')\n"
    copied = "import os, subprocess\ndef f(cmd):\n    env = dict(os.environ)\n    subprocess.run(cmd, env=env)\n"
    raw = "import os, subprocess\ndef f(cmd):\n    subprocess.Popen(cmd, env=os.environ.copy())\n"
    runner = "async def f(cmd):\n    await run_off_loop(cmd, env=dict(os.environ))\n"
    good = "def f(cmd):\n    env = tool_child_env()\n    subprocess.run(cmd, env=env)\n"
    defaulted = "async def f(cmd):\n    await run_off_loop(cmd, cwd='.')\n"
    for src in (bare, copied, raw, runner):
        assert _unsafe_spawns(src), src
    assert _unsafe_spawns(good) == []
    assert _unsafe_spawns(defaulted) == []
