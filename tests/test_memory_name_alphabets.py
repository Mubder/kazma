"""A person held in one alphabet and spoken of in the other (the memory plan's W6, step 2).

A fact held of ``omar`` was not replaced by "عمر نقل الدوحة" when the reply
wrote the name as the user did: ``belief_extractor._take_replaces`` compared
the two names letter for letter, and the old home and the new one both stayed
(two of thirty replies of live's model, 25 Nov). ``memory/name_alphabets.py``
knows a person's name in both; **it never joins two people**: only written
pairs match, a Latin spelling people use for two names is in none
(:data:`NOT`, :data:`RIVALS`), a name it does not know matches nothing, and a
store where two could be meant, or where the name as said is somebody already,
keeps both facts as before. A member's own name is that member's (N30).

Negative controls: the match taken away (the pairs stay open), a rival's
spelling planted in the list (the gate says so), each guard taken away.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory import belief_extractor, held_facts, name_alphabets
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.name_alphabets import may_be, same_name
from kazma_core.memory.schema_v2 import ensure_primary_schema

SAME = [
    ("omar", "عمر"), ("Umar", "عُمَر"), ("noura", "نورة"), ("noura", "نوره"), ("noor", "نور"), ("khaled", "خالد"),
    ("khalid", "خالد"), ("abdulrahman", "عبد الرحمن"), ("abdul_rahman", "عبدالرحمن"), ("abdullah", "عبد الله"),
    ("bu_fahad", "بو فهد"), ("abu_fahad", "أبو فهد"), ("bufahad", "بوفهد"), ("um_ahmad", "أم أحمد"),
    ("omar_alharbi", "عمر الحربي"), ("omar_al_harbi", "عمر_الحربي"), ("ali", "علي"), ("mohammed", "محمد"),
    ("fatima", "فاطمة"), ("aisha", "عائشة"), ("layla", "ليلى"), ("sara", "سارة"), ("sarah", "سارة"),
    ("salma", "سلمى"), ("mariam", "مريم"), ("hessa", "حصة"), ("zain", "زين"), ("lulwa", "لولوة"), ("huda", "هدى"),
    ("yousef", "يوسف"), ("saud", "سعود"), ("mona", "منى"), ("reem", "ريم"), ("maha", "مها"), ("faisal", "فيصل"),
    ("nasser", "ناصر"), ("jassim", "جاسم"), ("david", "ديفيد"), ("maria", "ماريا"),
]

#: Two people, or one name that could be another's: no match, either way round.
NOT = [
    ("amr", "عمر"), ("ammar", "عمر"), ("amer", "عمر"), ("omar", "عمرو"), ("omar", "عمار"), ("omar", "عامر"),
    ("omar", "عمير"), ("karima", "كريم"), ("karim", "كريمة"), ("khalida", "خالد"), ("khaled", "خالدة"),
    ("noura", "نور"), ("noor", "نورة"), ("amira", "أمير"), ("samira", "سمير"), ("sameer", "سمر"), ("salem", "سلمى"),
    ("salem", "سليم"), ("saleem", "سالم"), ("salman", "سليمان"), ("hassan", "حسن"), ("hassan", "حسان"),
    ("amal", "أمل"), ("amal", "آمال"), ("amir", "أمير"), ("amir", "عامر"), ("rashid", "راشد"), ("rashid", "رشيد"),
    ("lina", "لينا"), ("dana", "دانة"), ("hamed", "حمد"), ("zahra", "زهرة"), ("amina", "آمنة"), ("hussein", "حسن"),
    # The review of step 2: what the rules had joined.
    ("eli", "علي"), ("samer", "سمر"), ("samer", "سامر"), ("eiman", "أيمن"), ("eiman", "إيمان"), ("enas", "أنس"),
    ("alrashid", "الراشد"), ("alrashid", "الرشيد"), ("alsalim", "السالم"), ("alsalim", "السليم"), ("majid", "ماجد"),
    ("majid", "مجيد"), ("ela", "علاء"), ("nora", "نورة"), ("nora", "نورا"), ("hakim", "حكيم"), ("hakim", "حاكم"),
    ("khaled_alrashid", "خالد الراشد"), ("said", "سعيد"), ("sad", "سعد"), ("nor", "نور"), ("wed", "وعد"), ("ward", "ورد"),
    ("omar", "عمر الحربي"), ("omar_alharbi", "عمر"), ("omar_alharbi", "عمر العتيبي"), ("omar_otaibi", "عمر الحربي"),
    ("abdullah", "عبد الرحمن"), ("fahad", "بو فهد"), ("abu_fahad", "فهد"), ("berlin_office", "مكتب برلين"),
    ("falcon_project", "مشروع فالكون"), ("user", "المستخدم"), ("", "عمر"), ("omar", ""), ("omar", "omar"), ("عمر", "عمر"),
]


@pytest.mark.parametrize(("latin", "arabic"), SAME)
def test_one_name_in_two_alphabets(latin, arabic):
    assert same_name(latin, arabic) and same_name(arabic, latin)


@pytest.mark.parametrize(("latin", "arabic"), NOT)
def test_two_names_never_match(latin, arabic):
    assert not same_name(latin, arabic) and not same_name(arabic, latin)


#: Spellings people use for a name that is NOT the list's nearest one, or for
#: two names: Arabic name -> what it is written as. None may be another name's.
RIVALS = """
إيلي:eli,ely,elie إيناس:enas,inas,einas سامر:samer,samir مجيد:majid,majeed حاكم:hakim,hakem نورا:nora,norah كامل:kamel,kamil
حسان:hassan,hasan,hassaan حسن:hassan,hasan حماد:hammad,hamaad حامد:hamed,hamid حميدة:hamida عالية:alia,aliya علياء:alia,alya علية:alia
علا:ola,ala آلاء:alaa,ala علاء:alaa,ala عامر:amer,amir أمير:amir,emir أمل:amal آمال:amal أمينة:amina,ameena آمنة:amina أنور:anwar أنوار:anwar
أياد:ayad عياد:ayad,ayyad عيد:eid,aid سايد:sayed,sayd سيد:said,syed صعيد:said نصير:nasir,naseer نصار:nassar فهيد:fuhaid,fahid فهيم:fahim
فايز:fayez,faiz فيض:faiz فاضل:fadel,fadil فضل:fadl,fadel زايد:zayed زهرة:zahra زهراء:zahra هالة:hala هلا:hala حلا:hala هنا:hana هناء:hana حنا:hana,hanna
هيفا:haifa حيفا:haifa سها:suha,soha سهى:suha,soha جنى:jana جنا:jana جنة:jana,janna دانا:dana دانة:dana لينا:lina لينة:lina لين:leen,lean ملاك:malak ملك:malak,malik
مليك:malik راشد:rashid رشيد:rashid رشد:rushd رشا:rasha راشا:rasha رياض:riyad,riad رعد:raad رند:rand ريان:rayan,ryan ريام:riyam سليم:salim سالم:salim سلمة:salama
سلامة:salama سلوى:salwa سمية:somaya سناء:sana سنا:sana صنعاء:sanaa سهام:siham سحر:sahar سهر:sahar شهيد:shahid شاهد:shahid صادق:sadiq صديق:sadiq,siddiq صفا:safa
صفاء:safa ضياء:dia,diya طيب:tayeb طيبة:taiba عادل:adel عدل:adl عديل:adeel عبيدة:obaida,ubaida عباد:abbad,abad عزت:ezzat عزة:azza,aza عصام:essam,issam,isam
عاصم:assem عطا:atta,ata عطاء:ata عفيف:afif عقيل:aqeel,akil عليم:aleem غالي:ghali غالية:ghalia غلا:ghala فاتن:faten فتحي:fathi فؤاد:fouad,fuad فاروق:farouk,faruq
قيس:qais,kais كرم:karam كارم:karem لطفي:lutfi,lotfi لمى:lama لؤي:loay,louay ماهر:maher مهند:mohannad مجد:majd منار:manar مي:mai,may ميار:mayar ناجي:naji نجيب:najib
نداء:nidaa,nida نور الدين:nouraddin نعيمة:naima نعمان:noman,numan نوار:nawar هديل:hadeel هلال:hilal همام:hammam وجدان:wijdan وحيد:wahid,waheed واحد:wahed وسيم:waseem,wasim
وعد:waad,wed ورد:ward وردة:warda يارا:yara يسرى:yusra يسر:yusr يمنى:yumna باسمة:basima بشار:bashar بشارة:bishara بديع:badi بدرية:badria تمارا:tamara جبر:jabr,jaber
جهان:jihan جود:joud,jude حاتم:hatim حكم:hakam حليم:halim,haleem حنين:hanin حيان:hayan دانية:dania دينا:dena رائدة:raida رضوان:ridwan رضي:radi رقي:raqi زكي:zaki
زين العابدين:zainalabidin سراج:siraj سعاد:saad,souad سعدون:saadoun سعدي:saadi سلطانة:sultana عمرو:amr عمير:omair,omer عمارة:amara عمار:amar,ammar فرات:furat فراس:firas
فراح:farah مريام:miriam مروى:marwa مسعود:masoud مسعد:mosaad معن:maan منذر:monzer منصف:moncef ناصح:naseh نادي:nadi نديم:nadim هادية:hadia هزاع:hazza هيام:hiyam
""".split()

#: Declared: the other name is a rare one, and the spelling is taken as the usual name's.
RARE = {("عزة", "aza"), ("حاتم", "hatim"), ("عمير", "omer"), ("سعاد", "saad"), ("فراح", "farah"), ("مروى", "marwa"),
        ("حنين", "hanin"), ("جبر", "jaber"), ("سمية", "somaya"), ("فراس", "firas"), ("سعاد", "souad"), ("صفاء", "safa"),
        ("عاصم", "assem"), ("عادل", "adel"), ("عمار", "ammar"), ("عمرو", "amr"), ("دينا", "dena")}


def _joined_rivals() -> list[tuple[str, str]]:
    """The rivals whose spelling is read as another name of the list."""
    from kazma_core.documents.arabic import fold_for_search

    one = name_alphabets._names()[1]
    return [(rival, spelling) for rival, _, spellings in (e.partition(":") for e in " ".join(RIVALS).replace("_", " ").split())
            for spelling in spellings.split(",")
            if one.get(spelling) not in (None, fold_for_search(rival)) and (rival, spelling) not in RARE]


def test_no_rivals_spelling_is_another_name():
    """The class: every rival there is, every way it is written."""
    assert len(RIVALS) > 190
    assert _joined_rivals() == []


def test_no_spelling_of_the_whole_list_is_two_names():
    whose: dict[str, list[str]] = {}
    for entry in name_alphabets._NAMES:
        name, _, spellings = entry.partition(":")
        assert spellings and all(sp.isascii() and sp.isalpha() and sp == sp.lower() for sp in spellings.split(",")), entry
        for spelling in spellings.split(","):
            whose.setdefault(spelling, []).append(name)
    assert {k: v for k, v in whose.items() if len(v) > 1} == {} and len(whose) > 500
    made, one = name_alphabets._names()
    assert len(made) > 350
    for spelling, name in one.items():
        assert [n for n, its in made.items() if spelling in its] == [name], spelling


@pytest.mark.parametrize("planted", ["علي:eli", "سمر:samer", "أنس:enas", "كمال:kamel", "هناء:hana", "سعيد:said"])
def test_control_a_rivals_spelling_planted_in_the_list_is_found(monkeypatch, planted):
    monkeypatch.setattr(name_alphabets, "_NAMES", [*name_alphabets._NAMES, planted])
    name_alphabets._names.cache_clear()
    try:
        assert planted.partition(":")[2] in {spelling for _rival, spelling in _joined_rivals()}
    finally:
        monkeypatch.undo()
        name_alphabets._names.cache_clear()


def test_every_arabic_name_of_the_list_is_written_once():
    from kazma_core.documents.arabic import fold_for_search

    folded = [fold_for_search(e.partition(":")[0]) for e in name_alphabets._NAMES]
    assert [n for n in folded if folded.count(n) > 1] == []


# An English word that is a spelling.

def _english_words() -> set[str]:
    """The words of Kazma's own English documents, in small letters."""
    import re
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    files = subprocess.run(["git", "ls-files", "docs/docs/*.md", "*.md"], cwd=root, capture_output=True, text=True,
                           check=True).stdout.split()
    out: set[str] = set()
    for name in files:
        text = (root / name).read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"```.*?```|`[^`]*`|\*\*[^*]*\*\*", " ", text, flags=re.S)
        out |= set(re.findall(r"(?<![\w./_:@-])[a-z]{2,}(?![\w/_:@-])", text))
    return out


