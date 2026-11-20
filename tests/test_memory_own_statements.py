"""A model's reading of the user's fact is put to the user, never applied
as theirs (2026-11, the memory framework audit's F1; the second security
review of it, 2026-11-18).

The quick pass reads a few exact sentence forms and files what it reads as
the user's own statement. The model's deep pass reads everything else, and
whether the model's name fits the words only the user can say: "I'm
visiting Dubai next week" read as a home, "I met Dana" read as the user's
name, "I read that Initech is hiring" read as their employer. A reading the
deep pass made was credited to the user when the user spoke of themself
(the first half of F1); the review found the tail of that rule endless --
the same turn's reading replaced the quick pass's fact ("My name is Sam. I
met Dana yesterday."), another name for the same fact ("home_city",
"located_in") stood beside the user's, and a wrong first reading took the
user's trust and then blocked the real one.

So a deep-pass reading never gets the user's trust by itself (rule A). It
is written as the model's inference, as before F1, and a reading of one of
the user's own facts whose value stands in the user's own words with no one
else named is put to the user as a pending change (``memory/changes.py``):
in place of the fact they stated, or as the first one of its kind. The
user's facts go by Kazma's names (``fact_names``), so an alias meets the
user's fact. A correction in free words lands once the user confirms it.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.memory import belief_mutation
from kazma_core.memory.clause_subject import names_no_one_else
from kazma_core.memory.schema_v2 import ensure_primary_schema

ROOT = Path(__file__).resolve().parents[1]


# ── whose words: what may be put to the user ─────────────────────────────


@pytest.mark.parametrize(("message", "value", "spelled"), [
    ("We moved to Rotterdam last month.", "rotterdam", "Rotterdam"),
    ("Moved to Rotterdam last month.", "rotterdam", "Rotterdam"),   # no one named: asked
    ("No, Lisbon", "lisbon", "Lisbon"),
    ("I left Google, I am at Meta now.", "meta", "Meta"),
    ("My name is Omar وساكن في Kuwait City.", "kuwait_city", "Kuwait City"),
    ("انتقلنا إلى الرياض الشهر الماضي.", "الرياض", "الرياض"),
    ("تركت زين، أشتغل الحين في أريدُو.", "أريدو", "أريدُو"),
    ("أنا ساكن بدبي من سنتين.", "دبي", "دبي"),
])
def test_the_users_own_words_may_be_put_to_them(message, value, spelled):
    assert names_no_one_else(message, value) == spelled


@pytest.mark.parametrize(("message", "value"), [
    ("My sister moved to Rotterdam last month.", "rotterdam"),
    ("My manager works at Meta now.", "meta"),
    ("My manager يشتغل في Meta now.", "meta"),
    ("I think Rotterdam is where she lives.", "rotterdam"),  # a third person after it
    ("Translate to French: I live in Lyon.", "lyon"),        # not the user's own words
    ("My sister said: I live in Lyon.", "lyon"),
    ("أختي انتقلت إلى روتردام.", "روتردام"),
    ("عندي أخت اسمها نورة وانتقلت إلى روتردام.", "روتردام"),
    ("I live in Paris.", "lisbon"),                          # not in the message at all
    ("I don't live in Paris.", "paris"),                     # denied
    ("I never worked at Google.", "google"),
    ("أنا ما أسكن في دبي.", "دبي"),
    ("Should I move to Rotterdam?", "rotterdam"),            # asked
    ("Do I still live in Paris", "paris"),
    ("هل أنتقل إلى دبي؟", "دبي"),
    ("How do I say my name is Pierre in French?", "pierre"),
    ("Summarize my notes on Dubai.", "dubai"),               # a request to act on a text
])
def test_anyone_elses_words_are_never_put_to_the_user(message, value):
    assert names_no_one_else(message, value) is None


# ── through the worker's own deep pass ───────────────────────────────────


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    dual_write._reset_mirror()
    yield SimpleNamespace(dir=tmp_path, db=db)
    dual_write._reset_mirror()
    db.close()


def _deep_pass(mem, monkeypatch, *, value_first: str | None, then: str, model: list[dict],
               predicate: str = "lives_in", assistant: str = "Noted.", same_turn: bool = False,
               ) -> list[tuple[str, str]]:
    """The user's own fact first (the quick pass's, ``user_stated``; none
    for *value_first* None; in the deep pass's own turn when *same_turn*),
    then the worker's micro_consolidation of turn 2, the model's reply
    written for the test. The current facts of *predicate* afterwards."""
    import time

    from kazma_core.memory import worker_bootstrap
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import _episode_id, mirror_episode

    turn_time = time.time() - 600
    if value_first is not None:
        first = sqlite3.connect(str(mem.dir / "memory_state.db"), isolation_level=None)
        mutate_belief(first, "user", predicate, value_first, now=turn_time - (0 if same_turn else 600),
                      extraction_method="user_stated", source_session="s1", source_turn=2 if same_turn else 1)
        first.close()
    mirror_episode(session_id="s1", turn_number=2, user_text=then, assistant_text=assistant,
                   created_at=turn_time)

    async def llm(user_text, assistant_text="", *, use_llm=True, ignore_filler=False, vocabulary=None,
                  entities=None, said_on=None, previous=None):
        return ([dict(b, confidence=0.9, importance=4) for b in model],
                {"skipped_filler": False, "source": "llm", "applied": 0, "rejected": 0, "actions": []})

    monkeypatch.setattr("kazma_core.memory.belief_extractor.extract_beliefs_for_turn", llm)
    assert asyncio.run(worker_bootstrap._handle_micro_consolidation(
        {"episode_id": _episode_id("s1", 2, then)})) is True
    return _facts(mem, predicate)


def _facts(mem, predicate: str) -> list[tuple[str, str]]:
    return [(r["object"], r["extraction_method"]) for r in mem.db.execute(
        "SELECT object, extraction_method FROM beliefs WHERE subject='user' AND predicate=? "
        "AND valid_until IS NULL AND invalidated_at IS NULL", (predicate,))]


def _fact(predicate: str, value: str, subject: str = "user") -> dict:
    return {"subject": subject, "predicate": predicate, "predicate_type": "functional", "object": value}


def _waiting(mem) -> list[str]:
    from kazma_core.memory import changes

    return [c["new_value"] for c in changes.list_changes(mem.db, tenant_id="default") if c["kind"] == "pending"]


def test_a_correction_only_the_model_read_waits_for_the_user(mem, monkeypatch):
    """The user's own correction in free words: the user's fact stands, and
    the correction waits for the user in their spelling."""
    assert _deep_pass(mem, monkeypatch, value_first="Paris", then="We moved to Rotterdam last month.",
                      model=[_fact("lives_in", "rotterdam")]) == [("Paris", "user_stated")]
    assert _waiting(mem) == ["Rotterdam"]


def test_a_new_job_in_arabic_waits_for_the_user(mem, monkeypatch):
    assert _deep_pass(mem, monkeypatch, value_first="شركة زين", then="تركت زين، أشتغل الحين في أريدُو.",
                      predicate="works_at", model=[_fact("works_at", "أريدو")]) == [("شركة زين", "user_stated")]
    assert _waiting(mem) == ["أريدُو"]


@pytest.mark.parametrize(("name", "kazma"), [
    ("hometown", "lives_in"), ("location", "lives_in"), ("city", "lives_in"), ("current_location", "lives_in"),
    ("address", "lives_in"), ("moved_to", "lives_in"), ("lives-in", "lives_in"), ("lives in", "lives_in"),
    ("first_name", "name_is"), ("user_name", "name_is"),
    # The main session's check, fix 4: any separator, and camel case.
    ("home town", "lives_in"), ("lives.in", "lives_in"), ("livesIn", "lives_in"),
    ("currentLocation", "lives_in"), ("Home-City", "lives_in"), ("homeTown", "lives_in"),
    ("jobTitle", "current_role"), ("works/for", "works_at"), ("Full Name", "name_is"),
])
def test_the_third_reviews_names_are_kazmas(name, kazma):
    from kazma_core.memory.fact_names import _canonical_for_user, users_predicate

    assert _canonical_for_user(name) == kazma
    assert users_predicate("User", name) == kazma and users_predicate("acme", name) == name


@pytest.mark.parametrize("name", ["homes", "homeTowns", "home towns", "Employers", "jobTitles"])
def test_a_plural_in_any_spelling_stays_a_list(name):
    from kazma_core.memory.fact_names import _canonical_for_user, plural_of_a_kind

    assert _canonical_for_user(name) == name and plural_of_a_kind(name)


def test_control_the_old_separator_rule_misses_the_spellings(monkeypatch):
    """Negative control: eef80e49c's rule (hyphens and spaces only)."""
    from kazma_core.memory import fact_names

    monkeypatch.setattr(fact_names, "_normalised", lambda predicate: "_".join(predicate.replace("-", " ").split()))
    missed = [n for n in ("home town", "lives.in", "livesIn", "currentLocation")
              if fact_names._canonical_for_user(n) == n]
    assert missed == ["lives.in", "livesIn", "currentLocation"]


