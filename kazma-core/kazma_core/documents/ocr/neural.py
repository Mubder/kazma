"""The neural OCR engine (``documents.ocr.engine: neural``): Arabic, and the
Arabic-Indic digits Tesseract does not read.

2026-10-15 (Q02). Measured against Tesseract on the fidelity corpus, a
12-font Arabic number set (clean and as a poor scan) and a real 300 dpi
scan, CPU only; the numbers are in docs/plans/DOCUMENTS_INDUSTRIAL_HARDENING_GOAL.md.
No single engine read both Arabic letters and Arabic-Indic digits:
Tesseract read neither well, PaddleOCR's Arabic model read letters and
dropped numbers, EasyOCR's read numbers with dot errors in letters. This
engine is the combination that measured best, every step of it measured:

1. **Lines.** PaddleOCR PP-OCRv5 text detection; boxes of one text line
   whose gap is at most a line height are one line (detection splits Latin
   at word gaps, and a recognizer fed single words reads the neighbours'
   edges as stray characters), cut to the line's ink vertically.
2. **Reading.** EasyOCR's Arabic recognizer (``arabic_g1``: Arabic and Latin
   letters, both digit sets) reads every line; ``english_g2`` reads a line
   with no Arabic letter, and every line of an English-only page.
3. **Letters, by vote.** Each word without a digit is checked against two
   more readers of the same line: PaddleOCR's PP-OCRv5 Arabic recognizer and
   Tesseract's words on the page. When those two agree on another word,
   when one of them reads the word EasyOCR itself read second (a letter it
   doubted, its second reading >= 0.1), or when PaddleOCR is confident
   (>= 0.9) on its own, theirs is kept; otherwise EasyOCR's. The vote is
   over letters: a word's punctuation is EasyOCR's (PaddleOCR drops a full
   stop). Numbers are always EasyOCR's (the others drop or misread them).
   Without Tesseract, PaddleOCR's confident words alone are used.
4. **Marks.** EasyOCR writes no harakat (its Arabic list leaves them out); a
   mark it sees over a letter it writes as the nearest letter it has -- the
   tanween of "ريالاً" as the hamza of "ريالأ". Where another reader read the
   same letters with harakat on the same side of that letter, its marks are
   kept; a mark only another reader saw is not (Tesseract adds marks that
   are not on the page).
5. **Order.** A line is read left to right as drawn; it is put in logical
   order by Kazma's glyph-line rule (``parsers.pdf_rtl.logical_line``), the
   one the PDF reader uses -- never a recognizer's own ``get_display`` (a
   date drawn "30-09-2026" in Arabic text is "2026-09-30"). A space read
   before a full stop or comma is the gap the font leaves there, and goes;
   a "," or "." between Arabic-Indic digits is put back as ٫ or ٬ by the
   grammar of a number (``arabic.arabic_number_separators``).
6. **Specks.** Before a line is read, ink smaller than any mark its own pen
   draws (area under half the stroke width squared, no side longer than a
   stroke) is removed: on a poor scan a speck beside a decimal point read
   "3.75" as "3,75".

The models run with ONNX Runtime in this process -- the document worker --
and come from ``neural_models`` (verified against their manifest before
use). Nothing here downloads anything.
"""

from __future__ import annotations

import math
import os
import re
import statistics
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import DocumentLimitError, DocumentOcrError, DocumentOcrUnavailableError
from ..models import BlockType, BoundingBox, DocumentBlock
from .base import OcrCapability, OcrPageResult, OcrReadiness
from .neural_models import MODEL_FILES, model_directory, verify_model_directory

__all__ = [
    "ENGINE",
    "NeuralOcrProvider",
]

ENGINE = "neural"
#: A gap between two detected boxes of one line, in line heights, up to which
#: they are one line (measured: 1.0 kept English pages clean; per-word boxes
#: put stray characters between words).
_LINE_GAP = 1.0
#: PaddleOCR's confidence above which its reading of a word stands alone.
_LETTERS_CONFIDENT = 0.9
#: Ink darker than this is text when a line is cut to its ink.
_INK = 160
_RUNTIME_PACKAGES = ("numpy", "cv2", "onnxruntime", "pyclipper", "PIL")

