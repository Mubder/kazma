"""Arabic and English as equals on every path (roadmap theme 6, 2026-11).

The 2026-11 functional audit found the same request treated differently by
language, one path at a time: a "remember this" in Arabic stored as the
model's fact (FA-228), an Arabic research topic reported in English (FA-238),
the Knowledge footer in English under Arabic answers (FA-229), numbers read
away from their words in an Arabic PDF (FA-230, FA-004). Each got its own fix.

This suite runs each user-facing path that decides something by language
once per language, through the real function, and compares the two answers.
Its first run found five more:

* "ذكرني بكرة الساعة 9 الصبح" found no time at all (no Arabic calendar
  word was parsed) while "remind me tomorrow at 9am" fired at nine;
* "سو لي تقرير PDF" was general chat while "Make a PDF report" was a
  document request, and a bare "مستند" never matched (``ال?`` made only the
  ل optional);
* "موافق" approved a plan but "approved" did not, "proceed" did but "كمّل"
  did not;
* the session-budget message was Arabic whatever the user wrote, and four
  chat-app replies were Arabic, or Arabic with English in brackets.

The class gate: every product module holding Arabic in its code (not its
docstrings or comments) is either exercised by a pair here or declared with
why it needs none -- a new Arabic rule comes with its English twin and a pair.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from kazma_core.language_lock import detect_user_language

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 2, 18, 0, tzinfo=timezone.utc)
KUWAIT = ZoneInfo("Asia/Kuwait")


@dataclass(frozen=True)
class Pair:
    id: str
    modules: tuple[str, ...]
    en: Any
    ar: Any
    run: Callable[[Any], Any]
    #: When set, both answers must be this (not only equal to each other).
    expect: Any = field(default=None)


# ── the paths ─────────────────────────────────────────────────────────────


def _lock(text: str) -> bool:
    from kazma_core.language_lock import language_lock_message

    lang = detect_user_language(text)
    return {"en": "ENGLISH", "ar": "ARABIC"}[lang] in language_lock_message(text)


def _fixed_reply(text: str) -> bool:
    from kazma_core.language_lock import in_their_language

    reply = in_their_language(text, "⚠️ This chat's budget is spent.", "⚠️ ميزانية الجلسة انتهت.")
    return detect_user_language(reply) == detect_user_language(text)


def _remember(text: str) -> bool:
    from kazma_core.memory.remember_request import is_remember_request

    return is_remember_request(text)


def _provenance(pair: tuple[str, str]) -> str:
    from kazma_core.memory.provenance import agent_write_source

    message, value = pair
    return agent_write_source(value, message)


def _small_talk(pair: tuple[str, str]) -> bool:
    from kazma_core.memory.episode_text import is_small_talk

    return is_small_talk(*pair)


def _content_words(text: str) -> int:
    from kazma_core.memory.query_terms import content_terms

    return len(content_terms(text))


def _facts(text: str) -> list[tuple[str, str]]:
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    return sorted((f["subject"], f["predicate"]) for f in extract_beliefs_heuristic(text))


def _remind(text: str) -> tuple[str, Any]:
    from kazma_core.safety.commitment.relative_time import resolve_remind

    res = resolve_remind(text, request_at=NOW, zone=KUWAIT)
    return res.decision, res.fire_at


def _document_request(text: str) -> list[tuple[str, Any]]:
    from kazma_core.agent.intent.heuristics import detect_acts

    return [(act.kind.value, act.slots.get("format")) for act in detect_acts(text)]


def _report_in_topic_language(topic: str) -> bool:
    from kazma_core.tools.research_synthesize import report_language

    return report_language(topic) == detect_user_language(topic)


def _one_paragraph(text: str) -> bool:
    from kazma_core.agent.answer_format import _requests_one_paragraph

    return _requests_one_paragraph(text)


def _json_echo(prompt: str) -> str | None:
    from kazma_core.agent.json_echo_format import repair_json_echo

    return repair_json_echo(prompt, '```json\n{"a": 1}\n```\nDone.')


def _check_in(text: str) -> bool:
    from kazma_core.agent.topic_drift import is_interrogative_checkin

    return is_interrogative_checkin(text)


def _footer_in_question_language(text: str) -> bool:
    from kazma_core.stores.knowledge_index import knowledge_footer

    return detect_user_language(knowledge_footer(["Kuwait"], text)) == detect_user_language(text)


def _continuation(text: str) -> bool:
    from kazma_core.agent.long_task import is_continuation_reply

    return is_continuation_reply(text)


def _plan_approval(text: str) -> bool:
    from kazma_core.agent.plan_mode import is_plan_approve_reply

    return is_plan_approve_reply(text)


def _login_page(title: str) -> bool:
    from kazma_core.web_acquire.quality import is_login_document

    return is_login_document(f"<html><head><title>{title}</title></head><body><form>"
                             "<input type='password' name='p'></form></body></html>")


def _opinion_ask(text: str) -> bool:
    from kazma_core.x_api.stance import is_opinion_ask

    return is_opinion_ask(text)


def _summon_side(text: str) -> str:
    from kazma_core.x_api.stance import side_from_summon

    return side_from_summon(text)


def _speech_kept(text: str) -> bool:
    from kazma_core.voice.stt import sanitize_transcript

    return sanitize_transcript(text) is not None


def _sentences(text: str) -> int:
    from kazma_core.voice.tts import split_sentences

    return len(split_sentences(text))


def _direction(text: str) -> bool:
    from kazma_core.documents.arabic import is_rtl_dominant

    return is_rtl_dominant(text) == (detect_user_language(text) == "ar")


def _runs_listing(lang: str) -> bool:
    from kazma_core.runs.text import format_listing

    return detect_user_language(format_listing([], {}, lang=lang)) == lang


def _web_command(lang: str) -> bool:
    from kazma_ui.sse_chat._command_discovery import discovery_reply

    reply = discovery_reply("/help", lang=lang) or ""
    return bool(reply) and detect_user_language(reply) == lang


def _receipt_line(turn: tuple[str, str]) -> bool:
    """A file the answer claims and no tool wrote gets its "Not done" line,
    in the answer's language (resilience plan D3)."""
    from kazma_core.agent.answer_receipts import check_answer_receipts

    question, answer = turn
    out = check_answer_receipts([{"role": "user", "content": question},
                                 {"role": "assistant", "content": answer}], {})[-1]["content"]
    added = out[len(answer):].strip()
    return bool(added) and detect_user_language(added) == detect_user_language(answer)


