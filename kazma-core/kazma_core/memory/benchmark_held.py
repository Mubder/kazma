"""Is the deep pass shown the fact a turn changes? (the memory plan's W6, step 1)

A fact the deep pass is not shown cannot be replaced by it: the old value and
the new one both stay current (``memory.held_facts``). Here eight changes --
a relative's job, a habit and an allergy that ended, a move, a car, a plan, a
friend's city, a project's date -- are told in English, in Gulf Arabic and in
Modern Standard Arabic, to a store that holds the old fact, facts of people
and things the turn is not about, and none or forty newer facts of the user.
Each turn goes through what the memory worker reads for it
(``worker_bootstrap._turn_context``) and the one write path; the reply
replaces the old fact when it was shown, as a model that reads the list
would, and is filed under another name, as models name facts.

The Arabic turns are told twice: to a store that holds the old fact in
Arabic, and (``crossed``) to one that holds it in English. Vectors are
recorded (``scripts/memory_bench.py held-pairs-vectors``); no model is asked.
"""

from __future__ import annotations

import sqlite3
from typing import Any

__all__ = ["HELD_PAIRS", "HELD_PAIRS_VECTORS", "run_held_pairs"]

HELD_PAIRS_VECTORS = "held_pairs_vectors.npz"

#: What the assistant answered: it names nobody.
_ANSWER = {"en": "Thanks, I have noted that. Would you like a short checklist for what comes next?",
           "ar": "شكراً، سجلت ذلك. هل تحب قائمة قصيرة بالخطوات التالية؟"}

#: One change: the fact held (subject, name, kind, value) in each language,
#: the fact the turn tells (another name, as a model gives it; None: the old
#: one ended), and the turn.
HELD_PAIRS: tuple[dict[str, Any], ...] = (
    {"id": "job", "held": {"en": ("noura", "works_as", "functional", "dentist"),
                           "ar": ("نورة", "works_as", "functional", "طبيبة أسنان")},
     "told": {"en": ("profession", "surgeon"), "ar": ("profession", "جراحة")},
     "said": {"en": "My sister Noura is a surgeon now.", "gulf": "أختي نورة صارت جراحة الحين",
              "msa": "أختي نورة أصبحت جراحة."}},
    {"id": "habit", "held": {"en": ("user", "habit", "set", "swims every friday"),
                             "ar": ("user", "habit", "set", "يسبح كل جمعة")},
     "told": {"en": None, "ar": None},
     "said": {"en": "I stopped swimming, I run now.", "gulf": "بطلت السباحة، الحين أركض",
              "msa": "توقفت عن السباحة، وأنا أركض الآن."}},
    {"id": "home", "held": {"en": ("user", "lives_in", "functional", "Kuwait"),
                            "ar": ("user", "lives_in", "functional", "الكويت")},
     "told": {"en": ("relocated", "Porto"), "ar": ("relocated", "بورتو")},
     "said": {"en": "We moved, our home is in Porto now.", "gulf": "نقلنا، بيتنا الحين في بورتو",
              "msa": "انتقلنا، ومنزلنا الآن في بورتو."}},
    {"id": "allergy", "held": {"en": ("user", "allergic_to", "set", "peanuts"),
                               "ar": ("user", "allergic_to", "set", "الفول السوداني")},
     "told": {"en": None, "ar": None},
     "said": {"en": "Turns out I can eat peanuts after all.", "gulf": "طلع عادي آكل فول سوداني",
              "msa": "اتضح أنني أستطيع أكل الفول السوداني."}},
    {"id": "car", "held": {"en": ("user", "drives", "set", "Toyota Corolla"),
                           "ar": ("user", "drives", "set", "تويوتا كورولا")},
     "told": {"en": ("car", "Tesla"), "ar": ("car", "تسلا")},
     "said": {"en": "I sold the old car and I drive a Tesla now.", "gulf": "بعت سيارتي القديمة وصرت أسوق تسلا",
              "msa": "بعت سيارتي القديمة وأصبحت أقود تسلا."}},
    {"id": "plan", "held": {"en": ("user", "plans_to", "set", "travel to Japan in March"),
                            "ar": ("user", "plans_to", "set", "السفر إلى اليابان في مارس")},
     "told": {"en": None, "ar": None},
     "said": {"en": "We cancelled the Japan trip.", "gulf": "كنسلنا سفرة اليابان", "msa": "ألغينا رحلة اليابان."}},
    {"id": "friend", "held": {"en": ("omar", "lives_in", "functional", "Muscat"),
                              "ar": ("عمر", "lives_in", "functional", "مسقط")},
     "told": {"en": ("moved_home_to", "Doha"), "ar": ("moved_home_to", "الدوحة")},
     "said": {"en": "Omar moved to Doha last week.", "gulf": "عمر نقل الدوحة الأسبوع اللي طاف",
              "msa": "انتقل عمر إلى الدوحة الأسبوع الماضي."}},
    {"id": "project", "held": {"en": ("falcon_project", "deadline", "functional", "30 June"),
                               "ar": ("مشروع_فالكون", "deadline", "functional", "30 يونيو")},
     "told": {"en": ("due", "end of July"), "ar": ("due", "نهاية يوليو")},
     "said": {"en": "The Falcon project was pushed to the end of July.", "gulf": "مشروع فالكون تأجل لآخر يوليو",
              "msa": "تأجل مشروع فالكون إلى نهاية يوليو."}},
)