def test_a_spelling_that_is_an_english_word_is_declared():
    one = name_alphabets._names()[1]
    # Words of these documents that are names there too (an example's "layla", "omar", "sara").
    names_in_examples = {"layla", "omar", "sara"}
    assert (_english_words() & set(one)) - names_in_examples <= name_alphabets._ENGLISH_WORDS
    assert name_alphabets._ENGLISH_WORDS <= set(one)
    # Control: the words the rules had made names of are found by it.
    assert {"said", "nor", "ward"} & _english_words() >= {"said", "nor"}
    assert not {"said", "sad", "nor", "wed", "ward", "rim", "raid"} & set(one)


@pytest.mark.parametrize(("text", "named"), [
    ("I met Sultan and Omar.", ["سلطان", "عمر"]), ("The sultan of Oman met omar.", ["عمر"]), ("Sultan Ahmed street", ["احمد"]),
    ("he said it was sad, nor did we", []), ("it is nada", []), ("ask Iman about it", ["ايمان"]), ("the iman he has", [])])
def test_an_english_word_names_someone_only_where_it_is_written_as_a_name(text, named):
    assert sorted(name_alphabets.arabic_names_in(text)) == sorted(named)


# ── the write ────────────────────────────────────────────────────────────


@pytest.fixture()
def conn(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    yield c
    c.close()


def _hold(conn, subject, name, value, at=1.0, method="llm_inferred"):
    return mutate_belief(conn, subject, name, value, predicate_type="functional", extraction_method=method, now=at,
                         private=True, tenant_id="default")["belief_id"]


def _reply(conn, said, text, *facts):
    """A reply's *facts* (subject, name, value, the held subject it replaces)."""
    held = held_facts.held_facts(conn, tenant_id="default", text=text)
    ref = {(h["subject"], h["predicate"]): h["ref"] for h in held}
    beliefs = [{"subject": s, "predicate": p, "predicate_type": "functional", "object": v, "confidence": 0.9,
                **({"replaces": ref[replaced]} if replaced else {})} for s, p, v, replaced in facts]
    belief_extractor._apply_beliefs_to_v2(beliefs, conn, None, session_id="now", turn=1, tenant_id="default", now=9.0,
                                          private=True, user_text=text, held=held)


def _current(conn, *, user=False):
    return sorted((r[0], r[1], r[2]) for r in conn.execute(
        "SELECT subject, predicate, object FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL")
        if user or r[0] != "user")


#: (told, the name held, the name as said, the turn, the new value): each
#: language's turn about a person held in the other alphabet.
CROSSED = [
    ("gulf", "omar", "عمر", "عمر نقل الدوحة الأسبوع اللي طاف", "الدوحة"),
    ("msa", "omar", "عمر", "انتقل عمر إلى الدوحة الأسبوع الماضي.", "الدوحة"),
    ("english", "عمر", "omar", "Omar moved to Doha last week.", "Doha"),
    ("english, as a name is written", "عمر", "Omar", "Omar moved to Doha last week.", "Doha"),
]


@pytest.mark.parametrize(("_dialect", "held", "said", "turn", "new"), CROSSED)
def test_a_reply_in_the_users_script_replaces_the_fact_held_in_the_other(conn, _dialect, held, said, turn, new):
    _hold(conn, held, "lives_in", "Muscat")
    _reply(conn, said, turn, (said, "moved_home_to", new, (held, "lives_in")))
    assert _current(conn) == [(held, "lives_in", new)]
    # The old home is history, not gone.
    assert conn.execute("SELECT object FROM beliefs WHERE valid_until IS NOT NULL").fetchall()[0][0] == "Muscat"


@pytest.mark.parametrize(("_dialect", "held", "said", "turn", "new"), CROSSED)
def test_the_replys_other_facts_of_that_name_are_the_same_persons(conn, _dialect, held, said, turn, new):
    _hold(conn, held, "lives_in", "Muscat")
    _reply(conn, said, turn, (said, "drives", "Tesla", None), (said, "moved_home_to", new, (held, "lives_in")))
    assert _current(conn) == [(held, "drives", "Tesla"), (held, "lives_in", new)]


@pytest.mark.parametrize(("_dialect", "held", "said", "turn", "new"), CROSSED)
def test_control_without_the_match_both_stay(conn, monkeypatch, _dialect, held, said, turn, new):
    monkeypatch.setattr(name_alphabets, "same_name", lambda one, other: False)
    monkeypatch.setattr(name_alphabets, "in_arabic", lambda subject: None)
    _hold(conn, held, "lives_in", "Muscat")
    shown = held_facts.held_facts
    # What names the person in the list is taken away too: the fact is handed over.
    monkeypatch.setattr(held_facts, "held_facts", lambda c, **kw: shown(c, **{**kw, "text": f"{kw['text']} {held}"}))
    _reply(conn, said, turn, (said, "moved_home_to", new, (held, "lives_in")))
    assert len(_current(conn)) == 2 and (held, "lives_in", "Muscat") in _current(conn)


def test_a_name_in_another_case_is_the_same_person(conn):
    _hold(conn, "noura", "works_as", "dentist")
    _reply(conn, "Noura", "My sister Noura is a surgeon now.", ("Noura", "profession", "surgeon", ("noura", "works_as")))
    assert _current(conn) == [("noura", "works_as", "surgeon")]


# Never two people: in each the fact is written as it came, and both stay.

def test_somebody_held_under_the_name_as_said_is_himself(conn):
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, "عمر", "works_as", "طبيب", at=2.0)
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("omar", "lives_in")))
    assert _current(conn) == [("omar", "lives_in", "Muscat"), ("عمر", "lives_in", "الدوحة"), ("عمر", "works_as", "طبيب")]


