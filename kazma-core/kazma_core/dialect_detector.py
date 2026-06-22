"""Dialect detection engine for Arabic text.

Supports Kuwaiti (Gulf), Egyptian, Levantine, Maghrebi, and MSA.
Uses fasttext when available, falls back to rule-based detection.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Data models ───────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class DialectResult:
    """Result of dialect detection."""
    dialect: str  # "kw", "eg", "lb", "ma", "msa"
    confidence: float  # 0.0 – 1.0
    alternatives: list[tuple[str, float]] = field(default_factory=list)


# ── Kuwaiti dialect lexicon ───────────────────────────────────────────
# High-frequency Kuwaiti/Gulf Arabic markers that strongly indicate Kuwaiti dialect.
# Each entry maps a Kuwaiti word/pattern to an MSA equivalent (for documentation;
# the detector uses presence/absence, not translation).

_KUWAITI_MARKERS: dict[str, str] = {
    "شلونك": "كيف حالك",        # how are you
    "وين": "أين",               # where
    "ليش": "لماذا",             # why
    "هلا": "الآن",              # now
    "تمام": "جيد",              # okay
    "اخوي": "أخي",             # my brother (informal address)
    "ياخوي": "يا أخي",          # hey brother
    "شنو": "ماذا",             # what (Gulf)
    "شلون": "كيف",             # how (Gulf)
    "هجم": "تعال",             # come (Gulf)
    "يالله": "هيا بنا",            # come on
    "واجد": "كثير",             # a lot (Gulf)
    "خوش": "جيد",               # good (Gulf)
    "زينة": "جميلة",            # beautiful (Gulf)
    "abaloch": "أمامك",         # in front of you (transliterated)
    "yalla": "هيا",             # let's go
    "wallah": "والله",          # I swear
    "habibi": "حبيبي",          # my love
    "allah yisa'ak": "الله يساعدك",  # God help you
    "shay": "شيء",              # something (Gulf)
    "mako": "لا يوجد",          # there isn't (Gulf)
    "aku": "يوجد",              # there is (Gulf)
    "esh": "ماذا",              # what (Gulf short form)
    "aboush": "أبو",            # father of (Gulf)
    "daesh": "ماذا",            # what (Gulf variant)
    "gal": "قال",               # he said (Gulf)
    "agool": "أقول",            # I say (Gulf)
    "ba'a": "باع",              # he sold (Gulf)
    "yishtgil": "يشتغل",       # he works (Gulf pronunciation)
    "shaghal": "مشغول",         # busy (Gulf)
    "wain": "أين",              # where (variant)
    "ainak": "عينك",            # your eye / watch out
    "thuban": "ثعبان",          # snake
    "bait": "بيت",              # house (Gulf)
    "sawalef": "قصص",           # stories (Gulf plural)
    "rawain": "روائح",          # smells (Gulf)
    "darse": "درس",             # lesson (Gulf)
    "jareeda": "جريدة",         # newspaper (Gulf)
    "ma'ana": "معنا",           # with us
    "ma'ak": "معك",             # with you
    "ma'ah": "معه",             # with him
    "rah": "سأ",                 # I will (Gulf prefix)
    "aruh": "أذهب",             # I go (Gulf)
    "arid": "أريد",             # I want (Gulf)
    "areed": "أريد",            # I want (variant)
    "ayesh": "عايش",            # living (Gulf)
    "tawwal": "طويل",           # long (Gulf)
    "bukrah": "غداً",           # tomorrow (Gulf)
    "yume": "يوم",              # day (Gulf)
    "mbarheen": "مبروك",        # congratulations
    "abrooj": "عباءة",          # abaya (Gulf)
    "dishdash": "ثوب",          # thobe (Gulf)
}

# Egyptian dialect markers
_EGYPTIAN_MARKERS: set[str] = {
    "أنا عايز", "عايز", "إيه", "ليه", "أيوة", "مفيش", "بتاع",
    "يا عم", "يا حج", "أهلاً بيك", "ماشي", "حلوة", "يا سلام",
    "كده", "ألفين", "هنا", "بعدين", "طب", "يلا",
}

# Levantine dialect markers
_LEVANTINE_MARKERS: set[str] = {
    "شو", "هيك", "عم", "بكرا", "إذا", "خليني", "بده",
    "كثير", "يا زلمة", "يا عمو", "ما في", "هلق",
    "هون", "عنجد", "منيح", "زيتونة",
}

# Maghrebi dialect markers
_MAGHREBI_MARKERS: set[str] = {
    "واش", "باش", "دابا", "شحال", "غادي", "تي", "vere",
    "درت", "بزاف", "هاك", "واخا", "ساهلة",
}


# ── Rule-based detector ──────────────────────────────────────────────

def _rule_based_detect(text: str) -> DialectResult:
    """Rule-based dialect detection using keyword/pattern matching."""
    text_lower = text.strip()
    scores: dict[str, float] = {d: 0.0 for d in ("kw", "eg", "lb", "ma", "msa")}
    total_hits = 0

    # Kuwaiti markers
    for marker in _KUWAITI_MARKERS:
        if marker.lower() in text_lower:
            scores["kw"] += 2.0
            total_hits += 1

    # Egyptian markers
    for marker in _EGYPTIAN_MARKERS:
        if marker.lower() in text_lower:
            scores["eg"] += 2.0
            total_hits += 1

    # Levantine markers
    for marker in _LEVANTINE_MARKERS:
        if marker.lower() in text_lower:
            scores["lb"] += 2.0
            total_hits += 1

    # Maghrebi markers
    for marker in _MAGHREBI_MARKERS:
        if marker.lower() in text_lower:
            scores["ma"] += 2.0
            total_hits += 1

    # Heuristic: if no dialect markers found, lean toward MSA
    if total_hits == 0:
        # Short text with no markers — treat as MSA with moderate confidence
        scores["msa"] = 1.0
    else:
        # Give MSA a base score so it isn't zeroed out by dialect markers
        scores["msa"] = 1.0

    # Additional MSA indicators: formal constructions
    msa_formal = [
        "الذي", "التي", "هذا", "هذه", "ذلک", "الذين",
        "المملكة العربية السعودية", "الجمهورية", "الدولة",
        "بناءً على", "وفقاً", "إثر", "خلال", "بموجب",
    ]
    for pattern in msa_formal:
        if pattern in text:
            scores["msa"] += 0.5

    # Pick the winner
    best = max(scores, key=lambda d: scores[d])
    best_score = scores[best]

    if best_score == 0:
        return DialectResult(dialect="msa", confidence=0.5, alternatives=[])

    # Build alternatives sorted by score
    alternatives = sorted(
        [(d, s) for d, s in scores.items() if d != best and s > 0],
        key=lambda x: x[1],
        reverse=True,
    )[:3]

    # Confidence: ratio of winner score to total
    total = sum(scores.values())
    confidence = min(best_score / max(total, 0.001), 1.0) if total > 0 else 0.5
    # Ensure minimum confidence for positive matches
    confidence = max(confidence, 0.5)

    return DialectResult(
        dialect=best,
        confidence=round(confidence, 3),
        alternatives=[(d, round(s / max(total, 0.001), 3)) for d, s in alternatives],
    )


# ── Fasttext-based detector ──────────────────────────────────────────

def _fasttext_detect(text: str, model) -> DialectResult:
    """Detect dialect using fasttext model."""
    label_scores = model.predict(text, k=5)
    labels, scores = label_scores
    results = []
    for label, score in zip(labels, scores):
        # fasttext labels look like "__label__kw"
        dialect = label.replace("__label__", "")
        results.append((dialect, float(score)))

    if not results:
        return DialectResult(dialect="msa", confidence=0.5, alternatives=[])

    best_dialect, best_score = results[0]
    return DialectResult(
        dialect=best_dialect,
        confidence=round(best_score, 3),
        alternatives=[(d, round(s, 3)) for d, s in results[1:4]],
    )


# ── Public API ───────────────────────────────────────────────────────

class DialectDetector:
    """Detects Arabic dialect from input text.

    Supports Kuwaiti (kw), Egyptian (eg), Levantine (lb),
    Maghrebi (ma), and Modern Standard Arabic (msa).

    Uses fasttext when a model is available, falls back to
    rule-based detection.
    """

    SUPPORTED_DIALECTS: list[str] = ["kw", "eg", "lb", "ma", "msa"]

    def __init__(self, model_path: str = "models/fasttext-dialect.bin") -> None:
        self.model_path = model_path
        self._model = None
        self._loaded = False

    def _load_model(self) -> None:
        """Lazy-load the fasttext model."""
        if self._loaded:
            return
        self._loaded = True
        try:
            import fasttext  # type: ignore[import-untyped]
            self._model = fasttext.load_model(self.model_path)
            logger.info("Loaded fasttext dialect model from %s", self.model_path)
        except ImportError:
            logger.info("fasttext not installed; using rule-based detection")
        except Exception as exc:
            logger.warning("Failed to load fasttext model: %s; using rule-based", exc)

    def detect(self, text: str) -> DialectResult:
        """Detect dialect of input text.

        Returns DialectResult with dialect code, confidence, and alternatives.
        """
        self._load_model()

        if not text or not text.strip():
            return DialectResult(dialect="msa", confidence=0.5, alternatives=[])

        if self._model is not None:
            return _fasttext_detect(text, self._model)

        return _rule_based_detect(text)

    def detect_batch(self, texts: list[str]) -> list[DialectResult]:
        """Batch detection for efficiency."""
        self._load_model()
        if self._model is not None:
            # Batch predict with fasttext
            results: list[DialectResult] = []
            for text in texts:
                results.append(_fasttext_detect(text, self._model))
            return results
        return [_rule_based_detect(t) for t in texts]
