"""Every IDE door refuses Kazma's own stores and secret files, inside the
workspace too (review of the 2026-11-26 code-scanning triage, finding H1;
AGENTS.md sections 10 and 37).

``IdeService.resolve`` asked the path policy only about a path outside the
workspace. A path inside it was returned as it was, and the LSP request
(``POST /api/ide/lsp``) read it whole for code completion, hover, symbols
and diagnostics. On an install whose workspace is the install folder, that
included ``.env``. The IDE MCP server's ``read_file`` and ``search_code``
and the TUI's file preview read their files without asking either. Code
scanning had reported the LSP read (alert #53); it was dismissed on the
reasoning that ``resolve`` contained it.

The census: every file read in the IDE's modules (``kazma_core/ide/``, and
each module that drives ``get_ide_service()``) is declared in ``IDE_READS``
with a probe. The probe puts Kazma's secret files in the workspace and
checks the door never hands their words back. The control blinds the one
predicate (``store_registry.is_kazma_store``): then every probe finds the
secret, so each one reaches the check it names. A planted read the
declaration does not know is the census's own control.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]

#: What each secret file holds: one identifier, so code completion would
#: offer it whole.
SECRET = "demo_vault_key_7Qx9"

#: Kazma's own secret files, inside the workspace (the install folder):
#: the install's ``.env``, a backup's copy of it under the data folder, and
#: the file ``KAZMA_ENV_FILE`` names (any name, here a Python file, so the
#: IDE's Run would read it).
KAZMA_FILES = (".env", "kazma-data/backups/1/.env", "conf/kazma_env.py")

#: Every file read in the IDE's modules, and the check that keeps Kazma's
#: files out of it. Keys are ``<file>::<qualified function>``.
IDE_READS: dict[str, str] = {
    "kazma-core/kazma_core/ide/service.py::IdeService._read_file_sync":
        "IdeService.resolve, then check_path_access (rule 0 is is_kazma_store)",
    "kazma-core/kazma_core/ide/service.py::IdeService.run_file":
        "IdeService.resolve (is_kazma_store inside the workspace too)",
    "kazma-core/kazma_core/ide/lsp.py::_buffer":
        "the path handle_lsp resolved through IdeService.resolve",
    "kazma-core/kazma_core/ide/file_checkpoints.py::FileCheckpointStore.create":
        "check_path_access(read): a refused path is not captured",
    "kazma-gateway/kazma_gateway/mcp_server.py::_tool_read_file":
        "mcp_server._resolve (is_kazma_store)",
    "kazma-gateway/kazma_gateway/mcp_server.py::_tool_search_code":
        "is_kazma_store on each file before it is read",
    "kazma-tui/kazma_tui/files.py::FilesPanel._read_preview_text":
        "is_kazma_store before the preview opens the file",
    "kazma-core/kazma_core/ide/service.py::IdeService._git_read._run._ask":
        "git_policy's card-less forms (kazma_operand_refusal and validate_read_paths: every "
        "operand inside the workspace and asked is_kazma_store); a content read as "
        "safety.git_kazma_files plans it: the predicate's name and place rules as exclude "
        "pathspecs, every section kazma_paths_among accepts dropped; anything else the card",
    "kazma-core/kazma_core/ide/file_checkpoints.py::FileCheckpointStore.review":
        "check_path_access(read) on each held path again, before it is read",
    "kazma-core/kazma_core/ide/file_checkpoints.py::FileCheckpointStore.restore_hunk":
        "_held_file: check_path_access(write) on the held path before it is read",
    "kazma-core/kazma_core/ide/lsp.py::_local_symbols":
        "extract_symbols is handed the buffer handle_lsp read from a resolved path",
    "kazma-core/kazma_core/ide/lsp.py::_index_symbols":
        "the code index (indexer._kazmas_own) asks is_kazma_store before it reads a file",
}

#: Processes the IDE's modules start whose command is Kazma's own literal:
#: no caller names a path or an argument, so there is nothing to probe.
FIXED_PROCESSES: dict[str, str] = {
    "kazma-core/kazma_core/ide/env_context.py::_git":
        "git config --get remote.origin.url and git branch --show-current, literals",
    "kazma-gateway/kazma_gateway/agent_handler/commands.py::_try_ide_command":
        "/ide clone: git clone --depth 1 of https://github.com/<slug>.git into a new folder; "
        "reads no file of the workspace",
}

#: Public IdeService methods that are not a door of their own, and why.
NOT_A_DOOR: dict[str, str] = {
    "diff": "the path only labels the diff; both texts come from the caller",
    "resolve": "the resolver every door asks; each probe below goes through it",
    "refresh_root": "re-reads the workspace root; takes no path",
    "search": "the file_search tool through the registry, which refuses Kazma's files itself "
              "(tests/test_kazma_secret_files_refused.py, its searches and search_walk doors)",
    "codebase_search": "the codebase_search tool; the code index drops Kazma's files "
                       "(code_index.ripgrep._without_kazma_stores)",
    "run": "shell_exec through LocalToolRegistry.execute: an approval card, and the shell's own "
           "refusal of Kazma's files (tests/test_shell_exec_kazma_files.py)",
    "send_to_swarm": "a task for the swarm; each tool its workers call asks the predicate itself",
    "restore_file_checkpoint": "FileCheckpointStore.restore writes only a held path "
                               "check_path_access allows for writing (Kazma's files never)",
}


# ── The census ──────────────────────────────────────────────────────────


def _product_sources() -> dict[str, str]:
    """Every git-listed Python file of the packages, tests excluded."""
    listed = subprocess.run(
        ["git", "ls-files", "--", "kazma-*"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    out: dict[str, str] = {}
    for rel in listed:
        if not rel.endswith(".py") or "/tests/" in rel or "_tests/" in rel:
            continue
        out[rel] = (REPO / rel).read_text(encoding="utf-8")
    return out


def _drives_the_ide(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "get_ide_service":
                return True
    return False


def _write_only(mode: ast.expr | None) -> bool:
    """A literal mode that only writes (``"w"``, ``"ab"``, ``"x"``)."""
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return False
    return "r" not in mode.value and "+" not in mode.value


def _mode_of(call: ast.Call, position: int) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value
    return call.args[position] if len(call.args) > position else None


_PROCESS_STARTS = frozenset({
    ("subprocess", "run"), ("subprocess", "Popen"), ("subprocess", "check_output"),
    ("subprocess", "check_call"), ("subprocess", "call"), ("subprocess", "getoutput"),
    ("subprocess", "getstatusoutput"), ("asyncio", "create_subprocess_exec"),
    ("asyncio", "create_subprocess_shell"),
})


def _dotted(rel: str) -> str:
    """``kazma-core/kazma_core/tools/x.py`` -> ``kazma_core.tools.x``."""
    parts = rel.removesuffix(".py").split("/")[1:]
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _reads_directly(func: ast.AST) -> bool:
    """Whether *func*'s own body reads a file (the census's primitives) or
    starts a process, whose output is read too (``git_read.run_git``: until
    2026-11-27 a process started through a helper was not counted)."""
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and node.attr in ("read_text", "read_bytes"):
            return True
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and (node.value.id, node.attr) in _PROCESS_STARTS):
            return True
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "open" and not _write_only(_mode_of(node, 1)):
                return True
            if isinstance(fn, ast.Attribute) and fn.attr == "open" and not _write_only(_mode_of(node, 0)):
                return True
    return False


def read_helpers(sources: dict[str, str]) -> set[tuple[str, str]]:
    """``(module, function)`` for each module-level function of the packages
    whose own body reads a file (``text_newlines.read_exact`` and its kin):
    a call of one is a read where it is called."""
    found: set[tuple[str, str]] = set()
    for rel, text in sources.items():
        for node in ast.parse(text).body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _reads_directly(node):
                found.add((_dotted(rel), node.name))
    return found


def ide_read_sites(sources: dict[str, str], helpers: set[tuple[str, str]] | None = None) -> set[str]:
    """``<file>::<qualname>`` of every file read in the IDE's modules.

    The IDE's modules are ``kazma_core/ide/`` and every module that calls
    ``get_ide_service()``. A read is ``.read_text`` or ``.read_bytes``
    (called or handed to a thread), ``.open(...)`` and ``open(...)`` unless
    their literal mode only writes, a call of a function imported from
    another module of the packages that reads a file itself
    (:func:`read_helpers`), and a process the module starts
    (``subprocess.run`` and its kin, ``asyncio.create_subprocess_*``): what
    the process prints is read too.
    """
    helpers = read_helpers(sources) if helpers is None else helpers
    found: set[str] = set()
    for rel, text in sources.items():
        tree = ast.parse(text)
        if "/kazma_core/ide/" not in rel and not _drives_the_ide(tree):
            continue
        imported = {
            alias.asname or alias.name
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names if (node.module, alias.name) in helpers
        }
        stack: list[str] = []
        writes: set[int] = set()

        class _Visitor(ast.NodeVisitor):
            def _scoped(self, node: ast.AST, name: str) -> None:
                stack.append(name)
                self.generic_visit(node)
                stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._scoped(node, node.name)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._scoped(node, node.name)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self._scoped(node, node.name)

            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                if isinstance(func, ast.Name) and func.id == "open":
                    if not _write_only(_mode_of(node, 1)):
                        found.add(f"{rel}::{'.'.join(stack)}")
                elif isinstance(func, ast.Name) and func.id in imported:
                    found.add(f"{rel}::{'.'.join(stack)}")
                elif isinstance(func, ast.Attribute) and func.attr == "open":
                    if _write_only(_mode_of(node, 0)):
                        writes.add(id(func))
                self.generic_visit(node)

            def visit_Attribute(self, node: ast.Attribute) -> None:
                # Called or handed on (``asyncio.to_thread(subprocess.run,
                # ...)``, the form the off-loop rule asks for): a read either way.
                starts_a_process = (isinstance(node.value, ast.Name)
                                    and (node.value.id, node.attr) in _PROCESS_STARTS)
                if node.attr in ("read_text", "read_bytes") or starts_a_process or (
                    node.attr == "open" and id(node) not in writes
                ):
                    found.add(f"{rel}::{'.'.join(stack)}")
                self.generic_visit(node)

        _Visitor().visit(tree)
    return found


def test_every_ide_read_is_declared_with_its_check() -> None:
    found = ide_read_sites(_product_sources())
    declared = set(IDE_READS) | set(FIXED_PROCESSES)
    unknown = sorted(found - declared)
    stale = sorted(declared - found)
    assert not unknown, (
        "A file read in the IDE's modules is not declared in IDE_READS. "
        "Make it ask store_registry.is_kazma_store (or read a path "
        "IdeService.resolve returned), then declare it with a probe: "
        f"{unknown}"
    )
    assert not stale, f"IDE_READS names reads that are gone: {stale}"


def test_control_the_census_finds_an_undeclared_read() -> None:
    planted = {
        "kazma-core/kazma_core/ide/planted.py": (
            "from kazma_core.tools.planted_reader import slurp as take, label, start\n"
            "class Peek:\n"
            "    def look(self, p):\n"
            "        return p.read_text()\n"
            "    def keep(self, p):\n"
            "        with open(p, 'w') as fh:\n"
            "            fh.write('x')\n"
            "        with p.open('ab') as fh:\n"
            "            fh.write(b'x')\n"
            "    def through_a_helper(self, p):\n"
            "        return take(p)\n"
            "    def names_it(self, p):\n"
            "        return label(p)\n"
            "    def through_a_process_helper(self, p):\n"
            "        return start(['git', 'show', p])\n"
        ),
        "kazma-core/kazma_core/tools/planted_reader.py": (
            "def slurp(p):\n"
            "    return p.read_bytes()\n"
            "def label(p):\n"
            "    return p.name\n"
            "def start(argv):\n"
            "    return subprocess.Popen(argv).communicate()\n"
        ),
        "kazma-ui/kazma_ui/planted_front.py": (
            "import asyncio\n"
            "async def show(p):\n"
            "    get_ide_service()\n"
            "    return await asyncio.to_thread(p.read_bytes)\n"
            "def peek_with_git(p):\n"
            "    return subprocess.run(['git', 'show', p], capture_output=True).stdout\n"
            "async def peek_off_the_loop(p):\n"
            "    return await asyncio.to_thread(subprocess.run, ['git', 'show', p])\n"
        ),
        "kazma-ui/kazma_ui/not_the_ide.py": "def other(p):\n    return open(p).read()\n",
    }
    assert ide_read_sites(planted) == {
        "kazma-core/kazma_core/ide/planted.py::Peek.look",
        "kazma-core/kazma_core/ide/planted.py::Peek.through_a_helper",
        "kazma-core/kazma_core/ide/planted.py::Peek.through_a_process_helper",
        "kazma-ui/kazma_ui/planted_front.py::show",
        "kazma-ui/kazma_ui/planted_front.py::peek_with_git",
        "kazma-ui/kazma_ui/planted_front.py::peek_off_the_loop",
    }
    assert ("kazma_core.tools.planted_reader", "slurp") in read_helpers(planted)
    assert ("kazma_core.tools.planted_reader", "label") not in read_helpers(planted)
    assert ("kazma_core.tools.planted_reader", "start") in read_helpers(planted)


def test_every_declared_read_has_a_probe() -> None:
    assert set(PROBES) == set(IDE_READS)


# ── An install whose workspace is the install folder, as on live ────────


@pytest.fixture()
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from kazma_core import paths
    from kazma_core.ide.service import reset_ide_service
    from kazma_core.workspace.binding import configure_workspace

    root = tmp_path / "install"
    data = root / "kazma-data"
    (data / "workspace").mkdir(parents=True)
    (root / ".kazma").mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    monkeypatch.setenv("KAZMA_USER_HOME", str(root / ".kazma"))
    monkeypatch.setenv("KAZMA_WORKSPACE", str(root))
    monkeypatch.setenv("KAZMA_ENV_FILE", str(root / "conf" / "kazma_env.py"))
    monkeypatch.setenv("KAZMA_CODE_INDEX", "0")
    monkeypatch.delenv("KAZMA_IDE_LSP", raising=False)
    monkeypatch.setattr(paths, "installed_project_root", lambda: root)
    monkeypatch.setattr(paths, "get_project_root", lambda: root)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    configure_workspace(workspace=str(root))
    for rel in KAZMA_FILES[:2]:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"KAZMA_VAULT_KEY={SECRET}\n", encoding="utf-8")
    (root / "conf").mkdir()
    # A definition named like the secret too: the code index keeps names.
    (root / "conf" / "kazma_env.py").write_text(
        f'VAULT_KEY = "{SECRET}"\n\n\ndef {SECRET}():\n    return VAULT_KEY\n', encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def greet_all(items):\n    return items\n",
                                         encoding="utf-8")
    (root / ".env.example").write_text("KAZMA_VAULT_KEY=put_yours_here\n", encoding="utf-8")
    reset_ide_service()
    yield root
    reset_ide_service()


@pytest.fixture()
def tool_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """Every tool the IDE service would run, recorded; none runs."""
    from kazma_core.ide.service import IdeService

    calls: list[tuple[str, dict[str, Any]]] = []

    async def _record(self: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        calls.append((tool_name, dict(arguments)))
        return {"ok": False, "tool": tool_name, "output": "", "error": "not run in a test",
                "effect_uncertain": False}

    monkeypatch.setattr(IdeService, "_call_tool", _record)
    return calls


def _blind(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control: the one predicate never recognises Kazma's files."""
    from kazma_core import store_registry

    monkeypatch.setattr(store_registry, "is_kazma_store", lambda *_a, **_k: False)