@pytest.mark.parametrize("rival", ["umar", "omer", "omaar"])
def test_two_who_could_be_meant_neither_is(conn, rival):
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, rival, "lives_in", "Sur", at=2.0)
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("omar", "lives_in")))
    assert ("omar", "lives_in", "Muscat") in _current(conn) and (rival, "lives_in", "Sur") in _current(conn)


def test_a_known_other_name_is_no_rival(conn):
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, "ammar", "lives_in", "Sur", at=2.0)
    _hold(conn, "amr", "lives_in", "Nizwa", at=3.0)
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("omar", "lives_in")))
    assert _current(conn) == [("ammar", "lives_in", "Sur"), ("amr", "lives_in", "Nizwa"), ("omar", "lives_in", "الدوحة")]


@pytest.mark.parametrize(("held", "said"), [("amr", "عمر"), ("hassan", "حسن"), ("noor", "نورة"), ("omar_otaibi", "عمر"),
                                            ("falcon_project", "مشروع فالكون")])
def test_another_name_or_an_unknown_one_stays_apart(conn, held, said):
    _hold(conn, held, "lives_in", "Muscat")
    shown = held_facts.held_facts(conn, tenant_id="default", text=held)
    belief_extractor._apply_beliefs_to_v2(
        [{"subject": said, "predicate": "lives_in", "predicate_type": "functional", "object": "الدوحة",
          "confidence": 0.9, "replaces": shown[0]["ref"]}], conn, None, session_id="now", turn=1, tenant_id="default",
        now=9.0, private=True, user_text="نقل الدوحة", held=shown)
    assert len(_current(conn)) == 2


