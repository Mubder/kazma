"""The independent review of dated event lines (the memory plan's W4,
2026-11-24): what an event is, whose day it carries, and when it happened.

Each test fails on 81b7a2529, the commit reviewed (the controls and the cases
that must stay aside). Through the product's own apply step
(``belief_extractor._apply_beliefs_to_v2``), recall and block, ``memory_list``,
the "What Kazma knows about you" reader and forget, on real databases; English
and Arabic.

* **An event is an event** (H1): "I called my mom last Saturday" became the
  user's name, "moved_to Porto" their home, and an event the reply labelled
  single-valued closed the last one.
* **An event told again keeps its day** (H2): told in other words without the
  day, the retelling closed the dated entry and the day was gone; a retelling
  that named another event closed it ("bananas" by "fresh vegetables").
* **Two days are two occasions, and a wrong day can be corrected** (H3).
* **A day has a source** (M1, M2): forgetting the turn that gave it takes it
  back, and a day the model read never reads as the user's.
* **An event is its day** (M3), **a day to come is planned** (M4), **an event
  is the user's to tell** (M6).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from kazma_core.memory import belief_extractor, belief_mutation
from kazma_core.memory import recall as recall_mod
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

KUWAIT = ZoneInfo("Asia/Kuwait")
#: A Sunday.
TOLD = "2026-05-31"


def at(day: str) -> float:
    return datetime.fromisoformat(f"{day}T10:00:00+00:00").timestamp()


def event(predicate: str, obj: str, on: str | None, **more) -> dict:
    return {"subject": "user", "predicate": predicate, "predicate_type": "set", "object": obj, "confidence": 0.9,
            **({"on": on} if on else {}), **more}


def has_day(meta: dict) -> bool:
    return bool(meta.get("started") or meta.get("planned"))


class Mem:
    """One person's memory, told turn by turn as the deep pass replied."""

    def __init__(self, conn: sqlite3.Connection, ops: sqlite3.Connection) -> None:
        self.db, self.ops = conn, ops

    def tell(self, beliefs: list[dict], said: str, *, turn: int = 1, on: str = TOLD, held=None, stats=None, now=...):
        return belief_extractor._apply_beliefs_to_v2(
            beliefs, self.db, self.ops, session_id="chat-1", turn=turn, tenant_id="default",
            now=at(on) if now is ... else now, user_text=said, held=held, stats=stats, private=True)

    def says(self, said: str, *, turn: int = 1, on: str = TOLD) -> None:
        """The user's own words, read by the quick pass."""
        belief_extractor.extract_and_apply_beliefs_sync(
            self.db, self.ops, said, session_id="chat-1", turn=turn, tenant_id="default", now=at(on), private=True)

    def held(self) -> tuple[list[dict], dict[str, str]]:
        """The numbered facts the deep pass is shown now, and each value's number."""
        rows = belief_extractor.held_facts(self.db, tenant_id="default", text="")
        return rows, {r["object"]: r["ref"] for r in rows}

    def _current(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM beliefs WHERE predicate != 'related_to' AND valid_until IS NULL AND invalidated_at IS NULL "
            "AND object != '' ORDER BY rowid").fetchall()

    def facts(self, *cols: str) -> list[tuple]:
        return [tuple(r[c] for c in (cols or ("predicate", "object"))) for r in self._current()]

    def days(self) -> list[tuple[str, dict]]:
        return [(r["object"], json.loads(r["metadata_json"] or "{}")) for r in self._current()]

    def block(self, question: str, *, now: str = "2026-06-15") -> str:
        result = recall_mod.recall(question, conn=self.db, tenant_id="default", local_only=True, now=at(now))
        text = recall_mod.format_recall_block(result)
        return text.split("## Known Facts")[1].split("\n##")[0] if "## Known Facts" in text else ""

    def changes(self) -> list[tuple]:
        return [tuple(r) for r in self.db.execute("SELECT kind, new_value FROM memory_changes")]

    def episode(self, turn: int, said: str) -> str:
        from kazma_core.memory.dual_write import episode_row

        row = episode_row(session_id="chat-1", turn_number=turn, user_text=said, assistant_text="Noted.",
                          tenant_id="default", source="test", created_at=at(TOLD) + turn)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(episodes)")}
        cols = {k: v for k, v in row.items() if k in have}
        self.db.execute(f"INSERT INTO episodes ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                        list(cols.values()))
        return str(row["id"])

    def forget(self, episode_id: str) -> None:
        from kazma_core.memory.forget import forget_episode

        assert forget_episode(episode_id, tenant_id="default", conn=self.db).get("ok"), episode_id


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    monkeypatch.setattr(recall_mod, "_memory_zone", lambda: UTC)
    monkeypatch.setattr(belief_extractor, "_turn_zone", lambda: KUWAIT)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"), isolation_level=None)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"), isolation_level=None)
    ensure_ops_schema(ops)
    yield Mem(conn, ops)
    conn.close()
    ops.close()


