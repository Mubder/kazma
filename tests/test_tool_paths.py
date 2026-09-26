"""A relative path given to an agent tool means the active workspace.

Every file tool resolved relative paths against the server PROCESS's working
directory, while ``shell_exec`` runs in the active workspace and the prompt
names the workspace as the root. On the live install the two coincide (the
workspace is the install folder), so nothing looked wrong; after a Switch
Repo, ``file_read("README.md")`` would read the Kazma install's README, or
be refused as outside the workspace, while ``shell_exec("cat README.md")``
read the repo's. ``kazma_core.workspace.binding.resolve_tool_path`` is the
one answer now.

Each behavioural test below runs from a server CWD that holds a DIFFERENT
file under the same name, so a tool that still resolves against the CWD
reads, writes or lists the wrong thing and fails -- that is the negative
control built into every case. The static gate at the bottom finds every
tool that builds a path from its own parameter, with its own control.

Also here: file_search / file_list walking and matching (prune below the
root, braces, ``**`` inside a segment, binary files, links out of the root).
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

import pytest

from kazma_core.agent.tool_builtins import filesystem
from kazma_core.ide.workspace_scope import workspace_path_scope
from kazma_core.workspace.binding import resolve_active_root, resolve_tool_path

REPO = Path(__file__).resolve().parents[1]


class _Capture:
    """Stands in for the registry: collects the tool functions as registered."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register(self, **_kw: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.fixture(scope="module")
def tools() -> dict[str, Any]:
    cap = _Capture()
    filesystem.register_filesystem_tools(cap)
    return cap.tools


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """An empty folder: the suite's conftest keeps its stores in tmp_path itself."""
    r = tmp_path / "w"
    r.mkdir()
    return r