def test_a_fact_with_no_replaces_is_never_filed_under_the_other_alphabet(conn):
    _hold(conn, "omar", "lives_in", "Muscat")
    _reply(conn, "عمر", "عمر يحب القهوة", ("عمر", "likes", "القهوة", None))
    assert ("عمر", "likes", "القهوة") in _current(conn)


@pytest.mark.parametrize(("held", "said", "turn"), [
    ("samer", "سمر", "سمر نقلت دبي"), ("eli", "علي", "علي نقل دبي"), ("eiman", "أيمن", "أيمن نقل دبي"), ("enas", "أنس", "انتقل أنس إلى دبي"),
    ("khaled_alrashid", "خالد الراشد", "خالد الراشد نقل دبي"), ("majid", "ماجد", "انتقل ماجد إلى دبي"), ("سمر", "Samer", "Samer moved to Dubai"),
    ("kamel", "كمال", "كمال نقل دبي"), ("hana", "هناء", "هناء نقلت دبي"), ("هلا", "Hala", "Hala moved to Dubai")])
def test_two_people_of_near_names_are_never_one(conn, held, said, turn):
    """Through the whole write: the old home of one and the new one of the other both stay."""
    _hold(conn, held, "lives_in", "Boston")
    shown = held_facts.held_facts(conn, tenant_id="default", text=f"{turn} {held}")
    belief_extractor._apply_beliefs_to_v2(
        [{"subject": said, "predicate": "lives_in", "predicate_type": "functional", "object": "دبي", "confidence": 0.9,
          "replaces": shown[0]["ref"]}], conn, None, session_id="now", turn=1, tenant_id="default", now=9.0, private=True,
        user_text=turn, held=shown)
    assert (held, "lives_in", "Boston") in _current(conn) and len(_current(conn)) == 2


