"""The extraction set's event turns (dataset v4, the memory plan's W4): the
model's reply, and what the store holds after the product wrote it.

Each turn is answered here with the reply a reader would want (``IDEAL``), in
English and in Arabic, and everything is right; then one part of the write
path is put back as it was before the review of W4, with the same replies: the
reply's scores do not move and the stored ones fall. A scorer that read the
reply alone would see none of it (the negative control of the scorer itself).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from kazma_core.memory import belief_extractor, belief_mutation, benchmark, benchmark_events, fact_days

TURNS = {t["id"]: t for t in json.loads(benchmark.EXTRACTION_PATH.read_text(encoding="utf-8"))["turns"]
         if benchmark_events.is_event_turn(t)}


def _b(subject: str, predicate: str, obj: str, kind: str = "set", **more: Any) -> dict[str, Any]:
    return {"subject": subject, "predicate": predicate, "predicate_type": kind, "object": obj, "confidence": 0.9,
            "importance": 2, **more}


#: The reply a reader would want to each shape: English, Arabic.
IDEAL: dict[str, tuple[list[dict[str, Any]], ...]] = {
    "01": ([_b("user", "attended", "a pottery exhibition at the Sadu House", on="2026-05-30"),
                 _b("user", "bought", "a blue vase for 12 dinars", on="2026-05-30")],
                [_b("user", "visited", "معرض فخار في بيت السدو", on="2026-05-30"),
                 _b("user", "bought", "مزهرية زرقا بـ ١٢ دينار", on="2026-05-30")]),
    "02": ([_b("user", "called", "my mom", on="2026-05-30")], [_b("user", "called", "أمي", on="2026-05-30")]),
    "03": ([_b("user", "lives_in", "Porto", "functional", since="2026-05-30", replaces="f1")],
           [_b("user", "lives_in", "بورتو", "functional", since="2026-05-30", replaces="f1")]),
    "04": ([_b("user", "visited", "Jahra Farm", on="2026-05-30")],
                [_b("user", "visited", "مزرعة الجهراء", on="2026-05-30")]),
    "05": ([_b("user", "bought", "Canon camera", replaces="f1")],
                [_b("user", "bought", "كاميرا كانون", replaces="f1")]),
    "06": ([_b("user", "attended", "the Nawal concert", on="2026-05-22", replaces="f1")],
                [_b("user", "attended", "حفلة نوال", on="2026-05-22", replaces="f1")]),
    "07": ([_b("user", "has_appointment", "dentist appointment", on="2026-06-04")],
                [_b("user", "has_appointment", "موعد عند دكتور الأسنان", on="2026-06-04")]),
    "08": ([], []),
    "09": ([_b("dana", "visited", "Dubai", on="2026-05-29")], [_b("dana", "visited", "دبي", on="2026-05-29")]),
    "10": ([], []),
    "11": ([], []),
    "12": ([_b("user", "habit", "swims every Friday morning")], [_b("user", "habit", "السباحة كل جمعة الصبح")]),
    "13": ([_b("hana", "visited", "Istanbul")], [_b("هناء", "visited", "اسطنبول")]),
    "14": ([_b("user", "ran", "the Muscat marathon", on="2026-03")], [_b("user", "ran", "ماراثون مسقط", on="2026-03")]),
    "15": ([_b("user", "met", "old teacher Adel at the mall")], [_b("user", "met", "مدرسي القديم عادل في المول")]),
}
_OF = {"v": 0, "w": 1}


def _replies(changed: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, list[dict[str, Any]]]:
    return {tid: (changed or {}).get(tid, IDEAL[tid[1:]][_OF[tid[0]]]) for tid in TURNS}


def _run(replies: dict[str, list[dict[str, Any]]], ids: set[str] | None = None) -> dict[str, Any]:
    turns = [t for tid, t in TURNS.items() if ids is None or tid in ids]
    recording = {"prompt_sha256": benchmark._extraction_prompt_sha256(),
                 "message_shape": belief_extractor.MESSAGE_SHAPE,
                 "replies": {benchmark._reply_key(t, {}, "current"): json.dumps({"beliefs": replies[t["id"]]},
                                                                                 ensure_ascii=False) for t in turns}}
    return benchmark_events.run_event_turns(turns, [recording])["events"]


def test_every_shape_is_asked_in_both_languages():
    assert sorted(tid[1:] for tid in TURNS if tid[0] == "v") == sorted(tid[1:] for tid in TURNS if tid[0] == "w")
    assert {TURNS[f"v{n}"]["lang"] for n in IDEAL} == {"en"} and {TURNS[f"w{n}"]["lang"] for n in IDEAL} == {"ar"}
    for n in IDEAL:
        en, ar = TURNS[f"v{n}"]["stored"], TURNS[f"w{n}"]["stored"]
        assert set(en) == set(ar), n
        assert [len(v) if isinstance(v, list) else v for v in en.values()] == [
            len(v) if isinstance(v, list) else v for v in ar.values()], n
        assert [e["days"] for e in en.get("entries", ())] == [e["days"] for e in ar.get("entries", ())], n
        assert len(TURNS[f"v{n}"]["facts"]) == len(TURNS[f"w{n}"]["facts"]), n


def test_the_reply_a_reader_would_want_is_stored_whole():
    events = _run(_replies())
    for lang in ("en", "ar"):
        assert events[lang]["problems"] == [], events[lang]["problems"]
        assert {k: events[lang][k] for k in benchmark_events.EVENT_SCORES} == dict.fromkeys(
            benchmark_events.EVENT_SCORES, 1.0), events[lang]


# ── What the reply's score cannot see (each part of the write path put back) ──


def _called_by_kazmas_name(monkeypatch):
    """An event went by Kazma's name for its verb: "called" is name_is."""
    real = belief_extractor._read_days

    def read(clean, said_at):
        real(clean, said_at)
        clean.pop("event", None)

    monkeypatch.setattr(belief_extractor, "_read_days", read)


