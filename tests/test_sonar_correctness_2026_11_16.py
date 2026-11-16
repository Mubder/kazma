"""Correctness defects the SonarCloud review of 2026-11-16 found, each run
through the real code and each with the old behaviour as its control.

* the memory backfill called ``initialize_model_registry()`` with no store
  (a TypeError at DEBUG: every CLI backfill skipped belief extraction);
* two package ``__all__`` lists named what the package never defined
  (``import *`` raised);
* ``kazma ask --acp`` answered "cancelled" to a Ctrl+C and kept serving, the
  turn running on;
* the hardware telemetry stream turned a cancel into a normal end;
* the SQLite graph backend called itself available whatever happened, and
  left a connection open per read;
* the dialect detector called any text with no dialect marker MSA at
  confidence 1.0, English included;
* the raw settings transaction rolled back only on ``Exception``;
* the gateway's lifespan stopped nothing when the app ended with an error.
"""

from __future__ import annotations

import ast
import asyncio
import io
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _product_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z", "--", "kazma-*"], cwd=ROOT,
                         capture_output=True, check=True).stdout.decode("utf-8")
    return [ROOT / rel for rel in out.split("\0")
            if rel.endswith(".py") and "/tests/" not in rel and "_tests/" not in rel]


# ── the backfill's model client ──────────────────────────────────────────


def test_the_backfill_makes_the_registry_from_the_settings_store(monkeypatch):
    from kazma_core import config_store, model_registry
    from kazma_core.memory import backfill_v2

    made: list[object] = []
    client = object()

    def missing():
        raise RuntimeError("no registry in this process")

    def initialize(store):
        made.append(store)
        return SimpleNamespace(get_client=lambda: client)

    store = object()
    monkeypatch.setattr(model_registry, "get_model_registry", missing)
    monkeypatch.setattr(model_registry, "initialize_model_registry", initialize)
    monkeypatch.setattr(config_store, "get_config_store", lambda: store)
    assert backfill_v2._backfill_client() is client
    assert made == [store]


def _calls_without_a_store(source: str) -> list[int]:
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", "")) == "initialize_model_registry"
            and not node.args and not node.keywords]


def test_every_registry_initialization_names_its_store():
    """Gate: ``initialize_model_registry`` takes the settings store."""
    found = []
    for path in _product_files():
        text = path.read_text(encoding="utf-8")
        if "initialize_model_registry(" in text:
            found += [f"{path.relative_to(ROOT)}:{n}" for n in _calls_without_a_store(text)]
    assert found == [], found


def test_the_registry_gate_sees_the_old_call():
    assert _calls_without_a_store("initialize_model_registry()\n") == [1]


# ── __all__ names what the module defines ────────────────────────────────


def _undefined_all_names(source: str, path: Path | None = None) -> list[str]:
    tree = ast.parse(source)
    defined: set[str] = set()
    listed: list[str] = []

    def bind(target: ast.AST) -> None:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                defined.add(node.id)

    def walk(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    bind(t)
                    if isinstance(t, ast.Name) and t.id == "__all__" and isinstance(node.value, (ast.List, ast.Tuple)):
                        listed.extend(e.value for e in node.value.elts if isinstance(e, ast.Constant))
            elif isinstance(node, ast.AnnAssign):
                bind(node.target)
                if isinstance(node.target, ast.Name) and node.target.id == "__all__" and isinstance(
                        node.value, (ast.List, ast.Tuple)):
                    listed.extend(e.value for e in node.value.elts if isinstance(e, ast.Constant))
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    defined.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, (ast.If, ast.Try)):
                walk(node.body)
                walk(getattr(node, "orelse", []))
                for handler in getattr(node, "handlers", []):
                    walk(handler.body)
                walk(getattr(node, "finalbody", []))
    walk(tree.body)
    if "__getattr__" in defined:  # a lazy package answers its names itself
        return []
    if path is not None and path.name == "__init__.py":
        # A package may list its submodules: ``import *`` imports them.
        here = path.parent
        defined |= {n for n in listed if (here / f"{n}.py").is_file() or (here / n / "__init__.py").is_file()}
    return [name for name in listed if name not in defined]


def test_every_all_names_what_its_module_defines():
    """Gate: a name in ``__all__`` the module does not define makes
    ``from module import *`` raise."""
    found = []
    for path in _product_files():
        text = path.read_text(encoding="utf-8")
        if "__all__" in text:
            missing = _undefined_all_names(text, path)
            if missing:
                found.append(f"{path.relative_to(ROOT)}: {missing}")
    assert found == [], found