def _blind_with_its_exclusions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The git door's control: the predicate blind, and the exclusions the
    door builds from the predicate's name and place rules
    (``store_registry.kazma_exclude_pathspecs`` / ``kazma_excluded_among``)."""
    from kazma_core import store_registry

    _blind(monkeypatch)
    monkeypatch.setattr(store_registry, "kazma_exclude_pathspecs", lambda _top: [])
    monkeypatch.setattr(store_registry, "kazma_excluded_among", lambda _top, _paths: set())


# ── The probes: what each door hands back for Kazma's files ─────────────


def lsp_methods() -> list[str]:
    """Every method ``handle_lsp`` answers from a file, read from its source."""
    tree = ast.parse((REPO / "kazma-core/kazma_core/ide/lsp.py").read_text(encoding="utf-8"))
    handler = next(n for n in tree.body
                   if isinstance(n, ast.FunctionDef) and n.name == "handle_lsp")
    methods: list[str] = []
    for node in ast.walk(handler):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
                and test.left.id == "method" and isinstance(test.ops[0], ast.In)
                and isinstance(test.comparators[0], ast.Tuple)):
            continue
        answers_status = any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == "_status"
            for stmt in node.body for c in ast.walk(stmt)
        )
        if answers_status:
            continue
        methods += [e.value for e in test.comparators[0].elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str) and e.value]
    return methods


def _probe_read_file(root: Path, calls: list) -> str:
    from kazma_core.ide.service import get_ide_service

    return json.dumps([get_ide_service()._read_file_sync(rel) for rel in KAZMA_FILES])


def _probe_run_file(root: Path, calls: list) -> str:
    from kazma_core.ide.service import get_ide_service

    svc = get_ide_service()
    out = [asyncio.run(svc.run_file(rel)) for rel in KAZMA_FILES]
    return json.dumps([out, calls])


def _probe_lsp(root: Path, calls: list) -> str:
    from kazma_core.ide.lsp import handle_lsp

    out = []
    for method in lsp_methods():
        for rel in KAZMA_FILES + tuple(str(root / rel) for rel in KAZMA_FILES):
            out.append(handle_lsp(method, path=rel, prefix="demo"))
    return json.dumps(out)


def _probe_checkpoint(root: Path, calls: list) -> str:
    from kazma_core.ide.file_checkpoints import FileCheckpointStore

    store = FileCheckpointStore(root.parent / "checkpoints.db")
    held = store.get(store.create([*KAZMA_FILES, "src/app.py"], root=root))
    return json.dumps(held)


def _probe_mcp_read(root: Path, calls: list) -> str:
    from kazma_gateway.mcp_server import _tool_read_file

    out = []
    for rel in KAZMA_FILES:
        try:
            out.append(_tool_read_file(root, {"path": rel}))
        except PermissionError as exc:
            out.append(str(exc))
    return json.dumps(out)


def _probe_mcp_search(root: Path, calls: list) -> str:
    from kazma_gateway.mcp_server import _tool_search_code

    return _tool_search_code(root, {"pattern": "demo_vault"})


def _probe_tui_preview(root: Path, calls: list) -> str:
    pytest.importorskip("textual")
    from kazma_tui.files import FilesPanel

    out = []
    for rel in KAZMA_FILES:
        try:
            out.append(FilesPanel._read_preview_text(root / rel, 10_000)[0])
        except PermissionError as exc:
            out.append(str(exc))
    return json.dumps(out)


def _git(root: Path, *args: str) -> str:
    """Run git in *root* for the test's own setup (not through a door)."""
    import shutil

    git = shutil.which("git")
    assert git, "the git tests need git"
    return subprocess.run([git, "-c", "core.fsmonitor=false", "-c", "core.autocrlf=false", *args],
                          cwd=root, capture_output=True, text=True, check=True).stdout


