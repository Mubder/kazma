"""What in a message is clearly not the user telling: text they pasted, and
text another person wrote (the memory plan's W4, its third review,
2026-11-25).

``own_words`` answers another question. It decides TRUST -- may a fact be
filed as the user's own statement -- and takes out whatever it is unsure of: a
reported sentence, an "if", a message over 2,000 characters, a line behind any
label. An event is never filed as the user's statement, only as Kazma's
reading, and dropping one loses something the person said: of 45 ordinary
openings with three events each, ``own_words`` as the event gate dropped 12
English events of 135 and 60 Arabic ones ("لو سمحت، بعت سيارتي أمس": the "لو"
of "please" read as "if"; "القصة: ...": no name list tells an Arabic heading
from a person). So the event gate (``event_text``) asks this module instead:
only text whose SHAPE says it is not the user telling.

* a data fence, an attachment's line, a code fence, ``>`` lines;
* a forwarded message or a mail with its header, to its sign-off, to an aside
  ("By the way, ...", "بالمناسبة ..."), or to the end;
* a chat export's lines, a copied chat's header and the message under it, the
  lines of a transcript -- a line is a speaker's when the label is one no
  heading is (a time, a phone number, a role: "my sister:", "أختي:"), or the
  message shows a conversation (a label used twice, a "Me:" line, a line that
  answers another, the label named again). One labelled line alone ("The
  story: ...", "القصة: ...") says nothing;
* what another person is quoted as saying, after a colon ("My sister said:
  ...") or as a whole sentence in quotation marks, never what the user quotes
  themself saying ("I emailed my landlord: "I paid the rent"");
* the text an instruction works on ("Translate to French: ...", "صحح النص:
  ..."), to the blank line that ends it.

What is merely reported ("My manager told me I got the promotion"), supposed
("If I remember right, ..."), asked, or long is the user telling. A list of
shapes, read line by line; no model. At most ``_READ_MAX`` characters, so a
message costs its length once.
"""

from __future__ import annotations

import re
from functools import lru_cache

from kazma_core.memory import own_words as _own

__all__ = ["told_and_pasted"]

#: The most of a message that is read: four times what the deep pass shows a
#: model (``limits.DEEP_PASS_QUESTION``), where every event comes from.
_READ_MAX = 6000


# ── Words ────────────────────────────────────────────────────────────────

#: Verbs that only ever work on a text, whatever follows them.
_TRANSFORM_EN = ("translate", "proofread", "rephrase", "paraphrase", "reword", "rewrite")
_TRANSFORM_AR = frozenset({"ترجم", "ترجمي", "ترجملي", "ترجمه", "ترجمها", "ترجمو", "دقق", "دققي"})
#: A world to imagine is the user's way of telling ("Imagine! I sold my car",
#: "تخيل، بعت سيارتي"); a part to play is an instruction.
_SUPPOSING_EN = ("imagine", "suppose", "let's say", "lets say")
_SUPPOSING_AR = frozenset({"تخيل", "تخيلي", "افترض", "افترضي"})
_ROLES_PLAYED_EN = ("pretend", "act as", "act like", "play the role", "play as", "impersonate", "roleplay", "role-play",
                    "role play", "say as if", "speak as", "talk like")
_ROLES_PLAYED_AR = frozenset({"تقمص", "تقمصي"})
#: Nouns of a text: what is handed over ("Here is the email:") or asked about.
_TEXT_NOUNS_EN = (_own._TEXT_OBJECTS_EN | _own._PASTED_THINGS_EN | _own._SOURCE_NOUNS_EN | _own._TEXT_WORK_EN) - {
    "this", "these", "that", "it", "following", "below", "story", "note", "news", "fix", "google", "tone", "draft",
    "bio", "group", "book"}
_TEXT_NOUNS_AR = _own._TEXT_OBJECTS_AR - {"هذا", "هذي", "هذه", "هاذا", "التالي", "التاليه", "الكلام"}
#: What a question about a text asks of it ("What does this mean:").
_OF_A_TEXT = frozenset({"correct", "grammatical", "mean", "means", "spam", "scam", "phishing",
                        "معني", "معناه", "معناها", "صحيح", "صحيحه"})
#: Weak verbs of editing: "Check this: I sold my car!" asks nothing of a text.
_LOOKS_EN = ("check", "review")
#: What an instruction works on when the text is the user's own telling.
_OWN_TELLING_EN = frozenset({"story", "stories", "experience", "experiences", "journey", "life", "day", "week", "month",
                             "year", "trip", "biography", "achievements", "accomplishments", "diary", "journal"})
