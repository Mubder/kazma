"""The product measures memory changes are gated on
(``scripts/memory_product_measures.py``, the memory plan's W0).

* The tokens the recall block adds to a turn, on the retrieval benchmark's
  store with its recorded vectors, are the recorded ones
  (``tests/fixtures/memory_bench/product_measures.json``): a change to the
  block's size records its new numbers in the same commit, its before and
  after. Negative control: a block grown by one line is caught.
* The record holds every product measure the plan names.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RECORD = REPO / "tests" / "fixtures" / "memory_bench" / "product_measures.json"


@pytest.fixture(scope="module")
def measures():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import memory_product_measures
    finally:
        sys.path.pop(0)
    return memory_product_measures


@pytest.fixture(scope="module")
def store(measures):
    from kazma_core.memory import benchmark

    data = benchmark.load_dataset()
    replay = benchmark.ReplayEmbedder(measures.VECTORS)
    with measures._replayed(replay), benchmark._seeded_store(data) as (conn, _refs, _covers):
        yield conn, measures._questions(data)


def test_the_recall_block_adds_the_recorded_tokens(measures, store):
    conn, questions = store
    tokens, _seconds = measures.block_tokens(conn, questions)
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    today = {"block_tokens": {"store": measures.token_summary(tokens)}}
    assert measures.changed_tokens({"block_tokens": {"store": record["block_tokens"]["store"]}}, today) == []


def test_control_a_bigger_block_is_caught(measures, store, monkeypatch):
    from kazma_core.memory import recall

    real = recall.format_recall_block
    monkeypatch.setattr(recall, "format_recall_block",
                        lambda res, **kw: real(res, **kw) + "\n- one more line the block did not have before")
    conn, questions = store
    tokens, _seconds = measures.block_tokens(conn, questions)
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    today = {"block_tokens": {"store": measures.token_summary(tokens)}}
    assert measures.changed_tokens({"block_tokens": {"store": record["block_tokens"]["store"]}}, today)


def test_the_record_holds_every_product_measure():
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    assert set(record["benchmark"]) == {"en", "ar"}
    for store in ("store", "scale"):
        assert {"en", "ar", "all"} <= set(record["block_tokens"][store])
    assert {"answered", "precision", "abstained"} <= set(record["small_store"])
    assert record["recall_ms"]["p50"] > 0
    # Time to the first token: Kazma's own share (tests/e2e) and the
    # embedding model's, recorded on this PC.
    assert record["first_token"]["p50_s"] > 0 and record["embed_ms"]["p50"] > 0
