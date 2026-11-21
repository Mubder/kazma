"""Whether memory helped, and when it stops working (the memory framework
audit's F14, part H).

* **One recall record per turn**, through the real supervisor node: the ids
  shown, empty, degraded, failed and how long recall took -- ids and flags,
  never text, in the turn's space.
* **Used**: the answer holds a shown fact's value, or three of a shown
  turn's words the question did not already hold. **Corrected**: a shown
  fact the user replaced (or that was removed) within two days.
* **A week of failed extraction pages once** (the audit's acceptance), and
  its end once. Negative control: the dead-letter reporter without the
  extraction incident's hold pages once an hour, 168 times in the week.
* Recall that can only search by keyword, and recall that is slow, page
  once each and once when they end; an incident outlives a restart without
  a second page.
* The daily digest's memory line and its open incidents; memory health's
  week.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

DAY = 86400.0
T0 = 1_790_000_000.0


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import quality_watch
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr(quality_watch, "_SCHEMA_READY", set())
    db = sqlite3.connect(str(tmp_path / "memory_state.db"), check_same_thread=False)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    pages: list[tuple[str, str]] = []

    def page(key, title, detail="", **kw):
        pages.append((key, detail))
        return True

    monkeypatch.setattr("kazma_core.observability.ops_alerts.alert", page)
    yield SimpleNamespace(db=db, mp=monkeypatch, pages=pages, ops_path=tmp_path / "memory_ops.db")
    db.close()


def _rows(mem) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(mem.ops_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM memory_recall_records ORDER BY at").fetchall()
    finally:
        conn.close()


def _fact(db, predicate: str, value: str, *, source: str = "user_stated", tenant: str = "default",
          at: float = T0) -> str:
    from kazma_core.memory.belief_mutation import mutate_belief

    out = mutate_belief(db, "user", predicate, value, extraction_method=source, tenant_id=tenant,
                        now=at, private=True)
    db.commit()
    return str(out.get("belief_id") or out.get("id"))


def _hit(hid: str):
    from kazma_core.memory.recall import RecallHit

    return RecallHit(id=hid, content="", score=1.0)


# ── the record ─────────────────────────────────────────────────────────────


class _Response:
    def __init__(self, text: str) -> None:
        self.content = text
        self.tool_calls: list = []
        self.model = "fake-model"
        self.usage = {"total_tokens": 10, "prompt_tokens": 5, "completion_tokens": 5}
        self.cost_usd = 0.0


class _LLM:
    async def chat(self, messages=None, tools=None, model=None, **kwargs):
        return _Response("Done.")


class _Fake:
    compactor = type("C", (), {"retrieve_memories": staticmethod(lambda *a, **k: [])})()

    def should_halt(self) -> bool:
        return False

    def record_cost(self, cost: float) -> None:
        pass

    def trace_llm_call(self, **kwargs) -> None:
        pass

    async def check_and_enforce(self, state):
        return state


def _turn(mem, answer, *, thread: str = "th-quality", question: str = "where do I live these days?") -> None:
    from kazma_core.agent.graph_builder import supervisor_node

    mem.mp.setattr("kazma_core.memory.recall.recall", answer)
    fake = _Fake()
    asyncio.run(supervisor_node(
        {"messages": [{"role": "user", "content": question}], "iteration": 0, "max_iterations": 5,
         "tenant_id": "default", "thread_id": thread},
        llm=_LLM(), system_prompt="You are Kazma.", tool_definitions=[], tool_executor=None,
        cost_breaker=fake, authority=fake, tracer=fake,
    ))


def test_a_turn_leaves_one_recall_record_of_ids_and_flags(mem):
    from kazma_core.memory.recall import RecallResult

    porto = _fact(mem.db, "lives_in", "Porto")
    _turn(mem, lambda q, **kw: RecallResult([_hit(porto)], [_hit("e_turn1")]))
    rows = _rows(mem)
    assert len(rows) == 1
    row = rows[0]
    assert json.loads(row["shown_json"]) == {"episodes": ["e_turn1"], "beliefs": [porto], "summaries": []}
    assert (row["shown_count"], row["empty"], row["degraded"], row["failed"]) == (2, 0, "", 0)
    assert row["tenant_id"] == "default" and row["latency_ms"] >= 0
    assert "Porto" not in json.dumps(dict(row)), "a record holds ids, never a memory's text"


def test_a_degraded_and_a_failed_recall_are_recorded(mem):
    from kazma_core.memory.recall import RecallResult

    _turn(mem, lambda q, **kw: RecallResult([], [], degraded="keyword_only:embedder_unavailable"),
          thread="th-deg")

    def broken(q, **kw):
        raise sqlite3.OperationalError("database is locked")

    _turn(mem, broken, thread="th-fail")
    by = {r["degraded"]: r for r in _rows(mem)}
    assert by["keyword_only:embedder_unavailable"]["empty"] == 1
    assert by["keyword_only:embedder_unavailable"]["failed"] == 0
    assert by["recall_failed:error"]["failed"] == 1


# ── used, corrected, growth ────────────────────────────────────────────────


def _record(key: str, *, beliefs=(), episodes=(), tenant: str = "default", at: float = T0, **kw) -> None:
    from kazma_core.memory.recall import RecallResult
    from kazma_core.memory.recall_records import record_recall

    record_recall(key, tenant_id=tenant, result=RecallResult([_hit(b) for b in beliefs],
                                                             [_hit(e) for e in episodes]),
                  latency_ms=kw.get("latency_ms", 120.0), now=at)


def _episode(db, eid: str, question: str, answer: str, *, tenant: str = "default") -> None:
    db.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
               "tier, created_at, last_accessed) VALUES (?, ?, 's', 1, ?, ?, 'episodic', ?, ?)",
               (eid, tenant, question, answer, T0, T0))
    db.commit()


def test_the_answer_used_a_shown_fact_and_a_shown_turn(mem):
    from kazma_core.memory.recall_records import judge_used

    porto = _fact(mem.db, "lives_in", "Porto")
    _record("k-fact", beliefs=[porto])
    assert judge_used("k-fact", answer="You live in Porto, by the river.", question="where do I live?") is True
    _record("k-none", beliefs=[porto])
    assert judge_used("k-none", answer="I could not find that.", question="where do I live?") is False
    _episode(mem.db, "e_trip", "plan the Lisbon trip", "Book the riverside hotel, the tram pass and the fado show.")
    _record("k-turn", episodes=["e_trip"])
    assert judge_used("k-turn", answer="You chose the riverside hotel, a tram pass and a fado show.",
                      question="what did we plan for Lisbon?") is True
    assert [r["used"] for r in _rows(mem)] == [1, 0, 1]


def test_echoing_the_question_is_not_using_the_memory(mem):
    from kazma_core.memory.recall_records import judge_used

    _episode(mem.db, "e_budget", "the Lisbon trip budget", "Flights 420, hotel 610, food 300.")
    _record("k-echo", episodes=["e_budget"])
    assert judge_used("k-echo", answer="Here is the Lisbon trip budget you asked about.",
                      question="what was the Lisbon trip budget?") is False


def test_nothing_shown_is_not_judged(mem):
    from kazma_core.memory.recall_records import judge_used

    _record("k-empty")
    assert judge_used("k-empty", answer="Anything at all.") is None
    assert _rows(mem)[0]["used"] is None


def test_a_fact_the_user_replaced_soon_after_is_corrected(mem):
    from kazma_core.memory.recall_records import signals

    porto = _fact(mem.db, "lives_in", "Porto", at=T0 - 100)
    _record("k1", beliefs=[porto], at=T0)
    _fact(mem.db, "lives_in", "Lisbon", source="user_stated", at=T0 + 3600)
    out = signals(since=T0 - 10, until=T0 + 10 * DAY)
    assert out["corrected"] == 1
    assert out["growth"]["facts_replaced"] == 1


def test_a_reading_of_the_model_replacing_it_is_not_a_correction(mem):
    from kazma_core.memory.recall_records import signals

    gym = _fact(mem.db, "favorite_sport", "squash", source="llm_inferred", at=T0 - 100)
    _record("k1", beliefs=[gym], at=T0)
    _fact(mem.db, "favorite_sport", "tennis", source="llm_inferred", at=T0 + 3600)
    assert signals(since=T0 - 10, until=T0 + 10 * DAY)["corrected"] == 0


def test_a_correction_after_two_days_is_not_counted(mem):
    from kazma_core.memory.recall_records import signals

    porto = _fact(mem.db, "lives_in", "Porto", at=T0 - 100)
    _record("k1", beliefs=[porto], at=T0)
    _fact(mem.db, "lives_in", "Lisbon", at=T0 + 3 * DAY)
    assert signals(since=T0 - 10, until=T0 + 10 * DAY)["corrected"] == 0


def test_the_signals_keep_to_a_space(mem):
    from kazma_core.memory.recall_records import signals

    _record("ka1", tenant="space_a", at=T0)
    _record("ka2", tenant="space_a", at=T0 + 1)
    _record("kb1", tenant="space_b", at=T0 + 2)
    assert signals(since=T0 - 1, until=T0 + 10, tenant_id="space_a")["turns"] == 2
    assert signals(since=T0 - 1, until=T0 + 10, tenant_id="space_b")["turns"] == 1
    assert signals(since=T0 - 1, until=T0 + 10)["turns"] == 3


# ── a week of failed extraction ────────────────────────────────────────────


def _failing_week(mem, *, hold: bool = True) -> list[tuple[str, str]]:
    """Seven days of an outage: a deep pass fails every 15 minutes, and the
    queue gives one up every hour (its sixth attempt)."""
    from kazma_core.memory import quality_watch
    from kazma_core.memory.task_queue import _report_dead_letters

    if not hold:
        mem.mp.setattr(quality_watch, "incident_open", lambda name: False)
    t = T0
    end = T0 + 7 * DAY
    step = 0
    while t < end:
        quality_watch.deep_pass_outcome(False, tenant_id="default", now=t)
        step += 1
        if step % 4 == 0:
            _report_dead_letters(["micro_consolidation"], "The model is unavailable")
        t += 900.0
    return list(mem.pages)


def test_a_week_of_failed_extraction_pages_once(mem):
    pages = _failing_week(mem)
    assert [k for k, _ in pages] == ["memory.extraction_failing"], pages
    from kazma_core.memory import quality_watch

    quality_watch.deep_pass_outcome(True, tenant_id="default", now=T0 + 7 * DAY + 60)
    quality_watch.deep_pass_outcome(True, tenant_id="default", now=T0 + 7 * DAY + 120)
    keys = [k for k, _ in mem.pages]
    assert keys == ["memory.extraction_failing", "memory.extraction_back"], keys
    assert "7.0 days" in mem.pages[-1][1]


def test_control_without_the_hold_the_week_pages_every_hour(mem):
    pages = _failing_week(mem, hold=False)
    assert sum(1 for k, _ in pages if k == "memory.dead_letter") == 168


def test_a_short_blip_opens_nothing(mem):
    from kazma_core.memory import quality_watch

    for i in range(5):
        quality_watch.deep_pass_outcome(False, tenant_id="default", now=T0 + i * 60)
    quality_watch.deep_pass_outcome(True, tenant_id="default", now=T0 + 400)
    for i in range(5):
        quality_watch.deep_pass_outcome(False, tenant_id="default", now=T0 + 500 + i * 60)
    assert mem.pages == []


def test_an_open_incident_outlives_a_restart_without_a_second_page(mem):
    from kazma_core.memory import quality_watch

    for i in range(4):
        quality_watch.deep_pass_outcome(False, tenant_id="default", now=T0 + i * 300)
    assert [k for k, _ in mem.pages] == ["memory.extraction_failing"]
    mem.mp.setattr(quality_watch, "_SCHEMA_READY", set())  # a new process
    for i in range(10):
        quality_watch.deep_pass_outcome(False, tenant_id="default", now=T0 + DAY + i * 300)
    assert [k for k, _ in mem.pages] == ["memory.extraction_failing"]
    assert quality_watch.incident_open("extraction")


def test_a_poison_deep_pass_still_pages_its_dead_letter(mem):
    """One deep pass giving up while the others answer is no outage: its
    dead letter pages as before."""
    from kazma_core.memory import quality_watch
    from kazma_core.memory.task_queue import _report_dead_letters

    quality_watch.deep_pass_outcome(True, tenant_id="default", now=T0)
    _report_dead_letters(["micro_consolidation"], "unparseable")
    assert [k for k, _ in mem.pages] == ["memory.dead_letter"]


def test_the_deep_pass_handler_records_each_outcome(mem):
    from kazma_core.memory import worker_bootstrap as wb

    row = {"user_text": "I moved to Porto", "assistant_text": "Noted.", "session_id": "s", "turn_number": 1,
           "tenant_id": "default", "created_at": T0, "said_by": None}

    def prepare(eid):
        return {"primary": sqlite3.connect(":memory:", check_same_thread=False),
                "ops": sqlite3.connect(":memory:", check_same_thread=False), "row": row,
                "use_llm": True}

    mem.mp.setattr(wb, "_micro_consolidation_prepare", prepare)
    mem.mp.setattr(wb, "_mark_deep_pass", lambda *a, **k: None)
    answers = iter([([], {"model_unavailable": True}), ([], {})])

    async def extract(*a, **k):
        return next(answers)

    mem.mp.setattr("kazma_core.memory.belief_extractor.extract_beliefs_for_turn", extract)
    assert asyncio.run(wb._handle_micro_consolidation({"episode_id": "e1"})) is False
    assert asyncio.run(wb._handle_micro_consolidation({"episode_id": "e1"})) is True
    conn = sqlite3.connect(str(mem.ops_path))
    try:
        assert [r[0] for r in conn.execute("SELECT ok FROM memory_deep_passes ORDER BY rowid")] == [0, 1]
    finally:
        conn.close()


# ── recall incidents ───────────────────────────────────────────────────────


def _recall(i: int, *, degraded: str = "", latency: float = 200.0, at: float | None = None) -> None:
    from kazma_core.memory.recall import RecallResult
    from kazma_core.memory.recall_records import record_recall

    record_recall(f"r{i}", tenant_id="default", result=RecallResult([], [], degraded=degraded or None),
                  latency_ms=latency, now=T0 + i * 60 if at is None else at)


def test_keyword_only_recall_pages_once_and_its_end_once(mem):
    for i in range(30):
        _recall(i, degraded="keyword_only:embedder_unavailable")
    assert [k for k, _ in mem.pages] == ["memory.recall_degraded"]
    for i in range(30, 36):
        _recall(i)
    assert [k for k, _ in mem.pages] == ["memory.recall_degraded", "memory.recall_back"]


def test_slow_recall_pages_once_and_its_end_once(mem):
    for i in range(40):
        _recall(i, latency=12_000.0)
    assert [k for k, _ in mem.pages] == ["memory.recall_slow"]
    for i in range(40, 70):
        _recall(i, latency=300.0)
    assert [k for k, _ in mem.pages] == ["memory.recall_slow", "memory.recall_speed_back"]


def test_a_few_bad_recalls_open_nothing(mem):
    for i in range(6):
        _recall(i, degraded="keyword_only:embedder_unavailable", latency=20_000.0)
    assert mem.pages == []


# ── where it is read ───────────────────────────────────────────────────────


def test_the_digest_says_memorys_day_and_its_open_incident(mem):
    import time

    from kazma_core.memory import quality_watch
    from kazma_core.observability.daily_digest import _memory_incident_lines, _memory_line

    now = time.time()
    porto = _fact(mem.db, "lives_in", "Porto")
    _record("d1", beliefs=[porto], at=now - 60)
    _record("d2", at=now - 30)
    line = _memory_line(now - DAY)
    assert line.startswith("Memory: 2 recalls (1 empty, 0 degraded, 0 failed")
    for i in range(4):
        quality_watch.deep_pass_outcome(False, tenant_id="default", now=now - 3000 + i * 300)
    assert "deep passes 0 done, 4 failed" in _memory_line(now - DAY)
    lines = _memory_incident_lines()
    assert len(lines) == 1 and "Memory cannot read facts from conversations" in lines[0]


def test_memory_health_holds_the_week_and_is_degraded_by_an_incident(mem):
    from kazma_core.memory import quality_watch
    from kazma_core.memory.v2_health import build_v2_health

    for i in range(4):
        quality_watch.deep_pass_outcome(False, tenant_id="default", now=T0 + i * 300)
    out = build_v2_health()
    assert out["quality"]["available"] is True
    assert [i["name"] for i in out["incidents"]] == ["extraction"]
    assert out["status"] == "DEGRADED" and out["status_detail"] == "memory_incident"
    member = build_v2_health("space_x", install_details=False)
    assert "incidents" not in member and "quality" in member
