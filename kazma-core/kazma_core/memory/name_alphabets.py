"""One person's name in two alphabets: "عمر" and ``omar`` (the memory plan's W6).

A person is filed under the name the user wrote, in the script they wrote it
in. A user of two languages writes it in both, and a fact held of ``omar`` was
not replaced by "عمر نقل الدوحة": the old home and the new one both stayed.

**It never joins two people.** An Arabic name says no short vowels and a Latin
spelling says several Arabic letters alike, so by its letters alone "حسن" is
as near to "hassan" as "حسان" is. So the match is not made of letters, and not
of rules: it is the pairs written below (:data:`_NAMES`), an Arabic name and the
Latin spellings that are its own. A spelling people use for two names is
nobody's and is not in it: "hassan" (حسن, حسان), "amal" (أمل, آمال), "samer"
(سامر, and by rule سمر), "eiman", "majid", "rashid" match no one. Rules made the
spellings once, and joined Eli with علي and Samer with سمر (the review of step
2, 2026-11-26): two people joined is worse than one person missed. A name that
is not in the list matches nothing. To teach Kazma a name is to add its pair --
and its rivals to ``tests/test_memory_name_alphabets.py``, which holds that
none of them is joined.

A subject's every word must match ("عمر الحربي" is not ``omar`` and not
``omar_alotaibi``). Two people of one name are one subject in one alphabet
already; across the two, the caller asks whether another subject could be the
one meant (``belief_extractor._take_replaces``).
"""

from __future__ import annotations

import functools
import re
import unicodedata

__all__ = ["arabic_names_in", "in_arabic", "is_a_name", "may_be", "same_name", "same_spelling"]

