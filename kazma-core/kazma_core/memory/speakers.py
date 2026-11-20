"""Who is "I" in a shared space (Spaces S1-11; the R3 study, N30 to N36).

Memory files a person's facts under one entry, written as the word ``user``.
In a shared space several people talk to Kazma, and each needs a "self" of
their own: Sara's "I live in Kuwait City" and Omar's "I live in Lusail" are
two facts about two people, never a newer statement of one fact.

This module is the one place that knows about members. Everything else asks
it:

* **which spaces file by member** (:func:`room`, decision N30): a shared space
  (no ``personal_of`` in the identity store), from its first turn and while
  it has one member, once Spaces is enforced. A personal space -- every space
  an install has today, the owner's ``default`` included -- keeps the one
  ``user`` entry, and no line of memory behaves differently in it;
* **a member's entry** (:func:`entry_id`): ``member_<account id>``. A name can
  change and two people can share one; a memory key must not move when a name
  does. The shape is reserved: a model's subject of that shape is refused
  (``belief_extractor._sanitize_belief``), so only Kazma writes one;
* **where a fact goes** (:meth:`Room.filed`): ``user`` -- what the model and
  the quick pass write for "I" -- is the speaker's entry; a member's name or
  one they are called by (kept by ``identity.set_called_names``) is that
  member's entry; anything else stays. In a
  shared space a first-person fact whose speaker is not known is not filed
  (the turn is still remembered): nothing is filed under a guessed member;
* **whose words they are** (:meth:`Room.label`, N34): a statement about a
  member by someone else is ``member_stated``, which the trust gate never
  lets replace that member's own words;
* **how it is shown** (:meth:`Room.shown_subject`, :meth:`Room.shown_author`):
  the asker's own entry and turns as ``user`` / ``User:``, as today; another
  member's under their name;
* **what a question is about** (:func:`refers_to_asker`, :meth:`Room.named_in`):
  recall shows a question about the asker their own facts first, and a
  question that names a member that member's (the study's "self first" and
  "named first", section 2.4);
* **the text a fact's vector is made from** (:func:`fact_vector_text`): a
  member's facts are embedded as ``user <predicate> <object>``, the text a
  fact about ``user`` is embedded from today. Recall leans on that text: a
  lone member embedded under an id or a name moved 24 to 27 of the 78 gated
  scores of dataset v5; embedded as ``user ...`` none moved (study 4.3).

:func:`room` reads the identity store: call it off the event loop. It is a
loop-stall helper.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

logger = logging.getLogger(__name__)

__all__ = [
    "MEMBER_PREFIX",
    "MEMBER_STATED",
    "Member",
    "Room",
    "account_of_entry",
    "entry_id",
    "fact_vector_text",
    "is_member_entry",
    "refers_to_asker",
    "room",
]

#: The source label of a statement about a member by someone else (N34). It
#: is not one of ``provenance.USER_SOURCES``: the trust gate never lets it
#: replace the member's own words, and the member's own words replace it.
MEMBER_STATED = "member_stated"

MEMBER_PREFIX = "member_"
#: A member's entry: the prefix and an account id (``spaces.scope``'s shape).
_ENTRY_RE = re.compile(r"member_[a-z0-9][a-z0-9_-]{0,63}")
#: The hub every first-person fact of a personal space is filed under.
USER = "user"


def entry_id(account_id: str) -> str:
    """The entry a member's facts are filed under."""
    return f"{MEMBER_PREFIX}{account_id}"


def is_member_entry(subject: object) -> bool:
    """True for a subject of the reserved shape, whoever wrote it."""
    return isinstance(subject, str) and _ENTRY_RE.fullmatch(subject) is not None


def account_of_entry(subject: object) -> str | None:
    """The account a member's entry is for, else None."""
    return subject[len(MEMBER_PREFIX):] if is_member_entry(subject) else None


