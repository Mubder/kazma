"""Kazma's configuration files are readable and never writable by the model.

2026-11 review, on an install whose workspace IS its install folder: an
approved ``file_write`` of ``kazma.local.yaml`` -- which
``config_loader.load_merged_yaml`` reads beside ``kazma.yaml`` -- turned
``safety.hitl.enabled`` off and added ``agent.hooks.pre_tool`` commands that
``agent/tool_hooks`` runs before every tool call. ``config_save`` refused
those keys (``safety/protected_config``); the file that sets them was any
file to every write door.

Now ``store_registry.is_kazma_store(path, write=True)`` -- the one predicate
-- also answers for Kazma's configuration files (``kazma.yaml``,
``kazma.local.yaml``, ``kazma-permissions.yaml`` where Kazma reads them, and
the data dir's swarm templates, worker registry, settings snapshot and setup
record), and every write door asks it: the file tools (through
``check_path_access``), ``shell_exec``'s values and the files ``cp``/``mv``/
``unzip`` would create, ``python_exec``'s code, the extract saver. Reads
stay open. With the check off, every door writes again (negative control).
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

from kazma_core.agent.tool_refusal import RefusedBeforeEffect

CONFIG = ("kazma.yaml", "kazma.local.yaml", "kazma-permissions.yaml")
DATA_CONFIG = ("swarm_templates.json", "swarm_registry.json", "settings_snapshot.yaml",
               "setup_record.json")


@pytest.fixture()
def install(tmp_path, monkeypatch):
    """An install whose workspace is the install folder, as on live."""
    from kazma_core import paths
    from kazma_core.tools.file_write import configure_workspace

    root = tmp_path / "install"
    data = root / "kazma-data"
    (data / "workspace").mkdir(parents=True)
    (root / ".kazma").mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_USER_HOME", str(root / ".kazma"))
    monkeypatch.setenv("KAZMA_SHELL_ALLOW_MUTATE", "1")
    monkeypatch.setenv("KAZMA_SHELL_ALLOW_ARCHIVE", "1")
    monkeypatch.setattr(paths, "installed_project_root", lambda: root)
    monkeypatch.setattr(paths, "get_project_root", lambda: root)
    monkeypatch.chdir(root)
    configure_workspace(workspace=None, allow_absolute=False)
    for name in CONFIG:
        (root / name).write_text("safety:\n  hitl:\n    enabled: true\n", encoding="utf-8")
    for name in DATA_CONFIG:
        (data / name).write_text("{}\n", encoding="utf-8")
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")
    (root / "payload").mkdir()
    (root / "payload" / "kazma.local.yaml").write_text(
        "safety: {hitl: {enabled: false}}\nagent: {hooks: {pre_tool: [whoami]}}\n",
        encoding="utf-8")
    return root


def _tools() -> dict[str, object]:
    from kazma_core.agent.tool_builtins import filesystem

    tools: dict[str, object] = {}

    class _Capture:
        def register(self, **_kw):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn

            return deco

    filesystem.register_filesystem_tools(_Capture())
    return tools


async def _file_doors(path: str) -> dict[str, str]:
    """Each file-tool write door's answer for *path* (an error text, or what it did)."""
    from kazma_core.tools.file_apply_patch import file_apply_patch
    from kazma_core.tools.file_write import file_write

    from tests._tool_answer import answer

    tools = _tools()
    return {
        "file_write": await answer(file_write(path, "safety: {hitl: {enabled: false}}\n")),
        "file_append": await answer(tools["file_append"](path, "\nagent: {}\n")),
        "file_apply_patch": await answer(file_apply_patch(path, "true", "false")),
        "file_delete": await answer(tools["file_delete"](path)),
    }