#: Facts of people and things no turn above is about.
_OTHERS = {
    "en": (("acme", "based_in", "Lisbon"), ("berlin_office", "opened", "2024"), ("zain", "works_as", "pilot"),
           ("sara", "likes", "painting"), ("luna", "is_a", "cat"), ("blue_heron", "status", "shipped")),
    "ar": (("أكمي", "based_in", "لشبونة"), ("مكتب_برلين", "opened", "2024"), ("زين", "works_as", "طيار"),
           ("سارة", "likes", "الرسم"), ("لونا", "is_a", "قطة"), ("مشروع_هيرون", "status", "تم تسليمه")),
}

_NEWER_EN = {
    "visited": ("the aquarium", "a pottery class", "Cairo", "the national library", "Bali", "a jazz concert",
                "the dentist", "Mount Fuji", "the spice market", "a chess club"),
    "likes": ("karak tea", "hiking", "sushi", "old films", "chess", "dark chocolate", "board games", "podcasts",
              "gardening", "rain"),
    "uses_tool": ("vim", "Notion", "Docker", "Figma", "Excel", "Postman", "Obsidian", "Grafana", "Trello", "Zotero"),
    "owns": ("a bicycle", "a Nikon camera", "an espresso machine", "a cat", "a tent", "a blue lamp", "a piano",
             "a kayak", "a telescope", "a sewing machine"),
}
_NEWER_AR = {
    "visited": ("حوض الأسماك", "ورشة فخار", "القاهرة", "المكتبة الوطنية", "بالي", "حفلة موسيقية", "عيادة الأسنان",
                "جبل فوجي", "سوق التوابل", "نادي الشطرنج"),
    "likes": ("شاي الكرك", "المشي في الجبال", "السوشي", "الأفلام القديمة", "الشطرنج", "الشوكولاتة الداكنة",
              "ألعاب الطاولة", "البودكاست", "الزراعة", "المطر"),
    "uses_tool": ("فيم", "نوشن", "دوكر", "فيجما", "إكسل", "بوستمان", "أوبسيديان", "جرافانا", "تريلو", "زوتيرو"),
    "owns": ("دراجة هوائية", "كاميرا نيكون", "آلة إسبريسو", "قطة", "خيمة", "مصباح أزرق", "بيانو", "قارب كاياك",
             "تلسكوب", "ماكينة خياطة"),
}
_NEWER = {lang: tuple((name, value) for at in range(10) for name, values in table.items() for value in values[at:at + 1])
          for lang, table in (("en", _NEWER_EN), ("ar", _NEWER_AR))}


