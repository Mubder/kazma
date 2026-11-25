"""One role for one person, and a list read by its kind (the memory plan's W6, step 2).

A model names a fact freely. Another person's job came under "occupation",
"profession_is" and "works_as" (recorded replies, one turn asked five times),
and a changed job under another name left both current; the user's role came
under names the table missed (34 of 79 in the pilots' stores). ``fact_names``
reads the role's names as one where the reply gives ONE value, and files
another person's role or employer under the name that person already has.

What it must not do is as much of it: two people's values, two accounts'
resets, a company's two places, a person's four occupations, a part in a play
and the user's own word all stay. Today's table is the negative control.

The class: every statement in ``memory/`` that reads facts by one exact name
is declared here with why one name is right for it.
"""

from __future__ import annotations

import ast
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest
from kazma_core.memory import fact_names
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


def _say(conn, subject, predicate, value, at, *, kind="functional", method="llm_inferred"):
    """A fact as the deep pass files it."""
    subject, name = fact_names.users_names(subject, predicate)
    return mutate_belief(conn, subject, name, value, predicate_type=kind, extraction_method=method, now=at,
                         private=True, tenant_id="default")


def _current(conn, subject):
    return sorted((r[0], r[1]) for r in conn.execute(
        "SELECT predicate, object FROM beliefs WHERE subject = ? AND valid_until IS NULL AND invalidated_at IS NULL",
        (subject,)))


def _past(conn, subject):
    return sorted(r[0] for r in conn.execute(
        "SELECT object FROM beliefs WHERE subject = ? AND valid_until IS NOT NULL", (subject,)))


#: A person's job told twice, then a second time under another name.
JOBS = [("noura", "dentist", "surgeon"), ("نورة", "طبيبة أسنان", "جراحة"), ("هنا", "مهندسة", "مديرة مشروع")]


@pytest.mark.parametrize(("who", "old", "new"), JOBS)
@pytest.mark.parametrize("names", [("occupation", "job_title"), ("works_as", "profession_is"), ("profession", "occupation_is"),
                                   ("role_is", "works_as"), ("job_title", "has_occupation"), ("current_role", "profession")])
def test_a_persons_changed_job_is_one_current_value(conn, who, old, new, names):
    _say(conn, who, names[0], old, 1.0)
    _say(conn, who, names[1], new, 2.0)
    assert _current(conn, who) == [(names[0], new)] and _past(conn, who) == [old]


@pytest.mark.parametrize(("who", "old", "new"), JOBS)
def test_three_names_and_the_old_ones_are_history(conn, who, old, new):
    _say(conn, who, "occupation", old, 1.0)
    _say(conn, who, "profession_is", "x1", 2.0)
    _say(conn, who, "works_as", new, 3.0, kind="state")
    assert _current(conn, who) == [("occupation", new)] and _past(conn, who) == sorted(["x1", old])


@pytest.mark.parametrize(("who", "old", "new"), [("sara", "Google", "Initech"), ("سارة", "بنك الخليج", "أكمي")])
def test_a_persons_employer_too(conn, who, old, new):
    _say(conn, who, "works_for", old, 1.0)
    _say(conn, who, "employer", new, 2.0)
    assert _current(conn, who) == [("works_for", new)]


@pytest.mark.parametrize(("who", "old", "new"), JOBS)
def test_control_todays_table_keeps_both(conn, monkeypatch, who, old, new):
    monkeypatch.setattr(fact_names, "name_in_use", lambda conn, *, predicate, **kw: predicate)
    _say(conn, who, "occupation", old, 1.0)
    _say(conn, who, "job_title", new, 2.0)
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
    """Under the held name the trust gate would drop the reading: it is kept under its own."""
    _say(conn, who, "occupation", held, 1.0, method="user_explicit")
    _say(conn, who, "job_title", read, 2.0)
    assert _current(conn, who) == [("job_title", read), ("occupation", held)]
    _say(conn, who, "profession", read, 3.0, method="user_stated")
    assert ("job_title", read) in _current(conn, who)


def test_an_earlier_statement_never_becomes_current(conn):
    _say(conn, "noura", "occupation", "surgeon", 5.0)
    _say(conn, "noura", "job_title", "dentist", 1.0)
    assert _current(conn, "noura") == [("occupation", "surgeon")]


# ── the user ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("name", "old", "new"), [
    ("has_occupation", "engineer", "manager"), ("role_is", "محاسب", "مدير"), ("has_job", "nurse", "teacher"),
    ("current_job_is", "ممرض", "مدرس"), ("has_profession", "a dentist", "a surgeon"), ("employed_as", "طباخ", "سائق")])
def test_the_users_one_role_is_current_role(conn, name, old, new):
    _say(conn, "user", "job_title", old, 1.0)
    _say(conn, "user", name, new, 2.0)
    assert _current(conn, "user") == [("current_role", new)] and _past(conn, "user") == [old]


