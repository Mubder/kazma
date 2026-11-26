"""One role for one person, and a list read by its kind (the memory plan's W6, step 2).

A model names a fact freely. Another person's job came under "occupation",
"profession_is" and "works_as" (recorded replies, one turn asked five times),
and a changed job under another name left both current; the user's role came
under names the table missed (34 of 79 in the pilots' stores). ``fact_names``
reads the names of a person's occupation as one where the reply gives ONE
value, and files another person's under the name that person already has.

What it must not do is as much of it (the review of step 2, 2026-11-26): a
part in a play, a captain, a weekend job, a boss, a workplace's town, a second
member's sister, a list and the user's own word all stay. Today's table is the
negative control.

The classes: every statement in ``memory/`` that reads facts by one exact name
-- in SQL or in Python -- is declared here with why one name is right for it;
and the name is settled once, before anything compares it
(:func:`test_the_name_is_settled_before_every_check`): a forgotten fact, one
the user denied and an ended one are known under every name they are told by.
"""

from __future__ import annotations

import ast
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest
from kazma_core.memory import belief_extractor, changes, fact_names
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.schema_v2 import ensure_primary_schema

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    yield c
    c.close()


def _say(conn, subject, predicate, value, at, *, kind="functional", method="llm_inferred", text="", **more):
    """A fact as a reply of the deep pass files it; what the write answered."""
    clean = {"subject": subject, "predicate": predicate, "predicate_type": kind, "object": value, "confidence": 0.95,
             **more.pop("fact", {})}
    stats = belief_extractor._apply_beliefs_to_v2([clean], conn, None, session_id="s", turn=int(at), tenant_id="default",
                                                  now=at, private=True, user_text=text, extraction_method=method, **more)
    return {**stats, **(stats["actions"][-1] if stats["actions"] else {})}


def _current(conn, subject):
    return sorted((r[0], r[1]) for r in conn.execute(
        "SELECT predicate, object FROM beliefs WHERE subject = ? AND valid_until IS NULL AND invalidated_at IS NULL",
        (subject,)))


def _past(conn, subject):
    return sorted(r[0] for r in conn.execute(
        "SELECT object FROM beliefs WHERE subject = ? AND valid_until IS NOT NULL", (subject,)))


#: A person's job told twice, then a second time under another name.
JOBS = [("noura", "dentist", "surgeon"), ("نورة", "طبيبة أسنان", "جراحة"), ("هنا", "مهندسة", "مديرة مشروع")]


def _replacing(conn, who, name, value, at, *, held_name, **more):
    """A reply that says its fact replaces the one held under *held_name*."""
    from kazma_core.memory.held_facts import held_facts

    held = held_facts(conn, tenant_id="default", text=f"{who} {value}")
    ref = next(h["ref"] for h in held if h["subject"] == who and h["predicate"] == held_name)
    return _say(conn, who, name, value, at, held=held, fact={"replaces": ref, **more.pop("fact", {})}, **more)


NAMES = [("occupation", "job_title"), ("job_title", "profession_is"), ("profession", "occupation_is"),
         ("has_profession", "current_occupation"), ("employer", "current_employer")]


@pytest.mark.parametrize(("who", "old", "new"), JOBS)
@pytest.mark.parametrize("names", NAMES)
def test_a_persons_changed_job_is_one_value_where_the_reply_says_it_replaces(conn, who, old, new, names):
    _say(conn, who, names[0], old, 1.0)
    _replacing(conn, who, names[1], new, 2.0, held_name=names[0])
    assert _current(conn, who) == [(names[0], new)] and _past(conn, who) == [old]


@pytest.mark.parametrize(("who", "old", "new"), JOBS)
@pytest.mark.parametrize("names", NAMES)
def test_another_persons_job_under_another_name_alone_stays_beside(conn, who, old, new, names):
    """By the name alone another true occupation was closed (the delta check of the review of step 2)."""
    _say(conn, who, names[0], old, 1.0)
    _say(conn, who, names[1], new, 2.0)
    assert _current(conn, who) == sorted([(names[0], old), (names[1], new)])


#: The delta check's seven: a second true occupation, with no word that says so.
SECOND = [("noura", "occupation", "dentist", "job_title", "clinical consultant to Zain"),
          ("omar", "occupation", "engineer", "profession", "lecturer at night school"),
          ("omar", "employer", "KNPC", "current_employer", "his own bakery on Fridays"),
          ("ali", "occupation", "pilot", "job_title", "chairman of the diwaniya committee"),
          ("عمر", "occupation", "مهندس", "job_title", "مدرب كرة بالعصر"),
          ("ريم", "occupation", "طبيبة", "job_title", "عضوة مجلس إدارة الجمعية"),
          ("سارة", "occupation", "محامية", "profession", "كاتبة روايات")]


@pytest.mark.parametrize(("who", "name", "value", "other", "second"), SECOND)
def test_a_second_true_occupation_never_closes_the_first(conn, who, name, value, other, second):
    _say(conn, who, name, value, 1.0)
    _say(conn, who, other, second, 2.0)
    assert {v for _n, v in _current(conn, who)} == {value, second}


@pytest.mark.parametrize(("who", "old", "new"), JOBS)
def test_control_without_replaces_read_the_join_is_gone(conn, monkeypatch, who, old, new):
    monkeypatch.setattr(belief_extractor, "_take_replaces", lambda clean, *_a, **_k: clean.pop("replaces", None) and None)
    _say(conn, who, "occupation", old, 1.0)
    _replacing(conn, who, "job_title", new, 2.0, held_name="occupation")
    assert _current(conn, who) == [("job_title", new), ("occupation", old)]


# ── nothing true is joined or lost ───────────────────────────────────────