def _commit(root: Path, *paths: str) -> None:
    """Make the workspace a repository whose first commit holds *paths*
    (every file when none is named)."""
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Kazma Test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "add", "-f", "--", *(paths or (".",)))
    _git(root, "commit", "-q", "-m", "first")


def _commit_everything(root: Path) -> None:
    """A repository whose commit holds every file, Kazma's own included (a
    project that committed them, or a copy of the install folder's tree);
    then the work tree's secret changes, as a fresh key would."""
    _commit(root)
    (root / ".env").write_text(f"KAZMA_VAULT_KEY={SECRET}_fresh\n", encoding="utf-8")


#: Content reads that never name a Kazma file: a commit, the history, a
#: folder, a pattern, an exclusion, the work tree.
_UNNAMED_READS = (
    "show", "show HEAD", "log -p", "log --patch", "log -p --all", "log -p -- .",
    "log -p -- kazma-data", "log -p -- conf", "log -p -- [.]env", "log -p -- *.env", "log -p -- .en?",
    "log -p -- :!src", "log -p -- :^src", "diff", "diff HEAD", "diff . src", "diff -- [.]env",
    "stash show -p", "show --stat HEAD",
)


def _probe_git(root: Path, calls: list) -> str:
    """Read-only git in a repository that holds Kazma's files: by name, by
    forms that never name them, by the object id ``ls-files -s`` gives, and
    with the old file-to-file and folder-to-folder comparison."""
    from kazma_core.ide.service import get_ide_service

    _commit_everything(root)
    blobs = [line.split()[1] for line in _git(root, "ls-files", "-s").splitlines()]
    svc = get_ide_service()
    commands = [f"show HEAD:{rel}" for rel in KAZMA_FILES]
    commands += [f"log -p -- {rel}" for rel in KAZMA_FILES]
    commands += [f"blame {rel}" for rel in KAZMA_FILES]
    commands += [*_UNNAMED_READS, *(f"show {blob}" for blob in blobs)]
    commands += ["diff --no-index kazma-data src", "diff --no-index .env src/app.py"]
    return json.dumps([asyncio.run(svc.git(c)) for c in commands])