def fact_vector_text(subject: object, predicate: object, obj: object) -> str:
    """The text a fact's vector is made from. A member's entry is embedded as
    ``user`` (module docstring); every other fact as it always was. The one
    copy: the mutation, the re-embed and the repair pass all call it."""
    sub = USER if is_member_entry(subject) else str(subject or "")
    return f"{sub} {predicate or ''} {obj or ''}".strip()


# ── Words ───────────────────────────────────────────────────────────────


def _fold(text: str) -> str:
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(str(text or "")).lower().replace("’", "'")


_WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?")
#: Prefixes an Arabic word carries in front of a name ("وعمر", "لسارة"): one
#: is taken off, so "and Omar" names Omar.
_AR_JOINED = ("و", "ف", "ب", "ل")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(_fold(text))


def _is_arabic(word: str) -> bool:
    return bool(word) and "ء" <= word[0] <= "ي"


def _forms(word: str) -> set[str]:
    """A word, without an English possessive ("omar's" names Omar) and,
    for Arabic, without one joined prefix."""
    out = {word, word.split("'")[0]}
    if _is_arabic(word) and len(word) > 2 and word[0] in _AR_JOINED:
        out.add(word[1:])
    return out


# ── Is the question about the asker? ────────────────────────────────────

#: English words by which a speaker means themself alone. "We", "our" and
#: "us" include others: a question in the plural shows everyone's facts.
_EN_SELF = frozenset({"i", "i'm", "im", "i've", "ive", "i'd", "i'll", "me", "my", "mine", "myself"})
#: Arabic (folded) words that are the speaker by themselves: the pronoun,
#: "I have" (Gulf "عندي", MSA "لديّ"), "to me", "with me", "from me", "about
#: me", "in me", "I want" (Gulf "ابي", "ابغي", "ودي"), "mine" (Gulf "حقي",
#: "مالي"), "myself".
_AR_SELF = frozenset({
    "انا", "اني", "عندي", "لدي", "لي", "ليا", "معي", "مني", "عني", "فيني", "ابي", "ابغي", "ودي",
    "حقي", "حقتي", "مالي", "مالتي", "نفسي", "روحي",
})
#: First-person imperfects that are three letters long: a doubled root's
#: (C2 = C3, written once: "أحبّ", "أظنّ", "أشكّ"), and a weak root's whose
#: last radical falls away or is a vowel ("أجي", "أرى"). The general rule
#: below needs four letters, because most three-letter words that start
#: with alef are nouns or particles ("أهل", "أمس", "أين"); these are named.
_AR_SHORT_I_VERBS = frozenset({
    "احب", "احس", "اظن", "اشك", "اود", "احط", "اشم", "احل", "اعد", "اجي", "اري",
})
#: A hamza-initial root's first-person imperfect is written with alef madda
#: and three letters ("آكل", "آخذ": the "أ" of "I" and the root's own hamza);
#: these words with alef madda are nouns or adjectives instead.
_AR_MADDA_NOT_I = frozenset({"آخر", "آمن", "آية", "آلة", "آدم", "آسف"})
#: Arabic's short vowel marks and the tatweel: taken off a word before its
#: written form is read.
_AR_MARKS_RE = re.compile("[\u064B-\u065F\u0670\u0640]")


def _object_me(word: str) -> bool:
    """An imperfect verb of the third or second person (prefix "ي" or "ت")
    with the object suffix "ني", "me": "يتعبني" (it tires me), "يعجبني",
    "تساعدني". At least three letters stand between the two, and the last of
    them is not a long "ا" or "و": a word ending "-اني" or "-وني" is an
    adjective of origin ("يوناني", "تلفزيوني"), and with less between them
    the word is a noun or a particle ("يمني", "تقني", "يعني")."""
    if len(word) < 6 or word[0] not in ("ي", "ت") or not word.endswith("ني"):
        return False
    stem = word[1:-2]
    return len(stem) >= 3 and stem[-1] not in ("ا", "و")
