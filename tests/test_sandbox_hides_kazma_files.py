"""python_exec's container shows Kazma's own files empty, and a copy of a
store is a store (2026-11 functional audit, siblings of FA-001).

Live 2026-11-02: the workspace is the install folder, and the Docker sandbox
mounted it whole at ``/workspace``. One approved ``python_exec`` read
``/workspace/.env`` (the vault key and the database password), and the data
folder and ``.kazma`` (restic's password, the broker token, the logs) were
listed beside it. The text check before the card resolved ``/workspace/.env``
against the server's drive, not the workspace, so it said nothing.

And ``memory_state.db.pre_relabel`` (a full copy of the memory database left
by a repair script) was printed by ``file_read`` at read tier: only names
ending ``.db`` were stores.

Each rule has its negative control: the container command without the masks
shows ``.env``; the check without the ``/workspace`` mapping misses it; the
old suffix rule misses the copy.
"""

from __future__ import annotations

import ast
import asyncio
import shutil
import sqlite3
import subprocess
from pathlib import Path, PurePosixPath

import pytest
from kazma_core.ide.workspace_scope import workspace_path_scope

SECRETS = (".env", ".env.local", "restic.pass")
USERS_OWN = (".env.example", "notes/settings.txt", "src/app.py")
KAZMA_FOLDERS = ("kazma-data", ".kazma")


@pytest.fixture()
def install(tmp_path, monkeypatch):
    """An install whose workspace is the install folder, as on live."""
    from kazma_core import paths

    root = tmp_path / "install"
    data = root / "kazma-data"
    (data / "workspace").mkdir(parents=True)
    (data / "backups" / "universal" / "1").mkdir(parents=True)
    (root / ".kazma").mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_USER_HOME", str(root / ".kazma"))
    monkeypatch.delenv("KAZMA_ENV_FILE", raising=False)
    monkeypatch.setattr(paths, "installed_project_root", lambda: root)
    monkeypatch.setattr(paths, "get_project_root", lambda: root)
    monkeypatch.chdir(root)
    for rel in SECRETS + USERS_OWN + (
        "kazma-data/backups/universal/1/.env",
        ".kazma/document_broker.token",
    ):
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("KAZMA_VAULT_KEY=demo-vault-key-value\n", encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# What the container hides
# --------------------------------------------------------------------------


def test_kazma_paths_inside_names_the_install_folders_and_secrets(install):
    from kazma_core.store_registry import kazma_paths_inside

    folders, files = kazma_paths_inside(install)
    assert {f.relative_to(install.resolve()).as_posix() for f in folders} == set(KAZMA_FOLDERS)
    names = {f.relative_to(install.resolve()).as_posix() for f in files}
    assert set(SECRETS) <= names, names
    assert not names & set(USERS_OWN), names
    # A file inside a hidden folder is covered by the folder, not listed.
    assert not any(n.startswith(("kazma-data/", ".kazma/")) for n in names), names


def test_the_default_sandbox_hides_nothing(install):
    """The coding sandbox inside the data folder holds no Kazma file."""
    from kazma_core.store_registry import kazma_paths_inside

    sandbox = install / "kazma-data" / "workspace"
    (sandbox / ".env").write_text("A=1\n", encoding="utf-8")
    assert kazma_paths_inside(sandbox) == ([], [])


def _hidden(cmd: list[str], host_root: Path, rel: str) -> bool:
    """Whether the container command covers *rel* (a path in the workspace)."""
    from kazma_core.workspace.path_policy import sandbox_workspace_target

    target = PurePosixPath(sandbox_workspace_target(str(host_root.resolve())))
    inside = target / rel
    for i, arg in enumerate(cmd[:-1]):
        if arg != "--mount":
            continue
        spec = cmd[i + 1]
        fields = dict(
            f.strip('"').split("=", 1) for f in spec.split(",") if "=" in f
        )
        dst = PurePosixPath(fields.get("dst", ""))
        if dst == target:
            continue  # the workspace itself
        if inside == dst or dst in inside.parents:
            return True
    return False


async def _container_command(monkeypatch, workspace: Path) -> list[str]:
    """Run the Docker jail with docker faked; return the command it built."""
    from kazma_core.tools import code_exec

    seen: list[list[str]] = []

    class _Proc:
        returncode = 0

        async def communicate(self, input=None):  # noqa: A002 - asyncio's name
            return b"ok\n", b""

    async def _exec(*cmd, **_kw):
        seen.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(code_exec, "_docker_cli", lambda: "docker")
    monkeypatch.setattr(code_exec._fw, "_get_workspace", lambda: workspace)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _exec)
    script = workspace.parent / "snippet.py"
    script.write_text("print(1)\n", encoding="utf-8")
    await code_exec._run_docker_jail(script, str(workspace.parent), 5)
    assert seen, "docker was not run"
    return seen[0]


