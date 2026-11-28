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


def _forget(text: str) -> bool:
    from kazma_core.memory.forget_request import is_forget_request

    return is_forget_request(text)


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


def _named_last(text: str) -> str | None:
    """Who the words before the last word of *text* last named."""
    from kazma_core.memory.clause_subject import subjects

    return subjects(re.findall(r"[^\s.,!?،؛؟:;]+|[.,!?،؛؟:;]", text))[-1]


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


def _link_code(text: str) -> str | None:
    """A code sent to link a chat-app account (Spaces S3-01): "link" and
    "ربط" before it name the same request."""
    from kazma_core.spaces.chat_links import code_in

    return code_in(text)


def _space_word(text: str) -> str:
    """``/space`` reads "personal" and "none" in either language (Spaces
    S3-03)."""
    from kazma_core.spaces.chat_apps import _SPACE_COMMAND, _space_word as word

    match = _SPACE_COMMAND.fullmatch(text.strip())
    return word((match.group(1) or "") if match else "")


def _linked_notice(text: str) -> bool:
    """A sender with no linked account is told how to link, in their
    language (Spaces S3-02)."""
    from kazma_gateway.sender_rule import NOT_LINKED, private_notice

    notice = private_notice("telegram", "12345", text, NOT_LINKED)
    return detect_user_language(notice) == detect_user_language(text) and "/link" in notice


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



def _memory_read_line(text: str) -> bool:
    """memory_read's line about the page is in the memory's own language,
    for a middle page and for the last one."""
    from kazma_core.agent.tool_builtins.memory import _read_header

    page = {"id": "e_1", "text": text, "offset": 0, "end": len(text), "total": 2 * len(text)}
    middle = _read_header({**page, "next_offset": len(text)})
    last = _read_header({**page, "next_offset": None})
    return all(detect_user_language(line.replace("memory_read", "")) == detect_user_language(text)
               for line in (middle, last))

def _asker_meant(text: str) -> bool:
    """Whether a question in a shared space is about its asker (Spaces S1-11)."""
    from kazma_core.memory.speakers import refers_to_asker

    return refers_to_asker(text, ["Omar", "عمر", "Sara", "سارة"])


def _members_named(text: str) -> list[str]:
    from types import MappingProxyType

    from kazma_core.memory.speakers import Member, Room

    room = Room(space="team", shared=True, members=MappingProxyType({
        "omar": Member("omar", "Omar", ("عمر",)), "sara": Member("sara", "Sara", ("سارة",))}))
    return sorted(room.named_in(text))


def _time_asked(text: str) -> tuple:
    """What a question's time words ask for (part F): the period's first and
    last day, said or true, history."""
    from datetime import UTC, datetime

    from kazma_core.memory.time_words import read_time

    ask = read_time(text, now=datetime(2026, 9, 25, 12, tzinfo=UTC).timestamp(), zone=UTC)
    if ask is None:
        return ()
    day = (lambda ts: datetime.fromtimestamp(ts, UTC).date().isoformat() if ts is not None else None)
    return (day(ask.start), day(ask.end), ask.said, ask.history)


def _kinds_asked(text: str) -> tuple[str, ...]:
    """The fact kinds a question asks by its words (the memory plan's
    finding 10): an Arabic question names the fact an English one does."""
    from kazma_core.memory.fact_names import kinds_asked
    from kazma_core.memory.query_terms import content_terms

    return kinds_asked(content_terms(text))


def _said_of_the_user(text: str) -> tuple[str, ...]:
    """The fact kinds a question asks by saying "city" or "home" of the
    user ("my city", "مدينتي"): never "home" describing another thing."""
    from kazma_core.memory.fact_names import kinds_said_of_the_user

    return kinds_said_of_the_user(text)


def _follow_up(text: str) -> bool:
    from kazma_core.memory.question_shape import is_follow_up

    return is_follow_up(text)


def _asks_for_all(text: str) -> bool:
    from kazma_core.memory.question_shape import asks_for_all

    return asks_for_all(text)


def _asks_what_was_said(text: str) -> bool:
    """A question naming nothing but talk and its time (part G)."""
    from datetime import UTC, datetime

    from kazma_core.memory.question_shape import asks_what_was_said
    from kazma_core.memory.time_words import read_time

    ask = read_time(text, now=datetime(2026, 9, 25, 12, tzinfo=UTC).timestamp(), zone=UTC)
    return asks_what_was_said(text, ask.words if ask else frozenset())


def _about_me_line(text: str) -> tuple[bool, bool]:
    """A suggested About-me line (part G) is in the language the user said
    the fact in, and a value About me already holds is seen there (the
    Arabic comma read as a comma)."""
    from kazma_core.memory import profile_suggestions as ps

    lang = "ar" if ps._is_arabic(text) else "en"
    value = "بورتو" if lang == "ar" else "Porto"
    line = ps._line_for("lives_in", value, lang) or ""
    return (detect_user_language(line) == detect_user_language(text), ps._says_it_already(text, value))


def _lesson_signal(text: str) -> str | None:
    """What a turn teaches from (decision 12): a correction of the previous
    answer, or the user's feedback; None for a routine turn."""
    from kazma_core.skills.lesson_signal import read_signal

    found = read_signal([
        {"role": "user", "content": "Find the config file."},
        {"role": "assistant", "content": "Here is config.yaml from the project folder."},
        {"role": "user", "content": text},
        {"role": "assistant", "content": "Done."},
    ])
    return found.kind if found else None


M = "kazma-core/kazma_core/"

def _an_event_of_the_users(case: tuple[str, str]) -> bool:
    from kazma_core.memory.event_text import stands_elsewhere

    return not stands_elsewhere(case[1], case[0])


def _denies(text: str) -> bool:
    from kazma_core.memory.event_text import denies

    return denies(text)


def _corrects(text: str) -> bool:
    from kazma_core.memory.event_text import corrects

    return corrects(text)