@pytest.mark.parametrize(("subject", "first", "second"), [
    # a role and an employer, two people, two places, two accounts, a thing
    ("jane", ("works_at", "Google", "functional"), ("job_title", "product manager", "functional")),
    ("acme", ("located_in", "Lisbon", "functional"), ("based_in", "Porto", "functional")),
    ("أكمي", ("located_in", "لشبونة", "functional"), ("based_in", "بورتو", "functional")),
    ("copilot", ("next_reset", "1 June", "functional"), ("personal_next_reset", "9 June", "functional")),
    ("bookshelf", ("job", "holds the atlas", "functional"), ("position", "against the wall", "functional")),
    ("phone", ("company", "Apple", "functional"), ("employer", "none", "functional")),
    ("rachel", ("occupation", "yoga instructor", "functional"), ("has_role", "bridesmaid", "functional")),
    # a list is no replacement
    ("levi", ("current_role", "director", "functional"), ("occupation", "sternman", "set")),
    ("phil", ("works_as", "hypnotist", "set"), ("profession", "practitioner", "functional")),
    # What a sentence says beside a person's work (the review of step 2): a part, a captain, a second
    # job, the boss, the town, the agency that pays.
    ("rachel", ("occupation", "yoga instructor", "functional"), ("role", "bridesmaid", "functional")),
    ("راشيل", ("occupation", "مدربة يوغا", "functional"), ("role_is", "دور جولييت في المسرحية", "functional")),
    ("hana", ("occupation", "teacher", "functional"), ("works_as", "a lifeguard at weekends", "functional")),
    ("هنا", ("occupation", "مدرسة", "functional"), ("works_as", "منقذة في نهاية الأسبوع", "functional")),
    ("hana", ("job_title", "teacher", "functional"), ("has_occupation", "a part-time florist", "functional")),
    ("هنا", ("job_title", "مدرسة", "functional"), ("has_occupation", "بائعة ورد بعد الدوام", "functional")),
    ("sami", ("profession", "a retired pilot", "functional"), ("occupation", "consultant", "functional")),
    ("sami", ("occupation", "accountant", "functional"), ("job", "fixing the roof", "functional")),
    ("omar", ("employer", "Kuwait Oil Company", "functional"), ("workplace", "Ahmadi", "functional")),
    ("عمر", ("employer", "شركة نفط الكويت", "functional"), ("workplace", "الأحمدي", "functional")),
    ("omar", ("works_at", "Zain", "functional"), ("works_for", "Khaled", "functional")),
    ("dana", ("employer", "Ministry of Health", "functional"), ("employed_by", "a staffing agency", "functional")),
    ("dana", ("employer", "Kuwait Airways", "functional"), ("works_at", "the airport", "functional")),
    ("redis", ("works_as", "cache", "functional"), ("role", "session store", "functional")),
    ("db1", ("role", "primary", "functional"), ("job", "nightly vacuum", "functional")),
    ("nightly", ("job", "backup", "functional"), ("current_job", "running", "state")),
    # a former one, another kind of name
    ("diane", ("works_at", "App My Community", "functional"), ("previous_occupation", "campground owner", "functional")),
])
def test_two_true_facts_stay_two(conn, subject, first, second):
    for at, (name, value, kind) in enumerate((first, second), 1):
        _say(conn, subject, name, value, float(at), kind=kind)
    held = {v for _n, v in _current(conn, subject)}
    # "current_role" is Kazma's own single value whatever its label.
    assert {first[1], second[1]} <= held or first[0] == "current_role"


def test_two_people_of_one_job_stay_two(conn):
    _say(conn, "noura", "occupation", "dentist", 1.0)
    _say(conn, "hana", "job_title", "pilot", 2.0)
    assert _current(conn, "noura") == [("occupation", "dentist")] and _current(conn, "hana") == [("job_title", "pilot")]


@pytest.mark.parametrize(("who", "held", "told"), [
    ("eran", "neuroscientist specializing in recordings", "neuroscientist"),
    ("نورة", "طبيبة أسنان أطفال في السالمية", "طبيبة أسنان")])
def test_a_value_that_says_less_does_not_replace_it(conn, who, held, told):
    _say(conn, who, "occupation", held, 1.0)
    _say(conn, who, "profession", told, 2.0)
    assert ("occupation", held) in _current(conn, who)


@pytest.mark.parametrize(("who", "held", "read"), [("noura", "dentist", "surgeon"), ("نورة", "طبيبة أسنان", "جراحة")])
def test_what_the_user_said_of_a_person_stands_and_the_reading_is_kept(conn, who, held, read):
    """A reading that says it replaces what the user said is refused: the held value stands."""
    _say(conn, who, "occupation", held, 1.0, method="user_explicit")
    _replacing(conn, who, "job_title", read, 2.0, held_name="occupation")
    assert ("occupation", held) in _current(conn, who)
    _replacing(conn, who, "profession", read, 3.0, held_name="occupation", method="user_stated")
    assert _current(conn, who) == [("occupation", read)]


def test_an_earlier_statement_never_becomes_current(conn):
    _say(conn, "noura", "occupation", "surgeon", 5.0)
    _replacing(conn, "noura", "job_title", "dentist", 1.0, held_name="occupation")
    assert _current(conn, "noura") == [("occupation", "surgeon")]


# ── the user ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("name", "old", "new"), [
    ("has_occupation", "engineer", "manager"), ("has_job_title", "محاسب", "مدير"), ("current_occupation_is", "nurse", "teacher"),
    ("current_profession", "ممرض", "مدرس"), ("has_profession", "a dentist", "a surgeon")])
def test_the_users_one_role_is_current_role(conn, name, old, new):
    _say(conn, "user", "job_title", old, 1.0)
    _say(conn, "user", name, new, 2.0)
    assert _current(conn, "user") == [("current_role", new)] and _past(conn, "user") == [old]