async def test_the_container_shows_kazma_files_empty(install, monkeypatch):
    cmd = await _container_command(monkeypatch, install)
    for rel in SECRETS + (
        "kazma-data/memory_state.db",
        "kazma-data/backups/universal/1/.env",
        ".kazma/restic.pass",
        ".kazma/document_broker.token",
    ):
        assert _hidden(cmd, install, rel), f"{rel} visible in the container: {cmd}"
    for rel in USERS_OWN:
        assert not _hidden(cmd, install, rel), f"{rel} hidden: {cmd}"
    # The workspace itself is still mounted, read-only.
    assert any("type=bind" in a and a.endswith(",ro") and "/dev/null" not in a for a in cmd)
    assert "demo-vault-key-value" not in " ".join(cmd)


async def test_the_old_container_command_shows_the_env(install, monkeypatch):
    """Negative control: without the masks (the live build) ``.env``, the
    data folder and ``.kazma`` are all inside the container."""
    from kazma_core import store_registry

    monkeypatch.setattr(store_registry, "kazma_paths_inside", lambda _root: ([], []))
    cmd = await _container_command(monkeypatch, install)
    assert not _hidden(cmd, install, ".env")
    assert not _hidden(cmd, install, "kazma-data/memory_state.db")


async def test_a_workspace_whose_files_cannot_be_hidden_is_not_mounted(install, monkeypatch):
    """Fail closed: when the list cannot be worked out, the snippet runs
    without the workspace rather than with every secret in it."""
    from kazma_core import store_registry

    def _broken(_root):
        raise OSError("cannot list")

    monkeypatch.setattr(store_registry, "kazma_paths_inside", _broken)
    cmd = await _container_command(monkeypatch, install)
    assert "--mount" not in cmd, cmd


def test_a_path_with_a_comma_is_quoted_for_docker():
    from kazma_core.tools.code_exec import _mount_spec

    assert _mount_spec("type=bind", "src=/a,b", "dst=/w", "ro") == 'type=bind,"src=/a,b",dst=/w,ro'
    assert _mount_spec("src=/a\"b") == '"src=/a""b"'


