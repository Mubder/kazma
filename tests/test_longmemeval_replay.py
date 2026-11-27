"""The facts pilot's stores written again from its kept replies
(``scripts/longmemeval_replay.py``, the memory plan's W6 step 5), on a small
history of our own in LongMemEval's shape: no dataset text, no model call.

* The pilot's ``extract`` keeps a map a question: each deep pass's reply and
  the facts it was shown.
* Replayed through the write path, the store holds what the extraction wrote
  -- a changed job included: the reply's "replaces f1" closes the old one --
  and no model is asked.
* A reply's fact number is found by what the fact was among the facts this
  code shows; a fact it holds but did not show is handed to the pass; one it
  no longer holds is dropped from the reply and counted.
* The control: a round whose reply was not kept is counted, never asked, and
  its fact is not written.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import sys
from datetime import UTC
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def replay():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import longmemeval_replay
    finally:
        sys.path.pop(0)
    return longmemeval_replay


SAID = [("My sister Noura is a dentist in Porto.", "2023/05/02 (Tue) 09:00"),
        ("Can you suggest a recipe for lentil soup?", "2023/05/09 (Tue) 18:30"),
        ("Noura passed her exams, she is a surgeon now.", "2023/05/16 (Tue) 11:00")]


def _item() -> dict:
    return {"question_id": "replay_q", "question_type": "knowledge-update", "question": "What does my sister do?",
            "answer": "surgeon", "question_date": "2023/06/01 (Thu) 10:00",
            "haystack_sessions": [[{"role": "user", "content": text},
                                   {"role": "assistant", "content": "Noted, thanks for telling me."}]
                                  for text, _ in SAID],
            "haystack_session_ids": [f"s{n}" for n in range(len(SAID))],
            "haystack_dates": [date for _, date in SAID], "answer_session_ids": ["s2"]}


class Model:
    """The deep pass, scripted: Noura a dentist, then a surgeon replacing the
    held dentist fact by the number it was shown under."""

    def __init__(self) -> None:
        self.config = type("C", (), {"model": "scripted"})()
        self.calls = 0

    async def chat(self, messages, **_kw):
        from kazma_core.llm_provider import LLMResponse

        self.calls += 1
        said = messages[-1]["content"]
        found: list[dict] = []
        if "is a dentist" in said:
            found = [{"subject": "Noura", "predicate": "occupation", "object": "dentist", "confidence": 0.9}]
        elif "surgeon now" in said:
            held = re.search(r"\b(f\d+)\. noura occupation dentist", said)
            found = [{"subject": "Noura", "predicate": "occupation", "object": "surgeon", "confidence": 0.9,
                      **({"replaces": held.group(1)} if held else {})}]
        return LLMResponse(content=json.dumps({"beliefs": found}), model="scripted",
                           usage={"prompt_tokens": 10, "completion_tokens": 5})


@pytest.fixture
def private(tmp_path, monkeypatch):
    import kazma_core.memory.belief_extractor as extractor
    import kazma_core.memory.embedder as embedding
    import kazma_core.memory.held_facts as where
    import kazma_core.model_registry as registry
    import kazma_core.paths as paths

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "home"))
    monkeypatch.setenv("KAZMA_OPS_ALERTS", "0")
    # Restored afterwards: the scripts route these for their process.
    for module, name in ((paths, "primary_memory_db"), (paths, "memory_ops_db"), (where, "held_facts"),
                         (registry, "get_model_registry"), (extractor, "_turn_zone"),
                         (embedding, "get_embedder")):
        monkeypatch.setattr(module, name, getattr(module, name))
    monkeypatch.setattr(embedding, "get_embedder", lambda: None)
    monkeypatch.setattr(embedding, "encode_text_to_blob", lambda text: None)
    folders = {}
    for name in ("extracted", "replayed"):
        folders[name] = tmp_path / name
        for sub in ("stores", "done"):
            (folders[name] / sub).mkdir(parents=True)
    return folders


def _extract(replay, folder: Path, model) -> dict:
    pilot = replay.pilot
    cached = pilot.CachedModel(model, folder / "replies.jsonl")
    registry = type("Registry", (), {"get_client": lambda self, *_a, **_k: cached})()
    pilot._route_memory_paths()
    pilot._record_shown()
    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry), \
            mock.patch("kazma_core.memory.belief_extractor._turn_zone", lambda: UTC):
        return asyncio.run(pilot._extract([_item()], folder, cached, workers=1))["replay_q"]


def _replay(replay, source: Path, folder: Path, *, rounds: dict | None = None) -> dict:
    counts = {"refs_added": 0, "refs_lost": 0}
    replay._install("scripted", counts, None)
    return asyncio.run(replay.replay_question(_item(), rounds or replay.read_map(source / "map", "replay_q"),
                                              folder, counts))


def _jobs(store: Path) -> dict[str, bool]:
    """Noura's jobs held, and whether each is current."""
    conn = sqlite3.connect(str(store))
    try:
        return {r[0]: r[1] is None and r[2] is None for r in conn.execute(
            "SELECT object, valid_until, invalidated_at FROM beliefs WHERE subject = 'noura' "
            "AND object IN ('dentist', 'surgeon')")}
    finally:
        conn.close()