# ── H1: an event is an event ─────────────────────────────────────────────


@pytest.mark.parametrize(("predicate", "obj", "said"), [
    ("called", "my mom", "I called my mom last Saturday."),
    ("called", "أمي", "اتصلت على أمي السبت الماضي."),
    ("moved_to", "Porto", "I moved to Porto last Saturday."),
    ("moved_to", "بورتو", "انتقلت إلى بورتو السبت الماضي."),
    ("named", "Luna", "I named the kitten Luna last Saturday."),
])
def test_an_event_is_never_the_users_name_or_home(mem, predicate, obj, said):
    mem.tell([event(predicate, obj, "2026-05-30")], said)
    assert mem.facts("predicate", "predicate_type", "object") == [(predicate, "set", obj)]
    # ... and nothing is put to the user about it, a first reading included.
    assert mem.changes() == []


def test_an_event_leaves_the_users_own_home_and_name_alone(mem):
    mem.says("My name is Sam. I live in Lisbon.", on="2026-05-01")
    mem.tell([event("called", "my mom", "2026-05-30"), event("moved_to", "Porto", "2026-05-30")],
             "I called my mom on Saturday; I moved to Porto that day.", turn=2)
    assert {("name_is", "Sam"), ("lives_in", "Lisbon"), ("called", "my mom"), ("moved_to", "Porto")} <= set(mem.facts())
    assert mem.changes() == []


@pytest.mark.parametrize("label", ["functional", "state"])
def test_an_event_labelled_single_valued_closes_nothing(mem, label):
    mem.tell([event("bought", "a road bike", "2026-05-23", predicate_type=label)], "I bought a road bike last week.")
    mem.tell([event("bought", "a helmet", "2026-05-30", predicate_type=label)], "I bought a helmet yesterday.", turn=2)
    assert mem.facts("predicate_type", "object") == [("set", "a road bike"), ("set", "a helmet")]


def test_an_undated_event_the_model_labelled_single_is_a_list_entry_with_its_day(mem):
    mem.tell([event("finished", "Dune", None, predicate_type="functional")], "I finished Dune.")
    mem.tell([event("finished", "Dune", "2026-05-30")], "I finished Dune yesterday.", turn=2)
    assert mem.facts("predicate_type", "object") == [("set", "Dune")]
    assert "[on 30 May 2026] user finished Dune" in mem.block("finished Dune")
    mem.tell([event("finished", "Emma", "2026-05-31")], "I finished Emma today.", turn=3)
    assert len(mem.facts()) == 2


@pytest.mark.parametrize(("predicate", "stored"), [("lives_in", "lives_in"), ("home_city", "lives_in"),
                                                   ("works_at", "works_at"), ("favorite_color", "favorite_color")])
def test_a_day_on_a_single_value_is_when_it_began(mem, predicate, stored):
    mem.tell([event(predicate, "Porto", "2026-05-30", predicate_type="functional")], "Porto since Saturday.")
    assert mem.facts("predicate", "predicate_type") == [(stored, "functional")]
    meta = mem.days()[0][1]
    assert (meta.get("started"), meta.get("happened")) == ("2026-05-30", None)
    assert "[since 30 May 2026]" in mem.block("Porto")


def test_a_move_that_replaces_the_home_is_its_new_value_with_its_start(mem):
    mem.tell([{"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": "Lisbon",
               "confidence": 0.9}], "I'm in Lisbon.", on="2026-05-01")
    held, refs = mem.held()
    mem.tell([event("moved_to", "Porto", "2026-05-30", replaces=refs["Lisbon"])], "I moved to Porto on Saturday.",
             turn=2, held=held)
    assert mem.facts("predicate", "predicate_type", "object") == [("lives_in", "functional", "Porto")]
    meta = mem.days()[0][1]
    assert (meta.get("started"), meta.get("happened")) == ("2026-05-30", None)


# ── H2: an event told again keeps its day ────────────────────────────────


