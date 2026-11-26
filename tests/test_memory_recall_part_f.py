"""Recall, part F of the November memory plan (framework audit F9, F2 step 3).

What recall now does that it did not, each beside recall as it was:

* **The question's time** (``time_words``): "what did I tell you about my
  race in August?" keeps the August turn and leaves June's out; "where did I
  live in June?" answers with the value that held in June, not today's;
  "where did I live at first?" shows the earlier value before the current one.
  Control: recall that does not read the time (``_time_ask`` -> None).
* **A follow-up stored as typed** ("Yes, book it for Saturday at 10.") is
  joined to the line of the turn it goes on from. Control: no join.
* **The chat's own turns the model already reads** are not shown again.
  Control: recall that is not told them.
* **A request for every memory** gets a longer list and a note naming
  ``memory_list``. Control: the question read as an ordinary one.
* **Every recalled conversation carries its id** (``memory_read``).
* **An incomplete recall says so** (``recall_incomplete_note``), and a
  Postgres-primary search that fails is a failed recall, never "nothing
  remembered". Control: the mirror's search as it was (a failure read as no
  matches).

Words only (no embedder): the meaning side is the benchmark's (``dense``,
``follow_up``, ``list``, ``temporal``).
"""

from __future__ import annotations

import ast
import sqlite3
from datetime import UTC
from pathlib import Path

import pytest
from kazma_core.memory import benchmark
from kazma_core.memory import recall as recall_mod
from kazma_core.memory.limits import LIST_LIMIT, turn_key
from kazma_core.memory.recall import format_recall_block, recall, recall_incomplete_note
from kazma_core.memory.schema_v2 import ensure_primary_schema

ROOT = Path(__file__).resolve().parents[1]

#: BASE_TIME is 28 May 2026 (UTC): day 10 is 7 June, day 75 is 11 August.
RUN_LOGS = [{"id": f"run{i}", "day": 40 + i, "kind": "persona", "turns": [
    {"user": f"Long run today: {14 + i} km along the river.", "assistant": "Logged."}]} for i in range(8)]
DATASET = {
    "sessions": [
        {"id": "p1", "day": 0, "kind": "persona", "turns": [
            {"user": "I live in Paris, near the canal.", "assistant": "Lovely part of town."}]},
        {"id": "p2", "day": 140, "kind": "persona", "turns": [
            {"user": "We moved to London, I live there now.", "assistant": "Welcome to London!"}]},
        {"id": "race1", "day": 10, "kind": "persona", "turns": [
            {"user": "I ran the Lisbon race today, a half marathon in 1:52.",
             "assistant": "A strong half marathon."}]},
        {"id": "race2", "day": 75, "kind": "persona", "turns": [
            {"user": "Ran the Porto race this morning, the 10k in 48 minutes.",
             "assistant": "Well done in Porto."}]},
        {"id": "trip", "day": 30, "kind": "persona", "turns": [
            {"user": "Can you find me a funicular ride in Lisbon with a view?",
             "assistant": "The Gloria funicular ride climbs to a viewpoint over Lisbon."},
            {"user": "Yes, book it for Saturday at 10.", "assistant": "Done, Saturday at 10:00."}]},
        {"id": "tea", "day": 100, "kind": "persona", "turns": [
            {"user": "My favourite tea is jasmine green tea.", "assistant": "Jasmine it is."}]},
        {"id": "wk4", "day": 20, "kind": "persona", "turns": [
            {"user": "Week 4 bike ride: 32 km along the coast.", "assistant": "Logged."}]},
        {"id": "wk14", "day": 90, "kind": "persona", "turns": [
            {"user": "Week 14 bike ride: 41 km along the coast.", "assistant": "Logged."}]},
        *RUN_LOGS,
    ],
    "facts": [
        {"id": "f1", "day": 0, "session": "p1", "turn": 1, "subject": "user", "predicate": "lives_in",
         "object": "Paris", "predicate_type": "functional", "extraction_method": "user_stated", "importance": 4},
        {"id": "f2", "day": 140, "session": "p2", "turn": 1, "subject": "user", "predicate": "lives_in",
         "object": "London", "predicate_type": "functional", "extraction_method": "user_stated", "importance": 4},
    ],
    "summaries": [],
}
DAY = 86400.0


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr(recall_mod, "_memory_zone", lambda: UTC)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    refs = benchmark._seed(conn, DATASET)
    yield conn, refs
    conn.close()