@pytest.fixture
def places(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """``(workspace, server_cwd)``, each holding its own ``notes.txt``; CWD is the server's."""
    ws = tmp_path / "repo"
    cwd = tmp_path / "server-cwd"
    ws.mkdir()
    cwd.mkdir()
    (ws / "notes.txt").write_text("from the workspace\n", encoding="utf-8")
    (cwd / "notes.txt").write_text("from the server cwd\n", encoding="utf-8")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("KAZMA_FILE_CHECKPOINTS_DB", str(tmp_path / "checkpoints.db"))
    from kazma_core.ide import file_checkpoints

    file_checkpoints.reset_file_checkpoint_store()
    yield ws, cwd
    file_checkpoints.reset_file_checkpoint_store()


# ── the helper ───────────────────────────────────────────────────────────


async def test_the_helper_resolves_against_the_workspace(places):
    ws, cwd = places
    async with workspace_path_scope(ws):
        assert resolve_active_root() == ws.resolve()
        assert resolve_tool_path("notes.txt") == (ws / "notes.txt").resolve()
        assert resolve_tool_path(".") == ws.resolve()
        assert resolve_tool_path(str(cwd / "notes.txt")) == (cwd / "notes.txt").resolve()
        assert resolve_tool_path("~") == Path.home().resolve()


async def test_the_access_policy_reads_relative_paths_the_same_way(places):
    ws, _cwd = places
    from kazma_core.workspace.path_policy import check_path_access

    async with workspace_path_scope(ws):
        res = check_path_access("notes.txt", "read")
    assert res.allowed, res.reason
    assert Path(res.resolved) == (ws / "notes.txt").resolve()


# ── every file tool ──────────────────────────────────────────────────────


async def test_file_read(places):
    ws, _cwd = places
    from kazma_core.tools.file_read import clear_read_cache, file_read

    clear_read_cache()
    async with workspace_path_scope(ws):
        out = await file_read("notes.txt")
    assert "from the workspace" in out, out
    assert "server cwd" not in out


async def test_file_write_and_append(places, tools):
    ws, cwd = places
    from kazma_core.tools.file_write import file_write

    async with workspace_path_scope(ws):
        out = await file_write("made.txt", "hello")
        assert not out.startswith(("Error", "Safety")), out
        out = await tools["file_append"]("made.txt", " world")
        assert not out.startswith(("Error", "Safety")), out
    assert (ws / "made.txt").read_text(encoding="utf-8") == "hello world"
    assert not (cwd / "made.txt").exists()


async def test_file_apply_patch(places):
    ws, cwd = places
    from kazma_core.tools.file_apply_patch import file_apply_patch

    async with workspace_path_scope(ws):
        out = await file_apply_patch("notes.txt", old_string="from the", new_string="FROM THE")
    assert out.startswith("Patched"), out
    assert (ws / "notes.txt").read_text(encoding="utf-8").startswith("FROM THE workspace")
    assert (cwd / "notes.txt").read_text(encoding="utf-8").startswith("from the server")


async def test_a_failed_patch_set_restores_the_workspace_file(places):
    """The checkpoint and the patches must name the same files.

    The checkpoint resolved its relative paths against the CWD while the
    patches (now) resolve against the workspace: the rollback would restore
    a file nobody touched and leave the half-applied one behind.
    """
    ws, cwd = places
    from kazma_core.tools.file_apply_patch import file_apply_patch_set

    (ws / "b.txt").write_text("bee\n", encoding="utf-8")
    async with workspace_path_scope(ws):
        out = await file_apply_patch_set(
            [
                {"path": "notes.txt", "old_string": "from the", "new_string": "CHANGED"},
                {"path": "b.txt", "old_string": "no such text", "new_string": "x"},
            ],
            verify=False,
        )
    assert "restored checkpoint" in out, out
    assert (ws / "notes.txt").read_text(encoding="utf-8") == "from the workspace\n"
    assert (cwd / "notes.txt").read_text(encoding="utf-8") == "from the server cwd\n"


async def test_file_list(places, tools):
    ws, _cwd = places
    (ws / "only-in-ws.md").write_text("x", encoding="utf-8")
    async with workspace_path_scope(ws):
        out = await tools["file_list"]()
    assert "only-in-ws.md" in out.splitlines(), out


async def test_file_search(places, tools):
    ws, _cwd = places
    async with workspace_path_scope(ws):
        out = await tools["file_search"]("from the", glob="*.txt")
    assert "from the workspace" in out, out
    assert "server cwd" not in out


async def test_file_delete(places, tools):
    ws, cwd = places
    (ws / "gone.txt").write_text("x", encoding="utf-8")
    (cwd / "gone.txt").write_text("x", encoding="utf-8")
    async with workspace_path_scope(ws):
        out = await tools["file_delete"]("gone.txt")
    assert out.startswith("Deleted"), out
    assert not (ws / "gone.txt").exists()
    assert (cwd / "gone.txt").exists()


@pytest.mark.parametrize("target", [".", "..", "./"])
async def test_file_delete_refuses_the_workspace_root(places, tools, target):
    """With "." meaning the workspace, ``file_delete(".")`` would remove it."""
    ws, _cwd = places
    async with workspace_path_scope(ws):
        out = await tools["file_delete"](target)
    assert out.startswith(("Error: refusing", "Safety")), out
    assert (ws / "notes.txt").exists()


async def test_send_file(places, tools, monkeypatch):
    ws, _cwd = places
    import importlib

    # kazma_core.tools re-exports a FUNCTION named send_message.
    send_message = importlib.import_module("kazma_core.tools.send_message")

    monkeypatch.setattr(send_message, "get_current_delivery_target", lambda: "telegram:1")
    sent: dict[str, Any] = {}

    async def fake_send(**kw):
        sent.update(kw)
        return "ok"

    monkeypatch.setattr(send_message, "send_file_message", fake_send)
    async with workspace_path_scope(ws):
        out = await tools["send_file"]("notes.txt")
    assert out.startswith("File sent"), out
    assert Path(sent["file_path"]) == (ws / "notes.txt").resolve()


async def test_the_skill_tools(places):
    ws, _cwd = places
    from kazma_skills.native.document_platform.tools import _resolve_workspace_file
    from kazma_skills.native.document_processor.tools import _resolve_input

    async with workspace_path_scope(ws):
        p, err = _resolve_input("notes.txt")
        assert err is None and p == (ws / "notes.txt").resolve(), err
        p, err = _resolve_workspace_file("notes.txt")
        assert err is None and p == (ws / "notes.txt").resolve(), err


# ── file_search / file_list walking and matching ─────────────────────────


def _tree(root: Path, files: dict[str, bytes | str]) -> None:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body, encoding="utf-8")


