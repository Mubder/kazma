"""F7 — Majlis fast-path length guard (audit fix).

The Majlis cultural fast-path short-circuits greetings/farewells before the
LLM. F7 ensures a greeting FOLLOWED BY a real request is NOT swallowed by the
canned reply — it must fall through to the graph. The decision lives in
``_majlis_fast_path_reply`` (returns None to fall through).
"""
from __future__ import annotations

from kazma_gateway.agent_handler.graph import (
    _MAJLIS_FAST_PATH_MAX_LEN,
    _majlis_fast_path_reply,
)


class TestMajlisFastPathGuard:
    def test_short_farewell_short_circuits(self):
        """A short pure farewell gets the canned reply."""
        reply = _majlis_fast_path_reply("مع السلامة")
        assert reply is not None
        assert "في أمان الله" in reply

    def test_short_greeting_short_circuits(self):
        """A short pure greeting gets a cultural reply (non-None)."""
        reply = _majlis_fast_path_reply("السلام عليكم")
        assert reply is not None
        assert len(reply.strip()) > 0

    def test_long_farewell_falls_through(self):
        """A farewell longer than the cap is treated as greeting+task."""
        long_farewell = "مع السلامة " + "شكرا جزيلا على كل المساعدة التي قدمتها لي اليوم " * 3
        assert len(long_farewell.strip()) > _MAJLIS_FAST_PATH_MAX_LEN
        assert _majlis_fast_path_reply(long_farewell) is None

    def test_greeting_plus_task_falls_through(self):
        """F7 core: a greeting followed by a real request must reach the graph.

        'صباح الخير' (good morning) is a greeting pattern; appending a long
        research request pushes it past the cap → None (no canned reply).
        """
        text = (
            "صباح الخير، ابحث لي عن أحدث تطورات الذكاء الاصطناعي في المنطقة "
            "وقدم لي تقريرا مفصلا عن أهم الشركات الناشئة"
        )
        assert len(text.strip()) > _MAJLIS_FAST_PATH_MAX_LEN
        assert _majlis_fast_path_reply(text) is None

    def test_non_greeting_falls_through(self):
        """A normal task message never triggers the fast-path."""
        assert _majlis_fast_path_reply("reproduce this PDF with better templates") is None

    def test_empty_falls_through(self):
        assert _majlis_fast_path_reply("") is None
        assert _majlis_fast_path_reply("   ") is None

    def test_cap_is_reasonable(self):
        """The cap is positive and bounded (guards against accidental 0/huge)."""
        assert 0 < _MAJLIS_FAST_PATH_MAX_LEN <= 200
