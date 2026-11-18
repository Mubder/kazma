"""The user's own words in a message.

A chat message can hold text the user did not say about themself:

* an attached document: every surface puts its text into the user's message
  inside a data fence, under an "[Attached: ...]" line
  (``agent_handler/attachments.build_user_content``,
  ``agent/attached_text.py``);
* a forwarded message ("---------- Forwarded message ---------", "Forwarded
  from Dana", "رسالة محولة"; the chat apps fence one they are told is
  forwarded, under a "[Forwarded message ...]" line);
* a quote: a fenced code block, ``>`` lines, a quoted sentence in the first
  person ("she wrote "I live in Lyon"", in straight, curly, single or
  backtick quotes, or «»), and what someone else said after a colon ("my
  sister said: I moved to Rotterdam", "قالت: اسمي نورة"), to the end of that
  sentence ("Kazma said: your name is Ahmed. No, my name is Ahmad." keeps the
  correction);
* the text an instruction works on: "Translate to French: My name is
  Pierre", "Fix the grammar: I work at Initech", "Can you translate this into
  Spanish? ...", "سوي لي ترجمة: اسمي سارة", to the end of the message or to
  an aside that ends it ("By the way, ...", "بالمناسبة ...").

Inside a request to remember ("remember this: "I prefer dark mode"", "تذكر:
«أحب القهوة»") the quote is what the user asks to be kept: the user's own
statement.

Memory takes a fact at the user's own trust only from what is left. The
quick pass (``belief_extractor.extract_beliefs_heuristic``), the "remember
this" check (``remember_request.is_remember_request``) and the trust a
memory tool's write earns (``provenance.agent_write_source``) all read
through :func:`own_words`.

Nothing is lost by it: the turn keeps the whole message, and the model's
deep pass reads all of it, at the trust of an inference.

The rules lean one way: an instruction is recognised by its verb and, for
verbs that also mean something else ("fix", "correct", "صحح"), by naming the
text it works on ("fix the grammar:", "صحح هذا:"), so "Correct: my name is
Ahmad" stays the user's own correction. The decided cost of ending someone
else's words at their sentence: "my sister said: I'm a nurse. I live in
Rotterdam." files the second sentence under the user (a quoted span, "my
sister said: "I'm a nurse. I live in Rotterdam."", does not). Everything here
reads a bounded window per position, so a message is read in time linear in
its length.
"""

from __future__ import annotations

import re

__all__ = ["own_words"]

#: "[Attached: ...]", "[Full document at: ...]" and "[Forwarded message ...]"
#: lines: a file's name, or who forwarded something, is not the user's
#: statement. To the end of the line, at most 2,000 characters: a name holding
#: "]" ("report] I live in Lyon.pdf") ended the stub at its first bracket, and
#: the rest read as the user's words. Kazma writes each stub on a line of its
#: own, after the user's words.
_STUB_RE = re.compile(r"\[(?:Attached|Full document at|Forwarded message)\b[^\n]{0,2000}+")
#: The web chat's file stub (``agent/attached_text.py``). It comes last in a
#: message, and before 2026-11 the file's text followed it in a code block
#: that a line of three backticks inside the file ended early: what follows
#: it is never the user's.
_WEB_FILE_STUB = "[Attached file:"
#: A ``>`` quote line.
_QUOTE_LINE_RE = re.compile(r"^[ \t]{0,3}>[^\n]*+", re.MULTILINE)
#: A span in quotes, within one paragraph, at most 400 characters: straight
#: double quotes (never one right after a digit: 27" is an inch mark), curly
#: double quotes, guillemets, straight and curly single quotes (an
#: apostrophe inside a word is no quote: "I'm", "don't"), backticks.
_QUOTED_SPAN_RE = re.compile(
    r"(?<!\d)\"[^\"\n]{1,400}+\""
    r"|“[^”\n]{1,400}+”"
    r"|«[^»\n]{1,400}+»"
    r"|(?<!\w)'(?:[^'\n]|'(?=\w)){1,400}+'(?!\w)"
    r"|‘(?:[^’\n]|’(?=\w)){1,400}+’(?!\w)"
    r"|`[^`\n]{1,400}+`"
)
_WORD_RE = re.compile(r"[^\W\d_](?:[\w'’-]*+)")

