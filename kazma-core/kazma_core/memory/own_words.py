"""The user's own words in a message.

A chat message can hold text the user did not say about themself:

* an attached document: the chat apps and the web chat put its text into the
  user's message inside a data fence, under an "[Attached: ...]" line
  (``agent_handler/attachments.build_user_content``);
* a quote: a fenced code block, ``>`` lines, a quoted sentence in the first
  person ("she wrote "I live in Lyon""), and what someone else said after a
  colon ("my sister said: I moved to Rotterdam", "قالت: اسمي نورة");
* the text an instruction works on: "Translate to French: My name is
  Pierre", "Fix the grammar: I work at Initech", "Summarise this email: Hi,
  my name is Dana", "ترجم للإنجليزي: اسمي سارة".

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
Ahmad" stays the user's own correction. Everything here reads a bounded
window per position, so a message is read in time linear in its length.
"""

from __future__ import annotations

import re

__all__ = ["own_words"]

#: "[Attached: ...]" and "[Full document at: ...]" lines: a file's name is
#: not the user's statement. To the closing bracket, at most 2,000
#: characters, or the end of the line.
_STUB_RE = re.compile(r"\[(?:Attached|Full document at)\b[^\]\n]{0,2000}\]?")
#: A ``>`` quote line.
_QUOTE_LINE_RE = re.compile(r"^[ \t]{0,3}>[^\n]*+", re.MULTILINE)
#: A span in quotes, within one paragraph, at most 400 characters.
_QUOTED_SPAN_RE = re.compile(r"\"[^\"\n]{1,400}+\"|“[^”\n]{1,400}+”|«[^»\n]{1,400}+»")
_WORD_RE = re.compile(r"[^\W\d_](?:[\w'’-]*+)")

_FIRST_PERSON_EN = frozenset({"i", "i'm", "im", "i've", "i'd", "i'll", "me", "my", "mine", "myself",
                              "we", "we're", "we've", "our", "ours", "us"})
#: Folded: a pronoun, "my name", "I have", and the quick pass's
#: first-person verbs and participles.
_FIRST_PERSON_AR = frozenset({"انا", "احنا", "نحن", "اسمي", "عندي", "لدي", "ساكن", "ساكنه", "اسكن",
                              "اعيش", "اشتغل", "اعمل", "احب", "انتقلت", "انتقلنا", "شغال", "موظف",
                              "موظفه"})

_POLITE_EN = ("please", "pls", "plz", "kindly", "can you", "could you", "would you", "will you",
              "hey", "hi", "hello", "ok", "okay", "now", "also", "and", "then", "next")
#: Verbs that only ever work on a text: the head may stop at a colon.
_TEXT_VERBS_EN = ("translate", "proofread", "rephrase", "paraphrase", "summarise", "summarize",
                  "rewrite", "reword", "sum up", "reply to", "respond to", "draft a reply to",
                  "write a reply to", "answer this")
#: Verbs that also mean something else: an instruction only when it names
#: the text ("fix the grammar:", "correct this:"), never "Correct: ...".
_EDIT_VERBS_EN = ("fix", "correct", "edit", "polish", "improve", "shorten", "simplify", "reformat",
                  "format", "check", "review", "tidy up", "clean up")
_TEXT_OBJECTS_EN = frozenset({"this", "these", "that", "following", "below", "it", "text", "grammar",
                              "spelling", "punctuation", "wording", "tone", "email", "e-mail", "mail",
                              "message", "paragraph", "sentence", "letter", "note", "post", "tweet",
                              "draft", "reply", "bio", "story", "essay", "article", "caption", "cv",
                              "resume", "translation", "doc", "document"})
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
                            "صيغ", "رد", "ردي"})
_TEXT_OBJECTS_AR = frozenset({"هذا", "هذي", "هذه", "هاذا", "التالي", "التاليه", "النص", "الرساله",
                              "الايميل", "الايميلات", "البريد", "الفقره", "الجمله", "الاملاء",
                              "القواعد", "الصياغه", "صياغه", "الكلام", "التغريده", "المنشور", "الرد",
                              "المقال", "الخطاب", "الرسالة", "النصوص"})
_POLITE_AR = frozenset({"ممكن", "لو", "سمحت", "رجاء", "رجاءا", "من", "فضلك", "يا", "ريت", "تكفى",
                        "تكفي", "ابيك", "ابغاك", "ودي", "هلا", "مرحبا", "طيب", "اوكي", "الحين"})
