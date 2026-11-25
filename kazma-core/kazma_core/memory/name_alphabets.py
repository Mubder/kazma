"""One person's name in two alphabets: "عمر" and ``omar`` (the memory plan's W6).

A person is filed under the name the user wrote, in the script they wrote it
in. A user of two languages writes it in both, and a fact held of ``omar`` was
not replaced by "عمر نقل الدوحة": the old home and the new one both stayed.

**It never joins two people.** An Arabic name says no short vowels and a Latin
spelling says several Arabic letters alike, so by its letters alone "حسن" is
as near to "hassan" as "حسان" is. So the match is not made of letters: it is a
list of names (:data:`_NAMES`), each written with its vowels, from which the
Latin spellings people use are made by rule (:func:`_spellings_of`) -- and a
spelling that two names make is nobody's (:func:`_names`): "hassan" (حسن,
حسان), "amal" (أمل, آمال), "amir" (عامر, أمير) match no one. A name that is not
in the list matches nothing. To teach Kazma a name is to add one word; a rival
that would be confused with it is added beside it.

A subject's every word must match ("عمر الحربي" is not ``omar`` and not
``omar_alotaibi``). Two people of one name are one subject in one alphabet
already; across the two, the caller asks whether another subject could be the
one meant (``belief_extractor._take_replaces``).
"""

from __future__ import annotations

import functools
import itertools
import re
import unicodedata

__all__ = ["arabic_names_in", "in_arabic", "may_be", "same_name"]