@pytest.mark.parametrize(("first", "again", "said"), [
    ("a pottery exhibition at the craft centre", "the pottery exhibition with Dana",
     "I loved the pottery exhibition with Dana."),
    ("معرض فخار في مركز الحرف", "معرض الفخار مع دانة", "عجبني معرض الفخار مع دانة."),
])
@pytest.mark.parametrize("vague", [None, "2026-05"])
def test_an_event_told_again_in_other_words_keeps_its_day(mem, first, again, said, vague):
    mem.tell([event("attended", first, "2026-05-30")], f"I attended {first} yesterday.")
    held, refs = mem.held()
    mem.tell([event("attended", again, vague, replaces=refs[first])], said, turn=2, on="2026-06-02", held=held)
    assert mem.facts() == [("attended", again)]
    meta = mem.days()[0][1]
    assert (meta.get("started"), meta.get("happened")) == ("2026-05-30", True)
    # ... and whose day it is: the first telling's.
    assert [(meta.get("day_source") or {}).get(k) for k in ("session", "turn")] == ["chat-1", 1]
    assert f"[on 30 May 2026] user attended {again}" in mem.block(again)


@pytest.mark.parametrize(("first", "other"), [("bananas", "fresh vegetables"), ("موز", "خضار طازجة")])
@pytest.mark.parametrize("day", ["2026-05-30", None])
def test_another_event_never_closes_this_one(mem, first, other, day):
    """``replaces`` naming another event: both stay, each with its own day."""
    mem.tell([event("bought", first, "2026-05-23")], f"I bought {first} on the 23rd.")
    held, refs = mem.held()
    mem.tell([event("bought", other, day, replaces=refs[first])], f"I bought {other}.", turn=2, held=held)
    assert mem.facts() == [("bought", first), ("bought", other)]
    assert [m.get("started") for _v, m in mem.days()] == ["2026-05-23", day]


def test_the_same_event_with_a_corrected_day_is_one_entry(mem):
    """Kept as it was: a retelling that is the same event, with its day put right."""
    mem.tell([event("attended", "a charity spin class", "2026-05-24")], "I attended a charity spin class.")
    held, refs = mem.held()
    mem.tell([event("attended", "a charity spin class at the gym", "2026-02-14",
                    replaces=refs["a charity spin class"])],
             "The charity spin class at the gym was on Valentine's Day.", turn=2, held=held)
    assert mem.facts() == [("attended", "a charity spin class at the gym")]
    assert mem.days()[0][1]["started"] == "2026-02-14"


# ── H3: two days are two occasions, and a wrong day can be corrected ─────


@pytest.mark.parametrize(("obj", "said", "question"), [
    ("the dentist", "I visited the dentist yesterday.", "When was the last time I visited the dentist?"),
    ("طبيب الأسنان", "زرت طبيب الأسنان أمس.", "متى زرت طبيب الأسنان؟"),
])
def test_the_same_words_on_a_later_day_are_another_occasion(mem, obj, said, question):
    mem.tell([event("visited", obj, "2026-05-30")], said)
    mem.tell([event("visited", obj, "2026-11-10")], said, turn=9, on="2026-11-11")
    assert [m.get("started") for _o, m in mem.days()] == ["2026-05-30", "2026-11-10"]
    lines = [line for line in mem.block(question, now="2026-12-01").splitlines() if obj in line]
    assert ["10 Nov 2026" in line for line in lines] == [True, False] and "30 May 2026" in lines[1]
    # Told again, each is the entry it was.
    mem.tell([event("visited", obj, "2026-11-10")], said, turn=10, on="2026-11-12")
    assert len(mem.facts()) == 2


def test_the_same_event_however_it_is_spelled(mem):
    """The value is kept in the user's spelling, so a sentence that opens with it was another entry."""
    mem.tell([event("attended", "the pottery exhibition", None)], "I went to the pottery exhibition.")
    mem.tell([event("attended", "the pottery exhibition", "2026-05-30")], "The pottery exhibition was Saturday.",
             turn=2)
    assert mem.facts() == [("attended", "the pottery exhibition")]
    assert mem.days()[0][1].get("started") == "2026-05-30"


def test_an_undated_entry_takes_only_a_day_it_can_have_happened_on(mem):
    mem.tell([event("visited", "the dentist", None)], "I visited the dentist.")
    mem.tell([event("visited", "the dentist", "2026-11-10")], "I visited the dentist yesterday.", turn=9,
             on="2026-11-11")
    assert [m.get("started") for _o, m in mem.days()] == [None, "2026-11-10"]