def _pasted(text: str) -> bool:
    """Whether the message's last word stands in what is clearly pasted or
    another person's."""
    from kazma_core.memory.pasted_text import told_and_pasted

    last = text.split()[-1]
    return last in told_and_pasted(text)[1]


def _to_come(case: tuple[str, str]) -> bool:
    from kazma_core.memory.time_words import says_to_come

    return says_to_come(case[0], case[1], "2026-05-31")


def _asks_what_was_said_then(text: str) -> tuple[bool, float]:
    """Whether *text* asks only what was said in a period, and that period's
    length in days, asked on 19 August 2026 (``question_shape``,
    ``time_words``)."""
    from datetime import UTC, datetime

    from kazma_core.memory.question_shape import asks_what_was_said
    from kazma_core.memory.time_words import read_time

    ask = read_time(text, now=datetime(2026, 8, 19, 12, tzinfo=UTC).timestamp(), zone=UTC)
    if ask is None or ask.start is None:
        return False, 0.0
    return asks_what_was_said(text, ask.words), round((ask.end - ask.start) / 86400, 1)


def _reads_a_time(text: str) -> bool:
    """Whether ``time_words`` reads any time (a period, or what was true
    before) in *text*."""
    from datetime import UTC, datetime

    from kazma_core.memory.time_words import read_time

    return read_time(text, now=datetime(2026, 8, 19, 12, tzinfo=UTC).timestamp(), zone=UTC) is not None


def _start_of(text: str) -> tuple[bool, bool, float]:
    """What ``time_words`` reads of a "start of": any time, whether it is
    what was true before, and the period's length in days."""
    from datetime import UTC, datetime

    from kazma_core.memory.time_words import read_time

    ask = read_time(text, now=datetime(2026, 8, 19, 12, tzinfo=UTC).timestamp(), zone=UTC)
    if ask is None:
        return False, False, 0.0
    return True, bool(ask.history), round((ask.end - ask.start) / 86400, 1) if ask.start is not None else 0.0


def _events_lost(lang: int) -> int:
    """Of the openings twinned in ``fixtures/memory_events/openings.json`` and
    the events told after each, how many one language loses."""
    import json

    from kazma_core.memory.event_text import stands_elsewhere

    cases = json.loads((ROOT / "tests/fixtures/memory_events/openings.json").read_text(encoding="utf-8"))
    return sum(stands_elsewhere(told["value"][lang], opening[lang].format(told["said"][lang]), day=told["day"] or "",
                                said_on="2026-05-31")
               for opening in (*cases["openings"]["review"], *cases["openings"]["more"]) for told in cases["events"])


def _says_the_30th(text: str) -> bool:
    from kazma_core.memory.time_words import says_day

    return says_day(text, "2026-05-30", "2026-05-31")


def _names_subject(case: tuple[str, str]) -> bool:
    from kazma_core.memory.held_facts import _Said

    return _Said(case[0]).names(case[1]) is not None


def _list_finds(case: tuple[str, str]) -> bool:
    from kazma_core.memory.word_search import Asked

    return Asked.of(case[1], whole=True).any(case[0])


def _same_person(case: tuple[str, str]) -> bool:
    """One name in two alphabets (W6, step 2), whichever is held."""
    from kazma_core.memory.name_alphabets import same_name

    return same_name(*case)


def _names_person(case: tuple[str, str]) -> bool:
    from kazma_core.memory.held_facts import _Said

    return _Said(case[0]).names(case[1], person=True) is not None


def _credential_masked(text: str) -> bool:
    """A credential label hides the value in either language. True when it did.

    A JSON object is also masked by key name (``mask_deep``), which is how a
    dict reaches the receipt before the text masker sees it.
    """
    import json

    from kazma_core.errors import _REDACT, redact_secrets

    secret = "arpw-AR99" if "arpw-AR99" in text else "envplain-EE11"
    for masked in (redact_secrets(text), _REDACT.sub("<redacted>", text)):
        if secret in masked or "<redacted>" not in masked:
            return False
    if text.lstrip().startswith("{"):
        from kazma_core.security.config_mask import mask_deep

        dumped = json.dumps(mask_deep(json.loads(text)), ensure_ascii=False)
        if secret in dumped or "***" not in dumped:
            return False
    return True


def _plain_sentence_unchanged(text: str) -> bool:
    """A sentence with no credential label is left as it was."""
    from kazma_core.errors import _REDACT, redact_secrets

    return redact_secrets(text) == text and _REDACT.sub("<redacted>", text) == text