# A name as Arabic writes it (one way only: "ملك", Malak and a king, is left
# out), and the Latin spellings that are nobody else's.
_NAMES = """
عمر:omar,umar,omer عمرو:amr,amro عمار:ammar عامر:aamer عماد:imad,emad عمران:imran,omran أمير:ameer أميرة:amira,ameera
أمين:ameen آمنة:amna,aamna أماني:amani,amany إيمان:iman,eman أيمن:ayman أحمد:ahmad,ahmed
محمد:mohammed,mohammad,muhammad,mohamed,muhammed,mohamad,mohd محمود:mahmoud,mahmood,mahmud حمد:hamad حمدان:hamdan حميد:hameed
حمود:humoud,homoud مصطفى:mustafa,mostafa مرتضى:murtada,mortada علي:ali,aly حسين:hussain,hussein,husain,husein,hossein حسام:hussam,hossam,husam
محسن:mohsen,muhsin,mohsin إحسان:ihsan,ehsan خالد:khaled,khalid خالدة:khalida,khaleda خلود:khulood,khuloud,kholoud خليل:khalil,khaleel
خليفة:khalifa,khaleefa خلف:khalaf خولة:khawla,khaula سالم:salem سليم:saleem سلمان:salman سليمان:sulaiman,suleiman,soliman,sulayman
سلمى:salma سلطان:sultan سامي:sami سامية:samia سمير:sameer سميرة:samira,sameera سمر:samar سماح:samah سارة:sara,sarah سعد:saad
سعيد:saeed,sayeed سعود:saud,saoud سعاد:suad,souad,soad مساعد:musaed,mosaed ناصر:nasser,naser نصر:nasr منصور:mansour,mansoor,mansur
فهد:fahad,fahd فيصل:faisal,faysal,feisal فواز:fawaz,fawwaz فوزي:fawzi فوزية:fawzia,fawziya فاطمة:fatima,fatma,fatimah,fatemah,fatema
فرح:farah فريد:farid,fareed فريدة:farida,fareeda فجر:fajer فارس:faris,fares فراس:firas,feras عائشة:aisha,aysha,ayesha,aishah
مريم:mariam,maryam,mariyam نورة:noura,nourah نور:noor,nour نوري:nouri نوف:nouf نواف:nawaf نوال:nawal نايف:nayef نادية:nadia,nadiya
نادر:nader نادين:nadine ندى:nada نجلاء:najla نجوى:najwa نبيل:nabil,nabeel نبيلة:nabila,nabeela نسرين:nasreen,nasrin,nesreen هدى:huda,hoda
هند:hind,hend هيا:haya هيفاء:hayfa هاني:hani هنادي:hanadi هبة:heba,hiba هاشم:hashem,hashim هشام:hisham,hesham هنوف:hanouf هيثم:haitham,haytham
هادي:hadi هارون:haroon,haroun,harun إبراهيم:ibrahim,ebrahim,ibraheem إسماعيل:ismail,ismael,esmail,ismaeel إسحاق:ishaq أسماء:asma,asmaa
أسامة:osama,usama إياد:eyad,iyad أنس:anas أروى:arwa أسرار:asrar إسراء:israa,esraa أفنان:afnan أشواق:ashwaq أوس:aws أويس:owais,uwais
أيوب:ayoub,ayub,ayyub أبرار:abrar أحلام:ahlam أشرف:ashraf أكرم:akram أدهم:adham بدر:bader,badr بدور:budoor,bodour بسام:bassam
باسم:bassem,basem باسل:bassel بسمة:basma,basmah بشير:basheer,bashir بشرى:bushra بلال:bilal,belal بندر:bandar,bander براك:barrak
بركات:barakat بيان:bayan تركي:turki,torki تميم:tamim,tameem تامر:tamer ثامر:thamer ثريا:thuraya جمال:jamal,gamal جميل:jamil,jameel
جميلة:jamila,jameela جابر:jaber جاسم:jassim,jasem,jassem,jasim جعفر:jaafar,jafar جواد:jawad جوري:joori,jouri جواهر:jawaher جلال:jalal
جهاد:jihad,jehad حاتم:hatem حازم:hazem,hazim حبيب:habib,habeeb حفصة:hafsa,hafsah حكيم:hakeem حليمة:haleema,halima حمزة:hamza,hamzah
حنان:hanan حنين:haneen,hanin حيدر:haider,haidar,hayder حصة:hessa,hissa دانة:danah دلال:dalal دعاء:duaa,doaa داود:dawood,dawoud,daoud
دينا:dina,deena راشد:rashed رشيد:rasheed راكان:rakan رائد:raed رامي:rami رانيا:rania رنا:rana رهف:rahaf روان:rawan رغد:raghad
رباب:rabab ريم:reem ريما:reema ريماس:reemas رزان:razan رحمة:rahma,rahmah رضا:reda,redha رقية:ruqaya,ruqayya زين:zain,zein
زيد:zaid,zayd زينب:zainab,zaynab,zeinab زينة:zeina زكريا:zakaria,zakariya زهير:zuhair,zohair زياد:ziad,ziyad سيف:saif,sayf سهيل:suhail,sohail
سمية:sumaya,sumayya,somaya شادي:shadi شاهين:shaheen,shahin شهد:shahad شيخة:sheikha,shaikha شيماء:shaimaa,shaima شوق:shouq
شريف:sherif,sharif شريفة:sharifa,shareefa شهاب:shehab,shihab صالح:saleh,salih صلاح:salah صباح:sabah صقر:saqer,saqr صفاء:safaa
صفية:safiya,safia ضاري:dhari ضحى:duha طارق:tariq,tareq,tarek طلال:talal طاهر:taher,tahir طه:taha عادل:adel,adil عدنان:adnan
عاصم:asim,assem,asem عاطف:atef,atif عباس:abbas عبير:abeer عبيد:obaid,obeid,ubaid عثمان:othman,uthman,osman عزيز:aziz عزيزة:aziza
عزام:azzam عفاف:afaf عهود:ohoud,ohood عواطف:awatef عيسى:essa,issa,eissa غادة:ghada غازي:ghazi غانم:ghanem غدير:ghadeer قاسم:qasim,qassim,kassem
قتيبة:qutaiba كريم:kareem,karim كمال:kamal كوثر:kawthar لولوة:lulwa,loulwa,lolwa ليلى:laila,layla,leila ليث:laith ليان:layan لمياء:lamia,lamya
لبنى:lubna,lobna لجين:lujain,lojain لطيفة:latifa,lateefa لطيف:lateef ماجد:majed ماجدة:majeda,majida مجدي:majdi منير:munir,muneer
منيرة:munira,muneera,moneera منى:mona,muna منال:manal مشاري:mishari,meshari مشعل:mishal,meshal,mishaal مبارك:mubarak,mobarak مراد:murad
مروان:marwan مروة:marwa,marwah مرام:maram مها:maha مهدي:mahdi موسى:musa,mousa,moosa,moussa معاذ:muath,muaath,moath معتز:moataz مالك:malek
نعيم:naeem نضال:nidal,nedal وليد:waleed,walid وائل:wael وفاء:wafaa وسام:wissam,wesam ياسر:yasser,yaser,yasir ياسين:yaseen,yassin,yasin
ياسمين:yasmin,yasmine,yasmeen يحيى:yahya,yehia يزيد:yazeed,yazid يزن:yazan يعقوب:yaqoub,yacoub,yaqub يوسف:yousef,youssef,yusuf,yusef,yousuf
يونس:younis,younes,yunus مايكل:michael ديفيد:david دانيال:daniel ماريا:maria توماس:thomas بيتر:peter جورج:george روبرت:robert جيمس:james
كارلوس:carlos لوكاس:lucas,lukas أوليفيا:olivia ليام:liam مارتن:martin كيفن:kevin بريا:priya راجيش:rajesh إيما:emma آدم:adam إلياس:elias,ilyas
سوزان:susan,suzan نانسي:nancy كريستينا:christina,cristina فيليب:philip,phillip ألكسندر:alexander جوزيف:joseph أندرو:andrew باتريك:patrick
راشيل:rachel جيسيكا:jessica جينيفر:jennifer فيكتور:victor,viktor
""".split()