@pytest.mark.parametrize(("obj", "said"), [("the dentist", "It was the 29th I visited the dentist, not the 30th."),
                                           ("طبيب الأسنان", "زرت طبيب الأسنان يوم ٢٩ مو ٣٠.")])
def test_the_same_words_with_another_day_correct_it(mem, obj, said):
    mem.tell([event("visited", obj, "2026-05-30")], said)
    held, refs = mem.held()
    mem.tell([event("visited", obj, "2026-05-29", replaces=refs[obj])], said, turn=2, on="2026-06-01", held=held)
    ((value, meta),) = mem.days()
    assert (value, meta.get("started"), meta.get("happened")) == (obj, "2026-05-29", True)
    assert "[on 29 May 2026]" in mem.block(obj)


def test_a_day_after_the_first_telling_is_no_correction(mem):
    """Told in May, it did not happen in November: another occasion, even when
    the reply names the first entry."""
    mem.tell([event("visited", "the dentist", "2026-05-30")], "I visited the dentist yesterday.")
    held, refs = mem.held()
    mem.tell([event("visited", "the dentist", "2026-11-10", replaces=refs["the dentist"])],
             "I visited the dentist yesterday.", turn=9, on="2026-11-11", held=held)
    assert [m.get("started") for _o, m in mem.days()] == ["2026-05-30", "2026-11-10"]


def test_the_nightly_merge_keeps_both_occasions(mem):
    """The duplicate and same-words merges read the day as part of the event."""
    from kazma_core.memory.global_reconsolidation import _merge_duplicate_beliefs
    from kazma_core.memory.predicates import merge_same_word_predicates

    for n, (pred, day) in enumerate([("visited", "2026-05-30"), ("visited", "2026-11-10"),
                                     ("the_visited", "2026-11-20")]):
        mem.db.execute(
            "INSERT INTO beliefs (id, subject, predicate, predicate_type, object, confidence, structural_importance, valid_from, "
            "ingested_at, tenant_id, metadata_json, extraction_method) VALUES "
            "(?, 'user', ?, 'set', 'the dentist', 0.9, 1, ?, ?, 'default', ?, 'llm_inferred')",
            (f"b{n}", pred, at(day), at(day), json.dumps({"started": day, "happened": True})))
    _merge_duplicate_beliefs(mem.db, tenant_id="default", max_merges=50)
    merge_same_word_predicates(mem.db, tenant_id="default")
    assert len(mem.facts()) == 3


# ── M1: a day has a source, and goes with it ─────────────────────────────


def test_forgetting_the_turn_that_gave_the_day_takes_it_back(mem):
    mem.tell([event("attended", "the pottery exhibition", None)], "I went to the pottery exhibition.")
    said = "I went to the pottery exhibition last Saturday."
    mem.tell([event("attended", "the pottery exhibition", "2026-05-30")], said, turn=2)
    eid = mem.episode(2, said)
    assert mem.days()[0][1]["started"] == "2026-05-30"
    mem.forget(eid)
    ((value, meta),) = mem.days()
    assert value == "the pottery exhibition" and not has_day(meta) and "2026-05-30" not in json.dumps(meta)


def test_a_single_values_start_goes_with_the_turn_that_gave_it(mem):
    belief = {"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": "Rotterdam",
              "confidence": 0.9}
    mem.tell([belief], "I'm in Rotterdam.")
    mem.tell([{**belief, "since": "2026-04"}], "I'm in Rotterdam since April.", turn=2)
    eid = mem.episode(2, "I'm in Rotterdam since April.")
    assert mem.days()[0][1]["started"] == "2026-04"
    mem.forget(eid)
    assert [(v, has_day(m)) for v, m in mem.days()] == [("Rotterdam", False)]


def test_a_day_given_after_the_forget_landed_is_taken_back(mem):
    """The ledger pass: a fact pass that read its turn before the forget landed."""
    from kazma_core.memory.forget import enforce_ledger

    mem.tell([event("attended", "the pottery exhibition", None)], "I went to the pottery exhibition.")
    said = "I went to the pottery exhibition last Saturday."
    mem.forget(mem.episode(2, said))
    mem.tell([event("attended", "the pottery exhibition", "2026-05-30")], said, turn=2)
    assert mem.days()[0][1].get("started") == "2026-05-30"
    enforce_ledger(mem.db)
    assert [(v, has_day(m)) for v, m in mem.days()] == [("the pottery exhibition", False)]