_OWN_TELLING_AR = frozenset({"قصتي", "قصتنا", "تجربتي", "تجاربي", "رحلتي", "حياتي", "يومي", "اسبوعي", "شهري", "سفرتي",
                             "انجازاتي", "يومياتي", "مذكراتي"})
#: "My CV", "my email": a text named, not shown ("Can you review my CV?").
_MY_TEXT_AR = frozenset({"رسالتي", "رسايلي", "ايميلي", "ايميلاتي", "سيرتي", "مقالي", "نصي", "تغريدتي", "منشوري"})
_REPLY_AR = frozenset({"رد", "ردي", "ارد", "ترد", "تردين", "جاوب", "جاوبي", "تجاوب", "اجاوب"})
_POINTS_AR = frozenset({"هذا", "هذي", "هذه", "هاذا", "هاذي", "التالي", "التاليه"})
#: Written alike for "I" and for "she": "كتبت لأخوي: بعت سيارتي".
_I_OR_SHE_AR = frozenset({"كتبت", "سالت"})
_TO_ME_AR = frozenset({"لي", "لنا", "الي", "علي"})
#: The user's own saying before a quotation ("I emailed my landlord this: "..."").
_I_SAID_EN = re.compile(
    r"\b(?:i|we)(?:'ve|'d|'ll| will| have| had| just| also| then| already| even)* "
    r"(?:say|tell|write|text|reply|send|said|told|wrote|emailed|e-mailed|texted|messaged|sent|replied|answered|"
    r"posted|tweeted|noted|mentioned|announced|shouted|typed|wrote down|put|captioned)\b")
_I_SAID_AR = frozenset({"قلت", "قلنا", "كتبت", "كتبنا", "ارسلت", "رسلت", "طرشت", "دزيت", "رديت", "نشرت", "غردت",
                        "سجلت", "ذكرت", "خبرت", "بلغت", "اخبرت"})
#: Headings an Arabic line starts with: with or without the article, "ب" or a
#: pronoun ("القصة", "بصراحة", "وضعي"). And days, in both languages, which
#: head a diary's lines.
_HEADINGS_AR_RAW = (
    "قصة", "سالفة", "خلفية", "سياق", "مشكلة", "حل", "صراحة", "اختصار", "خلاصة", "أول", "أولا", "ثاني", "ثانيا",
    "ثالثا", "أخيرا", "أخبار", "خبر", "تحديث", "ملاحظة", "وضع", "حالة", "حقيقة", "يوم", "يومية", "صبح", "ظهر", "عصر",
    "مغرب", "عشاء", "غداء", "غدا", "فطور", "ليل", "ليلة", "مساء", "صباح", "شغل", "دوام", "بيت", "أهل", "عائلة",
    "دراسة", "صحة", "رياضة", "سفر", "مشتريات", "موعد", "اجتماع", "حلو", "سيء", "زين", "شين", "مهم", "سؤال", "جواب",
    "نتيجة", "سبب", "هدف", "خطة", "فكرة", "تذكير", "معلومة", "فائدة", "ميزة", "عيب", "بداية", "نهاية", "مختصر",
    "مفيد", "تفاصيل", "موضوع", "طلب", "مني", "عني", "إضافة", "توضيح", "تنبيه", "خطوة", "مرحلة", "أسبوع", "شهر", "سنة",
    "أمس", "بكرة", "اليوم", "الحين", "قبل", "بعد", "الآن", "واقع", "مفاجأة", "تخيل", "بشارة", "نقطة", "ملخص",
    "نوع", "ماركة", "لون", "مقاس", "حجم", "تاريخ", "وقت", "سعر", "تكلفة", "مجموع", "كمية", "مكان", "محل", "فئة", "مبلغ",
    "مدة", "مسافة")
_HEADINGS_EN = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "morning", "afternoon", "evening",
    "night", "noon", "midnight", "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "truth", "situation", "latest", "confession", "gossip", "good",
    "bad", "best", "worst", "highlight", "highlights", "lowlight", "family", "health", "money", "travel", "fitness",
    "shopping", "bought", "sold", "visited", "met", "done", "before", "after", "then", "earlier", "later", "wins",
    "type", "brand", "color", "colour", "size", "date", "time", "item", "cost", "price", "total", "condition",
    "quantity", "qty", "model", "place", "store", "shop", "notes", "category", "amount", "duration", "distance",
    "win", "loss", "losses", "reality", "surprise", "announcement", "diary", "journal", "log", "everything"})