def _budget_texts(text: str) -> bool:
    """The turn the token budget stopped, and the turn the daily limit
    refused, answer in the user's language (resilience plan E1)."""
    from kazma_core.agent.turn_token_budget import daily_refusal_text, stop_text

    lang = detect_user_language(text)
    return all(detect_user_language(t) == lang
               for t in (stop_text(text, 520_000, 500_000), daily_refusal_text(text, 5.0, 5.0)))


def _outbox_notices(text: str) -> bool:
    """A reply that went out late, or after one that may not have arrived
    whole, opens with that line in the reader's language (plan F3)."""
    from kazma_gateway.gateway import _earlier_reply_notice, _late_reply_notice

    lang = detect_user_language(text)
    return all(detect_user_language(notice(text).splitlines()[0]) == lang
               for notice in (_earlier_reply_notice, _late_reply_notice))


def _private_notice(text: str) -> bool:
    """A sender a private chat app refuses is told so, and their id, in
    their language (kazma_gateway.sender_rule, 2026-11-11)."""
    from kazma_gateway.sender_rule import private_notice

    notice = private_notice("telegram", "12345", text)
    return detect_user_language(notice) == detect_user_language(text) and "12345" in notice


def _read_only_notice(text: str) -> bool:
    """While the database is away each chat is told once, in its language (plan A4)."""
    from kazma_ui.read_only_mode import _notice

    return detect_user_language(_notice(text)) == detect_user_language(text)


M = "kazma-core/kazma_core/"