def _same_words_are_one_occasion(monkeypatch):
    """The second visit was dropped: the same words, whatever the day."""
    def told_again(conn, sub, pred, obj, tenant_id, corrects):
        row = conn.execute("SELECT id FROM beliefs WHERE subject = ? AND predicate = ? AND object = ? AND tenant_id = ? "
                           "AND valid_until IS NULL", (sub, pred, obj, tenant_id)).fetchone()
        return (str(row[0]), False) if row else (None, False)

    monkeypatch.setattr(belief_mutation, "_entry_told_again", told_again)


def _an_undated_retelling_takes_its_place(monkeypatch):
    """The retelling replaced the dated entry and kept no day."""
    monkeypatch.setattr(belief_extractor, "_keep_the_day", lambda entry, clean, method: None)


def _a_correction_is_another_occasion(monkeypatch):
    """The corrected day was one more visit, or nothing."""
    real = belief_extractor._day_written
    monkeypatch.setattr(belief_extractor, "_day_written",
                        lambda clean, replaced: {**real(clean, replaced), "corrects": None})
    monkeypatch.setattr(fact_days, "could_have_happened_by", lambda day, told_at: False)


def _a_day_to_come_is_a_day_it_happened(monkeypatch):
    monkeypatch.setattr(belief_extractor, "_started_on", lambda since, said_at, zone: since)


def _any_words_are_the_users(monkeypatch):
    """The pasted line is the user's own."""
    monkeypatch.setattr(belief_extractor, "_in_users_words", lambda value, user_text, told_before="": True)


#: The pasted lines read as the user's, as a model does now and then.
PASTED = {
    "v08": [_b("user", "visited", "Rome", on="2026-05-24"), _b("user", "bought", "a leather bag", on="2026-05-24")],
    "w08": [_b("user", "visited", "روما", on="2026-05-24"), _b("user", "bought", "شنطه جلد", on="2026-05-24")],
    "v09": [_b("user", "visited", "Dubai", on="2026-05-29"), _b("user", "met", "Khaled", on="2026-05-29")],
    "w09": [_b("user", "visited", "دبي", on="2026-05-29"), _b("user", "met", "خالد", on="2026-05-29")],
}
#: The move and the correction as another model words them.
WORDED = {
    "v04": [_b("user", "visited", "Jahra farm", on="2026-05-30")],
    "v05": [_b("user", "bought", "That Canon camera", replaces="f1")],
    "w05": [_b("user", "bought", "كاميرا كانون اللي اشتريتها", replaces="f1")],
}

