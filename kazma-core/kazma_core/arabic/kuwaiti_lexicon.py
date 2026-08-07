"""Canonical Kuwaiti Arabic dialect lexicon — single source of truth.

Previously there were TWO separate Kuwaiti marker dictionaries that drifted
apart over time:

1. ``kuwaiti_tokenizer.DIALECT_MARKERS`` — 47 entries, space-padded Latin keys,
   used for token annotation.
2. ``dialect_detector._KUWAITI_MARKERS`` — 53 entries, unpadded keys with
   ``\\b`` word-boundary matching, used for dialect scoring.

This module unifies them into one dict. Both consumers import from here and
apply their own matching strategies (padding vs word-boundary).

Each entry: ``{Kuwaiti_form: {"msa": MSA_equivalent, "tags": set_of_purposes}}``
Tags: ``"tokenizer"`` (used by kuwaiti_tokenizer), ``"detector"`` (used by
dialect_detector), or both.
"""

from __future__ import annotations

# The canonical Kuwaiti → MSA mapping. All unique entries from both lexicons.
# Latin-script entries are stored WITHOUT padding — each consumer adds its
# own matching strategy (tokenizer pads with spaces, detector uses \b).
CANONICAL_KUWAITI_MARKERS: dict[str, str] = {
    # Core conversational
    "شلونك": "كيف حالك",
    "شلون": "كيف",
    "وين": "أين",
    "ليش": "لماذا",
    "هلا": "الآن",
    "تمام": "جيد",
    "شنو": "ماذا",
    "اخوي": "أخي",
    "ياخوي": "يا أخي",
    "هجم": "تعال",
    "يالله": "هيا بنا",
    # Informal address
    "اخو": "أخ",
    "اخوات": "إخوة",
    "اخوكم": "أخوك",
    "اخويا": "أخي",
    "اختك": "أختك",
    # Descriptions / adjectives
    "خوش": "جيد",
    "زينة": "جميلة",
    "حلو": "جميل",
    "حلوه": "جميلة",
    "كبير": "كبير",
    "صغير": "صغير",
    "قديم": "قديم",
    "جديد": "جديد",
    "tawwal": "طويل",
    # Verbs / actions (Latin script — Gulf transliteration)
    "gal": "قال",
    "agool": "أقول",
    "aruh": "أذهب",
    "areed": "أريد",
    "arid": "أريد",
    "yishtgil": "يشتغل",
    "ayesh": "عايش",
    "shaghal": "مشغول",
    "ba'a": "باع",
    "rah": "سأ",
    # Prepositions / particles
    "mako": "لا يوجد",
    "aku": "يوجد",
    "ماف": "لا يوجد",
    "وايد": "كثير",
    "واجد": "كثير",
    "بس": "فقط",
    "عسب": "حتى",
    "عشان": "من أجل",
    "زاي": "مثل",
    "هيج": "هكذا",
    # Common Gulf expressions
    "بالعافية": "بالصحة",
    "يعطيك العافية": "الله يعطيك العافية",
    "تسلم": "الله يسلمك",
    "الله يسلمك": "وأنت بخير",
    "ما شاء الله": "ما شاء الله",
    "ان شاء الله": "إن شاء الله",
    "الحمد لله": "الحمد لله",
    "سبحان الله": "سبحان الله",
    # Transliterated Gulf expressions (detector-only)
    "wallah": "والله",
    "habibi": "حبيبي",
    "yalla": "هيا",
    "allah yisa'ak": "الله يساعدك",
    "shay": "شيء",
    "esh": "ماذا",
    "aboush": "أبو",
    "daesh": "ماذا",
    "abaloch": "أمامك",
    # Numbers / time
    "buckra": "غداً",
    "bukrah": "غداً",
    "yume": "يوم",
    "ibaarak": "مبروك",
    "mbarheen": "مبروك",
    # Objects / nouns
    "bait": "بيت",
    "sawalef": "قصص",
    "rawain": "روائح",
    "darse": "درس",
    "jareeda": "جريدة",
    "ainak": "عينك",
    "thuban": "ثعبان",
    "abrooj": "عباءة",
    "dishdash": "ثوب",
    # Pronouns / connectors
    "ma'ana": "معنا",
    "ma'ak": "معك",
    "ma'ah": "معه",
    # Variants
    "wain": "أين",
}