_FIRST_PERSON_EN = frozenset({"i", "i'm", "im", "i've", "i'd", "i'll", "me", "my", "mine", "myself",
                              "we", "we're", "we've", "our", "ours", "us"})
#: Folded: a pronoun, "my name", "I have", and the quick pass's
#: first-person verbs and participles.
_FIRST_PERSON_AR = frozenset({"انا", "احنا", "نحن", "اسمي", "عندي", "لدي", "ساكن", "ساكنه", "اسكن",
                              "اعيش", "اشتغل", "اعمل", "احب", "انتقلت", "انتقلنا", "شغال", "موظف",
                              "موظفه"})

#: Words before an instruction's verb: courtesy, and calling Kazma by name.
_POLITE_EN = ("please", "pls", "plz", "kindly", "can you", "could you", "would you", "will you",
              "hey", "hi", "hello", "ok", "okay", "now", "also", "and", "then", "next", "kazma")
#: Verbs that only ever work on a text, and the labels of a text that is not
#: the user speaking: the head may stop at a colon.
_TEXT_VERBS_EN = ("translate", "proofread", "rephrase", "paraphrase", "summarise", "summarize",
                  "rewrite", "reword", "sum up", "reply to", "respond to", "draft a reply to",
                  "write a reply to", "answer this", "roleplay", "role-play", "role play",
                  "example sentence", "sample sentence", "example text", "sample text")
#: Writing a text for someone ("Write an email to my landlord saying:"): an
#: instruction when it names what is written, never "write this down".
_WRITE_VERBS_EN = ("write", "draft", "compose")
_WRITTEN_THINGS_EN = frozenset({"email", "e-mail", "mail", "message", "letter", "post", "tweet", "reply",
                                "bio", "story", "essay", "article", "caption", "note", "text", "paragraph",
                                "script", "speech"})
#: Verbs that also mean something else: an instruction only when it names
#: the text ("fix the grammar:", "correct this:"), never "Correct: ...".
_EDIT_VERBS_EN = ("fix", "correct", "edit", "polish", "improve", "shorten", "simplify", "reformat",
                  "format", "check", "review", "tidy up", "clean up")
_TEXT_OBJECTS_EN = frozenset({"this", "these", "that", "following", "below", "it", "text", "grammar",
                              "spelling", "punctuation", "wording", "tone", "email", "e-mail", "mail",
                              "message", "paragraph", "sentence", "letter", "note", "post", "tweet",
                              "draft", "reply", "bio", "story", "essay", "article", "caption", "cv",
                              "resume", "translation", "doc", "document"})
#: The work named as a thing, before a colon: "Give me a translation:".
_TEXT_WORK_EN = frozenset({"translation", "summary", "paraphrase", "rewording"})
#: Someone else's words after a colon: "she said:", "my boss wrote:".
_REPORTING_EN = frozenset({"said", "says", "wrote", "writes", "told", "tells", "asked", "asks",
                           "replied", "replies", "texted", "messaged", "posted", "tweeted"})

#: Arabic, folded: the verbs (with their "to me"/"it" forms), polite words
#: before them, and the text they name.
#: A verb here may also carry "ت" for "you" ("تترجم", "تصححلي").
_TEXT_VERBS_AR = frozenset({"ترجم", "ترجمي", "ترجملي", "ترجمه", "ترجمها", "ترجمو", "لخص", "لخصي",
                            "لخصلي", "لخصه", "لخصها", "دقق", "دققي"})
_EDIT_VERBS_AR = frozenset({"صحح", "صححي", "صححلي", "صححه", "صححها", "عدل", "عدلي", "عدله", "عدلها",
                            "حسن", "حسني", "حسنلي", "راجع", "راجعي", "اختصر", "اختصري", "اعد", "عيد",
                            "صيغ", "رد", "ردي", "صلح", "صلحي", "صلحلي", "صلحه", "صلحها"})
_TEXT_OBJECTS_AR = frozenset({"هذا", "هذي", "هذه", "هاذا", "التالي", "التاليه", "النص", "الرساله",
                              "الايميل", "الايميلات", "البريد", "الفقره", "الجمله", "الاملاء",
                              "القواعد", "الصياغه", "صياغه", "الكلام", "التغريده", "المنشور", "الرد",
                              "المقال", "الخطاب", "الرسالة", "النصوص"})