PAIRS: list[Pair] = [
    Pair("language_lock", (M + "language_lock.py",),
         "Please summarise the meeting notes for me.", "لخص لي ملاحظات الاجتماع لو سمحت.", _lock, True),
    Pair("fixed_reply", (M + "language_lock.py", M + "agent/graph_supervisor.py",
                         "kazma-gateway/kazma_gateway/agent_handler/graph.py"),
         "Why did it stop?", "ليش وقف؟", _fixed_reply, True),
    Pair("remember_request", (M + "memory/remember_request.py",),
         "Remember that my sister's name is Noura.", "تذكر إن اسم أختي نورة.", _remember, True),
    Pair("remember_question", (M + "memory/remember_request.py",),
         "Do you remember my sister's name?", "تتذكر اسم أختي؟", _remember, False),
    Pair("provenance_held", (M + "memory/provenance.py",),
         ("Remember that my sister's name is Noura.", "Noura"), ("تذكر إن اسم أختي نورة.", "نورة"),
         _provenance, "user_explicit"),
    Pair("provenance_not_held", (M + "memory/provenance.py",),
         ("Remember that my sister's name is Noura.", "Riyadh"), ("تذكر إن اسم أختي نورة.", "الرياض"),
         _provenance, "agent_asserted"),
    Pair("small_talk", (M + "memory/episode_text.py",),
         ("thanks!", "You're welcome."), ("شكرا!", "العفو."), _small_talk, True),
    Pair("not_small_talk", (M + "memory/episode_text.py",),
         ("How old is Pixel?", "Three."), ("كم عمر بكسل؟", "ثلاث سنوات."), _small_talk, False),
    Pair("content_words", (M + "memory/query_terms.py",),
         "where does my sister live", "وين تسكن أختي", _content_words, 2),
    Pair("fact_lives_in", (M + "memory/belief_extractor.py", M + "memory/arabic_facts.py"),
         "I live in Dubai.", "أنا ساكن في دبي.", _facts, [("user", "lives_in")]),
    Pair("fact_name", (M + "memory/belief_extractor.py", M + "memory/arabic_facts.py"),
         "My name is Layla.", "اسمي ليلى.", _facts, [("user", "name_is")]),
    Pair("fact_works_at", (M + "memory/belief_extractor.py", M + "memory/arabic_facts.py"),
         "I work at Aramco.", "أشتغل عند أرامكو.", _facts, [("user", "works_at")]),
    Pair("fact_field_kept_out", (M + "memory/belief_extractor.py", M + "memory/arabic_facts.py"),
         "I work in marketing.", "أشتغل في التسويق.", _facts, []),
    Pair("remind_tomorrow_9", (M + "safety/commitment/relative_time.py",),
         "remind me tomorrow at 9am to call mom", "ذكرني بكرة الساعة 9 الصبح أتصل على أمي", _remind,
         ("allow", datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc))),
    Pair("remind_tomorrow_gulf", (M + "safety/commitment/relative_time.py",),
         "remind me tomorrow at 9am", "ذكرني باجر الساعة ٩ الصبح", _remind,
         ("allow", datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc))),
    Pair("remind_tomorrow_evening", (M + "safety/commitment/relative_time.py",),
         "remind me tomorrow at 9pm", "ذكرني غداً الساعة 9 مساءً", _remind,
         ("allow", datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc))),
    Pair("remind_day_after", (M + "safety/commitment/relative_time.py",),
         "remind me the day after tomorrow at 10am", "ذكرني بعد بكرة الساعة 10 الصبح", _remind,
         ("allow", datetime(2026, 11, 4, 7, 0, tzinfo=timezone.utc))),
    Pair("remind_next_week", (M + "safety/commitment/relative_time.py",),
         "remind me next week", "ذكرني الأسبوع الجاي", _remind),
    Pair("remind_next_month", (M + "safety/commitment/relative_time.py",),
         "remind me next month", "ذكرني الشهر القادم", _remind),
    Pair("remind_in_hours", (M + "safety/commitment/relative_time.py",),
         "remind me in 2 hours", "ذكرني بعد ساعتين", _remind),
    Pair("document_pdf", (M + "agent/intent/heuristics.py",),
         "Make a PDF report about the history of Kuwait", "سو لي تقرير PDF عن تاريخ الكويت",
         _document_request, [("document_generate", "pdf")]),
    Pair("document_write", (M + "agent/intent/heuristics.py",),
         "Write a PDF summary of the meeting", "اكتب ملخص PDF للاجتماع",
         _document_request, [("document_generate", "pdf")]),
    Pair("document_prepare", (M + "agent/intent/heuristics.py",),
         "Prepare a PDF of the agenda", "جهز لي PDF لجدول الأعمال",
         _document_request, [("document_generate", "pdf")]),
    Pair("document_word", (M + "agent/intent/heuristics.py",),
         "Make me a Word document of these notes", "اعمل لي مستند وورد لهذي الملاحظات",
         _document_request, [("document_generate", "docx")]),
    Pair("document_pdf_document", (M + "agent/intent/heuristics.py",),
         "Create a PDF document from this data", "أنشئ مستند PDF من هذه البيانات",
         _document_request, [("document_generate", "pdf")]),
    Pair("document_excel", (M + "agent/intent/heuristics.py",),
         "Make an Excel sheet of the expenses", "سوي لي ملف اكسل للمصاريف",
         _document_request, [("document_generate", "xlsx")]),
    Pair("document_powerpoint", (M + "agent/intent/heuristics.py",),
         "Make a PowerPoint about the plan", "سو لي بوربوينت عن الخطة",
         _document_request, [("document_generate", "pptx")]),
    Pair("document_not_requested", (M + "agent/intent/heuristics.py",),
         "What do you think about this PDF?", "شو رأيك في هذا الـ PDF؟",
         _document_request, [("general", None)]),
    Pair("market_is_not_make", (M + "agent/intent/heuristics.py",),
         "Where is the market?", "وين السوق؟", _document_request, [("general", None)]),
    Pair("report_language", (M + "tools/research_synthesize.py",),
         "History of Kuwait before oil", "تاريخ الكويت قبل النفط", _report_in_topic_language, True),
    Pair("one_paragraph", (M + "agent/answer_format.py",),
         "Answer in one paragraph: what is DNS?", "جاوب في فقرة وحدة: شنو هو DNS؟", _one_paragraph, True),
    Pair("json_echo", (M + "agent/json_echo_format.py",),
         'Return only this JSON object verbatim: {"a": 1}', 'أعد كائن JSON التالي فقط كما هو: {"a": 1}',
         _json_echo, '{"a": 1}'),
    Pair("check_in", (M + "agent/topic_drift.py",), "what's going on?", "شصار؟", _check_in, True),
    Pair("knowledge_footer", (M + "stores/knowledge_index.py",),
         "What was Kuwait's population in 1957?", "كم كان عدد سكان الكويت في 1957؟",
         _footer_in_question_language, True),
    Pair("continuation", (M + "agent/long_task.py",), "keep going", "كمّل", _continuation, True),
    Pair("not_continuation", (M + "agent/long_task.py",),
         "write me a poem", "اكتب لي قصيدة", _continuation, False),
    Pair("plan_approved", (M + "agent/plan_mode.py",), "approved", "موافق", _plan_approval, True),
    Pair("plan_proceed", (M + "agent/plan_mode.py",), "proceed", "كمّل", _plan_approval, True),
    Pair("plan_do_it", (M + "agent/plan_mode.py",), "do it", "سوّها", _plan_approval, True),
    Pair("plan_approve_the_plan", (M + "agent/plan_mode.py",),
         "approve the plan", "اعتمد الخطة", _plan_approval, True),
    Pair("plan_question", (M + "agent/plan_mode.py",),
         "what is the plan?", "شنو الخطة؟", _plan_approval, False),
    Pair("login_page", (M + "web_acquire/quality.py",),
         "Sign in | Example", "تسجيل الدخول | مثال", _login_page, True),
    Pair("opinion_ask", (M + "x_api/stance.py",),
         "what do you think about this?", "شو رأيك في هذا؟", _opinion_ask, True),
    Pair("summon_against", (M + "x_api/stance.py",),
         "attack this take", "اهجم على هالرأي", _summon_side, "against"),
    Pair("summon_support", (M + "x_api/stance.py",),
         "defend this", "دافع عن هذا", _summon_side, "support"),
    Pair("speech_noise", (M + "voice/stt.py",), "Thank you.", "شكرا", _speech_kept, False),
    Pair("speech_kept", (M + "voice/stt.py",), "yes", "نعم", _speech_kept, True),
    Pair("speech_sentences", (M + "voice/tts.py",),
         "Is it ready? Yes. Send it.", "هل هو جاهز؟ نعم. أرسله.", _sentences, 3),
    Pair("direction", (M + "documents/arabic.py",),
         "The meeting is on Tuesday.", "الاجتماع يوم الثلاثاء.", _direction, True),
    Pair("runs_listing", (M + "runs/text.py",), "en", "ar", _runs_listing, True),
    Pair("web_commands", ("kazma-ui/kazma_ui/sse_chat/_command_discovery.py",), "en", "ar", _web_command, True),
    Pair("receipt_not_done", (M + "agent/answer_receipts.py",),
         ("Save the report as a PDF.", "I saved report.pdf for you."),
         ("احفظ التقرير PDF.", "حفظت لك الملف report.pdf في مجلد العمل."), _receipt_line, True),
    Pair("budget_texts", (M + "agent/turn_token_budget.py",),
         "Summarise the report, please.", "لخص لي التقرير لو سمحت.", _budget_texts, True),
    Pair("outbox_notices", ("kazma-gateway/kazma_gateway/gateway.py",),
         "Here is the summary you asked for.", "هذا الملخص الذي طلبته.", _outbox_notices, True),
    Pair("read_only_notice", ("kazma-ui/kazma_ui/read_only_mode.py",),
         "hello, are you there?", "مرحبا، أنت موجود؟", _read_only_notice, True),
    Pair("private_notice", ("kazma-gateway/kazma_gateway/sender_rule.py",),
         "hello, who are you?", "مرحبا، من أنت؟", _private_notice, True),
]