def test_a_capital_user_is_the_user(mem, monkeypatch):
    """The third review, item 5: the model's "User" skipped the name map and
    the question, and its "home_city" stood beside the user's home."""
    model = [{"subject": "User", "predicate": "home_city", "predicate_type": "functional", "object": "rotterdam"}]
    assert _deep_pass(mem, monkeypatch, value_first="Paris", then="We moved to Rotterdam last month.",
                      model=model) == [("Paris", "user_stated")]
    assert _waiting(mem) == ["Rotterdam"]


def test_control_without_the_case_the_models_user_is_someone_else(mem, monkeypatch):
    # The fold and the map are one place (``fact_names``), for every writer.
    monkeypatch.setattr("kazma_core.memory.fact_names.users_names", lambda subject, predicate: (subject, predicate))
    model = [{"subject": "User", "predicate": "home_city", "predicate_type": "functional", "object": "rotterdam"}]
    _deep_pass(mem, monkeypatch, value_first="Paris", then="We moved to Rotterdam last month.", model=model)
    assert _waiting(mem) == []


def test_a_list_label_on_the_users_single_value_does_not_stand_beside_it(conn):
    """The third review, item 5: a model fact labelled ``set`` on a fact the
    user keeps single (``blood_type``) takes the user's row's class."""
    from kazma_core.memory.belief_mutation import mutate_belief

    mutate_belief(conn, "user", "blood_type", "O+", predicate_type="functional", extraction_method="user_stated",
                  source_session="s1", source_turn=1)
    mutate_belief(conn, "user", "blood_type", "A-", predicate_type="set", extraction_method="llm_inferred",
                  source_session="s1", source_turn=2)
    assert _current(conn, "blood_type") == [("O+", "user_stated")]