#: Writing a text for someone: "اكتب لي رسالة:", "سوي لي ايميل:".
_WRITE_VERBS_AR = frozenset({"اكتب", "اكتبي", "اكتبلي", "سوي", "سويلي", "سوو", "صيغلي"})
_WRITTEN_THINGS_AR = frozenset({"رساله", "الرساله", "ايميل", "الايميل", "تغريده", "التغريده", "منشور",
                                "المنشور", "خطاب", "الخطاب", "مقال", "المقال", "رد", "الرد", "نص", "فقره",
                                "قصه", "كلمه"})
#: The work named as a thing, before a colon: "سوي لي ترجمة:", "عطني تلخيص:".
#: Never "تصحيح": "تصحيح: أنا ساكن في دبي" is the user correcting themself.
_TEXT_WORK_AR = frozenset({"ترجمه", "تلخيص", "تدقيق", "صياغه"})
_POLITE_AR = frozenset({"ممكن", "لو", "سمحت", "رجاء", "رجاءا", "من", "فضلك", "يا", "ريت", "تكفى",
                        "تكفي", "ابيك", "ابغاك", "ودي", "هلا", "مرحبا", "طيب", "اوكي", "الحين",
                        "كازما"})
_I_AR = frozenset({"انا", "احنا", "نحن", "قلت", "قلنا", "كتبت"})
#: "قلت لي" / "قلتلي": "you told me" (the "قلت" of "I said" takes no "لي").
_YOU_TOLD_ME_AR = frozenset({"قلتلي", "كتبتلي"})
_REPORTING_AR = frozenset({"قال", "قالت", "قالوا", "قالو", "يقول", "تقول", "يقولون", "كتب", "كتبت",
                           "كتبو", "كتبوا", "يكتب", "تكتب", "رد", "ردت", "سال", "سالت"})

#: An aside that ends the text an instruction works on, at the start of a
#: line or a sentence after it: "Translate: thanks.\nBy the way, my name is
#: Sam." Compared lower-cased (English) or folded (Arabic).
_ASIDES_EN = ("by the way", "btw", "fyi", "p.s", "ps:", "ps,", "on another note", "unrelated", "oh and",
              "oh, and", "anyway,")
_ASIDES_AR = ("بالمناسبه", "على فكره", "ع فكره", "على الهامش", "بعيدا عن هذا", "بعيد عن هذا")

#: A forwarded message's header, at a line's start: what follows is someone
#: else's. Compared lower-cased without leading dashes, ">" or spaces
#: (English) or folded (Arabic).
_FORWARD_EN = ("forwarded message", "begin forwarded message", "forwarded from ", "fwd:",
               "original message")
_FORWARD_AR = ("رساله محوله", "رساله معاد توجيهها", "تمت اعاده توجيه", "محوله من", "رساله معاد ارسالها")

#: How far a head may run before its colon or line end.
_HEAD_MAX = 80
_SENTENCE_ENDS = ".!?؟"


def _fold(text: str) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(text)


def _words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD_RE.findall(text)]


def _speaks_in_first_person(span: str) -> bool:
    if any(w in _FIRST_PERSON_EN for w in _words(span)):
        return True
    # By word, never by space-split token: a quote mark stuck to a word
    # ("«اسمي") hid it.
    words = _WORD_RE.findall(_fold(span))
    for i, w in enumerate(words):
        if w in _FIRST_PERSON_AR or (len(w) > 3 and w[0] in "وف" and w[1:] in _FIRST_PERSON_AR):
            return True
        # "لوني المفضل", "أكلتي المفضلة": the speaker's own favourite.
        if len(w) > 2 and w.endswith("ي") and i + 1 < len(words) and words[i + 1] in ("المفضل", "المفضله"):
            return True
    return False


def _strip_polite_en(head: str) -> str:
    changed = True
    while changed:
        changed = False
        for word in _POLITE_EN:
            if head.startswith(word + " ") or head.startswith(word + ","):
                head = head[len(word):].lstrip(" ,!.")
                changed = True
    return head


#: Heads that look like an instruction and are not one.
_NOT_INSTRUCTIONS_EN = ("check this out", "check it out", "check out", "check my email", "check my mail",
                        "check my inbox", "check my messages", "write down", "write this down",
                        "write it down", "write that down", "note down")

#: What a head introduces: the text an instruction works on, or the words
#: someone else said.
_INSTRUCTION = "instruction"
_REPORTED = "reported"