#: The names after "ال" that a family carries: "الحربي" is ``alharbi``.
_FAMILIES = """
حربي:harbi عتيبي:otaibi,otaiby,utaibi مطيري:mutairi,mutairy عجمي:ajmi رشيدي:rashidi,rasheedi عنزي:enezi,anazi,enazi,anzi
شمري:shammari,shamari دوسري:dosari,dossary,dosary,dousari,dawsari قحطاني:qahtani غامدي:ghamdi زهراني:zahrani هاجري:hajri,hajeri
عازمي:azmi,azemi,azimi ظفيري:dhafiri,dhafeeri كندري:kandari صباح:sabah صالح:saleh راشد:rashed رشيد:rasheed غانم:ghanem,ghanim خالد:khaled,khalid
فهد:fahad,fahd شايع:shaya منصور:mansour,mansoor ناصر:nasser,naser سالم:salem سليم:saleem مبارك:mubarak خليفة:khalifa هاشمي:hashemi,hashimi
سيد:sayed,sayyid فرج:faraj نجار:najjar حداد:haddad خطيب:khatib,khateeb مالكي:maliki,malki جهني:juhani,johani بلوشي:balushi,baloushi حمد:hamad
مري:marri زعابي:zaabi كويتي:kuwaiti مناعي:mannai سويدي:suwaidi,suwaidy
""".split()

#: The names "عبد ال" is written before.
_SERVANT_OF = """
رحمن:rahman رحيم:rahim,raheem عزيز:aziz,azeez كريم:karim,kareem لطيف:latif,lateef هادي:hadi رزاق:razzaq,razaq محسن:mohsen,muhsin
حميد:hamid,hameed قادر:qader,qadir ملك:malik,malek وهاب:wahab,wahhab جليل:jalil,jaleel غفور:ghafour,ghafoor غني:ghani سلام:salam
مجيد:majid,majeed واحد:wahid,wahed باسط:basit,baset ناصر:nasser,naser جبار:jabbar,jabar فتاح:fattah,fatah
""".split()

