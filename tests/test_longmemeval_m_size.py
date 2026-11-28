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
    """Four rounds, each a different embed text, a sample of two. The estimate
    is the sample's rate times the distinct texts, not the sample's own
    seconds. These four texts are all distinct, so that count equals the
    rounds. Embedding all four, or printing the planted sentence, is the
    control."""
    stub = _Stub()
    report = lme.size_report(_fixture(), stub, sample=2, clock=_clock())
    assert report["questions"] == 2
    assert report["sessions"] == 4
    assert report["rounds"] == 4
    assert report["unique_texts"] == 4
    assert report["tokens"] > 0
    assert report["sample_n"] == 2
    assert report["sample_s"] == pytest.approx(0.25)
    assert report["encoded"] == stub.n == 2
    assert report["encoded"] != report["rounds"]
    assert report["estimate_s"] == pytest.approx(lme.embedding_estimate(report["unique_texts"], 0.25, 2))
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
    monkeypatch.setattr(lme, "_below_normal", lambda: False)

    def refuse_load(*_a, **_k):
        raise AssertionError("size must not json.load the file")

    real_loads = lme.json.loads

    def refuse_whole_list(text, *a, **k):
        if isinstance(text, str) and PLANTED in text and text.lstrip().startswith("["):
            raise AssertionError("size parsed the question list as one string")
        return real_loads(text, *a, **k)

    monkeypatch.setattr(lme.json, "load", refuse_load)
    monkeypatch.setattr(lme.json, "loads", refuse_whole_list)
    monkeypatch.setattr(lme, "_limit_cpu_threads", lambda: None)
    monkeypatch.setattr(lme, "_private_data_folder", lambda: None)
    stub = _Stub()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: stub)
    assert lme.main(["size", "--data", str(tmp_path), "--sample", "2"]) == 0
    out = capsys.readouterr().out
    assert PLANTED not in out
    report = json.loads(out)
    assert report["rounds"] == 4
    assert report["unique_texts"] == 4
    assert report["encoded"] == 2
    assert report["priority"] == "not lowered"
    assert "near-equal" in report["batch_what"]
    assert "the way a retrieve run batches" not in out
    # One warm-up encode so the sample's clock excludes the model load, then
    # the two sampled rounds. Not the other two rounds.
    assert stub.n == 3
    assert report["model_calls"] == 0


def test_the_estimate_counts_distinct_texts_not_every_round(lme):
    """Repeated and blank rounds are not embedded again. Multiplying by every
    round is the control."""
    items = [_item("q1", ["same", "same"]), _item("q2", ["same", "other"])]
    items[1]["haystack_sessions"].append([
        {"role": "user", "content": "   "},
        {"role": "assistant", "content": ""},
    ])
    report = lme.size_report(items, _Stub(), sample=2, clock=_clock())
    assert report["rounds"] == 5
    assert report["unique_texts"] == 2
    assert report["estimate_s"] == pytest.approx(lme.embedding_estimate(2, 0.25, 2))
    assert report["estimate_s"] == pytest.approx(0.25)
    assert report["estimate_s"] != pytest.approx(lme.embedding_estimate(report["rounds"], 0.25, 2))
    dumped = json.dumps(report)
    assert PLANTED not in dumped
    assert "the way a retrieve run batches" not in dumped
    assert "the way a retrieve run batches" not in (lme.measure_sample.__doc__ or "")
    assert "quartile" in report["batch_what"]
    assert "near-equal" in report["batch_what"]


def test_questions_are_read_one_at_a_time(lme, tmp_path, monkeypatch):
    """A handful of characters at a time still yields the questions. Parsing
    the file as one string is the control."""
    lme.corpus_counts(_fixture())
    path = tmp_path / "m.json"
    path.write_text(json.dumps(_fixture()), encoding="utf-8")

    def boom(*_a, **_k):
        raise AssertionError("the file was parsed as one string")

    monkeypatch.setattr(lme.json, "load", boom)
    monkeypatch.setattr(lme.json, "loads", boom)
    assert list(lme.iter_m(path, chunk=3)) == _fixture()
    streamed = lme.stream_counts(path, chunk=3)
    counted = lme.corpus_counts(_fixture())
    for key in ("questions", "sessions", "rounds", "tokens", "unique_texts", "embed_lengths"):
        assert streamed[key] == counted[key]


def test_a_truncated_list_is_refused_without_the_question(lme, tmp_path):
    """A file that is not a finished list stops. The message does not carry
    the question, and the read does not hang."""
    cut = tmp_path / "cut.json"
    cut.write_text('[{"question": "' + PLANTED + '", "haystack_sessions": [', encoding="utf-8")
    bare = tmp_path / "bare.json"
    bare.write_text("[", encoding="utf-8")
    obj = tmp_path / "obj.json"
    obj.write_text("{}", encoding="utf-8")
    for path in (cut, bare, obj):
        with pytest.raises(SystemExit) as caught:
            list(lme.iter_m(path, chunk=3))
        assert PLANTED not in str(caught.value)


def test_load_m_checks_the_hash_and_does_not_parse(lme, tmp_path, monkeypatch):
    path = tmp_path / lme.M_FILE
    path.write_text(json.dumps(_fixture()), encoding="utf-8")
    monkeypatch.setitem(lme.SOURCE["sha256"], lme.M_FILE, hashlib.sha256(path.read_bytes()).hexdigest())

    def boom(*_a, **_k):
        raise AssertionError("load_m parsed the file")

    monkeypatch.setattr(lme.json, "load", boom)
    monkeypatch.setattr(lme.json, "loads", boom)
    assert lme.load_m(tmp_path) == path


def test_a_failed_priority_drop_is_said(lme, monkeypatch, caplog):
    monkeypatch.setattr(lme, "_priority_lowered", lambda: True)
    assert lme._below_normal() is True
    assert lme._priority_word(True) == "below normal"
    assert "could not lower priority" not in caplog.text

    monkeypatch.setattr(lme, "_priority_lowered", lambda: False)
    with caplog.at_level("WARNING"):
        assert lme._below_normal() is False
    assert lme._priority_word(False) == "not lowered"
    assert "could not lower priority" in caplog.text

    def boom():
        raise OSError("nice")

    monkeypatch.setattr(lme, "_priority_lowered", boom)
    assert lme._below_normal() is False