def test_the_extraction_keeps_each_rounds_reply_and_what_it_was_shown(replay, private):
    stats = _extract(replay, private["extracted"], Model())
    mapped = json.loads((private["extracted"] / "map" / "replay_q.json").read_text(encoding="utf-8"))
    assert mapped["stats"]["deep"] == stats["deep"] == len(mapped["rounds"]) == 3
    last = mapped["rounds"][-1]
    dentist = next(h for h in last["held"] if h["object"] == "dentist")
    assert dentist["subject"] == "noura" and f'"replaces": "{dentist["ref"]}"' in last["content"] and last["key"]
    assert _jobs(private["extracted"] / "stores" / "replay_q.db") == {"dentist": False, "surgeon": True}


def test_a_replay_writes_the_same_store_and_asks_no_model(replay, private):
    model = Model()
    first = _extract(replay, private["extracted"], model)
    stats = _replay(replay, private["extracted"], private["replayed"])
    assert model.calls == 3 and (stats["deep"], stats["no_reply"], stats["refs_lost"]) == (3, 0, 0)
    assert {k: stats[k] for k in ("facts", "current", "deep_pass")} == \
        {k: first[k] for k in ("facts", "current", "deep_pass")}
    assert _jobs(private["replayed"] / "stores" / "replay_q.db") == {"dentist": False, "surgeon": True}


def test_control_a_round_with_no_kept_reply_is_counted_and_not_written(replay, private):
    _extract(replay, private["extracted"], Model())
    rounds = replay.read_map(private["extracted"] / "map", "replay_q")
    first = next(e for e in rounds.values() if "dentist" in str(e["content"]))
    first["content"] = None
    stats = _replay(replay, private["extracted"], private["replayed"], rounds=rounds)
    assert (stats["deep"], stats["no_reply"]) == (2, 1)
    # The dentist was never held, so the surgeon replaces nothing (its number is dropped, counted).
    assert _jobs(private["replayed"] / "stores" / "replay_q.db") == {"surgeon": True}
    assert stats["refs_lost"] == 1


def _shown(*facts):
    return [{"ref": f"f{n}", "id": f"b{n}", "subject": s, "shown": s, "predicate": p, "predicate_type": "set",
             "object": o, "method": "llm_inferred"} for n, (s, p, o) in enumerate(facts, 1)]


def _held_db(*facts) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
                 "extraction_method, valid_from, valid_until, invalidated_at)")
    for n, (s, p, o) in enumerate(facts, 1):
        conn.execute("INSERT INTO beliefs VALUES (?, 't', ?, ?, 'set', ?, 'llm_inferred', ?, NULL, NULL)",
                     (f"h{n}", s, p, o, n))
    return conn


def _entry(*replaces):
    return {"held": [{"ref": "f1", "subject": "user", "predicate": "lives_in", "object": "Porto"},
                     {"ref": "f2", "subject": "user", "predicate": "job_title", "object": "accountant"},
                     {"ref": "f3", "subject": "noura", "predicate": "occupation", "object": "dentist"}],
            "content": json.dumps({"beliefs": [{"subject": "x", "predicate": "y", "object": "z", "replaces": r}
                                               for r in replaces]})}


def test_a_number_is_found_by_what_the_fact_was(replay):
    counts = {"refs_added": 0, "refs_lost": 0}
    # This code shows the job under the name it keeps, first.
    shown = _shown(("user", "role", "accountant"), ("user", "lives_in", "Porto"))
    refs = replay.renumber(_entry("f2", "f1"), shown, _held_db(), "t", counts)
    assert refs == {"f2": "f1", "f1": "f2"} and counts["refs_added"] == 0


def test_a_fact_held_but_not_shown_is_handed_to_the_pass(replay):
    counts = {"refs_added": 0, "refs_lost": 0}
    shown = _shown(("user", "lives_in", "Porto"))
    refs = replay.renumber(_entry("f3"), shown, _held_db(("noura", "occupation", "dentist")), "t", counts)
    assert refs == {"f3": "f2"} and shown[-1]["object"] == "dentist" and counts["refs_added"] == 1
    # Control: a fact no longer held is not placed.
    counts = {"refs_added": 0, "refs_lost": 0}
    assert replay.renumber(_entry("f3"), _shown(), _held_db(), "t", counts) == {}


def test_a_number_is_never_placed_on_another_statement_of_the_value(replay):
    """The step 5 review, L1: "replaces f1 (lives_in Porto)" is not "visited
    Porto"; another name of the same statement is placed, and counted."""
    counts = {"refs_added": 0, "refs_lost": 0}
    entry = {"held": [{"ref": "f1", "subject": "user", "predicate": "lives_in", "object": "Porto"}],
             "content": json.dumps({"beliefs": [{"subject": "user", "predicate": "lives_in", "object": "Lyon",
                                                 "replaces": "f1"}]})}
    assert replay.renumber(entry, _shown(("user", "visited", "Porto")), _held_db(), "t", counts) == {}
    assert replay.renumber(entry, _shown(("user", "visited", "Porto")),
                           _held_db(("user", "visited", "Porto")), "t", counts) == {}
    job = {"held": [{"ref": "f1", "subject": "user", "predicate": "has_occupation", "object": "dentist"}],
           "content": json.dumps({"beliefs": [{"subject": "user", "predicate": "current_role", "object": "surgeon",
                                               "replaces": "f1"}]})}
    assert replay.renumber(job, _shown(("user", "current_role", "dentist")), _held_db(), "t", counts) == {"f1": "f1"}
    assert counts["refs_cross_name"] == 1