@pytest.mark.parametrize("other", ["نوره", "نُورة"])
def test_her_own_name_spelled_otherwise_is_a_rival(conn, other):
    """"نورة" is somebody already, as "نوره": she is not ``noura`` (the review, L6)."""
    _hold(conn, "noura", "lives_in", "Boston")
    _hold(conn, other, "works_as", "طبيبة", at=2.0)
    _reply(conn, "نورة", "نورة نقلت دبي", ("نورة", "lives_in", "دبي", ("noura", "lives_in")))
    assert ("noura", "lives_in", "Boston") in _current(conn)


# ── members and forgetting ───────────────────────────────────────────────

def _room(monkeypatch):
    from types import MappingProxyType

    from kazma_core.memory.speakers import Member, Room

    room = Room(space="shared", shared=True, members=MappingProxyType({
        "sara": Member(account="sara", name="Sara", called=("سارة",)),
        "omar": Member(account="omar", name="Omar", called=("عمر",))}))
    monkeypatch.setattr("kazma_core.memory.speakers.room", lambda _tenant: room)
    return room


def _said_in_the_room(conn, text, said_by, *facts, at=9.0):
    """A shared space's reply: (subject, name, value, held subject it replaces)."""
    held = held_facts.held_facts(conn, tenant_id="shared", text=text, said_by=said_by)
    ref = {(h["subject"], h["predicate"]): h["ref"] for h in held}
    belief_extractor._apply_beliefs_to_v2(
        [{"subject": s, "predicate": p, "predicate_type": "functional", "object": v, "confidence": 0.9,
          **({"replaces": ref[rep]} if rep in ref else {})} for s, p, v, rep in facts], conn, None, session_id="now",
        turn=int(at), tenant_id="shared", now=at, private=True, user_text=text, held=held, said_by=said_by)