def test_control_without_the_users_class_the_list_value_stands_beside(conn, monkeypatch):
    from kazma_core.memory import belief_mutation

    real = belief_mutation._established_kind
    monkeypatch.setattr(belief_mutation, "_established_kind",
                        lambda c, **kw: kw["ptype"] if kw["ptype"] == "set" else real(c, **kw))
    belief_mutation.mutate_belief(conn, "user", "blood_type", "O+", predicate_type="functional",
                                  extraction_method="user_stated", source_session="s1", source_turn=1)
    belief_mutation.mutate_belief(conn, "user", "blood_type", "A-", predicate_type="set",
                                  extraction_method="llm_inferred", source_session="s1", source_turn=2)
    assert sorted(_current(conn, "blood_type")) == [("A-", "llm_inferred"), ("O+", "user_stated")]


#: A long message (past the 2,000 characters the quick pass reads, within the
#: 4,000 an episode keeps for the deep pass: ``dual_write``) that ends with
#: the user's correction.
_NOTES = "We walked along the river and looked at the old boats for a while. " * 40
LONG_CORRECTION = "Hi Kazma, my notes from the trip.\n\n" + _NOTES + "\n\nCorrection: we moved to Lisbon last month."


def test_a_correction_at_the_end_of_a_long_message_is_put_to_the_user(mem, monkeypatch):
    """Rule D of the second security review: the end of a long message was
    never read, and a correction there was lost. The model's reading of it
    is put to the user as a question; the user's fact stands until they
    answer."""
    assert 2000 < len(LONG_CORRECTION) < 4000
    assert _deep_pass(mem, monkeypatch, value_first="Paris", then=LONG_CORRECTION,
                      model=[_fact("lives_in", "lisbon")]) == [("Paris", "user_stated")]
    assert _waiting(mem) == ["Lisbon"]


def test_the_end_after_a_forwarded_message_is_not_put_to_the_user(mem, monkeypatch):
    forwarded = ("---------- Forwarded message ---------\nFrom: Dana\n\n" + _NOTES
                 + "\n\nWe moved to Lisbon last month.")
    assert _deep_pass(mem, monkeypatch, value_first="Paris", then=forwarded,
                      model=[_fact("lives_in", "lisbon")]) == [("Paris", "user_stated")]
    assert _waiting(mem) == []