#: Who a line is said by, in Arabic, as ``own_words._ROLES_EN`` in English:
#: with the article or a pronoun ("أختي:", "الزبون:").
_ROLES_AR_RAW = (
    "أخت", "أخو", "أخ", "أم", "أمي", "أب", "أبو", "والد", "والدة", "زوج", "زوجة", "بنت", "ولد", "ابن", "عم", "خال",
    "عمة", "خالة", "جد", "جدة", "صديق", "صديقة", "صاحب", "صاحبة", "ربيع", "مدير", "مديرة", "زميل", "زميلة", "دكتور",
    "دكتورة", "عميل", "زبون", "موظف", "بائع", "مشتري", "مالك", "مستأجر", "سائق", "مدرس", "مدرسة", "جار", "جارة",
    "محامي", "ممرضة", "طالب")
_SUFFIXES_AR = ("ي", "ه", "ها", "هم", "نا", "ك", "تي", "ته", "تها", "تهم", "تنا", "تك")
_ME = frozenset({"me", "myself", "انا"})
#: A labelled line that answers another, or addresses someone: a conversation.
_ANSWERS = re.compile(
    r"\b(?:congrats|congratulations|thanks|thank you|thx|lol|haha|nice|wow|you|your|u|ok|okay|yes|no|hi|hello|hey)\b"
    r"|[?؟]|(?:^|\W)(?:مبروك|مبارك|شكرا|مشكور|مشكوره|تسلم|تسلمين|هلا|اهلا|انت|انتي|عليك|ههه\w*|تمام|اوكي|نعم|لا|"
    r"الله يبارك|ما شاء الله|ماشاء الله|يا)(?:\W|$)", re.IGNORECASE)

_SIGN_OFF = re.compile(
    r"[ \t]*(?:(?:best|kind|warm|many)(?: (?:regards|wishes|thanks))?|regards|cheers|sincerely|yours(?: \w+)?|"
    r"yours sincerely|yours truly|thanks(?: again)?|thank you|love|respectfully|"
    r"(?:مع |اطيب |خالص |مع خالص |مع اطيب |وتقبلوا )?(?:تحياتي|التحيات|التحيه|تحيه|تحياتنا)|شكرا(?: لك| لكم)?|"
    r"اخوك|اخوكم|المخلص|مع الشكر|ودمتم)(?:[ \t]\w+)?[ \t]*[,،.!]?[ \t]*")
_LETTER_OPENS = re.compile(
    r"[ \t]*(?:hi|hello|hey|dear|good (?:morning|afternoon|evening)|هلا|اهلا|مرحبا|عزيزي|عزيزتي|السلام عليكم|"
    r"حضره|الاخ|الاخت|الساده|سعاده)\b[^\n.!?؟]{0,60}[,،:!]?[ \t]*", re.IGNORECASE)
_MAIL_HEADER = re.compile(r"(?:from|subject|to|date|sent|cc|bcc|reply-to|من|الي|الموضوع|التاريخ|نسخه)[ \t]*:",
                          re.IGNORECASE)
#: Asides that end a pasted text, beside ``own_words``'s.
_MORE_ASIDES_EN = ("anyway", "anyways", "besides that", "separately")
_MORE_ASIDES_AR = ("المهم", "عالعموم", "على العموم", "علي العموم", "بعيدا عن")
_FORWARD_WORDS_MAX = 6


@lru_cache(maxsize=4)
def _folded_set(words: tuple[str, ...]) -> frozenset[str]:
    return frozenset(_own._fold(w) for w in words)


def _arabic_stems(word: str) -> set[str]:
    """*word* (folded) without "و", "ب", the article or a pronoun."""
    out = {word}
    for w in tuple(out):
        if len(w) > 3 and w[0] in "وبف":
            out.add(w[1:])
    for w in tuple(out):
        if w.startswith("ال") and len(w) > 3:
            out.add(w[2:])
    for w in tuple(out):
        for suffix in _SUFFIXES_AR:
            if w.endswith(suffix) and len(w) - len(suffix) >= 2:
                out.add(w[: -len(suffix)])
                if suffix.startswith("ت"):
                    out.add(w[: -len(suffix)] + "ه")  # "قصتي": "قصه"
    return out


def _is_heading(label: str) -> bool:
    for word in _own._WORD_RE.findall(_own._fold(label)):
        if word.lower() in _HEADINGS_EN:
            return True
        if not word.isascii() and (_arabic_stems(word) & _folded_set(_HEADINGS_AR_RAW) or _is_weekday_ar(word)):
            return True
    return False


