"""A time word after a place ends the place, in English and Arabic (the
memory plan's finding 10, 2026-11-22).

The real-use suite told Kazma "أسكن في لشبونة هالأيام." ("I live in Lisbon
these days") and the quick pass kept nothing in any run: a place must END
where it is read, and "هالأيام" was not an end, so the fact was voided. Its
English pair "I live in Lisbon these days." kept nothing either: only "now"
and "currently" ended an English place. And "I moved to Porto." kept
nothing in English while "انتقلت إلى بورتو." kept the move.

Each statement is stored by the product's quick pass
(``extract_and_apply_beliefs_sync``, as the post-turn pass calls it) into a
private memory database, and read back from it. Today's rules -- the place
end without the present, and no English move -- are the negative control.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from kazma_core.memory import arabic_facts, belief_extractor
from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

#: (statement, the place it names): each with a time word after the place,
#: and its plain form, in both languages.
TOLD = [
    ("I live in Lisbon these days.", "Lisbon"),
    ("I live in Lisbon at the moment.", "Lisbon"),
    ("I live in Lisbon nowadays.", "Lisbon"),
    ("I live in Lisbon.", "Lisbon"),
    ("I moved to Porto last month.", "Porto"),
    ("I moved to Porto.", "Porto"),
    ("We moved to New York two years ago.", "New York"),
    ("أسكن في لشبونة هالأيام.", "لشبونة"),
    ("أسكن في لشبونة هالفترة.", "لشبونة"),
    ("ساكن في دبي حالياً.", "دبي"),
    ("أسكن في لشبونة الحين.", "لشبونة"),
    ("أسكن في لشبونة.", "لشبونة"),
    ("انتقلت إلى بورتو الشهر الماضي.", "بورتو"),
    ("انتقلت إلى بورتو.", "بورتو"),
]
#: What the old rules lost: a time word after the place, and the English move.
LOST_BEFORE = {
    "I live in Lisbon these days.", "I live in Lisbon at the moment.", "I live in Lisbon nowadays.",
    "I moved to Porto last month.", "I moved to Porto.", "We moved to New York two years ago.",
    "أسكن في لشبونة هالأيام.", "أسكن في لشبونة هالفترة.",
}
#: Not a place, with or without the time word: nothing is kept.
NOT_A_PLACE = [
    "I moved to a new job these days.",
    "I live in a small flat these days.",
    "أسكن في شقة صغيرة هالأيام.",
    "انتقلت إلى وظيفة جديدة هالأيام.",
    "My sister moved to Rotterdam last month.",
    "أختي انتقلت إلى روتردام الشهر الماضي.",
    # A move to something that is not where the user lives (the main
    # session's paired negatives): an item, a version, an office.
    "We moved to the next item.",
    "انتقلنا إلى البند التالي.",
    "We moved to Python 3.12.",
    "انتقلنا إلى بايثون 3.12.",
    "We moved to a new office.",
    "انتقلنا إلى مكتب جديد.",
    "We moved to the Dubai office.",
    "انتقلنا إلى مكتب دبي.",
]


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    """The quick pass makes no model call; the write path's fact vector
    would load the real embedder, which this test does not read."""
    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)


def _stored(tmp_path, text: str) -> list[tuple[str, str]]:
    primary = sqlite3.connect(tmp_path / "memory_state.db")
    primary.row_factory = sqlite3.Row
    ensure_primary_schema(primary)
    ops = sqlite3.connect(tmp_path / "memory_ops.db", isolation_level=None)
    ensure_ops_schema(ops)
    try:
        extract_and_apply_beliefs_sync(primary, ops, text, "Noted.", session_id="s1", turn=1, tenant_id="default")
        return [(r["predicate"], r["object"]) for r in primary.execute(
            "SELECT predicate, object FROM beliefs WHERE valid_until IS NULL AND subject = 'user'")]
    finally:
        primary.close()
        ops.close()


@pytest.mark.parametrize(("text", "place"), TOLD)
def test_a_time_word_after_the_place_ends_it(tmp_path, text, place):
    assert _stored(tmp_path, text) == [("lives_in", place)]


@pytest.mark.parametrize("text", NOT_A_PLACE)
def test_a_time_word_makes_nothing_a_place(tmp_path, text):
    assert _stored(tmp_path, text) == []


def _old_rules(monkeypatch) -> None:
    """The rules before 2026-11-22: an English place ended at "now",
    "currently", "too", "also", punctuation, "and", "I", Arabic script or
    the end; the Arabic enders held no "هالأيام"; no English move."""
    monkeypatch.setattr(
        belief_extractor, "_value_end_en",
        lambda: r"(?:\s+(?:now|too|also|currently)\b|[.,!]|\s+and\s|\s+[Ii]\s|\s+(?=[ء-ي])|$)")
    monkeypatch.setattr(belief_extractor, "_MOVED_EN", r"(?!x)x")
    monkeypatch.setattr(arabic_facts, "present_ar_words", lambda: frozenset())


@pytest.mark.parametrize(("text", "place"), TOLD)
def test_control_the_old_rules_lose_the_place_after_a_time_word(tmp_path, monkeypatch, text, place):
    _old_rules(monkeypatch)
    stored = _stored(tmp_path, text)
    if text in LOST_BEFORE:
        assert stored == []
    else:
        assert stored == [("lives_in", place)]


def test_every_language_has_a_statement_the_old_rules_lost():
    """The gate is paired: each language holds a time-word statement that
    the old rules lost and the new ones keep."""
    arabic = {t for t in LOST_BEFORE if any("؀" <= c <= "ۿ" for c in t)}
    assert arabic and LOST_BEFORE - arabic


REPO = Path(__file__).resolve().parents[1]


def _memory_bench():
    spec = importlib.util.spec_from_file_location("_memory_bench_f10", REPO / "scripts" / "memory_bench.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_the_scenarios_version_is_locked_with_its_reason():
    """The write scenarios that locked the old difference between the
    languages changed in a new version, and the lock says why (never a
    quiet edit)."""
    bench = _memory_bench()
    locked = json.loads((REPO / "tests" / "fixtures" / "memory_bench" / "thresholds.json")
                        .read_text(encoding="utf-8"))["writes"]
    assert locked["version"] == bench.WRITES_VERSION
    reasons = {record["version"]: record["reason"] for record in locked["versions"]}
    assert bench.WRITES_VERSION in reasons
    # v8: English moves read as the Arabic twin's (finding 10); v9: a move
    # no word calls a place asks, in both languages (finding 15).
    assert "Arabic twin" in reasons[8] and "Slack" in reasons[9]


def test_a_new_scenarios_version_needs_its_reason():
    bench = _memory_bench()
    old = {"version": 7, "versions": []}
    versions, problems = bench.writes_versions(old, 8, "why it changed")
    assert not problems and versions[-1]["reason"] == "why it changed" and versions[-1]["version"] == 8
    # Control: with no reason the new version is refused, and the same
    # version needs none.
    assert bench.writes_versions(old, 8, None)[1]
    assert bench.writes_versions({"version": 8, "versions": versions}, 8, None) == (versions, [])