def _starts_with_text_verb_en(head: str) -> bool:
    head = _strip_polite_en(head.lower().replace("’", "'").strip())
    return any(head == verb or head.startswith(verb + " ") for verb in _TEXT_VERBS_EN)


def _head_kind_en(head: str, *, colon: bool) -> str | None:
    said = _words(head)  # "Kazma said:": the name is the speaker here, not a courtesy
    head = _strip_polite_en(head.lower().replace("’", "'").strip())
    for verb in _TEXT_VERBS_EN:
        if head == verb or head.startswith(verb + " "):
            return _INSTRUCTION
    words = _words(head)
    if not any(head.startswith(idiom) for idiom in _NOT_INSTRUCTIONS_EN):
        for verb in _EDIT_VERBS_EN:
            if head.startswith(verb + " ") and any(w in _TEXT_OBJECTS_EN for w in words[len(verb.split()):]):
                return _INSTRUCTION
        if any(head.startswith(verb + " ") for verb in _WRITE_VERBS_EN) and \
                any(w in _WRITTEN_THINGS_EN for w in words[1:]):
            return _INSTRUCTION
    if colon and any(w in _TEXT_WORK_EN for w in words):
        return _INSTRUCTION
    # "she said:", "my boss wrote:", "they told me:", "You said:" -- someone
    # else's words. A colon only ("My sister said I should" is the user's
    # sentence), and never the user's own ("As I said:").
    if (colon and bool(said) and len(said) <= 6 and any(w in _REPORTING_EN for w in said[1:])
            and not any(w.split("'")[0] in {"i", "we"} for w in said)):
        return _REPORTED
    return None


def _ar_words(head: str) -> list[str]:
    """Folded words of *head* without the polite ones; "هال" ("this ...")
    read as the article: "هالجملة" is "الجملة"."""
    out = []
    for w in _WORD_RE.findall(_fold(head)):
        if w in _POLITE_AR:
            continue
        out.append("ال" + w[3:] if w.startswith("هال") and len(w) > 4 else w)
    return out


def _ar_verb_forms(word: str) -> tuple[str, ...]:
    first = word[1:] if len(word) > 3 and word[0] in "وف" else word
    return (first, first[1:]) if first.startswith("ت") and len(first) > 3 else (first,)


def _starts_with_text_verb_ar(head: str) -> bool:
    words = _ar_words(head)
    return bool(words) and any(v in _TEXT_VERBS_AR for v in _ar_verb_forms(words[0]))


def _head_kind_ar(head: str, *, colon: bool) -> str | None:
    words = _ar_words(head)
    if not words:
        return None
    for verb in _ar_verb_forms(words[0]):
        if verb in _TEXT_VERBS_AR:
            return _INSTRUCTION
        if verb in _EDIT_VERBS_AR and any(w in _TEXT_OBJECTS_AR for w in words[1:]):
            return _INSTRUCTION
        if verb in _WRITE_VERBS_AR and any(w in _WRITTEN_THINGS_AR for w in words[1:]):
            return _INSTRUCTION
    if colon and any(w in _TEXT_WORK_AR for w in words):
        return _INSTRUCTION
    if not colon or len(words) > 6:
        return None
    told_me = any(w in _YOU_TOLD_ME_AR or (w == "قلت" and nxt == "لي")
                  for w, nxt in zip(words, [*words[1:], ""]))
    if told_me or (any(w in _REPORTING_AR for w in words) and not any(w in _I_AR for w in words)):
        return _REPORTED
    return None


def _head_kind(head: str, *, colon: bool) -> str | None:
    """Either language's rules: a head may mix them ("translate للعربي:",
    "ترجم to English:")."""
    return _head_kind_en(head, colon=colon) or _head_kind_ar(head, colon=colon)


def _starts_with_text_verb(head: str) -> bool:
    return _starts_with_text_verb_en(head) or _starts_with_text_verb_ar(head)


def _starts_with(text: str, english: tuple[str, ...], arabic: tuple[str, ...]) -> bool:
    lowered = " ".join(text.lower().replace("’", "'").split())
    if any(lowered.startswith(p) for p in english):
        return True
    folded = " ".join(_fold(text).split())
    return any(folded.startswith(p) for p in arabic)


def _head_starts(body: str) -> list[int]:
    """Where a head may start: the message's start, a line's start, and after
    the end of a sentence."""
    starts = [0]
    for i, ch in enumerate(body):
        if ch == "\n":
            starts.append(i + 1)
        elif ch in _SENTENCE_ENDS and i + 1 < len(body) and body[i + 1] in " \t":
            starts.append(i + 2)
    return starts