#: Folded Arabic words ending in ي that are not "my ...": particles,
#: pronouns, question words, demonstratives, numbers, and "علي" ("on" or the
#: name Ali), read as the speaker's only when standing alone (above).
_AR_NOT_MINE = frozenset({
    "في", "الي", "اللي", "هي", "شي", "يعني", "حتي", "متي", "اي", "ماهي", "انتي", "هذي", "ذي",
    "كي", "لكي", "التي", "الذي", "اللتي", "ثاني", "ثانيه", "باقي", "عادي", "كافي", "حالي", "ماضي",
    "بعدي", "قبلي", "علي",
})
#: Folded Arabic words starting with alef that are not a first-person verb
#: (أفعل): pronouns, question words, the elative of common adjectives
#: ("أكثر", "أفضل"), kinship and common nouns, and adverbs.
_AR_ALEF_NOT_I = frozenset({
    "انت", "انتي", "انتم", "انتو", "انتن", "احنا", "اين", "ايش", "ايه", "اذا", "اما", "انه", "انها",
    "اسم", "اسمه", "اسمها", "امس", "ابو", "اخو", "اخت", "اخوي", "اهل", "اول", "اخر", "اخير", "احد",
    "اكثر", "اقل", "افضل", "احسن", "اسوا", "اقرب", "ابعد", "اكبر", "اصغر", "اطول", "اقصر", "اغلي",
    "ارخص", "اسرع", "اجمل", "احلي", "اهم", "اكيد", "ايضا", "اصلا", "ابدا", "امبارح", "اسبوع",
    "اجازه", "اكل", "امي", "ابوي", "اخوك", "اختي", "امريكا", "اوروبا", "اسيا", "افريقيا", "ابريل",
    "اغسطس", "اكتوبر", "امارات", "اكو", "ماكو",
})


def refers_to_asker(question: str, names: Iterable[str] = ()) -> bool:
    """Whether a question is about the asker themself: a first-person word
    in English ("I", "my", "me"), or in Arabic the pronoun, a first-person
    word standing alone ("عندي", "حقي"), a noun with the possessive "ي"
    ("بيتي", "حساسيتي") or an imperfect verb with the first-person "أ"
    ("أسكن", "أشتغل"). The plural ("we", "our", "نا") is not the asker
    alone. *names* are the members' names: "أحمد" is a name, not a verb.
    Three forms are named apart: a three-letter first-person imperfect
    ("أحب", "آكل"), an object "me" on another person's verb ("يتعبني"), and
    MSA "لديّ".

    It leans one way: a question read as the asker's shows the asker's own
    facts and hides other members' (their turns stay); a question it misses
    shows everyone's, each under its owner's name. So a word is the asker's
    on narrow evidence.
    """
    not_mine = {w for name in names for w in _words(name)}
    written = _WORD_RE.findall(_AR_MARKS_RE.sub("", str(question or "")).replace("’", "'"))
    for raw in written:
        word = _fold(raw)
        if word in _EN_SELF:
            return True
        if not _is_arabic(word) or word in not_mine:
            continue
        if word in _AR_SELF:
            return True
        joined = len(word) > 4 and word[0] in ("و", "ف")
        bare = word[1:] if joined else word
        if bare in _AR_NOT_MINE or bare in not_mine or bare.startswith("ال"):
            continue
        if bare in _AR_SHORT_I_VERBS or _object_me(bare):
            return True
        unjoined = raw[1:] if joined else raw
        if len(unjoined) == 3 and unjoined[0] == "آ" and unjoined not in _AR_MADDA_NOT_I:
            return True
        if len(bare) >= 4 and bare.endswith("ي") and bare[0] != "ي":
            return True
        if len(bare) >= 4 and bare[0] == "ا" and bare not in _AR_ALEF_NOT_I:
            return True
    return False


# ── A space's people ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Member:
    """One member of a shared space: the account, the name it is shown
    under, and the other names it is called by (Latin and Arabic)."""

    account: str
    name: str
    called: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(n for n in (self.name, *self.called) if n)


def _same_words(text: str, name: str) -> bool:
    wanted = _words(name)
    return bool(wanted) and _words(text) == wanted


