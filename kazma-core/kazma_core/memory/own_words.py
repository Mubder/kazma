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
    # Double quotes and guillemets may run over a line break, never over a
    # blank line ("She wrote "My name is Dana.\nI live in Oslo."").
    r"(?<!\d)\"(?:[^\"\n]|\n(?!\n)){1,400}+\""
    r"|“(?:[^”\n]|\n(?!\n)){1,400}+”"
    r"|«(?:[^»\n]|\n(?!\n)){1,400}+»"
    # A single quote before a digit is an apostrophe ("the '90s").
    r"|(?<!\w)'(?!\d)(?:[^'\n]|'(?=\w)){1,400}+'(?!\w)"
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
#: Never "bio": a bio Kazma is asked to write is the user's, and the facts
#: given for it are theirs ("Write a bio: I work at Initech").
_WRITTEN_THINGS_EN = frozenset({"email", "e-mail", "mail", "message", "letter", "post", "tweet", "reply",
                                "story", "essay", "article", "caption", "note", "text", "paragraph",
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
#: Someone else's words after a colon: "she said:", "my boss wrote:", "her
#: bio reads:".
_REPORTING_EN = frozenset({"said", "says", "wrote", "writes", "told", "tells", "asked", "asks",
                           "replied", "replies", "texted", "messaged", "posted", "tweeted", "reads",
                           "added", "adds", "answered", "explained"})
#: A head this long may still name who spoke ("My sister who lives far away
#: told me yesterday on the phone:"); the colon is what makes it a head.
_REPORTED_HEAD_WORDS = 14
#: A text named by where it came from: "Message from Dana:".
_MESSAGE_THINGS_EN = frozenset({"message", "email", "e-mail", "mail", "text", "note", "letter", "comment",
                                "review", "post", "tweet", "reply", "dm", "sms", "voicemail"})
#: A question or request about a text the user pasted: "Tell me if this is
#: spam:", "Is this sentence correct:", "What does this mean:". The head
#: starts with one of these and names the text (``_TEXT_OBJECTS_EN``).
_ASKS_ABOUT_EN = frozenset({"tell", "is", "are", "does", "do", "what", "check", "can", "could", "would",
                            "how", "which", "why", "explain", "see"})
#: A text the user hands over: "Here is the article:", "Below is the email:".
_HANDS_OVER_EN = ("here is", "here's", "heres", "here are", "below is", "below are", "this is the",
                  "the following", "following is", "attached is")
#: Where a question starts: a sentence asking something states nothing of
#: the user ("How do I say my name is Pierre in French?").
_QUESTION_WORDS_EN = frozenset({"how", "what", "why", "which", "who", "whom", "whose", "where", "when",
                                "should", "shall", "can", "could", "would", "will", "do", "does", "did",
                                "is", "are", "am", "was", "were", "have", "has"})
_QUESTION_WORDS_AR = frozenset({"هل", "كيف", "وش", "شنو", "شو", "ايش", "ليش", "متى", "وين", "شلون", "كم",
                                "مين", "ماذا", "لماذا", "اين"})

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
#: Never "كتبت": it is "I wrote" and "she wrote" alike, and what follows
#: "كتبت:" is a written text either way, never a statement of self.
_I_AR = frozenset({"انا", "احنا", "نحن", "قلت", "قلنا"})
#: Arabic heads that hand over or ask about a text ("هذا المقال:", "وش
#: معنى هذا:", "هل هذي الجملة صحيحة:").
_HANDS_OVER_AR = frozenset({"هذا", "هذي", "هذه", "هاذا", "هاذي", "التالي"})
_ASKS_ABOUT_AR = frozenset({"هل", "وش", "شنو", "شو", "ايش", "شوف", "شيك", "تاكد", "اشرح", "معنى"})
#: "قلت لي" / "قلتلي": "you told me" (the "قلت" of "I said" takes no "لي").
_YOU_TOLD_ME_AR = frozenset({"قلتلي", "كتبتلي"})
_REPORTING_AR = frozenset({"قال", "قالت", "قالوا", "قالو", "يقول", "تقول", "يقولون", "كتب", "كتبت",
                           "كتبو", "كتبوا", "يكتب", "تكتب", "رد", "ردت", "سال", "سالت"})
#: Every Arabic verb these rules know: none of them names who spoke a line.
_AR_VERBS = _TEXT_VERBS_AR | _EDIT_VERBS_AR | _WRITE_VERBS_AR | _REPORTING_AR | {"تذكر", "احفظ", "سجل"}

#: An aside that ends the text an instruction works on, at the start of a
#: line or a sentence after it: "Translate: thanks.\nBy the way, my name is
#: Sam." Compared lower-cased (English) or folded (Arabic).
_ASIDES_EN = ("by the way", "btw", "fyi", "p.s", "ps:", "ps,", "on another note", "unrelated", "oh and",
              "oh, and", "anyway,")
_ASIDES_AR = ("بالمناسبه", "على فكره", "ع فكره", "على الهامش", "بعيدا عن هذا", "بعيد عن هذا")
#: Asides only at a line's start ("Can you review my text?\nAlso my name is
#: Sam."): inside a line, "Also, I have two cats" is still the text.
_LINE_ASIDES_EN = ("also ", "also,", "and also")
_LINE_ASIDES_AR = ("كمان ", "وكمان ", "ايضا", "وايضا")

#: A line of a pasted chat or transcript: who spoke, then a colon ("Dana:
#: My name is Dana", "[12:01, 18/11/2026] Dana: ...", "نورة: اسمي نورة").
#: The label is one to three words; a bracketed time may stand before it.
_SPEAKER_LINE_RE = re.compile(r"[ \t]{0,8}+(?:\[[^\]\n]{1,40}+\][ \t]{0,4}+)?+([^:\n]{1,40}+):")
#: Labels before a colon that are the user's own, or a form they fill in
#: about themself: never a speaker ("Correct: my name is Ahmad", "Name: Sam",
#: "News: I got the job"). Compared lower-cased (English) or folded (Arabic).
_OWN_LABELS = frozenset({
    "me", "i", "my", "myself", "mine", "note", "notes", "correct", "correction", "update", "updates", "edit",
    "remember", "reminder", "also", "ps", "p.s", "fyi", "btw", "answer", "todo", "important", "info",
    "context", "background", "actually", "yes", "no", "ok", "okay", "question", "q", "a", "re", "name",
    "age", "city", "job", "email", "phone", "address", "country", "location", "title", "role", "company",
    "status", "goal", "goals", "plan", "news", "fact", "facts", "tip", "summary", "today", "yesterday",
    "tomorrow", "now", "here", "result", "reason", "detail", "details", "about", "bio", "profile", "hey",
    "hi", "hello", "thanks", "anyway", "story", "home", "work", "hobby", "hobbies", "interests",
    "profession", "occupation", "employer", "birthday", "nationality", "language", "languages", "pronouns",
    "school", "university", "degree", "skills", "username", "website", "timezone", "warning", "heads",
    "انا", "اسمي", "ملاحظه", "تصحيح", "تحديث", "تذكير", "تذكر", "مهم", "للعلم", "سؤال", "جواب", "نعم", "لا",
    "الاسم", "العمر", "المدينه", "العمل", "الوظيفه", "الهاتف", "العنوان", "خبر", "اخبار", "معلومه",
    "معلومات", "فكره", "المهم", "سالفه", "قصه", "نبذه", "عني", "اليوم", "امس", "البارحه", "بكره", "الحين",
    "حاليا", "اخيرا", "بالمناسبه", "ترى", "يعني", "بس", "صراحه", "توضيح", "للتوضيح", "تنبيه", "مثال",
})
#: Lead-in words before a question's first word ("Hey Kazma, how do I ...").
_LEAD_INS_EN = frozenset({"hey", "hi", "hello", "ok", "okay", "so", "and", "but", "also", "kazma", "please",
                          "pls", "um", "well", "btw", "oh", "yo", "then"})
_LEAD_INS_AR = frozenset({"يا", "كازما", "هلا", "مرحبا", "طيب", "اوكي", "بس", "يعني"})
#: An address in a message: its ":" and "?" are no head's end.
_URL_RE = re.compile(r"https?://[^\s<>\"]{1,2000}+")
#: An instruction whose text is an address ("Summarize https://x.y and my
#: name is Sam"): the address is all it works on.
_URL_OBJECT_GAP = 40

#: A forwarded message's header, at a line's start: what follows is someone
#: else's. Compared lower-cased without leading dashes, ">" or spaces
#: (English) or folded (Arabic).
_FORWARD_EN = ("forwarded message", "begin forwarded message", "forwarded from ", "fwd:",
               "original message", "from:", "subject:")
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
    if any(head.startswith(idiom) for idiom in _NOT_INSTRUCTIONS_EN):
        return None  # "Please check my email:", "Write this down:": the user's own
    for verb in _EDIT_VERBS_EN:
        if head.startswith(verb + " ") and any(w in _TEXT_OBJECTS_EN for w in words[len(verb.split()):]):
            return _INSTRUCTION
    if any(head.startswith(verb + " ") for verb in _WRITE_VERBS_EN) and \
            any(w in _WRITTEN_THINGS_EN for w in words[1:]):
        return _INSTRUCTION
    if colon and any(w in _TEXT_WORK_EN for w in words):
        return _INSTRUCTION
    if not colon or not said:
        return None
    # "Here is the article:" -- a text the user hands over, never "Here is my
    # info:" (the user's own).
    if head.startswith(_HANDS_OVER_EN) and not _speaks_in_first_person(head):
        return _INSTRUCTION
    # "Tell me if this is spam:", "What does this mean:" -- a question about
    # the text that follows. Read after the courtesy words, so "Can you
    # remember this:" is never one.
    if words and words[0] in _ASKS_ABOUT_EN and any(w in _TEXT_OBJECTS_EN for w in words[1:]):
        return _INSTRUCTION
    # "Message from Dana:" -- a text named by who sent it.
    if said[0] in _MESSAGE_THINGS_EN and "from" in said[1:3]:
        return _REPORTED
    # "she said:", "my boss wrote:", "they told me:", "You said:" -- someone
    # else's words. A colon only (with none, "My sister said I should" is a
    # sentence ``_reported_cut`` reads), and never the user's own ("As I
    # said:").
    if (len(said) <= _REPORTED_HEAD_WORDS and any(w in _REPORTING_EN for w in said[1:])
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
    if not colon or len(words) > _REPORTED_HEAD_WORDS:
        return None
    # "هذا المقال:" -- a text handed over (never "هذا اسمي:"); "وش معنى هذا:",
    # "هل هذي الجملة صحيحة:" -- a question about the text that follows.
    if words[0] in _HANDS_OVER_AR and not _speaks_in_first_person(head):
        return _INSTRUCTION
    if words[0] in _ASKS_ABOUT_AR and any(w in _TEXT_OBJECTS_AR for w in words):
        return _INSTRUCTION
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


def _without_addresses(window: str) -> str:
    """*window* with each web address's characters blanked: an address's
    ":" and "?" end no head ("Summarize https://x.y?ref=1 and ...")."""
    return _URL_RE.sub(lambda m: "x" * len(m.group(0)), window)


def _is_aside(body: str, at: int) -> bool:
    text = body[at:at + 40]
    if _starts_with(text, _ASIDES_EN, _ASIDES_AR):
        return True
    return (at == 0 or body[at - 1] == "\n") and _starts_with(text, _LINE_ASIDES_EN, _LINE_ASIDES_AR)


def _cut(body: str) -> str:
    """*body* without the text its heads introduce: an instruction's text
    runs to the end of the message (or to an aside after it), or is the web
    address it names; someone else's words run to the end of their
    sentence."""
    kept: list[str] = []
    pos = 0
    starts = _head_starts(body)
    i = 0
    while i < len(starts):
        start = starts[i]
        i += 1
        if start < pos:
            continue  # inside text already dropped
        window = _without_addresses(body[start:start + _HEAD_MAX + 1])
        stops = [p for p in (window.find(":"), window.find("："), window.find("\n"), window.find("?"),
                             window.find("؟")) if p > 0]
        if not stops:
            if not _starts_with_text_verb(window):
                continue
            url = _URL_RE.search(body, start, start + _HEAD_MAX + 1)
            if url is not None and url.start() - start <= _URL_OBJECT_GAP:
                # "Summarize https://x.y and my name is Sam": the address is
                # the text; what follows it is the user's again.
                whole = _URL_RE.match(body, url.start())
                kept.append(body[pos:start])
                pos = whole.end() if whole is not None else url.end()
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
        if kind == _REPORTED:
            # Someone else's words, head and all ("My sister said: ..."), to
            # the end of their sentence; the rules after this one never read
            # the head again.
            kept.append(body[pos:start] + " ")
            pos = _sentence_end(body, end)
            continue
        # An instruction: its text runs to the end of the message, or to an
        # aside.
        kept.append(body[pos:end])
        while i < len(starts) and (starts[i] < end or not _is_aside(body, starts[i])):
            i += 1
        if i == len(starts):
            return "".join(kept)
        kept.append("\n")  # the aside is a sentence of its own
        pos = starts[i]
    kept.append(body[pos:])
    return "".join(kept)


#: Subjects that make a sentence the user's own report ("I told Ali I live in
#: Paris", "As I said, ..."), never someone else's.
_SUBJECT_I = frozenset({"i", "i'm", "im", "i've", "i'd", "i'll", "we", "we're", "we've", "انا", "احنا",
                        "نحن"})
#: Words between a reporting verb and what was reported ("told me I ...",
#: "said that I ...", "قالت لي أنا ...").
_REPORT_FILLERS = frozenset({"me", "us", "him", "her", "them", "that", "لي", "له", "لها", "ان"})
#: "إني", "إنني", "إننا": "that I", "that we" after a reporting verb.
_THAT_I_AR = frozenset({"اني", "انني", "اننا"})
#: The most of a message the scanners read. Scanning all of a long one cost
#: about six microseconds a line (12 s for 2 MiB, the review of F3), and a
#: long message is mostly a document, not the user speaking of themself.
_SCAN_MAX = 2000
#: Reporting verbs that may end a sentence after a comma ("My name is Dana,
#: she said."). Never a verb that is also a noun or an order ("رد": reply).
_TAIL_REPORTING = frozenset({"said", "says", "added", "adds", "wrote", "writes", "replied", "explained",
                             "answered", "told", "asked", "insisted", "قال", "قالت", "قالوا", "قالو", "يقول",
                             "تقول", "يقولون", "كتبت", "اضاف", "اضافت", "ردت"})
#: Words that start a clause of the user's own after a comma, never someone
#: named as the speaker ("..., which my boss says is great").
_TAIL_OPENERS = frozenset({"as", "like", "which", "who", "that", "and", "but", "so", "because", "since",
                           "when", "while", "if", "although", "though", "كما", "مثل", "زي", "اللي", "الذي",
                           "التي", "لان", "لكن", "و"})


def _bounded(body: str) -> str:
    """The first ``_SCAN_MAX`` characters of *body*, ended at its last
    paragraph break (a blank line) before them, so no quote, sentence or
    line is read cut in half; nothing, when the first ``_SCAN_MAX``
    characters hold no paragraph break (a document). Never its end: a head,
    a fence or a forward line in the part not read would decide what the end
    is, and the end would read as the user's. The turn keeps the whole
    message and the deep pass reads it."""
    if len(body) <= _SCAN_MAX:
        return body
    end = body.rfind("\n\n", 0, _SCAN_MAX)
    return body[:end] if end > 0 else ""


def _names_a_speaker(label: str) -> bool:
    """Whether *label*, before a colon at a line's start, names who spoke a
    pasted line ("Dana", "Ali Hassan", "نورة"): one to three words, no
    digit, not the user, not one of the user's own labels or a form field,
    not a head of its own (an instruction, someone reported). An English
    name is written with a capital."""
    label = label.strip()
    words = _WORD_RE.findall(label)
    if not 1 <= len(words) <= 3 or any(ch.isdigit() for ch in label):
        return False
    if any(w.lower() in _OWN_LABELS or _fold(w) in (_OWN_LABELS | _POLITE_AR | _I_AR) for w in words):
        return False  # "كما قلت:": the user's own report
    if _speaks_in_first_person(label) or _head_kind(label, colon=True) is not None:
        return False
    # Arabic has no capitals: a verb Kazma knows is no name ("صحح: اسمي أحمد"
    # is the user correcting themself).
    if any(v in _AR_VERBS for w in words if not w[0].isascii() for v in _ar_verb_forms(_fold(w))):
        return False
    # "Ali Hassan:" names someone; "Fun fact:" is a label.
    return all(w[0].isupper() for w in words if w[0].isascii())


def _without_speaker_lines(body: str) -> str:
    """*body* without the lines of a pasted chat or transcript: who spoke,
    then a colon, at a line's start ("Dana: My name is Dana", "[12:01,
    18/11/2026] Dana: ...", "نورة: اسمي نورة")."""
    out = []
    for line in body.split("\n"):
        match = _SPEAKER_LINE_RE.match(line)
        out.append("" if match and _names_a_speaker(match.group(1)) else line)
    return "\n".join(out)


def _without_quote_lines(body: str) -> str:
    """*body* without ``>`` quote lines, except those right under a request
    to remember ("Remember this:\n> my code is ZX-41"): the statement the
    user asks to be kept, read without its mark. Not under a request that
    reports someone ("Remember what my sister said:")."""
    from kazma_core.memory.remember_request import asks_to_remember

    out = []
    keeps = False
    for line in body.split("\n"):
        if _QUOTE_LINE_RE.match(line):
            out.append(line.lstrip(" \t")[1:] if keeps else " ")
            continue
        out.append(line)
        if line.strip():
            keeps = asks_to_remember(line) and not _reports(line)
    return "\n".join(out)


def _reports(text: str) -> bool:
    """Whether *text* names someone saying something ("Remember what my
    sister said:", "remember that Dana wrote")."""
    return any(w in _REPORTING_EN for w in _words(text)) or any(
        w in _REPORTING_AR for w in _WORD_RE.findall(_fold(text)))


def _sentences(body: str) -> list[str]:
    """*body* cut into its sentences and lines, which join back into it."""
    out: list[str] = []
    start = 0
    n = len(body)
    for i, ch in enumerate(body):
        if ch == "\n" or (ch in _SENTENCE_ENDS and (i + 1 == n or body[i + 1] in " \t\n")):
            out.append(body[start:i + 1])
            start = i + 1
    if start < n:
        out.append(body[start:])
    return out


def _reporting(word: str) -> bool:
    return word in _REPORTING_EN or word in _REPORTING_AR


def _named_after_comma(tokens: list[tuple[int, str]], folded: list[str], comma: int) -> bool:
    """Whether the words after *comma* only name who said the sentence
    ("..., she said.", "..., wrote Dana in her letter.", "..., قالت أختي"):
    a reporting verb among their first four, at most eight words, nobody of
    them the user, and not a clause of the user's own ("..., as my sister
    said", "..., which my boss says is great")."""
    tail = [w for (at, _), w in zip(tokens, folded, strict=True) if at > comma]
    if not 0 < len(tail) <= 8 or tail[0] in _TAIL_OPENERS:
        return False
    return any(w in _TAIL_REPORTING for w in tail[:4]) and not any(w in _SUBJECT_I for w in tail)


def _reports_on_the_user_at(folded: list[str], k: int) -> bool:
    """Whether the reporting verb at *k* is followed, past "me", "that" and
    the like, by a first-person word: someone's own words ("He said, I live
    in Lyon.", "أختي تقول أنا ساكنة") or their claim about the user ("Ali
    told me I work at Initech", "My friend says that I live in Lyon",
    "تقول إني ساكنة"). Neither is the user's own statement; the deep pass
    reads both."""
    nxt = k + 1
    while nxt < len(folded) and folded[nxt] in _REPORT_FILLERS and nxt < k + 4:
        nxt += 1
    if nxt >= len(folded):
        return False
    word = folded[nxt]
    return word in _FIRST_PERSON_EN or word in _FIRST_PERSON_AR or word in _THAT_I_AR


def _reported_cut(sentence: str) -> str:
    """*sentence* without what it reports someone else saying, with no colon
    to mark it: the words after a reporting verb that a first-person word
    follows ("He said, I live in Lyon.", "أختي تقول أنا ساكنة في دبي", "Ali
    told me I work at Initech") when the user is not its subject; or all of
    it, when it ends naming who said it ("My name is Dana, she said.", "I
    live in Oslo, wrote Dana in her letter."). Inside a request to remember
    too: "Remember that Ali says I work at Initech" asks to keep Ali's
    words."""
    end = "\n" if sentence.endswith("\n") else ""
    tokens = [(m.start(), m.group(0)) for m in _WORD_RE.finditer(sentence)]
    folded = [_fold(t).lower().replace("’", "'") for _, t in tokens]
    if not tokens:
        return sentence
    comma = max(sentence.rfind(","), sentence.rfind("،"))
    if comma > 0 and _named_after_comma(tokens, folded, comma):
        return " " + end
    for k, word in enumerate(folded):
        if not _reporting(word):
            continue
        if any(w in _SUBJECT_I for w in folded[:k]):
            return sentence  # the user's own report ("I told Ali I ...")
        if _reports_on_the_user_at(folded, k):
            return sentence[:tokens[k][0]] + end
    return sentence


def _asks(sentence: str) -> bool:
    """Whether *sentence* is a question ("How do I say my name is Pierre in
    French?", "كيف أقول اسمي سارة بالإنجليزي؟"): it ends in a question mark and
    starts, after a greeting, with a question word. A question states
    nothing of the user; a request to remember asked as one does."""
    from kazma_core.memory.remember_request import asks_to_remember

    if not sentence.strip().rstrip("\"'”’»)").endswith(("?", "؟")) or asks_to_remember(sentence):
        return False
    for word in _WORD_RE.findall(sentence):
        lowered, folded = word.lower().replace("’", "'"), _fold(word)
        if lowered in _LEAD_INS_EN or folded in _LEAD_INS_AR:
            continue
        return lowered in _QUESTION_WORDS_EN or folded in _QUESTION_WORDS_AR
    return False


def _without_reported_or_asked(body: str) -> str:
    out = []
    for sentence in _sentences(body):
        if _asks(sentence):
            out.append("\n" if sentence.endswith("\n") else " ")
        else:
            out.append(_reported_cut(sentence))
    return "".join(out)


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
    statement the user asks to be kept. Not when the request names someone
    saying it ("remember that Dana wrote "I live in Oslo"")."""
    from kazma_core.memory.remember_request import asks_to_remember

    def replace(match: re.Match[str]) -> str:
        span = match.group(0)
        if not _speaks_in_first_person(span):
            return span
        line_start = body.rfind("\n", max(0, match.start() - _HEAD_MAX), match.start()) + 1
        before = body[max(line_start, match.start() - _HEAD_MAX):match.start()]
        return span if asks_to_remember(before) and not _reports(before) else " "

    return _QUOTED_SPAN_RE.sub(replace, body)


def own_words(text: str | None) -> str:
    """*text* without what the user did not say about themself: data fences,
    attachment and forward lines, a forwarded message or pasted mail, the
    lines of a pasted chat, quote blocks, quoted first-person sentences,
    someone else's words after a colon or a reporting verb, the text an
    instruction works on, and questions. At most its first ``_SCAN_MAX``
    characters are read (``_bounded``)."""
    from kazma_core.safety.prompt_fence import outside_fences

    body = outside_fences(str(text or ""))
    stub = body.find(_WEB_FILE_STUB)
    if stub >= 0:
        body = body[:stub]
    body = _bounded(body)
    body = _strip_code_fences(body)
    body = _STUB_RE.sub(" ", body)
    body = _without_forwarded(body)
    body = _without_speaker_lines(body)
    body = _without_quote_lines(body)
    body = _without_quotes(body)
    return _without_reported_or_asked(_cut(body)).strip()


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
