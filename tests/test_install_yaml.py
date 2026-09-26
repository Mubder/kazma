"""kazma.yaml is the install's, parsed once per version (Stage 2, S4).

Memory's config and the embedder's each parsed ``kazma.yaml`` from the
process's working directory on every call -- several times per recall, since
the vector engine stamps and compares the embedding model name. They now read
``config_loader.install_yaml_section``: the file at the project root (where
``kazma-data`` is), parsed once per file version.
"""

from __future__ import annotations

import ast
import os
import textwrap
from pathlib import Path

import pytest

from kazma_core import config_loader, paths

REPO = Path(__file__).resolve().parents[1]


def _write(path: Path, model: str, weight: float) -> None:
    path.write_text(
        textwrap.dedent(
            f"""
            memory:
              embedding:
                model: {model}
              v2:
                archived_recall_weight: {weight}
            """
        ),
        encoding="utf-8",
    )


@pytest.fixture()
def install(tmp_path, monkeypatch):
    root = tmp_path / "install"
    root.mkdir()
    _write(root / "kazma.yaml", "model-one", 0.5)
    monkeypatch.setattr(paths, "_project_root", root)
    for name in ("KAZMA_EMBED_MODEL", "KAZMA_VECTOR_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("kazma_core.memory.embedder._store_embedding_overrides", lambda: {})
    monkeypatch.setattr("kazma_core.memory.config._read_store_overlay", lambda: {})
    parses: list[Path] = []
    real = config_loader.load_merged_yaml

    def counting(path=None):
        parses.append(Path(path))
        return real(path)

    monkeypatch.setattr(config_loader, "load_merged_yaml", counting)
    return root, parses


def test_memory_and_embedder_config_parse_the_file_once(install):
    from kazma_core.memory.config import read_memory_cfg
    from kazma_core.memory.embedder import get_embedding_model_name

    root, parses = install
    for _ in range(50):
        assert get_embedding_model_name() == "model-one"
        assert read_memory_cfg()["v2"]["archived_recall_weight"] == 0.5
    assert parses == [root / "kazma.yaml"]


def test_an_edit_is_read_on_the_next_call(install):
    from kazma_core.memory.embedder import get_embedding_model_name

    root, parses = install
    assert get_embedding_model_name() == "model-one"
    _write(root / "kazma.yaml", "model-two-longer", 0.5)  # a new size, whatever the clock
    assert get_embedding_model_name() == "model-two-longer"
    assert len(parses) == 2


def test_the_working_directory_is_not_the_install(install, tmp_path, monkeypatch):
    """A process started elsewhere reads the install's file, not the one it stands in."""
    from kazma_core.memory.embedder import get_embedding_model_name

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _write(elsewhere / "kazma.yaml", "a-stranger", 0.9)
    monkeypatch.chdir(elsewhere)
    assert get_embedding_model_name() == "model-one"


def test_callers_get_a_copy(install):
    block = config_loader.install_yaml_section("memory")
    block["v2"]["archived_recall_weight"] = 0.1
    assert config_loader.install_yaml_section("memory", "v2")["archived_recall_weight"] == 0.5


# ── gate: nothing else parses kazma.yaml from the working directory ───────

#: Files that may name the bare path, and why.
ALLOWED = {
    # The store parses once and caches (``_load_yaml``); entry points that know
    # the root pass it or call ``reload_from_root``.
    "kazma-core/kazma_core/config_store.py",
}


def _bare_yaml_reads(sources: dict[str, str]) -> list[str]:
    """``Path("kazma.yaml")`` / ``open("kazma.yaml")``, also as ``x or "kazma.yaml"``."""
    found = []
    for rel, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name not in ("Path", "open"):
                continue
            for arg in node.args[:1]:
                if any(isinstance(n, ast.Constant) and n.value == "kazma.yaml" for n in ast.walk(arg)):
                    found.append(f"{rel}:{node.lineno}")
    return found


def _product_sources() -> dict[str, str]:
    out = {}
    for pkg in REPO.glob("kazma-*/kazma_*"):
        for path in pkg.rglob("*.py"):
            rel = path.relative_to(REPO).as_posix()
            if "/tests/" not in rel:
                out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def test_no_module_parses_kazma_yaml_from_the_working_directory():
    found = [f for f in _bare_yaml_reads(_product_sources()) if f.split(":")[0] not in ALLOWED]
    assert not found, (
        "kazma.yaml read relative to the working directory -- use "
        "config_loader.install_yaml_section (the install's file, parsed once):\n  "
        + "\n  ".join(found)
    )


def test_the_gate_sees_both_forms():
    planted = {
        "x.py": textwrap.dedent(
            """
            from pathlib import Path
            def a():
                return Path("kazma.yaml").read_text()
            def b(p=None):
                return open(p or "kazma.yaml").read()
            def fine(root):
                return root / "kazma.yaml"
            """
        )
    }
    assert _bare_yaml_reads(planted) == ["x.py:4", "x.py:6"]
    assert os.path.basename("kazma.yaml") == config_loader.SHIPPED_CONFIG_NAME
