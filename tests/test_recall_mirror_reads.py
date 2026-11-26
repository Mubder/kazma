"""Recall never reads a mirror it could not read as "nothing found" (Spaces
S4-01, the 0b delta review's L-a and the ``strict=True`` follow-up; §44).

The Postgres mirror is read by recall in two ways:

* **Postgres primary**: the mirror IS memory. Its searches were strict, but a
  memory only the vector index found was fetched with ``strict`` off, so an
  outage there quietly dropped it from the answer. Now ``fetch_*`` are strict
  too: a failed read is a failed recall, said (``recall_failed:...``).
* **The top-up** of a thin local result with other installs' memories:
  every failure was taken at DEBUG for "nothing more", a space's refusal
  included. A refusal now fails the recall (it is no outage); an outage, a
  mirror that does not answer its ping, or one down since start keeps this
  install's own answer and says what it lacks: ``RecallResult.mirror_unread``,
  its own field, never ``degraded`` -- meaning search did not fall short, and
  every reader of ``degraded`` says it did (the review's M1). A top-up that
  fails half way keeps none of its rows (L2).

Every case goes through ``recall()``, each with its control. No database:
the mirror and the vector index are stand-ins. The gate at the end holds
every reader of ``degraded`` and ``mirror_unread`` to a register, and every
writer of ``degraded`` away from the mirror, with planted controls.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import struct
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_core.memory.recall import MIRROR_UNREAD, recall
from kazma_core.memory.schema_v2 import ensure_primary_schema

DIM = 8
MODEL = "mirror-reads-test-model"
QUESTION = "when does my passport run out"
OTHER_INSTALL = "b" * 32


def _unit(i: int) -> list[float]:
    v = [0.0] * DIM
    v[i] = 1.0
    return v


class _Embedder:
    dim = DIM

    def encode(self, text: str) -> list[float]:
        return _unit(0)


class _Refused(Exception):  # noqa: N818 - named for what the database said
    """A policy's refusal as the driver raises it: SQLSTATE 42501, which
    ``pg_helpers.is_space_refusal`` reads as a refusal once Spaces is enforced."""

    sqlstate = "42501"


class _Mirror:
    """The Postgres mirror: answers, is away, or refuses."""

    name = "postgres"

    def __init__(self, *, fails: BaseException | None = None, available: bool = True,
                 on: tuple[str, ...] = ("search", "fetch"), by_words: bool = True) -> None:
        self.fails = fails
        self.on = on
        self.by_words = by_words
        self.available = available
        self.strict: list[bool] = []
        self.rows = {"e_remote": {"id": "e_remote", "user_text": "My passport will run out in spring",
                                  "assistant_text": "", "summary_text": "", "tier": "episodic",
                                  "created_at": 1_780_000_000.0,
                                  "metadata_json": json.dumps({"install": OTHER_INSTALL})}}

    def _read(self, strict: bool, *kinds: str) -> bool:
        """Whether this read fails; a strict one raises, as the real reads do
        (a read that is not strict answers [])."""
        self.strict.append(strict)
        failing = self.fails is not None and any(kind in self.on for kind in kinds)
        if failing and strict:
            raise self.fails
        return failing

    def search_episodes(self, query: str, *, tenant_id: str = "default", limit: int = 10,
                        strict: bool = False) -> list[dict[str, Any]]:
        failing = self._read(strict, "search", "search_episodes")
        return [] if failing or not self.by_words else list(self.rows.values())

    def search_beliefs(self, query: str, *, tenant_id: str = "default", limit: int = 10,
                       strict: bool = False) -> list[dict[str, Any]]:
        self._read(strict, "search", "search_beliefs")
        return []

    def fetch_episodes(self, ids: list[str], *, tenant_id: str = "default",
                       strict: bool = False) -> list[dict[str, Any]]:
        return [] if self._read(strict, "fetch") else [self.rows[i] for i in ids if i in self.rows]

    def fetch_beliefs(self, ids: list[str], *, tenant_id: str = "default",
                      strict: bool = False) -> list[dict[str, Any]]:
        self._read(strict, "fetch")
        return []


class _Index:
    """The vector index: one memory, found by meaning only."""

    name = "pgvector"
    available = True
    write_ready = True

    def search(self, qvec: Any, *, tenant_id: str = "default", tier: Any = None, limit: int = 10,
               kind: str | None = None) -> list[tuple[str, float]]:
        return [] if kind == "belief" else [("e_remote", 0.9)]

    def similarities(self, qvec: Any, ids: Any, *, tenant_id: str = "default", kind: str | None = None) -> dict:
        return {i: 0.9 for i in ids}


@pytest.fixture()
def local(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """This install's memory database, with one memory on the question."""
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    conn.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, created_at, "
        "metadata_json, embedding_model_version, embedding) VALUES ('e_local', 'default', 's', 1, "
        "'Passport renewal: valid until March 2031', 'episodic', 1779000000.0, '{}', ?, ?)",
        (MODEL, struct.pack(f"{DIM}f", *_unit(0))))
    conn.commit()
    embedder = _Embedder()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: embedder)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: MODEL)
    monkeypatch.setattr("kazma_core.memory.state_backend.is_state_primary", lambda cfg=None: False)
    from kazma_core.memory.backends import LocalSqliteVectorBackend

    monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend",
                        lambda conn_=None: LocalSqliteVectorBackend(conn_ or conn))
    yield conn
    conn.close()