# EasyOCR's character classes (easyocr/config.py, Apache-2.0), in model order.
# Index 0 is the CTC blank. ``_IGNORED`` are classes outside the languages read
# (EasyOCR's own ``ignore_char`` for ['ar', 'en'] / ['en']): their probability
# is zeroed before decoding, as EasyOCR does.
_ARABIC_G1 = (
    "0123456789!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "٠١٢٣٤٥٦٧٨٩«»؟،؛ءآأؤإئااًبةتثجحخدذرزسشصضطظعغفقكلمنهوىيًٌٍَُِّْٰٓٔٱٹپچڈڑژکڭگںھۀہۂۃۆۇۈۋیېےۓە"
)
_ARABIC_G1_IGNORED = frozenset("ًًٌٍَُِّْٰٓٔٱٹپچڈڑژکڭگںھۀہۂۃۆۇۈۋیېےۓە")
_ENGLISH_G2 = (
    "0123456789!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~ €ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)
_ENGLISH_G2_IGNORED: frozenset[str] = frozenset()
_DIGIT_PASS_CHARS = frozenset("0123456789.,/:-")
#: A page's lines are read within this many OCR call limits (the call limit,
#: documents.ocr.subprocess_timeout_seconds, bounds one Tesseract run; a page
#: here is one run per line, ~0.4 s each on one core).
_PAGE_BUDGET = 4
#: EasyOCR's own score a digits-only cell reading must reach.
_DIGIT_PASS_SCORE = 0.75
#: EasyOCR's second reading of a letter, at or above this probability, is
#: one more reading of the word the vote may count (on the fidelity corpus
#: every letter EasyOCR misread had the right one second, at 0.22-0.34).
_ALTERNATIVE = 0.1
#: A space before a full stop or comma (and the Arabic comma, semicolon and
#: question mark) ending a word: no script Kazma reads puts one there, and a
#: recognizer reads the gap a font leaves after a final ف or ق as one. The
#: colon, semicolon, "!" and "?" are left as read (French sets a space before
#: them).
_SPACE_BEFORE_PUNCTUATION = re.compile(r"(?<=\S)\s+(?=[.,\u060c\u061b\u061f](?:\s|$))")
#: Marks EasyOCR writes as part of a letter: hamza above / below and madda
#: (أ إ آ ؤ ئ, decomposed).
_CARRIED_MARKS = frozenset("\u0653\u0654\u0655")
#: The harakat -- tanween, the short vowels, shadda, sukun: EasyOCR's Arabic
#: recognizer writes none of them.
_HARAKAT = frozenset("\u064b\u064c\u064d\u064e\u064f\u0650\u0651\u0652")
#: Marks drawn below their letter; every other one is drawn above it.
_BELOW = frozenset("\u064d\u0650\u0655")


def _runtime_problem() -> str | None:
    """Why the runtime packages are missing in this process, or None."""
    import importlib.util

    missing = [name for name in _RUNTIME_PACKAGES if importlib.util.find_spec(name) is None]
    if missing:
        return (
            "The neural OCR engine's packages are not installed ("
            + ", ".join(missing)
            + "); install the 'ocr' extra"
        )
    return None


# ── line geometry and order (pure Python, no model) ────────────────────────

Box = tuple[float, float, float, float]


def _merge_line_boxes(boxes: list[Box], gap: float = _LINE_GAP) -> list[Box]:
    """Detected boxes of one text line joined into one where the space between
    them is at most ``gap`` x the line's height."""
    items = sorted(boxes, key=lambda b: (b[1] + b[3]) / 2)
    rows: list[list[Box]] = []
    for box in items:
        if rows:
            row = rows[-1]
            top, bottom = min(b[1] for b in row), max(b[3] for b in row)
            overlap = min(bottom, box[3]) - max(top, box[1])
            if overlap >= 0.5 * min(box[3] - box[1], bottom - top):
                row.append(box)
                continue
        rows.append([box])
    merged: list[Box] = []
    for row in rows:
        row.sort(key=lambda b: b[0])
        height = statistics.median(b[3] - b[1] for b in row)
        current = list(row[0])
        for box in row[1:]:
            if box[0] - current[2] <= gap * height:
                current = [min(current[0], box[0]), min(current[1], box[1]),
                           max(current[2], box[2]), max(current[3], box[3])]
            else:
                merged.append((current[0], current[1], current[2], current[3]))
                current = list(box)
        merged.append((current[0], current[1], current[2], current[3]))
    return merged


def _logical(visual: str) -> str:
    """A line read left to right, in logical order (Kazma's glyph-line rule)."""
    from ..parsers.pdf_rtl import Glyph, logical_line

    glyphs = [Glyph(char, float(i), float(i + 1), 0.0, 1.0) for i, char in enumerate(visual)]
    return logical_line(glyphs) if glyphs else ""


def _line_text(visual: str) -> str:
    """A recognizer's line in logical order, its full stops and commas
    against the word they end."""
    return _SPACE_BEFORE_PUNCTUATION.sub("", _logical(visual))


def _variants(visual: str, alternatives: tuple[tuple[int, str], ...]) -> dict[int, set[str]]:
    """EasyOCR's second readings of single letters (``alternatives``: drawn
    position, letter), as other readings of the word each falls in: word
    index in the logical line -> the words. A letter swapped for one of the
    same bidi class leaves the line's order as it was."""
    if not alternatives:
        return {}
    base = _line_text(visual).split()
    variants: dict[int, set[str]] = {}
    for position, letter in alternatives:
        words = _line_text(visual[:position] + letter + visual[position + 1:]).split()
        if len(words) != len(base):
            continue
        changed = [index for index, (mine, theirs) in enumerate(zip(base, words)) if mine != theirs]
        if len(changed) == 1:
            variants.setdefault(changed[0], set()).add(words[changed[0]])
    return variants


def _word_parts(word: str) -> tuple[str, str, str]:
    """(punctuation before, the word, punctuation after): letters, marks and
    digits are the word."""
    kept = [index for index, char in enumerate(word) if unicodedata.category(char)[0] in "LMN"]
    if not kept:
        return word, "", ""
    return word[:kept[0]], word[kept[0]:kept[-1] + 1], word[kept[-1] + 1:]


def _clusters(word: str) -> list[tuple[str, str]]:
    """The word decomposed (NFD): each base character with the marks on it."""
    out: list[tuple[str, str]] = []
    for char in unicodedata.normalize("NFD", word):
        if unicodedata.category(char) == "Mn" and out:
            out[-1] = (out[-1][0], out[-1][1] + char)
        else:
            out.append((char, ""))
    return out


def _side(marks: str) -> str:
    below = {mark in _BELOW for mark in marks}
    return "below" if below == {True} else "above" if below == {False} else "both"


def _marks_from_readers(word: str, readers: list[str]) -> str:
    """``word`` with the harakat another reader of the same letters saw where
    EasyOCR saw a mark it could only write as a hamza or madda.

    EasyOCR's Arabic recognizer writes no harakat: the tanween over the alef
    of "ريالاً" comes out as the hamza of "ريالأ". A reader that writes
    harakat (Tesseract, PaddleOCR) and read the same letters, with harakat on
    the same side of that letter, decides which mark it is -- unless another
    reader read EasyOCR's hamza there too (then it stands), or the readers'
    harakat differ. Where EasyOCR saw no mark, another reader's is never
    added: Tesseract writes marks that are not on the page ("الشماليٍ")."""
    from ..arabic import fold_for_search

    mine = _clusters(word)
    if not any(set(marks) & _CARRIED_MARKS for _base, marks in mine):
        return word
    seen: dict[int, set[str]] = {}
    for reader in readers:
        theirs = _clusters(reader)
        if fold_for_search(reader) != fold_for_search(word) or len(theirs) != len(mine):
            continue
        if any(fold_for_search(a) != fold_for_search(b) for (a, _m), (b, _n) in zip(mine, theirs)):
            continue
        for index, ((_base, my_marks), (_other, their_marks)) in enumerate(zip(mine, theirs)):
            if not my_marks or not set(my_marks) <= _CARRIED_MARKS:
                continue
            if their_marks == my_marks or (
                    their_marks and set(their_marks) <= _HARAKAT and _side(my_marks) == _side(their_marks)):
                seen.setdefault(index, set()).add(their_marks)
    changed = {index: next(iter(marks)) for index, marks in seen.items()
               if len(marks) == 1 and next(iter(marks)) != mine[index][1]}
    if not changed:
        return word
    rebuilt = "".join(base + changed.get(index, marks) for index, (base, marks) in enumerate(mine))
    return unicodedata.normalize("NFC", rebuilt)


def _has_digit(text: str) -> bool:
    return any(char.isdigit() for char in text)


def _has_arabic(text: str) -> bool:
    return any("ARABIC" in unicodedata.name(char, "") and char.isalpha() for char in text)


def _has_latin(text: str) -> bool:
    return any(char.isascii() and char.isalpha() for char in text)


def _vote_words(easy: str, letters: str, letters_confidence: float, tesseract: list[str],
                variants: dict[int, set[str]] | None = None) -> str:
    """One line read three times (each in logical order): per word, EasyOCR's
    letters, unless the word holds no digit and

    * the two other readers agree on another word, or
    * one of them reads the word EasyOCR read second (``variants``: a letter
      EasyOCR doubted, its second reading -- two readers agree), or
    * PaddleOCR (``letters``) is confident on its own.

    The vote is over letters: the punctuation around a word is EasyOCR's
    (PaddleOCR drops a full stop); where the letters are EasyOCR's, the
    harakat it cannot write come from ``_marks_from_readers``."""
    import difflib

    from ..arabic import fold_for_search

    words = easy.split()
    if not words:
        return easy

    def core(word: str) -> str:
        return _word_parts(word)[1]

    keys = [fold_for_search(core(word)) for word in words]

    def aligned(other: list[str]) -> dict[int, str]:
        mapping: dict[int, str] = {}
        theirs = [fold_for_search(core(word)) for word in other]
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, keys, theirs, autojunk=False).get_opcodes():
            if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
                for k in range(i2 - i1):
                    mapping[i1 + k] = other[j1 + k]
        return mapping

    by_letters, by_tesseract = aligned(letters.split()), aligned(tesseract)
    out = []
    for index, word in enumerate(words):
        before, own, after = _word_parts(word)
        if not own or _has_digit(word):
            out.append(word)
            continue
        other, third = (core(by.get(index, "")) for by in (by_letters, by_tesseract))
        other, third = ("" if _has_digit(reading) else reading for reading in (other, third))
        readers = {fold_for_search(reading) for reading in (other, third) if reading}
        second = sorted(core(variant) for variant in (variants or {}).get(index, ()))
        agreed = next((variant for variant in second if fold_for_search(variant) in readers), None)
        if other and third and fold_for_search(other) == fold_for_search(third):
            chosen = other
        elif agreed:
            chosen = _marks_from_readers(agreed, [r for r in (third, other) if r])
        elif other and letters_confidence >= _LETTERS_CONFIDENT:
            chosen = other
        else:
            chosen = _marks_from_readers(own, [r for r in (third, other) if r])
        out.append(before + chosen + after)
    return " ".join(out)


