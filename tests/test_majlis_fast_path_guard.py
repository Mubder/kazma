"""F7 — Majlis fast-path length guard (audit fix).

The Majlis cultural fast-path short-circuits greetings/farewells before the
LLM. F7 ensures a greeting FOLLOWED BY a real request is NOT swallowed by the
canned reply — it must fall through to the graph. The decision lives in
``_majlis_fast_path_reply`` (returns None to fall through).
"""
from __future__ import annotations

import pytest

from kazma_gateway.agent_handler.graph import (
    _MAJLIS_FAST_PATH_MAX_LEN,
    _majlis_fast_path_reply,
)


class TestMajlisFastPathGuard:
    @pytest.mark.asyncio
    async def test_short_farewell_short_circuits(self):
        """A short pure farewell gets the canned reply."""
        reply = await _majlis_fast_path_reply("مع السلامة")
        assert reply is not None
        assert "في أمان الله" in reply

    @pytest.mark.asyncio
    async def test_short_greeting_short_circuits(self):
        """A short pure greeting gets a type-matched cultural reply."""
        reply = await _majlis_fast_path_reply("السلام عليكم")
        assert reply is not None
        # السلام عليكم must be answered with وعليكم السلام — NOT an
        # answer-to-شلونك like الحمد لله بخير (2026-08-15 fix).
        assert "وعليكم السلام" in reply

    # ── Type-matched replies (the reply relates to the greeting said) ─────

    @pytest.mark.asyncio
    async def test_salam_gets_wa_alaykom_reply(self):
        for text in ("السلام عليكم", "السلام عليكم ورحمة الله"):
            reply = await _majlis_fast_path_reply(text)
            assert reply is not None, text
            assert "وعليكم السلام" in reply, (text, reply)
            assert "بخير" not in reply, (text, reply)

    @pytest.mark.asyncio
    async def test_marhaba_gets_welcome_reply(self):
        for text in ("مرحبا", "مرحباً", "مرحب", "أهلا", "هلا"):
            reply = await _majlis_fast_path_reply(text)
            assert reply is not None, f"مرحبا-family must hit the fast path: {text}"
            assert any(w in reply for w in ("هلا", "أهلا", "مرحبتين", "حياك")), (text, reply)

    @pytest.mark.asyncio
    async def test_morning_greeting_gets_morning_reply(self):
        reply = await _majlis_fast_path_reply("صباح الخير")
        assert reply is not None and "صباح" in reply

    @pytest.mark.asyncio
    async def test_evening_greeting_gets_evening_reply(self):
        reply = await _majlis_fast_path_reply("مساء الخير")
        assert reply is not None and "مساء" in reply

    @pytest.mark.asyncio
    async def test_how_are_you_gets_fine_reply(self):
        reply = await _majlis_fast_path_reply("شلونك")
        assert reply is not None
        assert any(w in reply for w in ("الحمد لله", "بخير"))

    @pytest.mark.asyncio
    async def test_long_farewell_falls_through(self):
        """A farewell longer than the cap is treated as greeting+task."""
        long_farewell = "مع السلامة " + "شكرا جزيلا على كل المساعدة التي قدمتها لي اليوم " * 3
        assert len(long_farewell.strip()) > _MAJLIS_FAST_PATH_MAX_LEN
        assert await _majlis_fast_path_reply(long_farewell) is None

    @pytest.mark.asyncio
    async def test_greeting_plus_task_falls_through(self):
        """F7 core: a greeting followed by a real request must reach the graph.

        'صباح الخير' (good morning) is a greeting pattern; appending a long
        research request pushes it past the cap → None (no canned reply).
        """
        text = (
            "صباح الخير، ابحث لي عن أحدث تطورات الذكاء الاصطناعي في المنطقة "
            "وقدم لي تقريرا مفصلا عن أهم الشركات الناشئة"
        )
        assert len(text.strip()) > _MAJLIS_FAST_PATH_MAX_LEN
        assert await _majlis_fast_path_reply(text) is None

    @pytest.mark.asyncio
    async def test_non_greeting_falls_through(self):
        """A normal task message never triggers the fast-path."""
        assert await _majlis_fast_path_reply("reproduce this PDF with better templates") is None

    @pytest.mark.asyncio
    async def test_empty_falls_through(self):
        assert await _majlis_fast_path_reply("") is None
        assert await _majlis_fast_path_reply("   ") is None

    def test_cap_is_reasonable(self):
        """The cap is positive and bounded (guards against accidental 0/huge)."""
        assert 0 < _MAJLIS_FAST_PATH_MAX_LEN <= 200
