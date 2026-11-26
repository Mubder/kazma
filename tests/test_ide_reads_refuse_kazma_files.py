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
}

#: IdeService methods that take a path and are not a door of their own.
NOT_A_DOOR: dict[str, str] = {
    "diff": "the path only labels the diff; both texts come from the caller",
    "resolve": "the resolver every door asks; each probe below goes through it",
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


def ide_read_sites(sources: dict[str, str]) -> set[str]:
    """``<file>::<qualname>`` of every file read in the IDE's modules.

    The IDE's modules are ``kazma_core/ide/`` and every module that calls
    ``get_ide_service()``. A read is ``.read_text`` or ``.read_bytes``
    (called or handed to a thread), ``.open(...)`` and ``open(...)`` unless
    their literal mode only writes.
    """
    found: set[str] = set()
    for rel, text in sources.items():
        tree = ast.parse(text)
        if "/kazma_core/ide/" not in rel and not _drives_the_ide(tree):
            continue
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
                elif isinstance(func, ast.Attribute) and func.attr == "open":
                    if _write_only(_mode_of(node, 0)):
                        writes.add(id(func))
                self.generic_visit(node)

            def visit_Attribute(self, node: ast.Attribute) -> None:
                if node.attr in ("read_text", "read_bytes") or (
                    node.attr == "open" and id(node) not in writes
                ):
                    found.add(f"{rel}::{'.'.join(stack)}")
                self.generic_visit(node)

        _Visitor().visit(tree)
    return found


def test_every_ide_read_is_declared_with_its_check() -> None:
    found = ide_read_sites(_product_sources())
    unknown = sorted(found - set(IDE_READS))
    stale = sorted(set(IDE_READS) - found)
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
            "class Peek:\n"
            "    def look(self, p):\n"
            "        return p.read_text()\n"
            "    def keep(self, p):\n"
            "        with open(p, 'w') as fh:\n"
            "            fh.write('x')\n"
            "        with p.open('ab') as fh:\n"
            "            fh.write(b'x')\n"
        ),
        "kazma-ui/kazma_ui/planted_front.py": (
            "import asyncio\n"
            "async def show(p):\n"
            "    get_ide_service()\n"
            "    return await asyncio.to_thread(p.read_bytes)\n"
        ),
        "kazma-ui/kazma_ui/not_the_ide.py": "def other(p):\n    return open(p).read()\n",
    }
    assert ide_read_sites(planted) == {
        "kazma-core/kazma_core/ide/planted.py::Peek.look",
        "kazma-ui/kazma_ui/planted_front.py::show",
    }


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
    (root / "conf" / "kazma_env.py").write_text(f'VAULT_KEY = "{SECRET}"\n', encoding="utf-8")
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


PROBES: dict[str, Callable[[Path, list], str]] = {
    "kazma-core/kazma_core/ide/service.py::IdeService._read_file_sync": _probe_read_file,
    "kazma-core/kazma_core/ide/service.py::IdeService.run_file": _probe_run_file,
    "kazma-core/kazma_core/ide/lsp.py::_buffer": _probe_lsp,
    "kazma-core/kazma_core/ide/file_checkpoints.py::FileCheckpointStore.create": _probe_checkpoint,
    "kazma-gateway/kazma_gateway/mcp_server.py::_tool_read_file": _probe_mcp_read,
    "kazma-gateway/kazma_gateway/mcp_server.py::_tool_search_code": _probe_mcp_search,
    "kazma-tui/kazma_tui/files.py::FilesPanel._read_preview_text": _probe_tui_preview,
}


@pytest.mark.parametrize("read", sorted(PROBES))
def test_no_ide_read_hands_back_a_kazma_file(read: str, install: Path, tool_calls: list) -> None:
    seen = PROBES[read](install, tool_calls)
    assert SECRET not in seen, f"{read} handed back a Kazma secret file's words"


@pytest.mark.parametrize("read", sorted(PROBES))
def test_control_with_the_predicate_blind_each_probe_finds_the_secret(
    read: str, install: Path, tool_calls: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _blind(monkeypatch)
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
}


def ide_path_methods() -> set[str]:
    """The IdeService methods that take a path, read from the class."""
    from kazma_core.ide.service import IdeService

    return {
        name for name, fn in inspect.getmembers(IdeService, inspect.isfunction)
        if not name.startswith("_")
        and {"rel_path", "patches"} & set(inspect.signature(fn).parameters)
    }


def test_every_path_method_of_the_ide_service_is_probed() -> None:
    assert ide_path_methods() == set(_PATH_CALLS) | set(NOT_A_DOOR)


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