# The names, with their vowels; then the spellings the rule does not make.
# ":" separates them. A name is one that Arabic writes one way only: "ملك"
# (Malak, a king) and the like are left out.
_NAMES = """
عُمَر:omer عَمْرو:amr,amro عَمَّار عَامِر عُمَيْر عِمَاد عِمْرَان أَمِير أَمِيرَة أَمِين أَمِينَة آمِنَة أَمَل آمَال أَمَانِي إِيمَان أَيْمَن
أَحْمَد:ahmad,ahmed مُحَمَّد:mohammed,mohammad,muhammad,mohamed,muhammed,mohamad,mohd مَحْمُود:mahmoud,mahmood حَمَد:hamad حَامِد حَمْدَان
حُمُود حِمُودِي حَمِيد مُصْطَفَى:mustafa,mostafa مُرْتَضَى عَلِي:aly عَلِيَّة عَلْيَاء:alia,alya عَلَاء حَسَن حَسَّان حُسَيْن:hussain,hussein,husain,husein
حُسَام حُسْنِي مُحْسِن إِحْسَان خَالِد:khaled,khalid خَالِدَة خُلُود خَلِيل خَلِيفَة خَلَف خَوْلَة سَالِم:salem سَلِيم سَلْمَان سُلَيْمَان:sulaiman,suleiman,soliman
سَلْمَى سَلَامَة سَلَام سُلْطَان سَامِي سَامِيَة سَمِير سَمِيرَة سَمَر سَمِيحَة سَمَاح سَارَة:sarah سَعْد:saad سَعِيد:saeed,said سَعُود:saud,saoud سُعَاد
سَعَادَة مُسَاعِد نَاصِر:nasser,naser نَصْر نَصِير مَنْصُور:mansour,mansoor,mansur فَهْد:fahad,fahd فَهَد فَيْصَل:faisal,faysal,feisal فَوَّاز فَوْزِي فَوْزِيَّة
فَاطِمَة:fatma,fatimah,fatemah فَطِيم فَرَح فَرِيد فَرِيدَة فَجْر فَارِس فِرَاس فِهِيد عَائِشَة:aisha,aysha,ayesha,aishah مَرْيَم:mariam,maryam,mariyam نُورَة:nourah,norah
نُور:nour نُورِي نُوف:nouf نَوَّاف نَوَال نَايِف:nayef,naif نَادِيَة نَادِر نَادِين نَدَى نَجْلَاء نَجْوَى نَبِيل نَبِيلَة نَسِيم نَسْرِين هُدَى هِنْد:hind,hend
هَيَا هَيْفَاء هَانِي هَنَاء هَنَادِي هَالَة حَلَا هِبَة هَاشِم هِشَام:hisham,hesham هُنُوف هَيْثَم هَادِي هَارُون إِبْرَاهِيم:ibrahim,ebrahim,ibraheem
إِسْمَاعِيل:ismail,ismael,esmail إِسْحَاق أَسْمَاء:asma,asmaa أُسَامَة:osama,usama إِيَاد:eyad,iyad أَنَس أَنْوَر أَنْوَار أَرْوَى أَسِيل أَسْرَار إِسْرَاء:israa,esraa
آلَاء:alaa أَفْنَان أَشْوَاق أَوْس أُوَيْس أَيُّوب:ayoub,ayub أَبْرَار أَحْلَام أَشْرَف أَكْرَم أَدْهَم أَسْعَد أَسَد بَدْر:bader,badr بُدُور بَسَّام بَاسِم بَاسِل بَسْمَة بَشِير
بِشْر بُشْرَى بِلَال:bilal,belal بَنْدَر بَرَّاك بَرَكَات بَيَان تُرْكِي:turki,torki تَمِيم تَامِر ثَامِر:thamer ثُرَيَّا ثَنَاء جَمَال:jamal,gamal جَمِيل جَمِيلَة جَابِر:jaber
جَاسِم:jassim,jasem,jassem جَعْفَر:jaafar,jafar جَوَاد جُود جُورِي جَوَاهِر جَنَى جَلَال جَهَاد جِهَاد حَاتِم:hatem حَازِم:hazem حَبِيب حَسْنَاء حَفْصَة حَكِيم حَلِيمَة
حَمْزَة:hamza,hamzah حَنَان حَنِين حَيْدَر:haider,haidar,hayder حِصَّة دَانَة دَانَا دَلَال:dalal دُعَاء:duaa,doaa دَاوُد:dawood,dawoud,daoud دِينَا ذِكْرَى رَاشِد:rashed,rashid رَشِيد
رَشَا رَاكَان رَائِد:raed,raid رَامِي رَانِيَا:rania رَنَا رَنْد رَهَف رَوَان:rawan رَغَد رَبَاب رَبِيع رِيم:reem,rim رِيمَا رِيمَاس رَزَان رَحْمَة رَحِيم رِضَا:reda,redha
رُقَيَّة رُبَى زَيْن:zain,zein زَيْد:zaid,zayd,zayed زَايِد:zayed زَيْنَب:zainab,zaynab,zeinab زَيْنَة زَكَرِيَّا:zakaria,zakariya زُهَيْر زَهْرَاء:zahra,zahraa زَهْرَة زِيَاد:ziad,ziyad
سَيْف:saif,sayf سُهَا سُهَى سُهَيْل سُعْدَى سُمَيَّة:sumaya,sumayya,somaya شَادِي شَاهِين شَهَد:shahad شَيْخَة:sheikha,shaikha شَيْمَاء:shaimaa,shaima شَوْق:shouq
شَرِيف:sherif,sharif شَرِيفَة شَمْسَة شِهَاب صَالِح:saleh,salih صَلَاح صَبَاح صَقْر:saqer,saqr صَفَاء صَفِيَّة ضَاري:dhari ضُحَى طَارِق:tariq,tareq,tarek طَلَال:talal طَاهِر
طَيْف طَه:taha ظَافِر عَادِل:adel,adil عَدْنَان عَاصِم:asim,assem عَاطِف عَبَّاس عَبِير:abeer عَبْلَة عَبَادَة عُبَيْد عُثْمَان:othman,uthman,osman عَزِيز عَزِيزَة
عَزَّام عِزَّة عَفَاف عَقِيل عَهُود:ohoud عُهُود:ohoud عَوَاطِف عِيسَى:essa,issa,eissa عِنَاد غَادَة:ghada غَازِي:ghazi غَانِم:ghanem غَدِير غَلَا غَيْدَاء
قَاسِم:qasim,qassim,kassem قُتَيْبَة كَرِيم:kareem,karim كَرِيمَة كَمَال كَوْثَر:kawthar لُؤْلُؤَة لَوْلَوَة:lulwa,loulwa,lolwa لَيْلَى:laila,layla,leila لَيْث لَيَان
لَمْيَاء:lamia,lamya لُبْنَى لِينَا لِينَة لُجَيْن:lujain,lojain لَطِيفَة:latifa,lateefa لَطِيف مَاجِد:majed,majid مَاجِدَة مَجْدِي مُنِير مُنِيرَة:munira,moneera مُنَى
مَنَال:manal مَنَاهِل مِشَارِي:mishari,meshari مِشْعَل:mishal,meshal,mishaal مُشَارِي مُبَارَك:mubarak,mobarak مُرَاد مَرْوَان مَرْوَة مَرَام مَهَا مَهْدِي مُوسَى:musa,mousa,moosa
مُنْذِر مُعَاذ:muath,muaath مُعْتَز مُؤْمِن مَيْسَاء مَيْسَم مِشَاعِل مَشَاعِل:mashael مَلَاك مَالِك:malik,malek نَعِيم نِعْمَة نِضَال وَلِيد:waleed,walid وَائِل:wael
وَفَاء وَضْحَة وَسَام وِسَام وَسَن وَعْد وَرْد وَرْدَة يَاسِر:yasser,yaser,yasir يَاسِين:yaseen,yassin يَاسَمِين:yasmin,yasmine,yasmeen يَحْيَى:yahya,yehia
يَزِيد:yazeed,yazid يَزَن يَعْقُوب:yaqoub,yacoub,yaqub يُوسُف:yousef,youssef,yusuf,yusef يُونُس:younis,younes,yunus
""".split()