@dataclass(frozen=True)
class Room:
    """The people of one space, as memory files and shows them."""

    space: str
    shared: bool
    members: Mapping[str, Member] = field(default_factory=lambda: MappingProxyType({}))

    # ── Writing ─────────────────────────────────────────────────────────

    def entry_for(self, account: str | None) -> str | None:
        """The entry "I" is filed under for *account*: ``user`` in a personal
        space; the member's entry in a shared one; None for a speaker who is
        not known or not a member (nothing is filed for them)."""
        if not self.shared:
            return USER
        if account and account in self.members:
            return entry_id(account)
        return None

    def member_named(self, text: object) -> Member | None:
        """The member a subject or an object names: their name or one they
        are called by, word for word, any case, Arabic folded."""
        if not self.shared or not isinstance(text, str) or not text.strip():
            return None
        spoken = text.replace("_", " ")
        for member in self.members.values():
            if any(_same_words(spoken, name) for name in member.names):
                return member
        return None

    def filed(self, value: object, speaker: str | None) -> str | None:
        """Where *value* (a fact's subject, or its object) is filed for a
        statement by *speaker*. In a personal space: unchanged. In a shared
        space: ``user`` is the speaker's entry (None when the speaker is not
        known: not filed), a member's name is that member's entry, anything
        else stays."""
        if not self.shared or not isinstance(value, str):
            return value if isinstance(value, str) else None
        if value == USER:
            return self.entry_for(speaker)
        member = self.member_named(value)
        return entry_id(member.account) if member else value

    def label(self, method: str, subject: str, speaker: str | None) -> str:
        """A write's source label: a member's own words about another
        member are ``member_stated`` (N34). Everything else keeps its
        label: a statement about a shared thing (a trip, a project) is any
        member's to make."""
        from kazma_core.memory.provenance import USER_SOURCES

        if (self.shared and method in USER_SOURCES and is_member_entry(subject)
                and subject != self.entry_for(speaker)):
            return MEMBER_STATED
        return method

    def said_by(self, speaker: str | None) -> str | None:
        """What a write keeps as its author: the speaker's account in a
        shared space, nothing in a personal one (its rows stay byte for byte
        as they were)."""
        return speaker if self.shared and speaker else None

    # ── Reading ─────────────────────────────────────────────────────────

    def display_name(self, account: str) -> str:
        member = self.members.get(account)
        return (member.name if member and member.name else account) if account else ""

    def shown_subject(self, subject: str, asker: str | None) -> str:
        """How a fact's subject reads to *asker*: their own entry is
        ``user`` (as a personal space reads), another member's is that
        member's name, anything else is itself."""
        account = account_of_entry(subject)
        if not self.shared or account is None:
            return subject
        return USER if account == asker else self.display_name(account)

    def shown_author(self, said_by: str | None, asker: str | None) -> str | None:
        """The name a turn is shown under for *asker*: None for their own
        (shown ``User:``, as today) and for a turn with no author; another
        member's name for theirs."""
        if not self.shared or not said_by or said_by == asker:
            return None
        return self.display_name(said_by)

    def ranking_names(self) -> dict[str, str]:
        """Every member's entry and the name recall ranks it by (lower
        case): "omar lives in lusail" holds the word "omar"."""
        return {entry_id(a): m.name.lower() for a, m in self.members.items() if m.name}

    def named_in(self, question: str) -> set[str]:
        """The members a question names, by name or a name they are called
        by (every word of the name, an Arabic word with or without one
        joined prefix)."""
        if not self.shared:
            return set()
        held = {form for word in _words(question) for form in _forms(word)}
        return {
            account for account, member in self.members.items()
            if any(_words(name) and set(_words(name)) <= held for name in member.names)
        }

    def all_names(self) -> list[str]:
        return [name for member in self.members.values() for name in member.names]

    def others(self, asker: str | None) -> list[Member]:
        return [m for a, m in self.members.items() if a != asker]