def _sentence_end(body: str, at: int) -> int:
    """Where the sentence holding *at* ends: after its closing mark, or at its
    line's end."""
    n = len(body)
    i = at
    while i < n:
        ch = body[i]
        if ch == "\n":
            return i
        if ch in _SENTENCE_ENDS and (i + 1 == n or body[i + 1] in " \t\n"):
            return i + 1
        i += 1
    return n


def _cut(body: str) -> str:
    """*body* without the text its heads introduce: an instruction's text
    runs to the end of the message (or to an aside after it), someone else's
    words to the end of their sentence."""
    kept: list[str] = []
    pos = 0
    starts = _head_starts(body)
    i = 0
    while i < len(starts):
        start = starts[i]
        i += 1
        if start < pos:
            continue  # inside text already dropped
        window = body[start:start + _HEAD_MAX + 1]
        stops = [p for p in (window.find(":"), window.find("："), window.find("\n"), window.find("?"),
                             window.find("؟")) if p > 0]
        if not stops:
            if not _starts_with_text_verb(window):
                continue
            # "Translate to French My name is Pierre": no colon, the rest of
            # the message is the text.
            end, kind = start, _INSTRUCTION
        else:
            stop = min(stops)
            kind = _head_kind(window[:stop], colon=window[stop] != "\n")
            if kind is None:
                continue
            end = start + stop + 1
        kept.append(body[pos:end])
        if kind == _INSTRUCTION:
            # Its text runs to the end of the message, or to an aside.
            while i < len(starts) and (starts[i] < end
                                       or not _starts_with(body[starts[i]:starts[i] + 40], _ASIDES_EN, _ASIDES_AR)):
                i += 1
            if i == len(starts):
                return "".join(kept)
            pos = starts[i]
            continue
        pos = _sentence_end(body, end)
    kept.append(body[pos:])
    return "".join(kept)


def _without_forwarded(body: str) -> str:
    """*body* up to a forwarded message's header line."""
    start = 0
    while start <= len(body):
        end = body.find("\n", start)
        line = body[start:end if end >= 0 else len(body)][:120].lstrip(" \t>-*_")
        if line and _starts_with(line, _FORWARD_EN, _FORWARD_AR):
            return body[:start]
        if end < 0:
            return body
        start = end + 1
    return body


def _without_quotes(body: str) -> str:
    """*body* without quoted first-person spans, except the one a request to
    remember names ("remember this: "my code is ZX-41""): that is the
    statement the user asks to be kept."""
    from kazma_core.memory.remember_request import asks_to_remember

    def replace(match: re.Match[str]) -> str:
        span = match.group(0)
        if not _speaks_in_first_person(span):
            return span
        line_start = body.rfind("\n", max(0, match.start() - _HEAD_MAX), match.start()) + 1
        before = body[max(line_start, match.start() - _HEAD_MAX):match.start()]
        return span if asks_to_remember(before) else " "

    return _QUOTED_SPAN_RE.sub(replace, body)


def own_words(text: str | None) -> str:
    """*text* without what the user did not say about themself: data fences,
    attachment and forward lines, a forwarded message, quote blocks, quoted
    first-person sentences, someone else's words after a colon, and the text
    an instruction works on."""
    from kazma_core.safety.prompt_fence import outside_fences

    body = outside_fences(str(text or ""))
    stub = body.find(_WEB_FILE_STUB)
    if stub >= 0:
        body = body[:stub]
    body = _strip_code_fences(body)
    body = _STUB_RE.sub(" ", body)
    body = _without_forwarded(body)
    body = _QUOTE_LINE_RE.sub(" ", body)
    body = _without_quotes(body)
    return _cut(body).strip()


def _strip_code_fences(body: str) -> str:
    """Lines between a ``` (or ~~~) fence and its closing fence, or the end."""
    out: list[str] = []
    fence = ""
    for line in body.split("\n"):
        mark = line.lstrip(" \t")[:3]
        if fence:
            if line.strip().startswith(fence) and set(line.strip()) <= {fence[0]}:
                fence = ""
            continue
        if mark in ("```", "~~~"):
            fence = mark
            continue
        out.append(line)
    return "\n".join(out)