def _recall(conn, question: str, *, day: float = 150, **kw):
    result = recall(question, conn=conn, tenant_id=benchmark.BENCH_TENANT, local_only=True,
                    now=benchmark.BASE_TIME + day * DAY, **kw)
    assert not result.failed, result
    return result


def _no_time(monkeypatch):
    """Recall as it was before part F: the question's time is not read."""
    monkeypatch.setattr(recall_mod, "_time_ask", lambda *_a, **_k: None)


# ── The question's time ──────────────────────────────────────────────────


RACE_Q = "What did I tell you about my race in August?"


def test_a_question_about_what_was_said_in_a_month_keeps_that_month(memory):
    conn, refs = memory
    ids = [h.id for h in _recall(conn, RACE_Q).episodes]
    assert refs["race2#1"] in ids and refs["race1#1"] not in ids, ids


def test_without_reading_the_time_a_june_race_answers_an_august_question(memory, monkeypatch):
    """Negative control."""
    conn, refs = memory
    _no_time(monkeypatch)
    ids = [h.id for h in _recall(conn, RACE_Q).episodes]
    assert refs["race1#1"] in ids, ids


def _facts(result) -> list[str]:
    return [str(h.metadata.get("object")) for h in result.beliefs]


def test_what_was_true_in_a_month_is_that_months_value(memory):
    conn, _refs = memory
    assert _facts(_recall(conn, "Where did I live in June?")) == ["Paris"]


def test_without_reading_the_time_june_is_answered_with_today(memory, monkeypatch):
    """Negative control: recall showed where the user lives now."""
    conn, _refs = memory
    _no_time(monkeypatch)
    assert _facts(_recall(conn, "Where did I live in June?")) == ["London"]


def test_at_first_shows_the_earlier_value_before_the_current_one(memory):
    conn, _refs = memory
    result = _recall(conn, "Where did I live at first?")
    assert _facts(result) == ["Paris", "London"]
    block = format_recall_block(result)
    assert "[May 2026 to Oct 2026] user lives in Paris" in block, block


def test_without_reading_the_time_at_first_shows_today_only(memory, monkeypatch):
    """Negative control."""
    conn, _refs = memory
    _no_time(monkeypatch)
    assert _facts(_recall(conn, "Where did I live at first?")) == ["London"]


def test_no_time_named_shows_no_earlier_value(memory):
    conn, _refs = memory
    assert _facts(_recall(conn, "Where do I live?")) == ["London"]


# ── A follow-up joined to the turn it goes on from ───────────────────────


FUNICULAR_Q = "When did I book the funicular ride?"


def test_a_stored_follow_up_is_joined_to_the_line_of_its_turn(memory):
    conn, refs = memory
    result = _recall(conn, FUNICULAR_Q)
    lead = next(h for h in result.episodes if h.id == refs["trip#1"])
    assert lead.metadata.get("joined") == [refs["trip#2"]]
    block = format_recall_block(result)
    assert "-> then: User: Yes, book it for Saturday at 10." in block, block
    assert f"(memory {refs['trip#1']}, {refs['trip#2']})" in block, block


def test_without_the_join_the_booking_is_not_in_the_block(memory, monkeypatch):
    """Negative control: stored as typed, the follow-up holds one word of
    the question and is never shown on its own."""
    conn, _refs = memory
    monkeypatch.setattr(recall_mod, "_with_neighbours", lambda _c, hits, *_a, **_k: hits)
    block = format_recall_block(_recall(conn, FUNICULAR_Q))
    assert "Saturday" not in block, block


# ── The chat's own turns the model already reads ─────────────────────────


TEA_Q = "What is my favourite tea?"
TEA_TURN = "My favourite tea is jasmine green tea."


