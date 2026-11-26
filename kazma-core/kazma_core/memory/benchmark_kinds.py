"""Is a fact found by an Arabic question as by an English one, whatever kind it
is and whatever the model named it? (the memory plan's W6, step 2)

Twenty-three kinds of facts the deep pass writes -- under the names it gives
them, which no quick pass does ("intends_to", "has_occupation", "met") -- are
held in English and in Arabic, alone, among four others and among all of them
with twelve more, and each is asked in English, Gulf Arabic and Modern Standard
Arabic. Then the questions that use a kind's word otherwise ("What can I buy
here?", "شنو أشتري هدية؟"), which ask no fact. The real ``recall`` with bge-m3's
recorded vectors (``scripts/memory_bench.py fact-kinds-vectors``); no model is
asked. On 2026-11-25, 18 of these kinds asked once in each language: English
lost 2 of 36, Arabic 8.

:func:`run_lists` asks for a whole list whose entries the model named in
several ways ("plans_to", "planning_to_visit", "intends_to").
"""

from __future__ import annotations

from typing import Any

__all__ = ["FACT_KINDS", "FACT_KINDS_VECTORS", "TOLD", "TOLD_VECTORS", "run_fact_kinds", "run_fact_languages_kinds",
           "run_lists", "run_narrow_lists"]

FACT_KINDS_VECTORS = "fact_kinds_vectors.npz"

#: The fact (name, kind of value, value in English and in Arabic) and the
#: questions that ask it.
FACT_KINDS: tuple[dict[str, Any], ...] = (
    {"name": "age_is", "type": "functional", "en": "32", "ar": "٣٢",
     "asked": {"en": "How old am I?", "gulf": "كم عمري؟", "msa": "ما هو عمري؟"}},
    {"name": "birthday_is", "type": "functional", "en": "4 May", "ar": "٤ مايو",
     "asked": {"en": "When is my birthday?", "gulf": "متى عيد ميلادي؟", "msa": "متى يوم ميلادي؟"}},
    {"name": "has_pet", "type": "set", "en": "a cat named Milo", "ar": "قطوة اسمها ميلو",
     "asked": {"en": "Do I have any pets?", "gulf": "عندي حيوانات أليفة؟", "msa": "هل لدي حيوانات أليفة؟"}},
    {"name": "marital_status", "type": "functional", "en": "married", "ar": "متزوج",
     "asked": {"en": "Am I married?", "gulf": "أنا متزوج؟", "msa": "هل أنا متزوج؟"}},
    {"name": "speaks_language", "type": "set", "en": "Italian", "ar": "إيطالي",
     "asked": {"en": "What languages do I speak?", "gulf": "شنو اللغات اللي أتكلمها؟", "msa": "ما هي اللغات التي أتحدثها؟"}},
    {"name": "drives", "type": "functional", "en": "a Toyota Corolla", "ar": "تويوتا كورولا",
     "asked": {"en": "What car do I drive?", "gulf": "شنو سيارتي؟", "msa": "ما هي سيارتي؟"}},
    {"name": "enrolled_in", "type": "functional", "en": "Kuwait University", "ar": "جامعة الكويت",
     "asked": {"en": "Where do I study?", "gulf": "وين أدرس؟", "msa": "أين أدرس؟"}},
    {"name": "has_goal", "type": "set", "en": "run a marathon", "ar": "أركض ماراثون",
     "asked": {"en": "What are my goals?", "gulf": "شنو أهدافي؟", "msa": "ما هي أهدافي؟"}},
    {"name": "intends_to", "type": "set", "en": "visit Japan", "ar": "أزور اليابان",
     "asked": {"en": "What are my plans?", "gulf": "شنو خططي؟", "msa": "ما هي خططي؟"}},
    {"name": "is_interested_in", "type": "set", "en": "calligraphy", "ar": "الخط العربي",
     "asked": {"en": "What am I interested in?", "gulf": "شنو اهتماماتي؟", "msa": "ما هي اهتماماتي؟"}},
    {"name": "dietary_restriction", "type": "set", "en": "no gluten", "ar": "بدون جلوتين",
     "asked": {"en": "What can I not eat?", "gulf": "شنو ما أقدر آكل؟", "msa": "ما الذي لا أستطيع أن آكله؟"}},
    {"name": "has_occupation", "type": "functional", "en": "a nurse", "ar": "ممرض",
     "asked": {"en": "What do I do for a living?", "gulf": "شنو شغلتي؟", "msa": "ما هي مهنتي؟"}},
    {"name": "has_daughter", "type": "set", "en": "Lulwa", "ar": "لولوة",
     "asked": {"en": "What is my daughter called?", "gulf": "شنو اسم بنتي؟", "msa": "ما اسم ابنتي؟"}},
    {"name": "bought", "type": "set", "en": "a blue bicycle", "ar": "دراجة زرقاء",
     "asked": {"en": "What did I buy?", "gulf": "شنو شريت؟", "msa": "ماذا اشتريت؟"}},
    {"name": "visited", "type": "set", "en": "the Grand Mosque", "ar": "المسجد الكبير",
     "asked": {"en": "What places have I visited?", "gulf": "شنو الأماكن اللي زرتها؟", "msa": "ما الأماكن التي زرتها؟"}},
    {"name": "met", "type": "set", "en": "Omar", "ar": "عمر",
     "asked": {"en": "Who did I meet?", "gulf": "منو قابلت؟", "msa": "من قابلت؟"}},
    {"name": "attended", "type": "set", "en": "a pottery workshop", "ar": "ورشة فخار",
     "asked": {"en": "What did I attend?", "gulf": "شنو حضرت؟", "msa": "ماذا حضرت؟"}},
    {"name": "received", "type": "set", "en": "a silver watch", "ar": "ساعة فضية",
     "asked": {"en": "What did I receive?", "gulf": "شنو استلمت؟", "msa": "ماذا استلمت؟"}},
    {"name": "finished", "type": "set", "en": "the night course", "ar": "الدورة المسائية",
     "asked": {"en": "What did I finish?", "gulf": "شنو خلصت؟", "msa": "ماذا أنهيت؟"}},
    {"name": "phone_number", "type": "functional", "en": "555-0147", "ar": "٥٥٥٠١٤٧",
     "asked": {"en": "What is my phone number?", "gulf": "شنو رقم تلفوني؟", "msa": "ما هو رقم هاتفي؟"}},
    {"name": "doctor", "type": "functional", "en": "Dr Sara", "ar": "دكتورة سارة",
     "asked": {"en": "Who is my doctor?", "gulf": "منو دكتوري؟", "msa": "من هو طبيبي؟"}},
    {"name": "has_routine", "type": "set", "en": "walks at dawn", "ar": "المشي عند الفجر",
     "asked": {"en": "What is my routine?", "gulf": "شنو روتيني؟", "msa": "ما هو روتيني؟"}},
    {"name": "employed_at", "type": "functional", "en": "Initech", "ar": "إنيتك",
     "asked": {"en": "Where do I work?", "gulf": "وين أشتغل؟", "msa": "أين أعمل؟"}},
)

