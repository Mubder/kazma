"""Re-embedding memory never deletes the Knowledge Library's vectors.

``scripts/reembed.py`` ended every run with "7. Clear ChromaDB vector store":
``shutil.rmtree(data_dir() / "vector_memory")``. That folder held the V1 chat
index once; since 2026-09-26 it is the Knowledge Library's live Chroma store
(``paths.vector_memory_path``), so a manual re-embed wiped every library's
meaning search until the "knowledge vector repair" pass had embedded it all
again -- hours on a CPU. The product's rebuild (``memory/reembed.py``) had
already stopped doing it; the script, a second copy of the same rebuild, was
missed. The script now runs the product's function, so there is one rebuild.

* The script, run against a data dir holding a Chroma folder, re-embeds the
  memories and leaves the folder as it was. The old step, run the same way,
  deletes it (negative control).
* No product code or script deletes a folder tree in a function that names
  the Knowledge vector store, and the gate flags the old step (negative
  control).
"""

from __future__ import annotations

import ast
import importlib.util
import sqlite3
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "reembed.py"
OLD_VECTOR = b"\x00\x00\x80?" * 8

#: The step the script ran last until 2026-10-14, as it was written.
OLD_STEP = textwrap.dedent(
    '''
    import os
    import shutil

    def clear_vector_store(data_dir):
        # 7. Clear ChromaDB vector store (derived indexes, auto-recreate)
        vec_dir = os.path.join(data_dir(), "vector_memory")
        if os.path.isdir(vec_dir):
            shutil.rmtree(vec_dir)
    '''
)


# ── the script, run ─────────────────────────────────────────────────────


class _Embedder:
    dim = 8

    def encode(self, text):
        return [0.25] * 8


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("KAZMA_VECTOR_PATH", raising=False)
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _Embedder())
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "new-model")
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(db)
    db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "created_at, metadata_json, embedding, embedding_model_version) VALUES "
        "('e1', 'default', 's', 1, 'where is the pump manual', 'shelf two', 'episodic', 0, '{}', ?, 'old-model')",
        (OLD_VECTOR,),
    )
    db.commit()
    db.close()
    store = tmp_path / "vector_memory"
    store.mkdir()
    (store / "chroma.sqlite3").write_bytes(b"a library's vectors")
    return tmp_path


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_re_embeds_memory_and_keeps_the_knowledge_vectors(data_dir):
    from kazma_core.paths import vector_memory_path

    assert Path(vector_memory_path()) == data_dir / "vector_memory"
    script = _load(SCRIPT, "reembed_script_under_test")
    assert script.main() == 0

    assert (data_dir / "vector_memory" / "chroma.sqlite3").read_bytes() == b"a library's vectors"
    with sqlite3.connect(str(data_dir / "memory_state.db")) as db:
        blob, model = db.execute("SELECT embedding, embedding_model_version FROM episodes WHERE id = 'e1'").fetchone()
    assert model == "new-model" and blob != OLD_VECTOR
    assert (data_dir / "memory_state.db.pre_reembed").exists()


def test_the_old_step_deleted_them(data_dir, tmp_path_factory):
    """Negative control: the step the script ended with, run on the same data
    dir, takes the Knowledge Library's store with it."""
    from kazma_core.paths import data_dir as resolve_data_dir

    module = tmp_path_factory.mktemp("old_step") / "old_step.py"
    module.write_text(OLD_STEP, encoding="utf-8")
    _load(module, "reembed_old_step").clear_vector_store(resolve_data_dir)
    assert not (data_dir / "vector_memory").exists()


def test_without_a_model_the_script_changes_nothing(data_dir, monkeypatch):
    """The script inherits the product's rule (Memory audit M28): the model
    first, so a failed load leaves every vector and the store as they were."""
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    script = _load(SCRIPT, "reembed_script_no_model")
    assert script.main() == 1
    with sqlite3.connect(str(data_dir / "memory_state.db")) as db:
        assert db.execute("SELECT embedding FROM episodes WHERE id = 'e1'").fetchone()[0] == OLD_VECTOR
    assert (data_dir / "vector_memory" / "chroma.sqlite3").exists()


# ── the class: nothing deletes the store ────────────────────────────────

#: Calls that remove a folder and everything in it.
_TREE_DELETES = {"rmtree", "_rmtree_force", "_force_rmtree", "removedirs"}


def _python_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "*.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    out = []
    for rel in listed:
        parts = Path(rel).parts
        if "tests" in parts or not (parts[0].startswith("kazma-") or parts[0] == "scripts"):
            continue
        out.append(REPO_ROOT / rel)
    return out


def _names_the_store(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and "vector_memory" in sub.value:
            return True
        if isinstance(sub, ast.Name) and sub.id == "vector_memory_path":
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "vector_memory_path":
            return True
    return False


def _deletes_a_tree(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in _TREE_DELETES:
                return True
    return False


def _violations(source: str, label: str) -> list[str]:
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _deletes_a_tree(node) and _names_the_store(node):
            found.append(f"{label}:{node.lineno} {node.name}")
    return found


#: Functions that delete a folder named like the store and are not the
#: store, each with why.
_DECLARED = {
    ("scripts/knowledge_soak.py", "run_scale"):
        "the KB-Q01 harness 'loses the machine' of its own step directory (<data dir>/scale-<size>-<lang>, "
        "a private temporary folder unless --data-dir names one) to time the re-embed after a restore; it "
        "never opens an install's vector store",
}


def test_nothing_deletes_the_knowledge_vector_store():
    files = _python_files()
    assert any(p.name == "reembed.py" and p.parent.name == "scripts" for p in files)  # the gate sees scripts/
    assert any(p.name == "universal.py" for p in files)  # ...and the packages
    found = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        found += [v for v in _violations(path.read_text(encoding="utf-8"), rel)
                  if (rel, v.rsplit(" ", 1)[-1]) not in _DECLARED]
    assert not found, (
        "A function deletes a folder tree and names the Knowledge Library's vector store "
        "(paths.vector_memory_path / vector_memory). That folder is the live Chroma store; a "
        "model change is the knowledge vector repair pass's job (KnowledgeIndex.backfill_vectors):\n  "
        + "\n  ".join(found)
    )


def test_the_gate_flags_the_old_step():
    """Negative control of the gate."""
    assert _violations(OLD_STEP, "old_step.py") == ["old_step.py:5 clear_vector_store"]


def test_every_declared_exemption_is_still_a_tree_delete():
    """A declaration that no longer matches its function is stale."""
    for (rel, name), _why in _DECLARED.items():
        found = _violations((REPO_ROOT / rel).read_text(encoding="utf-8"), rel)
        assert any(v.endswith(f" {name}") for v in found), (rel, name)