@dataclass(frozen=True, slots=True)
class _Line:
    box: Box
    confidence: float
    text: str  # logical order


def _blocks_from_lines(
    lines: list[_Line], *, page_number: int, language: str, engine_version: str | None, dpi: int,
) -> tuple[tuple[DocumentBlock, ...], float | None]:
    """Text lines as blocks, top to bottom; lines on one row in their reading
    order (right to left for an Arabic row), joined when their gap is small.
    A "," or "." between Arabic-Indic digits is put back as the Arabic
    separator it stands for (EasyOCR has neither ٫ nor ٬)."""
    from ..arabic import arabic_number_separators, is_rtl_dominant

    items = sorted((line for line in lines if line.text.strip()), key=lambda line: (line.box[1] + line.box[3]) / 2)
    rows: list[list[_Line]] = []
    for item in items:
        if rows:
            row = rows[-1]
            top, bottom = min(r.box[1] for r in row), max(r.box[3] for r in row)
            overlap = min(bottom, item.box[3]) - max(top, item.box[1])
            if overlap >= 0.5 * min(item.box[3] - item.box[1], bottom - top):
                row.append(item)
                continue
        rows.append([item])
    blocks: list[DocumentBlock] = []
    confidences: list[float] = []
    for row in rows:
        rtl = is_rtl_dominant(" ".join(r.text for r in row))
        row.sort(key=lambda r: -r.box[2] if rtl else r.box[0])
        height = statistics.median(r.box[3] - r.box[1] for r in row)
        segments: list[list[_Line]] = [[row[0]]]
        for previous, current in zip(row, row[1:]):
            gap = (previous.box[0] - current.box[2]) if rtl else (current.box[0] - previous.box[2])
            if gap <= 1.5 * height:
                segments[-1].append(current)
            else:
                segments.append([current])
        for segment in segments:
            text = arabic_number_separators(" ".join(" ".join(s.text.split()) for s in segment))
            confidence = sum(s.confidence for s in segment) / len(segment)
            confidences.extend(s.confidence for s in segment)
            blocks.append(DocumentBlock(
                block_id=f"p{page_number}-ocr-{len(blocks) + 1}",
                block_type=BlockType.TEXT,
                text=text,
                bounding_box=BoundingBox(min(s.box[0] for s in segment), min(s.box[1] for s in segment),
                                         max(s.box[2] for s in segment), max(s.box[3] for s in segment)),
                confidence=round(confidence, 6),
                metadata={
                    "ocr": True,
                    "language": language,
                    "engine": ENGINE,
                    "engine_version": engine_version,
                    "dpi": dpi,
                    "direction": "rtl" if rtl else "ltr",
                    "coordinate_space": "image_pixels",
                },
            ))
    mean = sum(confidences) / len(confidences) if confidences else None
    return tuple(blocks), (round(mean, 6) if mean is not None else None)