def test_a_turn_the_model_reads_in_the_chat_is_not_recalled_again(memory):
    conn, refs = memory
    result = _recall(conn, TEA_Q, session_id="bench-tea", in_context=frozenset({turn_key(TEA_TURN)}))
    assert refs["tea#1"] not in [h.id for h in result.episodes]


def test_a_turn_of_another_chat_is_recalled_whatever_this_one_reads(memory):
    conn, refs = memory
    result = _recall(conn, TEA_Q, session_id="bench-other", in_context=frozenset({turn_key(TEA_TURN)}))
    assert refs["tea#1"] in [h.id for h in result.episodes]


def test_not_told_what_the_chat_holds_recall_shows_it_again(memory):
    """Negative control: recall as it was, the chat's own turn boosted in."""
    conn, refs = memory
    result = _recall(conn, TEA_Q, session_id="bench-tea")
    assert refs["tea#1"] in [h.id for h in result.episodes]


# ── A request for every memory ───────────────────────────────────────────


LIST_Q = "List all my long runs."


def test_a_list_question_gets_every_matching_memory_and_a_note(memory):
    conn, refs = memory
    result = _recall(conn, LIST_Q)
    ids = {h.id for h in result.episodes}
    assert {refs[f"run{i}#1"] for i in range(8)} <= ids
    assert result.listing and len(result.episodes) <= LIST_LIMIT
    assert "call memory_list for all of them" in format_recall_block(result)


def test_read_as_an_ordinary_question_a_list_shows_five(memory, monkeypatch):
    """Negative control."""
    conn, _refs = memory
    monkeypatch.setattr("kazma_core.memory.question_shape.asks_for_all", lambda _q: False)
    result = _recall(conn, LIST_Q)
    assert len(result.episodes) == 5 and not result.listing
    assert "memory_list for all" not in format_recall_block(result)


# ── A one-digit number is a word ─────────────────────────────────────────


WEEK_Q = "How far did I ride in week 4?"


def test_week_4_is_not_week_14(memory):
    conn, refs = memory
    ids = [h.id for h in _recall(conn, WEEK_Q).episodes]
    assert ids and ids[0] == refs["wk4#1"], ids


def test_without_the_digit_week_14_comes_first(memory, monkeypatch):
    """Negative control: the reader that dropped one-digit numbers, so
    "week 4" was just "week" and the newer log won."""
    from kazma_core.memory import query_terms

    conn, refs = memory
    plain = query_terms.content_terms
    monkeypatch.setattr(query_terms, "content_terms", lambda text, *, digits=False: plain(text))
    ids = [h.id for h in _recall(conn, WEEK_Q).episodes]
    assert ids and ids[0] == refs["wk14#1"], ids


# ── Ids on every recalled conversation ───────────────────────────────────


@pytest.mark.parametrize("question", [RACE_Q, FUNICULAR_Q, TEA_Q, LIST_Q, "Where do I live?"])
def test_every_recalled_conversation_line_ends_with_its_id(memory, question):
    conn, _refs = memory
    result = _recall(conn, question)
    block = format_recall_block(result)
    for hit in result.episodes:
        assert f"(memory {hit.id}" in block, (hit.id, block)


# ── An incomplete recall says so ─────────────────────────────────────────


class _BrokenEmbedder:
    model = "broken"
    dim = 8

    def encode(self, _text: str):
        raise RuntimeError("the model is away")


def test_a_recall_without_meaning_search_says_it_was_incomplete(memory, monkeypatch):
    conn, _refs = memory
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _BrokenEmbedder())
    result = _recall(conn, TEA_Q)
    note = recall_incomplete_note(result)
    assert note.startswith("## Memory search was incomplete"), (result.degraded, note)
    assert "does NOT mean nothing is remembered" in note


def test_a_whole_recall_has_no_note(memory):
    conn, _refs = memory
    assert recall_incomplete_note(_recall(conn, TEA_Q)) == ""