def _hold(conn: sqlite3.Connection, subject: str, name: str, kind: str, value: str, at: float) -> str:
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.benchmark import BENCH_TENANT
    from kazma_core.memory.fact_names import users_names

    subject, name = users_names(subject, name)
    stored = mutate_belief(conn, subject, name, value, predicate_type=kind, extraction_method="llm_inferred",
                           tenant_id=BENCH_TENANT, source_session="told-before", source_turn=1, now=at, private=True)
    return str(stored["belief_id"])


def _turn(said: str, answer: str, at: float) -> dict[str, Any]:
    """The turn as the memory worker reads it: its words and its stored vector."""
    from kazma_core.memory.benchmark import BENCH_TENANT
    from kazma_core.memory.embedder import encode_text_to_blob, get_embedding_model_name
    from kazma_core.memory.episode_text import embed_text

    return {"tenant_id": BENCH_TENANT, "session_id": "now", "turn_number": 1, "user_text": said,
            "assistant_text": answer, "created_at": at, "said_by": None,
            "embedding": encode_text_to_blob(embed_text(said, answer)),
            "embedding_model_version": get_embedding_model_name()}


def _one(pair: dict[str, Any], dialect: str, held_in: str, newer: int) -> dict[str, Any]:
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2
    from kazma_core.memory.benchmark import BASE_TIME, BENCH_TENANT, _private_stores
    from kazma_core.memory.worker_bootstrap import _turn_context

    lang = "en" if dialect == "en" else "ar"
    subject, name, kind, value = pair["held"][held_in]
    said, told = pair["said"][dialect], pair["told"][lang]
    with _private_stores("kazma-held-pairs") as (primary, ops):
        old = _hold(primary, subject, name, kind, value, BASE_TIME)
        for n, (other, what, its) in enumerate(_OTHERS[held_in]):
            _hold(primary, other, what, "set", its, BASE_TIME + 60 + n)
        for n, (what, its) in enumerate(_NEWER[held_in][:newer]):
            _hold(primary, "user", what, "set", its, BASE_TIME + 3600 + n)
        at = BASE_TIME + 86400.0
        held = _turn_context(primary, _turn(said, _ANSWER[lang], at))["held"]
        ref = next((h["ref"] for h in held if h["id"] == old), None)
        new = {"subject": subject, "predicate": name if told is None else told[0], "predicate_type": kind,
               "object": value if told is None else told[1], "confidence": 0.9,
               **({"ended": True} if told is None else {}), **({"replaces": ref} if ref else {})}
        # An ending of a fact the model was not shown names no value it knows.
        if told is not None or ref:
            _apply_beliefs_to_v2([new], primary, ops, session_id="now", turn=1, tenant_id=BENCH_TENANT, now=at + 60,
                                 private=True, user_text=said, held=held)
        current = primary.execute("SELECT valid_until IS NULL AND invalidated_at IS NULL FROM beliefs "
                                  "WHERE id = ? AND tenant_id = ?", (old, BENCH_TENANT)).fetchone()[0]
    about = {"user", subject}
    return {"pair": pair["id"], "dialect": dialect, "held_in": held_in, "newer": newer, "shown": ref is not None,
            "closed": not current, "places": len(held),
            "unrelated": sorted({h["subject"] for h in held} - about)}


def run_held_pairs() -> list[dict[str, Any]]:
    """Every pair, dialect, store language and number of newer facts: whether
    the old fact was ``shown``, whether it was ``closed``, and the subjects
    the turn is not about that were shown (``unrelated``)."""
    return [_one(pair, dialect, held_in, newer)
            for pair in HELD_PAIRS for dialect in ("en", "gulf", "msa")
            for held_in in (("en",) if dialect == "en" else ("ar", "en"))
            for newer in (0, 40)]