def _is_weekday_ar(word: str) -> bool:
    from kazma_core.memory.time_words import is_weekday

    return is_weekday(word)


def _somebody(label: str) -> bool:
    """Whether *label* may name who spoke: ``own_words``'s names, never a
    heading of either language."""
    return not _is_heading(label) and _own._names_a_speaker(label)


def _certain_speaker(label: str) -> bool:
    """A label no heading looks like: a role ("my sister", "أختي"), a phone
    number, an ``@handle``, a numbered role."""
    label = label.strip()
    words = _own._WORD_RE.findall(_own._bare_label(label) or label)
    if label.startswith("@") and _own._handle(label):
        return True
    if _own._PHONE_RE.fullmatch(label) and sum(ch.isdigit() for ch in label) >= 7:
        return True
    if _own._numbered_role(label) or _own._names_a_role(words):
        return True
    folded = [_own._fold(w) for w in words]
    if not 1 <= len(folded) <= 2 or any(w.isascii() for w in folded) or not all(w.startswith("ال") for w in folded[1:]):
        return False  # "أختي:", "صديقي المقرب:"; never "صديقي يدري:"
    first = folded[0]
    # Said of someone: the article or a pronoun written onto it; "دكتور:" alone
    # may be a list's heading.
    bound = first.startswith("ال") or any(first.endswith(s) for s in _SUFFIXES_AR)
    return bound and bool(_arabic_stems(first) & _folded_set(_ROLES_AR_RAW))


# ── The parts ────────────────────────────────────────────────────────────