def test_a_failed_recall_says_it_could_not_search_at_all():
    note = recall_incomplete_note(recall_mod._failed_recall("primary_search_failed"))
    assert "could not search at all" in note


def test_the_note_is_a_registered_turn_note():
    from kazma_core.turn_notes import note_kind

    note = recall_incomplete_note(recall_mod._failed_recall("x"))
    assert note_kind({"role": "system", "content": note}) == "memory_incomplete"


# ── The Postgres-primary path: a failed search is a failed recall ────────


class _FailingMirror:
    name = "postgres"
    available = True

    def search_episodes(self, query, *, tenant_id="default", limit=10, strict=False):
        raise ConnectionError("the mirror is away")

    def search_beliefs(self, query, *, tenant_id="default", limit=10, strict=False):
        raise ConnectionError("the mirror is away")

    def fetch_episodes(self, ids, *, tenant_id="default", strict=False):
        return []

    def fetch_beliefs(self, ids, *, tenant_id="default", strict=False):
        return []


def _primary(monkeypatch, mirror) -> None:
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.state_backend.is_state_primary", lambda cfg=None: True)
    monkeypatch.setattr("kazma_core.memory.state_backend.get_state_backend", lambda: mirror)
    monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend", lambda conn=None: None)


def test_a_primary_search_that_fails_is_a_failed_recall(monkeypatch):
    _primary(monkeypatch, _FailingMirror())
    result = recall("Where do I live?", tenant_id="t1")
    assert result.failed and result.degraded == "recall_failed:primary_search_failed", result
    assert "could not search at all" in recall_incomplete_note(result)


def test_with_the_search_as_it_was_a_failure_read_as_nothing_remembered(monkeypatch):
    """Negative control: the wrapper that swallowed the failure."""
    _primary(monkeypatch, _FailingMirror())

    def _swallow(query, *, tenant_id="default", limit=10, strict=False):
        try:
            return _FailingMirror().search_episodes(query)
        except Exception:
            return []

    monkeypatch.setattr("kazma_core.memory.state_backend.search_state_episodes", _swallow)
    monkeypatch.setattr("kazma_core.memory.state_backend.search_state_beliefs", _swallow)
    result = recall("Where do I live?", tenant_id="t1")
    assert not result.failed and result.empty, result


def test_the_postgres_backend_raises_only_when_strict(monkeypatch):
    from kazma_core.memory.state_backend import PostgresStateBackend

    be = PostgresStateBackend("postgresql://kazma@127.0.0.1:1/none")

    def _away():
        raise ConnectionError("away")

    monkeypatch.setattr(be, "_connect", _away)
    assert be.search_episodes("my dog") == [] and be.search_beliefs("my dog") == []
    with pytest.raises(ConnectionError):
        be.search_episodes("my dog", strict=True)
    with pytest.raises(ConnectionError):
        be.search_beliefs("my dog", strict=True)


# ── Class gates ──────────────────────────────────────────────────────────


def _recall_source() -> ast.Module:
    return ast.parse((ROOT / "kazma-core/kazma_core/memory/recall.py").read_text(encoding="utf-8"))


#: The one function allowed to read the question's words unfiltered: it
#: leaves the time words out itself (``test_search_words_leave_the_time_out``).
_FILTERS_ITSELF = {"_search_words"}
_WORD_READERS = {"content_terms", "search_terms"}


def _unfiltered_readers(tree: ast.Module) -> list[str]:
    """Each call of a question-word reader in *tree* that is neither inside
    ``_without_time_words(...)`` nor in a function that filters itself."""
    bad: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name in _FILTERS_ITSELF:
            continue
        wrapped = {id(inner) for outer in ast.walk(fn) if isinstance(outer, ast.Call)
                   and getattr(outer.func, "id", "") == "_without_time_words"
                   for inner in ast.walk(outer)}
        for call in ast.walk(fn):
            if (isinstance(call, ast.Call) and getattr(call.func, "id", "") in _WORD_READERS
                    and id(call) not in wrapped):
                bad.append(f"{fn.name}:{call.lineno}")
    return bad