@pytest.mark.parametrize("name", ["has_occupation", "has_profession", "current_occupation"])
@pytest.mark.parametrize("values", [("career coach", "influencer", "teacher"), ("مدرب", "مؤثر", "مدرس")])
def test_a_list_of_the_users_occupations_stays_a_list(conn, name, values):
    for at, value in enumerate(values):
        _say(conn, "user", name, value, float(at), kind="set")
    assert [v for _n, v in _current(conn, "user")] == sorted(values)


@pytest.mark.parametrize("values", [("career coach", "influencer", "teacher"), ("مدرب", "مؤثر", "مدرس")])
def test_a_list_stays_one_when_a_reply_labels_one_more_a_single_value(conn, values):
    """Read in the pilots' cached replies: a third occupation labelled
    functional took the list's teacher off it."""
    for at, value in enumerate(values[:2]):
        _say(conn, "user", "has_occupation", value, float(at), kind="set")
    _say(conn, "user", "has_occupation", values[2], 9.0)
    assert [v for _n, v in _current(conn, "user")] == sorted(values)


@pytest.mark.parametrize("values", [("engineer", "teacher", "poet"), ("مهندس", "مدرس", "شاعر")])
@pytest.mark.parametrize("method", ["llm_inferred", "user_stated"])
def test_kazmas_own_name_holding_a_list_takes_nothing(conn, values, method):
    """The user's stated list under ``current_role`` and one more told as a single value (the review's M2)."""
    for at, value in enumerate(values[:2]):
        mutate_belief(conn, "user", "current_role", value, predicate_type="set", extraction_method="user_stated",
                      now=float(at), private=True, tenant_id="default")
    _say(conn, "user", "has_occupation", values[2], 9.0, method=method)
    assert {v for _n, v in _current(conn, "user")} == set(values)


#: Beside a job, never instead of it.
BESIDE = [("role", "Romeo in the school play"), ("role_is", "captain of the football team"), ("role", "father of three"),
          ("role", "دور روميو في المسرحية"), ("role_is", "قائد الفريق"), ("has_role", "Éponine in the musical"),
          ("has_job", "a weekend barista"), ("has_job", "باريستا في الويكند"), ("current_job", "painting the fence"),
          ("role_is", "international student"), ("role_is", "airbnb host")]


@pytest.mark.parametrize(("name", "value"), BESIDE)
@pytest.mark.parametrize("stated", ["llm_inferred", "user_stated"])
def test_what_stands_beside_a_job_takes_no_job_and_asks_nothing(conn, name, value, stated):
    _say(conn, "user", "job_title", "engineer", 1.0, method=stated, text="I am an engineer")
    _say(conn, "user", name, value, 2.0, text=f"I am {value}")
    assert {v for _n, v in _current(conn, "user")} == {"engineer", value}
    _say(conn, "user", "job_title", "manager", 3.0, method="user_stated")
    assert value in {v for _n, v in _current(conn, "user")}, "the next job closes it"
    assert [c for c in changes.list_changes(conn, tenant_id="default") if c["kind"] == "pending"] == []


def test_a_role_held_under_a_name_from_before_the_table_is_filed_under_kazmas(conn):
    """Written before ``has_occupation`` was read as ``current_role``: the value is filed anew
    under Kazma's name, the trust gate decides, and the user's own word replaces it there."""
    mutate_belief(conn, "user", "has_occupation", "engineer", predicate_type="functional", extraction_method="user_stated",
                  now=1.0, private=True, tenant_id="default")
    refused = _say(conn, "user", "has_occupation", "manager", 2.0)
    assert refused["blocked"] == "lower_trust_source" and _current(conn, "user") == [("current_role", "engineer")]
    _say(conn, "user", "job_title", "manager", 3.0, method="user_stated")
    assert _current(conn, "user") == [("current_role", "manager")] and _past(conn, "user") == ["engineer"]


def test_the_quick_pass_writes_kazmas_name(conn):
    mutate_belief(conn, "user", "has_occupation", "engineer", predicate_type="functional", extraction_method="llm_inferred",
                  now=1.0, private=True, tenant_id="default")
    _say(conn, "user", "current_role", "manager", 2.0, method="user_stated")
    assert _current(conn, "user") == [("current_role", "manager")]


def test_a_store_that_holds_both_names_stays_as_it_is(conn):
    for name, value in (("has_occupation", "engineer"), ("current_role", "teacher")):
        mutate_belief(conn, "user", name, value, predicate_type="functional", extraction_method="llm_inferred", now=1.0,
                      private=True, tenant_id="default")
    _say(conn, "user", "has_occupation", "manager", 2.0)
    assert _current(conn, "user") == [("current_role", "manager"), ("has_occupation", "engineer")]


# ── the name is settled before anything asks by it (M1, M3) ──────────────

#: Every pair of names that say one thing of the user, from the tables.
SAME_KIND = sorted({(told, kazma) for table in (fact_names.KINDS, fact_names.ONE_VALUE_NAMES)
                    for kazma, names in table.items() for told in names} - {("moved_to", "lives_in"), ("called", "name_is"),
                                                                          ("named", "name_is")})


@pytest.mark.parametrize("value", ["a dentist", "طبيبة أسنان"])
@pytest.mark.parametrize(("told", "kazma"), SAME_KIND)
def test_what_the_user_denied_never_comes_back_under_any_name(conn, told, kazma, value):
    text = f"I am {value}"
    _say(conn, "user", told, value, 1.0, text=text)
    card = [c for c in changes.list_changes(conn, tenant_id="default") if c["kind"] == "reading"]
    if kazma != "age_is":
        assert card and card[0]["predicate"] == kazma, "a first reading becomes the user's fact with no card"
        changes.dismiss(conn, card[0]["id"], tenant_id="default")
        assert _current(conn, "user") == []
        for again in (told, kazma, *(t for t, k in SAME_KIND if k == kazma)):
            _say(conn, "user", again, value, 5.0, text=text)
        assert _current(conn, "user") == []
        # Control: another value is read as any.
        _say(conn, "user", told, "Somebody else", 6.0, text="I am Somebody else")
        assert _current(conn, "user") == [(kazma, "Somebody else")]