class _Message:
    """A message's body and which of its characters are pasted."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.pasted = bytearray(len(body))
        self.lines: list[tuple[int, int]] = []
        at = 0
        for line in body.split("\n"):
            self.lines.append((at, at + len(line)))
            at += len(line) + 1

    def mark(self, start: int, end: int) -> None:
        end = min(end, len(self.body))
        if end > start:
            self.pasted[start:end] = b"\x01" * (end - start)

    def text(self, number: int) -> str:
        start, end = self.lines[number]
        return self.body[start:end].translate(_own._BIDI_MARKS)

    def mark_lines(self, first: int, last: int) -> None:
        """The lines from *first* to *last*, the last one left out."""
        for number in range(first, min(last, len(self.lines))):
            self.mark(self.lines[number][0], self.lines[number][1] + 1)

    def blank(self, number: int) -> bool:
        return number >= len(self.lines) or not self.text(number).strip()

    def taken(self, number: int) -> bool:
        start, end = self.lines[number]
        return end > start and bool(self.pasted[start])

    def paragraph_end(self, number: int) -> int:
        """The first blank line at or after *number*."""
        while number < len(self.lines) and not self.blank(number):
            number += 1
        return number

    def next_text(self, number: int) -> int:
        while number < len(self.lines) and self.blank(number):
            number += 1
        return number

    def line_of(self, at: int) -> int:
        from bisect import bisect_right

        return bisect_right(self.lines, (at, len(self.body) + 1)) - 1

    def parted(self) -> tuple[str, str]:
        told = "".join(" " if gone else ch for ch, gone in zip(self.body, self.pasted, strict=True))
        kept = "".join(ch if gone else " " for ch, gone in zip(self.body, self.pasted, strict=True))
        return told, kept


def _aside(line: str) -> bool:
    return _own._starts_with(line.lstrip(" \t"), (*_own._ASIDES_EN, *_MORE_ASIDES_EN),
                             (*_own._ASIDES_AR, *_MORE_ASIDES_AR))


def _letter_end(msg: _Message, first: int) -> int:
    """Where the letter that starts at line *first* ends (a line number): after
    its sign-off and the name under it, before an aside, or the end."""
    number = first
    while number < len(msg.lines):
        line = msg.text(number)
        if number > first and (number == 0 or msg.blank(number - 1)) and _aside(line):
            return number
        if number > first and _SIGN_OFF.fullmatch(_own._fold(line).strip().lower()):
            end = msg.paragraph_end(number)
            name = msg.next_text(end)
            if end == number + 1 and name < len(msg.lines) and msg.paragraph_end(name) == name + 1 \
                    and len(msg.text(name).split()) <= 3 and not msg.text(name).rstrip().endswith((".", "?", "؟")):
                return name + 1  # "Best," and the name a paragraph below
            return end
        number += 1
    return number


def _code_fences(msg: _Message) -> None:
    fence = ""
    for number in range(len(msg.lines)):
        line = msg.text(number)
        mark = line.lstrip(" \t")[:3]
        if fence:
            msg.mark_lines(number, number + 1)
            if line.strip().startswith(fence) and set(line.strip()) <= {fence[0]}:
                fence = ""
        elif mark in ("```", "~~~"):
            msg.mark_lines(number, number + 1)
            fence = mark


def _quote_lines(msg: _Message) -> None:
    from kazma_core.memory.remember_request import asks_to_remember

    keeps = False
    for number in range(len(msg.lines)):
        line = msg.text(number)
        if _own._QUOTE_LINE_RE.match(line):
            if not keeps:
                msg.mark_lines(number, number + 1)
        elif line.strip():
            keeps = asks_to_remember(line) and not _own._reports(line)


def _is_forward(line: str) -> bool:
    bare = line[:120].lstrip(" \t>-*_")
    if not bare or len(bare.split()) > _FORWARD_WORDS_MAX and not bare.lower().startswith("fwd:"):
        return False
    return _own._starts_with(bare, _own._FORWARD_EN[:5], _own._FORWARD_AR)


def _mail_header(msg: _Message, number: int) -> bool:
    """Whether a mail's header starts at line *number*: two header lines, or
    "From:" and an address or a name."""
    def is_header(n: int) -> bool:
        return n < len(msg.lines) and bool(_MAIL_HEADER.match(_own._fold(msg.text(n)).lstrip(" \t>-*_")))

    if not is_header(number):
        return False
    if is_header(number + 1):
        return True
    line = msg.text(number).lstrip(" \t>-*_")
    name = line.partition(":")[2].strip()
    lone = line.lower().startswith("from") or _own._fold(line).startswith("من")
    return lone and bool(name) and not msg.blank(number + 1) and ("@" in name or _somebody(name))


def _mails(msg: _Message) -> None:
    for number in range(len(msg.lines)):
        if msg.taken(number):
            continue
        line = msg.text(number)
        if not line.strip():
            continue
        if _is_forward(line) or _mail_header(msg, number) or _own._from_someone(line.lstrip(" \t>-*_")):
            msg.mark_lines(number, _letter_end(msg, number))
        elif _own._is_wrote_line(line):
            after = msg.next_text(number + 1)
            msg.mark_lines(number, number + 1)
            if after < len(msg.lines) and msg.taken(after):
                continue  # what they wrote is the ``>`` lines under it
            dated = any(ch.isdigit() for ch in line) or "@" in line
            msg.mark_lines(number, _letter_end(msg, number) if dated else msg.paragraph_end(after))


def _copy_header(msg: _Message, number: int) -> bool:
    line = msg.text(number)
    if _own._TELEGRAM_HEADER_RE.match(line) or _own._DISCORD_HEADER_RE.match(line):
        return True
    if _own._CLOCK_LINE_RE.match(line):  # Slack's, under the name
        return number > 0 and not msg.blank(number - 1) and _somebody(msg.text(number - 1))
    if _own._SLACK_HEADER_RE.match(line):
        return _somebody(re.split(r"[ \t]{2,}", line.strip())[0])
    header = _own._HEADER_RE.match(line)
    return header is not None and _somebody(header.group("name"))


def _chats(msg: _Message) -> None:
    """Exports, copied chats and transcripts."""
    number = 0
    while number < len(msg.lines):
        line = msg.text(number)
        if not msg.taken(number) and line.strip():
            if _own._EXPORT_LINE_RE.match(line):
                end = msg.paragraph_end(number)
                msg.mark_lines(number, end)
                number = end
                continue
            if _copy_header(msg, number):
                end = msg.paragraph_end(msg.next_text(number + 1))
                msg.mark_lines(number, end)
                number = end
                continue
        number += 1
    _speaker_lines(msg)


_BULLET = re.compile(r"[ \t]{0,8}[*•\-–][ \t]")


def _speaker_lines(msg: _Message) -> None:
    spoken: list[tuple[int, str, str, bool]] = []  # line, label, what follows, certain
    mine = False
    for number in range(len(msg.lines)):
        line = msg.text(number)
        if msg.taken(number) or not line.strip():
            continue
        lead = _own._LABEL_LEAD_RE.match(line.lstrip(" \t"))
        bare = line.lstrip(" \t")[lead.end():] if lead else line
        if _BULLET.match(line):
            continue  # a list's field ("* Type: Boots"), never a chat's line
        match = _own._SPEAKER_LINE_RE.match(bare)
        angle = _own._ANGLE_LINE_RE.match(line)
        if match is not None:
            label, rest = match.group(2), bare[match.end():]
            if _own._fold(label).strip().lower() in _ME:
                mine = True
            elif _own._copied_label(match):
                spoken.append((number, label, rest, True))
            elif _certain_speaker(label) or _somebody(label):
                spoken.append((number, label, rest, _certain_speaker(label)))
        elif angle is not None and _somebody(angle.group(1)):
            spoken.append((number, angle.group(1), line[angle.end():], False))
    if not spoken:
        return
    labels = [_own._fold(label).strip().lower() for _n, label, _r, _c in spoken]
    chat = mine or len(set(labels)) < len(labels) or (
        len(labels) > 1 and any(_ANSWERS.search(_own._fold(rest)) for _n, _l, rest, _c in spoken))
    lines = {number for number, *_rest in spoken}
    others = " ".join(_own._fold(msg.text(n)).lower() for n in range(len(msg.lines)) if n not in lines)
    others_words = set(_own._WORD_RE.findall(others))
    for (number, _label, _rest, certain), label in zip(spoken, labels, strict=True):
        named_again = bool(set(_own._WORD_RE.findall(label)) & others_words)
        if chat or certain or named_again:
            msg.mark_lines(number, number + 1)


# ── Heads: an instruction's text, someone's words ────────────────────────


def _hands_over(words: list[str], folded: list[str]) -> bool:
    return any(w in _TEXT_NOUNS_EN for w in words) or any(w in _TEXT_NOUNS_AR for w in folded)


def _of_their_own(words: list[str], folded: list[str]) -> bool:
    """"Write my story in an engaging way: I started ...", "a story of mine",
    "رتب قصتي: ...": a text to work on that tells the user's own doings."""
    if {"my", "mine", "our"} & set(words) and _OWN_TELLING_EN & set(words):
        return True
    return bool(_OWN_TELLING_AR & set(folded))