def _probe_lsp_index(root: Path, calls: list) -> str:
    """Completion and hover in an ordinary file, with the code index on: the
    index's names come from every source file of the workspace it read."""
    from kazma_core.ide.lsp import handle_lsp

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("KAZMA_CODE_INDEX", "1")
        out = [
            handle_lsp("textDocument/completion", path="src/app.py", prefix="demo"),
            handle_lsp("hover", path="src/app.py", content=f"{SECRET}()\n", line=0, character=3),
            handle_lsp("definition", path="src/app.py", content=f"{SECRET}()\n", line=0, character=3),
        ]
    return json.dumps(out)


def _held_while_missing(root: Path) -> tuple[Any, str, list[Path]]:
    """A checkpoint that holds paths which were nobody's when it was taken
    (missing) and are Kazma's files now, as a folder on their way linked
    into the data folder afterwards makes them."""
    from kazma_core import store_registry
    from kazma_core.ide.file_checkpoints import FileCheckpointStore

    later = [root / "kazma-data" / "backups" / "2" / ".env"]
    store = FileCheckpointStore(root.parent / "checkpoints.db")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(store_registry, "is_kazma_store", lambda *_a, **_k: False)
        checkpoint = store.create([str(p) for p in later], root=root)
    for path in later:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"KAZMA_VAULT_KEY={SECRET}\n", encoding="utf-8")
    return store, checkpoint, later