@pytest.mark.parametrize("value", ["dentist", "طبيبة أسنان"])
@pytest.mark.parametrize(("told", "kazma"), [*SAME_KIND, ("role_is", "role_is"), ("has_job", "has_job"), ("current_job", "current_job")])
def test_a_forgotten_fact_never_comes_back_from_its_turn_under_any_name(conn, monkeypatch, told, kazma, value):
    from types import MappingProxyType

    from kazma_core.memory.speakers import Member, Room

    room = Room(space="shared", shared=True, members=MappingProxyType({
        "sara": Member(account="sara", name="Sara"), "omar": Member(account="omar", name="Omar")}))
    monkeypatch.setattr("kazma_core.memory.speakers.room", lambda _tenant: room)

    def turn(name, not_again=None):
        belief_extractor._apply_beliefs_to_v2(
            [{"subject": "Sara", "predicate": name, "predicate_type": "functional", "object": value, "confidence": 0.95}], conn,
            None, session_id="c", turn=1, tenant_id="shared", now=1.0, private=True, user_text=f"Sara is {value}", said_by="omar",
            not_again=not_again)

    turn(told)
    row = conn.execute("SELECT id, subject, predicate, object FROM beliefs WHERE valid_until IS NULL").fetchone()
    assert row["subject"] == "member_sara" and row["predicate"] == kazma
    mark = {(row["subject"], row["predicate"], str(row["object"]).casefold())}
    conn.execute("DELETE FROM beliefs")
    for name in (told, kazma, *(t for t, k in SAME_KIND if k == kazma)):
        turn(name, mark)
    assert conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 0
    turn(told, {(row["subject"], row["predicate"], "another value")})  # control: the mark is what keeps it out
    assert conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 1


def test_forgotten_under_kazmas_name_while_a_list_of_the_kind_is_held(conn, monkeypatch):
    """A list keeps the told name, so the names differ: the statement is the same."""
    from types import MappingProxyType

    from kazma_core.memory.speakers import Member, Room

    room = Room(space="shared", shared=True, members=MappingProxyType({"sara": Member(account="sara", name="Sara")}))
    monkeypatch.setattr("kazma_core.memory.speakers.room", lambda _tenant: room)
    for at, value in enumerate(("engineer", "teacher")):
        mutate_belief(conn, "member_sara", "current_role", value, predicate_type="set", extraction_method="user_stated", now=float(at),
                      private=True, tenant_id="shared")
    belief_extractor._apply_beliefs_to_v2(
        [{"subject": "Sara", "predicate": "has_occupation", "predicate_type": "functional", "object": "dentist", "confidence": 0.95}],
        conn, None, session_id="c", turn=1, tenant_id="shared", now=5.0, private=True, user_text="Sara is a dentist",
        said_by="sara", not_again={("member_sara", "current_role", "dentist")})
    assert "dentist" not in {r[0] for r in conn.execute("SELECT object FROM beliefs")}


@pytest.mark.parametrize(("who", "old", "new"), JOBS)
@pytest.mark.parametrize("names", [("occupation", "job_title"), ("profession", "has_occupation"), ("employer", "employer_name")])
@pytest.mark.parametrize("ended_as", [0, 1])
def test_an_ended_fact_is_ended_whatever_the_reply_calls_it(conn, who, old, new, names, ended_as):
    """The review's M3: the surgeon filed under "occupation" stayed when the reply ended "job_title surgeon"."""
    _say(conn, who, names[0], old, 1.0)
    _replacing(conn, who, names[1], new, 2.0, held_name=names[0])
    assert _current(conn, who) == [(names[0], new)]
    stats = _say(conn, who, names[ended_as], new, 3.0, fact={"ended": True})
    assert stats.get("ended") == 1 and _current(conn, who) == []


@pytest.mark.parametrize("name", ["has_occupation", "job_title", "current_occupation", "has_profession", "works_as"])
def test_the_users_own_ended_job_too(conn, name):
    _say(conn, "user", "job_title", "surgeon", 1.0)
    assert _say(conn, "user", name, "surgeon", 2.0, fact={"ended": True}).get("ended") == 1
    assert _current(conn, "user") == []


def test_control_an_ending_of_another_value_or_another_kind_ends_nothing(conn):
    _say(conn, "noura", "occupation", "surgeon", 1.0)
    for name, value in (("job_title", "dentist"), ("lives_in", "surgeon"), ("favorite_job", "surgeon")):
        assert not _say(conn, "noura", name, value, 2.0, fact={"ended": True}).get("ended")
    assert _current(conn, "noura") == [("occupation", "surgeon")]


@pytest.mark.parametrize(("one", "other", "same"), [
    ("has_occupation", "current_role", True), ("job_title", "occupation", True), ("employer", "works_at", True),
    ("planning_to", "plans_to", True), ("age", "has_age", True), ("age", "birthday", False), ("favorite_color", "favorite_car", False),
    ("favorite_color", "color_favorite", True), ("likes", "loves", False), ("lives_in", "works_at", False), ("met", "received", False)])
def test_names_that_say_one_thing(one, other, same):
    assert fact_names.same_statement(one, other) is same


# The gate: what asks by the name runs after it is settled, and nothing names the fact again.

#: A function of the pass that asks by a fact's name (found by the source below).
_ASKS_BY_NAME = {"_forgotten_of_them", "_said_not_me_before", "_apply_ending", "mutate_belief", "_put_to_the_user"}
_SETTLES = "name_to_file"


