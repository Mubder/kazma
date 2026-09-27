"""A request to remember is heard in Arabic too (W4); small talk is kept, never recalled (W5).

W4: "remember this" promoted a turn to the recall tier and counted as a durable
cue for fact extraction -- in English only. "تذكر أن اجتماعي يوم الثلاثاء" was
an ordinary turn, and at eight characters "تذكر هذا" was filler.
``memory/remember_request.py`` hears both languages; Arabic is compared folded
and as whole words ("تذكرة", a ticket, is not "تذكر").

W5: a greeting answered by a greeting took a recall slot whenever the user
greeted again -- by meaning, "hello" is close to "Hi / Hello! How can I help?".
Recall now leaves small talk out of the history it shows; the turn stays
stored. Small talk is the words themselves with a short reply, never a
length: "Pixel's age?" is short and a question.
"""

from __future__ import annotations

import math
import re
import sqlite3
import struct
import time

import pytest

from kazma_core.memory.episode_text import is_small_talk
from kazma_core.memory.remember_request import is_remember_request
from kazma_core.memory.schema_v2 import ensure_primary_schema

# ── W4 ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("text", "asks"), [
    ("Remember that I am vegan", True),
    ("don't forget my sister's birthday", True),
    ("تذكر أن اجتماعي يوم الثلاثاء", True),
    ("تذكّر هذا", True),  # with a shadda
    ("وتذكر أن الحفلة السبت", True),  # "and remember"
    ("لا تنسى موعد الطبيب", True),
    ("احفظ رقم الغرفة 512", True),
    ("سجّل عندك رقمي", True),
    ("خلك فاكر إن عندي اجتماع", True),
    ("حط في بالك إني ما آكل لحم", True),
    ("حجزت تذكرة الطيران", False),  # a ticket
    ("What time is it?", False),
    ("", False),
])
def test_a_request_to_remember_in_either_language(text, asks):
    assert is_remember_request(text) is asks


def test_an_arabic_request_to_remember_goes_to_the_recall_tier():
    from kazma_core.memory.dual_write import episode_row

    row = episode_row(session_id="s", turn_number=1, user_text="تذكر أن اجتماعي يوم الثلاثاء",
                      assistant_text="حاضر، يوم الثلاثاء.")
    assert (row["tier"], row["meta"].get("promote_reason")) == ("recall", "explicit_remember")
    assert row["importance"] >= 3
    ticket = episode_row(session_id="s", turn_number=2, user_text="حجزت تذكرة الطيران",
                         assistant_text="رحلة موفقة!")
    assert ticket["tier"] == "working"  # negative control: an ordinary live turn


def test_a_short_arabic_request_to_remember_is_not_filler():
    from kazma_core.memory.belief_extractor import is_filler_turn

    assert is_filler_turn("تذكر هذا") is False
    assert is_filler_turn("تذكرة") is True  # negative control: short, and a ticket


# ── W5 ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("user", "answer", "small"), [
    ("Hi", "Hello! How can I help?", True),
    ("thanks a lot", "Anytime!", True),
    ("Hello Kazma", "Hi there!", True),
    ("مرحبا", "أهلاً! كيف أقدر أساعدك؟", True),
    ("شكراً جزيلاً", "العفو!", True),
    ("صباح الخير", "صباح النور!", True),
    # A greeting is answered in kind, at length on the live install (531
    # characters at most, 2026-09-27); a report after a greeting is not.
    ("hello", "Hey there! Anything you'd like me to do with those emails? " * 5, True),
    ("hello", "Here is your morning briefing. " * 40, False),
    # "ok" is often the go-ahead for a report.
    ("ok", "Done.", True),
    ("ok", "x" * 300, False),
    ("Pixel's age?", "Four years old.", False),  # short, and a question
    ("hi, what is my dog called?", "Pixel.", False),
])
def test_small_talk_is_the_words_and_a_short_reply(user, answer, small):
    assert is_small_talk(user, answer) is small


DIM = 256
_GREETING = {"hi", "hello", "hey", "help"}


def _vec(text: str) -> list[float]:
    v = [0.0] * DIM
    for word in re.findall(r"[a-z]+", text.lower()):
        v[0 if word in _GREETING else 1 + sum(map(ord, word)) % (DIM - 1)] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class _Toy:
    dim = DIM

    def encode(self, text):
        return _vec(text)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    toy = _Toy()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: toy)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "toy")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_dim", lambda: DIM)
    c = sqlite3.connect(str(tmp_path / "memory_state.db"))
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    for i, (user, answer) in enumerate([
        ("Hi", "Hello! How can I help?"),
        ("What's my dog's name?", "Your greyhound is called Pixel."),
        ("ok", "Here is the full report on the migration: " + "details " * 60),
    ]):
        c.execute(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
            "summary_text, tier, structural_importance, created_at, metadata_json, embedding, "
            "embedding_model_version) VALUES (?,?,?,?,?,?,'','episodic',1,?,'{}',?,?)",
            # Vectors of the whole turn: this is about which turns recall may
            # show, not about how a vector is made (test_memory_episode_text).
            (f"e{i}", "default", "s", i, user, answer, time.time() - 100 + i,
             struct.pack(f"<{DIM}f", *_vec(f"{user} {answer}")), "toy"),
        )
    c.commit()
    yield c
    c.close()


def test_a_greeting_is_not_recalled_when_the_user_greets_again(conn, monkeypatch):
    from kazma_core.memory import recall as recall_mod

    found = recall_mod.recall("hello there", conn=conn)
    assert "e0" not in {h.id for h in found.episodes}
    assert conn.execute("SELECT count(*) FROM episodes").fetchone()[0] == 3  # kept, not deleted
    # Negative control: without the rule the greeting is what recall finds.
    monkeypatch.setattr(recall_mod, "is_small_talk", lambda user, answer: False)
    again = recall_mod.recall("hello there", conn=conn)
    assert "e0" in {h.id for h in again.episodes}


def test_an_ok_before_a_report_is_still_history(conn):
    from kazma_core.memory import recall as recall_mod

    found = recall_mod.recall("the migration report details", conn=conn)
    assert "e2" in {h.id for h in found.episodes}