#: What else the larger stores hold: kinds no question above asks.
_OTHERS: tuple[tuple[str, str, str, str], ...] = (
    ("uses_tool", "set", "Notion", "نوشن"), ("prefers", "set", "dark mode", "الوضع الداكن"),
    ("struggles_with", "set", "sleeping early", "النوم بدري"), ("owns", "set", "a standing desk", "طاولة وقوف"),
    ("watched", "set", "Dune", "دون"), ("believes", "set", "reading helps", "القراءة تفيد"),
    ("active_project", "functional", "a garden shed", "بناء مخزن"),
    ("timezone", "functional", "Asia/Kuwait", "توقيت الكويت"), ("laptop_model", "functional", "ThinkPad", "ثنك باد"),
    ("collects", "set", "stamps", "طوابع"), ("plays", "set", "chess", "شطرنج"), ("listens_to", "set", "jazz", "جاز"),
)

#: A kind's word in another sense, asked of the whole store: the named kinds
#: are not asked, and none of their facts may be shown for the word's sake.
OTHER_SENSE: tuple[tuple[str, str, str], ...] = (
    ("en", "What can I buy here?", "bought"), ("gulf", "شنو أشتري هدية؟", "bought"), ("msa", "ماذا أشتري هدية؟", "bought"),
    ("en", "What can I eat for dinner?", "dietary_restriction"), ("gulf", "شنو آكل على العشا؟", "dietary_restriction"),
    ("msa", "ماذا آكل على العشاء؟", "dietary_restriction"),
    ("en", "How much does the living room lamp cost?", "has_occupation"),
    ("gulf", "شكثر سعر ليت الصالة؟", "has_occupation"), ("msa", "كم سعر مصباح غرفة المعيشة؟", "has_occupation"),
    ("en", "How many goals did he score?", "has_goal"), ("gulf", "كم هدف سجل اللاعب؟", "has_goal"),
    ("msa", "كم هدفاً سجل اللاعب؟", "has_goal"),
    ("en", "Where is the plan of the building?", "intends_to"), ("gulf", "وين خطة المبنى؟", "intends_to"),
    ("msa", "أين خطة المبنى؟", "intends_to"),
    ("en", "What is the interest rate at the bank?", "is_interested_in"),
    ("gulf", "شنو نسبة الفايدة بالبنك؟", "is_interested_in"), ("msa", "ما نسبة الفائدة في البنك؟", "is_interested_in"),
)


def _ask(conn: Any, question: str, *, days: float = 1.0) -> Any:
    from datetime import UTC
    from unittest import mock

    from kazma_core.memory.benchmark import BASE_TIME, BENCH_TENANT
    from kazma_core.memory.recall import recall

    with mock.patch("kazma_core.memory.recall._memory_zone", lambda: UTC):
        return recall(question, conn=conn, tenant_id=BENCH_TENANT, limit=5, local_only=True,
                      now=BASE_TIME + days * 86400)