def _in_the_room(conn):
    return sorted((r[0], r[1], r[2]) for r in conn.execute(
        "SELECT subject, predicate, object FROM beliefs WHERE tenant_id = 'shared' AND valid_until IS NULL"))


@pytest.mark.parametrize(("text", "said", "new"), [("عمر نقل الدوحة", "عمر", "الدوحة"), ("انتقل عمر إلى الدوحة", "عمر", "الدوحة"),
                                                   ("Omar moved to Doha", "Omar", "Doha")])
def test_a_members_own_name_is_the_members(conn, monkeypatch, text, said, new):
    """``umar`` is somebody else: what Sara says of the member Omar is Omar's (N30)."""
    _room(monkeypatch)
    mutate_belief(conn, "umar", "lives_in", "Muscat", predicate_type="functional", extraction_method="llm_inferred", now=1.0,
                  private=True, tenant_id="shared", said_by="sara")
    _said_in_the_room(conn, text, "sara", (said, "lives_in", new, ("umar", "lives_in")))
    assert _in_the_room(conn) == [("member_omar", "lives_in", new), ("umar", "lives_in", "Muscat")]


@pytest.mark.parametrize("sister", ["sister", "my_sister", "أختي"])
@pytest.mark.parametrize("names", [("occupation", "job_title"), ("profession", "has_occupation"), ("employer", "current_employer")])
def test_two_members_sisters_are_two_people(conn, monkeypatch, sister, names):
    _room(monkeypatch)
    _said_in_the_room(conn, "My sister is a dentist", "sara", (sister, names[0], "dentist", None), at=1.0)
    _said_in_the_room(conn, "My sister is a pilot", "omar", (sister, names[1], "pilot", None), at=2.0)
    assert {v for _s, _p, v in _in_the_room(conn)} == {"dentist", "pilot"}
    # Control: one member's two words of their own sister are one fact.
    _said_in_the_room(conn, "My sister is a surgeon now", "sara", (sister, names[1] + "_is", "surgeon", None), at=3.0)
    assert "surgeon" in {v for _s, _p, v in _in_the_room(conn)}