def episode_metadata(space_id: str | None, said_by: str | None) -> dict[str, str] | None:
    """What an episode written from a turn keeps of its author: ``said_by``
    (the asker's account) in a shared space; nothing in a personal one, whose
    rows stay as they were."""
    if not said_by:
        return None
    kept = room(space_id).said_by(said_by)
    return {"said_by": kept} if kept else None


# ── Who may forget what (N32, N33) ────────────────────────────────────

#: A refusal's code: the turn is another member's and the asker is not the
#: space's owner (N32).
NOT_YOURS = "not_yours"


def may_forget_turn(space_id: str | None, asker: str | None, said_by: str | None) -> bool:
    """Whether *asker* may forget a turn *said_by* wrote (N32): in a shared
    space its author and the space's owner; anyone in a personal space. A
    turn with no author there is the owner's to forget. An unreadable
    identity store raises: "may" is never guessed."""
    if not room(space_id).shared:
        return True
    if asker and said_by and asker == said_by:
        return True
    from kazma_core.spaces import identity

    return bool(asker) and identity.owns_space(asker, str(space_id))


def may_keep_chat_out(space_id: str | None, asker: str | None, chat_id: str) -> bool:
    """Whether *asker* may keep a chat out of memory (N32): in a shared space
    the chat's opener (its record, Spaces S1-07) and the space's owner."""
    if not room(space_id).shared:
        return True
    from kazma_core.spaces.threads import record_of

    record = record_of(chat_id)
    return may_forget_turn(space_id, asker, record.opener if record else None)


def forget_note(space_id: str | None, asker: str | None, subject: str | None,
                said_by: str | None) -> dict[str, str] | None:
    """What Forget says when a member forgets a fact about themself that
    another member stated (N33): the fact is gone and not learned again from
    that message, and the message stays -- it still holds the words, can
    still be recalled, and its author or the space's owner can forget it.
    ``{"code": "others_message_stays", "author": name}``, or None."""
    people = room(space_id)
    if not people.shared or not said_by or said_by == asker:
        return None
    if account_of_entry(subject) != asker:
        return None
    return {"code": "others_message_stays", "author": people.display_name(said_by)}


def _personal_room(space: str = "") -> Room:
    """A space that files every first-person fact under ``user``."""
    return Room(space=space, shared=False)


_OVERRIDE: dict[str, Room] = {}


@contextlib.contextmanager
def private_room(people: Room) -> Iterator[None]:
    """While inside, :func:`room` answers *people* for its space, with no
    identity store read. For a private database seeded by a benchmark or a
    test (``memory.benchmark.run_speaker_benchmark``), never for live work."""
    _OVERRIDE[people.space] = people
    try:
        yield
    finally:
        _OVERRIDE.pop(people.space, None)


def room(space_id: str | None) -> Room:
    """The people of *space_id*, read from the identity store.

    A personal space, a tenant that is no space (an older memory mode's),
    and every space while Spaces is not enforced: a personal room. A shared
    space: a shared room with its members. An unreadable identity store
    raises (``identity.IdentityUnavailable``): a turn is never filed under a
    guessed member; its write fails and the queue tries it again."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID, spaces_enforced

    space = str(space_id or "")
    if space in _OVERRIDE:
        return _OVERRIDE[space]
    if not space or space == OWNER_SPACE_ID or not spaces_enforced():
        return _personal_room(space)
    from kazma_core.spaces import identity

    reg = identity.read_registry()
    found = reg["spaces"].get(space)
    if not found or found.get("personal_of"):
        return _personal_room(space)
    members: dict[str, Member] = {}
    for membership in reg["memberships"]:
        if membership.get("space") != space:
            continue
        account = str(membership.get("account") or "")
        record = reg["accounts"].get(account) or {}
        if not account:
            continue
        called = tuple(str(n).strip() for n in (record.get("called") or ()) if str(n).strip())
        members[account] = Member(account=account, name=str(record.get("name") or "").strip(),
                                  called=called)
    return Room(space=space, shared=True, members=MappingProxyType(members))
