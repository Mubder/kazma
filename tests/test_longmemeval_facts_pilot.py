"""The facts pilot (``scripts/longmemeval_facts_pilot.py``, the memory plan's
W4): LongMemEval histories written round by round through the product's own
deep-pass task, each question in a store of its own, on a small history of our
own in LongMemEval's shape (no dataset text in the repository).

* The committed pilot is a quarter of dev, each type at its quota.
* A round's event reaches its question's store as the product writes it (the
  quick pass, then ``worker_bootstrap._handle_micro_consolidation``), with its
  day, and recall shows it "[on ...]"; the block without the dated event facts
  does not.
* A second run asks the model nothing: the replies are kept by message.
* The harness's ``answer --ids`` asks only the questions the file names.
* The control: with Kazma's memory paths not routed to the question's store,
  the deep pass writes elsewhere and the store holds no deep-pass fact.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def pilot():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import longmemeval_facts_pilot
    finally:
        sys.path.pop(0)
    return longmemeval_facts_pilot


def test_the_committed_pilot_is_a_quarter_of_dev(pilot):
    held = json.loads(pilot.PILOT.read_text(encoding="utf-8"))
    dev = set(pilot.bench.question_ids("dev"))
    assert held["quotas"] == pilot.QUOTAS and held["size"] == sum(pilot.QUOTAS.values()) == 50
    assert len(set(held["ids"])) == 50 and set(held["ids"]) <= dev


def _item() -> dict:
    said = [("I went to a pottery exhibition at the craft centre last Saturday.", "2023/05/14 (Sun) 09:00"),
            ("Can you suggest a recipe for lentil soup?", "2023/05/16 (Tue) 18:30"),
            ("What is the capital of Portugal?", "2023/05/20 (Sat) 11:00")]
    return {"question_id": "pilot_q", "question_type": "temporal-reasoning",
            "question": "When did I go to the pottery exhibition?", "answer": "x",
            "question_date": "2023/06/01 (Thu) 10:00",
            "haystack_sessions": [[{"role": "user", "content": text},
                                   {"role": "assistant", "content": "Noted, thanks for telling me."}]
                                  for text, _ in said],
            "haystack_session_ids": [f"s{n}" for n in range(len(said))],
            "haystack_dates": [date for _, date in said], "answer_session_ids": ["s0"]}


EVENT = {"subject": "user", "predicate": "attended", "predicate_type": "set",
         "object": "a pottery exhibition at the craft centre", "on": "2023-05-13", "confidence": 0.9}


class _Model:
    """A scripted provider in Kazma's shape: the event for the pottery turn."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.config = type("C", (), {"model": "scripted"})()
        self.calls, self.refuse = 0, refuse

    async def chat(self, messages, **_kw):
        from kazma_core.llm_provider import LLMResponse

        if self.refuse:
            raise AssertionError("a replayed run asks the model nothing")
        self.calls += 1
        found = [EVENT] if "pottery" in messages[-1]["content"] else []
        return LLMResponse(content=json.dumps({"beliefs": found}), model="scripted",
                           usage={"prompt_tokens": 100, "completion_tokens": 10,
                                  "prompt_tokens_details": {"cached_tokens": 80}})


@pytest.fixture
def private(tmp_path, monkeypatch):
    import kazma_core.paths as paths

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KAZMA_OPS_ALERTS", "0")
    # Restored after the test: the pilot routes these to each question's store.
    monkeypatch.setattr(paths, "primary_memory_db", paths.primary_memory_db)
    monkeypatch.setattr(paths, "memory_ops_db", paths.memory_ops_db)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    folder = tmp_path / "pilot"
    for sub in ("stores", "done"):
        (folder / sub).mkdir(parents=True)
    return folder


def _run(pilot, folder, provider, *, route: bool = True) -> dict:
    model = pilot.CachedModel(provider, folder / "replies.jsonl")
    registry = type("Registry", (), {"get_client": lambda self, *_a, **_k: model})()
    if route:
        pilot._route_memory_paths()
    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry), \
            mock.patch("kazma_core.memory.belief_extractor._turn_zone", lambda: UTC):
        return asyncio.run(pilot._extract([_item()], folder, model, workers=2))["pilot_q"]


def test_an_event_reaches_its_questions_store_with_its_day(pilot, private):
    provider = _Model()
    stats = _run(pilot, private, provider)
    # Three rounds: each a quick pass, each a deep pass (none is small talk).
    assert (stats["rounds"], stats["quick"], stats["deep"], stats["deep_failed"]) == (3, 3, 3, 0)
    assert provider.calls == 3 and stats["dated_events"] == 1 and stats["deep_pass"] == 1
    db = private / "stores" / "pilot_q.db"
    block, shown = pilot._recall_block(_item(), db, without_events=False)
    assert "[on 13 May 2023] user attended a pottery exhibition at the craft centre" in block
    assert shown["dated_event_facts"] == 1
    without, shown = pilot._recall_block(_item(), db, without_events=True)
    assert "user attended" not in without and shown["dated_event_facts"] == 0


def test_a_second_run_asks_the_model_nothing(pilot, private):
    first = _run(pilot, private, _Model())
    (private / "done" / "pilot_q.json").unlink()
    again = _run(pilot, private, _Model(refuse=True))
    assert {k: again[k] for k in ("facts", "dated_events", "deep")} == \
        {k: first[k] for k in ("facts", "dated_events", "deep")}


def test_control_without_routing_the_deep_pass_writes_elsewhere(pilot, private):
    stats = _run(pilot, private, _Model(), route=False)
    assert stats["deep_pass"] == 0 and stats["dated_events"] == 0


def test_answer_asks_only_the_questions_the_ids_file_names(tmp_path, monkeypatch, pilot):
    bench = pilot.bench
    items = [dict(_item(), question_id=q) for q in ("a1", "a2", "a3")]
    asked: list[str] = []

    class Chat:
        def __init__(self, *, model, **_kw):
            self.model = model

        def ask(self, prompt, *, max_tokens):
            asked.append(prompt)
            return "yes", {"prompt_tokens": 1, "completion_tokens": 1}

    monkeypatch.setenv("DEMO_KEY", "demo-key")
    monkeypatch.setattr(bench, "_private_data_folder", lambda: None)
    monkeypatch.setattr(bench, "load", lambda _data: items)
    monkeypatch.setattr(bench, "question_ids", lambda _split: ["a1", "a2", "a3"])
    monkeypatch.setattr(bench, "kazma_history", lambda item, rows: ("block", "whole", {"shown": 1, "facts": 0}))
    monkeypatch.setattr(bench, "history_rows", lambda item: [])
    monkeypatch.setattr(bench, "history_string", lambda item, **_kw: ("full", 0))
    monkeypatch.setattr(bench, "plain_top", lambda *_a, **_kw: [])
    monkeypatch.setattr(bench, "CachedEmbedder", lambda *a, **k: None)
    monkeypatch.setattr(bench, "Chat", Chat)
    monkeypatch.setattr(bench, "ANSWERS", tmp_path / "answers.json")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    ids = tmp_path / "ids.json"
    ids.write_text(json.dumps({"ids": ["a3", "a1"]}), encoding="utf-8")
    assert bench._cmd_answer(tmp_path, None, "m", "https://example.invalid", 1, split="dev", key_env="DEMO_KEY",
                             conditions=("kazma",), ids=ids) == 0
    result = json.loads((tmp_path / "answers_dev.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in result["results"]] == ["a1", "a3"] and result["ids_from"] == "ids.json"