def test_control_without_reading_the_end_the_correction_is_lost(mem, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.own_words.last_words", lambda text: "")
    assert _deep_pass(mem, monkeypatch, value_first="Paris", then=LONG_CORRECTION,
                      model=[_fact("lives_in", "lisbon")]) == [("Paris", "user_stated")]
    assert _waiting(mem) == []


@pytest.mark.parametrize(("then", "model", "spelled"), [
    ("We moved to Rotterdam last month.", "rotterdam", "Rotterdam"),
    ("انتقلنا إلى الرياض الشهر الماضي.", "الرياض", "الرياض"),
])
def test_a_first_reading_is_the_models_and_asks_nothing(mem, monkeypatch, then, model, spelled):
    """With no fact of the user's, the reading is written as the model's
    inference and shown on the Memory page as Kazma's reading -- never a
    question. "This is right" makes it the user's."""
    from kazma_core.memory import changes

    assert _deep_pass(mem, monkeypatch, value_first=None, then=then,
                      model=[_fact("lives_in", model)]) == [(model, "llm_inferred")]
    assert _waiting(mem) == []
    [reading] = changes.list_changes(mem.db, tenant_id="default")
    assert (reading["kind"], reading["new_value"]) == ("reading", spelled)
    changes.confirm(mem.db, reading["id"], tenant_id="default", private=True)
    assert _facts(mem, "lives_in") == [(spelled, "user_explicit")]


def test_not_me_takes_the_reading_out_and_it_is_never_filed_again(mem, monkeypatch):
    from kazma_core.memory import changes

    _deep_pass(mem, monkeypatch, value_first=None, then="I'm visiting Dubai next week.",
               model=[_fact("lives_in", "dubai")])
    [reading] = changes.list_changes(mem.db, tenant_id="default")
    assert changes.dismiss(mem.db, reading["id"], tenant_id="default")["taken_out"]
    assert _facts(mem, "lives_in") == []
    _deep_pass(mem, monkeypatch, value_first=None, then="Back in Dubai for the week.",
               model=[_fact("home_city", "Dubai")])
    assert _facts(mem, "lives_in") == []
    assert changes.list_changes(mem.db, tenant_id="default") == []


def test_not_me_never_stops_the_users_own_words(mem, monkeypatch):
    from kazma_core.memory import changes
    from kazma_core.memory.belief_mutation import mutate_belief

    _deep_pass(mem, monkeypatch, value_first=None, then="I'm visiting Dubai next week.",
               model=[_fact("lives_in", "dubai")])
    [reading] = changes.list_changes(mem.db, tenant_id="default")
    changes.dismiss(mem.db, reading["id"], tenant_id="default")
    conn = sqlite3.connect(str(mem.dir / "memory_state.db"), isolation_level=None)
    mutate_belief(conn, "user", "lives_in", "Dubai", extraction_method="user_stated", source_session="s1",
                  source_turn=5)
    conn.close()
    assert _facts(mem, "lives_in") == [("Dubai", "user_stated")]


#: Five facts the deep pass files about a user who gave none: on most turns
#: it files several. None of them is a question.
FIVE_FACTS = [_fact("lives_in", "rotterdam"), _fact("works_at", "initech"), _fact("current_role", "nurse"),
              _fact("prefers", "black coffee"), _fact("active_project", "garden shed")]
FIVE_SAID = ("We moved to Rotterdam last month; I'm a nurse at Initech now, I love black coffee and I'm "
             "building a garden shed.")


def _questions(mem) -> int:
    from kazma_core.memory import changes

    note = changes.format_changes_note([c for c in changes.list_changes(mem.db, tenant_id="default")
                                        if c["kind"] == "pending"])
    return note.count("waiting for the user")


def test_a_turn_of_five_first_readings_asks_nothing(mem, monkeypatch):
    _deep_pass(mem, monkeypatch, value_first=None, then=FIVE_SAID, model=FIVE_FACTS)
    assert _facts(mem, "lives_in") == [("rotterdam", "llm_inferred")]
    assert _waiting(mem) == [] and _questions(mem) == 0


def test_control_asking_about_every_first_reading_questions_the_user(mem, monkeypatch):
    """Negative control: every first reading recorded as a question -- the
    rule this replaced."""
    from kazma_core.memory import changes

    real = changes._insert

    def as_a_question(conn, **kw):
        return real(conn, **{**kw, "kind": changes.PENDING})

    monkeypatch.setattr(changes, "_insert", as_a_question)
    monkeypatch.setattr(changes, "_reading_names",
                        lambda: frozenset({"lives_in", "works_at", "current_role", "prefers", "active_project"}))
    _deep_pass(mem, monkeypatch, value_first=None, then=FIVE_SAID, model=FIVE_FACTS)
    assert _questions(mem) >= 3


def test_a_wrong_first_reading_never_blocks_the_users_own_words(mem, monkeypatch):
    """The review's finding 5: "I'm visiting Dubai" read as a home, then the
    user's own "I live in Kuwait": the user's words replace the model's, and
    the question waiting on it is answered."""
    from kazma_core.memory.belief_mutation import mutate_belief

    _deep_pass(mem, monkeypatch, value_first=None, then="I'm visiting Dubai next week.",
               model=[_fact("lives_in", "dubai")])
    conn = sqlite3.connect(str(mem.dir / "memory_state.db"), isolation_level=None)
    mutate_belief(conn, "user", "lives_in", "Kuwait", extraction_method="user_stated", source_session="s1",
                  source_turn=3)
    conn.close()
    assert _facts(mem, "lives_in") == [("Kuwait", "user_stated")]
    assert _waiting(mem) == []


@pytest.mark.parametrize("alias", ["home_city", "located_in", "resides_in", "city_home", "current_city"])
def test_another_name_for_the_users_fact_meets_it(mem, monkeypatch, alias):
    """The review's finding 4: the model's other name for the user's home
    stood beside it, and "adds, never replaces" compared one name only."""
    assert _deep_pass(mem, monkeypatch, value_first="Kuwait", then="We moved to Dubai last month.",
                      model=[_fact(alias, "dubai")]) == [("Kuwait", "user_stated")]
    assert _facts(mem, alias) == []
    assert _waiting(mem) == ["Dubai"]


def test_a_plural_alias_is_a_list_of_its_own(mem, monkeypatch):
    _deep_pass(mem, monkeypatch, value_first="Kuwait", then="We have homes in Dubai and Oman.",
               model=[_fact("homes", "dubai"), _fact("homes", "oman")])
    assert _facts(mem, "lives_in") == [("Kuwait", "user_stated")]
    assert sorted(_facts(mem, "homes")) == [("dubai", "llm_inferred"), ("oman", "llm_inferred")]
    assert belief_mutation._classify_predicate("employers", "functional") == "set"


#: The review of F1's first half (2026-11-18): each of these replaced the
#: user's own fact. (predicate, the user's value, the later message, the
#: model's reading, whether it is asked about: a statement is, a denial, a
#: question or a request is not.)
MODEL_READINGS = [
    ("lives_in", "Kuwait", "I'm visiting Dubai next week", "dubai", True),
    ("lives_in", "Kuwait", "I never lived in Paris.", "paris", False),
    ("lives_in", "Kuwait", "I think Rotterdam is where Dana lives.", "rotterdam", True),
    ("name_is", "Sam", "I met Dana yesterday.", "dana", True),
    ("works_at", "Acme", "I read that Initech is hiring.", "initech", True),
    ("lives_in", "الكويت", "أنا رايح دبي الأسبوع الجاي", "دبي", True),
    ("lives_in", "Kuwait", "Should I move to Rotterdam?", "rotterdam", False),
    ("lives_in", "الكويت", "أنا ما أسكن في دبي.", "دبي", False),
]


@pytest.mark.parametrize(("predicate", "held", "then", "read", "asked"), MODEL_READINGS)
def test_a_reading_only_the_model_made_never_replaces_the_users_fact(mem, monkeypatch, predicate, held, then,
                                                                    read, asked):
    assert _deep_pass(mem, monkeypatch, value_first=held, then=then, predicate=predicate,
                      model=[_fact(predicate, read)]) == [(held, "user_stated")]
    assert bool(_waiting(mem)) is asked


def test_the_same_turns_reading_never_replaces_the_quick_pass(mem, monkeypatch):
    """The review's finding 1: "My name is Sam. I met Dana yesterday." -- the
    quick pass reads Sam, a careless model reads Dana in the same turn. The
    user's words of a moment ago stand, and nothing is asked."""
    assert _deep_pass(mem, monkeypatch, value_first="Sam", then="My name is Sam. I met Dana yesterday.",
                      predicate="name_is", model=[_fact("name_is", "dana")], same_turn=True) == [
        ("Sam", "user_stated")]
    assert _waiting(mem) == []


def test_a_summarised_page_cannot_steer_the_users_fact(mem, monkeypatch):
    """The deep pass reads the reply too: a page Kazma summarised, read as
    the user's facts, changes nothing and asks nothing."""
    assert _deep_pass(mem, monkeypatch, value_first="Kuwait", then="Summarize this page about the expo for me.",
                      assistant="The page says the organiser lives in Dubai and works at Emaar.",
                      model=[_fact("lives_in", "Dubai")]) == [("Kuwait", "user_stated")]
    assert _waiting(mem) == []


@pytest.mark.parametrize("then", ["My sister moved to Rotterdam last month.", "أختي انتقلت إلى روتردام."])
def test_someone_elses_move_filed_under_the_user_changes_nothing(mem, monkeypatch, then):
    """The model files it under the user, as a careless model would."""
    value = "rotterdam" if then.startswith("My") else "روتردام"
    assert _deep_pass(mem, monkeypatch, value_first="Paris", then=then,
                      model=[_fact("lives_in", value)]) == [("Paris", "user_stated")]
    assert _waiting(mem) == []


def test_a_managers_job_filed_under_the_user_changes_nothing(mem, monkeypatch):
    assert _deep_pass(mem, monkeypatch, value_first="Google", then="My manager يشتغل في Meta now.",
                      predicate="works_at", model=[_fact("works_at", "Meta")]) == [("Google", "user_stated")]


# ── negative controls ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("predicate", "held", "then", "read", "asked"), MODEL_READINGS)
def test_control_a_reading_at_the_users_trust_replaced_each(mem, monkeypatch, predicate, held, then, read, asked):
    """Every deep-pass reading at the user's trust (what crediting gave a
    reading the rule took for the user's): each replaced the user's fact."""
    monkeypatch.setattr("kazma_core.memory.belief_extractor._default_method", lambda stats: "user_stated")
    current = _deep_pass(mem, monkeypatch, value_first=held, then=then, predicate=predicate,
                         model=[_fact(predicate, read)])
    assert current != [(held, "user_stated")]


def test_control_without_the_name_table_another_name_stands_beside(mem, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.fact_names._canonical_for_user", lambda predicate: predicate)
    _deep_pass(mem, monkeypatch, value_first="Kuwait", then="We moved to Dubai last month.",
               model=[_fact("home_city", "dubai")])
    assert _facts(mem, "lives_in") == [("Kuwait", "user_stated")] and _facts(mem, "home_city") == [
        ("dubai", "llm_inferred")]
    assert _waiting(mem) == []


def test_control_without_not_me_the_value_is_filed_again(mem, monkeypatch):
    from kazma_core.memory import changes

    _deep_pass(mem, monkeypatch, value_first=None, then="I'm visiting Dubai next week.",
               model=[_fact("lives_in", "dubai")])
    [reading] = changes.list_changes(mem.db, tenant_id="default")
    changes.dismiss(mem.db, reading["id"], tenant_id="default")
    monkeypatch.setattr(changes, "user_said_not_me", lambda conn, **kw: False)
    _deep_pass(mem, monkeypatch, value_first=None, then="Back in Dubai for the week.",
               model=[_fact("lives_in", "dubai")])
    assert _facts(mem, "lives_in") == [("dubai", "llm_inferred")]


# ── one value written two ways ───────────────────────────────────────────


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    c = sqlite3.connect(str(tmp_path / "memory_state.db"), isolation_level=None)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    yield c
    c.close()


def _current(conn, predicate: str) -> list[tuple[str, str]]:
    return [(r["object"], r["extraction_method"]) for r in conn.execute(
        "SELECT object, extraction_method FROM beliefs WHERE predicate=? AND valid_until IS NULL "
        "AND invalidated_at IS NULL", (predicate,))]


@pytest.mark.parametrize(("said", "respelled", "predicate"), [
    ("Lisbon", "lisbon", "lives_in"), ("Gulf Bank", "gulf_bank", "works_at"),
    ("Abu Faisal", "abu_faisal", "name_is"), ("أريدُو", "أريدو", "works_at"), ("الكويت", "الكويت ", "lives_in"),
])
def test_a_respelling_in_the_same_turn_leaves_the_users_word(conn, said, respelled, predicate):
    write = belief_mutation.mutate_belief
    write(conn, "user", predicate, said, extraction_method="user_stated", source_session="s", source_turn=1,
          now=1000.0, private=True)
    result = write(conn, "user", predicate, respelled, extraction_method="llm_inferred", source_session="s",
                   source_turn=1, now=1000.0, private=True)
    assert result["action"] == "noop"
    assert _current(conn, predicate) == [(said, "user_stated")]


def test_the_users_word_for_the_models_reading_makes_it_theirs(conn):
    """The same value from the user raises the model's reading to the user's
    trust, in the user's spelling (a confirmed first reading)."""
    write = belief_mutation.mutate_belief
    write(conn, "user", "lives_in", "rotterdam", extraction_method="llm_inferred", now=1000.0, private=True)
    write(conn, "user", "lives_in", "Rotterdam", extraction_method="user_explicit", now=1000.0, private=True)
    assert _current(conn, "lives_in") == [("Rotterdam", "user_explicit")]
    write(conn, "user", "lives_in", "ROTTERDAM", extraction_method="llm_inferred", now=1000.0, private=True)
    assert _current(conn, "lives_in") == [("Rotterdam", "user_explicit")]


def test_letter_for_letter_the_model_takes_the_users_fact(conn, monkeypatch):
    """Negative control: the old comparison."""
    monkeypatch.setattr(belief_mutation, "_same_value", lambda old, new: old == new)
    monkeypatch.setattr("kazma_core.memory.provenance.may_overrule", lambda existing, incoming: True)
    write = belief_mutation.mutate_belief
    write(conn, "user", "lives_in", "Lisbon", extraction_method="user_stated", source_session="s",
          source_turn=1, now=1000.0, private=True)
    write(conn, "user", "lives_in", "lisbon", extraction_method="llm_inferred", source_session="s",
          source_turn=1, now=1000.0, private=True)
    assert _current(conn, "lives_in") == [("lisbon", "llm_inferred")]


# ── every deep-pass write carries its message ────────────────────────────

#: The functions that apply extracted facts, and the wrappers that forward
#: their caller's keywords (``**kw``) to them, which may not name it.
_APPLIERS = {"_apply_beliefs_to_v2", "_apply_if_still_remembered", "_apply_on_shareable_connections"}
_FORWARDERS = {("kazma-core/kazma_core/memory/belief_extractor.py", "_apply_on_shareable_connections"),
               ("kazma-core/kazma_core/memory/worker_bootstrap.py", "_apply_if_still_remembered")}


def _unnamed_calls(path: str, source: str) -> list[str]:
    tree = ast.parse(source)
    out: list[str] = []

    def walk(node: ast.AST, inside: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else inside
            if isinstance(child, ast.Call):
                func = child.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                applier = called in _APPLIERS or any(
                    isinstance(a, ast.Name) and a.id in _APPLIERS for a in child.args[:1])
                if applier and (path, inside) not in _FORWARDERS \
                        and not any(k.arg == "user_text" for k in child.keywords):
                    out.append(f"{path}:{child.lineno} {inside}")
            walk(child, name)

    walk(tree, "")
    return out


def _product_sources() -> dict[str, str]:
    import subprocess

    listed = subprocess.run(["git", "ls-files", "kazma-*", "scripts"], cwd=ROOT, capture_output=True, text=True,
                            check=True).stdout.split()
    return {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in listed
            if rel.endswith(".py") and "/tests/" not in rel}


def test_every_deep_pass_write_names_its_message():
    found = [line for path, source in _product_sources().items() if any(a in source for a in _APPLIERS)
             for line in _unnamed_calls(path, source)]
    assert not found, ("a call that applies extracted facts must pass user_text=: without the message a "
                       "reading of the user's fact is never put to them:\n  " + "\n  ".join(found))


def test_the_gate_sees_a_call_without_the_message():
    planted = ("def f(raw, p, o):\n"
               "    _apply_beliefs_to_v2(raw, p, o, stats={})\n"
               "    asyncio.to_thread(_apply_if_still_remembered, raw, p, o, user_text='x')\n")
    assert _unnamed_calls("kazma-core/kazma_core/memory/x.py", planted) == ["kazma-core/kazma_core/memory/x.py:2 f"]
