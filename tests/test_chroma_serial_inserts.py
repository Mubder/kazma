"""Chroma inserts a replayed log on one thread (KB-Q01, 2026-10-21).

The Knowledge soak crashed three times (2026-10-17/19) with an access
violation inside Chroma's HNSW insert, at the same address each time: the
first read of a collection after a client starts applies its write log on
Chroma's own worker threads, and that insert races in hnswlib (about one
client start in 5,000; ``scripts/chroma_replay_race.py``). Kazma gives the
pool one thread before any client starts
(``chroma_local.serial_index_inserts``).

* the function sets the variable when nothing set it, and keeps (and names
  once) an operator's other value;
* every place that builds a Chroma client calls it first (a gate over the
  product and the scripts, with a planted client as its negative control);
* in a fresh process with the real chromadb, a replay through Kazma's own
  client adds one thread, and the same replay without the call adds Chroma's
  whole pool (the negative control).
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from kazma_core.memory import chroma_local

ROOT = Path(__file__).resolve().parents[1]
_CLIENT_CALLS = {"PersistentClient", "EphemeralClient", "Client", "HttpClient"}


def test_it_sets_one_thread_when_nothing_set_it(monkeypatch):
    monkeypatch.delenv(chroma_local.INSERT_THREADS_ENV, raising=False)
    assert chroma_local.serial_index_inserts() == "1"
    assert os.environ[chroma_local.INSERT_THREADS_ENV] == "1"


def test_an_operator_value_is_kept_and_named_once(monkeypatch, caplog):
    monkeypatch.setenv(chroma_local.INSERT_THREADS_ENV, "8")
    monkeypatch.setattr(chroma_local, "_INSERT_THREADS_SAID", type(chroma_local._INSERT_THREADS_SAID)())
    with caplog.at_level(logging.WARNING, logger=chroma_local.__name__):
        assert chroma_local.serial_index_inserts() == "8"
        assert chroma_local.serial_index_inserts() == "8"
    said = [r for r in caplog.records if r.name == chroma_local.__name__ and "RAYON_NUM_THREADS=8" in r.getMessage()]
    assert len(said) == 1 and said[0].levelno == logging.WARNING
    assert os.environ[chroma_local.INSERT_THREADS_ENV] == "8"


def _client_calls_without_the_pin(source: str) -> list[int]:
    """Lines building a Chroma client with no ``serial_index_inserts()``
    call earlier in the same function."""
    tree = ast.parse(source)
    found: list[int] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            continue
        body_nodes = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
        pins = [n.lineno for n in body_nodes
                if (isinstance(n.func, ast.Name) and n.func.id == "serial_index_inserts")
                or (isinstance(n.func, ast.Attribute) and n.func.attr == "serial_index_inserts")]
        for call in body_nodes:
            func = call.func
            if not (isinstance(func, ast.Attribute) and func.attr in _CLIENT_CALLS
                    and isinstance(func.value, ast.Name) and func.value.id == "chromadb"):
                continue
            if not any(line < call.lineno for line in pins):
                found.append(call.lineno)
    return sorted(set(found))


def _sources() -> list[Path]:
    paths = [p for pkg in ROOT.glob("kazma-*") for p in pkg.rglob("*.py")
             if "tests" not in p.parts and ".venv" not in p.parts]
    paths += [p for p in (ROOT / "scripts").rglob("*.py")]
    # The drill builds Chroma's client on purpose without the call: it
    # measures the crash with Chroma's own thread pool.
    return [p for p in paths if p.name != "chroma_replay_race.py"]


def test_every_chroma_client_is_built_after_the_call():
    offenders = []
    built = 0
    for path in _sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "chromadb." not in text:
            continue
        built += sum(text.count(f"chromadb.{name}(") for name in _CLIENT_CALLS)
        offenders += [f"{path.relative_to(ROOT)}:{line}" for line in _client_calls_without_the_pin(text)]
    assert built >= 3, "the gate found too few Chroma clients to be reading the code"
    assert not offenders, "a Chroma client built before serial_index_inserts(): " + ", ".join(offenders)


def test_the_gate_fails_on_a_client_built_without_the_call():
    planted = textwrap.dedent("""
        def open_store(path):
            import chromadb
            return chromadb.PersistentClient(path=path)

        def open_store_pinned(path):
            serial_index_inserts()
            import chromadb
            return chromadb.PersistentClient(path=path)
    """)
    assert _client_calls_without_the_pin(planted) == [4]


_REPLAY = textwrap.dedent("""
    import os, random, sys
    import psutil
    folder, pinned = sys.argv[1], sys.argv[2] == "pinned"
    if pinned:
        from kazma_core.memory import vector_store_global
        client = vector_store_global._shared_client(folder)
    else:
        import chromadb
        from chromadb.config import Settings
        client = chromadb.PersistentClient(path=folder, settings=Settings(anonymized_telemetry=False))
    col = client.get_or_create_collection("replay", configuration={"hnsw": {"space": "cosine"}},
                                          embedding_function=None)
    before = psutil.Process().num_threads()
    rng = random.Random(1)
    col.upsert(ids=[f"a{i}" for i in range(400)],
               embeddings=[[rng.random() for _ in range(64)] for _ in range(400)])
    col.get(limit=1)  # the log is applied by now: Chroma's insert threads have started
    print(psutil.Process().num_threads() - before, os.environ.get("RAYON_NUM_THREADS"))
""")


def _threads_added(tmp_path: Path, pinned: bool) -> tuple[int, str]:
    env = {k: v for k, v in os.environ.items() if k != chroma_local.INSERT_THREADS_ENV}
    folder = tmp_path / ("pinned" if pinned else "default")
    out = subprocess.run([sys.executable, "-c", _REPLAY, str(folder), "pinned" if pinned else "default"],
                         env=env, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    added, value = out.stdout.split()[-2:]
    return int(added), value


def test_a_replay_through_kazmas_client_runs_on_one_thread(tmp_path):
    pytest.importorskip("psutil")
    chromadb = pytest.importorskip("chromadb")
    if getattr(chromadb, "__path__", None) is None:
        pytest.skip("a chromadb stand-in, not the package")
    if (os.cpu_count() or 1) < 4:
        pytest.skip("too few CPUs for Chroma's own pool to show")
    added, value = _threads_added(tmp_path, pinned=True)
    assert value == "1"
    assert added <= 2, f"the replay started {added} threads"
    control, _ = _threads_added(tmp_path, pinned=False)  # Chroma's own pool: the crash's shape
    assert control >= 4, f"Chroma's own pool started only {control} threads; the check proves nothing here"