#: Spellings that are English words too: in a text they name no one unless they
#: are written as a name, with a capital where no sentence begins ("the sultan",
#: "a martin"; "I met Sultan"). ``tests`` hold it to the English of Kazma's own
#: documents.
_ENGLISH_WORDS = frozenset("sultan hind nada jihad iman hamza martin victor peter".split())


@functools.lru_cache(maxsize=1)
def _names() -> tuple[dict[str, frozenset[str]], dict[str, str]]:
    """``(Arabic name, folded -> its Latin spellings, Latin spelling -> the one
    Arabic name it is)``."""
    from kazma_core.documents.arabic import fold_for_search

    made: dict[str, set[str]] = {}

    def add(entries: list[str], arabic: str = "", latin: tuple[str, ...] = ("",)) -> None:
        for entry in entries:
            name, _, spellings = entry.partition(":")
            made.setdefault(arabic + fold_for_search(name), set()).update(
                before + s for before in latin for s in spellings.split(","))

    add(_NAMES)
    add(_FAMILIES, "ال", ("al", "el"))
    add(_SERVANT_OF, "عبدال", ("abdul", "abdel", "abdal", "abdol", "abdu", "abdur"))
    add(["عبدالله:abdullah,abdallah,abdulla,abdalla"])
    return ({name: frozenset(spellings) for name, spellings in made.items()},
            {spelling: name for name, spellings in made.items() for spelling in spellings})


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


@functools.lru_cache(maxsize=65536)
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


@functools.lru_cache(maxsize=65536)
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


#: A word with a capital, and no sentence beginning at it.
_CAPITAL = re.compile(r"(?<![Tt]he )(?<![Aa] )(?<![Aa]n )(?<![Tt]his )(?<![Tt]hat )(?<!\w)[A-Z][^\W_]*")
#: A name before one of these is a place's ("Badr Hall", "Salem Street").
_PLACE = re.compile(r"(?<!\w)([^\W\d_]+)\s+(?:Hall|Street|St|Road|Rd|Avenue|Ave|Tower|Towers|Mall|Mosque|School|Hospital|"
                    r"Clinic|Center|Centre|Bank|Park|Club|Hotel|Square|Building|University|College|Company|Stadium|"
                    r"Market|Beach|Island|Bridge|Palace|Gate|Complex)(?!\w)")


def arabic_names_in(text: str) -> dict[str, list[int]]:
    """The names the Latin words of *text* are the one spelling of, in folded
    Arabic, and where they stand ("Omar met Sara Al-Harbi" names عمر, ساره and
    الحربي). An English word is one only where it is written as a name
    (:data:`_ENGLISH_WORDS`: "Sultan moved", never "the sultan"), and a name
    before a place's word is the place's ("Badr Hall")."""
    found: dict[str, list[int]] = {}
    as_names = {w.casefold() for w in _CAPITAL.findall(text)}
    places = {w.casefold() for w in _PLACE.findall(text)}
    for at, word in enumerate(_latin_words(text)):
        name = _arabic_of(word)
        if name and word not in places and (word not in _ENGLISH_WORDS or word in as_names):
            found.setdefault(name, []).append(at)
    return found


def is_a_name(word: str) -> bool:
    """Whether the Arabic *word* is a name of the list ("سامي", whose ending
    is its own: nobody's "سام")."""
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(str(word or "")) in _names()[0]


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


def same_spelling(one: str, other: str) -> bool:
    """Whether two names of one alphabet are the same letters, however they are
    written ("نورة", "نوره"; "Noura", ``noura``)."""
    return _arabic_words(one) == _arabic_words(other) != ()


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