@pytest.mark.parametrize("name", ["has_occupation", "has_job", "role_is"])
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


@pytest.mark.parametrize("value", ["Éponine in the musical", "دور جولييت في المسرحية"])
def test_a_part_is_no_job(conn, value):
    _say(conn, "user", "job_title", "engineer", 1.0)
    _say(conn, "user", "has_role", value, 2.0)
    assert len(_current(conn, "user")) == 2


def test_a_role_held_under_a_name_from_before_the_table_is_still_replaced(conn):
    """Written before ``has_occupation`` was read as ``current_role``: the new
    value supersedes it there, and the trust gate still decides."""
    _say(conn, "user", "has_occupation", "engineer", 1.0, method="user_stated")
    conn.execute("UPDATE beliefs SET predicate = 'has_occupation'")
    refused = _say(conn, "user", "has_occupation", "manager", 2.0)
    assert refused["blocked"] == "lower_trust_source" and _current(conn, "user") == [("has_occupation", "engineer")]
    _say(conn, "user", "job_title", "manager", 3.0, method="user_stated")
    assert _current(conn, "user") == [("has_occupation", "manager")]


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
    ("belief_mutation.py", "_mutate_functional_locked"): "the value under the name canonical_predicate and name_in_use chose",
    ("belief_mutation.py", "_record_earlier_statement"): "the history of that name",
    ("belief_mutation.py", "_entry_told_again"): "an entry told again under its own name",
    ("changes.py", "_recorded"): "a change is recorded under Kazma's name",
    ("changes.py", "_to_tell"): "a change is recorded under Kazma's name",
    ("changes.py", "_held_again"): "the entries of the list the undone write closed",
    ("forget.py", "_reopen_replaced"): "the entry that took a closed one's place has its name",
    ("list_reopen.py", "find_closed_entries"): "entries closed under that one name",
    ("recall.py", "_occasions"): "the other occasions of one event",
    ("held_facts.py", "held_facts"): "leaves out the hub's own link, by its fixed name",
}
_BY_NAME = re.compile(r"\bFROM\s+(?:beliefs|memory_changes)\b.*\bpredicate\s*=\s*\?", re.IGNORECASE | re.DOTALL)


def _readers(source: str) -> set[str]:
    """The functions of *source* with a statement that reads facts by one name."""
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            nested = {id(x) for f in ast.walk(node) if f is not node and isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                      for x in ast.walk(f)}
            for call in ast.walk(node):
                if isinstance(call, (ast.Constant, ast.JoinedStr)) and id(call) not in nested:
                    parts = [call] if isinstance(call, ast.Constant) else call.values
                    text = "".join(str(p.value) for p in parts if isinstance(p, ast.Constant))
                    if _BY_NAME.search(text):
                        out.add(node.name)
    return out


def test_every_reader_of_facts_by_one_name_is_declared():
    files = subprocess.run(["git", "ls-files", "kazma-core/kazma_core/memory/*.py"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    found = {(Path(f).name, name) for f in files for name in _readers((ROOT / f).read_text(encoding="utf-8"))
             if "benchmark" not in f}
    assert found - set(ONE_NAME) == set(), "read by the kind (fact_names.list_kind), or declare why one name is right"
    assert set(ONE_NAME) - found == set(), "declared and gone"


def test_control_a_list_read_by_one_name_is_found():
    old = '''def _list_siblings(conn):
    conn.execute("SELECT id FROM beliefs WHERE tenant_id = ? AND subject = ? AND predicate = ? "
                 f"AND {X} ORDER BY valid_from", ())
'''
    assert _readers(old) == {"_list_siblings"}


def test_every_kind_that_is_asked_is_some_names_kind():
    """Each key of the tables is a kind a name of the model's is read as, or a
    Kazma name; and has words in both languages (``ASKED_WITH``)."""
    names = ["has_goal", "plans_to", "interested_in", "bought", "received", "met", "attended", "finished", "has_routine",
             "dietary_restriction", "doctor", "age_is", "birthday_is", "enrolled_in", "drives", "has_daughter",
             "has_son", "favorite_color", "lives_in", "works_at", "current_role", "name_is", "preferred_name",
             "allergic_to"]
    kinds = {fact_names.fact_kind(n) for n in names}
    assert kinds == set(fact_names.ASKED_WITH) | set(fact_names.ASKED_ALONE)
    assert set(fact_names.LISTS) <= kinds and set(fact_names._FEW) <= kinds
    assert set(fact_names._INDEXED) <= kinds
    for key, words in {**fact_names.ASKED_WITH, **fact_names.ASKED_ALONE}.items():
        assert any(w.isascii() for w in words) and any(not w.isascii() for w in words), key
    assert fact_names.fact_kind("owns_boat") is None  # control
