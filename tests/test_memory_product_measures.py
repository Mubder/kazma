"""The product measures memory changes are gated on
(``scripts/memory_product_measures.py``, the memory plan's W0).

* The tokens the recall block adds to a turn, on the retrieval benchmark's
  store with its recorded vectors, are the recorded ones
  (``tests/fixtures/memory_bench/product_measures.json``): a change to the
  block's size records its new numbers in the same commit, its before and
  after. Negative control: a block grown by one line is caught.
* The record holds every product measure the plan names.
* The long store (``long``: memories as long as live's) is padded with no
  content word, and live's lengths are read only from a native backup,
  opened immutable, never the live file.
"""

from __future__ import annotations

import json
import sqlite3
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
    for store in ("store", "scale", "long"):
        assert {"en", "ar", "all"} <= set(record["block_tokens"][store])
    assert {"answered", "precision", "abstained"} <= set(record["small_store"])
    assert record["recall_ms"]["p50"] > 0
    # Time to the first token: Kazma's own share (tests/e2e) and the
    # embedding model's, recorded on this PC.
    assert record["first_token"]["p50_s"] > 0 and record["embed_ms"]["p50"] > 0


def test_the_long_store_s_padding_holds_no_content_word(measures):
    from kazma_core.memory.query_terms import content_terms

    for arabic in (False, True):
        assert content_terms(measures._filler(arabic) * 3) == []
    # Negative control: a padding with one word a question could ask about.
    assert content_terms(measures._filler(False) + " vaccine ") == ["vaccine"]


def test_lengthen_draws_each_conversation_to_live_s_lengths(measures):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE episodes (id TEXT, tenant_id TEXT, user_text TEXT, assistant_text TEXT)")
    conn.executemany("INSERT INTO episodes VALUES (?,?,?,?)", [
        ("e_1", "t", "Where is my car?", "In the garage."),
        ("e_2", "t", "", "A swarm result."),
        ("e_3", "other", "Not this tenant's.", "Left alone."),
    ])
    lengths = {"question_percentiles": [300] * 101, "answer_percentiles": [2000] * 101,
               "one_side_percentiles": [900] * 101}
    assert measures.lengthen(conn, lengths, tenant_id="t") == 2
    rows = {r[0]: r for r in conn.execute("SELECT id, user_text, assistant_text FROM episodes")}
    assert rows["e_1"][1].startswith("Where is my car?") and 290 <= len(rows["e_1"][1]) <= 300
    assert rows["e_1"][2].startswith("In the garage.") and 1990 <= len(rows["e_1"][2]) <= 2000
    assert rows["e_2"][1] == "" and 890 <= len(rows["e_2"][2]) <= 900
    assert rows["e_3"][1] == "Not this tenant's."


def test_live_lengths_reads_a_backup_immutable_and_never_the_live_file(measures, tmp_path):
    backup = tmp_path / "memory_state_1795311656.db"
    conn = sqlite3.connect(backup)
    conn.execute("CREATE TABLE episodes (user_text TEXT, assistant_text TEXT, tier TEXT)")
    conn.executemany("INSERT INTO episodes VALUES (?,?,?)", [
        ("q" * 10, "a" * 100, "episodic"), ("q" * 20, "a" * 200, "recall"),
        ("", "s" * 50, "archived"), ("x" * 99, "y" * 99, "forgotten"),
    ])
    conn.commit()
    conn.close()
    before = backup.stat().st_mtime_ns
    report = measures.live_lengths(backup)
    assert report["episodes"] == 3 and report["two_sided"] == 2 and report["one_sided"] == 1
    assert report["question_percentiles"][0] == 10 and report["question_percentiles"][100] == 20
    assert report["answer_percentiles"][100] == 200 and report["one_side_percentiles"] == [50] * 101
    assert backup.stat().st_mtime_ns == before
    assert not (tmp_path / "memory_state_1795311656.db-wal").exists()
    # The live file itself is refused, before it is opened.
    live = tmp_path / "memory_state.db"
    with pytest.raises(SystemExit, match="never read"):
        measures.live_lengths(live)
    assert not live.exists()