#: The names after "ال" that a family carries: "الحربي" is ``alharbi``.
_FAMILIES = """
حَرْبِي عُتَيْبِي:otaibi,otaiby,utaibi مُطَيْرِي:mutairi,mutairy عَجْمِي:ajmi رَشِيدِي:rashidi,rasheedi,rashedi عَنْزِي:enezi,anazi,enazi,anzi شَمَّرِي:shammari,shamari
دَوْسَرِي:dosari,dossary,dosary,dousari قَحْطَانِي:qahtani غَامِدِي:ghamdi زَهْرَانِي:zahrani هَاجِرِي:hajri,hajeri عَازِمِي:azmi,azemi,azimi ظَفِيرِي:dhafiri,dhafeeri كَنْدَرِي:kandari
صَبَاح صَالِح رَاشِد غَانِم خَالِد فَهْد شَايِع:shaya مَنْصُور نَاصِر سَالِم مُبَارَك:mubarak خَلِيفَة هَاشِمِي هَاشِم سَيِّد:sayed,sayyid فَرَج نَجَّار:najjar حَدَّاد:haddad خَطِيب:khatib,khateeb
مَالِكِي:maliki جُهَنِي:juhani,johani بَلُوشِي:balushi,baloushi حَمْد مَرِّي:marri زَعَابِي:zaabi كُوَيْتِي:kuwaiti مَنَاعِي:mannai سُوَيْدِي:suwaidi,suwaidy
""".split()

#: The names "عبد ال" is written before.
_SERVANT_OF = """
رَحْمَن:rahman رَحِيم عَزِيز كَرِيم لَطِيف هَادِي رَزَّاق:razzaq مُحْسِن حَمِيد قَادِر:qader مَلِك وَهَّاب:wahab جَلِيل غَفُور:ghafour غَنِي سَلَام مَجِيد:majeed وَاحِد:wahed بَاسِط:baset نَاصِر جَبَّار فَتَّاح:fattah لَه:allah
""".split()

#: Names from other languages, as Arabic writes them.
_FOREIGN: dict[str, tuple[str, ...]] = {
    "مايكل": ("michael",), "ديفيد": ("david",), "دانيال": ("daniel",), "ماريا": ("maria",),
    "توماس": ("thomas",), "بيتر": ("peter",), "جورج": ("george",), "روبرت": ("robert",), "جيمس": ("james",),
    "كارلوس": ("carlos",), "لوكاس": ("lucas", "lukas"), "أوليفيا": ("olivia",), "ليام": ("liam",),
    "مارتن": ("martin",), "كيفن": ("kevin",), "بريا": ("priya",), "راجيش": ("rajesh",), "إيما": ("emma",),
    "آدم": ("adam",), "إلياس": ("elias", "ilyas"), "سوزان": ("susan", "suzan"), "نانسي": ("nancy",),
    "كريستينا": ("christina", "cristina"), "فيليب": ("philip", "phillip"), "ألكسندر": ("alexander",),
    "جوزيف": ("joseph",), "أندرو": ("andrew",), "باتريك": ("patrick",), "راشيل": ("rachel",),
    "جيسيكا": ("jessica",), "جينيفر": ("jennifer",), "فيكتور": ("victor", "viktor"),
}

# ── From a name with its vowels to the spellings people use ──────────────

_FATHA, _DAMMA, _KASRA, _SHADDA, _SUKUN = "َ", "ُ", "ِ", "ّ", "ْ"
_SHORT = {_FATHA: ("a", "e"), _DAMMA: ("u", "o"), _KASRA: ("i", "e")}
_LONG = {(_FATHA, "ا"): ("a", "aa"), (_FATHA, "ى"): ("a",), (_FATHA, "ة"): ("a", "ah"), (_DAMMA, "و"): ("u", "oo", "ou", "o"),
         (_KASRA, "ي"): ("i", "ee")}