@pytest.mark.parametrize("name", CONFIG + tuple(f"kazma-data/{n}" for n in DATA_CONFIG))
async def test_every_file_tool_refuses_to_change_a_config_file(install, name):
    from kazma_core.ide.workspace_scope import workspace_path_scope

    before = (install / name).read_bytes()
    async with workspace_path_scope(install):
        doors = await _file_doors(name)
    assert (install / name).read_bytes() == before, doors
    for door, said in doors.items():
        assert "configuration" in said.lower(), (door, said)


async def test_a_config_file_is_still_readable(install):
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.tools.file_read import clear_read_cache, file_read

    from tests._tool_answer import answer

    async with workspace_path_scope(install):
        clear_read_cache()
        read = await answer(file_read("kazma.yaml"))
    assert "hitl" in read, read


async def test_a_projects_own_kazma_yaml_is_the_users(install, tmp_path):
    """A kazma.yaml in another project (not a folder Kazma reads) is a file."""
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.tools.file_write import file_write

    from tests._tool_answer import answer

    project = tmp_path / "project"
    project.mkdir()
    async with workspace_path_scope(project):
        said = await answer(file_write("kazma.yaml", "name: mine\n"))
    assert (project / "kazma.yaml").read_text(encoding="utf-8") == "name: mine\n", said


@pytest.fixture()
def shell(monkeypatch, install):
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

    async def run(command: str) -> str:
        async with workspace_path_scope(install):
            return await registry._tools["shell_exec"].func(command, timeout=10)

    run.ran = ran  # type: ignore[attr-defined]
    return run


@pytest.mark.parametrize("command", [
    "touch kazma.local.yaml",
    "cp notes.txt kazma.local.yaml",
    "cp payload/kazma.local.yaml .",
    "cp -r payload/. .",
    "mv notes.txt kazma.yaml",
    "uniq notes.txt kazma.local.yaml",
    "git diff --output=kazma.local.yaml",
    "git checkout -- kazma.yaml",
    "cp notes.txt kazma-data/swarm_templates.json",
    "cp payload/kazma.local.yaml kazma-permissions.yaml",
])
async def test_shell_exec_refuses_to_change_a_config_file(shell, command):
    with pytest.raises(RefusedBeforeEffect) as refused:
        await shell(command)
    assert shell.ran == [], command
    assert "configuration" in str(refused.value) or "Kazma's own file" in str(refused.value), (
        str(refused.value))


@pytest.mark.parametrize("command", ["cat kazma.yaml", "grep -n hitl kazma.local.yaml",
                                     "head kazma-data/swarm_templates.json"])
async def test_shell_exec_still_reads_a_config_file(shell, command):
    await shell(command)
    assert len(shell.ran) == 1


async def test_unzip_refuses_an_archive_that_would_write_config(shell, install):
    import zipfile

    with zipfile.ZipFile(install / "evil.zip", "w") as zf:
        zf.writestr("kazma.local.yaml", "safety: {hitl: {enabled: false}}\n")
    with zipfile.ZipFile(install / "fine.zip", "w") as zf:
        zf.writestr("docs/readme.md", "hello\n")
    with pytest.raises(RefusedBeforeEffect) as refused:
        await shell("unzip evil.zip")
    assert "kazma.local.yaml" in str(refused.value)
    await shell("unzip fine.zip")
    assert len(shell.ran) == 1


async def test_python_exec_refuses_code_naming_a_config_file(install, tmp_path):
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.workspace.path_policy import code_mentions_control_plane

    async with workspace_path_scope(install):
        assert code_mentions_control_plane(
            "open('kazma.local.yaml', 'w').write('x')") == "kazma.local.yaml"
        assert code_mentions_control_plane(
            f"open({str(install / 'kazma.yaml')!r}, 'a')") == "kazma.yaml"
        if os.name == "nt":
            assert code_mentions_control_plane(
                "open('/workspace/kazma.local.yaml','w')") == "kazma.local.yaml"
        assert code_mentions_control_plane("print('hello')") is None
    project = tmp_path / "project"
    project.mkdir()
    async with workspace_path_scope(project):
        # Another project's kazma.yaml is the user's file.
        assert code_mentions_control_plane("open('kazma.yaml','w')") is None