def _hold(conn: Any, facts: list[tuple[str, str, str]]) -> dict[str, int]:
    """*facts* (name, kind of value, value) of the user, filed as the deep
    pass files them; the id of each -> its place."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.benchmark import BASE_TIME, BENCH_TENANT
    from kazma_core.memory.fact_names import users_names

    out: dict[str, int] = {}
    for n, (name, kind, value) in enumerate(facts):
        subject, filed = users_names("user", name)
        stored = mutate_belief(conn, subject, filed, value, predicate_type=kind, extraction_method="llm_inferred",
                               tenant_id=BENCH_TENANT, source_session=f"s{n}", source_turn=1,
                               now=BASE_TIME + n * 60, private=True)
        out[str(stored["belief_id"])] = n
    return out


def run_fact_kinds() -> list[dict[str, Any]]:
    """Every (language told, store size, fact, way asked): the facts shown, by
    the name each was told under, and the one that was asked (``want``; for a
    word in another sense ``never``, the kind that must not be shown)."""
    from kazma_core.memory.benchmark import _private_stores

    rows: list[dict[str, Any]] = []
    for told in ("en", "ar"):
        kinds = [(k["name"], k["type"], k[told]) for k in FACT_KINDS]
        others = [(name, kind, en if told == "en" else ar) for name, kind, en, ar in _OTHERS]
        for size in (1, 5, len(kinds) + len(others)):
            # A store of every fact is written once; a small one for each fact.
            for at in range(len(kinds)) if size <= 5 else (None,):
                facts = [*kinds, *others] if at is None else [kinds[at], *others[:size - 1]]
                with _private_stores("kazma-fact-kinds") as (conn, _ops):
                    place = _hold(conn, facts)
                    for n, kind in enumerate(FACT_KINDS) if at is None else ((0, FACT_KINDS[at]),):
                        for way, question in kind["asked"].items():
                            result = _ask(conn, question)
                            shown = [place.get(h.id) for h in result.beliefs]
                            rows.append({"told": told, "size": size, "asked": way, "want": kind["name"],
                                         "question": question, "found": n in shown, "degraded": result.degraded,
                                         "shown": [facts[p][0] for p in shown if p is not None]})
                    for way, question, never in OTHER_SENSE if at is None else ():
                        result = _ask(conn, question)
                        rows.append({"told": told, "size": size, "asked": way, "never": never, "question": question,
                                     "shown": [facts[place[h.id]][0] for h in result.beliefs if h.id in place],
                                     "degraded": result.degraded})
    return rows


#: One list, its entries named as the model named such entries in the pilots'
#: stores, in English and in Arabic; what no entry of it is; the question.
LISTS: tuple[dict[str, Any], ...] = (
    {"kind": "plans", "entries": (("plans_to", "visit Japan", "أزور اليابان"), ("planning_to", "paint the kitchen", "أصبغ المطبخ"),
                                  ("intends_to", "learn Spanish", "أتعلم الإسباني"), ("has_plan", "a garden party", "حفلة في الحديقة"),
                                  ("planning_event", "a family dinner", "عشا عائلي"), ("is_planning", "a camping trip", "رحلة تخييم")),
     "not": (("plan_type", "5 GB of data", "باقة ٥ جيجا"), ("has_plant", "a fern", "نبتة سرخس")),
     "asked": {"en": ("List all my plans.", "List the plans I told you about."),
               "gulf": ("عطني كل خططي", "عطني قائمة الخطط اللي قلت لك عنها"),
               "msa": ("اذكر لي كل خططي", "اذكر لي الخطط التي أخبرتك عنها")}},
    {"kind": "goals", "entries": (("has_goal", "run a marathon", "أركض ماراثون"), ("goal", "save 2,000 dinars", "أوفر ٢٠٠٠ دينار"),
                                  ("current_goal", "read more", "أقرأ أكثر"), ("career_goal", "lead a team", "أقود فريق"),
                                  ("goal_is", "sleep early", "أنام بدري")),
     "not": (("goals_scored", "seven", "سبعة"),),
     "asked": {"en": ("List all my goals.", "List the goals I told you about."),
               "gulf": ("عطني كل أهدافي", "عطني قائمة الأهداف اللي قلت لك عنها"),
               "msa": ("اذكر لي كل أهدافي", "اذكر لي الأهداف التي أخبرتك عنها")}},
    {"kind": "interests", "entries": (("interested_in", "calligraphy", "الخط العربي"), ("has_interest", "astronomy", "الفلك"),
                                      ("is_interested_in", "old cars", "السيارات القديمة"), ("hobby", "fishing", "الحداق"),
                                      ("current_interest", "baking", "الخبز")),
     "not": (("interest_rate", "four percent", "أربعة بالمية"),),
     "asked": {"en": ("List all my interests.", "List the interests I told you about."),
               "gulf": ("عطني كل اهتماماتي", "عطني قائمة الاهتمامات اللي قلت لك عنها"),
               "msa": ("اذكر لي كل اهتماماتي", "اذكر لي الاهتمامات التي أخبرتك عنها")}},
    {"kind": "bought", "entries": (("bought", "a blue bicycle", "دراجة زرقاء"), ("purchased", "a lamp", "ليت"),
                                   ("bought_item", "a tent", "خيمة")),
     "not": (("wants_to_buy", "a kayak", "قارب كاياك"),),
     "asked": {"en": ("List everything I bought.", "List the things I bought."),
               "gulf": ("عطني كل الأشياء اللي شريتها", "عطني قائمة اللي شريته"),
               "msa": ("اذكر لي كل الأشياء التي اشتريتها", "اذكر لي كل ما اشتريته")}},
)

#: A question of SOME of a kind, told under one name, in a store with more of
#: the kind under others: the entries of that name are the answer, the others
#: are not (the review of step 2: 8 of 8 books became 4, 5 right concerts 12).
NARROW: tuple[dict[str, Any], ...] = (
    {"name": "plans_to_read", "entries": (("Dune", "رواية كثبان"), ("The Odyssey", "الأوديسة"), ("a book on bees", "كتاب عن النحل"),
                                          ("Emma", "رواية إيما"), ("The Trial", "رواية المحاكمة"), ("a history of Kuwait", "تاريخ الكويت"),
                                          ("poems by Mutanabbi", "ديوان المتنبي"), ("Animal Farm", "مزرعة الحيوان")),
     "others": (("plans_to", "set", "paint the kitchen", "أصبغ المطبخ"), ("planning_to", "set", "learn Spanish", "أتعلم الإسباني"),
                ("intends_to", "set", "run a marathon", "أركض ماراثون"), ("has_plan", "set", "a garden party", "حفلة في الحديقة"),
                ("plans_to_buy", "set", "a tent", "خيمة"), ("plans_to_visit", "set", "Japan", "اليابان")),
     "asked": {"en": ("List all the books I plan to read.", "What are all the books I'm planning to read?"),
               "gulf": ("عطني كل الكتب اللي ناوي أقراها", "شنو كل الكتب اللي أخطط أقراها؟"),
               "msa": ("اذكر لي كل الكتب التي أنوي قراءتها", "ما هي كل الكتب التي أخطط لقراءتها؟")}},
    {"name": "planning_trip", "entries": (("to Oman in spring", "إلى عمان في الربيع"), ("to Istanbul", "إلى إسطنبول"),
                                          ("to the chalet", "إلى الشاليه"), ("to Cairo with Dana", "إلى القاهرة مع دانة"), ("to Georgia", "إلى جورجيا")),
     "others": (("plans_to", "set", "paint the kitchen", "أصبغ المطبخ"), ("planning_to", "set", "learn Spanish", "أتعلم الإسباني"),
                ("intends_to", "set", "sleep early", "أنام بدري"), ("has_plan", "set", "a garden party", "حفلة في الحديقة")),
     "asked": {"en": ("List all the trips I am planning.",), "gulf": ("عطني كل السفرات اللي أخطط لها",),
               "msa": ("اذكر لي كل الرحلات التي أخطط لها",)}},
    {"name": "attended_concert", "entries": (("Fairouz in Beirut", "فيروز في بيروت"), ("a jazz night", "ليلة جاز"), ("Coldplay", "كولدبلاي"),
                                             ("an oud recital", "أمسية عود"), ("the Opera House gala", "حفل دار الأوبرا")),
     "others": (("attended", "set", "a pottery workshop", "ورشة فخار"), ("attended_meeting", "set", "the school board", "مجلس المدرسة"),
                ("participated_in", "set", "a beach clean-up", "تنظيف الشاطئ"), ("attended_wedding", "set", "of a neighbour", "عرس الجيران")),
     "asked": {"en": ("How many concerts did I attend?", "List every concert I attended."),
               "gulf": ("كم حفلة غنائية حضرت؟", "عطني كل الحفلات الغنائية اللي حضرتها"),
               "msa": ("كم حفلة غنائية حضرت؟", "اذكر لي كل الحفلات الغنائية التي حضرتها")}},
    {"name": "career_goal", "entries": (("lead a team", "أقود فريق"), ("a pay rise", "زيادة راتب"), ("move to product", "أنتقل لإدارة المنتجات")),
     "others": (("has_goal", "set", "run a marathon", "أركض ماراثون"), ("goal", "set", "save 2,000 dinars", "أوفر ٢٠٠٠ دينار"),
                ("current_goal", "set", "read more", "أقرأ أكثر"), ("fitness_goal", "set", "lose five kilos", "أنقص خمسة كيلو")),
     "asked": {"en": ("How many career goals do I have?",), "gulf": ("كم هدف مهني عندي؟",), "msa": ("كم هدفاً مهنياً لدي؟",)}},
)


def run_lists() -> list[dict[str, Any]]:
    """Every (list, language told, way asked): how many of its entries a
    question for all of them shows, of how many, and what it shows that is not
    of the list."""
    from kazma_core.memory.benchmark import _private_stores

    rows: list[dict[str, Any]] = []
    for told in ("en", "ar"):
        at = 1 if told == "en" else 2
        for one in LISTS:
            entries = [(e[0], "set", e[at]) for e in one["entries"]]
            apart = [(e[0], "set", e[at]) for e in one["not"]]
            others = [(name, kind, en if told == "en" else ar) for name, kind, en, ar in _OTHERS]
            with _private_stores("kazma-lists") as (conn, _ops):
                place = _hold(conn, [*others, *apart, *entries])
                for way, question in ((way, q) for way, asked in one["asked"].items() for q in asked):
                    result = _ask(conn, question)
                    shown = [place[h.id] for h in result.beliefs if h.id in place]
                    first = len(others) + len(apart)
                    rows.append({"kind": one["kind"], "told": told, "asked": way, "question": question, "of": len(entries),
                                 "shown": sum(1 for p in shown if p >= first), "degraded": result.degraded,
                                 "apart": [apart[p - len(others)][0] for p in shown if len(others) <= p < first]})
    return rows


def run_narrow_lists() -> list[dict[str, Any]]:
    """Every (:data:`NARROW` list, language told, way asked): how many of the
    name's own entries are shown, of how many, and how many of the kind's other
    names are."""
    from kazma_core.memory.benchmark import _private_stores

    rows: list[dict[str, Any]] = []
    for told in ("en", "ar"):
        at = 0 if told == "en" else 1
        for one in NARROW:
            entries = [(one["name"], "set", e[at]) for e in one["entries"]]
            beside = [(name, kind, en if told == "en" else ar) for name, kind, en, ar in (*one["others"], *_OTHERS[:8])]
            with _private_stores("kazma-narrow-lists") as (conn, _ops):
                # Told before the kind's other entries: the newest are the others.
                place = _hold(conn, [*entries, *beside])
                for way, question in ((way, q) for way, asked in one["asked"].items() for q in asked):
                    result = _ask(conn, question)
                    shown = [place[h.id] for h in result.beliefs if h.id in place]
                    n = len(entries)
                    rows.append({"name": one["name"], "told": told, "asked": way, "question": question, "of": n,
                                 "own": sum(1 for p in shown if p < n), "degraded": result.degraded,
                                 "other": sum(1 for p in shown if n <= p < n + len(one["others"]))})
    return rows


# ── Told as a turn, asked three ways (the memory plan's W6, step 3) ─────────
#
# :data:`FACT_KINDS` seeds the deep pass's names straight into the store. Here
# each fact is TOLD, as a user turn, and filed as the product files it: the
# quick pass first, then the deep pass's reply (scripted, with the names the
# pilots' models gave), through ``belief_extractor._apply_beliefs_to_v2`` --
# ``users_names``, ``name_to_file`` and the quick pass's own names are in the
# measure. Told in English and in Arabic, in stores of one, five and thirty
# facts; asked in English, Gulf and Modern Standard Arabic, in the first
# person and with the thing asked ("Which bike did I buy?"); and, of each
# kind, a question that uses its word in another sense, which shows nothing.
# ``run_fact_languages``' table (five kinds the quick pass writes) grown by
# the kinds a model names. Its vectors are recorded apart
# (:data:`TOLD_VECTORS`), so the locked run keeps its own.

TOLD_VECTORS = "fact_languages_kinds_vectors.npz"

#: kind -> the deep pass's name and label, the turn and the value in English and
#: in Arabic, the questions (first person, then with the thing asked) and the
#: question in another sense, each in English, Gulf and MSA.
TOLD: tuple[dict[str, Any], ...] = (
    {"kind": "bought", "name": "bought", "type": "set",
     "told": {"en": ("I bought a blue bicycle.", "a blue bicycle"), "ar": ("شريت دراجة زرقاء.", "دراجة زرقاء")},
     "asked": {"en": ("What did I buy?", "Which bike did I buy?"), "gulf": ("شنو شريت؟", "شنو الدراجة اللي شريتها؟"),
               "msa": ("ماذا اشتريت؟", "ما الدراجة التي اشتريتها؟")},
     "other": {"en": "What can I buy here?", "gulf": "شنو أشتري هدية؟", "msa": "ماذا أشتري هدية؟"}},
    {"kind": "visited", "name": "visited", "type": "set",
     "told": {"en": ("I visited the Grand Mosque.", "the Grand Mosque"), "ar": ("زرت المسجد الكبير.", "المسجد الكبير")},
     "asked": {"en": ("Which places have I visited?", "Which mosque did I visit?"),
               "gulf": ("عدد كل الأماكن اللي زرتها", "أي مسجد زرت؟"), "msa": ("ما الأماكن التي زرتها؟", "أي مسجد زرت؟")},
     "other": {"en": "Which places do tourists visit?", "gulf": "وين يروحون السياح؟", "msa": "ما الأماكن التي يزورها السياح؟"}},
    {"kind": "attended", "name": "attended", "type": "set",
     "told": {"en": ("I attended a pottery workshop.", "a pottery workshop"), "ar": ("حضرت ورشة فخار.", "ورشة فخار")},
     "asked": {"en": ("What did I attend?", "Which workshop did I attend?"), "gulf": ("شنو حضرت؟", "أي ورشة حضرت؟"),
               "msa": ("ماذا حضرت؟", "أي ورشة حضرتها؟")},
     "other": {"en": "Who attended the meeting?", "gulf": "منو حضر الاجتماع؟", "msa": "من حضر الاجتماع؟"}},
    {"kind": "met", "name": "met", "type": "set",
     "told": {"en": ("I met Omar at the gym.", "Omar"), "ar": ("قابلت عمر في النادي.", "عمر")},
     "asked": {"en": ("Who did I meet?", "Who did I meet at the gym?"), "gulf": ("منو قابلت؟", "منو قابلت في النادي؟"),
               "msa": ("من قابلت؟", "من التقيت في النادي؟")},
     "other": {"en": "Where should we meet?", "gulf": "وين نتقابل؟", "msa": "أين نلتقي؟"}},
    {"kind": "received", "name": "received", "type": "set",
     "told": {"en": ("I received a silver watch.", "a silver watch"), "ar": ("استلمت ساعة فضية.", "ساعة فضية")},
     "asked": {"en": ("What did I receive?", "Which watch did I receive?"), "gulf": ("شنو استلمت؟", "شنو الساعة اللي استلمتها؟"),
               "msa": ("ماذا استلمت؟", "ما الساعة التي استلمتها؟")},
     "other": {"en": "When will the parcel be received?", "gulf": "متى يوصل الطرد؟", "msa": "متى يصل الطرد؟"}},
    {"kind": "finished", "name": "finished", "type": "set",
     "told": {"en": ("I finished the night course.", "the night course"), "ar": ("خلصت الدورة المسائية.", "الدورة المسائية")},
     "asked": {"en": ("What did I finish?", "Which course did I finish?"), "gulf": ("شنو خلصت؟", "أي دورة خلصت؟"),
               "msa": ("ماذا أنهيت؟", "أي دورة أنهيتها؟")},
     "other": {"en": "Has the match finished?", "gulf": "خلصت المباراة؟", "msa": "هل انتهت المباراة؟"}},
    {"kind": "plans", "name": "intends_to", "type": "set",
     "told": {"en": ("I plan to visit Japan.", "visit Japan"), "ar": ("ناوي أزور اليابان.", "أزور اليابان")},
     "asked": {"en": ("What are my plans?", "Which country do I plan to visit?"), "gulf": ("شنو خططي؟", "أي دولة ناوي أزورها؟"),
               "msa": ("ما هي خططي؟", "أي بلد أنوي زيارته؟")},
     "other": {"en": "Where is the plan of the building?", "gulf": "وين خطة المبنى؟", "msa": "أين خطة المبنى؟"}},
    {"kind": "goals", "name": "has_goal", "type": "set",
     "told": {"en": ("My goal is to run a marathon.", "run a marathon"), "ar": ("هدفي أركض ماراثون.", "أركض ماراثون")},
     "asked": {"en": ("What are my goals?", "Which race is my goal?"), "gulf": ("شنو أهدافي؟", "شنو هدفي بالجري؟"),
               "msa": ("ما هي أهدافي؟", "ما هدفي في الجري؟")},
     "other": {"en": "How many goals did he score?", "gulf": "كم هدف سجل؟", "msa": "كم هدفاً سجل اللاعب؟"}},
    {"kind": "interests", "name": "is_interested_in", "type": "set",
     "told": {"en": ("I'm interested in calligraphy.", "calligraphy"), "ar": ("أنا مهتم بالخط العربي.", "الخط العربي")},
     "asked": {"en": ("What am I interested in?", "Which art am I interested in?"), "gulf": ("شنو اهتماماتي؟", "أي فن مهتم فيه؟"),
               "msa": ("ما هي اهتماماتي؟", "بأي فن أهتم؟")},
     "other": {"en": "What is the interest rate?", "gulf": "شنو نسبة الفايدة؟", "msa": "ما نسبة الفائدة؟"}},
    {"kind": "habits", "name": "has_routine", "type": "set",
     "told": {"en": ("Every day I walk at dawn.", "walk at dawn"), "ar": ("كل يوم أمشي عند الفجر.", "أمشي عند الفجر")},
     "asked": {"en": ("What is my routine?", "When do I walk?"), "gulf": ("شنو روتيني؟", "متى أمشي؟"),
               "msa": ("ما هو روتيني؟", "متى أمشي؟")},
     "other": {"en": "What is a soldier's routine?", "gulf": "شنو روتين الجندي؟", "msa": "ما روتين الجندي؟"}},
    {"kind": "diet", "name": "dietary_restriction", "type": "set",
     "told": {"en": ("I can't eat gluten.", "gluten"), "ar": ("ما أقدر آكل جلوتين.", "جلوتين")},
     "asked": {"en": ("What can I not eat?", "Can I eat gluten?"), "gulf": ("شنو ما أقدر آكل؟", "أقدر آكل جلوتين؟"),
               "msa": ("ما الذي لا أستطيع أن آكله؟", "هل أستطيع أكل الجلوتين؟")},
     "other": {"en": "What can I eat for dinner?", "gulf": "شنو آكل على العشا؟", "msa": "ماذا آكل على العشاء؟"}},
    {"kind": "doctor", "name": "doctor", "type": "functional",
     "told": {"en": ("My doctor is Dr Sara.", "Dr Sara"), "ar": ("دكتوري الدكتورة سارة.", "الدكتورة سارة")},
     "asked": {"en": ("Who is my doctor?", "Which doctor do I see?"), "gulf": ("منو دكتوري؟", "عند أي دكتور أراجع؟"),
               "msa": ("من هو طبيبي؟", "أي طبيب أراجع؟")},
     "other": {"en": "Can you recommend a doctor?", "gulf": "تنصحني بدكتور؟", "msa": "هل تنصحني بطبيب؟"}},
    {"kind": "age_is", "name": "has_age", "type": "functional",
     "told": {"en": ("I am 32 years old.", "32"), "ar": ("عمري ٣٢ سنة.", "٣٢")},
     "asked": {"en": ("How old am I?", "What is my age?"), "gulf": ("كم عمري؟", "شكثر عمري؟"), "msa": ("كم عمري؟", "ما هو عمري؟")},
     "other": {"en": "How old is the castle?", "gulf": "كم عمر القلعة؟", "msa": "ما عمر القلعة؟"}},
    {"kind": "birthday", "name": "birthday_is", "type": "functional",
     "told": {"en": ("My birthday is on 4 May.", "4 May"), "ar": ("عيد ميلادي يوم ٤ مايو.", "٤ مايو")},
     "asked": {"en": ("When is my birthday?", "Which month is my birthday in?"), "gulf": ("متى عيد ميلادي؟", "بأي شهر ميلادي؟"),
               "msa": ("متى عيد ميلادي؟", "في أي شهر يوم ميلادي؟")},
     "other": {"en": "What is the Gregorian calendar?", "gulf": "شنو التقويم الميلادي؟", "msa": "ما هو التقويم الميلادي؟"}},
    {"kind": "study", "name": "enrolled_in", "type": "functional",
     "told": {"en": ("I study at Kuwait University.", "Kuwait University"), "ar": ("أدرس في جامعة الكويت.", "جامعة الكويت")},
     "asked": {"en": ("Where do I study?", "Which university do I study at?"), "gulf": ("وين أدرس؟", "بأي جامعة أدرس؟"),
               "msa": ("أين أدرس؟", "في أي جامعة أدرس؟")},
     "other": {"en": "What should I teach the students?", "gulf": "شنو أدرس الطلاب؟", "msa": "ماذا أدرّس الطلاب؟"}},
    {"kind": "car", "name": "drives", "type": "functional",
     "told": {"en": ("I drive a Toyota Corolla.", "a Toyota Corolla"), "ar": ("أسوق تويوتا كورولا.", "تويوتا كورولا")},
     "asked": {"en": ("What car do I drive?", "Which Toyota do I drive?"), "gulf": ("شنو سيارتي؟", "أي تويوتا أسوق؟"),
               "msa": ("ما هي سيارتي؟", "ما نوع السيارة التي أقودها؟")},
     "other": {"en": "Which car is the fastest?", "gulf": "أي سيارة أسرع؟", "msa": "ما أسرع سيارة؟"}},
    {"kind": "daughters", "name": "has_daughter", "type": "set",
     "told": {"en": ("My daughter is called Lulwa.", "Lulwa"), "ar": ("بنتي اسمها لولوة.", "لولوة")},
     "asked": {"en": ("What is my daughter called?", "Who is my daughter?"), "gulf": ("شنو اسم بنتي؟", "منو بنتي؟"),
               "msa": ("ما اسم ابنتي؟", "من هي ابنتي؟")},
     "other": {"en": "What are good names for a girl?", "gulf": "شنو أحلى أسامي البنات؟", "msa": "ما أجمل أسماء البنات؟"}},
    {"kind": "sons", "name": "has_son", "type": "set",
     "told": {"en": ("My son is called Fahad.", "Fahad"), "ar": ("ولدي اسمه فهد.", "فهد")},
     "asked": {"en": ("What is my son called?", "Who is my son?"), "gulf": ("شنو اسم ولدي؟", "منو ولدي؟"),
               "msa": ("ما اسم ابني؟", "من هو ابني؟")},
     "other": {"en": "What shall I build in the garden?", "gulf": "شنو أبني بالحديقة؟", "msa": "ماذا أبني في الحديقة؟"}},
    {"kind": "current_role", "name": "has_occupation", "type": "functional",
     "told": {"en": ("I'm a nurse.", "a nurse"), "ar": ("أنا ممرض.", "ممرض")},
     "asked": {"en": ("What do I do for a living?", "What is my job?"), "gulf": ("شنو شغلتي؟", "شنو وظيفتي؟"),
               "msa": ("ما هي مهنتي؟", "ما وظيفتي؟")},
     "other": {"en": "How much is the living room lamp?", "gulf": "شكثر سعر ليت الصالة؟", "msa": "كم سعر مصباح غرفة المعيشة؟"}},
    {"kind": "pets", "name": "has_pet", "type": "set",
     "told": {"en": ("I have a cat named Milo.", "a cat named Milo"), "ar": ("عندي قطوة اسمها ميلو.", "قطوة اسمها ميلو")},
     "asked": {"en": ("Do I have any pets?", "What is my cat called?"), "gulf": ("عندي حيوانات أليفة؟", "شنو اسم قطوتي؟"),
               "msa": ("هل لدي حيوانات أليفة؟", "ما اسم قطتي؟")},
     "other": {"en": "Which pets live the longest?", "gulf": "أي حيوان أليف يعيش أكثر؟", "msa": "ما الحيوانات الأليفة الأطول عمراً؟"}},
    # A pet held by its name alone ("has_cat Milo"): "What is my cat called?"
    # shares no word with it (the review of step 3, L3).
    {"kind": "pets", "name": "has_cat", "type": "set",
     "told": {"en": ("My cat is called Simba.", "Simba"), "ar": ("قطوتي اسمها سمبا.", "سمبا")},
     "asked": {"en": ("What is my cat called?", "What's my cat's name?"), "gulf": ("شنو اسم قطوتي؟", "منو قطوتي؟"),
               "msa": ("ما اسم قطتي؟", "ما هو اسم قطتي؟")},
     "other": {"en": "Are cats clever?", "gulf": "القطاوة ذكية؟", "msa": "هل القطط ذكية؟"}},
    {"kind": "languages", "name": "speaks_language", "type": "set",
     "told": {"en": ("I speak Italian.", "Italian"), "ar": ("أتكلم إيطالي.", "إيطالي")},
     "asked": {"en": ("What languages do I speak?", "Do I speak Italian?"), "gulf": ("شنو اللغات اللي أتكلمها؟", "أتكلم إيطالي؟"),
               "msa": ("ما اللغات التي أتحدثها؟", "هل أتحدث الإيطالية؟")},
     "other": {"en": "What language is spoken in Brazil?", "gulf": "شنو لغة البرازيل؟", "msa": "ما لغة البرازيل؟"}},
    {"kind": "married", "name": "marital_status", "type": "functional",
     "told": {"en": ("I am married.", "married"), "ar": ("أنا متزوج.", "متزوج")},
     "asked": {"en": ("Am I married?", "What is my marital status?"), "gulf": ("أنا متزوج؟", "شنو حالتي الاجتماعية؟"),
               "msa": ("هل أنا متزوج؟", "ما حالتي الاجتماعية؟")},
     "other": {"en": "When did the king get married?", "gulf": "متى تزوج الملك؟", "msa": "متى عقد الملك قرانه؟"}},
)

#: Store sizes: a fact alone, among four others, and among all.
TOLD_SIZES = (1, 5, 30)

#: Questions in another sense that hold a word the tables give the kind, as a
#: word of its own (the review of step 3, M3): the precision rows must be able
#: to see what is added. Asked in the stores of five and of thirty. By the
#: told row's name.
TOLD_OTHER_BARE: dict[str, dict[str, tuple[str, ...]]] = {
    "doctor": {"en": ("Which doctor is best for teeth?", "What doctor do I see for a cold?"),
               "gulf": ("أي دكتور أسنان أفضل؟", "شنو لازم أراجع قبل الامتحان؟", "لأي دكتور أروح للأسنان؟"),
               "msa": ("أي طبيب أسنان أفضل في المدينة؟", "متى أراجع البنك؟", "أي طبيب أراجع لعيوني؟")},
    "has_pet": {"en": ("What's my pet peeve?", "Is my pet project done?"), "gulf": ("البروتين الحيواني زين؟",),
                "msa": ("هل البروتين الحيواني صحي؟",)},
    "is_interested_in": {"en": ("How do I take care of my health?", "How do I care for my mother?"),
                         "gulf": ("شلون أهتم بصحتي؟", "بأي طريقة أهتم بأمي؟"), "msa": ("كيف أهتم بصحتي؟", "بأي شكل أهتم بنفسي؟")},
    "drives": {"en": ("What's in my drive?", "Where did I save the file on Google Drive?", "How is my driving?",
                      "Where did I drive?"),
            "gulf": ("وين السوق؟",), "msa": ("أين السوق؟",)},
    "visited": {"en": ("What should I visit in Paris?",), "gulf": ("شنو أزور بباريس؟",), "msa": ("ماذا أزور في باريس؟",)},
    "marital_status": {"en": ("Is the actor married?",), "gulf": ("الممثل متزوج؟",), "msa": ("هل الممثل متزوج؟",)},
    "dietary_restriction": {"en": ("Can I eat here?", "Can we eat outside?", "Can I take Panadol with coffee?",
                                   "Can I eat this?"),
             "gulf": ("أقدر آكل الحين؟", "يصير آكل بالليل؟", "أقدر آكل بالبيت؟"),
             "msa": ("هل أستطيع أن أتناول البنادول مع القهوة؟", "هل يمكنني أن آكل هذا؟")},
}

#: More first-person questions of a told row, by its name (form ``also``):
#: "my pet" in both languages (the delta review of step 3, N2).
TOLD_ASKED_ALSO: dict[str, dict[str, tuple[str, ...]]] = {
    "has_pet": {"en": ("What is my pet's name?", "What's my pet called?"), "gulf": ("شنو اسم حيواني الأليف؟",),
                "msa": ("ما اسم حيواني الأليف؟",)},
}


def _other_sense(kind: dict[str, Any]) -> list[tuple[str, str]]:
    """The kind's questions in another sense, (way, question)."""
    bare = TOLD_OTHER_BARE.get(kind["name"], {})
    return [(way, q) for way in ("en", "gulf", "msa") for q in (kind["other"][way], *bare.get(way, ()))]