def _mirror(monkeypatch: pytest.MonkeyPatch, mirror: _Mirror) -> _Mirror:
    monkeypatch.setattr("kazma_core.memory.state_backend.get_state_backend", lambda: mirror)
    return mirror


# ── Postgres primary: the mirror is memory ───────────────────────────────


def _primary(monkeypatch: pytest.MonkeyPatch, mirror: _Mirror) -> None:
    _mirror(monkeypatch, mirror)
    monkeypatch.setattr("kazma_core.memory.state_backend.is_state_primary", lambda cfg=None: True)
    index = _Index()
    monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend", lambda conn=None: index)


def test_a_memory_the_primary_mirror_could_not_fetch_is_a_failed_recall(local: Any,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """The index found it, the mirror's word search answered, and the mirror
    was away when it was fetched: the answer left it out and said nothing
    (``fetch_*`` were not strict)."""
    mirror = _Mirror(fails=ConnectionError("the mirror is away"), on=("fetch",), by_words=False)
    _primary(monkeypatch, mirror)
    result = recall(QUESTION)
    assert result.failed and "primary_search_failed" in str(result.degraded), result.degraded
    assert True in mirror.strict


def test_the_primary_mirror_that_answers_gives_the_memory(local: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # The control: the same reads answered.
    mirror = _Mirror(by_words=False)
    _primary(monkeypatch, mirror)
    result = recall(QUESTION)
    assert not result.failed and [h.id for h in result.episodes] == ["e_remote"]
    assert mirror.strict and all(mirror.strict), "every read of the mirror as memory is strict"


# ── The top-up of a thin local result ────────────────────────────────────


def test_an_outage_keeps_the_local_answer_and_says_the_mirror_was_not_read(
        local: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _mirror(monkeypatch, _Mirror(fails=ConnectionError("the mirror is away")))
    result = recall(QUESTION, conn=local)
    assert [h.id for h in result.episodes] == ["e_local"] and not result.failed
    assert result.mirror_unread and result.degraded is None, "meaning search ran: not degraded (M1)"


def test_a_mirror_that_does_not_answer_its_ping_is_said_too(local: sqlite3.Connection,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _mirror(monkeypatch, _Mirror(available=False))
    result = recall(QUESTION, conn=local)
    assert [h.id for h in result.episodes] == ["e_local"] and result.mirror_unread


def test_a_mirror_down_since_start_is_said_and_no_mirror_is_not(local: sqlite3.Connection,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """L1: a configured mirror that failed its first ping comes back as the
    null backend, which recall read as "no mirror"."""
    from kazma_core.memory.state_backend import _NullStateBackend

    monkeypatch.setattr("kazma_core.memory.state_backend.get_state_backend", lambda: _NullStateBackend())
    monkeypatch.setattr("kazma_core.memory.state_backend.mirrors_to_postgres", lambda cfg=None: True)
    result = recall(QUESTION, conn=local)
    assert [h.id for h in result.episodes] == ["e_local"] and result.mirror_unread
    # The control: none configured.
    monkeypatch.setattr("kazma_core.memory.state_backend.mirrors_to_postgres", lambda cfg=None: False)
    assert not recall(QUESTION, conn=local).mirror_unread


def test_a_top_up_that_fails_half_way_keeps_none_of_its_rows(local: sqlite3.Connection,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """L2: the episode top-up answered, the belief search failed after it: the
    other install's memory stayed in the answer that said they were unread."""
    _mirror(monkeypatch, _Mirror(fails=ConnectionError("the mirror went away"), on=("search_beliefs",)))
    result = recall(QUESTION, conn=local)
    assert [h.id for h in result.episodes] == ["e_local"] and result.mirror_unread
    # The control: both searches answer, and the memory joins.
    _mirror(monkeypatch, _Mirror())
    assert [h.id for h in recall(QUESTION, conn=local).episodes] == ["e_local", "e_remote"]


def test_meaning_search_unavailable_and_the_mirror_unread_are_both_kept(local: sqlite3.Connection,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """L4: the embedder's note and the mirror's, from one recall."""
    class _Broken(_Embedder):
        def encode(self, text: str) -> list[float]:
            raise RuntimeError("the embedder is down")

    broken = _Broken()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: broken)
    _mirror(monkeypatch, _Mirror(fails=ConnectionError("the mirror is away")))
    result = recall(QUESTION, conn=local)
    assert not result.failed and "embedder" in str(result.degraded), result.degraded
    assert result.mirror_unread


def test_a_mirror_that_answers_tops_up_and_says_nothing(local: sqlite3.Connection,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    # The control: another install's memory joins, labelled weak, and nothing is missing.
    _mirror(monkeypatch, _Mirror())
    result = recall(QUESTION, conn=local)
    assert [h.id for h in result.episodes] == ["e_local", "e_remote"]
    assert result.degraded is None and not result.mirror_unread
    # No mirror at all is not an incomplete answer.
    from kazma_core.memory.state_backend import _NullStateBackend

    monkeypatch.setattr("kazma_core.memory.state_backend.get_state_backend", lambda: _NullStateBackend())
    monkeypatch.setattr("kazma_core.memory.state_backend.mirrors_to_postgres", lambda cfg=None: False)
    none = recall(QUESTION, conn=local)
    assert none.degraded is None and not none.mirror_unread


def test_a_refused_top_up_fails_the_recall(local: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    """L-a: the refusal L2 raised again landed in the top-up's DEBUG handler
    and read as "nothing more found"."""
    from kazma_core.spaces import scope

    _mirror(monkeypatch, _Mirror(fails=_Refused("permission denied: no space is set")))
    monkeypatch.setattr(scope, "_enforced", True)
    with scope.bind_scope("default", "owner"):
        result = recall(QUESTION, conn=local)
    assert result.failed and "search_failed" in str(result.degraded), result.degraded


def _memory_search(query: str) -> str:
    import asyncio

    from kazma_core.agent.tool_builtins import register_builtin_tools
    from kazma_core.agent.tool_registry import LocalToolRegistry

    registry = LocalToolRegistry()
    register_builtin_tools(registry)
    return asyncio.run(registry.get_tool("memory_search").func(query=query))


_EMBEDDER_DOWN = "meaning search is unavailable (the embedder failed)"


@pytest.mark.parametrize(("degraded", "mirror_unread", "words_only"), [
    (None, True, False),
    (_EMBEDDER_DOWN, False, True),  # the control: still said
    (_EMBEDDER_DOWN, True, True),
])
def test_the_memory_tool_says_what_an_unread_mirror_lacks(local: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
                                                          degraded: str | None, mirror_unread: bool,
                                                          words_only: bool) -> None:
    """The tool said "keyword matches only" for every incomplete search: for an
    unread mirror that is not true (meaning search ran)."""
    from kazma_core.memory.recall import RecallHit, RecallResult

    hit = RecallHit(id="e_local", content="Passport renewal: valid until March 2031", score=0.9,
                    kind="episode", source="local", metadata={})
    monkeypatch.setattr("kazma_core.memory.recall._recall",
                        lambda *a, **k: RecallResult(beliefs=[], episodes=[hit], degraded=degraded,
                                                     mirror_unread=mirror_unread))
    out = _memory_search(QUESTION)
    assert ("keyword matches only" in out) is words_only, out
    assert (MIRROR_UNREAD[1:] in out) is mirror_unread and "e_local" in out, out


@pytest.mark.parametrize("mirror_unread", [True, False])
def test_nothing_found_with_the_mirror_unread_is_no_failure(local: sqlite3.Connection,
                                                           monkeypatch: pytest.MonkeyPatch, mirror_unread: bool) -> None:
    """L3: the tool said "recall failed" though this install's search ran whole."""
    from kazma_core.memory.recall import RecallResult

    monkeypatch.setattr("kazma_core.memory.recall._recall",
                        lambda *a, **k: RecallResult(beliefs=[], episodes=[], mirror_unread=mirror_unread))
    out = _memory_search(QUESTION)
    assert "recall failed" not in out and "No relevant memories found" in out, out
    assert (MIRROR_UNREAD[1:] in out) is mirror_unread, out


def test_the_turn_note_and_the_quality_records_leave_the_mirror_out() -> None:
    """M1: the per-turn note told the model "keyword matches only", and the
    quality page counted the recall as degraded, for an unread mirror."""
    from kazma_core.memory.recall import RecallHit, RecallResult, recall_incomplete_note

    hit = RecallHit(id="e_local", content="x", score=0.9, kind="episode", source="local", metadata={})
    unread = RecallResult(beliefs=[], episodes=[hit], mirror_unread=True)
    assert recall_incomplete_note(unread) == ""
    assert not unread.degraded, "quality_watch counts a recall record's degraded code"
    # The control: meaning search unavailable is said.
    assert "keyword matches only" in recall_incomplete_note(
        RecallResult(beliefs=[], episodes=[hit], degraded=_EMBEDDER_DOWN, mirror_unread=True))


def test_the_memory_page_search_says_the_mirror_beside_the_hits(local: sqlite3.Connection,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.memory.federated_search import federated_search

    _mirror(monkeypatch, _Mirror(fails=ConnectionError("the mirror is away")))
    out = federated_search(QUESTION, include_knowledge=False)
    assert out["mirror_unread"] is True and out["degraded"] is None, out
    assert [h["id"] for h in out["hits"]] == ["e_local"]
    # The control: a mirror that answers.
    _mirror(monkeypatch, _Mirror())
    assert federated_search(QUESTION, include_knowledge=False)["mirror_unread"] is False


# ── every reader and writer of degraded, held to the source (M1) ─────────

ROOT = Path(__file__).resolve().parents[1]

#: Every product function that reads ``RecallResult.degraded`` (an attribute
#: or ``getattr``) and what it does with it. ``degraded`` means meaning search
#: fell short or recall failed; the mirror unread is not in it.
DEGRADED_READERS: dict[str, str] = {
    "kazma-core/kazma_core/agent/graph_supervisor.py::supervisor_node": "logs the per-turn note's reason",
    "kazma-core/kazma_core/agent/tool_builtins/memory.py::register_memory_tools._memory_search_sync":
        "says recall failed, or keyword matches only",
    "kazma-core/kazma_core/memory/benchmark.py::run_fact_languages": "a benchmark records it",
    "kazma-core/kazma_core/memory/benchmark_kinds.py::run_fact_kinds": "a benchmark records it",
    "kazma-core/kazma_core/memory/benchmark_kinds.py::run_fact_languages_kinds": "a benchmark records it",
    "kazma-core/kazma_core/memory/benchmark_kinds.py::run_lists": "a benchmark records it",
    "kazma-core/kazma_core/memory/benchmark_kinds.py::run_narrow_lists": "a benchmark records it",
    "kazma-core/kazma_core/memory/federated_search.py::federated_search": "the Memory page's degraded banner",
    "kazma-core/kazma_core/memory/recall.py::RecallResult.failed": "a failed recall's code",
    "kazma-core/kazma_core/memory/recall.py::recall": "joins the embedder's note",
    "kazma-core/kazma_core/memory/recall.py::recall_incomplete_note": "the per-turn note: keyword matches only",
    "kazma-core/kazma_core/memory/recall.py::search_with_status": "the graph search and the TUI panel",
    "kazma-core/kazma_core/memory/recall_records.py::record_recall": "the quality page and memory.recall_degraded",
    "kazma-ui/kazma_ui/health.py::_check_memory_recall": "the health canary's failure reason",
    "kazma-ui/kazma_ui/routes_direct/memory.py::register_memory_routes._memory_v2_probe":
        "the Memory page's probe banner",
}

#: Every product function that reads ``RecallResult.mirror_unread``: the
#: explicit searches, which say it. The per-turn note and the quality page do
#: not (``recall_incomplete_note``'s docstring says why).
MIRROR_UNREAD_READERS: dict[str, str] = {
    "kazma-core/kazma_core/agent/tool_builtins/memory.py::register_memory_tools._memory_search_sync":
        "the memory tool's own sentence",
    "kazma-core/kazma_core/memory/federated_search.py::federated_search": "the Memory page's mirror note",
    "kazma-ui/kazma_ui/routes_direct/memory.py::register_memory_routes._memory_v2_probe":
        "the Memory page's mirror note",
}

_FIELDS = ("degraded", "mirror_unread")


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--",
                             "kazma-*/kazma_*/*.py"], cwd=ROOT, capture_output=True, text=True, check=True)
    texts = {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in sorted(set(listed.stdout.split()))}
    return {rel: text for rel, text in texts.items() if any(f in text for f in _FIELDS)}


def _uses(sources: dict[str, str]) -> tuple[dict[str, set[str]], list[str]]:
    """Each field's readers (``path::function``), and every write of
    ``degraded`` whose value names the mirror."""
    readers: dict[str, set[str]] = {f: set() for f in _FIELDS}
    mirror_writes: list[str] = []
    for rel, text in sources.items():
        stack: list[str] = []

        def where() -> str:
            return f"{rel}::{'.'.join(stack) or '<module>'}"  # noqa: B023 - read while visiting

        def names_mirror(value: ast.AST) -> bool:
            return any((isinstance(n, ast.Name) and n.id in ("MIRROR_UNREAD", "mirror_unread"))
                       or (isinstance(n, ast.Attribute) and n.attr == "mirror_unread") for n in ast.walk(value))

        class _Visit(ast.NodeVisitor):
            def _scope(self, node: Any) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope  # noqa: N815 - ast's names

            def visit_Attribute(self, node: ast.Attribute) -> None:
                if node.attr in _FIELDS and isinstance(node.ctx, ast.Load):
                    readers[node.attr].add(where())
                self.generic_visit(node)

            def visit_Call(self, node: ast.Call) -> None:
                if (isinstance(node.func, ast.Name) and node.func.id == "getattr" and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant) and node.args[1].value in _FIELDS):
                    readers[node.args[1].value].add(where())
                for kw in node.keywords:
                    if kw.arg == "degraded" and names_mirror(kw.value):
                        mirror_writes.append(where())
                self.generic_visit(node)

            def visit_Assign(self, node: ast.Assign) -> None:
                for target in node.targets:
                    named = (isinstance(target, ast.Attribute) and target.attr == "degraded") or (
                        isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "degraded")
                    if named and names_mirror(node.value):
                        mirror_writes.append(where())
                self.generic_visit(node)

        _Visit().visit(ast.parse(text))
    return readers, mirror_writes


def test_every_reader_of_degraded_and_of_the_mirror_is_declared() -> None:
    readers, mirror_writes = _uses(_product_sources())
    for field, declared in (("degraded", DEGRADED_READERS), ("mirror_unread", MIRROR_UNREAD_READERS)):
        assert sorted(readers[field] - set(declared)) == [], (
            f"a new reader of RecallResult.{field}: declare what it says, and that an unread mirror is "
            "not meaning search fallen short (recall-strict review M1)")
        assert sorted(set(declared) - readers[field]) == [], f"no longer reads {field}: take it out"
    assert mirror_writes == [], f"the mirror unread is its own field, never degraded: {mirror_writes}"


def test_the_gate_sees_a_new_reader_and_a_mirror_written_into_degraded() -> None:
    # Negative controls, planted.
    planted = {"kazma-core/kazma_core/planted.py": (
        "def show(result):\n"
        "    return result.degraded\n"
        "def mark(result, unread):\n"
        "    result.degraded = MIRROR_UNREAD if unread else None\n"
        "    return RecallResult([], [], degraded=MIRROR_UNREAD)\n")}
    readers, mirror_writes = _uses(planted)
    assert readers["degraded"] == {"kazma-core/kazma_core/planted.py::show"}
    assert mirror_writes == ["kazma-core/kazma_core/planted.py::mark", "kazma-core/kazma_core/planted.py::mark"]


_PAGE = ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "memory_console.js"


def _banner_sites(js: str) -> tuple[int, int]:
    """Where the page shows a memory search's degraded banner, and where it
    shows the mirror's note."""
    return (len(re.findall(r"_memoryDegradedBanner\(data\.degraded\)", js)),
            len(re.findall(r"data\.mirror_unread \? _mirrorUnreadBanner\(\)", js)))


def test_every_memory_banner_on_the_page_says_the_mirror_too() -> None:
    degraded, mirror = _banner_sites(_PAGE.read_text(encoding="utf-8"))
    assert degraded >= 2 and degraded == mirror, (degraded, mirror)
    # The control: a site that shows the degraded banner alone.
    assert _banner_sites("if (data.degraded) x.push(_memoryDegradedBanner(data.degraded));") == (1, 0)
