"""Recall never reads a mirror it could not read as "nothing found" (Spaces
S4-01, the 0b delta review's L-a and the ``strict=True`` follow-up; §44).

The Postgres mirror is read by recall in two ways:

* **Postgres primary**: the mirror IS memory. Its searches were strict, but a
  memory only the vector index found was fetched with ``strict`` off, so an
  outage there quietly dropped it from the answer. Now ``fetch_*`` are strict
  too: a failed read is a failed recall, said (``recall_failed:...``).
* **The top-up** of a thin local result with other installs' memories:
  every failure was taken at DEBUG for "nothing more", a space's refusal
  included. A refusal now fails the recall (it is no outage); an outage, or a
  mirror that does not answer its ping, keeps this install's own answer and
  says what it lacks (``recall.MIRROR_UNREAD``).

Every case goes through ``recall()``, each with its control. No database:
the mirror and the vector index are stand-ins.
"""

from __future__ import annotations

import json
import sqlite3
import struct
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

    def _read(self, strict: bool, kind: str) -> bool:
        """Whether this read fails; a strict one raises, as the real reads do
        (a read that is not strict answers [])."""
        self.strict.append(strict)
        failing = self.fails is not None and kind in self.on
        if failing and strict:
            raise self.fails
        return failing

    def search_episodes(self, query: str, *, tenant_id: str = "default", limit: int = 10,
                        strict: bool = False) -> list[dict[str, Any]]:
        failing = self._read(strict, "search")
        return [] if failing or not self.by_words else list(self.rows.values())

    def search_beliefs(self, query: str, *, tenant_id: str = "default", limit: int = 10,
                       strict: bool = False) -> list[dict[str, Any]]:
        self._read(strict, "search")
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
    assert result.degraded == MIRROR_UNREAD


def test_a_mirror_that_does_not_answer_its_ping_is_said_too(local: sqlite3.Connection,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _mirror(monkeypatch, _Mirror(available=False))
    result = recall(QUESTION, conn=local)
    assert [h.id for h in result.episodes] == ["e_local"] and result.degraded == MIRROR_UNREAD


def test_a_mirror_that_answers_tops_up_and_says_nothing(local: sqlite3.Connection,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    # The control: another install's memory joins, labelled weak, and nothing is missing.
    _mirror(monkeypatch, _Mirror())
    result = recall(QUESTION, conn=local)
    assert [h.id for h in result.episodes] == ["e_local", "e_remote"] and result.degraded is None
    # No mirror at all is not an incomplete answer.
    from kazma_core.memory.state_backend import _NullStateBackend

    monkeypatch.setattr("kazma_core.memory.state_backend.get_state_backend", lambda: _NullStateBackend())
    assert recall(QUESTION, conn=local).degraded is None


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


@pytest.mark.parametrize(("degraded", "words_only"), [
    (MIRROR_UNREAD, False),
    ("meaning search is unavailable (the embedder failed)", True),  # the control: still said
])
def test_the_memory_tool_says_what_an_unread_mirror_lacks(local: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch,
                                                          degraded: str, words_only: bool) -> None:
    """The tool said "keyword matches only" for every incomplete search: for an
    unread mirror that is not true (meaning search ran)."""
    from kazma_core.memory.recall import RecallHit, RecallResult

    hit = RecallHit(id="e_local", content="Passport renewal: valid until March 2031", score=0.9,
                    kind="episode", source="local", metadata={})
    monkeypatch.setattr("kazma_core.memory.recall._recall",
                        lambda *a, **k: RecallResult(beliefs=[], episodes=[hit], degraded=degraded))
    out = _memory_search(QUESTION)
    assert degraded in out and ("keyword matches only" in out) is words_only, out