@pytest.mark.parametrize("turn_kept", [True, False])
@pytest.mark.parametrize(("filed", "asked", "turn"), [
    ("khaled", "خالد", "خالد نقل الدوحة ويشتغل في بنك قطر"), ("khaled", "خالد", "انتقل خالد إلى الدوحة ويعمل في بنك قطر"),
    ("خالد", "Khaled", "Khaled moved to Doha and works at Qatar Bank"), ("خالد_العتيبي", "Khaled Al-Otaibi", "Khaled Al-Otaibi moved"),
    ("khaled_alotaibi", "خالد", "خالد العتيبي نقل")])
def test_forgetting_a_name_forgets_who_is_filed_under_the_other_spelling(tmp_path, monkeypatch, turn_kept, filed, asked, turn):
    """The review's M8: told of خالد, filed under ``khaled``; with the turn gone
    "forget everything that mentions خالد" found nothing."""
    from kazma_core.memory import forget
    from kazma_core.memory.mention_match import Needle

    c = sqlite3.connect(":memory:")
    ensure_primary_schema(c)
    for name, value in (("lives_in", "الدوحة"), ("works_at", "بنك قطر")):
        c.execute("INSERT INTO beliefs (id, tenant_id, subject, predicate, object, predicate_type, valid_from, ingested_at) "
                  "VALUES (?, 'default', ?, ?, ?, 'functional', 1, 1)", (f"b{name}", filed, name, value))
    c.execute("INSERT INTO beliefs (id, tenant_id, subject, predicate, object, predicate_type, valid_from, ingested_at) "
              "VALUES ('other', 'default', 'sara', 'lives_in', 'Muscat', 'functional', 1, 1)")
    if turn_kept:
        c.execute("INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, created_at) "
                  "VALUES ('e1', 'default', 's', 1, ?, '', 'raw', 1)", (turn,))
    c.commit()
    found = forget._mentions_found(c, Needle.of(asked), tenant="default")
    assert sorted(found["facts"]) == ["blives_in", "bworks_at"]
    c.close()


