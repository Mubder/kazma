"""Workspace-scoped IDE LSP (hover / complete / definition / diagnostics)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.ide.lsp import handle_lsp, lsp_enabled, word_at


def _workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KAZMA_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("KAZMA_IDE_LSP", raising=False)
    monkeypatch.delenv("KAZMA_CODE_INDEX", raising=False)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    from kazma_core.workspace.binding import configure_workspace

    configure_workspace(workspace=str(tmp_path))
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(
        'class Greeter:\n'
        '    """Say hello."""\n'
        "    def hello(self, name):\n"
        '        """Greet one person."""\n'
        "        return name\n"
        "\n"
        "def greet_all(items):\n"
        "    return [Greeter().hello(x) for x in items]\n",
        encoding="utf-8",
    )
    return tmp_path


class TestWordAt:
    def test_ident(self) -> None:
        src = "def greet_all(items):\n    return 1\n"
        word, start, end = word_at(src, 0, 6)
        assert word == "greet_all"
        assert src.splitlines()[0][start:end] == "greet_all"


class TestLsp:
    def test_status(self) -> None:
        st = handle_lsp("status")
        assert st["ok"] is True
        assert st["enabled"] is True
        assert "python" in st["languages"]

    def test_kill_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_IDE_LSP", "0")
        assert lsp_enabled() is False
        out = handle_lsp("complete", path="x.py", content="def foo():\n    pass\n")
        assert out["ok"] is False

    def test_complete_and_hover(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _workspace(tmp_path, monkeypatch)
        src = (root / "pkg" / "mod.py").read_text(encoding="utf-8")
        comp = handle_lsp(
            "complete",
            path="pkg/mod.py",
            content=src,
            prefix="greet",
            line=6,
            character=4,
        )
        labels = {i["label"] for i in comp["items"]}
        assert "greet_all" in labels
        assert "Greeter" in labels

        hover = handle_lsp(
            "hover",
            path="pkg/mod.py",
            content=src,
            line=0,
            character=8,  # Greeter
        )
        assert hover["ok"] is True
        assert hover["hover"]
        assert "Greeter" in hover["hover"]["contents"]
        assert "Say hello" in hover["hover"]["contents"]

    def test_definition_same_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _workspace(tmp_path, monkeypatch)
        src = (root / "pkg" / "mod.py").read_text(encoding="utf-8")
        out = handle_lsp(
            "definition",
            path="pkg/mod.py",
            content=src,
            line=7,
            character=16,  # Greeter() in greet_all
        )
        locs = out["locations"]
        assert locs
        assert locs[0]["path"].replace("\\", "/").endswith("pkg/mod.py")
        assert int(locs[0]["line"]) == 1

    def test_diagnostics_python(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _workspace(tmp_path, monkeypatch)
        out = handle_lsp(
            "diagnostics",
            path="pkg/broken.py",
            content="def x(\n",
        )
        diags = out["diagnostics"]
        assert diags
        assert diags[0]["severity"] == "error"
        assert diags[0]["line"] >= 1

    def test_diagnostics_json(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _workspace(tmp_path, monkeypatch)
        out = handle_lsp("diagnostics", path="a.json", content="{")
        assert out["diagnostics"]
        ok = handle_lsp("diagnostics", path="a.json", content='{"a": 1}')
        assert ok["diagnostics"] == []

    def test_path_escape_denied(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _workspace(tmp_path, monkeypatch)
        out = handle_lsp(
            "complete",
            path="../../etc/passwd",
            content="x = 1\n",
            prefix="x",
        )
        assert out["ok"] is False
        assert "error" in out

    def test_document_symbols(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _workspace(tmp_path, monkeypatch)
        src = (root / "pkg" / "mod.py").read_text(encoding="utf-8")
        out = handle_lsp("documentSymbol", path="pkg/mod.py", content=src)
        names = {s["name"] for s in out["symbols"]}
        assert "Greeter" in names
        assert "greet_all" in names
        assert "hello" in names
