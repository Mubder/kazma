"""Text the user quoted, attached or asked Kazma to work on is never stored
as the user's own statement (2026-11, the memory framework audit's F3).

The quick pass ran its patterns over the whole message, and so did the
"remember this" check that decides a memory tool's trust. "Translate to
French: My name is Pierre" stored the user's name as Pierre, at the user's
own trust; so did a forwarded document, whose text the chat apps and the web
chat put into the user's message inside a data fence. ``memory/own_words.py``
gives what is left once fences, attachment lines, quotes, someone else's
words after a colon and the text an instruction works on are taken out, and
those paths read it. The turn keeps the whole message, and the model's deep
pass still reads all of it, at an inference's trust.
"""

from __future__ import annotations

import re
import time
from unittest.mock import patch

import pytest
from kazma_core.memory import belief_extractor
from kazma_core.memory.belief_extractor import extract_beliefs_heuristic
from kazma_core.memory.own_words import own_words
from kazma_core.memory.provenance import USER_EXPLICIT, agent_write_source
from kazma_core.memory.remember_request import is_remember_request

#: One sentence per quick-pass pattern, and the fact each gives said plainly.
STATEMENTS = [
    ("My name is Pierre.", ("name_is", "Pierre")),
    ("I live in Lyon.", ("lives_in", "Lyon")),
    ("I work at Initech.", ("works_at", "Initech")),
    ("My favourite city is lisbon.", ("favorite_city", "lisbon")),
    ("I love mondays.", ("prefers", "mondays")),
    ("I'm allergic to peanuts.", ("allergic_to", "peanuts")),
    ("اسمي سارة.", ("name_is", "سارة")),
    ("ساكنة في جدة.", ("lives_in", "جدة")),
    ("أشتغل في شركة الاتصالات.", ("works_at", "شركة الاتصالات")),
    ("لوني المفضل الأخضر.", ("favorite_color", "الأخضر")),
    ("أحب القهوة.", ("prefers", "القهوة")),
    ("عندي حساسية من الفول.", ("allergic_to", "الفول")),
]

#: Each way a message carries text that is not the user's own, as a
#: function of the planted sentence.
FORMS = {
    "translate": lambda s: f"Translate to French: {s}",
    "fix the grammar": lambda s: f"Fix the grammar: {s}",
    "summarise this email": lambda s: f"Summarise this email:\n{s}",
    "polite rephrase": lambda s: f"Hey, could you rephrase this: {s}",
    "after a sentence": lambda s: f"Thanks. Proofread the following: {s}",
    "translate (ar)": lambda s: f"ترجم للإنجليزي: {s}",
    "you translate (ar)": lambda s: f"ممكن تترجم هذا: {s}",
    "correct this text (ar)": lambda s: f"صحح هذا النص: {s}",
    "reported": lambda s: f"My sister said: {s}",
    "reported (ar)": lambda s: f"قالت أختي: {s}",
    "quote line": lambda s: f"Look at this:\n> {s}",
    "code fence": lambda s: f"```\n{s}\n```",
}


def _facts(text: str) -> set[tuple[str, str]]:
    return {(f["predicate"], f["object"]) for f in extract_beliefs_heuristic(text)}


def _attached(text: str, tmp_path, monkeypatch) -> str:
    """A message with *text* as an attached document's contents, built by
    the chat apps' own builder."""
    from kazma_gateway.agent_handler.attachments import build_user_content
    from kazma_gateway.gateway import Attachment

    monkeypatch.setattr("kazma_gateway.agent_handler.attachments.ATTACHMENT_DIR", tmp_path)
    attachment = Attachment(kind="file", mime="application/pdf", filename="letter.pdf", data=b"%PDF-1.4 letter")
    with patch("kazma_gateway.agent_handler.attachments._try_parse_attachment", return_value=text):
        content = build_user_content("Please file this.", [attachment], vision_capable=False)
    assert isinstance(content, str) and "<kazma:data" in content
    return content


@pytest.mark.parametrize(("sentence", "fact"), STATEMENTS)
def test_said_plainly_each_sentence_is_a_fact(sentence, fact):
    """The control: each planted sentence is one the quick pass reads."""
    assert fact in _facts(sentence)


@pytest.mark.parametrize("form", sorted(FORMS))
@pytest.mark.parametrize(("sentence", "fact"), STATEMENTS)
def test_quoted_text_is_not_the_users_statement(form, sentence, fact):
    assert fact not in _facts(FORMS[form](sentence)), FORMS[form](sentence)


@pytest.mark.parametrize(("sentence", "fact"), STATEMENTS)
def test_an_attached_document_is_not_the_users_statement(sentence, fact, tmp_path, monkeypatch):
    assert fact not in _facts(_attached(sentence, tmp_path, monkeypatch))


@pytest.mark.parametrize(("message", "fact"), [
    ("Correct: my name is Ahmad.", ("name_is", "Ahmad")),
    ("Correction: my name is Ahmad, not Ahmed.", ("name_is", "Ahmad")),
    ("Note: I live in Lisbon now.", ("lives_in", "Lisbon")),
    ("As I said: I live in Lisbon.", ("lives_in", "Lisbon")),
    ("Fix my calendar\nI live in Lisbon.", ("lives_in", "Lisbon")),
    ("صحح: اسمي أحمد.", ("name_is", "أحمد")),
    ("كما قلت: أنا ساكن في الكويت.", ("lives_in", "الكويت")),
    ("Call me \"Sam\" from now on.", None),
])
def test_the_users_own_words_stay_theirs(message, fact):
    """A word that also introduces a text, with no text named; a reporting
    verb the user says of themself; a quote with no first person: all the
    user's own."""
    assert own_words(message) == message
    if fact:
        assert fact in _facts(message)


def test_a_document_cannot_earn_the_users_highest_trust(tmp_path, monkeypatch):
    """"Remember this" with an attached document: a memory tool storing the
    document's words is the agent's write, never the user's own."""
    message = _attached("Remember that my name is Sara Lee.", tmp_path, monkeypatch)
    assert agent_write_source("Sara Lee", message) != USER_EXPLICIT
    assert agent_write_source("Sara Lee", "Remember that my name is Sara Lee.") == USER_EXPLICIT


@pytest.mark.parametrize("message", [
    "Translate to French: remember to call mom.",
    "ترجم للإنجليزي: تذكر إن موعدي الثلاثاء.",
    "> Remember that the meeting moved.",
])
def test_a_remember_request_inside_quoted_text_is_none(message):
    assert not is_remember_request(message)
    assert is_remember_request("Remember that the meeting moved.")  # the control