def _starts(plain: str, verbs: tuple[str, ...], ends: str = " ") -> bool:
    return any(plain == v or plain.startswith(tuple(v + end for end in ends)) for v in verbs)


def _clear_reported(words: list[str], folded: list[str]) -> bool:
    """Whether a head ``own_words`` reads as someone's reporting ("My sister
    said:") clearly is: not "كتبت لأخوي:" (I wrote), "الكل يقول إني مجنون:"
    (as "Everyone says I'm crazy:"), "You won't believe it:"."""
    said = [w for w in folded if w in _own._REPORTING_AR]
    said_en = {w for w in words if w in _own._REPORTING_EN}
    if said == folded[:1] and said[0] in _I_OR_SHE_AR and not said_en:
        return bool(_TO_ME_AR & set(folded))  # "كتبت لي:" is she; "دانة كتبت:" names who
    if (_own._THAT_I_AR | _own._I_AR) & set(folded):
        return False
    of_you = {"you", "you'll", "you'd"} & set(words)
    return not (said_en and not said and of_you and not said_en - _own._MORE_THINKING_EN)


def _clear_instruction(head: str, plain: str, folded: list[str], mark: str, inline: bool) -> bool:
    """Whether a head ``own_words`` reads as an instruction clearly works on
    the text after it."""
    words, bare = _own._words(head), _own._words(plain)
    colon = mark in (":", "：")
    first = _own._ar_verb_forms(folded[0]) if folded else ()
    transform = _starts(plain, _TRANSFORM_EN) or any(v in _TRANSFORM_AR for v in first)
    mine = bool({"my", "our"} & set(words) or _MY_TEXT_AR & set(folded))
    if _of_their_own(words, folded):
        return False
    if not mark:
        return _starts(plain, _ROLES_PLAYED_EN) or any(v in _ROLES_PLAYED_AR for v in first) or (transform and not mine)
    if colon and transform:
        return True
    if plain.startswith(_own._HANDS_OVER_EN) or folded[:1] and folded[0] in _own._HANDS_OVER_AR:
        return _hands_over(words, folded)
    if _starts(" ".join(folded), _own._EXAMPLE_HEADS_AR) or plain.rstrip(" .,").startswith(_own._EXAMPLE_HEADS_EN):
        return False
    if (bare[:1] == ["write"] and "note" in bare) or "ملاحظه" in folded:
        return False  # "Write a note: I paid the rent on 28 May"
    asks = bool(set(bare[:1]) & _own._ASKS_ABOUT_EN or set(folded[:1]) & _own._ASKS_ABOUT_AR)
    if (asks or _starts(plain, _LOOKS_EN)) and not (set(bare) | set(folded)) & (
            _TEXT_NOUNS_EN | _TEXT_NOUNS_AR | _OF_A_TEXT):
        return False
    if colon:
        # "Summary of my day:" is the user's own, "Summarize:" an instruction.
        return not (mine and _own._TEXT_WORK_EN & set(bare) and not _own._head_kind(head, colon=False))
    # "Can you review my CV? I finished my degree yesterday" is the user's.
    return not mine and (transform or not inline)


