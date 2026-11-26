"""Dataset v9: the benchmark's facts under the names the product holds (the
memory plan's W6, the framework audit's F-C (5), 2026-11-26).

v8 seeded the user's role as ``job_title`` and the user's name as ``name``:
names no writer of the product keeps (``fact_names.users_names`` and
``name_to_file`` file them as ``current_role`` and ``name_is``). The benchmark
measured a store no user has, and hid a miss: under the product's names,
"what is it that I do for a living?" (q601) found nothing, because the role's
phrase asked only "do I do for a living".

From v9 ``benchmark._seed`` files a fact as the extraction pass does. Older
versions seed as they did, so they replay exactly (the control). The class:
every user fact of the dataset, held under the name a product writer gives it.
"""

from __future__ import annotations

import pytest
from kazma_core.memory import benchmark, fact_names


def _held(data) -> dict[str, tuple[str, str]]:
    with benchmark._seeded_store(data) as (conn, refs, _covers):
        back = {v: k for k, v in refs.items()}
        return {back[str(r[0])]: (str(r[1]), str(r[2])) for r in conn.execute("SELECT id, subject, predicate FROM beliefs")
                if str(r[0]) in back}


@pytest.fixture(scope="module")
def stores(monkeypatch_module):
    monkeypatch_module.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch_module.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    now = benchmark.load_dataset()
    return now, _held(now), _held(benchmark.dataset_as_of(now, 8))


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_v9_holds_the_users_role_and_name_as_the_product_does(stores):
    now, v9, _v8 = stores
    assert now["version"] >= benchmark.NAMED_AS_THE_PRODUCT_FROM == 9
    assert v9["f01"] == ("user", "name_is") and v9["f34"] == ("user", "current_role")


def test_control_v8_seeds_as_it_did(stores):
    _now, _v9, v8 = stores
    assert v8["f01"] == ("user", "name") and v8["f34"] == ("user", "job_title")


def test_every_user_fact_is_held_under_the_name_a_writer_gives_it(stores):
    """The class: each fact of the user's, under ``users_names`` and the
    pass's filing; a fact of anyone else under its own name."""
    now, v9, _v8 = stores
    for fact in now["facts"]:
        subject, name = fact_names.users_names(fact["subject"], fact["predicate"])
        kind = fact_names._one_kind(subject, name) if fact["predicate_type"] != "set" else None
        assert v9[fact["id"]] == (subject, kind or name), fact


@pytest.mark.parametrize("question", [
    "What is it that I do for a living?", "what is it that I do for a living?", "Tell me what I do for work.",
    "ما الذي أعمله لكسب عيشي؟", "شنو أسوي لكسب رزقي؟", "شنو شغلتي؟"])
def test_what_i_do_for_a_living_asks_the_role(question):
    """q601, in English, Gulf and MSA."""
    assert "current_role" in fact_names.kinds_said_of_the_user(question)


@pytest.mark.parametrize("question", [
    "What do they do for a living?", "How do I make a living online?", "كيف أكسب رزقي من الإنترنت؟",
    "What is the cost of living here?", "كم تكلفة المعيشة هنا؟",
    # The phrase itself, asking advice or another thing (step 4's review, M3).
    "What should I do for a living?", "What could I do for a living if I quit?", "What can I do for work in Dubai?",
    "What jobs can I do for a living from home?", "What would I do for a living without a degree?",
    "Is it weird that I do for a living what my dad did?",
    "ماذا يجب أن أفعل لكسب رزقي؟", "هل يكفي راتبي لكسب عيشي؟", "اقترح علي طريقة لكسب رزقي", "كيف أوفر لكسب قوتي؟",
    "شو لازم أسوي لكسب رزقي بعد التقاعد؟", "شنو أقدر أسوي لكسب رزقي؟", "شنو لازم أسوي لكسب عيشي؟"])
def test_control_a_living_not_said_of_the_user_asks_nothing(question):
    assert fact_names.kinds_said_of_the_user(question) == ()