_I_AR = frozenset({"انا", "احنا", "نحن", "قلت", "قلنا", "كتبت"})
_REPORTING_AR = frozenset({"قال", "قالت", "قالوا", "قالو", "يقول", "تقول", "يقولون", "كتب", "كتبت",
                           "كتبو", "كتبوا", "يكتب", "تكتب", "رد", "ردت", "سال", "سالت"})

#: How far a head may run before its colon or line end.
_HEAD_MAX = 80


def _fold(text: str) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(text)


def _words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD_RE.findall(text)]


def _speaks_in_first_person(span: str) -> bool:
    if any(w in _FIRST_PERSON_EN for w in _words(span)):
        return True
    for w in _fold(span).split():
        if w in _FIRST_PERSON_AR or (len(w) > 3 and w[0] in "وف" and w[1:] in _FIRST_PERSON_AR):
            return True
    return False


def _strip_polite_en(head: str) -> str:
    changed = True
    while changed:
        changed = False
        for word in _POLITE_EN:
            if head.startswith(word + " ") or head.startswith(word + ","):
                head = head[len(word):].lstrip(" ,")
                changed = True
    return head


def _is_head_en(head: str, *, colon: bool) -> bool:
    head = _strip_polite_en(head.lower().replace("’", "'").strip())
    for verb in _TEXT_VERBS_EN:
        if head == verb or head.startswith(verb + " "):
            return True
    words = _words(head)
    for verb in _EDIT_VERBS_EN:
        if head.startswith(verb + " ") and any(w in _TEXT_OBJECTS_EN for w in words[len(verb.split()):]):
            return True
    # "she said:", "my boss wrote:", "they told me:" -- someone else's words.
    # A colon only ("My sister said I should" is the user's sentence), and
    # never the user's own ("As I said:").
    return (colon and bool(words) and len(words) <= 6 and any(w in _REPORTING_EN for w in words[1:])
            and not any(w.split("'")[0] in {"i", "we"} for w in words))


def _is_head_ar(head: str, *, colon: bool) -> bool:
    words = [w for w in _fold(head).split() if w not in _POLITE_AR]
    if not words:
        return False
    first = words[0][1:] if len(words[0]) > 3 and words[0][0] in "وف" else words[0]
    for verb in (first, first[1:] if first.startswith("ت") and len(first) > 3 else ""):
        if verb in _TEXT_VERBS_AR:
            return True
        if verb in _EDIT_VERBS_AR and any(w in _TEXT_OBJECTS_AR for w in words[1:]):
            return True
    return (colon and len(words) <= 6 and any(w in _REPORTING_AR for w in words)
            and not any(w in _I_AR for w in words))


def _head_starts(body: str) -> list[int]:
    """Where a head may start: the message's start, a line's start, and after
    the end of a sentence."""
    starts = [0]
    for i, ch in enumerate(body):
        if ch == "\n":
            starts.append(i + 1)
        elif ch in ".!?؟" and i + 1 < len(body) and body[i + 1] in " \t":
            starts.append(i + 2)
    return starts


def _cut(body: str) -> str:
    """*body* without the text its heads introduce: an instruction's text
    runs to the end of the message, someone else's words to the end of their
    line."""
    kept: list[str] = []
    pos = 0
    for start in _head_starts(body):
        if start < pos:
            continue  # inside a line already dropped
        window = body[start:start + _HEAD_MAX + 1]
        stops = [p for p in (window.find(":"), window.find("："), window.find("\n")) if p > 0]
        if not stops:
            continue
        stop = min(stops)
        head = window[:stop]
        colon = window[stop] != "\n"
        arabic = any("؀" <= c <= "ۿ" for c in head)
        is_head = _is_head_ar if arabic else _is_head_en
        if not is_head(head, colon=colon):
            continue
        end = start + stop + 1
        kept.append(body[pos:end])
        if is_head(head, colon=False):
            return "".join(kept)  # an instruction: its text runs to the end
        line_end = body.find("\n", end)
        if line_end < 0:
            return "".join(kept)
        pos = line_end
    kept.append(body[pos:])
    return "".join(kept)


def own_words(text: str | None) -> str:
    """*text* without what the user did not say about themself: data fences,
    attachment lines, quote blocks, quoted first-person sentences, someone
    else's words after a colon, and the text an instruction works on."""
    from kazma_core.safety.prompt_fence import outside_fences

    body = outside_fences(str(text or ""))
    body = _strip_code_fences(body)
    body = _STUB_RE.sub(" ", body)
    body = _QUOTE_LINE_RE.sub(" ", body)
    body = _QUOTED_SPAN_RE.sub(lambda m: " " if _speaks_in_first_person(m.group(0)) else m.group(0), body)
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