@pytest.mark.parametrize("pair", PAIRS, ids=lambda p: p.id)
def test_the_same_request_in_either_language(pair: Pair) -> None:
    en, ar = pair.run(pair.en), pair.run(pair.ar)
    assert en == ar, f"{pair.id}: English {en!r} vs Arabic {ar!r}"
    if pair.expect is not None:
        assert en == pair.expect, f"{pair.id}: {en!r}, expected {pair.expect!r}"


def test_the_fact_keeps_the_users_own_words():
    """The same fact, each in its own language: never translated."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    (en,) = extract_beliefs_heuristic("I live in Dubai.")
    (ar,) = extract_beliefs_heuristic("أنا ساكن في دبي.")
    assert en["object"] == "Dubai" and ar["object"] == "دبي"


def test_the_report_speaks_its_language_in_every_heading():
    from kazma_core.tools.research_synthesize import report_words

    en, ar = report_words("en"), report_words("ar")
    assert set(en) == set(ar)
    assert all(detect_user_language(ar[key]) == "ar" for key in ar), ar


class _TrippedBreaker:
    """A cost breaker whose session budget is spent, and the node's other parts."""

    compactor = type("C", (), {"retrieve_memories": staticmethod(lambda *a, **k: [])})()

    def should_halt(self) -> bool:
        return True

    def record_user_interaction(self) -> None:
        pass

    def record_cost(self, cost: float) -> None:
        pass

    def trace_llm_call(self, **kwargs) -> None:
        pass

    async def check_and_enforce(self, state):
        return state