def _clear_kind(head: str, mark: str, inline: bool) -> str | None:
    """``own_words``'s kind of a head, where it is clear that what follows is
    not the user telling. *mark* ends the head (":", "?", a line break, or ""
    for none) and *inline* says text follows it on its line."""
    colon = mark in (":", "：")
    question = mark in ("?", "؟")
    plain = _own._strip_polite_en(head.lower().replace("’", "'").strip())
    folded = _own._ar_words(head)
    first = _own._ar_verb_forms(folded[0]) if folded else ()
    if _starts(plain, _SUPPOSING_EN, " ,!") or any(v in _SUPPOSING_AR for v in first):
        return None
    if any(v in _REPLY_AR for v in first) and folded[1:2] == ["علي"] and any(
            w in _POINTS_AR or w in _TEXT_NOUNS_AR for w in folded[2:5]):
        return None if inline and not colon else _own._INSTRUCTION  # "رد على هذا:"
    kind = _own._head_kind(head, colon=colon or question, question=question)
    if kind == _own._REPORTED:
        return kind if colon and _clear_reported(_own._words(head), folded) else None
    if kind is None or not _clear_instruction(head, plain, folded, mark, inline):
        return None
    return kind


def _heads(msg: _Message) -> None:
    body = msg.body
    done = 0
    for start in _own._head_starts(body):
        if start < done or start >= len(body) or msg.pasted[start]:
            continue
        window = _own._without_addresses(body[start:start + _own._HEAD_MAX + 1])
        stops = [p for p in map(window.find, (":", "：", "\n", "?", "؟")) if p > 0]
        if not stops:
            done = max(done, _open_instruction(msg, start, window))
            continue
        stop = min(stops)
        head = window[:stop]
        if any(head[i] in _own._SENTENCE_ENDS and head[i + 1] in " \t" for i in range(len(head) - 1)):
            continue  # the sentence after this one starts a head of its own
        after = start + stop + 1
        number = msg.line_of(start + stop)
        inline = bool(body[after:msg.lines[number][1]].strip())
        kind = _clear_kind(head, window[stop], inline)
        if kind is None:
            continue
        end = _text_end(msg, number, after, inline, kind)
        msg.mark(after, end)
        done = max(done, end)


def _open_instruction(msg: _Message, start: int, window: str) -> int:
    """"Translate to French I bought a Rolex yesterday.": no colon, the text
    is the sentence (an address: the address alone)."""
    if _clear_kind(window, "", True) is None or not _own._starts_with_text_verb(window):
        return start
    end = _own._sentence_end(msg.body, start)
    if _own._URL_RE.search(msg.body, start, min(end, start + _own._HEAD_MAX + 1)):
        return start
    msg.mark(start, end)
    return end


def _text_end(msg: _Message, number: int, after: int, inline: bool, kind: str) -> int:
    """Where the text a head introduces ends (a place in the body)."""
    body = msg.body
    if inline and kind == _own._REPORTED:
        return _own._sentence_end(body, after)
    if inline:
        end = msg.lines[msg.paragraph_end(number) - 1][1]
        for place in _own._head_starts(body[after:end])[1:]:
            if _own._is_aside(body, after + place) or _aside(body[after + place:after + place + 40]):
                return after + place
        return end
    first = msg.next_text(number + 1)
    if first >= len(msg.lines) or msg.taken(first):
        return after  # the text is the quote under it, marked already
    if _aside(msg.text(first)):
        return after
    if _LETTER_OPENS.fullmatch(_own._fold(msg.text(first))):
        last = _letter_end(msg, first)
    else:
        last = msg.paragraph_end(first)
        for n in range(first + 1, last):
            if _aside(msg.text(n)) or _own._is_aside(body, msg.lines[n][0]):
                last = n
                break
    return msg.lines[last - 1][1]


# ── Quotations ───────────────────────────────────────────────────────────