def _tell(primary: Any, ops: Any, facts: list[tuple[str, str, str, str]]) -> None:
    """Each (turn, deep pass name, label, value) filed as the product files it:
    the quick pass, then the deep pass's reply about it."""
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2, extract_and_apply_beliefs_sync
    from kazma_core.memory.benchmark import BASE_TIME, BENCH_TENANT

    for n, (text, name, kind, value) in enumerate(facts):
        at = BASE_TIME + n * 60
        extract_and_apply_beliefs_sync(primary, ops, text, "Noted.", session_id=f"s{n}", turn=1, tenant_id=BENCH_TENANT,
                                       now=at, private=True)
        _apply_beliefs_to_v2([{"subject": "user", "predicate": name, "predicate_type": kind, "object": value,
                               "confidence": 0.9}], primary, ops, session_id=f"s{n}", turn=1, tenant_id=BENCH_TENANT,
                             now=at, private=True, user_text=text, extraction_method="llm_inferred")


def run_fact_languages_kinds(kinds: tuple[dict[str, Any], ...] = TOLD) -> list[dict[str, Any]]:
    """Every (language told, store size, fact, way asked, form): whether recall
    showed the fact, and for the questions in another sense, whether it showed
    the kind's fact (``never``)."""
    from kazma_core.memory.benchmark import _private_stores

    rows: list[dict[str, Any]] = []
    for told in ("en", "ar"):
        facts = [(k["told"][told][0], k["name"], k["type"], k["told"][told][1]) for k in kinds]
        fillers = [(f"Note: {en if told == 'en' else ar}.", name, kind, en if told == "en" else ar)
                   for name, kind, en, ar in _OTHERS]
        for size in TOLD_SIZES:
            groups = ([(at, [facts[at], *fillers[:size - 1]]) for at in range(len(facts))] if size <= 5
                      else [(None, [*facts, *fillers][:max(size, len(facts))])])
            for at, store in groups:
                with _private_stores("kazma-told-kinds") as (conn, ops):
                    _tell(conn, ops, store)
                    for n, kind in enumerate(kinds) if at is None else ((at, kinds[at]),):
                        value = facts[n][3]
                        also = TOLD_ASKED_ALSO.get(kind["name"], {})
                        for way, questions in kind["asked"].items():
                            for form, question in (*zip(("mine", "thing"), questions, strict=True),
                                                   *(("also", q) for q in also.get(way, ()))):
                                result = _ask(conn, question)
                                rows.append({"told": told, "size": size, "asked": way, "form": form, "kind": kind["kind"],
                                             "question": question, "found": any(value in h.content for h in result.beliefs),
                                             "degraded": result.degraded})
                        if size > 1:
                            for way, question in _other_sense(kind):
                                result = _ask(conn, question)
                                rows.append({"told": told, "size": size, "asked": way, "form": "other", "kind": kind["kind"],
                                             "question": question, "shown": any(value in h.content for h in result.beliefs),
                                             "degraded": result.degraded})
    return rows