async def test_search_never_enters_skipped_folders(root, tools, monkeypatch):
    """19 s on the live install: rglob walked .venv and filtered afterwards."""
    _tree(root, {
        "src/app.py": "needle = 1\n",
        ".venv/lib/site.py": "needle = 2\n",
        "node_modules/pkg/index.js": "needle\n",
        ".git/config": "needle\n",
    })
    visited: list[str] = []
    real_walk = os.walk

    def spy(top, *a, **k):
        for dirpath, dirnames, filenames in real_walk(top, *a, **k):
            visited.append(dirpath)
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(os, "walk", spy)
    async with workspace_path_scope(root):
        out = await tools["file_search"]("needle", glob="*")
    assert out.splitlines() == [f"{root / 'src' / 'app.py'}:1: needle = 1"], out
    assert not [v for v in visited if re.search(r"[\\/](\.venv|node_modules|\.git)([\\/]|$)", v)]

    # Negative control: with the skip set emptied the same walk does enter
    # them, so the assertion above can see a regression.
    visited.clear()
    monkeypatch.setattr(filesystem, "_WALK_SKIP_DIRS", frozenset())
    list(filesystem._walk(root))
    assert any(".venv" in v for v in visited)


@pytest.mark.parametrize("root_name", ["kazma-data", "build"])
async def test_a_root_named_like_a_skipped_folder_is_searched(tmp_path, tools, root_name):
    """The old test looked at the ABSOLUTE path: searching inside kazma-data,
    or any repo under a folder named build, always found nothing."""
    root = tmp_path / root_name / "repo"
    _tree(root, {"a.txt": "needle\n"})
    async with workspace_path_scope(root):
        out = await tools["file_search"]("needle", glob="*.txt")
    assert out.endswith("a.txt:1: needle"), out


@pytest.mark.parametrize(
    ("glob", "expected"),
    [
        ("kazma-**/*.py", {"kazma-core/m.py", "kazma-ui/u.py"}),  # live 2026-09-24: ValueError
        ("*.{js,ts}", {"web/a.js", "web/b.ts"}),  # pathlib has no braces: matched nothing
        ("web/*.js", {"web/a.js"}),
        ("**/m.py", {"kazma-core/m.py"}),
        ("*.py", {"kazma-core/m.py", "kazma-ui/u.py", "top.py"}),
    ],
)
async def test_search_globs(root, tools, glob, expected):
    _tree(root, {
        "kazma-core/m.py": "needle\n",
        "kazma-ui/u.py": "needle\n",
        "top.py": "needle\n",
        "web/a.js": "needle\n",
        "web/b.ts": "needle\n",
        "web/c.css": "needle\n",
    })
    async with workspace_path_scope(root):
        out = await tools["file_search"]("needle", glob=glob)
    found = {
        Path(line.rsplit(":", 2)[0]).relative_to(root).as_posix()
        for line in out.splitlines()
    }
    assert found == expected, out