def test_a_day_taken_over_goes_with_the_turn_it_came_from(mem):
    mem.tell([event("attended", "a pottery exhibition", "2026-05-30")], "I attended a pottery exhibition yesterday.")
    held, refs = mem.held()
    mem.tell([event("attended", "the pottery exhibition with Dana", None, replaces=refs["a pottery exhibition"])],
             "I attended the pottery exhibition with Dana.", turn=2, held=held)
    eid = mem.episode(1, "I attended a pottery exhibition yesterday.")
    assert mem.days()[0][1]["started"] == "2026-05-30"
    mem.forget(eid)
    assert [(v, has_day(m)) for v, m in mem.days()] == [("the pottery exhibition with Dana", False)]


def test_a_forgotten_correction_gives_the_earlier_day_back(mem):
    mem.tell([event("visited", "the dentist", "2026-05-30")], "I visited the dentist yesterday.")
    held, refs = mem.held()
    mem.tell([event("visited", "the dentist", "2026-05-29", replaces=refs["the dentist"])],
             "I visited the dentist on the 29th.", turn=2, held=held)
    eid = mem.episode(2, "I visited the dentist on the 29th.")
    assert mem.days()[0][1]["started"] == "2026-05-29"
    mem.forget(eid)
    meta = mem.days()[0][1]
    assert (meta.get("started"), meta.get("happened")) == ("2026-05-30", True) and "2026-05-29" not in json.dumps(meta)