def test_the_all_gate_sees_the_old_packages():
    old = 'from __future__ import annotations\n\n__all__ = ["DocxEngine"]\n'
    assert _undefined_all_names(old) == ["DocxEngine"]
    lazy = '__all__ = ["X"]\n\ndef __getattr__(name):\n    raise AttributeError(name)\n'
    assert _undefined_all_names(lazy) == []


def test_star_imports_of_the_two_packages_work():
    namespace: dict[str, object] = {}
    exec("from kazma_core.voice import *\nfrom kazma_core.documents.engines import *", namespace)


# ── ACP: a cancel of the server stops the turn ───────────────────────────


async def test_a_cancelled_acp_prompt_stops_its_turn(monkeypatch):
    from kazma_core.cli import ask as ask_mod

    started = asyncio.Event()
    stopped: list[str] = []

    async def turn(*_a, **_k):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.append("turn")

    monkeypatch.setattr(ask_mod, "run_ask", turn)
    rpc = ask_mod._JsonRpcStdio(io.StringIO(""), io.StringIO())
    state = ask_mod.AcpSessionState(yolo=True)
    task = asyncio.create_task(ask_mod._acp_run_prompt(
        rpc, state, sid="s1", prompt="hi", msg_id=1, yolo=True, workspace=""))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped == ["turn"], "the turn ran on after the cancel"


# ── the telemetry stream passes a cancel on ──────────────────────────────


async def test_the_telemetry_stream_passes_a_cancel_on(monkeypatch):
    from kazma_core.telemetry import HardwareMonitor

    monitor = HardwareMonitor()

    async def stats():
        return "snapshot"

    monkeypatch.setattr(monitor, "get_stats", stats)
    stream = monitor.stream(interval=30.0)
    assert await stream.__anext__() == "snapshot"
    nxt = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0.05)
    nxt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await nxt
    await stream.aclose()


# ── the SQLite graph backend ─────────────────────────────────────────────


def test_an_unreadable_sqlite_graph_is_not_available():
    from kazma_core.memory.graph_backend import SqliteGraphBackend

    empty = sqlite3.connect(":memory:")
    assert SqliteGraphBackend(empty).available is False
    empty.execute("CREATE TABLE beliefs (id TEXT)")
    assert SqliteGraphBackend(empty).available is True


def test_the_sqlite_graph_closes_the_connections_it_opens(monkeypatch, tmp_path):
    from kazma_core import paths
    from kazma_core.memory.graph_backend import SqliteGraphBackend

    monkeypatch.setattr(paths, "primary_memory_db", lambda: str(tmp_path / "memory_state.db"))
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def connect(*a, **k):
        conn = real_connect(*a, **k)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect)
    backend = SqliteGraphBackend()
    assert backend.available is True
    assert backend.neighbors("user") == []
    assert len(opened) == 2
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


# ── the dialect detector ─────────────────────────────────────────────────


def test_text_with_no_dialect_marker_is_msa_without_certainty():
    from kazma_core.dialect_detector import _rule_based_detect

    english = _rule_based_detect("Please schedule the meeting for tomorrow.")
    assert english.dialect == "msa" and english.confidence <= 0.5
    formal = _rule_based_detect("بناءً على القرار الذي صدر خلال الاجتماع")
    assert formal.dialect == "msa" and 0.5 < formal.confidence <= 0.8
    kuwaiti = _rule_based_detect("شلونك؟ شنو صار اليوم")
    assert kuwaiti.dialect == "kw"


# ── the raw settings transaction ─────────────────────────────────────────


def test_a_cancelled_settings_transaction_rolls_back(tmp_path):
    from kazma_core.config_store import ConfigStore

    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        with pytest.raises(asyncio.CancelledError):
            with store.transaction() as conn:
                conn.execute(
                    "INSERT INTO settings (key, value, category, updated_at)"
                    " VALUES ('a.b', '1', 'general', '2026-11-16')")
                raise asyncio.CancelledError()
        assert store.get("a.b") is None
        store.set("a.c", 2)  # no transaction left open on the connection
        assert store.get("a.c") == 2
    finally:
        store.close()


# ── the gateway's lifespan ───────────────────────────────────────────────


async def test_the_gateway_lifespan_stops_after_an_error():
    from kazma_gateway.gateway import GatewayManager

    calls: list[str] = []

    async def start():
        calls.append("start")

    async def stop():
        calls.append("stop")

    gateway = SimpleNamespace(start=start, stop=stop)
    with pytest.raises(RuntimeError):
        async with GatewayManager.lifespan(gateway, None):
            raise RuntimeError("the app failed")
    assert calls == ["start", "stop"]