async def test_search_skips_binary_files_and_links_out_of_the_root(tmp_path, tools):
    root = tmp_path / "repo"
    outside = tmp_path / "secret.txt"
    outside.write_text("needle outside\n", encoding="utf-8")
    _tree(root, {"data.db": b"SQLite\0needle", "ok.txt": "needle inside\n"})
    try:
        (root / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pass  # no symlink privilege on this Windows box; the binary half still runs
    async with workspace_path_scope(root):
        out = await tools["file_search"]("needle", glob="*")
    assert out.splitlines() == [f"{root / 'ok.txt'}:1: needle inside"], out


async def test_search_answers_in_words_not_exceptions(root, tools):
    _tree(root, {"a.py": "x\n"})
    async with workspace_path_scope(root):
        bad_regex = await tools["file_search"]("foo(", glob="*.py")
        no_files = await tools["file_search"]("x", glob="*.rs")
        miss = await tools["file_search"]("zzz", glob="*.py")
        absolute = await tools["file_search"]("x", glob=str(root / "*.py"))
    assert bad_regex.startswith("Error: 'foo(' is not a valid regular expression"), bad_regex
    assert "matches glob '*.rs'" in no_files, no_files
    assert miss.startswith("No matches for 'zzz'") and "1 files searched" in miss, miss
    assert absolute.startswith("Error: glob must be relative"), absolute


async def test_search_refuses_before_saying_what_exists(tmp_path, tools):
    """Out of scope answers the same whether or not the path exists."""
    from kazma_core.workspace.binding import configure_workspace

    # The suite's conftest allows absolute paths; this test needs the real rule.
    configure_workspace(workspace=None, allow_absolute=False)
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "exists-outside").mkdir()
    async with workspace_path_scope(ws):
        a = await tools["file_search"]("x", path=str(tmp_path / "exists-outside"))
        b = await tools["file_search"]("x", path=str(tmp_path / "missing-outside"))
    assert a.startswith("Safety") and b.startswith("Safety"), (a, b)


async def test_list_patterns(root, tools):
    _tree(root, {
        "src/a.py": "",
        "src/deep/b.py": "",
        "build/out.py": "",
        ".venv/lib/c.py": "",
        "readme.md": "",
    })
    async with workspace_path_scope(root):
        top = (await tools["file_list"]()).splitlines()
        nested = (await tools["file_list"](pattern="**/*.py")).splitlines()
        one_level = (await tools["file_list"](pattern="src/*.py")).splitlines()
        odd = await tools["file_list"](pattern="sr**")
    # The top level shows every folder, skipped ones included -- they exist.
    assert top == [".venv", "build", "readme.md", "src"], top
    # A recursive pattern reports paths, and does not enter .venv or build.
    assert nested == ["src/a.py", "src/deep/b.py"], nested
    assert one_level == ["src/a.py"], one_level
    assert odd.splitlines() == ["src"], odd


async def test_list_says_when_it_stopped(root, tools):
    for i in range(filesystem._LIST_MAX_ENTRIES + 5):
        (root / f"f{i:04d}.txt").write_text("", encoding="utf-8")
    async with workspace_path_scope(root):
        out = (await tools["file_list"]()).splitlines()
    assert len(out) == filesystem._LIST_MAX_ENTRIES + 1
    assert out[-1] == "... (5 more; narrow the pattern)"


# ── the gate: every tool that builds a path from its parameter ───────────

_TOOL_DIRS = (
    "kazma-core/kazma_core/tools",
    "kazma-core/kazma_core/agent/tool_builtins",
    "kazma-skills/kazma_skills/native",
)
_PATHY = re.compile(
    r"^(path|paths|file|files|filename|filepath|dir|dirname|directory|folder|root|"
    r"src|dst|source|dest|destination|target|cwd|workdir|workspace)$"
    r"|_(path|paths|file|dir|directory|folder|root)$"
)
_OPENERS = {
    "Path", "PurePath", "pathlib.Path", "open", "io.open", "os.path.abspath",
    "os.path.realpath", "os.path.exists", "os.path.isfile", "os.path.isdir",
    "os.listdir", "os.scandir", "os.walk",
}
# Functions that take a path the MODEL never wrote, each with the reason.
_NOT_TOOL_INPUT = {
    ("kazma-core/kazma_core/tools/research_eval.py", "score_report_file"):
        "research pipeline: a report path it wrote itself",
    ("kazma-core/kazma_core/tools/research_evidence.py", "_load"):
        "research pipeline: source files it downloaded",
    ("kazma-core/kazma_core/tools/research_evidence.py", "write_claims_json"):
        "research pipeline: its own output file",
    ("kazma-core/kazma_core/agent/tool_builtins/filesystem.py", "_walk"):
        "receives a root the tool already resolved with resolve_tool_path",
    ("kazma-skills/kazma_skills/native/email_manager/backends/sandbox.py", "__init__"):
        "the sandbox mailbox database under the data dir",
    ("kazma-core/kazma_core/tools/text_newlines.py", "existing_newline"):
        "receives a path the calling tool already resolved",
}