class _NoModel:
    async def chat(self, *args, **kwargs):  # the budget is spent: never called
        raise AssertionError("the model was called with the budget spent")


def _budget_reply(text: str) -> str:
    import asyncio

    from kazma_core.agent.graph_builder import supervisor_node

    breaker = _TrippedBreaker()
    out = asyncio.run(supervisor_node(
        {"messages": [{"role": "user", "content": text}], "iteration": 0, "max_iterations": 5,
         "tenant_id": "default", "thread_id": f"th-budget-{detect_user_language(text)}"},
        llm=_NoModel(), system_prompt="You are Kazma.", tool_definitions=[], tool_executor=None,
        cost_breaker=breaker, authority=breaker, tracer=breaker,
    ))
    return out["messages"][-1]["content"]


def test_the_supervisors_budget_reply_is_in_the_users_language():
    """The real supervisor node, its cost breaker tripped, answers in the
    language of the user's message: it was Arabic for everyone."""
    en = _budget_reply("Summarise the report, please.")
    ar = _budget_reply("لخص لي التقرير لو سمحت.")
    assert detect_user_language(en) == "en", en
    assert detect_user_language(ar) == "ar", ar


def test_negative_control_the_old_budget_reply_was_arabic_for_everyone(monkeypatch):
    monkeypatch.setattr("kazma_core.language_lock.in_their_language", lambda text, en, ar: ar)
    assert detect_user_language(_budget_reply("Summarise the report, please.")) == "ar"