_TWO = {"و": ("aw", "au", "ow"), "ي": ("ay", "ai", "ei", "ey")}
_LETTERS = {
    "ب": ("b",), "ت": ("t",), "ث": ("th",), "ج": ("j",), "ح": ("h",), "خ": ("kh",), "د": ("d",), "ذ": ("th", "dh"),
    "ر": ("r",), "ز": ("z",), "س": ("s",), "ش": ("sh",), "ص": ("s",), "ض": ("d", "dh"), "ط": ("t",),
    "ظ": ("z", "dh"), "ع": ("",), "غ": ("gh",), "ف": ("f",), "ق": ("q",), "ك": ("k",), "ل": ("l",), "م": ("m",),
    "ن": ("n",), "ه": ("h",), "و": ("w",), "ي": ("y",), "ء": ("",), "أ": ("",), "إ": ("",), "ؤ": ("",), "ئ": ("", "y"),
}


def _spellings_of(name: str) -> set[str]:
    """The Latin spellings of *name*, an Arabic name with its vowels."""
    parts: list[tuple[str, ...]] = []
    at, last = 0, len(name) - 1
    while at <= last:
        letter, at = name[at], at + 1
        marks = ""
        while at <= last and name[at] in (_FATHA, _DAMMA, _KASRA, _SHADDA, _SUKUN):
            marks, at = marks + name[at], at + 1
        if letter == "آ":
            parts.append(("a", "aa"))
            continue
        if letter in "اىة":  # a long "a" whose short vowel was not written before it
            parts.append(("a", "ah") if letter == "ة" else ("a",))
            continue
        sounds = _LETTERS[letter]
        if _SHADDA in marks:
            sounds = (*sounds, *(s * 2 for s in sounds if len(s) == 1))
        elif letter in "سص" and parts and parts[-1][0][-1:] in "aeiou" and at <= last:
            sounds = (*sounds, "ss")  # "Hassan", "Youssef", "Nasser"
        parts.append(sounds)
        short = next((m for m in marks if m in _SHORT), "")
        following = name[at] if at <= last else ""
        after = name[at + 1] if at < last else ""
        # A letter that only lengthens the vowel before it has no mark.
        alone = following and after not in (_FATHA, _DAMMA, _KASRA, _SHADDA, _SUKUN)
        if short and alone and (short, following) in _LONG:
            parts.append(_LONG[short, following])
            at += 1
        elif short == _FATHA and following in _TWO and after == _SUKUN:
            parts.append(_TWO[following])
            at += 2
        elif short:
            parts.append(_SHORT[short])
    return {"".join(p) for p in itertools.product(*parts)} - {""}


def _read(entry: str) -> tuple[str, set[str]]:
    """``(the name as memory folds it, its Latin spellings)`` of one entry."""
    from kazma_core.documents.arabic import fold_for_search

    name, _, more = entry.partition(":")
    return fold_for_search(name), _spellings_of(name) | {s for s in more.split(",") if s}


@functools.lru_cache(maxsize=1)
def _names() -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """``(Arabic name -> its Latin spellings, Latin spelling -> the one Arabic
    name it is)``: a spelling two names make is in neither."""
    from kazma_core.documents.arabic import fold_for_search

    made: dict[str, set[str]] = {}
    for entry in _NAMES:
        name, spellings = _read(entry)
        made.setdefault(name, set()).update(spellings)
    for name, spellings in _FOREIGN.items():
        made.setdefault(fold_for_search(name), set()).update(spellings)
    for entry in _FAMILIES:
        name, spellings = _read(entry)
        made.setdefault("ال" + name, set()).update(f"{the}{s}" for the in ("al", "el") for s in spellings)
    for entry in _SERVANT_OF:
        name, spellings = _read(entry)
        god = name == "له"
        made.setdefault("عبد" + ("الله" if god else "ال" + name), set()).update(
            {"abdullah", "abdallah", "abdulla", "abdalla"} if god
            else {f"abd{the}{s}" for the in ("ul", "el", "al", "ol", "ur", "u") for s in spellings})
    whose: dict[str, set[str]] = {}
    for name, spellings in made.items():
        for spelling in spellings:
            whose.setdefault(spelling, set()).add(name)
    one = {spelling: next(iter(names)) for spelling, names in whose.items() if len(names) == 1}
    return {name: frozenset(s for s in spellings if s in one) for name, spellings in made.items()}, one