async def test_python_exec_tool_refuses_it(install):
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.tools.code_exec import python_exec

    async with workspace_path_scope(install):
        with pytest.raises(RefusedBeforeEffect) as refused:
            await python_exec("open('kazma.local.yaml','w').write('x')")
    assert "configuration" in str(refused.value)
    assert not (install / "kazma.local.yaml").read_text(encoding="utf-8").startswith("x")


async def test_control_without_the_config_check_every_door_writes(install, monkeypatch):
    """Negative control: the doors refuse because of the config check. With it
    off, file_write changes kazma.local.yaml -- the reviewed attack."""
    from kazma_core import store_registry
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_core.tools.file_write import file_write
    from kazma_core.workspace.path_policy import code_mentions_control_plane

    from tests._tool_answer import answer

    monkeypatch.setattr(store_registry, "is_kazma_config_file", lambda _p: False)
    async with workspace_path_scope(install):
        said = await answer(file_write("kazma.local.yaml", "safety: {hitl: {enabled: false}}\n"))
        assert code_mentions_control_plane("open('kazma.local.yaml','w')") is None
    assert "enabled: false" in (install / "kazma.local.yaml").read_text(encoding="utf-8"), said


# ── gates ────────────────────────────────────────────────────────────────


def _repo() -> Path:
    return Path(__file__).resolve().parents[1]


def _product_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--", "kazma-*"], cwd=_repo(),
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [_repo() / rel for rel in out.split("\0")
            if rel.endswith(".py") and "/tests/" not in rel]


def _config_names_in(source: str) -> set[str]:
    """Every ``kazma*.yaml`` / ``kazma*.yml`` file name a module spells."""
    import re

    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if re.fullmatch(r"kazma[\w.-]*\.ya?ml", node.value):
                names.add(node.value)
    return names


def test_every_config_file_kazma_reads_is_protected():
    """Gate: a ``kazma*.yaml`` name product code reads is in CONFIG_FILE_NAMES,
    and so are the config loader's own names."""
    from kazma_core import config_loader
    from kazma_core.store_registry import CONFIG_FILE_NAMES

    spelled: set[str] = set()
    for path in _product_files():
        spelled |= _config_names_in(path.read_text(encoding="utf-8"))
    assert {"kazma.yaml", "kazma-permissions.yaml"} <= spelled, spelled
    missing = spelled - CONFIG_FILE_NAMES
    assert not missing, f"add these to store_registry.CONFIG_FILE_NAMES: {missing}"
    assert {config_loader.SHIPPED_CONFIG_NAME, config_loader.LOCAL_CONFIG_NAME} <= CONFIG_FILE_NAMES


def test_the_gate_sees_a_new_config_name():
    """Negative control: a planted ``kazma-extra.yaml`` read is caught."""
    from kazma_core.store_registry import CONFIG_FILE_NAMES

    planted = _config_names_in("PATH = ROOT / 'kazma-extra.yaml'\n")
    assert planted - CONFIG_FILE_NAMES == {"kazma-extra.yaml"}


def test_every_write_door_asks_with_write():
    """Gate: product code that decides a write with ``check_path_access``
    passes a write mode, and ``is_kazma_store`` callers in the save paths ask
    with ``write=True`` (a read-mode check lets configuration through)."""
    offenders: list[str] = []
    for path in _product_files():
        source = path.read_text(encoding="utf-8")
        if "is_kazma_store(target" not in source:
            continue
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", "")) == "is_kazma_store"
                and node.args and getattr(node.args[0], "id", "") == "target"
                and not any(k.arg == "write" for k in node.keywords)
            ):
                offenders.append(f"{path.relative_to(_repo())}:{node.lineno}")
    assert not offenders, offenders