PAIRS: list[Pair] = [
    Pair("lesson_correction", (M + "skills/lesson_signal.py",),
         "No, I meant the config in the other folder.", "لا، قصدي الملف اللي في المجلد الثاني.",
         _lesson_signal, "correction"),
    Pair("lesson_feedback", (M + "skills/lesson_signal.py",),
         "From now on keep your answers short.", "من الحين اختصر ردودك.", _lesson_signal, "feedback"),
    Pair("lesson_routine", (M + "skills/lesson_signal.py",),
         "What is the weather tomorrow?", "كيف الجو بكرة؟", _lesson_signal, None),
    Pair("lesson_thanks", (M + "skills/lesson_signal.py",),
         "Thanks a lot!", "شكرا جزيلا!", _lesson_signal, None),
    Pair("time_said_last_month", (M + "memory/time_words.py",),
         "What did I tell you last month?", "شنو قلت لك الشهر الماضي؟", _time_asked,
         ("2026-08-01", "2026-09-01", True, False)),
    Pair("time_true_in_june", (M + "memory/time_words.py",),
         "Where was I living in June?", "وين كنت ساكن في يونيو؟", _time_asked,
         ("2026-06-01", "2026-07-01", False, False)),
    Pair("time_at_first", (M + "memory/time_words.py",),
         "Where did we live at first?", "في البداية وين كنا ساكنين؟", _time_asked,
         (None, None, False, True)),
    Pair("follow_up", (M + "memory/question_shape.py",),
         "And where does she work?", "ووين تشتغل؟", _follow_up, True),
    Pair("not_follow_up", (M + "memory/question_shape.py",),
         "Where does my sister work?", "وين تشتغل أختي؟", _follow_up, False),
    Pair("follow_up_when", (M + "memory/question_shape.py",),
         "And when?", "ومتى؟", _follow_up, True),
    Pair("asks_for_all_of_mine", (M + "memory/question_shape.py",),
         "Give me all my plans", "عطني كل خططي", _asks_for_all, True),
    Pair("asks_for_a_list", (M + "memory/question_shape.py",),
         "List the plans", "عطني قائمة الخطط", _asks_for_all, True),
    Pair("not_asks_for_all", (M + "memory/question_shape.py",),
         "I run daily", "أركض كل يوم", _asks_for_all, False),
    Pair("asks_for_all", (M + "memory/question_shape.py",),
         "How many times did I go to the gym?", "كم مرة رحت النادي؟", _asks_for_all, True),
    Pair("about_me_line", (M + "memory/profile_suggestions.py",),
         "I live in Porto, near the river.", "أسكن في بورتو، قرب النهر.", _about_me_line, (True, True)),
    Pair("asks_what_was_said", (M + "memory/question_shape.py",),
         "What did we talk about back in June?", "شنو تكلمنا عنه في يونيو؟", _asks_what_was_said, True),
    Pair("names_a_topic", (M + "memory/question_shape.py",),
         "What did I tell you about Pixel in August?", "وش قلت لك عن بكسل في أغسطس؟",
         _asks_what_was_said, False),
    Pair("language_lock", (M + "language_lock.py",),
         "Please summarise the meeting notes for me.", "لخص لي ملاحظات الاجتماع لو سمحت.", _lock, True),
    Pair("fixed_reply", (M + "language_lock.py", M + "agent/graph_supervisor.py",
                         "kazma-gateway/kazma_gateway/agent_handler/graph.py"),
         "Why did it stop?", "ليش وقف؟", _fixed_reply, True),
    Pair("memory_read_line", (M + "agent/tool_builtins/memory.py",),
         "I booked the flight to Lisbon for the third of May and paid with the work card.",
         "حجزت رحلة لشبونة يوم ثلاثة مايو ودفعت بالبطاقة حق الشغل.", _memory_read_line, True),
    Pair("remember_request", (M + "memory/remember_request.py",),
         "Remember that my sister's name is Noura.", "تذكر إن اسم أختي نورة.", _remember, True),
    Pair("remember_question", (M + "memory/remember_request.py",),
         "Do you remember my sister's name?", "تتذكر اسم أختي؟", _remember, False),
    Pair("forget_request", (M + "memory/forget_request.py",),
         "Can you forget what I said about Dana?", "تقدر تنسى اللي قلته عن دانة؟", _forget, True),
    Pair("forget_not_a_request", (M + "memory/forget_request.py",),
         "I moved to Porto last month.", "انتقلت إلى بورتو الشهر الماضي.", _forget, False),
    Pair("forget_dont_forget", (M + "memory/forget_request.py",),
         "Don't forget my birthday.", "لا تنسى عيد ميلادي.", _forget, False),
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
    Pair("asked_where_i_live", (M + "memory/fact_names.py",),
         "Where do I live now?", "وين أسكن الحين؟", _kinds_asked, ("lives_in",)),
    Pair("asked_my_name", (M + "memory/fact_names.py",),
         "What is my name?", "شنو اسمي؟", _kinds_asked, ("name_is", "preferred_name")),
    Pair("asked_nothing_of_the_user", (M + "memory/fact_names.py",),
         "What time is it in Tokyo?", "كم الساعة في طوكيو؟", _kinds_asked, ()),
    Pair("said_of_the_user", (M + "memory/fact_names.py",),
         "Which city is home for me at the moment?", "أي مدينة صارت بيتي هالفترة؟", _said_of_the_user,
         ("lives_in",)),
    Pair("not_said_of_the_user", (M + "memory/fact_names.py",),
         "What is my home Wi-Fi network called?", "شنو اسم شبكة الواي فاي في بيتي؟", _said_of_the_user, ()),
    # A kind of fact is asked in either language, whatever the model named it (W6, step 2).
    Pair("asked_my_plans", (M + "memory/fact_names.py",),
         "What are my plans?", "شنو خططي؟", _said_of_the_user, ("plans",)),
    Pair("asked_what_i_bought", (M + "memory/fact_names.py",),
         "What did I buy?", "شنو شريت؟", _said_of_the_user, ("bought",)),
    Pair("asked_what_i_do", (M + "memory/fact_names.py",),
         "What do I do for a living?", "شنو شغلتي؟", _said_of_the_user, ("current_role",)),
    Pair("asked_nothing_of_what_i_bought", (M + "memory/fact_names.py",),
         "What can I buy here?", "شنو أشتري هدية؟", _said_of_the_user, ()),
    Pair("asked_a_cars_number", (M + "memory/fact_names.py",),
         "What is my car's plate number?", "وش رقم سيارتي؟", _said_of_the_user, ()),
    Pair("fact_lives_in", (M + "memory/belief_extractor.py", M + "memory/arabic_facts.py"),
         "I live in Dubai.", "أنا ساكن في دبي.", _facts, [("user", "lives_in")]),
    Pair("fact_name", (M + "memory/belief_extractor.py", M + "memory/arabic_facts.py"),
         "My name is Layla.", "اسمي ليلى.", _facts, [("user", "name_is")]),
    Pair("fact_works_at", (M + "memory/belief_extractor.py", M + "memory/arabic_facts.py"),
         "I work at Aramco.", "أشتغل عند أرامكو.", _facts, [("user", "works_at")]),
    Pair("fact_field_kept_out", (M + "memory/belief_extractor.py", M + "memory/arabic_facts.py"),
         "I work in marketing.", "أشتغل في التسويق.", _facts, []),
    Pair("fact_someone_else_kept_out", (M + "memory/belief_extractor.py", M + "memory/arabic_facts.py"),
         "My sister moved to Rotterdam.", "أختي انتقلت إلى روتردام.", _facts, []),
    Pair("own_words_translation_kept_out", (M + "memory/own_words.py",),
         "Translate to French: My name is Pierre.", "ترجم للإنجليزي: اسمي سارة.", _facts, []),
    Pair("own_words_correction_kept", (M + "memory/own_words.py",),
         "Correct: my name is Ahmad.", "صحح: اسمي أحمد.", _facts, [("user", "name_is")]),
    Pair("own_words_reported_kept_out", (M + "memory/own_words.py",),
         "My sister said: I live in Lyon.", "قالت أختي: ساكنة في ليون.", _facts, []),
    Pair("own_words_after_asking_kazmas_view", (M + "memory/own_words.py",),
         "What do you think about my rugs? I live in Dubai.", "شنو رايك في سجادي؟ أنا ساكن في دبي.", _facts,
         [("user", "lives_in")]),
    Pair("own_words_after_do_you_think", (M + "memory/own_words.py",),
         "Do you think so? I live in Dubai.", "تعتقد؟ أنا ساكن في دبي.", _facts, [("user", "lives_in")]),
    Pair("own_words_after_asking_for_ideas", (M + "memory/own_words.py",),
         "Do you have recipe ideas that feature cumin? I live in Dubai.",
         "شنو يناسب هذا الجبن؟ أنا ساكن في دبي.", _facts, [("user", "lives_in")]),
    Pair("own_words_i_want_to_tell_you", (M + "memory/own_words.py",),
         "I want to tell you: I live in Dubai.", "بغيت أقولك: أنا ساكن في دبي.", _facts, [("user", "lives_in")]),
    Pair("own_words_asked_about_a_text_kept_out", (M + "memory/own_words.py",),
         "What do you think about this text?\nI live in Dubai.", "ما رأيك في هذا النص؟\nأنا ساكن في دبي.", _facts, []),
    Pair("own_words_asked_what_someone_thinks_kept_out", (M + "memory/own_words.py",),
         "Do you know what Ali thinks? I live in Dubai.", "شنو يعتقد علي؟ أنا ساكن في دبي.", _facts, []),
    Pair("an_event_in_the_question_asked", (M + "memory/event_text.py", M + "memory/own_words.py"),
         ("Do you think I should fertilize the plant I received in the mail?", "a plant in the mail"),
         ("تعتقد لازم أسمد النبتة اللي وصلتني بالبريد؟", "نبتة بالبريد"), _an_event_of_the_users, True),
    Pair("an_event_of_a_paste_kept_out", (M + "memory/event_text.py",),
         ("Translate: I received a plant in the mail.", "a plant in the mail"),
         ("ترجم: وصلتني نبتة بالبريد.", "نبتة بالبريد"), _an_event_of_the_users, False),
    Pair("an_event_after_any_opening", (M + "memory/event_text.py", M + "memory/pasted_text.py"), 0, 1, _events_lost, 0),
    Pair("pasted_after_an_instruction", (M + "memory/pasted_text.py",),
         "Translate this: I sold my car", "ترجم هذا: بعت سيارتي", _pasted, True),
    Pair("pasted_in_a_mail", (M + "memory/pasted_text.py",),
         "From: Omar\nSubject: news\n\nI sold my car", "من: عمر\nالموضوع: أخبار\n\nبعت سيارتي", _pasted, True),
    Pair("pasted_said_by_a_sister", (M + "memory/pasted_text.py",),
         "My sister said: I sold my car", "أختي قالت: بعت سيارتي", _pasted, True),
    Pair("not_pasted_after_please", (M + "memory/pasted_text.py",),
         "Please, I sold my car", "لو سمحت، بعت سيارتي", _pasted, False),
    Pair("not_pasted_under_a_heading", (M + "memory/pasted_text.py",),
         "The story: I sold my car", "القصة: بعت سيارتي", _pasted, False),
    Pair("not_pasted_when_imagined", (M + "memory/pasted_text.py",),
         "Imagine, I sold my car", "تخيل، بعت سيارتي", _pasted, False),
    Pair("a_correction_corrects", (M + "memory/event_text.py",), "Sorry, I meant Omar", "آسف أقصد عمر", _corrects, True),
    Pair("a_no_before_a_noun", (M + "memory/event_text.py",),
         "I saw the dentist on 2 May, no cavities", "زرت دكتور الأسنان يوم ٢ مايو، لا تسوس", _denies, False),
    Pair("a_denial_beside_also", (M + "memory/event_text.py",),
         "Not Paris, and I also went to Lyon", "مو باريس، ورحت ليون كمان", _denies, False),
    Pair("a_day_to_come", (M + "memory/time_words.py",),
         ("I'm going to the concert tomorrow", "2026-06-01"), ("رايح الحفلة بكرة", "2026-06-01"), _to_come, True),
    Pair("a_day_nearer_behind", (M + "memory/time_words.py",),
         ("I finished it on 10 December", "2026-12-10"), ("خلصته يوم 10 ديسمبر", "2026-12-10"), _to_come, False),
    # the step 5 follow-up's L3: "at the start of this month" asks what was
    # said in Arabic as in English
    Pair("said_at_the_start_of_this_month", (M + "memory/time_words.py", M + "memory/question_shape.py"),
         "What did we talk about at the start of this month?", "شنو تكلمنا عنه في بداية هذا الشهر؟",
         _asks_what_was_said_then, (True, 18.5)),
    Pair("said_early_this_month", (M + "memory/time_words.py", M + "memory/question_shape.py"),
         "What did we talk about at the start of this month?", "شنو تكلمنا عنه أول هذا الشهر؟",
         _asks_what_was_said_then, (True, 18.5)),
    # the fifth follow-up review's L-a: a bare "start" or "first" names no
    # time in either language
    Pair("the_start_of_a_story_is_no_time", (M + "memory/time_words.py",),
         "What is the start of the story?", "شنو بداية القصة؟", _reads_a_time, False),
    Pair("first_this_thing_is_no_time", (M + "memory/time_words.py",),
         "Where do I live, first this thing?", "وين أسكن، أول هذا الشي؟", _reads_a_time, False),
    # the sixth follow-up review's L2: one "start of" rule for both languages
    Pair("the_start_of_a_month_named", (M + "memory/time_words.py",),
         "What did we talk about at the start of January?", "شنو تكلمنا عنه في بداية يناير؟", _start_of,
         (True, True, 31.0)),
    # the seventh follow-up review's L-b: the other Arabic forms of it
    Pair("the_start_of_a_month_with_bi", (M + "memory/time_words.py",),
         "What did we talk about at the start of January?", "شنو تكلمنا عنه ببداية يناير؟", _start_of,
         (True, True, 31.0)),
    Pair("the_first_of_a_month", (M + "memory/time_words.py",),
         "What did we talk about at the start of January?", "شنو تكلمنا عنه أول يناير؟", _start_of,
         (True, True, 31.0)),
    Pair("in_the_first_of_a_month", (M + "memory/time_words.py",),
         "What did we talk about at the start of January?", "شنو تكلمنا عنه في أول يناير؟", _start_of,
         (True, True, 31.0)),
    Pair("the_start_of_a_story", (M + "memory/time_words.py",),
         "What happened at the start of the story?", "شنو صار في بداية القصة؟", _start_of, (False, False, 0.0)),
    Pair("the_start_of_ramadan", (M + "memory/time_words.py",),
         "What did I say at the start of Ramadan?", "شنو قلت في بداية رمضان؟", _start_of, (False, False, 0.0)),
    Pair("at_the_start_alone_is_at_first", (M + "memory/time_words.py",),
         "Where did I live at the start?", "وين كنت ساكن في البداية؟", _start_of, (True, True, 0.0)),
    # "قبل ثلاث أسابيع", "السنة هذي", counted stretches, "بداية أغسطس"
    Pair("three_weeks_ago", (M + "memory/time_words.py",),
         "What did I do three weeks ago?", "شنو سويت قبل ثلاث أسابيع؟", _time_asked,
         ("2026-08-31", "2026-09-09", False, False)),
    Pair("three_weeks_ago_msa", (M + "memory/time_words.py",),
         "What did I do three weeks ago?", "ماذا فعلت قبل ثلاثة أسابيع؟", _time_asked,
         ("2026-08-31", "2026-09-09", False, False)),
    Pair("this_year_gulf", (M + "memory/time_words.py",),
         "What did we talk about this year?", "شنو تكلمنا عنه السنة هذي؟", _time_asked,
         ("2026-01-01", "2026-09-25", True, False)),
    Pair("this_year_msa", (M + "memory/time_words.py",),
         "What did we talk about this year?", "شنو تكلمنا عنه هذه السنة؟", _time_asked,
         ("2026-01-01", "2026-09-25", True, False)),
    Pair("this_year_reversed", (M + "memory/time_words.py",),
         "What did we talk about this year?", "شنو تكلمنا عنه السنة هذه؟", _time_asked,
         ("2026-01-01", "2026-09-25", True, False)),
    Pair("this_year_aam", (M + "memory/time_words.py",),
         "What did we talk about this year?", "شنو تكلمنا عنه هذا العام؟", _time_asked,
         ("2026-01-01", "2026-09-25", True, False)),
    Pair("this_year_aam_reversed", (M + "memory/time_words.py",),
         "What did we talk about this year?", "شنو تكلمنا عنه العام هذا؟", _time_asked,
         ("2026-01-01", "2026-09-25", True, False)),
    Pair("this_year_gulf_aam", (M + "memory/time_words.py",),
         "What did we talk about this year?", "شنو تكلمنا عنه هالعام؟", _time_asked,
         ("2026-01-01", "2026-09-25", True, False)),
    Pair("past_ten_days", (M + "memory/time_words.py",),
         "What did we talk about in the past 10 days?", "شنو تكلمنا عنه آخر عشر أيام؟", _time_asked,
         ("2026-09-15", "2026-09-25", True, False)),
    Pair("past_ten_days_msa", (M + "memory/time_words.py",),
         "What did we talk about in the past 10 days?", "شنو تكلمنا عنه في الأيام العشرة الماضية؟", _time_asked,
         ("2026-09-15", "2026-09-25", True, False)),
    Pair("last_three_weeks", (M + "memory/time_words.py",),
         "What did we talk about in the last 3 weeks?", "شنو تكلمنا عنه آخر ثلاث أسابيع؟", _time_asked,
         ("2026-09-04", "2026-09-25", True, False)),
    Pair("last_three_weeks_msa_number", (M + "memory/time_words.py",),
         "What did we talk about in the last 3 weeks?", "شنو تكلمنا عنه آخر ثلاثة أسابيع؟", _time_asked,
         ("2026-09-04", "2026-09-25", True, False)),
    Pair("last_three_weeks_msa", (M + "memory/time_words.py",),
         "What did we talk about in the last 3 weeks?", "شنو تكلمنا عنه في الأسابيع الثلاثة الماضية؟", _time_asked,
         ("2026-09-04", "2026-09-25", True, False)),
    Pair("last_three_weeks_gone", (M + "memory/time_words.py",),
         "What did we talk about in the last 3 weeks?", "شنو تكلمنا عنه ثلاث أسابيع اللي فاتت؟", _time_asked,
         ("2026-09-04", "2026-09-25", True, False)),
    Pair("the_start_of_august", (M + "memory/time_words.py",),
         "What did we talk about at the start of August?", "شنو تكلمنا عنه بداية أغسطس؟", _start_of,
         (True, True, 31.0)),
    Pair("the_start_of_august_month_word", (M + "memory/time_words.py",),
         "What did we talk about at the start of August?", "شنو تكلمنا عنه بداية شهر أغسطس؟", _start_of,
         (True, True, 31.0)),
    Pair("not_a_counted_stretch", (M + "memory/time_words.py",),
         "What did I do with the last 3 emails?", "شنو سويت قبل ثلاث صفحات؟", _time_asked, ()),
    Pair("not_this_year", (M + "memory/time_words.py",),
         "What did we talk about the school year?", "شنو تكلمنا عنه في السنة الدراسية؟", _time_asked, ()),
    Pair("a_weekday_gone_by", (M + "memory/time_words.py",),
         ("I sold it last Friday", "2026-06-05"), ("بعته الجمعة اللي فاتت", "2026-06-05"), _to_come, False),
    Pair("a_correction_denies", (M + "memory/event_text.py",),
         "Sorry, not Ali, I meant Omar", "عفوا، مو علي، أقصد عمر", _denies, True),
    Pair("a_day_said_by_its_weekday", (M + "memory/time_words.py",),
         "It was last Saturday", "كان السبت اللي فات", _says_the_30th, True),
    Pair("a_day_of_another_date", (M + "memory/time_words.py",),
         "the email I got last month", "الإيميل اللي وصلني الشهر اللي فات", _says_the_30th, False),
    Pair("speaker_someone_else", (M + "memory/clause_subject.py",),
         "My sister is a nurse and moved", "أختي ممرضة وانتقلت", _named_last, "other"),
    Pair("speaker_sentence_end", (M + "memory/clause_subject.py",),
         "My friend told me about Kazma. Moved", "صديقي نصحني بكازما. انتقلت", _named_last, None),
    Pair("speaker_the_user", (M + "memory/clause_subject.py",),
         "My sister is in Jeddah and I moved", "أختي في جدة وأنا انتقلت", _named_last, "speaker"),
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
    Pair("not_linked_notice", ("kazma-gateway/kazma_gateway/sender_rule.py",),
         "hello, who are you?", "مرحبا، من أنت؟", _linked_notice, True),
    Pair("link_code", (M + "spaces/chat_links.py",),
         "link ABCD-2345", "ربط ABCD-2345", _link_code, "ABCD2345"),
    Pair("space_personal", (M + "spaces/chat_apps.py",),
         "/space personal", "/space شخصي", _space_word, "personal"),
    Pair("space_none", (M + "spaces/chat_apps.py",),
         "/space none", "/space لا شيء", _space_word, "none"),
    # Who a question in a shared space is about (Spaces S1-11): the asker,
    # in either language, or a member it names.
    Pair("asker_possessive", (M + "memory/speakers.py",),
         "Where is my car parked?", "وين سيارتي واقفة؟", _asker_meant, True),
    Pair("asker_verb", (M + "memory/speakers.py",),
         "What do I like to eat?", "شنو أحب آكل؟", _asker_meant, True),
    Pair("asker_not_meant", (M + "memory/speakers.py",),
         "Where does Omar live?", "وين ساكن عمر؟", _asker_meant, False),
    Pair("member_named", (M + "memory/speakers.py",),
         "When is Omar's birthday?", "متى عيد ميلاد عمر؟", _members_named, ["omar"]),
    # Whose facts the deep pass is shown (the memory plan's W6, step 1).
    Pair("held_subject_named", (M + "memory/held_facts.py",),
         ("My sister Noura is a surgeon now.", "noura"), ("أختي نورة صارت جراحة", "نورة"), _names_subject, True),
    Pair("held_subject_not_named", (M + "memory/held_facts.py",),
         ("What should I cook tonight?", "acme"), ("شنو أطبخ اليوم؟", "سارة"), _names_subject, False),
    Pair("held_small_word_name", (M + "memory/held_facts.py",),
         ("I told Will yesterday", "will"), ("كلمت علي أمس", "علي"), _names_subject, True),
    Pair("held_small_word_no_name", (M + "memory/held_facts.py",),
         ("I will call later", "will"), ("الكتاب على الطاولة", "علي"), _names_subject, False),
    # A list of memory finds a word as the user says it (W6).
    Pair("list_word_of_mine", (M + "memory/word_search.py",),
         ("a blue car", "my car"), ("سيارة زرقاء", "سيارتي"), _list_finds, True),
    Pair("list_word_told_as_mine", (M + "memory/word_search.py",),
         ("my car is blue", "cars"), ("سيارتي زرقاء", "السيارة"), _list_finds, True),
    Pair("list_word_inside_another", (M + "memory/word_search.py",),
         ("a new carpet", "car"), ("مقهوة جديدة", "قهوة"), _list_finds, False),
    # A person held in one alphabet and spoken of in the other (W6, step 2).
    Pair("held_name_other_alphabet", (M + "memory/held_facts.py", M + "memory/name_alphabets.py"),
         ("Omar moved to Doha", "عمر"), ("عمر نقل الدوحة", "omar"), _names_person, True),
    Pair("held_name_other_alphabet_another", (M + "memory/held_facts.py", M + "memory/name_alphabets.py"),
         ("Amr moved to Doha", "عمر"), ("عمار نقل الدوحة", "omar"), _names_person, False),
    Pair("one_person_two_alphabets", (M + "memory/name_alphabets.py",),
         ("noura", "نورة"), ("نورة", "noura"), _same_person, True),
    Pair("two_people_two_alphabets", (M + "memory/name_alphabets.py",),
         ("noor", "نورة"), ("نور", "noura"), _same_person, False),
    # One name is not another, and a thing is said with its article (the reviews of step 1).
    Pair("held_name_not_another", (M + "memory/held_facts.py",),
         ("I maxed out the card", "max"), ("كلمت كريمة", "كريم"), _names_person, False),
    Pair("held_name_not_another_shorter", (M + "memory/held_facts.py",),
         ("Max called", "maxine"), ("كلمت خالد", "خالدة"), _names_person, False),
    Pair("held_name_not_a_verb", (M + "memory/held_facts.py",),
         ("The car willed itself on", "will"), ("سارت السيارة", "سارة"), _names_person, False),
    Pair("held_name_not_a_place", (M + "memory/held_facts.py",),
         ("I flew to Indiana", "india"), ("سافرت الهند", "هند"), _names_person, False),
    Pair("held_name_not_a_plant", (M + "memory/held_facts.py",),
         ("I bought roses", "rosalynn"), ("شريت وردة", "ورد"), _names_person, False),
    Pair("held_two_word_name", (M + "memory/held_facts.py",),
         ("I met Abu Dhabi Bank", "abu_dhabi_bank"), ("كلمت عبدالله", "عبد_الله"), _names_person, True),
    Pair("held_two_word_name_joined", (M + "memory/held_facts.py",),
         ("Mary Ann called", "mary_ann"), ("أبو فهد وصل", "بو_فهد"), _names_person, True),
    Pair("held_role_of_mine", (M + "memory/held_facts.py",),
         ("My manager moved to Jabriya", "manager"), ("مديري نقل الجابرية", "مدير"), _names_person, True),
    Pair("held_role_of_mine_msa", (M + "memory/held_facts.py",),
         ("My manager moved to Jabriya", "manager"), ("انتقل مديري إلى الجابرية.", "مدير"), _names_person, True),
    Pair("held_role_neighbor", (M + "memory/held_facts.py",),
         ("My neighbor retired", "neighbor"), ("جاري تقاعد", "جار"), _names_person, True),
    Pair("held_role_with_the_article", (M + "memory/held_facts.py",),
         ("The doctor moved", "doctor"), ("الدكتور نقل", "دكتور"), _names_person, True),
    Pair("held_thing_of_mine", (M + "memory/held_facts.py",),
         ("My car broke down", "car"), ("سيارتي خربت", "سيارة"), _names_subject, True),
    Pair("held_thing_with_the_article", (M + "memory/held_facts.py",),
         ("The car broke down", "car"), ("السيارة خربت", "سيارة"), _names_subject, True),
    Pair("held_one_digit", (M + "memory/held_facts.py",),
         ("Room 7 is booked now.", "room_7"), ("غرفة 7 محجوزة", "غرفة_7"), _names_subject, True),
    Pair("held_another_digit", (M + "memory/held_facts.py",),
         ("Room 7 is booked now.", "room_1"), ("غرفة 7 محجوزة", "غرفة_1"), _names_subject, False),
    Pair("credential_label_password", (M + "errors.py",),
         "password: envplain-EE11", "كلمة السر: arpw-AR99", _credential_masked, True),
    Pair("credential_label_api_key", (M + "errors.py",),
         "OPENAI_API_KEY=envplain-EE11", "كلمة المرور: arpw-AR99", _credential_masked, True),
    Pair("credential_label_secret", (M + "errors.py",),
         "secret: envplain-EE11", "الرمز السري: arpw-AR99", _credential_masked, True),
    Pair("credential_label_secret_key", (M + "errors.py",),
         "api_key: envplain-EE11", "مفتاح سري: arpw-AR99", _credential_masked, True),
    Pair("credential_label_plain_sentence", (M + "errors.py",),
         "The room is ready for the meeting.", "الغرفة جاهزة للاجتماع.", _plain_sentence_unchanged, True),
    Pair("credential_label_shadda", (M + "errors.py",),
         "password: envplain-EE11", "كلمة الس\u0651ر: arpw-AR99", _credential_masked, True),
    Pair("credential_json_key", (M + "errors.py",),
         '{"password": "envplain-EE11"}', '{"كلمة المرور": "arpw-AR99"}', _credential_masked, True),
    Pair("credential_label_pin", (M + "errors.py",),
         "secret: envplain-EE11", "الرقم السري: arpw-AR99", _credential_masked, True),
    Pair("credential_label_no_colon", (M + "errors.py",),
         "password envplain-EE11", "كلمة المرور arpw-AR99", _credential_masked, True),
    Pair("credential_word_in_a_sentence", (M + "errors.py",),
         "I forgot the password yesterday.", "نسيت كلمة المرور أمس.", _plain_sentence_unchanged, True),
    Pair("credential_phrase_is", (M + "errors.py",),
         "password is envplain-EE11", "كلمة المرور هي arpw-AR99", _credential_masked, True),
    Pair("credential_phrase_fullwidth", (M + "errors.py",),
         "password\uFF1Aenvplain-EE11", "كلمة السر\uFF1Aarpw-AR99", _credential_masked, True),
    Pair("credential_phrase_joined", (M + "errors.py",),
         "api_key: envplain-EE11", "كلمةالمرور: arpw-AR99", _credential_masked, True),
    Pair("credential_phrase_passcode", (M + "errors.py",),
         "secret: envplain-EE11", "رمز المرور: arpw-AR99", _credential_masked, True),
    Pair("credential_phrase_copula_huwa", (M + "errors.py",),
         "passwd is envplain-EE11", "الرقم السري هو arpw-AR99", _credential_masked, True),
    Pair("credential_phrase_ordinary", (M + "errors.py",),
         "the password is required", "كلمة المرور لازم تكون طويلة", _plain_sentence_unchanged, True),
    Pair("credential_phrase_year", (M + "errors.py",),
         "The secret 2024 plan", "السر ٢٠٢٤ في الخطة", _plain_sentence_unchanged, True),
    Pair("credential_phrase_hyphen", (M + "errors.py",),
         "api_key rotation-policy doc", "مفتاح سري سياسة-التدوير", _plain_sentence_unchanged, True),
    Pair("credential_phrase_newline", (M + "errors.py",),
         "secret\n2. next item", "كلمة المرور\n٢. البند التالي", _plain_sentence_unchanged, True),
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
    M + "memory/benchmark.py": "the statements and questions of finding 10's gate, told and asked in "
                               "both languages (FACT_LANGUAGES): data the benchmark runs, nothing decided "
                               "by Arabic text; tests/test_memory_fact_languages.py",
    M + "memory/benchmark_kinds.py": "the facts and questions in English, Gulf and MSA (FACT_KINDS, LISTS): data the "
                                     "benchmark runs, nothing decided by Arabic text; tests/test_memory_fact_kinds.py",
    M + "memory/benchmark_held.py": "the changes told in English, Gulf and MSA (HELD_PAIRS): data the "
                                    "benchmark runs, nothing decided by Arabic text; "
                                    "tests/test_memory_held_facts_shown.py",
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
    M + "windows_trust.py": "each phrase about Windows' file check and each part's name as an en/ar pair "
                            "(WORDS, Part.name, Part.feature): nothing decided by Arabic text; "
                            "tests/test_windows_trust.py checks every pair is whole",
    M + "web_acquire/operations.py": "each status word in both languages (an en and an ar table)",
    M + "x_api/_text/regexp/valid_cctld.py": "a vendored list of internet country domains",
    M + "x_api/_text/regexp/valid_gtld.py": "a vendored list of internet top-level domains",
    M + "x_api/reply_style.py": "strong-language words of both languages in one pattern",
    M + "x_api/subject_policy.py": "the Arabic article rule for matching a subject's name",
    "kazma-skills/kazma_skills/native/arabic_bilingual_nlp/tools.py": "a bilingual NLP skill: Hijri dates written in Arabic",
    "kazma-skills/kazma_skills/native/email_manager/analyze.py": "the product's Arabic name in a prompt",
    "kazma-ui/kazma_ui/hitl_answerers.py": "the bell's line for a card waiting for the person, and the "
                                           "lines a turn-control refusal is said with (turn_lines), "
                                           "(English, Arabic) pairs chosen by the page's language or "
                                           "language_lock.in_their_language; nothing is decided by "
                                           "Arabic text; tests/test_approvals_reach_the_asker.py, "
                                           "tests/test_chat_turn_control_per_person.py",
    "kazma-ui/kazma_ui/hitl_timeout.py": "the line a turn ends with when its card's asker left the space, "
                                         "two fixed strings that language_lock.in_their_language chooses "
                                         "between; nothing is decided by Arabic text; "
                                         "tests/test_approvals_review_fixes.py",
    "kazma-ui/kazma_ui/sse_chat/__init__.py": "the refusal of a message while a card waits on another member, "
                                              "two fixed strings that language_lock.in_their_language "
                                              "chooses between; nothing is decided by Arabic text; "
                                              "tests/test_approvals_review_fixes.py",
    "kazma-ui/kazma_ui/config_errors.py": "a settings-save error in the PAGE's language (current_language), "
                                          "English otherwise; tests/test_durable_config_outage.py",
    M + "cron/scheduler.py": "a job's refusal when its person lacks the Scheduled feature, an (English, "
                             "Arabic) pair chosen by the job's language; nothing is decided by Arabic "
                             "text; tests/test_feature_grants.py",
    M + "agent/content_refusal.py": "the line an answer from another vendor begins with, two fixed "
                                    "strings that language_lock.in_their_language chooses between; "
                                    "nothing is decided by Arabic text; "
                                    "tests/test_content_refusal_failover.py",
    M + "reasoning_text.py": "the marks a sentence ends with before a closing tag: the Arabic question "
                             "mark beside the Latin ones, one rule for both languages; "
                             "tests/test_reasoning_is_never_the_answer.py (both)",
    M + "agent/turn_budget.py": "the honest end of a turn refused before it started (its space's or "
                                "person's share of turns in use, S4-04), two fixed strings that "
                                "language_lock.in_their_language chooses between; nothing is decided "
                                "by Arabic text; tests/test_space_fairness.py",
    M + "spaces/install_paid.py": "the refusal of a paid tool in a turn on the person's own key, two "
                                  "fixed strings that language_lock.in_their_language chooses between "
                                  "(both without the person's words); nothing is decided by Arabic "
                                  "text; tests/test_install_paid_tools.py",
    M + "spaces/daily_limits.py": "the daily-limit refusal as two fixed strings that "
                                  "language_lock.in_their_language chooses between; nothing is decided "
                                  "by Arabic text; tests/test_person_daily_limit.py",
    M + "spaces/approvals.py": "X Studio's refusal of a person's own click when approvers decide their "
                               "cards, an (English, Arabic) pair chosen by the page's language; nothing "
                               "is decided by Arabic text; tests/test_tool_doors.py",
    M + "spaces/features.py": "each feature's name and its refusal in both languages (NAMES, held equal "
                              "to the catalog); nothing is decided by Arabic text; "
                              "tests/test_feature_grants.py",
    M + "memory/passages.py": "one sentence-end pattern holds both languages' marks (. ! ? and the Arabic "
                              "question mark and semicolon), so a passage is cut at sentence breaks the "
                              "same way in either; nothing is decided by which language the text is in",
    "kazma-cli/kazma_cli/self_check.py": "kazma self-check's section titles, verdicts and closing lines "
                                         "in both languages; the doctor's and the security report's own "
                                         "lines stay as those checks wrote them; nothing is decided by "
                                         "Arabic text; tests/test_self_check.py",
    "kazma-cli/kazma_cli/known_gap_settings.py": "each setting-conditional known gap's title and detail "
                                                 "in both languages; whether it applies is the existing "
                                                 "setting function, not the Arabic text; "
                                                 "tests/test_self_check.py",
    "kazma-cli/kazma_cli/receipt.py": "the receipt's titles and what a signature proves, fixed strings "
                                      "in both languages; nothing is decided by Arabic text; "
                                      "tests/test_receipt.py",
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