def _probe_checkpoint_review(root: Path, calls: list) -> str:
    store, checkpoint, _later = _held_while_missing(root)
    return json.dumps(store.review(checkpoint))


def _probe_restore_hunk(root: Path, calls: list) -> str:
    """Undoing a change reads the held file first. The evidence is what the
    door did with it: a refusal, or the file read and rewritten."""
    store, checkpoint, later = _held_while_missing(root)
    out: list[str] = []
    for path in later:
        try:
            out.append(store.restore_hunk(checkpoint, str(path), 0))
        except ValueError as exc:
            out.append(str(exc))
        if SECRET not in path.read_text(encoding="utf-8"):
            out.append(f"read and rewrote the file that held {SECRET}")
    return json.dumps(out)


PROBES: dict[str, Callable[[Path, list], str]] = {
    "kazma-core/kazma_core/ide/service.py::IdeService._read_file_sync": _probe_read_file,
    "kazma-core/kazma_core/ide/lsp.py::_local_symbols": _probe_lsp,
    "kazma-core/kazma_core/ide/lsp.py::_index_symbols": _probe_lsp_index,
    "kazma-core/kazma_core/ide/file_checkpoints.py::FileCheckpointStore.review": _probe_checkpoint_review,
    "kazma-core/kazma_core/ide/file_checkpoints.py::FileCheckpointStore.restore_hunk": _probe_restore_hunk,
    "kazma-core/kazma_core/ide/service.py::IdeService.run_file": _probe_run_file,
    "kazma-core/kazma_core/ide/lsp.py::_buffer": _probe_lsp,
    "kazma-core/kazma_core/ide/file_checkpoints.py::FileCheckpointStore.create": _probe_checkpoint,
    "kazma-gateway/kazma_gateway/mcp_server.py::_tool_read_file": _probe_mcp_read,
    "kazma-gateway/kazma_gateway/mcp_server.py::_tool_search_code": _probe_mcp_search,
    "kazma-tui/kazma_tui/files.py::FilesPanel._read_preview_text": _probe_tui_preview,
    "kazma-core/kazma_core/ide/service.py::IdeService._git_read._run._ask": _probe_git,
}