def test_negative_control_a_pair_catches_an_english_only_rule(monkeypatch):
    """The reminder pair with Arabic calendar words unknown (the resolver
    before theme 6): the pair's answers differ, so the suite fails."""
    from kazma_core.safety.commitment import relative_time

    monkeypatch.setattr(relative_time, "_RE_CALENDAR_AR", re.compile(r"(?!x)x"))
    pair = next(p for p in PAIRS if p.id == "remind_tomorrow_9")
    assert pair.run(pair.en) != pair.run(pair.ar)


# ── the class gate ───────────────────────────────────────────────────────

#: Product modules with Arabic in their code that need no pair here, and why.
DECLARED: dict[str, str] = {
    "kazma-gateway/kazma_gateway/adapters/whatsapp.py":
        "two short notes (a file it could not download, an approval only the owner may answer) "
        "written in both languages; WhatsAppAdapter._say passes the pair to "
        "language_lock.in_their_language, which picks by what the person wrote",
    "kazma-gateway/kazma_gateway/agent_handler/hitl.py":
        "the restart notice of a continued turn is two fixed strings that "
        "language_lock.in_their_language chooses between (the fixed_reply pair runs that "
        "chooser); tests/test_turn_resume.py delivers it",
    M + "agent/command_catalog.py": "each command's description in both languages (description / "
                                    "description_ar); tests/test_slash_help_and_menu.py",
    M + "safety/card_notes.py": "the approval cards' notes worded in both languages (NOTE_TEXT); "
                                "the card's own language picks one, nothing is decided by "
                                "Arabic text; tests/test_outside_text_fetch.py",
    M + "agent/state.py": "an attribute's documentation string names an Arabic pivot phrase",
    M + "agent/task_ledger.py": "reads Kazma's OWN Arabic next-step phrasing out of its replies "
                                "(\"الآن\", \"سأقوم ب\"), beside the English forms",
    M + "agent/tool_loop_breaker.py": "a word list naming the assistant's own earlier output in both "
                                      "languages (\"draft\"/\"مسودة\"); tests/test_context_integrity.py",
    M + "agent/turn_input.py": "the short follow-up words of both languages (\"continue\"/\"أكمل\"), "
                               "one list; the continuation pairs above hold the same words",
    M + "agent_runner.py": "the product's Arabic name in the system prompt (كاظمه, never كازما)",
    M + "arabic/kuwaiti_lexicon.py": "an Arabic-only lexicon (Kuwaiti to MSA): no English counterpart exists",
    M + "config_defaults.py": "the product's Arabic name in a retired default prompt",
    M + "cultural_context.py": "Hijri month names and Gulf cultural notes: Arabic content by design",
    M + "cultural_context_enrichment.py": "Arabic greetings named in cultural guidance to the model",
    M + "dialect_detector.py": "tells Arabic dialects apart: Arabic-only by nature",
    M + "documents/engines/docx.py": "Arabic script ranges and the Arabic page footer; held by "
                                      "tests/test_document_layout.py and the fidelity corpus (EN/AR/bilingual)",
    M + "documents/engines/html.py": "Arabic punctuation in the URL end rule; tests/test_document_layout.py",
    M + "documents/fidelity.py": "the fidelity corpus's EN/AR/bilingual cases (tests/test_document_fidelity.py)",
    M + "documents/hostile_corpus.py": "a hostile Arabic file name in the hostile corpus",
    M + "documents/ocr/neural.py": "Arabic OCR reading rules; tests/test_document_ocr_reading_rules.py",
    M + "documents/operations.py": "Arabic script ranges in fuzzy content checks",
    M + "documents/parsers/pdf_rtl.py": "right-to-left PDF reading; tests/test_document_pdf_rtl.py and "
                                         "tests/test_document_pdf_number_stays_beside_its_words.py (FA-230)",
    M + "documents/rich_render.py": "Arabic script ranges for shaping; tests/test_document_layout.py",
    M + "documents/style_theme.py": "the Arabic page label beside the English one (Page / صفحة)",
    M + "kuwaiti_tokenizer.py": "Arabic punctuation for an Arabic-only tokenizer",
    M + "majlis.py": "Gulf hospitality phrases: Arabic content by design",
    M + "majlis_runtime.py": "an Arabic farewell of the Majlis persona",
    M + "memory/recall.py": "an Arabic example (\"انشرها\") in guidance to the model, beside the English",
    M + "msa_tokenizer.py": "Arabic letter normalisation: Arabic-only by nature",
    M + "observability/alert_catalog.py": "each alert's card title in both languages (title / title_ar); "
                                          "tests/test_alert_catalog.py checks every entry has both",
    M + "pacing.py": "Gulf greetings for pacing: Arabic content by design",
    M + "personalities.py": "each personality's description in both languages (description / description_ar)",
    M + "product_knowledge.py": "the product's Arabic name and its Arabic facts for the model",
    M + "rbac.py": "a division's Arabic name beside its English name (name / name_ar)",
    M + "runs/kinds.py": "each run kind's label in both languages (label / label_ar); runs_listing pair",
    M + "runs/notify.py": "each incident wording as an (English, Arabic) pair; runs_listing pair",
    M + "tone_adapter.py": "Arabic honorifics: Arabic content by design",
    M + "web_acquire/monitor.py": "each alert title as an (English, Arabic) pair; tests/test_acquisition_monitor.py",
    M + "web_acquire/operations.py": "each status word in both languages (an en and an ar table)",
    M + "x_api/_text/regexp/valid_cctld.py": "a vendored list of internet country domains",
    M + "x_api/_text/regexp/valid_gtld.py": "a vendored list of internet top-level domains",
    M + "x_api/reply_style.py": "strong-language words of both languages in one pattern",
    M + "x_api/subject_policy.py": "the Arabic article rule for matching a subject's name",
    "kazma-skills/kazma_skills/native/arabic_bilingual_nlp/tools.py": "a bilingual NLP skill: Hijri dates written in Arabic",
    "kazma-skills/kazma_skills/native/email_manager/analyze.py": "the product's Arabic name in a prompt",
    "kazma-ui/kazma_ui/config_errors.py": "a settings-save error in the PAGE's language (current_language), "
                                          "English otherwise; tests/test_durable_config_outage.py",
}