def test_the_attachment_shape_in_the_write_scenarios_is_the_builders(tmp_path, monkeypatch):
    """scripts/memory_bench.py writes its attached messages by hand; they must
    be what the builder makes, path aside."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "_memory_bench", Path(__file__).resolve().parents[1] / "scripts" / "memory_bench.py")
    bench = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(bench)
    document = "Dear team, my name is Sara Lee and I work at Acme Corp."
    built = _attached(document, tmp_path, monkeypatch)
    written = bench._attached("Please file this.", "letter.pdf", "application/pdf", document)
    path = re.compile(r"\[Full document at: [^\]]*? —")
    assert path.sub("[Full document at: X —", built) == path.sub("[Full document at: X —", written)


def test_a_long_message_is_read_in_linear_time():
    hostile = ("she said: x\n" * 20000) + ("\"I " * 20000) + ("[Attached: " * 5000) + ("> " * 20000)
    started = time.perf_counter()
    own_words(hostile)
    small = time.perf_counter() - started
    started = time.perf_counter()
    own_words(hostile * 4)
    assert time.perf_counter() - started < max(1.0, small * 12)


# ── negative control: the paths as they were ─────────────────────────────


def test_reading_the_raw_message_stores_quoted_text_as_the_users(monkeypatch, tmp_path):
    monkeypatch.setattr(belief_extractor, "own_words", lambda text: text or "")
    assert ("name_is", "Pierre") in _facts("Translate to French: My name is Pierre.")
    assert ("name_is", "سارة") in _facts("ترجم للإنجليزي: اسمي سارة.")
    assert ("works_at", "Initech") in _facts(_attached("I work at Initech.", tmp_path, monkeypatch))


def _quoted_scenarios() -> dict:
    import json

    from kazma_core.memory.benchmark import WRITES_PATH

    data = json.loads(WRITES_PATH.read_text(encoding="utf-8"))
    return {**data, "scenarios": [s for s in data["scenarios"] if s["category"] == "quoted"]}


def test_the_quoted_write_scenarios_pass_and_see_the_raw_message(monkeypatch):
    """Through the write path (both passes, the trust gate): each quoted
    scenario passes, and fails with the quick pass reading the raw message."""
    from kazma_core.memory.benchmark import run_write_scenarios

    dataset = _quoted_scenarios()
    assert len(dataset["scenarios"]) >= 6
    assert run_write_scenarios(dataset)["failing"] == []
    monkeypatch.setattr(belief_extractor, "own_words", lambda text: (text or "").strip())
    failing = run_write_scenarios(dataset)["failing"]
    assert sorted(failing) == sorted(s["id"] for s in dataset["scenarios"]), failing


def test_the_trust_gate_without_own_words_gives_a_document_the_users_trust(monkeypatch, tmp_path):
    from kazma_core.memory import provenance

    message = _attached("Remember that my name is Sara Lee.", tmp_path, monkeypatch)
    monkeypatch.setattr(provenance, "own_words", lambda text: text or "")
    monkeypatch.setattr(provenance, "is_plain", lambda text: True)  # item 2's check came after
    monkeypatch.setattr("kazma_core.memory.remember_request.own_words", lambda text: text or "")
    assert agent_write_source("Sara Lee", message) == USER_EXPLICIT


# ── the review of F3 (2026-11): what the first rules missed ──────────────

#: More ways a message carries someone else's text or a text to work on,
#: each found by the review.
MORE_FORMS = {
    "curly quote": lambda s: f"She wrote “{s}”",
    "single quote": lambda s: f"She wrote '{s}'",
    "curly single quote": lambda s: f"She wrote ‘{s}’",
    "guillemets stuck to the words": lambda s: f"got this text: «{s}»",
    "backticks": lambda s: f"Run `echo {s}`",
    "calling Kazma by name": lambda s: f"Hey Kazma, translate this: {s}",
    "a head in two languages": lambda s: f"translate للعربي: {s}",
    "a question as the head": lambda s: f"Can you translate this into Spanish? {s}",
    "no colon": lambda s: f"Translate to French {s}",
    "fix this (gulf)": lambda s: f"صلح هذا: {s}",
    "this sentence (gulf)": lambda s: f"صحح لي هالجملة: {s}",
    "make a translation (gulf)": lambda s: f"سوي لي ترجمة: {s}",
    "give a translation (gulf)": lambda s: f"عطني ترجمة: {s}",
    "write an email": lambda s: f"Write an email to my landlord saying: {s}",
    "write a message (ar)": lambda s: f"اكتب لي رسالة: {s}",
    "roleplay": lambda s: f"Roleplay as a customer: {s}",
    "example sentence": lambda s: f"Example sentence: {s}",
    "forwarded (mail)": lambda s: f"---------- Forwarded message ---------\nFrom: Dana\n{s}",
    "forwarded from": lambda s: f"Forwarded from Dana\n{s}",
    "forwarded (ar)": lambda s: f"رسالة محولة\n{s}",
    "you told me (ar)": lambda s: f"قلت لي: {s}",
    "an old stub with a bracket in its name": lambda s: f"[Attached: report] {s} (application/pdf) — use file_read]",
}


@pytest.mark.parametrize("form", sorted(MORE_FORMS))
@pytest.mark.parametrize(("sentence", "fact"), STATEMENTS)
def test_the_reviews_forms_are_not_the_users_statement(form, sentence, fact):
    assert fact not in _facts(MORE_FORMS[form](sentence)), MORE_FORMS[form](sentence)


@pytest.mark.parametrize(("message", "kept", "dropped"), [
    # Someone else's words end at their sentence: the user's answer stays.
    ("Kazma said: your name is Ahmed. No, my name is Ahmad.", ("name_is", "Ahmad"), None),
    ("You said: I live in Paris. Wrong, I live in Lisbon.", ("lives_in", "Lisbon"), ("lives_in", "Paris")),
    ("You wrote: lives in Paris. Actually I live in Lisbon now", ("lives_in", "Lisbon"), None),
    ("قلت لي: ساكن في باريس. لا، أنا ساكن في لشبونة", ("lives_in", "لشبونة"), ("lives_in", "باريس")),
    ("My sister said: I live in Oslo. I live in Bergen.", ("lives_in", "Bergen"), ("lives_in", "Oslo")),
    # An aside ends the text an instruction works on.
    ("Translate: thanks.\nBy the way, my name is Sam.", ("name_is", "Sam"), None),
    ("Translate to French: My name is Pierre. Btw, I live in Dubai.", ("lives_in", "Dubai"),
     ("name_is", "Pierre")),
    ("ترجم: شكرا.\nبالمناسبة، اسمي سام.", ("name_is", "سام"), None),
    # An inch mark is no quote.
    ("My 27\" monitor arrived and I live in Berlin, \"finally\"", ("lives_in", "Berlin"), None),
    # Idioms that look like an instruction and are the user speaking.
    ("Please check my email: I work at Initech now", ("works_at", "Initech"), None),
    ("Write this down: I live in Lisbon.", ("lives_in", "Lisbon"), None),
])
def test_the_users_own_words_beside_someone_elses_stay_theirs(message, kept, dropped):
    facts = _facts(message)
    assert kept in facts, (message, facts)
    if dropped:
        assert dropped not in facts, (message, facts)


@pytest.mark.parametrize(("message", "stored"), [
    ('remember this: "I prefer dark mode"', "I prefer dark mode"),
    ('remember: "I prefer dark mode"', "I prefer dark mode"),
    ("remember this: 'my code is ZX-41'", "my code is ZX-41"),
    ("Remember that: “my code is ZX-41”", "my code is ZX-41"),
    ("تذكر هذا: «أحب القهوة»", "أحب القهوة"),
    ("تذكر: «الكود ZX-41»", "الكود ZX-41"),
])
def test_inside_a_remember_request_the_quote_is_the_users_statement(message, stored):
    assert is_remember_request(message)
    assert stored in own_words(message)
    assert agent_write_source(stored, message) == USER_EXPLICIT


@pytest.mark.parametrize("message", [
    'she wrote: "I prefer dark mode"',
    "قالت أختي «أحب القهوة»",
])
def test_a_quote_outside_a_remember_request_is_not(message):
    assert agent_write_source("I prefer dark mode" if "dark" in message else "أحب القهوة", message) != USER_EXPLICIT


def test_control_without_the_remember_rule_the_quote_is_left_out(monkeypatch):
    from kazma_core.memory import remember_request

    monkeypatch.setattr(remember_request, "asks_to_remember", lambda text: False)
    assert "dark mode" not in own_words('remember this: "I prefer dark mode"')


def test_control_the_first_rules_missed_the_reviews_forms(monkeypatch):
    """The rules of 3adb66f2e: a reporting head ran to the end of its line, a
    quote was straight, curly or «» and judged by space-split words, a head
    stopped only at a colon or a line end and read one language."""
    from kazma_core.memory import own_words as ow

    # The security review's layers (2026-11-18) came after: off here.
    monkeypatch.setattr(ow, "_without_reported_or_asked", lambda body: body)
    monkeypatch.setattr(ow, "_without_speaker_lines", lambda body: body)
    monkeypatch.setattr(ow, "_without_pasted_chats", lambda body: body)
    monkeypatch.setattr(ow, "_QUOTED_SPAN_RE", re.compile(r"\"[^\"\n]{1,400}+\"|“[^”\n]{1,400}+”|«[^»\n]{1,400}+»"))
    monkeypatch.setattr(ow, "_speaks_in_first_person",
                        lambda span: any(w in ow._FIRST_PERSON_EN for w in ow._words(span))
                        or any(w in ow._FIRST_PERSON_AR for w in ow._fold(span).split()))
    monkeypatch.setattr(ow, "_without_forwarded", lambda body: body)
    monkeypatch.setattr(ow, "_sentence_end", lambda body, at: (body.find("\n", at) + 1) or len(body))
    assert ("name_is", "Dana") in _facts("She wrote 'My name is Dana.'")
    assert ("name_is", "نورة") in _facts("got this text: «اسمي نورة»")
    assert ("name_is", "Dana") in _facts("Forwarded from Dana\nMy name is Dana.")
    assert ("name_is", "Ahmad") not in _facts("Kazma said: your name is Ahmed. No, my name is Ahmad.")


@pytest.mark.parametrize("piece", [
    "'I ", "‘I ", "`I ", "\"I ", "You said: x. ", "Translate: x.\nBy the way, y. ",
    "قلت لي: س. ", "Forwarded \n", "[Attached: x\n", "translate this ",
    "Can you translate this? ", "Write an email saying: x\n",
])
def test_the_reviews_rules_read_a_long_message_in_linear_time(piece):
    small = piece * 4000
    started = time.perf_counter()
    own_words(small)
    once = time.perf_counter() - started
    started = time.perf_counter()
    own_words(small * 4)
    assert time.perf_counter() - started < max(1.0, once * 12), piece


# ── the security review of F1's second half (2026-11-18) ─────────────────

#: Someone else's words the first rules still filed under the user, each
#: with a fact it gave: a pasted chat or mail, a text handed over or asked
#: about, reported speech with no colon, a long reporting head, a question,
#: a quote over a line break, a quote's reporting tail.
SOMEONE_ELSES = [
    ("Dana: My name is Dana and I live in Oslo.", ("lives_in", "Oslo")),
    ("[12:01, 18/11/2026] Dana: I work at Initech now.", ("works_at", "Initech")),
    ("Me: hi\nDana: I live in Oslo.", ("lives_in", "Oslo")),
    ("نورة: اسمي نورة وأنا ساكنة في دبي", ("name_is", "نورة")),
    ("From: Dana\nSubject: hi\n\nMy name is Dana and I live in Oslo.", ("lives_in", "Oslo")),
    ("Here is the article:\n\nMy name is Dana and I live in Oslo.", ("lives_in", "Oslo")),
    ("He said, I live in Lyon.", ("lives_in", "Lyon")),
    ("My friend Ali says I live in Lyon", ("lives_in", "Lyon")),
    ("Ali told me I work at Initech", ("works_at", "Initech")),
    ("أختي تقول أنا ساكنة في دبي", ("lives_in", "دبي")),
    ("My sister who lives far away told me yesterday on the phone: I live in Lyon.", ("lives_in", "Lyon")),
    ("قالت أختي بالتلفون أمس وهي تبكي كثير: أنا ساكنة في دبي", ("lives_in", "دبي")),
    ("Her bio reads: I work at Initech.", ("works_at", "Initech")),
    ("Message from Dana: My name is Dana.", ("name_is", "Dana")),
    ("Tell me if this is spam: My name is Dana and I work at Initech", ("works_at", "Initech")),
    ("Is this sentence correct: I work at Initech", ("works_at", "Initech")),
    ("What does this mean: My name is Pierre", ("name_is", "Pierre")),
    ("How do I say my name is Pierre in French?", ("name_is", "Pierre")),
    ('She wrote "My name is Dana.\nI live in Oslo."', ("lives_in", "Oslo")),
    ("“My name is Dana” she said. I live in Oslo, she added.", ("lives_in", "Oslo")),
    ("My name is Dana, she said.", ("name_is", "Dana")),
    ("I live in Oslo, wrote Dana in her letter.", ("lives_in", "Oslo")),
    ("كتبت: اسمي نورة", ("name_is", "نورة")),
]


@pytest.mark.parametrize(("message", "fact"), SOMEONE_ELSES)
def test_someone_elses_words_are_not_the_users(message, fact):
    assert fact not in _facts(message), (message, own_words(message))


#: The user's own words the first rules lost, or new rules could: an aside
#: on its own line, facts given for the user's own bio, what follows an
#: address, a year's apostrophe, the user's own report, a clause of their
#: own after a comma, a label of their own, a statement after a question.
USERS_OWN = [
    ("Can you review my text?\nAlso my name is Sam.", ("name_is", "Sam")),
    ("Write a bio: I work at Initech and I live in Dubai.", ("works_at", "Initech")),
    ("Summarize https://x.y and my name is Sam", ("name_is", "Sam")),
    ("Summarize https://x.y/a?b=1 and my name is Sam", ("name_is", "Sam")),
    ("The '90s were great. I live in Lyon. I love rock 'n' roll", ("lives_in", "Lyon")),
    ("I told Ali I live in Paris.", ("lives_in", "Paris")),
    ("I work at Initech, which my boss says is great.", ("works_at", "Initech")),
    ("My name is Sam, as my sister said.", ("name_is", "Sam")),
    ("Me: I live in Paris", ("lives_in", "Paris")),
    ("Fun fact: I live in Lyon.", ("lives_in", "Lyon")),
    ("News: I work at Initech now.", ("works_at", "Initech")),
    ("ملاحظة: أنا ساكن في دبي", ("lives_in", "دبي")),
    ("What's your name? My name is Sam.", ("name_is", "Sam")),
    ("Translate: thanks.\nAlso, I live in Lisbon.", ("lives_in", "Lisbon")),
]


@pytest.mark.parametrize(("message", "fact"), USERS_OWN)
def test_the_users_own_words_the_review_found_stay_theirs(message, fact):
    assert fact in _facts(message), (message, own_words(message))


def test_an_also_inside_a_line_is_still_the_text():
    """"Also" ends a text to work on only at a line's start: inside it, it is
    the text going on."""
    assert ("name_is", "Pierre") not in _facts("Translate: hello. Also my name is Pierre.")


def test_a_remembered_quote_line_is_the_users_statement():
    message = "Remember this:\n> my code is ZX-41"
    assert is_remember_request(message) and "my code is ZX-41" in own_words(message)
    assert agent_write_source("code is ZX-41", message) == USER_EXPLICIT
    for other in ("Look at this:\n> my code is ZX-41", "Remember what my sister said:\n> my code is ZX-41",
                  'remember that Dana wrote "my code is ZX-41"'):
        assert agent_write_source("code is ZX-41", other) != USER_EXPLICIT, other


def test_control_the_rules_before_the_review_gave_each_fact(monkeypatch):
    """Negative control: the rules of 5a60b5f21 -- no speaker lines, no
    reported speech without a colon, no questions, no remembered quote line,
    no mail headers, heads of six words, quotes on one line, "كتبت" read as
    "I wrote", no handed-over or asked-about text -- give every fact above
    to the user, and lose the user's own (the address, the line aside, the
    '90s, the bio)."""
    from kazma_core.memory import own_words as ow

    _rules_before_rule_b(monkeypatch)  # rule B (2026-11-18) came after
    monkeypatch.setattr(ow, "_bounded", lambda body: body)
    monkeypatch.setattr(ow, "_without_speaker_lines", lambda body: body)
    monkeypatch.setattr(ow, "_without_quote_lines", lambda body: ow._QUOTE_LINE_RE.sub(" ", body))
    monkeypatch.setattr(ow, "_without_reported_or_asked", lambda body: body)
    monkeypatch.setattr(ow, "_without_addresses", lambda window: window)
    monkeypatch.setattr(ow, "_URL_RE", re.compile(r"(?!x)x"))
    monkeypatch.setattr(ow, "_LINE_ASIDES_EN", ())
    monkeypatch.setattr(ow, "_LINE_ASIDES_AR", ())
    monkeypatch.setattr(ow, "_FORWARD_EN", ("forwarded message", "begin forwarded message", "forwarded from ",
                                            "fwd:", "original message"))
    monkeypatch.setattr(ow, "_REPORTED_HEAD_WORDS", 6)
    monkeypatch.setattr(ow, "_REPORTING_EN", ow._REPORTING_EN - {"reads", "added", "adds", "answered", "explained"})
    monkeypatch.setattr(ow, "_HANDS_OVER_EN", ())
    monkeypatch.setattr(ow, "_HANDS_OVER_AR", frozenset())
    monkeypatch.setattr(ow, "_ASKS_ABOUT_EN", frozenset())
    monkeypatch.setattr(ow, "_ASKS_ABOUT_AR", frozenset())
    monkeypatch.setattr(ow, "_MESSAGE_THINGS_EN", frozenset())
    monkeypatch.setattr(ow, "_I_AR", ow._I_AR | {"كتبت"})
    monkeypatch.setattr(ow, "_WRITTEN_THINGS_EN", ow._WRITTEN_THINGS_EN | {"bio"})
    monkeypatch.setattr(ow, "_QUOTED_SPAN_RE", re.compile(
        r"(?<!\d)\"[^\"\n]{1,400}+\"|“[^”\n]{1,400}+”|«[^»\n]{1,400}+»"
        r"|(?<!\w)'(?:[^'\n]|'(?=\w)){1,400}+'(?!\w)|‘(?:[^’\n]|’(?=\w)){1,400}+’(?!\w)|`[^`\n]{1,400}+`"))
    given = [message for message, fact in SOMEONE_ELSES if fact in _facts(message)]
    assert given == [message for message, _ in SOMEONE_ELSES]
    lost = {message for message, fact in USERS_OWN if fact not in _facts(message)}
    assert {"Summarize https://x.y and my name is Sam", "Can you review my text?\nAlso my name is Sam.",
            "The '90s were great. I live in Lyon. I love rock 'n' roll",
            "Write a bio: I work at Initech and I live in Dubai."} <= lost
    assert agent_write_source("code is ZX-41", "Remember this:\n> my code is ZX-41") != USER_EXPLICIT