def _docker_with_image() -> tuple[str, str] | None:
    from kazma_core.tools.code_exec import DEFAULT_DOCKER_IMAGE, _docker_cli

    docker = _docker_cli()
    if not docker:
        return None
    try:
        probe = subprocess.run(  # noqa: S603 - fixed argv, the docker CLI
            [docker, "image", "inspect", DEFAULT_DOCKER_IMAGE],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (docker, DEFAULT_DOCKER_IMAGE) if probe.returncode == 0 else None


async def test_a_real_container_reads_the_env_as_empty(install, monkeypatch):
    """The real Docker run: ``.env`` reads empty, a file in the data folder
    is not there, the user's own file reads whole. Skipped without docker and
    the sandbox image (never pulled by a test)."""
    if _docker_with_image() is None:
        pytest.skip("docker or the sandbox image is not available")
    from kazma_core.tools import code_exec
    from kazma_core.workspace.path_policy import sandbox_workspace_target

    monkeypatch.setattr(code_exec._fw, "_get_workspace", lambda: install)
    target = sandbox_workspace_target(str(install.resolve()))
    snippet = (
        f"print('env', len(open({target + '/.env'!r}).read()))\n"
        "try:\n"
        f"    open({target + '/kazma-data/backups/universal/1/.env'!r})\n"
        "    print('data seen')\n"
        "except FileNotFoundError:\n"
        "    print('data 0')\n"
        f"print('own', len(open({target + '/notes/settings.txt'!r}).read()))\n"
    )
    out_dir = install.parent / "run"
    out_dir.mkdir()
    script = out_dir / "snippet.py"
    script.write_text(code_exec._build_sandbox_script(snippet), encoding="utf-8")
    out = await code_exec._run_docker_jail(script, str(out_dir), 60)
    assert "env 0" in out, out
    assert "data 0" in out, out
    assert "own 37" in out, out

    # Negative control: the same run without the masks (the live build).
    from kazma_core import store_registry

    monkeypatch.setattr(store_registry, "kazma_paths_inside", lambda _root: ([], []))
    out = await code_exec._run_docker_jail(script, str(out_dir), 60)
    assert "env 37" in out and "data seen" in out, out


# --------------------------------------------------------------------------
# The check before the card reads /workspace as the workspace
# --------------------------------------------------------------------------


@pytest.fixture()
def fixed_mount(monkeypatch):
    from kazma_core.workspace import path_policy

    monkeypatch.setattr(path_policy, "_sandbox_mount_is_fixed", lambda: True)


async def test_workspace_paths_in_code_are_refused_before_the_card(install, fixed_mount):
    from kazma_core.safety.commitment.authorize import _exec_names_kazma_store
    from kazma_core.workspace.path_policy import code_mentions_control_plane

    async with workspace_path_scope(install):
        for path in ("/workspace/.env", "/workspace/kazma-data/memory_state.db", "/workspace/restic.pass"):
            code = f"print(open({path!r}).read())"
            assert code_mentions_control_plane(code), path
            assert _exec_names_kazma_store({"code": code}, ""), path
        assert not code_mentions_control_plane("print(open('/workspace/notes/settings.txt').read())")


async def test_without_the_mapping_the_check_misses_workspace_paths(install, fixed_mount, monkeypatch):
    """Negative control: the live check resolved ``/workspace/.env`` against
    the server's own drive and said nothing."""
    from kazma_core.workspace import path_policy

    monkeypatch.setattr(path_policy, "_sandbox_token_on_host", lambda _t, _r: None)
    async with workspace_path_scope(install):
        assert not path_policy.code_mentions_control_plane("print(open('/workspace/.env').read())")


# --------------------------------------------------------------------------
# A copy of a store is a store
# --------------------------------------------------------------------------


def _sqlite_file(path: Path) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE t (x)")
        con.commit()
    finally:
        con.close()


COPIES = (
    "memory_state.db.pre_relabel",
    "memory_state.db.pre_entity_key",
    "research_sessions.db.bak-2026-08-14-flood",
    "cron.sqlite3~",
)
NOT_STORES = (
    "kazma_settings_contaminated_2026-08-14.json",
    "swarm_registry.json",
    "install_id",
    "workspace/x.db",
    "workspace/copy.db.bak",
)


def test_a_copy_of_a_store_is_a_store(install):
    from kazma_core.store_registry import is_kazma_store
    from kazma_core.workspace.path_policy import check_path_access

    data = install / "kazma-data"
    for name in COPIES:
        _sqlite_file(data / name)
        assert is_kazma_store(data / name), name
        assert not check_path_access(data / name, "read").allowed, name
    # A SQLite file under any name is one, by its header.
    _sqlite_file(data / "snapshot-old")
    assert is_kazma_store(data / "snapshot-old")
    for name in NOT_STORES:
        (data / name).parent.mkdir(parents=True, exist_ok=True)
        if name.endswith((".db", ".bak")):
            _sqlite_file(data / name)  # the user's sandbox databases stay theirs
        else:
            (data / name).write_text("{}", encoding="utf-8")
        assert not is_kazma_store(data / name), name


def test_the_old_suffix_rule_missed_the_copies():
    """Negative control: the live rule (a name ending in a database suffix)
    let file_read print memory_state.db.pre_relabel."""
    from kazma_core.store_registry import _DB_SUFFIXES

    for name in COPIES:
        assert not name.endswith(_DB_SUFFIXES), name


# --------------------------------------------------------------------------
# Class gate: one place builds a container mount of a workspace
# --------------------------------------------------------------------------

#: (file, function) allowed to spell a container bind mount, and why.
#: ``_mount_args`` (the move from Docker Desktop, AGENTS.md section 63) makes
#: a container again on Kazma's engine with the bind mounts it already had on
#: Docker Desktop: the same folders, as the engine names them, never a new
#: one Kazma chose.
MOUNT_BUILDERS = {("kazma-core/kazma_core/tools/code_exec.py", "_workspace_mounts"),
                  ("kazma-core/kazma_core/engine_move.py", "_mount_args")}
_MOUNT_MARKERS = ("type=bind", "--volume")


def _product_files(repo: Path) -> list[Path]:
    out = subprocess.run(  # noqa: S603 - fixed argv
        ["git", "ls-files", "*.py"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.split()
    return [
        repo / p
        for p in out
        if p.startswith(("kazma-core/kazma_core/", "kazma-skills/kazma_skills/", "kazma-ui/kazma_ui/",
                         "kazma-gateway/kazma_gateway/", "kazma-cli/kazma_cli/", "kazma-tui/kazma_tui/"))
    ]


def _docstrings(source: str) -> set[int]:
    tree = ast.parse(source)
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            doc = node.body[0] if node.body else None
            if isinstance(doc, ast.Expr) and isinstance(doc.value, ast.Constant):
                ids.add(id(doc.value))
    return ids


def test_only_one_function_builds_a_container_mount():
    repo = Path(__file__).resolve().parents[1]
    if shutil.which("git") is None:
        pytest.skip("git not available")
    found = set()
    for path in _product_files(repo):
        source = path.read_text(encoding="utf-8", errors="replace")
        if not any(m in source for m in _MOUNT_MARKERS):
            continue
        tree = ast.parse(source)
        docs = _docstrings(source)
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                if (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and id(child) not in docs
                    and any(m in child.value for m in _MOUNT_MARKERS)
                ):
                    found.add((path.relative_to(repo).as_posix(), _enclosing(tree, child)))
    assert found <= MOUNT_BUILDERS, (
        "a container mount is built outside _workspace_mounts, which hides "
        f"Kazma's files in it: {sorted(found - MOUNT_BUILDERS)}"
    )
    assert found == MOUNT_BUILDERS, f"the declared builder no longer builds a mount: {found}"


def _enclosing(tree: ast.AST, target: ast.AST) -> str:
    best = "<module>"
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(node):
                if sub is target:
                    best = node.name  # innermost wins: walk order is outer first
    return best


def test_the_gate_sees_a_mount_built_elsewhere():
    """Negative control: a planted function spelling a bind mount is found."""
    planted = (
        "def run_tool(ws):\n"
        "    return ['docker', 'run', '--mount', f'type=bind,src={ws},dst=/w,ro']\n"
    )
    tree = ast.parse(planted)
    hits = {
        _enclosing(tree, c)
        for n in ast.walk(tree)
        for c in ast.iter_child_nodes(n)
        if isinstance(c, ast.Constant) and isinstance(c.value, str) and "type=bind" in c.value
    }
    assert hits == {"run_tool"}
