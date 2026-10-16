"""A question naming an identifier finds the one chunk that names it.

The KB-Q01 72 h soak failed at cycle 1,757 (2026-10-16): asked "Which port
does zqfmbfjvdv listen on?" of a library holding "zqfmbfjvdv listens on port
6634." and five look-alikes ("zqtkjhghfc listens on port 5997", ...), the
library search returned the five look-alikes and not the answer. The keyword
index ranked the answer first by a wide margin; the relevance judge
(``knowledge_index._judge``, KQ01) ranks by meaning lift plus 0.12 x word
coverage, the look-alikes were nearer by meaning, and the answer came 6th.

The meaning scores below are the ones the soak's store produced for that
question (re-asked on a copy of it). The rule: the one candidate holding every
content word of the question, when one of those words is in no other
candidate, leads -- it changes the order of what is kept, never what is kept.
"""

from __future__ import annotations

import pytest

from kazma_core.stores import knowledge_index as ki
from kazma_core.stores.knowledge_index import KnowledgeHit

QUESTION = "Which port does zqfmbfjvdv listen on?"
ANSWER = "zqfmbfjvdv"

#: (chunk id, heading, content, meaning similarity) as measured on the soak's store.
ROWS = [
    ("c-e678", "Adapter Throughput", "zqtkjhghfc listens on port 5997.", 0.3113),
    ("c-dbb4", "Repository Backup", "zqnlmnbvwx listens on port 2241.", 0.3055),
    ("c-4f19", "Endpoint Account", "zqbsgvffgq listens on port 6639.", 0.3031),
    ("c-bc08", "Folder Worker", "zqjqmhwmck listens on port 4928.", 0.2850),
    ("c-d00c", "Protocol Alert Project", "zqbqxcrksb listens on port 2336.", 0.2515),
    ("c-a74b", "Request Partition Latency", "zqfmbfjvdv listens on port 6634.", 0.2095),
    ("c-8a7d", "Module Monitor Outcome", "The owner of zqcgvdmxvw is desk 2357.", 0.2080),
    ("c-cc78", "Tutorial Section Batch", "Window example buffer issue module release.", 0.1900),
    ("c-9f0c", "Health Paragraph Patch", "Outcome workspace shard quota check theme.", 0.1876),
    ("c-c45c", "Cursor Patch 7", "Cursor patch notes.", 0.1823),
    ("c-6071", "Worker Theme", "File capacity journal module build range report.", 0.1788),
    ("c-e38d", "Value File", "Record section recovery process region version.", 0.1763),
    ("c-f1a0", "Queue Index", "Index queue window shard build.", 0.1750),
    ("c-f1a1", "Shard Theme", "Theme shard version release check.", 0.1740),
    ("c-f1a2", "Batch Report", "Batch report quota module window.", 0.1735),
]


def _hits(rows=ROWS) -> tuple[list[KnowledgeHit], dict[str, float], list[float]]:
    hits = [KnowledgeHit(chunk_id=cid, content=f"## {head}\n\n{text}", score=0.0,
                         library_id="lib", source_url="https://docs.example.test/p",
                         document_title=head, section_header=head, chunk_index=0,
                         has_code=False) for cid, head, text, _sim in rows]
    sims = {cid: sim for cid, _h, _t, sim in rows}
    return hits, sims, sorted(sims.values(), reverse=True)


def _top5(judge=ki._judge) -> list[str]:
    hits, sims, dense = _hits()
    return [h.content for h in judge(QUESTION, hits, sims, dense)][:5]


def test_the_answer_naming_the_identifier_leads():
    top = _top5()
    assert ANSWER in top[0], top


def test_the_old_order_left_it_out(monkeypatch):
    """Negative control: the judge without the rule is the soak's failure."""
    monkeypatch.setattr(ki, "_sole_exact_match", lambda terms, candidates: None)
    assert not any(ANSWER in c for c in _top5())


@pytest.mark.parametrize("answer_sim", [0.2095, 0.17, 0.12, 0.05])
def test_it_reorders_what_is_kept_and_never_keeps_more(answer_sim, monkeypatch):
    """Same set with and without the rule, whatever the answer's meaning
    score: a chunk below the floor or outside the gap stays out, so a
    question nothing answers stays unanswered."""
    rows = [(cid, head, text, answer_sim if cid == "c-a74b" else sim)
            for cid, head, text, sim in ROWS]
    hits, sims, dense = _hits(rows)
    with_rule = {h.chunk_id for h in ki._judge(QUESTION, list(hits), sims, dense)}
    monkeypatch.setattr(ki, "_sole_exact_match", lambda terms, candidates: None)
    hits, sims, dense = _hits(rows)
    without = {h.chunk_id for h in ki._judge(QUESTION, list(hits), sims, dense)}
    assert with_rule == without


@pytest.mark.parametrize("rows, why", [
    ([r for r in ROWS] + [("c-dup", "Copy", "zqfmbfjvdv listens on port 7000.", 0.30)],
     "two chunks name everything the question names"),
    ([(cid, h, t.replace("zqfmbfjvdv ", ""), s) for cid, h, t, s in ROWS],
     "no chunk names the identifier"),
])
def test_no_lead_without_one_distinctive_full_match(rows, why):
    hits, _sims, _dense = _hits(rows)
    from kazma_core.memory.query_terms import content_terms

    assert ki._sole_exact_match(content_terms(QUESTION), hits) is None, why


def test_a_lone_match_with_no_distinctive_word_does_not_lead():
    """Every word of "listen port" is in many chunks: no identifier, no lead."""
    hits, _sims, _dense = _hits()
    assert ki._sole_exact_match(["port", "listen"], hits) is None