# ── A subject's words ────────────────────────────────────────────────────

_WORD = re.compile(r"[^\W_]+")
#: What is written before a name as a word of its own, or onto it.
_ARTICLES = ("al", "el")
_SERVANTS = ("abdul", "abdel", "abdal", "abdol", "abdur", "abd")
_FATHERS = {"abu": "ابو", "abo": "ابو", "bu": "ابو", "bo": "ابو", "um": "ام", "umm": "ام", "om": "ام"}


def _latin_words(subject: str) -> list[str]:
    """A Latin subject's words, with what is written before a name joined to
    it: "abdul_rahman", "al-harbi", "bu fahad"."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", subject.casefold()) if not unicodedata.combining(c))
    words = _WORD.findall(plain)
    out: list[str] = []
    held = ""
    for word in words:
        if word in _ARTICLES or (word in _SERVANTS and not held):
            held += word
            continue
        out.append(held + word)
        held = ""
    return [*out, held] if held else out


def _arabic_of(word: str) -> str | None:
    """The Arabic name the Latin *word* is a spelling of, if it is one's."""
    return _names()[1].get(word)


def in_arabic(subject: str) -> tuple[str, ...] | None:
    """The Latin *subject* in the folded Arabic words of its one reading
    (``omar_alharbi`` -> عمر, الحربي); None when a word of it is no known name
    of one person."""
    words = _latin_words(str(subject or ""))
    out: list[str] = []
    at = 0
    while at < len(words):
        word = words[at]
        father = _FATHERS.get(word)
        if father and at + 1 < len(words):
            name = _arabic_of(words[at + 1])
            read, at = (father + name if name else None), at + 1
        else:
            read = _arabic_of(word)
            if read is None:
                # Written onto it: "abufahad", "bufahad", "umahmad".
                read = next((arabic + name for latin, arabic in _FATHERS.items()
                             if word.startswith(latin) and (name := _arabic_of(word[len(latin):]))), None)
        if read is None:
            return None
        out.append(read)
        at += 1
    return tuple(out) if out else None


def _arabic_words(subject: str) -> tuple[str, ...]:
    from kazma_core.documents.arabic import fold_for_search
    from kazma_core.memory.held_facts import _whole_names

    out: list[str] = []
    words = _whole_names(_WORD.findall(fold_for_search(str(subject or "").replace("_", " "))))
    at = 0
    while at < len(words):
        if words[at] == "ام" and at + 1 < len(words):
            out.append("ام" + words[at + 1])
            at += 2
            continue
        out.append(words[at])
        at += 1
    return tuple(out)


def arabic_names_in(text: str) -> dict[str, list[int]]:
    """The names the Latin words of *text* are the one spelling of, in folded
    Arabic, and where they stand ("Omar met Sara Al-Harbi" names عمر, ساره and
    الحربي)."""
    found: dict[str, list[int]] = {}
    for at, word in enumerate(_latin_words(text)):
        name = _arabic_of(word)
        if name:
            found.setdefault(name, []).append(at)
    return found


def _is_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and all(c <= "ɏ" for c in letters)


def same_name(one: str, other: str) -> bool:
    """Whether *one* and *other* are one name, the one in Latin letters and the
    other in Arabic ones. Never for two names of one alphabet: there a name is
    compared as it is written."""
    first, second = str(one or ""), str(other or "")
    if _is_latin(second):
        first, second = second, first
    if not _is_latin(first) or _is_latin(second) or not second:
        return False
    read = in_arabic(first)
    return read is not None and read == _arabic_words(second)


def _frame(word: str) -> str:
    """The consonants of a Latin *word*, a doubled one once ("ammar", "amr")."""
    return re.sub(r"(.)\1+", r"\1", re.sub("[aeiouy]", "", word))


def may_be(said: str, subject: str) -> bool:
    """Whether *subject* could be another spelling of the name *said* in the
    other alphabet, though no known one: the same name, or -- of a Latin name
    that is no known name's -- one with the same consonants ("hamoud" beside
    "حمود"). Asked of the OTHER subjects a store holds before *said* is read as
    one of them: where two could be meant, neither is."""
    if same_name(said, subject):
        return True
    if _is_latin(said) or not _is_latin(subject) or in_arabic(subject) is not None:
        return False
    spoken, latin = _arabic_words(said), _latin_words(subject)
    if len(spoken) != len(latin):
        return False
    made = _names()[0]
    return all(_frame(word) in {_frame(s) for s in made.get(name, ())} for name, word in zip(spoken, latin, strict=True))