# ── a long message: read up to a paragraph break (fix 7 of the review) ────


def test_a_long_message_is_read_to_its_last_paragraph_break_before_the_cap():
    from kazma_core.memory.own_words import _SCAN_MAX

    start = "My name is Sam.\n\n"
    assert own_words(start + "x " * _SCAN_MAX).strip() == "My name is Sam."
    assert own_words("I live in Lyon. " + "x " * _SCAN_MAX) == ""  # one paragraph: a document
    # Nothing past the cap is read: a forward line or a head there would
    # decide what follows it, unread.
    tail = "\n\n---------- Forwarded message ---------\nMy name is Dana."
    assert "Dana" not in own_words(start + ("filler words here. " * 150) + tail)
    # A quote open at the cap is never read cut in half.
    opened = start + ("y " * (_SCAN_MAX // 2 - 40)) + 'She wrote "My name\nis Dana. ' + "z " * 200 + '"'
    assert "Dana" not in own_words(opened)


@pytest.mark.parametrize("shape", [
    "\n", "'a ", "Translate. ", "قبل سنتين ", "\"I\n", "I Lyon ", "she said: I live. ", ":", "Dana: x\n",
    "He said, I live in Lyon. ", "How do I say my name? ", "https://x.y?a ", "> x\n", "Also x\n",
])
def test_every_scanner_reads_a_long_message_in_bounded_time(shape):
    """The review measured about six microseconds a line (12 s for 2 MiB).
    Read at most ``_SCAN_MAX`` characters: four times the text costs no more
    than the cap's."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic as quick
    from kazma_core.memory.clause_subject import names_no_one_else

    def took(text: str) -> float:
        started = time.perf_counter()
        own_words(text)
        quick(text)
        names_no_one_else(text, "Lyon")
        return time.perf_counter() - started

    small = shape * (100_000 // len(shape))
    once = took(small)
    assert took(small * 4) < max(0.5, once * 3), shape


def test_control_without_the_cap_a_long_message_costs_its_length(monkeypatch):
    """Negative control: with every line read, 200,000 lines cost far more
    than the cap's."""
    from kazma_core.memory import own_words as ow

    text = "\n" * 200_000
    started = time.perf_counter()
    ow.own_words(text)
    bounded = time.perf_counter() - started
    monkeypatch.setattr(ow, "_bounded", lambda body: body)
    started = time.perf_counter()
    ow.own_words(text)
    assert time.perf_counter() - started > max(0.05, bounded * 10)


# ── rule B of the second security review of F1 (2026-11-18) ──────────────

#: The paste shapes and reported claims the review listed, each with a fact
#: the rules before it filed under the user: a WhatsApp export (a digit in
#: the label, the Arabic form with its direction marks), lower-case and
#: handle labels, a role, Telegram, Discord and Slack copy headers, a mail's
#: "On ... wrote:" running past its own line, a text of someone else's named,
#: reported speech with no listed verb, an example and a part to play.
PASTES = [
    ("18/11/2026, 12:01 - Dana: I live in Oslo.", ("lives_in", "Oslo")),
    ("11/18/26, 9:41 PM - Dana: I work at Initech.", ("works_at", "Initech")),
    ("‏١٨‏/١١‏/٢٠٢٦، ١٢:٠١ م - نورة: اسمي نورة", ("name_is", "نورة")),
    ("[18/11/2026, 12:01:33] dana_92: I live in Oslo.", ("lives_in", "Oslo")),
    ("[12:01, 18/11/2026] dana: I live in Oslo.", ("lives_in", "Oslo")),
    ("dana: I live in Oslo.", ("lives_in", "Oslo")),
    ("dana_92: I work at Initech.", ("works_at", "Initech")),
    ("the customer: My name is Pierre.", ("name_is", "Pierre")),
    ("Dana Smith, [18.11.26 12:01]\nI live in Oslo.", ("lives_in", "Oslo")),
    ("نورة، [18.11.26 12:01]\nأشتغل في شركة الاتصالات", ("works_at", "شركة الاتصالات")),
    ("Dana — Today at 12:01 PM\nI work at Initech.", ("works_at", "Initech")),
    ("dana_92 — 11/18/2026 12:01 PM\nMy name is Dana.", ("name_is", "Dana")),
    ("Dana Smith  12:01 PM\nI live in Oslo.", ("lives_in", "Oslo")),
    ("Dana Smith\n12:01 PM\nI live in Oslo.", ("lives_in", "Oslo")),
    ("On Tue, Nov 18, 2026 at 12:01 PM Dana Smith <dana@example.com> wrote:\nMy name is Dana.\nI live in Oslo.",
     ("lives_in", "Oslo")),
    ("Dana wrote:\nI live in Oslo.\nI work at Initech.", ("works_at", "Initech")),
    ("Dana wrote:\n\nI live in Oslo.", ("lives_in", "Oslo")),
    ("My sister said:\nI'm a nurse.\nI live in Rotterdam.", ("lives_in", "Rotterdam")),
    ("Dana's bio: I work at Initech.", ("works_at", "Initech")),
    ("Her message was: I live in Oslo.", ("lives_in", "Oslo")),
    # (An Arabic label at a line's start was already a speaker: these are
    # heads after a sentence.)
    ("وصلني هذا اليوم. رسالتها: اسمي سارة", ("name_is", "سارة")),
    ("وصلتني رسالة اليوم. رسالة دانة: اسمي سارة", ("name_is", "سارة")),
    ("According to Ali, I work at Initech.", ("works_at", "Initech")),
    ("I work at Initech, according to Ali.", ("works_at", "Initech")),
    ("حسب كلام أختي، أنا ساكن في دبي", ("lives_in", "دبي")),
    ("على حسب كلام علي أنا ساكن في دبي", ("lives_in", "دبي")),
    ("Ali thinks I live in Lyon.", ("lives_in", "Lyon")),
    ("Dana claims I live in Lyon.", ("lives_in", "Lyon")),
    ("He was like, I'm allergic to peanuts.", ("allergic_to", "peanuts")),
    ("She's like, I live in Lyon.", ("lives_in", "Lyon")),
    ("As Dana put it, I live in Lyon.", ("lives_in", "Lyon")),
    ("أختي كاتبة لي إني أشتغل في شركة الاتصالات", ("works_at", "شركة الاتصالات")),
    ("Pretend you are me and say: I live in Lyon.", ("lives_in", "Lyon")),
    ("Pretend you are me and say I live in Lyon.", ("lives_in", "Lyon")),
    ("تخيل إنك أنا وقل: اسمي سارة", ("name_is", "سارة")),
    ("For example: I work at Initech.", ("works_at", "Initech")),
    ("e.g.: I work at Initech.", ("works_at", "Initech")),
    ("عندي سؤال عن الكتابة اليوم. مثلا: اسمي سارة", ("name_is", "سارة")),
]

#: The user's own, beside them: a greeting or a heading before a colon, a
#: comma before a time with no copy's brackets, the user's own text and
#: report, a lower-case phrase, a step, "according to me", the user who is
#: the writer, what the user knows.
PASTES_KEPT = [
    ("Good Morning: I live in Oslo.", ("lives_in", "Oslo")),
    ("good morning: I live in Oslo.", ("lives_in", "Oslo")),
    ("صباح الخير: أنا ساكن في دبي", ("lives_in", "دبي")),
    ("Random thought: I live in Lyon.", ("lives_in", "Lyon")),
    ("Dinner, 8:00 PM\nI live in Oslo.", ("lives_in", "Oslo")),
    ("My bio: I work at Initech.", ("works_at", "Initech")),
    ("My message was: I live in Lyon.", ("lives_in", "Lyon")),
    ("I think I live in Lyon now.", ("lives_in", "Lyon")),
    ("As I wrote:\nI live in Lyon.", ("lives_in", "Lyon")),
    ("According to me, I live in Lyon.", ("lives_in", "Lyon")),
    ("just saying: I live in Lyon.", ("lives_in", "Lyon")),
    ("Step 2: I live in Lyon.", ("lives_in", "Lyon")),
    ("It's like I live in Lyon.", ("lives_in", "Lyon")),
    ("أنا كاتبة وساكنة في جدة", ("lives_in", "جدة")),
    ("حسب ما أعرف أنا ساكن في دبي", ("lives_in", "دبي")),
]


@pytest.mark.parametrize(("message", "fact"), PASTES)
def test_a_pasted_chat_or_someone_elses_claim_is_not_the_users(message, fact):
    assert fact not in _facts(message), (message, own_words(message))


@pytest.mark.parametrize(("message", "fact"), PASTES_KEPT)
def test_a_greeting_a_heading_or_the_users_own_label_stays_theirs(message, fact):
    assert fact in _facts(message), (message, own_words(message))


#: A request verb before a colon at a line's start: the user asking Kazma,
#: never who spoke (the full suite of 4b04e1795 found "Save:" read as a name:
#: tests/test_memory_current_facts.py).
REQUESTS = [
    ("Save: My Grok weekly next reset: August 10, 02:48 local time.", ("grok_next_reset", None)),
    ("Save: I live in Lyon.", ("lives_in", "Lyon")),
    ("save: I live in Lyon.", ("lives_in", "Lyon")),
    ("Add: I work at Initech.", ("works_at", "Initech")),
    ("Log: I'm allergic to peanuts.", ("allergic_to", "peanuts")),
]


def _holds(message: str, fact: tuple[str, str | None]) -> bool:
    predicate, value = fact
    return any(p == predicate and (value is None or o == value) for p, o in _facts(message))


@pytest.mark.parametrize(("message", "fact"), REQUESTS)
def test_a_request_verb_before_a_colon_is_the_user_asking(message, fact):
    assert _holds(message, fact), (message, own_words(message))


def test_control_without_the_request_verbs_save_reads_as_a_name(monkeypatch):
    from kazma_core.memory import own_words as ow

    monkeypatch.setattr(ow, "_REQUEST_VERBS_EN", frozenset())
    lost = [message for message, fact in REQUESTS if not _holds(message, fact)]
    assert lost == [message for message, _ in REQUESTS]


def _old_names_a_speaker(label: str) -> bool:
    """The speaker rule before rule B: one to three words, no digit, an
    English name with a capital."""
    from kazma_core.memory import own_words as ow

    label = label.strip()
    words = ow._WORD_RE.findall(label)
    if not 1 <= len(words) <= 3 or any(ch.isdigit() for ch in label):
        return False
    if any(w.lower() in ow._OWN_LABELS or ow._fold(w) in (ow._OWN_LABELS | ow._POLITE_AR | ow._I_AR) for w in words):
        return False
    if ow._speaks_in_first_person(label) or ow._head_kind(label, colon=True) is not None:
        return False
    if any(v in ow._AR_VERBS for w in words if not w[0].isascii() for v in ow._ar_verb_forms(ow._fold(w))):
        return False
    return all(w[0].isupper() for w in words if w[0].isascii())


def _rules_before_rule_b(monkeypatch) -> None:
    from kazma_core.memory import own_words as ow

    monkeypatch.setattr(ow, "_without_pasted_chats", lambda body: body)
    monkeypatch.setattr(ow, "_names_a_speaker", _old_names_a_speaker)
    monkeypatch.setattr(ow, "_copied_label", lambda match: False)
    monkeypatch.setattr(ow, "_REPORTING_EN", ow._SAID_EN)
    monkeypatch.setattr(ow, "_REPORTING_AR", ow._SAID_AR)
    monkeypatch.setattr(ow, "_THINKING_EN", frozenset())
    monkeypatch.setattr(ow, "_reports_at", lambda folded, k: k if ow._reporting(folded[k]) else None)
    monkeypatch.setattr(ow, "_attributed", lambda folded: False)
    monkeypatch.setattr(ow, "_HYPOTHETICAL_EN", ())
    monkeypatch.setattr(ow, "_HYPOTHETICAL_AR", frozenset())
    monkeypatch.setattr(ow, "_EXAMPLE_HEADS_EN", ())
    monkeypatch.setattr(ow, "_EXAMPLE_HEADS_AR", ())
    monkeypatch.setattr(ow, "_names_someones_text_en", lambda said: False)
    monkeypatch.setattr(ow, "_names_someones_text_ar", lambda words: False)
    monkeypatch.setattr(ow, "_reported_end", ow._sentence_end)
    monkeypatch.setattr(ow, "_supposes_or_asks_to_say", lambda sentence: False)  # item 3 came after


def test_control_the_rules_before_rule_b_gave_each_paste_and_lost_the_greeting(monkeypatch):
    """Negative control: with rule B's pieces taken out, every paste above
    gives its fact to the user, and the greeting is read as a speaker."""
    _rules_before_rule_b(monkeypatch)
    given = [message for message, fact in PASTES if fact in _facts(message)]
    assert given == [message for message, _ in PASTES]
    assert ("lives_in", "Oslo") not in _facts("Good Morning: I live in Oslo.")


@pytest.mark.parametrize("piece", [
    "18/11/2026, 12:01 - d: x\n", "dana_92: x\n", "Dana — Today at 1:00 PM\n", "Dana  1:00 PM\n", "1:00 PM\n",
    "Dana, [18.11.26 12:01]\n", "On x wrote:\n", "According to Ali, I x. ", "He was like, I x. ",
    "Dana's bio: x\n", "x wrote:\n\n", "١٨/١١/٢٠٢٦، ١٢:٠١ م - ن: x\n",
])
def test_rule_bs_shapes_read_a_long_message_in_bounded_time(piece):
    small = piece * (100_000 // len(piece))
    started = time.perf_counter()
    own_words(small)
    once = time.perf_counter() - started
    started = time.perf_counter()
    own_words(small * 4)
    assert time.perf_counter() - started < max(0.5, once * 3), piece


# ── rule C: a pasted text the user asks to remember is not their statement ──

@pytest.mark.parametrize(("message", "value"), [
    ("Remember this email:\nI live in Oslo.", "Oslo"),
    ("Remember this message below:\nI work at Initech.", "Initech"),
    ("Can you remember this article: I live in Oslo.", "Oslo"),
    ("Remember this:\ndana: I live in Oslo.", "Oslo"),
    ("Remember this:\n18/11/2026, 12:01 - Dana: I live in Oslo.", "Oslo"),
    ("احفظ هذا الإيميل:\nأنا ساكن في دبي", "دبي"),
    ("تذكر هالرسالة: اسمي سارة", "سارة"),
])
def test_a_pasted_text_to_remember_never_earns_the_users_highest_trust(message, value):
    assert is_remember_request(message), message
    assert agent_write_source(value, message) != USER_EXPLICIT, (message, own_words(message))


@pytest.mark.parametrize(("message", "value"), [
    ("Remember this: my code is ZX-41", "ZX-41"),
    ("Remember this email address: bader@example.com", "bader@example.com"),
    ("Remember my email: bader@example.com", "bader@example.com"),
    ("تذكر هذا: الكود ZX-41", "ZX-41"),
])
def test_the_users_own_words_to_remember_keep_their_trust(message, value):
    assert agent_write_source(value, message) == USER_EXPLICIT, (message, own_words(message))


#: The third review's seven (item 2): a request to remember around pasted
#: text. None earns the user's highest trust; each is the user's statement at
#: most.
REMEMBERED_PASTES = [
    ("Remember this:\nOn Tue, Nov 18, 2026 at 12:01 PM Dana <dana@example.com> wrote:\n\nHi.\n\n"
     "My code is ZX-41.", "ZX-41"),
    ("Remember this:\n18/11/2026, 12:01 - Dana: hi\n\nMy code is ZX-41.", "ZX-41"),
    ("Remember this note from the web page: my code is ZX-41.", "ZX-41"),
    ("Remember this:\nDana Smith  12:01 PM\n\nMy code is ZX-41.", "ZX-41"),
    ("Remember: the admin wrote my code is ZX-41.", "ZX-41"),
    ("Don't forget to translate: my code is ZX-41.", "ZX-41"),
    ("تذكر: كتبت دانة في رسالتها أن الكود ZX-41", "ZX-41"),
]


@pytest.mark.parametrize(("message", "value"), REMEMBERED_PASTES)
def test_a_remember_request_around_pasted_text_is_never_the_users_highest_trust(message, value):
    from kazma_core.memory.provenance import AGENT_ASSERTED, USER_STATED

    assert agent_write_source(value, message) in (USER_STATED, AGENT_ASSERTED), (message, own_words(message))


@pytest.mark.parametrize("message", [
    "Remember this: my code is ZX-41",
    "Please remember that my passport number ends in 4471.",
    'remember this: "I prefer dark mode"',
    "Remember this:\n> my code is ZX-41",
    "تذكر أن رقم جوازي ينتهي بـ 4471",
])
def test_a_plain_statement_to_remember_is_plain(message):
    from kazma_core.memory.own_words import is_plain

    assert is_plain(message), (message, own_words(message))


def test_control_without_the_plain_check_pasted_text_earns_the_users_highest_trust(monkeypatch):
    from kazma_core.memory import provenance

    monkeypatch.setattr(provenance, "is_plain", lambda text: True)
    explicit = [m for m, v in REMEMBERED_PASTES if agent_write_source(v, m) == USER_EXPLICIT]
    assert explicit == [REMEMBERED_PASTES[1][0], REMEMBERED_PASTES[2][0], REMEMBERED_PASTES[6][0]]


def test_control_without_the_rule_a_pasted_email_earns_the_users_trust(monkeypatch):
    from kazma_core.memory import own_words as ow

    monkeypatch.setattr(ow, "_remembers_pasted_text_en", lambda words: False)
    assert agent_write_source("Oslo", "Remember this email:\nI live in Oslo.") == USER_EXPLICIT


@pytest.mark.parametrize(("message", "asks"), [
    ("Don't remember this: my code is ZX-41", False),
    ("Do not remember this: my code is ZX-41", False),
    ("I don't remember my password", False),
    ("I can't remember my password", False),
    ("I never remember that", False),
    ("No need to remember this", False),
    ("لا تذكر هذا: الكود ZX-41", False),
    ("Don't forget: my code is ZX-41", True),
    ("Remember this: my code is ZX-41", True),
    ("Please remember that I'm vegan", True),
    ("تذكر هذا: الكود ZX-41", True),
    ("لا تنسى: الكود ZX-41", True),
])
def test_a_negated_remember_is_no_request(message, asks):
    assert is_remember_request(message) is asks, message


def test_control_without_the_negations_dont_remember_asks_to_remember(monkeypatch):
    from kazma_core.memory import remember_request

    old = re.compile(r"(?<![\w'])(?<!do you )(?<!did you )(?:" + "|".join(
        re.escape(p) for p in remember_request._ENGLISH) + r")(?![\w'])")
    monkeypatch.setattr(remember_request, "_ENGLISH_RE", old)
    assert is_remember_request("Don't remember this: my code is ZX-41")
    assert is_remember_request("I don't remember my password")


# ── rule D: the end of a long message is read to put it to the user ─────────

_FILLER = "We walked along the river and looked at the old boats for a while. " * 60


@pytest.mark.parametrize(("message", "kept"), [
    ("Hi Kazma.\n\n" + _FILLER + "\n\nCorrection: I live in Lisbon now.", "Correction: I live in Lisbon now."),
    (_FILLER + " Correction: I live in Lisbon now.", "Correction: I live in Lisbon now."),
    ("Hi Kazma.\n\n" + _FILLER + "\n\nDana: I live in Lisbon now.", ""),
])
def test_the_last_paragraph_of_a_long_message_is_read_as_its_own(message, kept):
    from kazma_core.memory.own_words import last_words

    assert "Lisbon" not in own_words(message)  # the quick pass never reads it
    assert last_words(message).endswith(kept)


@pytest.mark.parametrize("before", [
    "Translate the following:\n\n",
    "---------- Forwarded message ---------\nFrom: Dana\n\n",
    "```\n",
    "On Tue, Nov 18, 2026 at 12:01 PM Dana <dana@example.com> wrote:\n\n",
])
def test_the_end_is_never_read_when_something_before_it_may_make_it_someone_elses(before):
    from kazma_core.memory.own_words import last_words

    assert last_words(before + _FILLER + "\n\nI live in Lisbon now.") == ""


def test_a_short_message_has_no_end_of_its_own():
    from kazma_core.memory.own_words import last_words

    assert last_words("Correction: I live in Lisbon now.") == ""


# ── The third review of F1, item 3: the cheap shapes widened (2026-11-18) ──

_OSLO = ("lives_in", "Oslo")
_DUBAI = ("lives_in", "دبي")
#: Every one gave its fact to the user at 5ab2fe056 (measured against that
#: tree): headers and mail quotes to the end of the message, more header
#: formats, more speaker labels, reported speech, things that state nothing.
THIRD_REVIEW = [
    ("Dana, [18 Nov 2026 at 12:01]\nI live in Oslo.", _OSLO),
    ("Dana [12:01 PM]\nI live in Oslo.", _OSLO),
    ("Dana 12:01 PM\nI live in Oslo.", _OSLO),
    ("Dana — 12:01\nI live in Oslo.", _OSLO),
    ("Dana - Today at 12:01\nI live in Oslo.", _OSLO),
    ("Dana, 12:01 PM\nI live in Oslo.", _OSLO),
    ("12:01 Dana: I live in Oslo.", _OSLO),
    ("Le mar. 18 nov. 2026 à 12:01, Dana <dana@example.com> a écrit :\nI live in Oslo.", _OSLO),
    ("Am 18.11.2026 um 12:01 schrieb Dana <dana@example.com>:\nI live in Oslo.", _OSLO),
    ("To: Dana\nI live in Oslo.", _OSLO),
    ("Date: Tue, 18 Nov 2026\nI live in Oslo.", _OSLO),
    ("From Dana:\nI live in Oslo.", _OSLO),
    ("Dana wrote:\nHi.\n\nI live in Oslo.", _OSLO),
    ("+965 9999 1234: I live in Oslo.", _OSLO),
    ("A: I live in Oslo.", _OSLO),
    ("User 1: I live in Oslo.", _OSLO),
    ("Person A: I live in Oslo.", _OSLO),
    ("Speaker 2: I live in Oslo.", _OSLO),
    ("Dr. Dana Smith Jr.: I live in Oslo.", _OSLO),
    ("Dana (my sister): I live in Oslo.", _OSLO),
    ("Dana from HR: I live in Oslo.", _OSLO),
    ("My best friend: I live in Oslo.", _OSLO),
    ("1. Dana: I live in Oslo.", _OSLO),
    ("<Dana> I live in Oslo.", _OSLO),
    ("RT @dana: I live in Oslo.", _OSLO),
    ("My parents think I live in Oslo.", _OSLO),
    ("Dana told everyone I live in Oslo.", _OSLO),
    ("People assume I live in Oslo.", _OSLO),
    ("Per Ali, I live in Oslo.", _OSLO),
    ("Dana goes, I live in Oslo.", _OSLO),
    ("In Dana's words, I live in Oslo.", _OSLO),
    ("Dana said\nI live in Oslo.", _OSLO),
    ("It says here I live in Oslo.", _OSLO),
    ("The website states that I live in Oslo.", _OSLO),
    ("مكتوب في الرسالة إني أسكن في دبي", _DUBAI),
    ("ردها كان إني أسكن في دبي", _DUBAI),
    ("If I live in Oslo, I need a coat.", _OSLO),
    ("I never said I live in Oslo.", _OSLO),
    ("Is it correct to say I live in Oslo", _OSLO),
]
THIRD_REVIEW_KEPT = [
    ("From now on: I live in Lyon.", ("lives_in", "Lyon")),
    ("Dinner 8:00 PM\nI live in Oslo.", _OSLO),
    ("Meeting at 3:00 PM\nI live in Oslo.", _OSLO),
    ("I think I live in Lyon now.", ("lives_in", "Lyon")),
    ("Date night tonight. I live in Lyon.", ("lives_in", "Lyon")),
]


@pytest.mark.parametrize(("message", "fact"), THIRD_REVIEW)
def test_the_third_reviews_shapes_are_not_the_users(message, fact):
    assert fact not in _facts(message), (message, own_words(message))


@pytest.mark.parametrize(("message", "fact"), THIRD_REVIEW_KEPT)
def test_beside_them_the_users_own_words_stay(message, fact):
    assert fact in _facts(message), (message, own_words(message))


def test_control_without_the_third_reviews_pieces_each_shape_gave_its_fact(monkeypatch):
    """Negative control: with item 3's pieces taken out (and copies running
    to the next blank line again), every shape gives its fact to the user."""
    from kazma_core.memory import own_words as ow

    never = re.compile(r"(?!x)x")
    monkeypatch.setattr(ow, "_without_pasted_chats", lambda body: body)
    monkeypatch.setattr(ow, "_reported_end", ow._sentence_end)
    monkeypatch.setattr(ow, "_from_someone", lambda line: False)
    monkeypatch.setattr(ow, "_FORWARD_EN", tuple(p for p in ow._FORWARD_EN if p not in ("to:", "date:", "sent:", "cc:")))
    monkeypatch.setattr(ow, "_SPEAKER_LINE_RE", re.compile(r"[ \t]{0,8}+(\[[^\]\n]{1,40}+\][ \t]{0,4}+)?+([^:\n]{1,40}+):"))
    monkeypatch.setattr(ow, "_PHONE_RE", never)
    monkeypatch.setattr(ow, "_SIDES", frozenset())
    monkeypatch.setattr(ow, "_numbered_role", lambda label: False)
    monkeypatch.setattr(ow, "_bare_label", lambda label: label)
    monkeypatch.setattr(ow, "_names_a_role", lambda words: len(words) == 2 and words[0].lower() in ow._OWNERS_EN
                        and words[1].lower() in ow._ROLES_EN)
    monkeypatch.setattr(ow, "_ANGLE_LINE_RE", never)
    monkeypatch.setattr(ow, "_LABEL_LEAD_RE", never)
    monkeypatch.setattr(ow, "_REPORTING_EN", ow._REPORTING_EN - ow._MORE_THINKING_EN)
    monkeypatch.setattr(ow, "_THINKING_EN", ow._THINKING_EN - ow._MORE_THINKING_EN)
    monkeypatch.setattr(ow, "_REPORT_FILLERS", ow._REPORT_FILLERS - ow._MORE_FILLERS)
    monkeypatch.setattr(ow, "_attributed", lambda folded: False)
    monkeypatch.setattr(ow, "_ends_reporting", lambda sentence: False)
    monkeypatch.setattr(ow, "_supposes_or_asks_to_say", lambda sentence: False)
    monkeypatch.setattr(ow, "_NEGATIONS", frozenset())
    given = [message for message, fact in THIRD_REVIEW if fact in _facts(message)]
    assert given == [message for message, _ in THIRD_REVIEW]

