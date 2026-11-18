"""A user's correction is the user's own word, whichever pass read it
(2026-11, the memory framework audit's F1, first half).

The quick pass reads a few sentence forms and files what it reads as the
user's own statement; the model's deep pass filed everything as an
inference, and an inference may not replace what the user said. So "I live
in Paris", then "We moved to Rotterdam last month" (a form only the model
reads) left Paris current for good, and "I left Google, I am at Meta now"
left Google. And because the "same value" test compared letter for letter,
the model writing the user's own words in other letters ("lisbon") replaced
the user's fact in its own turn, at the model's trust.

Now trust follows who is speaking about themself: a fact the model read
about the user is the user's own statement, in the user's spelling, when
the user says it of themself in their own words
(``clause_subject.speaks_of_self``: never a quote or an attached document,
the nearest person named before the value is the speaker, and the sentence
states it -- no denial before it, no question, no request). "My sister
moved to Rotterdam" stays an inference whatever the model files it under.
And one value written two ways is no change.

Credited so, a reading may ADD the user's fact where the user holds none,
and never REPLACE one (the review of this half, 2026-11-18): Kazma cannot
tell without a model whether the model's name fits the words -- "I'm
visiting Dubai next week" read as lives_in, "I met Dana" as name_is -- so
the user's fact stands and the correction waits for the user
(``memory/changes.py``). A replacement comes from the quick pass's exact
shapes, or from the user's answer.
"""

from __future__ import annotations

import ast
import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.memory import belief_mutation
from kazma_core.memory.clause_subject import speaks_of_self
from kazma_core.memory.schema_v2 import ensure_primary_schema

ROOT = Path(__file__).resolve().parents[1]


# ── who says it ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("message", "value", "spelled"), [
    ("We moved to Rotterdam last month.", "rotterdam", "Rotterdam"),
    ("I left Google, I am at Meta now.", "meta", "Meta"),
    ("My name is Layla and I live in Lisbon now.", "lisbon", "Lisbon"),
    ("My name is Layla and I live in Lisbon now.", "layla", "Layla"),
    ("My name is Omar وساكن في Kuwait City.", "kuwait_city", "Kuwait City"),
    ("My sister lives in Lyon and I live in Lyon too.", "lyon", "Lyon"),
    ("انتقلنا إلى الرياض الشهر الماضي.", "الرياض", "الرياض"),
    ("تركت زين، أشتغل الحين في أريدُو.", "أريدو", "أريدُو"),
    ("أنا ساكن بدبي من سنتين.", "دبي", "دبي"),
    ("اسمي ليلى.", "ليلى", "ليلى"),
])
def test_the_user_says_it_of_themself(message, value, spelled):
    assert speaks_of_self(message, value) == spelled