_ARABIC = re.compile("[؀-ۿ]")


def _arabic_in_code(source: str) -> bool:
    """True when a string literal that is not a docstring holds Arabic."""
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    return any(isinstance(node, ast.Constant) and isinstance(node.value, str)
               and id(node) not in docstrings and _ARABIC.search(node.value)
               for node in ast.walk(tree))


def _modules_with_arabic_code() -> set[str]:
    listed = subprocess.run(["git", "ls-files", "kazma-*"], cwd=ROOT, capture_output=True, text=True,
                            check=True).stdout.split()
    out = set()
    for rel in listed:
        if not rel.endswith(".py") or "/tests/" in rel or "/i18n/" in rel:
            continue
        source = (ROOT / rel).read_text(encoding="utf-8")
        if _ARABIC.search(source) and _arabic_in_code(source):
            out.add(rel)
    return out


def test_every_arabic_rule_has_a_pair_or_a_reason():
    paired = {module for pair in PAIRS for module in pair.modules}
    found = _modules_with_arabic_code()
    missing = sorted(found - paired - set(DECLARED))
    assert not missing, ("product modules decide by Arabic text with no English pair here and no "
                         f"declared reason: {missing}")
    stale = sorted(m for m in DECLARED if m not in found)
    assert not stale, f"declared modules hold no Arabic in their code any more: {stale}"
    both = sorted(set(DECLARED) & paired)
    assert not both, f"declared AND paired (drop the declaration): {both}"
    gone = sorted(m for m in paired if not (ROOT / m).is_file())
    assert not gone, f"pairs name modules that do not exist: {gone}"


def test_negative_control_the_gate_sees_a_new_arabic_rule():
    assert _arabic_in_code('_WORDS = ("نعم", "yes")\n')
    assert not _arabic_in_code('"""كاظمه in a docstring."""\nX = 1  # نعم\n')