def test_forgetting_the_chat_takes_its_days_and_an_archived_copys(mem):
    from kazma_core.memory.forget import forget_chat

    mem.tell([event("attended", "the pottery exhibition", None)], "I went to the pottery exhibition.")
    mem.db.execute("UPDATE beliefs SET source_session = 'another-chat'")
    mem.tell([event("attended", "the pottery exhibition", "2026-05-30")], "The pottery exhibition was Saturday.",
             turn=2)
    row = dict(mem.db.execute("SELECT * FROM beliefs").fetchone())
    row.pop("embedding", None)
    cols = [r[1] for r in mem.db.execute("PRAGMA table_info(beliefs_archive)")]
    values = {"id": "a1", "tenant_id": "default", "archived_at": 1.0,
              "original_belief_json": json.dumps(row, default=str)}
    values = {k: v for k, v in values.items() if k in cols}
    mem.db.execute(f"INSERT INTO beliefs_archive ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                   list(values.values()))
    assert mem.days()[0][1]["started"] == "2026-05-30"
    assert forget_chat("chat-1", tenant_id="default", conn=mem.db)["ok"]
    assert [(v, has_day(m)) for v, m in mem.days()] == [("the pottery exhibition", False)]
    archived = mem.db.execute("SELECT original_belief_json FROM beliefs_archive").fetchone()[0]
    assert "2026-05-30" not in archived and "pottery" in archived


# ── M2: a day the model read is never the user's ─────────────────────────


@pytest.mark.parametrize(("said", "city", "tag"), [
    ("I live in Lisbon.", "Lisbon", "[since 2019, Kazma's reading] user lives in Lisbon"),
    ("أسكن في لشبونة.", "لشبونة", "[since 2019، قراءة Kazma] user lives in لشبونة"),
])
def test_a_day_the_model_read_is_tagged_on_the_users_own_fact(mem, said, city, tag):
    mem.says(said)
    assert mem.facts("extraction_method", "object") == [("user_stated", city)]
    mem.tell([{"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": city,
               "since": "2019", "confidence": 0.9}], said)
    assert mem.facts("extraction_method", "object") == [("user_stated", city)]
    meta = mem.days()[0][1]
    assert meta.get("started") == "2019" and (meta.get("day_source") or {}).get("method") == "llm_inferred"
    assert tag in mem.block(f"lives in {city}")
    from kazma_core.memory.about_you import facts_about

    (fact,) = facts_about(mem.db, tenant_id="default", now=at(TOLD))["facts"]
    assert fact["source"] == "you" and fact["when"] == {"kind": "since", "day": "2019", "reading": True}


def test_a_day_an_older_build_put_on_the_users_fact_is_tagged_too(mem):
    """Rows written before a day named its source: only the model's pass ever gave one."""
    mem.says("I live in Lisbon.")
    mem.db.execute("UPDATE beliefs SET metadata_json = json_set(metadata_json, '$.started', '2019')")
    assert "[since 2019, Kazma's reading] user lives in Lisbon" in mem.block("lives in Lisbon")


# ── M3: an event is its day ──────────────────────────────────────────────


@pytest.mark.parametrize(("bike", "sofa", "said", "question"), [
    ("a road bike", "a green sofa", "I bought a road bike and a green sofa.", "What did I buy last month?"),
    ("دراجة هوائية", "كنبة خضراء", "اشتريت دراجة هوائية و كنبة خضراء.", "شنو اشتريت الشهر الماضي؟"),
])
def test_what_i_did_last_month_is_that_months_events_only(mem, bike, sofa, said, question):
    mem.tell([event("bought", bike, "2026-05-16"), event("bought", sofa, "2026-03-10")], said, on="2026-05-20")
    ask = recall_mod._time_ask(question, at("2026-06-15"))
    assert ask is not None
    rows = {r["object"]: dict(r) for r in mem._current()}
    assert recall_mod._held_in(rows[bike], ask) and not recall_mod._held_in(rows[sofa], ask)
    for asked, shown in ((bike, [bike]), (sofa, [])):
        hits = recall_mod.recall(f"{question} {asked}", conn=mem.db, tenant_id="default", local_only=True,
                                 now=at("2026-06-15")).beliefs
        assert [h.metadata["object"] for h in hits] == shown


def test_a_month_said_of_an_event_is_that_month(mem):
    row = {"predicate_type": "set", "valid_from": at("2026-06-10"), "valid_until": None,
           "metadata_json": '{"started": "2026-05", "happened": true}'}
    may = recall_mod._time_ask("What did I do last month?", at("2026-06-15"))
    june = recall_mod._time_ask("What did I do this month?", at("2026-06-15"))
    assert recall_mod._held_in(row, may) and not recall_mod._held_in(row, june)


# ── M4: a day to come is planned ─────────────────────────────────────────


@pytest.mark.parametrize(("obj", "said"), [("the jazz concert", "I have tickets to the jazz concert next Sunday."),
                                           ("حفلة الجاز", "عندي تذاكر حفلة الجاز الأحد الجاي.")])
def test_a_day_to_come_is_planned_and_then_was(mem, obj, said):
    mem.tell([event("scheduled_event", obj, "2026-06-07", since="2026-05")], said)
    meta = mem.days()[0][1]
    # Its own metadata; the start the reply gave is kept.
    assert (meta.get("planned"), meta.get("started"), meta.get("happened")) == ("2026-06-07", "2026-05", None)
    assert f"[planned for 7 Jun 2026] user scheduled event {obj}" in mem.block(obj, now="2026-06-01")
    assert f"[planned for 7 Jun 2026] user scheduled event {obj}" in mem.block(obj, now="2026-06-07")
    assert f"[was planned for 7 Jun 2026] user scheduled event {obj}" in mem.block(obj, now="2026-06-08")


def test_a_future_day_never_throws_away_a_valid_start(mem):
    mem.tell([{"subject": "user", "predicate": "lives_in", "predicate_type": "functional", "object": "Porto",
               "since": "2026-04", "on": "2026-08-01", "confidence": 0.9}], "I've been in Porto since April.")
    assert mem.days()[0][1].get("started") == "2026-04"


def test_a_planned_day_that_came_is_the_day_it_happened(mem):
    mem.tell([event("attended", "the jazz concert", "2026-06-07")], "I'm going to the jazz concert next Sunday.")
    assert mem.days()[0][1].get("planned") == "2026-06-07"
    mem.tell([event("attended", "the jazz concert", "2026-06-07")], "I attended the jazz concert yesterday.", turn=2,
             on="2026-06-08")
    ((_value, meta),) = mem.days()
    assert (meta.get("started"), meta.get("happened"), meta.get("planned")) == ("2026-06-07", True, None)


def test_told_at_no_known_time_a_day_to_come_is_not_one_that_happened(mem):
    mem.tell([event("attended", "the jazz concert", "2999-06-07")], "I attended the jazz concert.", now=None)
    meta = mem.days()[0][1]
    assert not meta.get("happened") and meta.get("planned") == "2999-06-07"


# ── M6: an event is the user's to tell ───────────────────────────────────


@pytest.mark.parametrize(("said", "obj", "kept"), [
    ("I went to a pottery exhibition at the craft centre last Saturday.", "a pottery exhibition at the craft centre",
     True),
    # In a question, and in other words for a few.
    ("Can you suggest a lock for the road bike I bought yesterday in Leiden?", "a road bike in Leiden", True),
    ("اشتريت دراجة هوائية أمس من ليدن، أي قفل تنصحني؟", "دراجة هوائية من ليدن", True),
    # The assistant's, a pasted page's, someone else's.
    ("What should I do this weekend?", "a pottery exhibition at the craft centre", False),
    ("Translate to Dutch: I bought a road bike yesterday in Leiden.", "a road bike in Leiden", False),
    ("My sister said: I bought a road bike yesterday in Leiden.", "a road bike in Leiden", False),
    ("ترجم: اشتريت دراجة هوائية أمس من ليدن.", "دراجة هوائية من ليدن", False),
    ("شنو أسوي هالويكند؟", "معرض فخار في مركز الحرف", False),
])
def test_an_event_stands_in_the_users_own_words(mem, said, obj, kept):
    stats = {"skipped_filler": False, "source": "llm", "applied": 0, "rejected": 0, "actions": []}
    mem.tell([event("bought", obj, "2026-05-30")], said, stats=stats)
    assert bool(mem.facts()) is kept
    assert stats.get("not_users_words", 0) == (0 if kept else 1)


@pytest.mark.parametrize(("obj", "first", "said", "kept"), [
    # Named by what was told before, the day in the user's own words.
    ("a gold watch", "I have a gold watch.", "It was a gift, I got it in 2018.", True),
    ("a gold watch", "I have a gold watch.", "Yeah, I think I got it last Monday.", True),
    ("ساعة ذهبية", "عندي ساعة ذهبية.", "اشتريتها قبل أسبوعين.", True),
    ("ساعة ذهبية", "عندي ساعة ذهبية.", "اي والله، كان السبت الماضي.", True),
    # No day of the user's: the assistant's, or a pasted text's.
    ("a gold watch", "I have a gold watch.", "Yes, that one. Thank you!", False),
    ("a gold watch", "I have a gold watch.", "Translate to Dutch: I got it in 2018.", False),
    ("ساعة ذهبية", "عندي ساعة ذهبية.", "ترجم: اشتريتها قبل أسبوعين.", False),
    # Nothing held of it: the assistant's alone.
    ("a gold watch", "", "Yeah, I think I got it last Monday.", False),
])
def test_a_day_told_of_something_held_need_not_name_it_again(mem, obj, first, said, kept):
    if first:
        mem.tell([event("owns", obj, None)], first)
    held, _refs = mem.held()
    mem.tell([event("bought", obj, "2018")], said, turn=2, held=held)
    assert (("bought", obj) in mem.facts()) is kept


# ── The smaller points ───────────────────────────────────────────────────


@pytest.mark.parametrize(("day", "label"), [("2026-05-30", "[on 30 May 2026]"), ("2026-04", "[in Apr 2026]"),
                                            ("2005", "[in 2005]")])
def test_a_month_or_a_year_is_said_in(mem, day, label):
    mem.tell([event("visited", "Rome", day)], "I visited Rome.")
    assert f"{label} user visited Rome" in mem.block("Rome")


def test_what_kazma_knows_says_when_an_event_happened(mem):
    from kazma_core.memory.about_you import facts_about

    mem.tell([event("visited", "Rome", "2026-04"), event("bought", "a road bike", "2026-05-30"),
              event("scheduled_event", "the jazz concert", "2026-06-07")],
             "I visited Rome, bought a road bike and have the jazz concert.")
    listed = facts_about(mem.db, tenant_id="default", now=at("2026-06-01"))["facts"]
    when = {f["value"]: (f["when"]["kind"], f["when"]["day"]) for f in listed}
    assert when == {"Rome": ("in", "2026-04"), "a road bike": ("on", "2026-05-30"),
                    "the jazz concert": ("planned", "2026-06-07")}
    later = facts_about(mem.db, tenant_id="default", now=at("2026-07-01"))["facts"]
    assert {f["when"]["kind"] for f in later if f["value"] == "the jazz concert"} == {"was_planned"}


def test_a_corrected_event_keeps_its_day(mem):
    from kazma_core.memory.about_you import correct_fact

    mem.tell([event("visited", "Rome", "2026-05-30")], "I visited Rome.")
    bid = mem.db.execute("SELECT id FROM beliefs").fetchone()[0]
    assert correct_fact(mem.db, bid, "Florence", tenant_id="default", now=at("2026-06-01"))["ok"]
    ((value, meta),) = mem.days()
    assert (value, meta.get("started"), meta.get("happened")) == ("Florence", "2026-05-30", True)
    assert (meta.get("day_source") or {}).get("method") == "llm_inferred"


def test_memory_list_shows_an_events_day_and_lists_it_by_it(mem):
    from kazma_core.memory.memory_list import list_memories

    mem.tell([event("bought", "a road bike", "2026-05-16"), event("bought", "a green sofa", "2026-03-10")],
             "I bought a road bike and a green sofa.", on="2026-06-05")
    every = list_memories(tenant_id="default", about="bought", conn=mem.db, now=at("2026-06-15"), zone=UTC)
    assert {ln["text"]: ln["date"] for ln in every.lines} == {"user bought a road bike": "on 16 May 2026",
                                                              "user bought a green sofa": "on 10 Mar 2026"}
    may = list_memories(tenant_id="default", about="bought", when="2026-05", conn=mem.db, now=at("2026-06-15"),
                        zone=UTC)
    assert [ln["text"] for ln in may.lines] == ["user bought a road bike"]


def test_an_entry_with_unreadable_metadata_takes_the_day(mem):
    mem.tell([event("visited", "Rome", None)], "I visited Rome.")
    mem.db.execute("UPDATE beliefs SET metadata_json = '{not json'")
    mem.tell([event("visited", "Rome", "2026-05-30")], "I visited Rome yesterday.", turn=2)
    assert [m.get("started") for _o, m in mem.days()] == ["2026-05-30"]


@pytest.mark.asyncio
async def test_one_reply_gives_at_most_eight_facts(monkeypatch):
    from kazma_core.llm_provider import LLMResponse

    class Model:
        config = type("C", (), {"model": "scripted"})()

        async def chat(self, messages, **_kw):
            return LLMResponse(content=json.dumps({"beliefs": [event("bought", f"thing {n}", None)
                                                               for n in range(12)]}), model="scripted")

    monkeypatch.setattr("kazma_core.model_registry.get_model_registry",
                        lambda: type("R", (), {"get_client": lambda self, *a, **k: Model()})())
    beliefs, unavailable = await belief_extractor._llm_extract("I bought things.")
    assert not unavailable and len(beliefs) == 8


# ── Controls ─────────────────────────────────────────────────────────────


def test_control_an_entry_matched_by_its_words_alone_drops_the_second_occasion(mem, monkeypatch):
    """Negative control (H3): the list entry matched without its day, as it was."""
    def by_words(conn, sub, pred, obj, tenant_id, corrects):
        row = conn.execute("SELECT id FROM beliefs WHERE subject = ? AND predicate = ? AND object = ? AND "
                           "tenant_id = ? AND valid_until IS NULL", (sub, pred, obj, tenant_id)).fetchone()
        return (str(row[0]) if row else None), False

    monkeypatch.setattr(belief_mutation, "_entry_told_again", by_words)
    mem.tell([event("visited", "the dentist", "2026-05-30")], "I visited the dentist yesterday.")
    mem.tell([event("visited", "the dentist", "2026-11-10")], "I visited the dentist yesterday.", turn=9,
             on="2026-11-11")
    assert [m.get("started") for _o, m in mem.days()] == ["2026-05-30"]


def test_control_without_the_shared_word_bananas_are_closed(mem, monkeypatch):
    """Negative control (H2): any ``replaces`` taken as the same event."""
    monkeypatch.setattr(belief_extractor, "_same_event", lambda entry, clean: True)
    mem.tell([event("bought", "bananas", "2026-05-23")], "I bought bananas on the 23rd.")
    held, refs = mem.held()
    mem.tell([event("bought", "fresh vegetables", "2026-05-30", replaces=refs["bananas"])],
             "I bought fresh vegetables.", turn=2, held=held)
    assert mem.facts() == [("bought", "fresh vegetables")]


def test_control_a_forget_without_the_step_leaves_the_day(mem, monkeypatch):
    """Negative control (M1)."""
    from kazma_core.memory import forget

    monkeypatch.setattr(forget, "_forget_given_days", lambda conn, **_kw: [])
    mem.tell([event("attended", "the pottery exhibition", None)], "I went to the pottery exhibition.")
    mem.tell([event("attended", "the pottery exhibition", "2026-05-30")], "The pottery exhibition was Saturday.",
             turn=2)
    mem.forget(mem.episode(2, "The pottery exhibition was Saturday."))
    assert mem.days()[0][1]["started"] == "2026-05-30"


def test_control_every_event_kept_takes_the_assistants_for_the_users(mem, monkeypatch):
    """Negative control (M6)."""
    monkeypatch.setattr(belief_extractor, "_in_users_words", lambda value, user_text, told_before: True)
    mem.tell([event("attended", "a pottery exhibition", "2026-05-30")], "What should I do this weekend?")
    assert mem.facts() == [("attended", "a pottery exhibition")]