def test_every_question_word_reader_in_recall_leaves_the_time_words_out():
    """A month's name is the time asked about, never a word the memory says:
    counted, it lowered the answer's word coverage, and searched, it matched
    turns that only named the month (part F)."""
    assert not _unfiltered_readers(_recall_source())


def test_the_gate_sees_a_reader_that_keeps_the_time_words():
    """Negative control: a planted reader, and the same one filtered."""
    planted = ast.parse("def _planted(q):\n    return content_terms(q)\n")
    assert _unfiltered_readers(planted) == ["_planted:2"]
    fixed = ast.parse("def _planted(q):\n    return _without_time_words(content_terms(q))\n")
    assert _unfiltered_readers(fixed) == []


def test_search_words_leave_the_time_out():
    ask = recall_mod._time_ask(RACE_Q, benchmark.BASE_TIME + 150 * DAY)
    token = recall_mod._ASK.set(ask)
    try:
        words = recall_mod._search_words(RACE_Q)
    finally:
        recall_mod._ASK.reset(token)
    assert "race" in words and "august" not in words, words
    assert "august" in recall_mod._search_words(RACE_Q)  # no time read: as before


# ── Every caller of recall says when it was not whole ────────────────────

_PACKAGES = ("kazma-core/kazma_core", "kazma-gateway/kazma_gateway", "kazma-ui/kazma_ui",
             "kazma-cli/kazma_cli", "kazma-tui/kazma_tui", "kazma-skills/kazma_skills")
_RECALL_MODULE = "kazma_core.memory.recall"
#: Entries that answer a person or the model and carry ``degraded``.
_STATUS_ENTRIES = {"recall", "search_with_status"}
#: Reading whether recall was whole: ``result.degraded`` / ``result.failed``,
#: ``recall_incomplete_note(result)``, or ``search_with_status``'s second
#: value bound to ``degraded``.
_STATUS_ATTRS = {"degraded", "failed"}

#: Callers of recall that need not read whether it was whole, with why.
_DECLARED_CALLERS = {
    "kazma-core/kazma_core/memory/benchmark.py::_ask_questions":
        "the benchmark scores a recall that failed as a miss, on every question",
    "kazma-core/kazma_core/memory/benchmark.py::run_speaker_benchmark":
        "the speakers benchmark scores a recall that failed as a miss",
    "kazma-core/kazma_core/memory/benchmark_kinds.py::_ask":
        "the benchmark's rows say a degraded recall, and its gate refuses one",
    "kazma-core/kazma_core/memory/eval_golden.py::run_golden_eval":
        "a recall that failed fails its case: nothing it expects is found",
}
#: Callers of ``recall.search`` (``[]`` on any failure): Kazma's own context,
#: never a person's view of their memory, each with why.
_DECLARED_SEARCH = {
    "kazma-core/kazma_core/compaction.py::_search":
        "past memories added to a compacted context: best-effort, the turn's own recall says when it was not whole",
    "kazma-core/kazma_core/skills/self_improvement.py::_analyze_success":
        "the refiner's past patterns: a lesson is written without them",
    "kazma-core/kazma_core/skills/self_improvement.py::_analyze_failure":
        "the refiner's past failure patterns: a lesson is written without them",
    "kazma-core/kazma_core/swarm/phonebook.py::memory_context":
        "a worker's memory context: a task runs as written without it (a WARNING when it cannot be read)",
}


def _recall_names(nodes) -> tuple[dict[str, str], set[str]]:
    """``local name -> entry`` for the status entries *nodes* import from
    recall, and the names they bind to ``recall.search``."""
    status: dict[str, str] = {}
    plain: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module == _RECALL_MODULE:
            for alias in node.names:
                if alias.name in _STATUS_ENTRIES:
                    status[alias.asname or alias.name] = alias.name
                elif alias.name == "search":
                    plain.add(alias.asname or alias.name)
    return status, plain


def _innermost_functions(tree: ast.Module):
    """Each function with the nodes of its own body (nested functions left to
    themselves)."""
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        own: list[ast.AST] = []
        stack = list(fn.body)
        while stack:
            node = stack.pop()
            own.append(node)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)) and node is not fn:
                continue
            stack.extend(ast.iter_child_nodes(node))
        yield fn, own