#: Arabic verbs of "I" and "we" that begin a sentence ("فتحنا فرع", "شريت
#: رولكس"); a few nouns end alike.
_VERB_OF_I_AR = re.compile(r"\w{2,}(?:نا|ت)")
_NOUNS_IN_T = frozenset({"بنت", "بيت", "وقت", "صوت", "موت", "تحت", "زيت", "اخت", "حوت", "توت", "كنت", "ليت", "بنات",
                         "بيوت", "ذكريات", "حكايات", "سنوات", "ايات", "كلمات", "صمت", "الوقت", "البيت", "البنت"})


def _whole_sentence(span: str) -> bool:
    """Whether a quoted *span* is a sentence someone says of themself, never a
    title ("I Will Survive", «بيت من ورق»)."""
    words = _own._WORD_RE.findall(span)
    if len(words) < 2:
        return False
    if words[0].isascii():
        if len(words) < 3:
            return False
        long = [w for w in words[1:] if len(w) > 3]
        titled = bool(long) and sum(w[0].isupper() for w in long) * 2 > len(long)
        return not titled and _own._speaks_in_first_person(span)
    first = _own._fold(words[0])
    return _own._speaks_in_first_person(span) or (
        bool(_VERB_OF_I_AR.fullmatch(first)) and first not in _NOUNS_IN_T and not first.startswith("ال"))


def _says_so_themself(before: str) -> bool:
    if _I_SAID_EN.search(before.lower().replace("’", "'")):
        return True
    folded = _own._WORD_RE.findall(_own._fold(before))
    return any(w in _I_SAID_AR and not set(folded[k + 1:k + 2]) & {"لي", "لنا"} for k, w in enumerate(folded))


def _quotations(msg: _Message) -> None:
    from kazma_core.memory.remember_request import asks_to_remember

    body = msg.body
    for match in _own._QUOTED_SPAN_RE.finditer(body):
        if msg.pasted[match.start()] or not _whole_sentence(match.group(0)):
            continue
        before = body[max(0, match.start() - 2 * _own._HEAD_MAX):match.start()]
        cut = max((m.end() for m in re.finditer(r"[.!?؟]\s|\n", before)), default=0)
        before = before[cut:]
        if _says_so_themself(before) or (asks_to_remember(before) and not _own._reports(before)):
            continue
        msg.mark(match.start(), match.end())


#: A sentence that ends by naming who says it, and nothing more: "..., he
#: says.", "..., says the mayor", "..., قالت أختي".
_SAYS_WHO_EN = re.compile(r"(?:he|she|they) (?:says|said|writes|wrote|adds|added)|(?:says|said) (?:the|his|her) [\w-]+")
_SAYS_WHO_AR = re.compile(r"(?:قال|قالت|يقول|تقول) (\w+)")


def _direct_speech(msg: _Message) -> None:
    at = 0
    for sentence in _own._sentences(msg.body):
        start, at = at, at + len(sentence)
        comma = max(sentence.rfind(","), sentence.rfind("،"))
        if comma <= 0 or msg.pasted[start]:
            continue
        tail = sentence[comma + 1:].strip().rstrip(".!؟?").lower()
        who = _SAYS_WHO_AR.fullmatch(_own._fold(tail))
        if _SAYS_WHO_EN.fullmatch(tail) or (who is not None and (
                who[1].startswith("ال") or _arabic_stems(who[1]) & _folded_set(_ROLES_AR_RAW))):
            msg.mark(start, at)


@lru_cache(maxsize=64)
def told_and_pasted(text: str) -> tuple[str, str]:
    """*text* parted: what the user may be telling, and what is clearly pasted
    or another person's. Each keeps its place (the other part is blank)."""
    from kazma_core.safety.prompt_fence import outside_fences

    whole = str(text or "")
    body = outside_fences(whole)
    fenced = whole[: 4 * _READ_MAX] if body != whole else ""
    stub = body.find(_own._WEB_FILE_STUB)
    attached = body[stub:stub + _READ_MAX] if stub >= 0 else ""
    msg = _Message((body[:stub] if stub >= 0 else body)[:_READ_MAX])
    for match in _own._STUB_RE.finditer(msg.body):
        msg.mark(match.start(), match.end())
    _code_fences(msg)
    _quote_lines(msg)
    _mails(msg)
    _chats(msg)
    _heads(msg)
    _quotations(msg)
    _direct_speech(msg)
    told, pasted = msg.parted()
    return told, "\n".join(x for x in (pasted, attached, fenced) if x.strip())