# ── the models ──────────────────────────────────────────────────────────────

class _Models:
    """The four ONNX sessions of one model directory (loaded once per process)."""

    def __init__(self, directory: Path, threads: int) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.enable_cpu_mem_arena = False
        options.log_severity_level = 3

        def session(role: str) -> Any:
            return ort.InferenceSession(str(directory / MODEL_FILES[role]), options,
                                        providers=["CPUExecutionProvider"])

        self.det = session("det")
        self.rec_arabic = session("rec_arabic")
        self.rec_latin = session("rec_latin")
        self.rec_letters = session("rec_letters")
        meta = self.rec_letters.get_modelmeta().custom_metadata_map
        if "character" not in meta:
            raise DocumentOcrUnavailableError("The PaddleOCR recognizer carries no character list")
        self.letters_classes = ["blank", *meta["character"].splitlines(), " "]
        self.version = f"ppocrv5-det+easyocr-arabic_g1+english_g2+ppocrv5-arabic (onnxruntime {ort.__version__})"


_MODELS: dict[tuple[str, int], _Models] = {}
_MODELS_LOCK = threading.Lock()


def _models(directory: Path, threads: int) -> _Models:
    key = (str(directory.resolve()), threads)
    with _MODELS_LOCK:
        loaded = _MODELS.get(key)
        if loaded is None:
            loaded = _Models(directory, threads)
            _MODELS[key] = loaded
        return loaded


def _threads() -> int:
    """The worker sets OMP_NUM_THREADS (1 in the document worker)."""
    try:
        return max(1, min(8, int(os.environ.get("OMP_NUM_THREADS") or 1)))
    except ValueError:
        return 1


def _engine_errors() -> tuple[type[BaseException], ...]:
    """What loading or running the models raises when a model or an image is
    bad: ONNX Runtime's own errors (they derive from ``Exception`` directly),
    OpenCV's, pyclipper's, and the array errors of malformed input. A page or
    cell that hits one fails as an OCR error; anything else is a defect and
    surfaces as one."""
    errors: list[type[BaseException]] = [
        RuntimeError, ValueError, LookupError, TypeError, ArithmeticError, MemoryError, OSError,
    ]
    try:
        from onnxruntime.capi import onnxruntime_pybind11_state as state

        errors += [value for value in vars(state).values()
                   if isinstance(value, type) and issubclass(value, Exception)]
    except ImportError:
        pass
    try:
        import cv2

        errors.append(cv2.error)
    except ImportError:
        pass
    try:
        import pyclipper

        errors.append(pyclipper.ClipperException)
    except (ImportError, AttributeError):
        pass
    return tuple(errors)


# ── detection (PP-OCRv5, the DB pipeline as RapidOCR 3.10 runs it) ──────────

def _resize32(image: Any, ratio: float) -> Any:
    import cv2

    h, w = image.shape[:2]
    resize_h = int(round(int(h * ratio) / 32) * 32)
    resize_w = int(round(int(w * ratio) / 32) * 32)
    if resize_h <= 0 or resize_w <= 0:
        return None
    return cv2.resize(image, (resize_w, resize_h))