def _reads_status(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in _STATUS_ATTRS:
            return True
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "recall_incomplete_note":
            return True
        if isinstance(node, ast.Name) and node.id == "degraded" and isinstance(node.ctx, ast.Load):
            return True
    return False


def _recall_callers(tree: ast.Module, rel: str) -> tuple[list[str], list[str]]:
    """``(callers that do not read the status, functions importing search)``."""
    # A name is bound where it is imported: the module, the function, or a
    # function around it (``retrieve_memories`` imports ``search`` and its
    # nested ``_search`` calls it). A local variable named ``search`` is not.
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    owns = dict(_innermost_functions(tree))
    module_status, module_plain = _recall_names(tree.body)
    silent: list[str] = []
    search_users: list[str] = []
    for fn, own in owns.items():
        status, plain = dict(module_status), set(module_plain)
        scope: ast.AST | None = fn
        while scope is not None:
            if scope in owns:
                more_status, more_plain = _recall_names(owns[scope])
                status.update(more_status)
                plain |= more_plain
            scope = parents.get(scope)
        key = f"{rel}::{fn.name}"
        calls = [n for n in own if isinstance(n, ast.Call) and getattr(n.func, "id", "") in status]
        if calls and not _reads_status(fn):
            silent.append(key)
        calls_search = any(isinstance(n, ast.Call) and getattr(n.func, "id", "") in plain for n in own)
        # Handed on as a function (``to_thread(search, ...)``) is a call too.
        hands_search = any(isinstance(n, ast.Call) and any(
            isinstance(a, ast.Name) and a.id in plain for a in n.args) for n in own)
        if calls_search or hands_search:
            search_users.append(key)
    return silent, search_users


def _product_callers() -> tuple[set[str], set[str]]:
    silent: set[str] = set()
    users: set[str] = set()
    for pkg in _PACKAGES:
        for path in sorted((ROOT / pkg).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            if _RECALL_MODULE not in text:
                continue
            a, b = _recall_callers(ast.parse(text), rel)
            silent.update(a)
            users.update(b)
    return silent, users


def test_every_caller_of_recall_reads_whether_it_was_whole():
    """A recall that could not search by meaning, or at all, came back empty
    and read as "nothing remembered" (framework audit F9): the supervisor
    read only "empty or not", and the TUI said "No hits". Every caller reads
    ``degraded`` / ``failed`` (or hands the result to
    ``recall_incomplete_note``), or is declared with why."""
    silent, _users = _product_callers()
    assert silent <= set(_DECLARED_CALLERS), sorted(silent - set(_DECLARED_CALLERS))


def test_every_caller_of_search_is_kazmas_own_context():
    """``recall.search`` gives ``[]`` on any failure: for Kazma's own
    context only. A surface showing a person their memory uses
    ``search_with_status``."""
    _silent, users = _product_callers()
    assert users <= set(_DECLARED_SEARCH), sorted(users - set(_DECLARED_SEARCH))


def test_the_caller_declarations_excuse_something():
    silent, users = _product_callers()
    assert set(_DECLARED_CALLERS) <= silent, sorted(set(_DECLARED_CALLERS) - silent)
    assert set(_DECLARED_SEARCH) <= users, sorted(set(_DECLARED_SEARCH) - users)


def test_the_caller_gate_sees_a_caller_that_drops_the_status():
    """Negative control: the supervisor's recall as it was (empty or not),
    and the TUI's search as it was (``search``, "No hits")."""
    planted = ast.parse(
        "from kazma_core.memory.recall import recall\n"
        "def supervisor_node(q):\n"
        "    result = recall(q)\n"
        "    if not result.empty:\n"
        "        return result\n"
    )
    assert _recall_callers(planted, "x.py")[0] == ["x.py::supervisor_node"]
    tui = ast.parse(
        "def _run_graph_search(self, q):\n"
        "    from kazma_core.memory.recall import search\n"
        "    hits = search(q, limit=20)\n"
    )
    assert _recall_callers(tui, "kazma-tui/kazma_tui/memory_panel.py")[1] == [
        "kazma-tui/kazma_tui/memory_panel.py::_run_graph_search"]


# ── The surfaces that show a person their memory say an incomplete search ──


def _degraded_result(*_a, **_k):
    from kazma_core.memory.recall import RecallHit, RecallResult

    hit = RecallHit(id="e_tea", content="User: My favourite tea is jasmine.", score=0.9, kind="episode")
    return RecallResult(beliefs=[], episodes=[hit], degraded="meaning search is unavailable (the embedder failed)")


def test_search_with_status_says_what_kept_it_from_being_whole(monkeypatch):
    monkeypatch.setattr(recall_mod, "recall", _degraded_result)
    hits, degraded = recall_mod.search_with_status("favourite tea", tenant_id="t1")
    assert [h["id"] for h in hits] == ["e_tea"] and degraded.startswith("meaning search")
    # ``search`` (Kazma's own context) keeps its list-only shape.
    assert [h["id"] for h in recall_mod.search("favourite tea")] == ["e_tea"]


def _graph_search_route():
    from types import SimpleNamespace

    from fastapi import FastAPI
    from kazma_ui.routes_direct import memory as memory_routes

    app = FastAPI()
    memory_routes.register_memory_routes(SimpleNamespace(app=app))
    return next(r for r in app.routes if r.path == "/api/memory/graph/search").endpoint


def test_the_memory_search_route_says_an_incomplete_search(monkeypatch):
    monkeypatch.setattr(recall_mod, "recall", _degraded_result)
    answer = _graph_search_route()(q="favourite tea", limit=5)
    assert answer["degraded"] and [r["id"] for r in answer["results"]] == ["e_tea"]


def test_the_route_as_it_was_said_nothing(monkeypatch):
    """Negative control: the route over ``search``, the list alone."""
    monkeypatch.setattr(recall_mod, "recall", _degraded_result)
    monkeypatch.setattr(recall_mod, "search_with_status",
                        lambda *a, **k: (recall_mod.search(*a, **k), None))
    assert not _graph_search_route()(q="favourite tea", limit=5)["degraded"]


class _Widget:
    def __init__(self, value: str = "") -> None:
        self.value = value
        self.text = ""
        self.rows: list[tuple] = []

    def update(self, text: str) -> None:
        self.text = text

    def clear(self) -> None:
        self.rows.clear()

    def add_row(self, *row) -> None:
        self.rows.append(row)


def _tui_search(question: str) -> str:
    from types import SimpleNamespace

    from kazma_tui.memory_panel import MemoryTab

    widgets = {"#mem-graph-search": _Widget(question), "#mem-graph-results": _Widget(),
               "#mem-graph-msg": _Widget()}
    MemoryTab._run_graph_search(SimpleNamespace(query_one=lambda sel, _cls=None: widgets[sel]))
    return widgets["#mem-graph-msg"].text


def test_the_terminal_apps_search_says_an_incomplete_search(monkeypatch):
    monkeypatch.setattr(recall_mod, "recall", _degraded_result)
    assert "Search incomplete" in _tui_search("favourite tea")
    failed = lambda *_a, **_k: recall_mod._failed_recall("primary_search_failed")  # noqa: E731
    monkeypatch.setattr(recall_mod, "recall", failed)
    message = _tui_search("favourite tea")
    assert "No hits" in message and "Search incomplete" in message


def test_the_terminal_app_as_it_was_said_no_hits(monkeypatch):
    """Negative control: over ``search``, a failed recall read "No hits"."""
    monkeypatch.setattr(recall_mod, "recall", lambda *_a, **_k: recall_mod._failed_recall("primary_search_failed"))
    monkeypatch.setattr(recall_mod, "search_with_status",
                        lambda *a, **k: (recall_mod.search(*a, **k), None))
    message = _tui_search("favourite tea")
    assert "No hits" in message and "incomplete" not in message