@pytest.mark.parametrize(("needle", "subject"), [
    ("خالد", "khalida"), ("Omar", "عمار"), ("Samer", "سمر"), ("علي", "eli"), ("خالد", "user"), ("Khaled", "خالدية"), ("حسن", "hassan")])
def test_forgetting_a_name_never_takes_another_person(needle, subject):
    from kazma_core.memory.mention_match import Needle

    assert not Needle.of(needle).in_name(subject)


def test_control_an_id_is_matched_as_it_is_written_without_the_names(monkeypatch):
    from kazma_core.memory import mention_match

    mention_match._holds_the_name.cache_clear()
    monkeypatch.setattr(name_alphabets, "same_name", lambda one, other: False)
    assert not mention_match.Needle.of("خالد").in_name("khaled")
    mention_match._holds_the_name.cache_clear()


def test_the_user_is_nobody_elses_spelling(conn):
    _hold(conn, "user", "lives_in", "Muscat")
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("user", "lives_in")))
    assert ("user", "lives_in", "Muscat") in _current(conn, user=True)


def test_control_each_guard_is_needed(conn, monkeypatch):
    monkeypatch.setattr(name_alphabets, "may_be", lambda said, subject: False)
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, "omaar", "lives_in", "Sur", at=2.0)
    _reply(conn, "عمر", "عمر نقل الدوحة", ("عمر", "lives_in", "الدوحة", ("omar", "lives_in")))
    assert ("omar", "lives_in", "الدوحة") in _current(conn), "the control: without it, one of two is taken"


def test_a_rival_is_a_name_of_the_same_consonants_that_is_nobodys():
    assert may_be("عمر", "umar") and may_be("عمر", "omaar") and may_be("حمود", "hamoud")
    assert not may_be("عمر", "ammar") and not may_be("عمر", "sara") and not may_be("عمر", "عمر")


# ── what the deep pass is shown ──────────────────────────────────────────

@pytest.mark.parametrize(("subject", "text", "named"), [
    ("omar", "عمر نقل الدوحة", True), ("omar", "كلمت عمر أمس", True), ("omar", "وعمر بعد", True),
    ("عمر", "Omar moved to Doha", True), ("عمر_الحربي", "Omar Al-Harbi called", True), ("omar_alharbi", "عمر الحربي اتصل", True),
    ("bu_fahad", "بو فهد رجع", True), ("ali", "كلمت علي أمس", True),
    ("omar", "عمار نقل", False), ("omar", "عمرو نقل", False), ("ali", "الكتاب على الطاولة", False),
    ("عمر", "Amr moved", False), ("عمر_الحربي", "Omar called", False), ("acme", "شنو أطبخ اليوم؟", False)])
def test_a_turn_names_who_is_held_in_the_other_alphabet(subject, text, named):
    assert bool(held_facts._Said(text).names(subject, person=True)) is named


def test_a_friend_held_in_the_other_alphabet_is_shown_with_no_embedder(conn):
    """Found by name, not near in meaning: among 300 newer facts."""
    _hold(conn, "omar", "lives_in", "Muscat")
    _hold(conn, "عمر_العتيبي", "lives_in", "الكويت", at=2.0)
    for n in range(300):
        mutate_belief(conn, "user", "visited", f"city {n}", predicate_type="set", extraction_method="llm_inferred",
                      now=10.0 + n, private=True, tenant_id="default")
    shown = {h["subject"] for h in held_facts.held_facts(conn, tenant_id="default", text="عمر نقل الدوحة")}
    assert "omar" in shown and "عمر_العتيبي" not in shown