CASES = [
    ("02", _called_by_kazmas_name, None, "safe"),
    ("04", _same_words_are_one_occasion, None, "day"),
    ("05", _an_undated_retelling_takes_its_place, WORDED, "day"),
    ("06", _a_correction_is_another_occasion, None, "day"),
    ("07", _a_day_to_come_is_a_day_it_happened, None, "day"),
    ("08", _any_words_are_the_users, PASTED, "safe"),
    ("09", _any_words_are_the_users, PASTED, "safe"),
]


@pytest.mark.parametrize(("shape", "put_back", "replies", "falls"), CASES,
                         ids=[f"{c[0]}-{c[1].__name__.strip('_')}" for c in CASES])
def test_the_stored_score_falls_where_the_replys_does_not_move(monkeypatch, shape, put_back, replies, falls):
    ids = {f"v{shape}", f"w{shape}"}
    before = _run(_replies(replies), ids)
    put_back(monkeypatch)
    after = _run(_replies(replies), ids)
    for lang in ("en", "ar"):
        assert before[lang][falls] == 1.0, (lang, before[lang]["problems"])
        assert after[lang][falls] < 1.0 and after[lang]["whole"] < before[lang]["whole"], lang
        # The reply is the same: reading it alone shows nothing.
        assert {k: after[lang].get(k) for k in ("found", "right", "quiet", "on")} == {
            k: before[lang].get(k) for k in ("found", "right", "quiet", "on")}, lang


def test_a_move_told_as_an_event_is_an_event_and_no_home():
    """A reply that names no held home: the home stands, the event is kept
    with its day, and what the turn misses is the question."""
    moved = {"v03": [_b("user", "moved_to", "Porto", "functional", on="2026-05-30")],
             "w03": [_b("user", "moved_to", "بورتو", "functional", on="2026-05-30")]}
    events = _run(_replies(moved), set(moved))
    assert events["en"]["problems"] == ["v03: not asked: user Porto"]
    assert events["ar"]["problems"] == ["w03: not asked: user بورتو"]
    assert events["en"]["on"] == events["ar"]["on"] == 1.0


def test_a_reply_that_misses_the_event_is_not_kept():
    events = _run(_replies({"v01": [], "w01": []}), {"v01", "w01"})
    for lang in ("en", "ar"):
        assert events[lang]["stored"] == events[lang]["whole"] == events[lang]["found"] == 0.0


def test_a_wrong_day_is_not_the_day():
    wrong = {"v14": [_b("user", "ran", "the Muscat marathon", on="2025-03")],
             "w14": [_b("user", "ran", "ماراثون مسقط", on="2025-03")]}
    events = _run(_replies(wrong), set(wrong))
    for lang in ("en", "ar"):
        assert events[lang]["stored"] == 1.0 and events[lang]["day"] == events[lang]["on"] == 0.0


def test_other_facts_held_than_the_model_was_shown_is_nothing_right(monkeypatch):
    """A reply's "f1" means the fact of that number: a write path that holds
    other facts by then got the turn wrong, and its scores fall (a quick pass
    that keeps nothing is the control)."""
    ids = {"v02", "w02", "v06", "w06"}
    whole = _run(_replies(), ids)
    monkeypatch.setattr(belief_extractor, "_sanitize_belief", lambda b: None)
    none = _run(_replies(), ids)
    for lang in ("en", "ar"):
        assert whole[lang]["whole"] == 1.0, whole[lang]["problems"]
        assert none[lang]["whole"] == none[lang]["safe"] == none[lang]["stored"] == 0.0
        assert "the model was asked with" in none[lang]["problems"][0]


def test_the_turns_without_events_are_scored_as_they_were():
    """The event turns are scored apart: v3's scores stay comparable."""
    data = json.loads(benchmark.EXTRACTION_PATH.read_text(encoding="utf-8"))
    plain = [t for t in data["turns"] if not benchmark_events.is_event_turn(t)]
    assert benchmark.run_extraction_benchmark({**data, "turns": plain})["heuristic"] == (
        benchmark.run_extraction_benchmark(data)["heuristic"])
