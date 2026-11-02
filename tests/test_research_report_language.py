"""A research question in Arabic gets an Arabic report (FA-238).

The 2026-11 functional audit, on live: two Arabic topics
(``rs_e6b8d713c07a47d2``, ``rs_5712615b648d483c``) were written up in English
throughout. The pipeline passed ``language`` to the query planner only; the
synthesis prompt had no language at all, and the Research page sends none.

The topic's language is detected when none is given, the synthesis model is
told to write in it, and the report's own headings follow it. A scripted model
on the real synthesis path; the old prompt (no instruction) is the negative
control.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kazma_core.tools import research_synthesize as rs

AR_TOPIC = "ما تاريخ صناعة اللؤلؤ في الكويت قبل النفط؟"
EN_TOPIC = "What was the history of pearling in Kuwait before oil?"


class _Scripted:
    def __init__(self) -> None:
        self.seen: list[list[dict[str, Any]]] = []

    async def chat(self, messages=None, tools=None, *args, **kwargs):
        from kazma_core.llm_provider import LLMResponse

        self.seen.append(list(messages or []))
        return LLMResponse(content="## Findings\nPearling was the main trade.", tool_calls=[],
                           finish_reason="stop", model="stub", usage={"total_tokens": 1}, cost_usd=0.0)


@pytest.fixture()
def model(tmp_path, monkeypatch):
    from kazma_core.web_acquire import research_llm

    scripted = _Scripted()
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "a.md").write_text("Pearling in Kuwait, 1900-1940: dhows, divers.", encoding="utf-8")
    monkeypatch.setattr(rs, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(research_llm, "resolved_route", lambda: (scripted, "stub", None))
    return scripted


def _synthesize(topic: str, language: str = "") -> str:
    return asyncio.run(rs.synthesize_from_digests(["research/a.md"], question=topic, language=language))


def _system(model: _Scripted) -> str:
    return next(m["content"] for m in model.seen[-1] if m["role"] == "system")


def test_an_arabic_topic_is_written_in_arabic(model):
    out = _synthesize(AR_TOPIC)
    assert "Write the whole analysis in Arabic" in _system(model)
    assert out.startswith("# خلاصة البحث") and "**السؤال:**" in out


def test_an_english_topic_stays_english(model):
    out = _synthesize(EN_TOPIC)
    assert "Write the whole analysis in English" in _system(model)
    assert out.startswith("# Research synthesis")


def test_an_asked_language_wins(model):
    _synthesize(EN_TOPIC, language="ar")
    assert "in Arabic" in _system(model)


def test_the_report_words_and_outline_follow_the_language():
    assert rs.report_language(AR_TOPIC) == "ar" and rs.report_language(EN_TOPIC) == "en"
    ar = rs.report_words("ar")
    assert ar["outline"].count("## ") == rs.report_words("en")["outline"].count("## ")
    assert "Executive" not in ar["outline"] and "Sources" not in ar["outline"]
    assert set(ar) == set(rs.report_words("en"))


def test_the_pipeline_passes_the_language_to_the_synthesis():
    """The two synthesis calls and the report's own headings read it."""
    src = Path(rs.__file__).with_name("research_pipeline.py").read_text(encoding="utf-8")
    assert src.count("language=report_lang") == 2
    assert "words['report']" in src and "words['log']" in src and 'title=words["claims"]' in src


def test_the_old_prompt_named_no_language(model, monkeypatch):
    """Negative control: the synthesis before FA-238 told the model nothing."""
    monkeypatch.setattr(rs, "report_language", lambda topic, language="": "xx-none")
    monkeypatch.setattr(rs, "_LANGUAGE_NAMES", {})
    _synthesize(AR_TOPIC)
    assert "Arabic" not in _system(model)
