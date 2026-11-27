"""LongMemEval-M's sizing mode (``scripts/longmemeval_bench.py size``).

A small fixture in LongMemEval's shape. The real file stays outside the
repository, and the embedder is a stub: the mode embeds the sample, never
the whole file, and its report quotes none of the text.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PLANTED = "ZEBRA-KEYS-PLANTED"


@pytest.fixture(scope="module")
def lme():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import longmemeval_bench as mod
    finally:
        sys.path.pop(0)
    return mod


class _Stub:
    """Counts texts. Never the real model."""

    def __init__(self) -> None:
        self.n = 0
        self.dim = 1024
        self.model = "stub"

    def encode(self, text: str) -> list[float]:
        del text
        self.n += 1
        return [0.0, 0.0, 0.0, 0.0]

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(text) for text in texts]


def _item(qid: str, sentences: list[str]) -> dict:
    sessions = []
    for text in sentences:
        sessions.append([
            {"role": "user", "content": text},
            {"role": "assistant", "content": "Noted."},
        ])
    return {
        "question_id": qid, "question_type": "single-session-user", "question": "q", "answer": "a",
        "question_date": "2023/06/01 (Thu) 10:00", "answer_session_ids": ["s0"],
        "haystack_session_ids": [f"s{i}" for i in range(len(sessions))],
        "haystack_dates": ["2023/05/20 (Sat) 02:21"] * len(sessions),
        "haystack_sessions": sessions,
    }


def _fixture() -> list[dict]:
    return [_item("q1", [PLANTED, "a second round"]), _item("q2", ["third round", "fourth round"])]


def _clock():
    ticks = iter((0.0, 0.25))

    def now() -> float:
        return next(ticks)

    return now


def test_the_pin_is_the_downloaded_file(lme):
    assert lme.SOURCE["revision"].startswith("98d7416c24c7")
    digest = lme.SOURCE["sha256"][lme.M_FILE]
    assert digest.startswith("9d79e55247")
    assert len(digest) == 64


def test_a_wrong_file_is_refused(lme, tmp_path):
    (tmp_path / lme.M_FILE).write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit):
        lme.load_m(tmp_path)


def test_the_estimate_extrapolates_the_sample_and_quotes_nothing(lme):
    """Four rounds, a sample of two. The estimate is the sample's rate times
    every round, not the sample's own seconds. Embedding all four, or printing
    the planted sentence, is the control."""
    stub = _Stub()
    report = lme.size_report(_fixture(), stub, sample=2, clock=_clock())
    assert report["questions"] == 2
    assert report["sessions"] == 4
    assert report["rounds"] == 4
    assert report["tokens"] > 0
    assert report["sample_n"] == 2
    assert report["sample_s"] == pytest.approx(0.25)
    assert report["encoded"] == stub.n == 2
    assert report["encoded"] != report["rounds"]
    assert report["estimate_s"] == pytest.approx(lme.embedding_estimate(4, 0.25, 2))
    assert report["estimate_s"] != pytest.approx(report["sample_s"])
    assert report["estimate_s"] == pytest.approx(0.5)
    assert report["model_calls"] == 0
    assert report["retrieved"] is False and report["answered"] is False
    assert report["recall_included"] is False
    assert PLANTED not in json.dumps(report)

    whole = lme.size_report(_fixture(), _Stub(), sample=4, clock=_clock())
    assert whole["encoded"] == whole["rounds"] == 4
    assert whole["sampled_all"] is True


def test_embedding_estimate_is_not_the_raw_sample(lme):
    assert lme.embedding_estimate(100, 0.5, 10) == pytest.approx(5.0)
    assert lme.embedding_estimate(100, 0.5, 10) != pytest.approx(0.5)


def test_size_does_not_retrieve_or_answer(lme, tmp_path, monkeypatch, capsys):
    """The command prints the counts. Retrieve, answer and ask are not called,
    and the planted sentence is not in what it prints."""
    items = _fixture()
    path = tmp_path / lme.M_FILE
    path.write_text(json.dumps(items), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setitem(lme.SOURCE["sha256"], lme.M_FILE, digest)

    def boom(*_a, **_k):
        raise AssertionError("size must not retrieve or answer")

    monkeypatch.setattr(lme, "_cmd_retrieve", boom)
    monkeypatch.setattr(lme, "_cmd_answer", boom)
    monkeypatch.setattr(lme, "ask", boom)
    monkeypatch.setattr(lme, "_below_normal", lambda: None)
    monkeypatch.setattr(lme, "_limit_cpu_threads", lambda: None)
    monkeypatch.setattr(lme, "_private_data_folder", lambda: None)
    stub = _Stub()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: stub)
    assert lme.main(["size", "--data", str(tmp_path), "--sample", "2"]) == 0
    out = capsys.readouterr().out
    assert PLANTED not in out
    report = json.loads(out)
    assert report["rounds"] == 4
    assert report["encoded"] == 2
    # One warm-up encode so the sample's clock excludes the model load, then
    # the two sampled rounds. Not the other two rounds.
    assert stub.n == 3
    assert report["model_calls"] == 0