def _order(source: str) -> tuple[list[str], set[str]]:
    """The calls of the pass's loop in order, and the helpers that still give a fact's name."""
    tree = ast.parse(source)
    body = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_apply_beliefs_to_v2")
    loop = next(n for n in ast.walk(body) if isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "b")
    calls = [(n.lineno, n.col_offset, n.func.id) for n in ast.walk(loop) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    named = {f.name for f in tree.body if isinstance(f, ast.FunctionDef) for n in ast.walk(f)
             if isinstance(n, ast.Assign) for t in n.targets
             if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == "predicate"
             and f is not body}
    named |= {"<loop>" for n in ast.walk(loop) if isinstance(n, ast.Assign) for t in n.targets
              if isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and t.slice.value == "predicate"
              and not (isinstance(n.value, ast.Attribute) and n.value.attr == "name")}
    return [name for _l, _c, name in sorted(calls)], named


def _out_of_order(source: str) -> set[str]:
    calls, renames = _order(source)
    settled = calls.index(_SETTLES) if calls.count(_SETTLES) == 1 else -1
    before = {c for c in calls[:settled] if c in _ASKS_BY_NAME} if settled >= 0 else {_SETTLES}
    return before | {c for c in calls[settled + 1:] if c in renames} | ({"<loop>"} & renames)


def _source(name: str) -> str:
    return (ROOT / "kazma-core/kazma_core/memory" / name).read_text(encoding="utf-8")


def test_the_name_is_settled_before_every_check():
    source = _source("belief_extractor.py")
    assert _out_of_order(source) == set()
    assert set(_order(source)[0]) >= _ASKS_BY_NAME, "a check moved out of the pass: gate it where it went"
    # Every function of the pass that compares a fact's name is one of them, or runs before and renames.
    asking = {f.name for f in ast.parse(source).body if isinstance(f, ast.FunctionDef)
              if "same_statement" in ast.unparse(f) or "changes.user_said_not_me" in ast.unparse(f)}
    assert asking == {"_forgotten_of_them", "_current_fact", "_user_said_not_me"}
    # The mutation names a fact by its words alone (``predicates``), and by nothing else.
    mutation = _source("belief_mutation.py")
    assert _SETTLES + "(" not in mutation and "users_predicate" not in mutation
    assert mutation.count("canonical_predicate(") == 1


@pytest.mark.parametrize(("old", "new"), [
    # a check before the name is settled
    ("        filing = name_to_file(", "        _said_not_me_before(primary_conn, clean, _method, tenant_id, stats)\n        filing = name_to_file("),
    ("        filing = name_to_file(", "        _forgotten_of_them(clean, not_again)\n        filing = name_to_file("),
    # the name given again after it
    ("        if _held_back_by_auto_store(", "        clean[\"predicate\"] = named\n        if _held_back_by_auto_store("),
    ("        if _held_back_by_auto_store(", "        _take_replaces(clean, held, primary_conn, tenant_id)\n        if _held_back_by_auto_store("),
    # not settled at all
    ("        clean[\"predicate\"] = filing.name", "        clean[\"predicate\"] = named"),
])
def test_control_a_check_moved_or_a_name_given_again_is_found(old, new):
    source = _source("belief_extractor.py")
    assert source.count(old) == 1
    assert _out_of_order(source.replace(old, new)) != set()


def test_an_age_is_one_value_and_no_card(conn):
    from kazma_core.memory import changes

    _say(conn, "user", "has_age", "31", 1.0)
    assert _current(conn, "user") == [("age_is", "31")]
    _say(conn, "user", "current_age", "٣٢", 2.0)
    assert _current(conn, "user") == [("age_is", "٣٢")]
    assert "age_is" not in changes._reading_names(), "a first reading of one is put on the Memory page"


# ── kinds of lists ───────────────────────────────────────────────────────

@pytest.mark.parametrize(("name", "kind"), [
    ("plans_to", "plans"), ("planning_to_visit", "plans"), ("is_planning", "plans"), ("has_plan", "plans"),
    ("intends_to_try", "plans"), ("planned_purchase", "plans"), ("current_plan", "plans"),
    ("has_goal", "goals"), ("career_goal", "goals"), ("goal_is", "goals"), ("wants_to_achieve", None),
    ("is_interested_in", "interests"), ("has_interest_in", "interests"), ("interest", "interests"), ("hobby", "interests"),
    ("bought", "bought"), ("purchased_item", "bought"), ("participated_in", "attended"), ("has_routine", "habits"),
    # other things
    ("has_plant", None), ("plan_type", None), ("phone_plan", None), ("health_plan", None), ("interest_rate", None),
    ("goals_scored", None), ("uses_tool", None), ("uses_service", None), ("went_to", None), ("owns", None),
    ("explains", None), ("airplane_seat", None), ("wants_to_buy", None), ("considering_buying", None)])
def test_a_names_kind_of_list(name, kind):
    assert fact_names.list_kind(name) == kind


# ── the class: who reads facts by one name ───────────────────────────────

#: (file, function): why one exact name is right for it.
ONE_NAME = {
    ("belief_mutation.py", "_established_kind"): "the kind of one name's own entries, after the name is chosen",
    ("belief_mutation.py", "_mutate_functional_locked"): "the value under the name the pass settled",
    ("belief_mutation.py", "_record_earlier_statement"): "the history of that name",
    ("belief_mutation.py", "_entry_told_again"): "an entry told again under its own name",
    ("belief_mutation.py", "_mutate_set"): "a note ('noted') is Kazma's own name",
    ("belief_mutation.py", "_users_list_entry"): "Kazma's own names a person holds several of (_LISTABLE)",
    ("belief_mutation.py", "_is_favorite"): "a favourite's name by its own words",
    ("belief_mutation.py", "file_under"): "the fact re-filed, and the open changes of its old name with it",
    ("belief_extractor.py", "_llm_extract"): "the prompt's words (a module text that holds 'predicate'), no statement",
    ("schema_v2.py", "ensure_primary_schema"): "the schema's own text, no statement",
    ("hygiene.py", "is_blocked_belief_triple"): "the name's shape (its length, a space), not which name",
    ("forget.py", "finish_old_tombstones"): "an emptied name (''), not which one",
    ("forget.py", "_mentions_found"): "what the user asked to forget, matched in the name's words",
    ("current_facts.py", "parse_current_facts"): "one current fact a subject and name, as the store keeps it",
    ("graph_sync.py", "_plan_changes"): "the graph copy's edge against the fact's own name",
    ("recall.py", "_named_facts"): "reads by the kind (fact_kind)",
    ("belief_extractor.py", "_tells_an_undated_event"): "the reply's own name, before it is settled: is it an event",
    ("belief_extractor.py", "extract_beliefs_heuristic"): "the quick pass's own names, Kazma's",
    ("changes.py", "_recorded"): "a change of one fact, recorded under the name it was settled as",
    ("changes.py", "_to_tell"): "a change is recorded under Kazma's name",
    ("changes.py", "_held_again"): "the entries of the list the undone write closed",
    ("changes.py", "answer_pending"): "the changes of the fact just written, under its settled name",
    ("changes.py", "record_first_reading"): "a reading is shown for Kazma's own names only",
    ("backfill_v2.py", "_backfill_graph_to_beliefs"): "Kazma's own names, left out of an old copy",
    ("backfill_v2.py", "_llm_extract_beliefs_from_memories"): "a note ('noted') is Kazma's own name",
    ("ego_anchor.py", "object_should_mint_entity"): "Kazma's names of a link between two things",
    ("fact_names.py", "asked_words"): "Kazma's own name of a role, against the other names of it",
    ("fact_names.py", "names_one_value"): "Kazma's name against the one it was told by",
    ("predicates.py", "canonical_predicate"): "the names of the same words: what it is for",
    ("profile_suggestions.py", "_line_for"): "Kazma's own names, as lines of About me",
    ("forget.py", "_reopen_replaced"): "the entry that took a closed one's place has its name",
    ("list_reopen.py", "find_closed_entries"): "entries closed under that one name",
    ("recall.py", "_occasions"): "the other occasions of one event",
    ("recall.py", "_list_siblings"): "the name found first; the kind's other names after it (_of_the_same_list)",
    ("recall.py", "_of_the_same_list"): "reads by the kind: the names it leaves out are the one already read",
    ("held_facts.py", "held_facts"): "leaves out the hub's own link, by its fixed name",
    ("reembed.py", "_reencode_row"): "the row's own name, made into its vector's text",
    ("rehydrate.py", "_noted_index"): "a note ('noted') is Kazma's own name",
}
#: A statement that compares a fact's name -- "=", "==", "IN", "LIKE", "IS", in
#: SQL however it is written -- and not one that sets it.
_BY_NAME = re.compile(r"(?<!SET )(?<![\w.])(?:\w+\.)?predicate\s*\)?\s*(?:=|==|!=|<>|\bIS\b|\bLIKE\b|\bGLOB\b|\bNOT\s+IN\b|"
                      r"\bIN\b|\bBETWEEN\b|\bREGEXP\b|\bMATCH\b)(?!\s*(?:NULL|NOT NULL|''|excluded|CASE))"
                      r"|(?:=|==|!=|<>|\bLIKE\b|\bIN\s*\()\s*(?:\w+\.)?predicate\b|\binstr\s*\(\s*(?:\w+\.)?predicate\b",
                      re.IGNORECASE)
#: A statement's conditions: what follows WHERE, ON or HAVING, its grouping and order taken out.
_CONDITION = re.compile(r"\b(?:WHERE|ON|HAVING)\b(.*?)(?=\bSELECT\b|\bUNION\b|\bGROUP\s+BY\b|\bORDER\s+BY\b|\bLIMIT\b|"
                        r"\bBEGIN\b|\bFOR\s+EACH\b|$)", re.IGNORECASE | re.DOTALL)
_NOT_A_CONDITION = re.compile(r"\bpredicate\s+IS\s+(?:NOT\s+)?NULL\b", re.IGNORECASE)
_NAME_WORDS = ("predicate", "pred", "predicates")
#: What compares one value with another, called.
_COMPARING_CALLS = {"startswith", "endswith", "find", "index", "count", "__eq__", "__contains__", "eq", "ne", "contains",
                    "is_", "is_not", "fullmatch", "match", "search"}


def _a_name(node: ast.AST) -> bool:
    """Whether *node* is a fact's name: ``row["predicate"]``, ``x.predicate``,
    ``pred``, ``_field(row, "predicate")``, ``str(pred).lower()``."""
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "predicate"
    if isinstance(node, ast.Attribute):
        return node.attr in _NAME_WORDS or _a_name(node.value) and node.attr in ("lower", "strip", "casefold")
    if isinstance(node, ast.Name):
        return node.id in _NAME_WORDS
    if isinstance(node, ast.Call):
        return (any(isinstance(a, ast.Constant) and a.value == "predicate" for a in node.args)
                or _a_name(node.func) or any(_a_name(a) for a in node.args) and getattr(node.func, "id", "") == "str")
    return False


def _holds_a_name(node: ast.AST) -> bool:
    """Whether *node* reads a fact's name anywhere in it: a tuple, a set of them, a call over one."""
    return any(_a_name(x) for x in ast.walk(node))


def _a_count(node: ast.AST) -> bool:
    """``len(...)``: how many, not which."""
    return isinstance(node, ast.Call) and getattr(node.func, "id", "") == "len"


def _compares_a_name(node: ast.AST) -> bool:
    """A comparison, a comparing call, a table keyed by names, or a ``match`` over a fact's name."""
    if isinstance(node, ast.Compare):
        sides = [node.left, *node.comparators]
        return any((_holds_a_name(a) or _holds_a_name(b))
                   and not any(isinstance(x, ast.Constant) and x.value in (None, "") or _a_count(x) for x in (a, b))
                   for a, b in zip(sides, sides[1:], strict=False))
    if isinstance(node, ast.Call):
        called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        on = node.func.value if isinstance(node.func, ast.Attribute) else None
        return called in _COMPARING_CALLS and ((on is not None and _a_name(on)) or any(_holds_a_name(a) for a in node.args))
    if isinstance(node, ast.DictComp):
        return _holds_a_name(node.key)
    if isinstance(node, ast.Match):
        return _holds_a_name(node.subject)
    return False


def _reads_by_name(text: str) -> bool:
    """Whether a statement's text compares a fact's name, however it is put."""
    if _BY_NAME.search(text):
        return True
    return any(re.search(r"(?<![\w.])(?:\w+\.)?predicate\b", _NOT_A_CONDITION.sub(" ", condition)) is not None
               for condition in _CONDITION.findall(text))


def _readers(source: str) -> set[str]:
    """The functions of *source* that read or compare facts by one name: in a
    statement however its text is put together (a constant, an f-string, "+",
    a module's constant), or in Python. Not read: a row's name by its place
    (``row[2] == name``), a reader in another module than the statement."""
    tree = ast.parse(source)
    module = {t.id: "".join(str(c.value) for c in ast.walk(n.value) if isinstance(c, ast.Constant) and isinstance(c.value, str))
              for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None
              for t in (n.targets if isinstance(n, ast.Assign) else [n.target]) if isinstance(t, ast.Name)}
    out = set()
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        inner = {id(x) for f in ast.walk(func) if f is not func and isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                 for x in ast.walk(f)}
        doc = ast.get_docstring(func, clean=False)
        for node in ast.walk(func):
            if id(node) in inner:
                continue
            text = ""
            if isinstance(node, ast.JoinedStr):
                text = "".join(str(v.value) if isinstance(v, ast.Constant) else "?" for v in node.values)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != doc:
                text = node.value
            elif isinstance(node, ast.Name) and node.id in module:
                text = module[node.id]
            elif _compares_a_name(node):
                out.add(func.name)
            if text and re.search(r"\b(?:FROM|UPDATE|DELETE|JOIN|WHERE|AND|OR)\b|^\s*(?:=|IN)\b", text, re.IGNORECASE) \
                    and _reads_by_name(text):
                out.add(func.name)
    return out


def test_every_reader_of_facts_by_one_name_is_declared():
    files = subprocess.run(["git", "ls-files", "kazma-core/kazma_core/memory/*.py"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    found = {(Path(f).name, name) for f in files for name in _readers((ROOT / f).read_text(encoding="utf-8"))
             if "benchmark" not in f and "eval_" not in f}
    assert found - set(ONE_NAME) == set(), "read by the kind (fact_names.same_statement, list_kind), or declare why one name is right"
    assert set(ONE_NAME) - found == set(), "declared and gone"


#: A reader of facts by one name, written each way (the review of step 2 planted sixteen).
PLANTED = [
    'conn.execute("SELECT id FROM beliefs WHERE subject = ? AND predicate = ?", ())',
    'conn.execute("SELECT id FROM beliefs WHERE predicate IN (?, ?)", ())',
    'conn.execute("SELECT id FROM beliefs WHERE predicate == ?", ())',
    'conn.execute("SELECT id FROM beliefs WHERE predicate = :p", {})',
    'conn.execute("SELECT id FROM beliefs WHERE predicate LIKE ?", ())',
    'conn.execute(f"SELECT id FROM beliefs WHERE {x} AND predicate = ?", ())',
    'conn.execute("SELECT id FROM beliefs WHERE subject = ? " + "AND predicate = ?", ())',
    'conn.execute(_SQL, ())',
    'conn.execute("SELECT b.id FROM beliefs b JOIN beliefs c ON b.predicate = c.predicate", ())',
    'conn.execute("UPDATE beliefs SET object = ? WHERE predicate = ?", ())',
    'conn.execute("DELETE FROM beliefs WHERE predicate IS ?", ())',
    'return [r for r in rows if r["predicate"] == name]',
    'return any(row.predicate == name for row in rows)',
    'return _field(row, "predicate") in names',
    'return str(pred).lower() != other',
    'if pred == clean["predicate"]:\n        return 1',
    # The delta check of the review: eleven it did not see.
    'conn.execute("SELECT id FROM beliefs WHERE ? = predicate", ())',
    'conn.execute("SELECT id FROM beliefs WHERE predicate BETWEEN ? AND ?", ())',
    'conn.execute("SELECT id FROM beliefs WHERE instr(predicate, ?) > 0", ())',
    'conn.execute("SELECT id FROM beliefs WHERE predicate REGEXP ?", ())',
    'return {r["predicate"]: r for r in rows}.get(name)',
    'return name in {r["predicate"] for r in rows}',
    'return row.predicate.startswith(name)',
    'return row.predicate is name',
    'return (row.subject, row.predicate) == (x, name)',
    'return operator.eq(row["predicate"], name)',
    'match row["predicate"]:\n        case "lives_in":\n            return 1',
]


@pytest.mark.parametrize("planted", PLANTED)
def test_control_each_way_of_reading_by_one_name_is_found(planted):
    source = f'_SQL = "SELECT id FROM beliefs WHERE predicate = ?"\ndef _reads(conn, rows, row, name, names, pred, other, x, clean):\n    {planted}\n'
    assert _readers(source) == {"_reads"}, planted


def test_control_what_sets_a_name_or_asks_none_is_not_a_reader():
    source = ('def _writes(conn, b):\n    conn.execute("UPDATE beliefs SET predicate = ? WHERE id = ?", ())\n'
              '    conn.execute("SELECT predicate FROM beliefs WHERE predicate IS NOT NULL", ())\n    return b["predicate"] or ""\n')
    assert _readers(source) == set()


def test_every_kind_that_is_asked_is_some_names_kind():
    """Each key of the tables is a kind a name of the model's is read as, or a
    Kazma name; and has words in both languages (``ASKED_WITH``)."""
    names = ["has_goal", "plans_to", "interested_in", "bought", "received", "met", "attended", "finished", "has_routine",
             "dietary_restriction", "doctor", "age_is", "birthday_is", "enrolled_in", "drives", "has_daughter",
             "has_son", "favorite_color", "lives_in", "works_at", "current_role", "name_is", "preferred_name",
             "allergic_to", "visited", "has_pet", "marital_status"]
    kinds = {fact_names.fact_kind(n) for n in names}
    assert kinds == set(fact_names.ASKED_WITH) | set(fact_names.ASKED_ALONE)
    assert set(fact_names.LISTS) <= kinds and set(fact_names._FEW) <= kinds
    assert set(fact_names._INDEXED) <= kinds
    for key, words in {**fact_names.ASKED_WITH, **fact_names.ASKED_ALONE}.items():
        assert any(w.isascii() for w in words) and any(not w.isascii() for w in words), key
    assert fact_names.fact_kind("owns_boat") is None  # control


# ── the delta check of the review of step 2 ─────────────────────────────

@pytest.mark.parametrize(("value", "text"), [("a lawyer", "I am a lawyer"), ("محامي", "أنا محامي"), ("محامية", "أعمل محامية")])
@pytest.mark.parametrize("again", ["role", "role_is", "has_role"])
def test_a_denied_role_told_as_a_role_stays_out(conn, value, text, again):
    """N2: "Not me" to ``current_role lawyer``, then ``role_is lawyer`` was filed again."""
    _say(conn, "user", "has_occupation", value, 1.0, text=text)
    card = next(c for c in changes.list_changes(conn, tenant_id="default") if c["kind"] == "reading")
    changes.dismiss(conn, card["id"], tenant_id="default")
    _say(conn, "user", again, value, 2.0, text=text)
    assert _current(conn, "user") == []


@pytest.mark.parametrize("again", ["role", "role_is", "has_role"])
def test_a_forgotten_job_retold_as_a_role_stays_out(conn, monkeypatch, again):
    from types import MappingProxyType

    from kazma_core.memory.speakers import Member, Room

    room = Room(space="shared", shared=True, members=MappingProxyType({"sara": Member(account="sara", name="Sara")}))
    monkeypatch.setattr("kazma_core.memory.speakers.room", lambda _tenant: room)
    belief_extractor._apply_beliefs_to_v2(
        [{"subject": "Sara", "predicate": again, "predicate_type": "functional", "object": "dentist", "confidence": 0.95}],
        conn, None, session_id="c", turn=1, tenant_id="shared", now=1.0, private=True, user_text="Sara is a dentist",
        said_by="sara", not_again={("member_sara", "current_role", "dentist")})
    assert conn.execute("SELECT COUNT(*) FROM beliefs").fetchone()[0] == 0


def test_a_role_is_compared_never_joined():
    assert fact_names.same_statement("role_is", "current_role") and fact_names.same_statement("has_role", "job_title")
    assert fact_names.fact_kind("role") is None, "a part in a play asks no job"


@pytest.mark.parametrize(("name", "value"), [("has_job_title", "treasurer of the chess club"),
                                             ("current_occupation", "part of a volunteer team"),
                                             ("has_job_title", "أمين صندوق النادي"), ("current_occupation", "عضو في لجنة الحي")])
def test_a_club_role_never_replaces_the_users_job(conn, name, value):
    """N7: the user's "engineer" was closed by a club treasurer's title."""
    _say(conn, "user", "job_title", "engineer", 1.0)
    _say(conn, "user", name, value, 2.0)
    assert {v for _n, v in _current(conn, "user")} == {"engineer", value}


@pytest.mark.parametrize("child", ["5", "٥"])
def test_a_childs_age_never_replaces_the_users(conn, child):
    _say(conn, "user", "has_age", "32", 1.0)
    _say(conn, "user", "current_age", child, 2.0)
    assert {v for _n, v in _current(conn, "user")} == {"32", child}
    _say(conn, "user", "current_age", "33", 3.0)
    assert ("age_is", "33") in _current(conn, "user"), "control: the user's next age replaces it"


def test_an_open_reading_follows_the_fact_it_is_filed_under(conn):
    """N8: re-filed under Kazma's name, the reading's card was left under the old one."""
    _say(conn, "user", "has_profession", "engineer", 1.0, text="I am an engineer")
    conn.execute("UPDATE beliefs SET predicate = 'has_profession'")
    conn.execute("UPDATE memory_changes SET predicate = 'has_profession'")
    _say(conn, "user", "has_profession", "manager", 2.0, method="user_stated", text="I am a manager")
    assert _current(conn, "user") == [("current_role", "manager")]
    open_ = [c for c in changes.list_changes(conn, tenant_id="default")]
    assert all(c["predicate"] == "current_role" for c in open_) and all(c["state"] != "open" for c in
                                                                         changes.list_changes(conn, tenant_id="default",
                                                                                              states=("open",)))


def test_the_kinds_rows_are_read_by_their_words(conn):
    """N6: a role's name read every fact of the user's; now the rows whose names hold the kind's words."""
    import time

    rows = [(f"b{i}", "default", "user", f"likes_{i}", f"thing {i}", "set", float(i), float(i), "llm_inferred")
            for i in range(10000)]
    conn.executemany("INSERT INTO beliefs (id, tenant_id, subject, predicate, object, predicate_type, valid_from, ingested_at, "
                     "extraction_method) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    start = time.perf_counter()
    for _ in range(10):
        fact_names.name_to_file(conn, tenant_id="default", subject="user", predicate="has_occupation",
                                predicate_type="functional", value="engineer")
    assert (time.perf_counter() - start) / 10 < 0.02