def _raw_path_uses(source: str) -> list[tuple[str, int, str]]:
    """``(function, line, parameter)`` for each path parameter used raw."""
    hits: list[tuple[str, int, str]] = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {a.arg for a in fn.args.args + fn.args.kwonlyargs if _PATHY.search(a.arg)}
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in params
                and ast.unparse(node.func) in _OPENERS
            ):
                hits.append((fn.name, node.lineno, node.args[0].id))
    return hits


def test_no_tool_resolves_its_path_against_the_process_cwd():
    offenders = []
    for d in _TOOL_DIRS:
        for f in sorted((REPO / d).rglob("*.py")):
            rel = f.relative_to(REPO).as_posix()
            for fn, line, param in _raw_path_uses(f.read_text(encoding="utf-8")):
                if (rel, fn) not in _NOT_TOOL_INPUT:
                    offenders.append(f"{rel}:{line} {fn}({param}=...)")
    assert not offenders, (
        "A tool builds a path from its parameter without resolve_tool_path, so "
        "a relative path means the server's working directory instead of the "
        "workspace:\n  " + "\n  ".join(offenders)
    )


def test_the_exemptions_still_exist():
    """An exemption whose function is gone would hide a new one of that name."""
    for (rel, fn), _reason in _NOT_TOOL_INPUT.items():
        names = {
            n.name
            for n in ast.walk(ast.parse((REPO / rel).read_text(encoding="utf-8")))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert fn in names, f"{rel}: {fn} is exempted but no longer defined"


def test_the_gate_sees_a_raw_path():
    """Negative control: the gate flags the shape every tool had."""
    old = "async def file_read(path: str):\n    p = Path(path).expanduser().resolve()\n"
    new = "async def file_read(path: str):\n    p = resolve_tool_path(path)\n"
    assert _raw_path_uses(old) == [("file_read", 2, "path")]
    assert _raw_path_uses(new) == []
    assert _raw_path_uses("def f(image_path):\n    open(image_path)\n") == [("f", 2, "image_path")]


# ── the CLI's explicit workspace ─────────────────────────────────────────


async def test_the_cli_workspace_outranks_the_web_uis_active_row(tmp_path, monkeypatch):
    """``kazma ask`` in a project folder works on that folder.

    The CLI pinned its workspace with ``configure_workspace`` -- the process
    pin, which the ladder ranks BELOW the web UI's active workspace row -- so
    with a row active, its file and shell tools worked in the web's
    workspace. It now pins the path scope, the top rung.
    """
    import contextvars

    from kazma_core.cli import ask

    project = tmp_path / "project"
    web = tmp_path / "web-workspace"
    project.mkdir()
    web.mkdir()

    class _Store:
        @staticmethod
        def get_active_workspace():
            return {"root_path": str(web)}

    monkeypatch.setattr("kazma_core.stores.get_workspace_store", lambda: _Store())
    # The real .env loader runs, over a ladder with nothing on it: no test
    # reads a real .env (AGENTS §38).
    monkeypatch.setenv("KAZMA_USER_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("KAZMA_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)

    def boot_then_resolve() -> Path:
        ask._boot_env(workspace=str(project))
        return resolve_active_root()

    assert contextvars.copy_context().run(boot_then_resolve) == project.resolve()

    # Negative control: the process pin alone loses to the active row.
    from kazma_core.workspace.binding import configure_workspace

    def pin_only() -> Path:
        configure_workspace(workspace=str(project))
        return resolve_active_root()

    assert contextvars.copy_context().run(pin_only) == web.resolve()