@pytest.mark.parametrize(("message", "value"), [
    ("My sister moved to Rotterdam last month.", "rotterdam"),
    ("My manager works at Meta now.", "meta"),
    ("My manager يشتغل في Meta now.", "meta"),
    ("Moved to Rotterdam last month.", "rotterdam"),        # no one named: an inference
    ("No, Lisbon", "lisbon"),
    ("I think Rotterdam is where she lives.", "rotterdam"),  # a third person after it
    ("Translate to French: I live in Lyon.", "lyon"),        # not the user's own words
    ("My sister said: I live in Lyon.", "lyon"),
    ("أختي انتقلت إلى روتردام.", "روتردام"),
    ("عندي أخت اسمها نورة وانتقلت إلى روتردام.", "روتردام"),
    ("اشتغل في زين", "زين"),                                  # no hamza: also the imperative
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
def test_anyone_else_or_no_one_is_not_the_user(message, value):
    assert speaks_of_self(message, value) is None


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


def _deep_pass(mem, monkeypatch, *, said_first: str, value_first: str | None, then: str, model: list[dict],
               predicate: str = "lives_in", assistant: str = "Noted.") -> list[tuple[str, str]]:
    """The user's first statement (the quick pass's, ``user_stated``; none
    for *value_first* None), then the worker's micro_consolidation of a later
    turn, the model's reply written for the test. The current facts of
    *predicate* afterwards."""
    import time

    from kazma_core.memory import worker_bootstrap
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import _episode_id, mirror_episode

    turn_time = time.time() - 600
    if value_first is not None:
        first = sqlite3.connect(str(mem.dir / "memory_state.db"), isolation_level=None)
        mutate_belief(first, "user", predicate, value_first, now=turn_time - 600,
                      extraction_method="user_stated", source_session="s1", source_turn=1)
        first.close()
    mirror_episode(session_id="s1", turn_number=2, user_text=then, assistant_text=assistant,
                   created_at=turn_time)

    async def llm(user_text, assistant_text="", *, use_llm=True, ignore_filler=False, vocabulary=None,
                  entities=None):
        return ([dict(b, confidence=0.9, importance=4) for b in model],
                {"skipped_filler": False, "source": "llm", "applied": 0, "rejected": 0, "actions": []})

    monkeypatch.setattr("kazma_core.memory.belief_extractor.extract_beliefs_for_turn", llm)
    assert asyncio.run(worker_bootstrap._handle_micro_consolidation(
        {"episode_id": _episode_id("s1", 2, then)})) is True
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
    the correction waits for the user in their spelling (memory/changes.py)."""
    assert _deep_pass(mem, monkeypatch, said_first="I live in Paris.", value_first="Paris",
                      then="We moved to Rotterdam last month.",
                      model=[_fact("lives_in", "rotterdam")]) == [("Paris", "user_stated")]
    assert _waiting(mem) == ["Rotterdam"]


def test_a_new_job_in_arabic_waits_for_the_user(mem, monkeypatch):
    assert _deep_pass(mem, monkeypatch, said_first="أشتغل في شركة زين.", value_first="شركة زين",
                      then="تركت زين، أشتغل الحين في أريدُو.", predicate="works_at",
                      model=[_fact("works_at", "أريدو")]) == [("شركة زين", "user_stated")]
    assert _waiting(mem) == ["أريدُو"]


@pytest.mark.parametrize(("then", "model", "spelled"), [
    ("We moved to Rotterdam last month.", "rotterdam", "Rotterdam"),
    ("انتقلنا إلى الرياض الشهر الماضي.", "الرياض", "الرياض"),
])
def test_where_the_user_holds_none_a_credited_reading_adds_theirs(mem, monkeypatch, then, model, spelled):
    assert _deep_pass(mem, monkeypatch, said_first="", value_first=None, then=then,
                      model=[_fact("lives_in", model)]) == [(spelled, "user_stated")]
    assert _waiting(mem) == []


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
    assert _deep_pass(mem, monkeypatch, said_first="", value_first=held, then=then, predicate=predicate,
                      model=[_fact(predicate, read)]) == [(held, "user_stated")]
    assert bool(_waiting(mem)) is asked


def test_a_summarised_page_cannot_steer_the_users_fact(mem, monkeypatch):
    """The deep pass reads the reply too: a page Kazma summarised, read as
    the user's facts, changes nothing and asks nothing."""
    assert _deep_pass(mem, monkeypatch, said_first="", value_first="Kuwait",
                      then="Summarize this page about the expo for me.",
                      assistant="The page says the organiser lives in Dubai and works at Emaar.",
                      model=[_fact("lives_in", "Dubai")]) == [("Kuwait", "user_stated")]
    assert _waiting(mem) == []


@pytest.mark.parametrize(("predicate", "held", "then", "read", "asked"), MODEL_READINGS)
def test_control_the_first_halfs_rule_replaced_each(mem, monkeypatch, predicate, held, then, read, asked):
    """Negative control: credited readings that may replace, and every
    sentence read as stated (the rule of bbfa48cb8). Each replaced the
    user's fact."""
    from kazma_core.memory import clause_subject

    monkeypatch.setattr(belief_mutation, "_user_holds_another", lambda *a, **k: False)
    monkeypatch.setattr(clause_subject, "_asserted", lambda raw: [True] * len(raw))
    current = _deep_pass(mem, monkeypatch, said_first="", value_first=held, then=then, predicate=predicate,
                         model=[_fact(predicate, read)])
    assert current != [(held, "user_stated")] and current[0][1] == "user_stated"


@pytest.mark.parametrize("then", ["My sister moved to Rotterdam last month.", "أختي انتقلت إلى روتردام."])
def test_someone_elses_move_filed_under_the_user_changes_nothing(mem, monkeypatch, then):
    """The model files it under the user, as a careless model would."""
    value = "rotterdam" if then.startswith("My") else "روتردام"
    assert _deep_pass(mem, monkeypatch, said_first="I live in Paris.", value_first="Paris", then=then,
                      model=[_fact("lives_in", value)]) == [("Paris", "user_stated")]


def test_a_managers_job_filed_under_the_user_changes_nothing(mem, monkeypatch):
    assert _deep_pass(mem, monkeypatch, said_first="I work at Google.", value_first="Google",
                      then="My manager يشتغل في Meta now.", predicate="works_at",
                      model=[_fact("works_at", "Meta")]) == [("Google", "user_stated")]


def test_without_the_grading_the_correction_is_refused(mem, monkeypatch):
    """Negative control: every deep-pass fact an inference, as before."""
    monkeypatch.setattr("kazma_core.memory.clause_subject.speaks_of_self", lambda message, value: None)
    assert _deep_pass(mem, monkeypatch, said_first="I live in Paris.", value_first="Paris",
                      then="We moved to Rotterdam last month.",
                      model=[_fact("lives_in", "rotterdam")]) == [("Paris", "user_stated")]


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


def test_letter_for_letter_the_model_takes_the_users_fact(conn, monkeypatch):
    """Negative control: the old comparison."""
    monkeypatch.setattr(belief_mutation, "_same_value", lambda old, new: old == new)
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
                       "fact the user said of themself is filed as an inference:\n  " + "\n  ".join(found))


def test_the_gate_sees_a_call_without_the_message():
    planted = ("def f(raw, p, o):\n"
               "    _apply_beliefs_to_v2(raw, p, o, stats={})\n"
               "    asyncio.to_thread(_apply_if_still_remembered, raw, p, o, user_text='x')\n")
    assert _unnamed_calls("kazma-core/kazma_core/memory/x.py", planted) == ["kazma-core/kazma_core/memory/x.py:2 f"]