def _detect(models: _Models, bgr: Any) -> list[Box]:
    """Text boxes (axis-aligned) in the page's own pixels."""
    import cv2
    import numpy as np
    import pyclipper

    page_h, page_w = bgr.shape[:2]
    # The page within 30..2000 pixels a side (RapidOCR's Global bounds).
    image, ratio_h, ratio_w = bgr, 1.0, 1.0
    if max(page_h, page_w) > 2000:
        image = _resize32(bgr, 2000.0 / max(page_h, page_w))
    if image is not None and min(image.shape[:2]) < 30:
        image = _resize32(image, 30.0 / min(image.shape[:2]))
    if image is None:
        return []
    ratio_h, ratio_w = page_h / image.shape[0], page_w / image.shape[1]
    # A very wide or very short image gets black bands above and below.
    top = 0
    h, w = image.shape[:2]
    if h <= 30 or w / h > 8:
        new_h = max(int(w / 8), 30) * 2
        top = int(abs(new_h - h) / 2)
        image = cv2.copyMakeBorder(image, top, top, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    src_h, src_w = image.shape[:2]
    ratio = 736.0 / min(src_h, src_w) if min(src_h, src_w) < 736 else 1.0
    resized = _resize32(image, ratio)
    if resized is None:
        return []
    tensor = ((resized.astype("float32") / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[None].astype("float32")
    pred = models.det.run(None, {models.det.get_inputs()[0].name: tensor})[0][0, 0]
    height, width = pred.shape
    mask = cv2.dilate((pred > 0.3).astype(np.uint8), np.array([[1, 1], [1, 1]]))
    contours = cv2.findContours((mask * 255).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    contours = contours[1] if len(contours) == 3 else contours[0]

    def mini_box(contour: Any) -> tuple[Any, float]:
        rect = cv2.minAreaRect(contour)
        points = sorted(list(cv2.boxPoints(rect)), key=lambda p: p[0])
        i1, i4 = (0, 1) if points[1][1] > points[0][1] else (1, 0)
        i2, i3 = (2, 3) if points[3][1] > points[2][1] else (3, 2)
        return np.array([points[i1], points[i2], points[i3], points[i4]]), min(rect[1])

    boxes: list[Box] = []
    for contour in contours[:1000]:
        points, side = mini_box(contour)
        if side < 3:
            continue
        x_min = int(np.clip(np.floor(points[:, 0].min()), 0, width - 1))
        x_max = int(np.clip(np.ceil(points[:, 0].max()), 0, width - 1))
        y_min = int(np.clip(np.floor(points[:, 1].min()), 0, height - 1))
        y_max = int(np.clip(np.ceil(points[:, 1].max()), 0, height - 1))
        score_mask = np.zeros((y_max - y_min + 1, x_max - x_min + 1), dtype=np.uint8)
        shifted = points.copy()
        shifted[:, 0] -= x_min
        shifted[:, 1] -= y_min
        cv2.fillPoly(score_mask, shifted.reshape(1, -1, 2).astype(np.int32), 1)
        if cv2.mean(pred[y_min:y_max + 1, x_min:x_max + 1], score_mask)[0] < 0.5:
            continue
        # Unclip by area x 1.6 / perimeter, as the DB paper and PaddleOCR do.
        xs, ys = points[:, 0], points[:, 1]
        area = 0.5 * abs(float(np.dot(xs, np.roll(ys, 1)) - np.dot(ys, np.roll(xs, 1))))
        perimeter = float(np.sum(np.hypot(np.diff(np.append(xs, xs[0])), np.diff(np.append(ys, ys[0])))))
        if perimeter <= 0:
            continue
        offset = pyclipper.PyclipperOffset()
        offset.AddPath(points, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        expanded = offset.Execute(area * 1.6 / perimeter)
        if not expanded:
            continue
        box, side = mini_box(np.array(expanded).reshape((-1, 1, 2)))
        if side < 5:
            continue
        box[:, 0] = np.clip(np.round(box[:, 0] / width * src_w), 0, src_w)
        box[:, 1] = np.clip(np.round(box[:, 1] / height * src_h), 0, src_h)
        box = np.clip(box, 0, [src_w - 1, src_h - 1]).astype(np.int32)
        x0, x1 = int(box[:, 0].min()), int(box[:, 0].max())
        y0, y1 = int(box[:, 1].min()), int(box[:, 1].max())
        if x1 - x0 <= 3 or y1 - y0 <= 3:
            continue
        boxes.append((
            max(0.0, min(page_w, x0 * ratio_w)), max(0.0, min(page_h, (y0 - top) * ratio_h)),
            max(0.0, min(page_w, x1 * ratio_w)), max(0.0, min(page_h, (y1 - top) * ratio_h)),
        ))
    return boxes


# ── recognition ─────────────────────────────────────────────────────────────

def _softmax(values: Any) -> Any:
    import numpy as np

    shifted = values - values.max(axis=2, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=2, keepdims=True)


def _contrast(grey: Any, target: float) -> Any:
    """EasyOCR's ``adjust_contrast_grey``: stretch a low-contrast crop."""
    import numpy as np

    high, low = np.percentile(grey, 90), np.percentile(grey, 10)
    if (high - low) / np.maximum(10, high + low) < target:
        stretched = (grey.astype(int) - low + 25) * (200.0 / np.maximum(10, high - low))
        grey = np.clip(stretched, 0, 255).astype(np.uint8)
    return grey


Reading = tuple[str, float, float, tuple[tuple[int, str], ...]]


def _second_readings(probs: Any, index: Any, keep: Any, classes: str, text: str) -> tuple[tuple[int, str], ...]:
    """Per emitted letter, EasyOCR's second reading at the frame it was
    surest of: (position in ``text``, letter), when it reaches
    ``_ALTERNATIVE`` and is a letter of the same bidi class (a swap that
    leaves the line's order as it was)."""
    import numpy as np

    out: list[tuple[int, str]] = []
    starts = np.flatnonzero(keep)
    for position, start in enumerate(starts):
        cls = int(index[start])
        end = int(start) + 1
        while end < len(index) and int(index[end]) == cls:
            end += 1
        frame = int(start) + int(np.argmax(probs[0, start:end, cls]))
        row = probs[0, frame].copy()
        row[0] = row[cls] = 0.0
        second = int(row.argmax())
        if second == 0 or row[second] < _ALTERNATIVE:
            continue
        letter, emitted = classes[second - 1], text[position]
        if (letter != emitted and letter.isalpha() and emitted.isalpha()
                and unicodedata.bidirectional(letter) == unicodedata.bidirectional(emitted)):
            out.append((position, letter))
    return tuple(out)


def _easyocr_read(session: Any, classes: str, ignored: frozenset[str], crop: Any) -> Reading:
    """EasyOCR's recognition of one grey crop (get_image_list + AlignCollate +
    greedy CTC, with its low-confidence contrast retry), left to right:
    (text, EasyOCR's own score, mean character probability, its second
    readings of doubted letters -- ``_second_readings``)."""
    import cv2
    import numpy as np
    from PIL import Image

    height, width = crop.shape[:2]
    if height <= 0 or width <= 0:
        return "", 0.0, 0.0, ()
    ratio = width / height
    if ratio < 1.0:
        ratio = 1.0 / ratio
        resized = cv2.resize(crop, (64, int(64 * ratio)), interpolation=cv2.INTER_LINEAR)
    else:
        resized = cv2.resize(crop, (int(64 * ratio), 64), interpolation=cv2.INTER_LINEAR)
    image_width = math.ceil(max(ratio, 1.0)) * 64
    # EasyOCR zeroes each ignored character's FIRST class (str.index); a
    # character listed twice keeps its second class, and so does this.
    ignore = sorted({classes.index(char) + 1 for char in ignored if char in classes})

    def read(contrast: bool) -> Reading:
        image = _contrast(resized, 0.5) if contrast else resized
        pil = Image.fromarray(image, "L")
        w, h = pil.size
        target = min(image_width, math.ceil(64 * (w / float(h))))
        scaled = np.asarray(pil.resize((target, 64), Image.BICUBIC), dtype=np.float32) / 255.0
        scaled = (scaled - 0.5) / 0.5
        batch = np.zeros((1, 1, 64, image_width), dtype=np.float32)
        batch[0, 0, :, :target] = scaled
        if target < image_width:
            batch[0, 0, :, target:] = scaled[:, target - 1:target]
        logits = session.run(None, {session.get_inputs()[0].name: batch})[0]
        probs = _softmax(logits.astype(np.float32))
        if ignore:
            probs[:, :, ignore] = 0.0
            probs = probs / probs.sum(axis=2, keepdims=True)
        index = probs.argmax(axis=2)[0]
        keep = np.insert(index[1:] != index[:-1], 0, True) & (index != 0)
        text = "".join(classes[i - 1] for i in index[keep])
        best = probs.max(axis=2)[0][index != 0]
        if len(best) == 0:
            best = np.array([0.0])
        # EasyOCR's own score decides the contrast retry, as EasyOCR does;
        # the confidence reported is the mean character probability, the
        # scale Tesseract's and PaddleOCR's confidences are on.
        score = float(best.prod() ** (2.0 / np.sqrt(len(best))))
        return text, score, float(best.mean()), _second_readings(probs, index, keep, classes, text)

    first = read(False)
    if first[1] < 0.1:
        second = read(True)
        if second[1] >= first[1]:
            first = second
    return first


def _paddle_read(models: _Models, crop: Any) -> tuple[str, float]:
    """PP-OCRv5 Arabic recognition of one grey crop (RapidOCR's preprocessing), left to right."""
    import cv2
    import numpy as np

    height, width = crop.shape[:2]
    if height <= 0 or width <= 0:
        return "", 0.0
    image = np.stack([crop] * 3, axis=-1)
    max_ratio = max(320 / 48, width / height)
    image_width = int(48 * max_ratio)
    resized_w = image_width if math.ceil(48 * width / height) > image_width else int(math.ceil(48 * width / height))
    resized = cv2.resize(image, (resized_w, 48)).astype("float32").transpose(2, 0, 1) / 255.0
    resized = (resized - 0.5) / 0.5
    batch = np.zeros((1, 3, 48, image_width), dtype=np.float32)
    batch[0, :, :, :resized_w] = resized
    preds = models.rec_letters.run(None, {models.rec_letters.get_inputs()[0].name: batch})[0][0]
    index = preds.argmax(axis=1)
    keep = np.ones(len(index), dtype=bool)
    keep[1:] = index[1:] != index[:-1]
    keep &= index != 0
    classes = models.letters_classes
    text = "".join(classes[i] for i in index[keep] if i < len(classes))
    confidence = float(np.mean(preds.max(axis=1)[keep])) if keep.any() else 0.0
    return text, confidence


def _stroke_width(ink: Any) -> float:
    """The pen's width: the median horizontal run of ink (0 with no ink)."""
    import numpy as np

    padded = np.pad(ink.astype(np.int8), ((0, 0), (1, 1)))
    steps = np.diff(padded, axis=1)
    starts, ends = np.flatnonzero(steps == 1), np.flatnonzero(steps == -1)
    return float(np.median(ends - starts)) if len(starts) else 0.0


def _remove_specks(page: Any, box: Box) -> int:
    """Whiten, in ``page`` (writable), the ink specks in one text line's box;
    returns how many.

    A speck is ink smaller than any mark the line's own pen draws: area under
    half the stroke width squared, and no side longer than a stroke. Measured
    on the fidelity corpus (2026-10-15): specks of a poor scan are 1-3 px of
    area; the smallest real mark -- a blurred full stop on that scan, stroke
    3 -- is 6, and dots on clean 300 dpi pages (stroke 3-4) are 9 and up.
    The stroke is the line's own (a heading's pen is not a footnote's), and
    only the line's box is cleaned (detection draws it well past the ink, and
    the crop is cut to the ink inside it): a neighbouring line in a smaller
    type keeps its dots. Ink that continues past the box (one pixel around
    it) is never a speck: it is the end of a neighbour's letter or rule. A
    speck ON the box's edge is still one."""
    import cv2
    import numpy as np

    page_h, page_w = page.shape[:2]
    x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
    x1, y1 = min(page_w, int(box[2])), min(page_h, int(box[3]))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return 0
    stroke = _stroke_width(page[y0:y1, x0:x1] < _INK)
    if stroke < 2:
        return 0
    # The box and a one-pixel ring around it: a component reaching the
    # ring's outer edge (or the page's) goes on outside the box.
    region = page[max(0, y0 - 1):y1 + 1, max(0, x0 - 1):x1 + 1]
    _count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        (region < _INK).astype(np.uint8), connectivity=8)
    left, upper = stats[1:, cv2.CC_STAT_LEFT], stats[1:, cv2.CC_STAT_TOP]
    width, height = stats[1:, cv2.CC_STAT_WIDTH], stats[1:, cv2.CC_STAT_HEIGHT]
    inside = (left > 0) & (upper > 0) & (left + width < region.shape[1]) & (upper + height < region.shape[0])
    small = (stats[1:, cv2.CC_STAT_AREA] < stroke * stroke / 2) & (np.maximum(width, height) <= stroke)
    specks = np.flatnonzero(inside & small) + 1
    if not len(specks):
        return 0
    mask = np.isin(labels, specks)
    # The speck's light halo (a scan's blur, JPEG) goes with it.
    halo = cv2.dilate(mask.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & (labels == 0)
    region[mask | halo] = 255
    return int(len(specks))


def _ink_rows(grey: Any, box: Box) -> tuple[int, int, int, int] | None:
    """The box in integer pixels, cut vertically to its ink (with a margin)."""
    import numpy as np

    page_h, page_w = grey.shape[:2]
    x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
    x1, y1 = min(page_w, int(box[2])), min(page_h, int(box[3]))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    rows = np.nonzero((grey[y0:y1, x0:x1] < _INK).sum(axis=1))[0]
    if len(rows):
        margin = max(2, (rows[-1] - rows[0] + 1) // 8)
        y0, y1 = max(0, y0 + int(rows[0]) - margin), min(page_h, y0 + int(rows[-1]) + 1 + margin)
    return x0, y0, x1, y1


def _ink_crop(grey: Any) -> tuple[Any, Box] | None:
    """A cell's ink, tight with a margin, and its box; None when empty."""
    import numpy as np

    ys, xs = np.nonzero(grey < _INK)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    margin = max(4, (y1 - y0) // 6)
    x0, y0 = max(0, x0 - margin), max(0, y0 - margin)
    x1, y1 = min(grey.shape[1], x1 + margin), min(grey.shape[0], y1 + margin)
    return grey[y0:y1, x0:x1], (float(x0), float(y0), float(x1), float(y1))


class NeuralOcrProvider:
    """PP-OCRv5 detection + EasyOCR recognition (+ letter vote), ONNX Runtime."""

    engine = ENGINE
    #: tables.read_tables reads a numeric cell once more as digits; here with
    #: the English recognizer, kept only when EasyOCR's own score is >= 0.75
    #: (measured: bold European digits in a contents table 0.66 -> 1.0 of
    #: them read; an English reading of an Arabic-Indic number never passes).
    digit_pass = True
    #: ``recognize`` takes ``skip_regions``: the page pass leaves the lines
    #: inside ruled tables to the table pass (each line costs a recognizer
    #: run; tables.with_tables drops them anyway).
    skips_table_regions = True

    def __init__(self, model_dir: Path | str | None = None, *, tesseract: Any = None) -> None:
        self.model_dir = Path(model_dir) if model_dir is not None else model_directory()
        self._tesseract = tesseract
        self._capability: OcrCapability | None = None

    # -- readiness ------------------------------------------------------------
    def probe(self, requested_languages: tuple[str, ...] = ()) -> OcrCapability:
        if self._capability is None:
            problem = _runtime_problem()
            status = verify_model_directory(self.model_dir) if problem is None else None
            if problem is None and status is not None and not status.ready:
                problem = status.reason
            self._capability = OcrCapability(
                "neural-onnx", ENGINE, status.version if status else None, ("ara", "eng"),
                ("text", "bounding_boxes", "confidence", "unicode", "arabic_indic_digits"),
                OcrReadiness.UNAVAILABLE if problem else OcrReadiness.READY, problem,
            )
        base = self._capability
        missing = sorted(set(requested_languages) - set(base.languages))
        if missing and base.available:
            return OcrCapability(base.provider, base.engine, base.engine_version, base.languages, base.features,
                                 OcrReadiness.UNAVAILABLE,
                                 f"The neural OCR engine does not read: {', '.join(missing)}")
        return base

    def _ready_models(self) -> _Models:
        capability = self.probe()
        if not capability.available:
            raise DocumentOcrUnavailableError(capability.reason or "The neural OCR engine is unavailable")
        try:
            return _models(self.model_dir, _threads())
        except DocumentOcrUnavailableError:
            raise
        except _engine_errors() as exc:  # a model that will not load
            raise DocumentOcrUnavailableError(
                f"The neural OCR models could not be loaded ({type(exc).__name__})") from exc

    def _tesseract_provider(self) -> Any:
        if self._tesseract is None:
            from .tesseract import TesseractProvider

            self._tesseract = TesseractProvider()
        return self._tesseract

    # -- reading ----------------------------------------------------------------
    @staticmethod
    def _load(image_path: Path, max_pixels: int) -> tuple[Any, Any]:
        import numpy as np
        from PIL import Image

        try:
            with Image.open(image_path) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise DocumentLimitError("OCR image exceeds the configured pixel limit")
                rgb = image.convert("RGB")
                grey = np.asarray(image.convert("L"), dtype=np.uint8)
        except DocumentLimitError:
            raise
        except OSError as exc:
            raise DocumentOcrError("The OCR image cannot be read") from exc
        bgr = np.ascontiguousarray(np.asarray(rgb, dtype=np.uint8)[:, :, ::-1])
        return bgr, grey

    def _tesseract_words(self, image_path: Path, language: str, *, dpi: int, timeout_seconds: float,
                         output_limit_bytes: int, work_dir: Path) -> list[tuple[float, float, str]]:
        """Tesseract's words on the image (centre x, centre y, text), or none
        when Tesseract or its language data is missing: the vote then has two
        readers."""
        tesseract = self._tesseract_provider()
        wanted = "ara+eng" if "ara" in language.split("+") else "eng"
        capability = tesseract.probe()
        if not capability.available or not set(wanted.split("+")) <= set(capability.languages):
            return []
        try:
            return tesseract.words(image_path, language=wanted, dpi=dpi, timeout_seconds=timeout_seconds,
                                   output_limit_bytes=output_limit_bytes, work_dir=work_dir)
        except (DocumentOcrError, DocumentOcrUnavailableError, DocumentLimitError, OSError):
            return []

    def _read_lines(self, models: _Models, bgr: Any, grey: Any, language: str, *, deadline: float,
                    words: list[tuple[float, float, str]],
                    skip_regions: tuple[tuple[float, float, float, float], ...] = ()) -> list[_Line]:
        english_only = language.split("+") == ["eng"]
        lines: list[_Line] = []
        # Specks are removed line by line, each by its own line's pen, in a
        # copy: the page image itself is the caller's.
        clean = grey.copy()
        for box in _merge_line_boxes(_detect(models, bgr)):
            centre_x, centre_y = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            if any(r[0] <= centre_x <= r[2] and r[1] <= centre_y <= r[3] for r in skip_regions):
                continue
            if time.monotonic() > deadline:
                raise DocumentOcrError("Neural OCR exceeded its time limit", code="ocr_timeout")
            _remove_specks(clean, box)
            cut = _ink_rows(clean, box)
            if cut is None:
                continue
            x0, y0, x1, y1 = cut
            crop = clean[y0:y1, x0:x1]
            if english_only:
                visual, _score, confidence, _second = _easyocr_read(
                    models.rec_latin, _ENGLISH_G2, _ENGLISH_G2_IGNORED, crop)
                lines.append(_Line(box, confidence, _line_text(visual)))
                continue
            visual, _score, confidence, second = _easyocr_read(models.rec_arabic, _ARABIC_G1, _ARABIC_G1_IGNORED, crop)
            if _has_latin(visual) and not _has_arabic(visual):
                visual, _score, confidence, _second = _easyocr_read(
                    models.rec_latin, _ENGLISH_G2, _ENGLISH_G2_IGNORED, crop)
                lines.append(_Line(box, confidence, _line_text(visual)))
                continue
            text = _line_text(visual)
            if _has_arabic(text):
                letters, letters_confidence = _paddle_read(models, crop)
                inside = [word for (cx, cy, word) in words if box[0] <= cx <= box[2] and box[1] <= cy <= box[3]]
                text = _vote_words(text, _logical(letters), letters_confidence, inside, _variants(visual, second))
            lines.append(_Line(box, confidence, text))
        return lines

    def recognize(
        self,
        image_path: Path,
        *,
        page_number: int,
        language: str,
        dpi: int,
        max_pixels: int,
        timeout_seconds: float,
        output_limit_bytes: int,
        work_dir: Path,
        skip_regions: tuple[tuple[float, float, float, float], ...] = (),
    ) -> OcrPageResult:
        models = self._ready_models()
        bgr, grey = self._load(Path(image_path), max_pixels)
        deadline = time.monotonic() + _PAGE_BUDGET * max(1.0, float(timeout_seconds))
        words = [] if language.split("+") == ["eng"] else self._tesseract_words(
            Path(image_path), language, dpi=dpi, timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes, work_dir=work_dir)
        try:
            lines = self._read_lines(models, bgr, grey, language, deadline=deadline, words=words,
                                     skip_regions=tuple(skip_regions))
        except (DocumentOcrError, DocumentLimitError):
            raise
        except _engine_errors() as exc:  # a model run failure fails this page only
            raise DocumentOcrError(f"Neural OCR failed ({type(exc).__name__})") from exc
        blocks, confidence = _blocks_from_lines(lines, page_number=page_number, language=language,
                                               engine_version=models.version, dpi=dpi)
        return OcrPageResult(page_number=page_number, blocks=blocks, language=language, engine=ENGINE,
                             engine_version=models.version, dpi=dpi, confidence=confidence)

    def recognize_region(
        self,
        image_path: Path,
        *,
        language: str,
        dpi: int,
        psm: int,
        digits_only: bool,
        timeout_seconds: float,
        output_limit_bytes: int,
        work_dir: Path,
    ) -> OcrPageResult:
        """One table cell: a single line is read as one crop (``psm`` 7), a
        block (6) as a small page; ``digits_only`` reads it with the English
        recognizer and keeps only a reading made of digits."""
        if psm not in (6, 7):
            raise DocumentOcrError("Unsupported page segmentation mode for a region")
        models = self._ready_models()
        bgr, grey = self._load(Path(image_path), 1 << 31)

        def result(lines: list[_Line]) -> OcrPageResult:
            blocks, confidence = _blocks_from_lines(lines, page_number=1, language=language,
                                                   engine_version=models.version, dpi=dpi)
            return OcrPageResult(page_number=1, blocks=blocks, language=language, engine=ENGINE,
                                 engine_version=models.version, dpi=dpi, confidence=confidence)

        try:
            if psm == 7 or digits_only:
                grey = grey.copy()
                _remove_specks(grey, (0.0, 0.0, float(grey.shape[1]), float(grey.shape[0])))
                cut = _ink_crop(grey)
                if cut is None:
                    return result([])
                crop, box = cut
                if digits_only or language.split("+") == ["eng"]:
                    visual, score, confidence, second = _easyocr_read(
                        models.rec_latin, _ENGLISH_G2, _ENGLISH_G2_IGNORED, crop)
                else:
                    visual, score, confidence, second = _easyocr_read(
                        models.rec_arabic, _ARABIC_G1, _ARABIC_G1_IGNORED, crop)
                # The digits-only reading stands only when it IS digits and
                # EasyOCR itself is sure of it (its own score, the scale the
                # pass was measured on): an English reading of Arabic-Indic
                # digits is confident by character and wrong as a number.
                if digits_only and not (visual.strip() and set(visual.strip()) <= _DIGIT_PASS_CHARS
                                        and score >= _DIGIT_PASS_SCORE):
                    return result([])
                text = _line_text(visual)
                if not digits_only and _has_arabic(text):
                    # A cell's words get the same letter vote as a page's lines.
                    letters, letters_confidence = _paddle_read(models, crop)
                    words = self._tesseract_words(
                        Path(image_path), language, dpi=dpi, timeout_seconds=timeout_seconds,
                        output_limit_bytes=output_limit_bytes, work_dir=work_dir)
                    text = _vote_words(text, _logical(letters), letters_confidence, [w for _x, _y, w in words],
                                       _variants(visual, second))
                return result([_Line(box, confidence, text)])
            deadline = time.monotonic() + _PAGE_BUDGET * max(1.0, float(timeout_seconds))
            words = [] if language.split("+") == ["eng"] else self._tesseract_words(
                Path(image_path), language, dpi=dpi, timeout_seconds=timeout_seconds,
                output_limit_bytes=output_limit_bytes, work_dir=work_dir)
            return result(self._read_lines(models, bgr, grey, language, deadline=deadline, words=words))
        except (DocumentOcrError, DocumentLimitError):
            raise
        except _engine_errors() as exc:  # a model run failure fails this cell only
            raise DocumentOcrError(f"Neural OCR failed ({type(exc).__name__})") from exc
