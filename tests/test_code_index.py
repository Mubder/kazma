"""Workspace codebase index (ripgrep + regex/tree-sitter symbols)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.code_index.indexer import ensure_index, notify_file_changed, status
from kazma_core.code_index.search import format_search, search_codebase
from kazma_core.code_index.symbols import extract_symbols
from kazma_core.code_index.walk import iter_source_files


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KAZMA_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("KAZMA_CODE_INDEX", raising=False)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type(
            "S",
            (),
            {"get_active_workspace": staticmethod(lambda: None)},
        )(),
    )
    from kazma_core.workspace.binding import configure_workspace

    configure_workspace(workspace=str(tmp_path))
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(
        "class Greeter:\n"
        "    def hello(self, name):\n"
        "        return f'hi {name}'\n"
        "\n"
        "def greet_all(items):\n"
        "    return [Greeter().hello(x) for x in items]\n",
        encoding="utf-8",
    )
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "hidden.py").write_text(
        "def should_not_index():\n    return 1\n",
        encoding="utf-8",
    )
    return tmp_path


def test_iter_skips_venv(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "skip.py").write_text("def nope():\n    pass\n", encoding="utf-8")
    files = list(iter_source_files(tmp_path))
    names = {p.name for p in files}
    assert "ok.py" in names
    assert "skip.py" not in names


def test_regex_python_symbols(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text("class Foo:\n    def bar(self):\n        return 1\n\ndef baz():\n    return 2\n", encoding="utf-8")
    names = {(s.name, s.kind) for s in extract_symbols(p)}
    assert ("Foo", "class") in names
    assert ("bar", "method") in names
    assert ("baz", "function") in names


_PY_SOURCE = (
    "class Foo:\n"
    "    def bar(self, x=1):\n"
    "        def helper():\n"
    "            return x\n"
    "        return helper()\n"
    "\n"
    "    @property\n"
    "    def size(self):\n"
    "        return 2\n"
    "\n"
    "def baz():\n"
    "    return 2\n"
)


def test_tree_sitter_names_a_class_s_function_a_method_as_the_regex_path_does() -> None:
    """With tree-sitter installed (the ``index`` extra) every Python method
    was a "function" and its signature "function bar": the index answered
    differently depending on which extractor the machine had (2026-11-13)."""
    pytest.importorskip("tree_sitter_python")
    from kazma_core.code_index.symbols import _regex_symbols, _tree_sitter_symbols

    ts = {(s.name, s.kind, s.signature) for s in _tree_sitter_symbols("python", _PY_SOURCE)}
    assert ("Foo", "class", "class Foo") in ts
    assert ("bar", "method", "def bar(self, x=1)") in ts
    assert ("size", "method", "def size(self)") in ts  # a decorated method
    assert ("baz", "function", "def baz()") in ts
    assert ("helper", "function", "def helper()") in ts  # a function inside a method
    regex = {(s.name, s.kind) for s in _regex_symbols("python", _PY_SOURCE)}
    for name in ("Foo", "bar", "size", "baz"):
        assert {k for n, k, _sig in ts if n == name} == {k for n, k in regex if n == name}, name


def test_negative_control_the_capture_name_alone_calls_a_method_a_function(monkeypatch) -> None:
    pytest.importorskip("tree_sitter_python")
    from kazma_core.code_index import symbols

    monkeypatch.setattr(symbols, "_ts_kind", lambda node, cap: "class" if "class" in cap else "function")
    kinds = {(s.name, s.kind) for s in symbols._tree_sitter_symbols("python", _PY_SOURCE)}
    assert ("bar", "function") in kinds and ("bar", "method") not in kinds


def test_tree_sitter_indexes_a_javascript_class_s_methods() -> None:
    pytest.importorskip("tree_sitter_javascript")
    from kazma_core.code_index.symbols import _tree_sitter_symbols

    source = "class Greeter {\n  hello(name) { return name; }\n}\nfunction main() {}\n"
    found = {(s.name, s.kind) for s in _tree_sitter_symbols("javascript", source)}
    assert {("Greeter", "class"), ("hello", "method"), ("main", "function")} <= found


def test_search_finds_symbol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _workspace(tmp_path, monkeypatch)
    st = ensure_index(root)
    assert st["files"] >= 1
    assert st["symbols"] >= 3
    result = search_codebase("Greeter", root=root, mode="symbol")
    names = [s["name"] for s in result["symbols"] if isinstance(s, dict)]
    assert "Greeter" in names
    text = format_search(result)
    assert "Greeter" in text
    assert "pkg/mod.py" in text.replace("\\", "/")


def test_search_text_hits_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _workspace(tmp_path, monkeypatch)
    ensure_index(root)
    result = search_codebase("hi {name}", root=root, mode="text")
    joined = "\n".join(str(x) for x in result["text"])
    assert "mod.py" in joined.replace("\\", "/")


def test_notify_picks_up_new_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _workspace(tmp_path, monkeypatch)
    ensure_index(root)
    (root / "pkg" / "new.py").write_text("def brand_new():\n    return 0\n", encoding="utf-8")
    notify_file_changed(root / "pkg" / "new.py")
    result = search_codebase("brand_new", root=root, mode="symbol")
    names = [s["name"] for s in result["symbols"] if isinstance(s, dict)]
    assert "brand_new" in names


def test_kill_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _workspace(tmp_path, monkeypatch)
    monkeypatch.setenv("KAZMA_CODE_INDEX", "0")
    st = ensure_index(root)
    assert st["files"] == 0
    result = search_codebase("Greeter", root=root)
    assert result["symbols"] == []
    info = status(root)
    assert info["enabled"] is False


@pytest.mark.asyncio
async def test_codebase_search_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _workspace(tmp_path, monkeypatch)
    from kazma_core.agent.tool_registry import LocalToolRegistry

    reg = LocalToolRegistry()
    out = await reg.execute("codebase_search", {"query": "greet_all", "mode": "symbol"})
    assert out.get("is_error") is False
    assert "greet_all" in (out.get("content") or "")