#: A read whose check is the predicate and rules built from it: its
#: control blinds them all (the git door's exclusions, 2026-11-27).
BLINDS: dict[str, Callable[[pytest.MonkeyPatch], None]] = {
    "kazma-core/kazma_core/ide/service.py::IdeService._git_read._run._ask": _blind_with_its_exclusions,
}


@pytest.mark.parametrize("read", sorted(PROBES))
def test_no_ide_read_hands_back_a_kazma_file(read: str, install: Path, tool_calls: list) -> None:
    seen = PROBES[read](install, tool_calls)
    assert SECRET not in seen, f"{read} handed back a Kazma secret file's words"


@pytest.mark.parametrize("read", sorted(PROBES))
def test_control_with_the_predicate_blind_each_probe_finds_the_secret(
    read: str, install: Path, tool_calls: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    BLINDS.get(read, _blind)(monkeypatch)
    seen = PROBES[read](install, tool_calls)
    assert SECRET in seen, f"{read}'s probe does not reach the predicate it names"


# ── H1 itself, and the IDE service's other doors ────────────────────────


def test_code_completion_offers_no_word_of_a_secret_file(install: Path) -> None:
    from kazma_core.ide.lsp import handle_lsp

    for path in (".env", str(install / ".env")):
        answer = handle_lsp("textDocument/completion", path=path, prefix="demo")
        assert answer["ok"] is False
        assert SECRET not in json.dumps(answer)


def test_every_lsp_method_refuses_a_kazma_file(install: Path) -> None:
    from kazma_core.ide.lsp import handle_lsp

    methods = lsp_methods()
    assert {"textDocument/completion", "hover", "diagnostics", "documentSymbol"} <= set(methods)
    for method in methods:
        for rel in KAZMA_FILES:
            answer = handle_lsp(method, path=rel, prefix="demo")
            assert answer["ok"] is False, (method, rel)


_PATH_CALLS: dict[str, Callable[[Any, str], Any]] = {
    "read_file": lambda svc, rel: svc.read_file(rel),
    "write_file": lambda svc, rel: svc.write_file(rel, "x"),
    "apply_patch": lambda svc, rel: svc.apply_patch(rel, old_string="a", new_string="b"),
    "apply_patch_set": lambda svc, rel: svc.apply_patch_set(
        [{"path": rel, "old_string": "a", "new_string": "b"}]),
    "delete_file": lambda svc, rel: svc.delete_file(rel),
    "list_path": lambda svc, rel: svc.list_path(rel),
    "run_file": lambda svc, rel: svc.run_file(rel),
    "git": lambda svc, rel: svc.git(f"show HEAD:{rel}"),
}


def ide_public_methods() -> set[str]:
    """Every public method of IdeService, read from the class."""
    from kazma_core.ide.service import IdeService

    return {name for name, _fn in inspect.getmembers(IdeService, inspect.isfunction)
            if not name.startswith("_")}


def test_every_public_method_of_the_ide_service_is_probed_or_declared() -> None:
    assert ide_public_methods() == set(_PATH_CALLS) | set(NOT_A_DOOR)


@pytest.mark.parametrize("method", sorted(_PATH_CALLS))
def test_the_ide_refuses_a_kazma_file_before_any_tool_runs(
    method: str, install: Path, tool_calls: list,
) -> None:
    from kazma_core.ide.service import get_ide_service

    for rel in KAZMA_FILES:
        answer = asyncio.run(_PATH_CALLS[method](get_ide_service(), rel))
        assert answer["ok"] is False, (method, rel)
        assert SECRET not in json.dumps(answer)
    assert tool_calls == [], f"{method} ran a tool on a Kazma file: {tool_calls}"


def test_control_blind_the_save_reaches_its_tool(
    install: Path, tool_calls: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_core.ide.service import get_ide_service

    _blind(monkeypatch)
    asyncio.run(get_ide_service().write_file(".env", "x"))
    assert [name for name, _ in tool_calls] == ["file_write"]


def test_the_users_own_files_still_open(install: Path, tool_calls: list) -> None:
    """Only Kazma's files are refused: a project file, and a template the
    user keeps beside them, open in every door."""
    from kazma_core.ide.lsp import handle_lsp
    from kazma_core.ide.service import get_ide_service
    from kazma_gateway.mcp_server import _tool_read_file

    svc = get_ide_service()
    assert svc._read_file_sync("src/app.py")["ok"] is True
    assert "put_yours_here" in svc._read_file_sync(".env.example")["content"]
    labels = {i["label"] for i in handle_lsp("completion", path="src/app.py", prefix="greet")["items"]}
    assert "greet_all" in labels
    assert "greet_all" in _tool_read_file(install, {"path": "src/app.py"})
    assert asyncio.run(svc.list_path("src"))["ok"] is True


# ── Kazma's configuration: the IDE opens it and never changes it ────────

#: The IdeService methods that change a file: they resolve with ``write``.
_SAVES = ("write_file", "apply_patch", "apply_patch_set", "delete_file")


def test_the_ide_saves_no_kazma_configuration_and_still_opens_it(
    install: Path, tool_calls: list,
) -> None:
    """Configuration is readable by every door and writable by none
    (``is_kazma_store(path, write=True)``): a save, a patch or a delete of
    ``kazma.yaml`` is refused before its tool (and its approval card) runs."""
    from kazma_core.ide.lsp import handle_lsp
    from kazma_core.ide.service import get_ide_service
    from kazma_gateway.mcp_server import _tool_write_file

    (install / "kazma.yaml").write_text("agent:\n  name: kazma\n", encoding="utf-8")
    svc = get_ide_service()
    for method in _SAVES:
        answer = asyncio.run(_PATH_CALLS[method](svc, "kazma.yaml"))
        assert answer["ok"] is False, method
    with pytest.raises(PermissionError):
        _tool_write_file(install, {"path": "kazma.yaml", "content": "x"})
    assert tool_calls == []
    assert svc._read_file_sync("kazma.yaml")["ok"] is True
    assert handle_lsp("documentSymbol", path="kazma.yaml")["ok"] is True
    # The control: asked as a read, the resolver lets configuration through;
    # only the save's write mode refuses it.
    assert svc.resolve("kazma.yaml") == (install / "kazma.yaml").resolve()
    with pytest.raises(ValueError):
        svc.resolve("kazma.yaml", write=True)


# Read-only git's own classes (what git prints, both doors, the folders git
# turns down) are tests/test_git_reads_print_no_kazma_file.py's.


# ── The checkpoint review page reads a held path only when it may ───────


def test_the_review_route_leaves_out_a_held_path_that_is_kazmas_now(
    install: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GET /api/ide/checkpoints/{id}/review`` read every held path again;
    one that names a Kazma file now went back to the page in ``after``."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_ui.ide_api import create_ide_router

    store, checkpoint, later = _held_while_missing(install)
    monkeypatch.setattr("kazma_core.ide.file_checkpoints.get_file_checkpoint_store", lambda: store)
    app = FastAPI()
    app.include_router(create_ide_router())
    answer = TestClient(app).get(f"/api/ide/checkpoints/{checkpoint}/review").json()
    assert answer["ok"] is True and answer["files"] == [], answer
    assert answer["not_shown"] == len(later), "the page is told some held files are not shown"
    assert SECRET not in json.dumps(answer)


# ── The code index keeps nothing of a Kazma file it held before ─────────


def test_the_code_index_drops_what_it_held_of_a_kazma_file(
    install: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An index built before the rule (or before the file was Kazma's) read
    the file; the next ``ensure_index`` drops it, the file untouched."""
    from kazma_core.code_index.indexer import ensure_index
    from kazma_core.ide.lsp import handle_lsp

    monkeypatch.setenv("KAZMA_CODE_INDEX", "1")
    with monkeypatch.context() as patch:
        _blind(patch)
        ensure_index(install)
        before = handle_lsp("textDocument/completion", path="src/app.py", prefix="demo")
    assert SECRET in json.dumps(before), "the control: the old index serves the file's names"
    after = handle_lsp("textDocument/completion", path="src/app.py", prefix="demo")
    assert SECRET not in json.dumps(after), after


# ── A save outside the workspace asks in write mode ─────────────────────


def test_a_save_outside_the_workspace_asks_the_policy_in_write_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``resolve`` answered a path outside the workspace from
    ``check_path_access`` in read mode, a save's included: where a grant
    reaches Kazma's configuration (here the escape hatch), it resolved for
    writing. The write tool refused it later, after its card."""
    from kazma_core import paths
    from kazma_core.ide.service import get_ide_service, reset_ide_service
    from kazma_core.workspace.binding import configure_workspace

    install, project = tmp_path / "install", tmp_path / "project"
    (install / "kazma-data").mkdir(parents=True)
    project.mkdir()
    (install / "kazma.yaml").write_text("agent:\n  name: kazma\n", encoding="utf-8")
    monkeypatch.setenv("KAZMA_DATA_DIR", str(install / "kazma-data"))
    monkeypatch.setenv("KAZMA_USER_HOME", str(install / ".kazma"))
    monkeypatch.setenv("KAZMA_WORKSPACE", str(project))
    monkeypatch.setattr(paths, "installed_project_root", lambda: install)
    monkeypatch.setattr(paths, "get_project_root", lambda: install)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    configure_workspace(workspace=str(project), allow_absolute=True)
    reset_ide_service()
    try:
        svc = get_ide_service()
        config = str(install / "kazma.yaml")
        # The control: read mode lets configuration through (every door reads it).
        assert svc.resolve(config) == (install / "kazma.yaml").resolve()
        with pytest.raises(ValueError):
            svc.resolve(config, write=True)
    finally:
        reset_ide_service()
