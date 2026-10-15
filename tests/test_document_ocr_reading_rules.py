"""How an OCR reading becomes text: numbers, punctuation, letters and marks.

2026-10-15 (Q02, the fidelity corpus's last misses). The neural engine read
the corpus's Arabic page as "ريالأ" for "ريالاً", "٣,٧٥" for "٣٫٧٥",
"التغليف ." for "التغليف." and lost the final full stop; on the table page
"ربالأ"; on the degraded English scan "3,75" for "3.75". Each had a cause,
and each rule here was measured against it, with its negative control (the
old behaviour fails the test):

* no recognizer Kazma reads numbers with can write ٫ or ٬ (EasyOCR
  ``arabic_g1``, Tesseract ``ara``): a "," or "." between Arabic-Indic digits
  is put back by a number's grammar, in both engines' readings; a separator
  before exactly three digits stays as read (thousands or fils), and
  European digits are never touched;
* EasyOCR reads the gap a font leaves before a full stop as a space: it goes;
* the letter vote is over letters: PaddleOCR drops a full stop, so a word's
  punctuation is EasyOCR's;
* a letter EasyOCR doubted (its second reading >= 0.1) is taken from another
  reader that reads EasyOCR's own second reading -- two readers agree;
* EasyOCR writes no harakat: the tanween over an alef comes out as a hamza,
  and the harakat another reader saw there replace it; a mark only another
  reader saw is never added (Tesseract writes marks that are not there);
* ink smaller than the line's own pen is a speck and is removed before the
  line is read (a speck beside a decimal point made "3.75" read "3,75"); real
  marks and ink continuing past the line's box stay;
* a line's last mark is two readers' (the same day, after the live install
  read "placed under review:"): EasyOCR says THAT a mark is there, Tesseract's
  reading of the same word says WHICH -- one mark; a mark Tesseract alone
  reads is never added, a number's own marks are never touched, and on an
  Arabic line Tesseract's "," (``ara`` has no "،") is a comma of either form.

The end-to-end readings need the neural engine's packages and models (and,
for the Arabic page, LibreOffice and Tesseract with ``ara``); without them
they SKIP -- a skip is not evidence.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from kazma_core.documents import arabic
from kazma_core.documents.ocr import neural, neural_models

# ── the old rules (HEAD before 2026-10-15), the negative controls ──────────


def _old_vote_words(easy: str, letters: str, letters_confidence: float, tesseract: list[str]) -> str:
    import difflib

    fold = arabic.fold_for_search
    words = easy.split()
    if not words:
        return easy
    folded = [fold(word) for word in words]

    def aligned(other: list[str]) -> dict[int, str]:
        mapping: dict[int, str] = {}
        theirs = [fold(word) for word in other]
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, folded, theirs, autojunk=False).get_opcodes():
            if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
                for k in range(i2 - i1):
                    mapping[i1 + k] = other[j1 + k]
        return mapping

    by_letters, by_tesseract = aligned(letters.split()), aligned(tesseract)
    out = []
    for index, word in enumerate(words):
        if neural._has_digit(word):
            out.append(word)
            continue
        other, third = by_letters.get(index), by_tesseract.get(index)
        if (other is not None and third is not None and not neural._has_digit(other)
                and fold(other) == fold(third)):
            out.append(other)
        elif other is not None and letters_confidence >= 0.9 and not neural._has_digit(other):
            out.append(other)
        else:
            out.append(word)
    return " ".join(out)


def _old_rules(monkeypatch) -> None:
    monkeypatch.setattr(neural, "_line_text", neural._logical)
    monkeypatch.setattr(neural, "_vote_words",
                        lambda easy, letters, conf, tesseract, variants=None: _old_vote_words(
                            easy, letters, conf, tesseract))
    monkeypatch.setattr(neural, "_remove_specks", lambda page, box: 0)
    monkeypatch.setattr(arabic, "arabic_number_separators", lambda text: text)


# ── Arabic separators ─────────────────────────────────────────────────────

@pytest.mark.parametrize(("read", "expected"), [
    ("انخفضت نسبة المرتجعات إلى ٣,٧٥ بالمئة", "انخفضت نسبة المرتجعات إلى ٣٫٧٥ بالمئة"),
    ("٣.٧٥", "٣٫٧٥"),
    ("١٢٣٤,٥", "١٢٣٤٫٥"),
    ("١,٢٣٤,٥٦٧,٨٩", "١٬٢٣٤٬٥٦٧٫٨٩"),
    ("١,٢٣٤,٥٦٧.٨٩", "١٬٢٣٤٬٥٦٧٫٨٩"),
    ("۳,۷۵", "۳٫۷۵"),
    ("(٣,٧٥).", "(٣٫٧٥)."),
])
def test_a_stand_in_mark_between_arabic_indic_digits_is_put_back(read, expected):
    assert arabic.arabic_number_separators(read) == expected
    # Negative control: the reading as it was kept the recognizer's mark.
    assert read != expected


@pytest.mark.parametrize("read", [
    "١٢,٣٤٥",           # thousands, or three decimal places (fils): kept as read
    "١,٢٣٤,٥٦٧",        # the last group of three decides nothing either
    "٣٠.٠٩.٢٠٢٦",       # a date: its inner groups are not of three
    "١٢٣٤,٥٦٧,٨٩",      # a grouped number's head is at most three digits
    "بنسبة 99.95 بالمئة",  # European digits keep the European marks
    "3,75",
    "1,٢٣",             # mixed digits: not one Arabic-Indic number
    "٣,٧٥.5",
    "٣٫٧٥",             # already Arabic
])
def test_what_a_number_s_grammar_cannot_decide_stays_as_read(read):
    assert arabic.arabic_number_separators(read) == read


def test_both_engines_put_the_separator_back(monkeypatch):
    from kazma_core.documents.ocr.tesseract import _parse_tsv

    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    words = ["انخفضت", "إلى", "٣,٧٥", "بالمئة"]
    rows = "".join(f"5\t1\t1\t1\t1\t{i}\t{900 - 100 * i}\t10\t80\t40\t90\t{w}\n" for i, w in enumerate(words, 1))
    tesseract = _parse_tsv((header + rows).encode("utf-8"), page_number=1, language="ara",
                           engine_version="5", dpi=300)
    line = neural._Line((100.0, 10.0, 1400.0, 50.0), 0.9, "انخفضت إلى ٣,٧٥ بالمئة")
    blocks, _ = neural._blocks_from_lines([line], page_number=1, language="ara", engine_version=None, dpi=300)
    assert "٣٫٧٥" in tesseract.blocks[0].text and "٣٫٧٥" in blocks[0].text
    # Negative control: without the rule both keep the ASCII comma.
    monkeypatch.setattr(arabic, "arabic_number_separators", lambda text: text)
    tesseract = _parse_tsv((header + rows).encode("utf-8"), page_number=1, language="ara",
                           engine_version="5", dpi=300)
    blocks, _ = neural._blocks_from_lines([line], page_number=1, language="ara", engine_version=None, dpi=300)
    assert "٣,٧٥" in tesseract.blocks[0].text and "٣,٧٥" in blocks[0].text


def test_no_reader_kazma_reads_numbers_with_writes_the_arabic_separators():
    # The rule's premise, held: EasyOCR's arabic_g1 classes have neither.
    assert arabic.ARABIC_DECIMAL_SEPARATOR not in neural._ARABIC_G1
    assert arabic.ARABIC_THOUSANDS_SEPARATOR not in neural._ARABIC_G1
    assert "," in neural._ARABIC_G1 and "." in neural._ARABIC_G1


# ── punctuation ───────────────────────────────────────────────────────────

def test_a_space_read_before_a_full_stop_goes():
    drawn = ". فيلغتلا رييغت"  # EasyOCR's reading, left to right, with the font's gap read as a space
    assert neural._line_text(drawn) == "تغيير التغليف."
    assert neural._logical(drawn) == "تغيير التغليف ."  # negative control: the old line text
    assert neural._line_text("the end ,and more") == "the end ,and more"  # not before a word
    for kept in ("Bonjour !", "voici :", "a word ...", "about .5 of it"):
        assert neural._line_text(kept) == kept


def test_the_vote_is_over_letters_and_the_punctuation_is_easyocr_s():
    easy, letters = "ووضعا تحت المراجعة.", "ووضعا تحت المراجعة"  # PaddleOCR drops the full stop
    assert neural._vote_words(easy, letters, 0.97, []) == "ووضعا تحت المراجعة."
    assert _old_vote_words(easy, letters, 0.97, []) == "ووضعا تحت المراجعة"  # negative control
    # Agreement is over letters too: Tesseract's "النهائي." agrees with PaddleOCR's "النهائي".
    easy, letters, tess = "الموعد الذهائي.", "الموعد النهائي", ["الموعد", "النهائي."]
    assert neural._vote_words(easy, letters, 0.5, tess) == "الموعد النهائي."
    assert _old_vote_words(easy, letters, 0.5, tess) == "الموعد الذهائي."


# ── EasyOCR's own second reading ──────────────────────────────────────────

def _probabilities(frames: list[dict[int, float]], classes: int):
    np = pytest.importorskip("numpy")
    probs = np.full((1, len(frames), classes + 1), 1e-6, dtype=np.float32)
    for t, row in enumerate(frames):
        for cls, p in row.items():
            probs[0, t, cls] = p
    return probs / probs.sum(axis=2, keepdims=True)


_CLASSES = "بي1"  # 1 ب, 2 ي, 3 the digit
_FRAMES = [
    {0: 1.0},
    {2: 0.7, 1: 0.28, 0: 0.02},   # ي, ب second at 0.28: kept
    {0: 1.0},
    {1: 0.91, 2: 0.09},           # ب, ي second at 0.09: below 0.1
    {3: 0.6, 2: 0.4},             # a digit: its second reading is no letter of it
]


def test_easyocr_s_second_reading_of_a_doubted_letter_is_kept():
    np = pytest.importorskip("numpy")
    probs = _probabilities(_FRAMES, len(_CLASSES))
    index = probs.argmax(axis=2)[0]
    keep = np.insert(index[1:] != index[:-1], 0, True) & (index != 0)
    text = "".join(_CLASSES[i - 1] for i in index[keep])
    assert text == "يب1"
    assert neural._second_readings(probs, index, keep, _CLASSES, text) == ((0, "ب"),)


def test_the_recognizer_returns_its_second_readings():
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    probs = _probabilities(_FRAMES, len(_CLASSES))

    class Session:
        def get_inputs(self):
            return [type("Input", (), {"name": "image"})()]

        def run(self, _outputs, _feed):
            return [np.log(probs)]

    crop = np.full((32, 96), 255, dtype=np.uint8)
    reading = neural._easyocr_read(Session(), _CLASSES, frozenset(), crop)
    assert (reading.text, reading.second) == ("يب1", ((0, "ب"),))
    # An empty crop is an empty reading of the same shape.
    assert neural._easyocr_read(Session(), _CLASSES, frozenset(), crop[:0]) == neural.Reading("", 0.0, 0.0)


def test_a_second_reading_counts_only_when_another_reader_reads_it():
    pytest.importorskip("bidi.algorithm")
    drawn = "يف ألاير"  # "ريالأ في" drawn left to right
    assert neural._logical(drawn) == "ريالأ في"
    variants = neural._variants(drawn, ((drawn.rindex("ي"), "ب"),))
    assert variants == {0: {"ربالأ"}}
    # The table page: EasyOCR read ب (0.74), its second reading ي (0.26) is
    # Tesseract's word -- two readers agree; Tesseract's tanween comes too.
    easy, tess = "ربالأ في", ["ريالاً", "في"]
    assert neural._vote_words(easy, "", 0.0, tess, {0: {"ريالأ"}}) == "ريالاً في"
    assert neural._vote_words(easy, "", 0.0, tess) == "ربالأ في"  # without EasyOCR's second reading
    assert _old_vote_words(easy, "", 0.0, tess) == "ربالأ في"  # negative control
    # A second reading no other reader reads changes nothing.
    assert neural._vote_words(easy, "", 0.0, ["كتاب", "في"], {0: {"ريالأ"}}) == "ربالأ في"


# ── marks ─────────────────────────────────────────────────────────────────

def test_the_hamza_easyocr_wrote_for_a_tanween_takes_the_reader_s_harakat():
    easy, tess = "ريالأ في", ["ريالاً", "في"]
    assert neural._vote_words(easy, "", 0.0, tess) == "ريالاً في"
    assert _old_vote_words(easy, "", 0.0, tess) == "ريالأ في"  # negative control
    # A mark only another reader saw is never added (Tesseract's "الشماليٍ").
    assert neural._vote_words("الشمالي", "", 0.0, ["الشماليٍ"]) == "الشمالي"
    # A reader that read EasyOCR's hamza too keeps it; so does a real hamza.
    assert neural._marks_from_readers("ريالأ", ["ريالاً", "ريالأ"]) == "ريالأ"
    assert neural._marks_from_readers("بدأ", ["بدأ"]) == "بدأ"
    # Marks on the other side of the letter do not stand for the hamza.
    assert neural._marks_from_readers("ريالأ", ["ريالاٍ"]) == "ريالأ"
    # Readers that disagree on the harakat leave EasyOCR's reading.
    assert neural._marks_from_readers("ريالأ", ["ريالاً", "ريالاَ"]) == "ريالأ"


# ── a line's last mark ────────────────────────────────────────────────────

def _current_vote(text: str, tesseract: list[str]) -> str:
    """What the engine did before the final-mark vote: the letter vote, which
    keeps EasyOCR's punctuation (the negative control)."""
    return neural._vote_words(text, "", 0.0, tesseract)


@pytest.mark.parametrize(("easy", "tesseract", "beyond", "written"), [
    # EasyOCR's English model reads a line-final "." as ":" (0.82 on the
    # clean corpus page), "," as ";".
    ("were placed under review:", ["were", "placed", "under", "review."], 0.0, "were placed under review."),
    ("improved by evening;", ["improved", "by", "evening,"], 0.0, "improved by evening,"),
    ("takes the evening route?,", ["takes", "the", "evening", "route?"], 0.0, "takes the evening route?"),
    # A mark EasyOCR gave >= 0.1 past its last letter, but did not write.
    ("after the packaging change", ["after", "the", "packaging", "change."], 0.37, "after the packaging change."),
    # Arabic: EasyOCR reads "؛" as ";" or ":"; Tesseract's ara writes "؛".
    ("وتفتح البوابة في الثامنة;", ["وتفتح", "البوابة", "في", "الثامنة؛"], 0.0, "وتفتح البوابة في الثامنة؛"),
    ("وجفت الطرق صباحا:", ["وجفت", "الطرق", "صباحا؛"], 0.0, "وجفت الطرق صباحا؛"),
    # Tesseract's "," on an Arabic line: ara cannot write "،".
    ("تحسنت التوقعات مساء.", ["تحسنت", "التوقعات", "مساء,"], 0.0, "تحسنت التوقعات مساء،"),
    ("تحسنت التوقعات مساء,", ["تحسنت", "التوقعات", "مساء,"], 0.0, "تحسنت التوقعات مساء,"),
])
def test_easyocr_says_a_mark_is_there_tesseract_says_which(easy, tesseract, beyond, written):
    assert neural._vote_final_mark(easy, tesseract, beyond) == written
    if easy != written:
        assert _current_vote(easy, tesseract) == easy  # negative control: the current vote keeps the misread


@pytest.mark.parametrize(("easy", "tesseract", "beyond"), [
    ("Findings", ["Findings."], 0.0),                                   # a heading: no mark is added
    ("النتائج", ["النتائج."], 0.0),
    ("during the drill", ["during", "the", "drill."], 0.04),            # EasyOCR saw no mark (< 0.1)
    ("before cleaning itl", ["before", "cleaning", "it!"], 0.0),        # not the same letters
    ("the same day;", ["the", "same", "day;."], 0.0),                   # Tesseract's two marks
    ("on 2026-09-30:", ["on", "30-09-2026."], 0.0),                     # another reading of the number
    ("fell to 3,75", ["fell", "to", "3.75"], 0.9),                     # a number's own mark: never touched
    ("the end.)", ["the", "end:)"], 0.0),                              # not a lone mark: left as read
    ("the end.", [], 0.0),                                              # no Tesseract
])
def test_what_two_readers_do_not_agree_on_stays_as_read(easy, tesseract, beyond):
    assert neural._vote_final_mark(easy, tesseract, beyond) == easy


def test_the_marks_easyocr_gave_past_each_end_of_a_line():
    np = pytest.importorskip("numpy")
    classes = "ab.:"  # 1 a, 2 b, 3 ".", 4 ":"
    probs = _probabilities([
        {0: 0.6, 4: 0.4},   # before the first letter: ":" 0.4
        {1: 1.0},
        {0: 0.9, 3: 0.1},   # between letters: not past an end
        {2: 1.0},
        {0: 0.63, 4: 0.37},  # past the last letter: ":" 0.37
        {0: 0.55, 3: 0.45},  # padding past the crop (stronger): not looked at
    ], len(classes))
    index = probs.argmax(axis=2)[0]
    keep = np.insert(index[1:] != index[:-1], 0, True) & (index != 0)
    left, right = neural._marks_beyond(probs, keep, classes, used=0.5)
    assert (round(left, 2), round(right, 2)) == (0.4, 0.37)
    # The trap the helper avoids: an integer, a slice and a list put the
    # list's axis first.
    assert probs[0, :, [3, 4]].shape == (2, 6) and probs[0][:, [3, 4]].shape == (6, 2)


def test_an_english_page_asks_tesseract_only_for_a_mark_to_vote_on(monkeypatch):
    pytest.importorskip("numpy")
    from types import SimpleNamespace

    boxes = [(10.0, 10.0, 300.0, 40.0), (10.0, 60.0, 300.0, 90.0)]
    texts = iter([])
    monkeypatch.setattr(neural, "_detect", lambda models, bgr: boxes)
    monkeypatch.setattr(neural, "_ink_rows", lambda grey, box: tuple(int(v) for v in box))
    monkeypatch.setattr(neural, "_remove_specks", lambda page, box: 0)
    monkeypatch.setattr(neural, "_easyocr_read", lambda *a: neural.Reading(next(texts), 0.9, 0.9))
    asked: list[int] = []

    def words():
        asked.append(1)
        return [(150.0, 75.0, "under"), (250.0, 75.0, "review.")]

    provider = neural.NeuralOcrProvider(Path("unused"))
    grey = __import__("numpy").full((120, 320), 255, dtype="uint8")
    models = SimpleNamespace(rec_latin=None, rec_arabic=None)
    texts = iter(["Findings", "under review:"])
    lines = provider._read_lines(models, None, grey, "eng", deadline=1e18, words=words)
    assert [line.text for line in lines] == ["Findings", "under review."] and asked == [1]
    texts = iter(["Findings", "Summary"])  # no line ends in a mark: Tesseract is not run
    asked.clear()
    provider._read_lines(models, None, grey, "eng", deadline=1e18, words=words)
    assert asked == []


# ── specks ────────────────────────────────────────────────────────────────

def _line(pen: int, dot: int):
    np = pytest.importorskip("numpy")
    page = np.full((90, 420), 255, dtype=np.uint8)
    for x in range(20, 300, 16):
        page[25:55, x:x + pen] = 0       # the letters' strokes
    page[60:60 + dot, 310:310 + dot] = 0  # a full stop
    return page


def test_ink_smaller_than_the_line_s_pen_is_a_speck():
    pytest.importorskip("cv2")
    page = _line(pen=4, dot=5)
    page[30, 340] = 0                 # specks: 1 px, 2 x 1, 1 x 2
    page[40, 350:352] = 0
    page[50:52, 360] = 0
    page[10, 200] = 0                 # a speck ON the box's top edge
    page[2:12, 380:382] = 0           # a stroke continuing above the box
    box = (10.0, 10.0, 400.0, 75.0)
    assert neural._stroke_width(page[10:75, 10:400] < neural._INK) == 4
    before = page.copy()
    assert neural._remove_specks(page, box) == 4
    assert (page[30, 340], page[40, 350], page[50, 360], page[10, 200]) == (255, 255, 255, 255)
    assert (page[60:65, 310:315] == 0).all()        # the full stop stays
    assert (page[25:55, 20:24] == 0).all()          # the strokes stay
    assert (page[2:12, 380:382] == 0).all()         # ink going on past the box stays
    # Negative control: the old crop kept every speck.
    assert (before[30, 340], before[40, 350], before[50, 360]) == (0, 0, 0)


def test_the_pen_is_the_line_s_own():
    pytest.importorskip("cv2")
    # A line in a thin type: its 2 x 2 dots are its marks, not specks.
    thin = _line(pen=2, dot=2)
    assert neural._remove_specks(thin, (10.0, 10.0, 400.0, 75.0)) == 0
    assert (thin[60:62, 310:312] == 0).all()
    # The same dot judged by a heavier pen is one (negative control: the
    # threshold follows the stroke).
    heavy = _line(pen=4, dot=2)
    assert neural._remove_specks(heavy, (10.0, 10.0, 400.0, 75.0)) == 1


# ── the real engine on the corpus pages (models needed) ────────────────────

def _installed() -> Path:
    problem = neural._runtime_problem()
    if problem:
        pytest.skip(problem)
    directory = neural_models.model_directory()
    status = neural_models.verify_model_directory(directory)
    if not status.ready:
        pytest.skip(status.reason)
    return directory


def _page(case_id: str, tmp_path: Path) -> Path:
    from kazma_core.documents import fidelity

    case = next(c for c in fidelity.corpus() if c.case_id == case_id)
    if case.language == "ar" and not (shutil.which("soffice") or shutil.which("libreoffice")
                                      or Path("C:/Program Files/LibreOffice/program/soffice.exe").exists()):
        pytest.skip("LibreOffice is not installed (Arabic PDF layout route)")
    return fidelity.build_case(case, tmp_path / "corpus")


def _reader(directory: Path, monkeypatch):
    """The engine, with each model reading remembered: the negative control
    re-reads the page under the old rules from the same model outputs."""
    remembered: dict = {}
    easy, paddle = neural._easyocr_read, neural._paddle_read
    words = neural.NeuralOcrProvider._tesseract_words

    def once(key, read):
        if key not in remembered:
            remembered[key] = read()
        return remembered[key]

    monkeypatch.setattr(neural, "_easyocr_read", lambda s, c, i, crop: once(
        ("easy", c, crop.shape, crop.tobytes()), lambda: easy(s, c, i, crop)))
    monkeypatch.setattr(neural, "_paddle_read", lambda m, crop: once(
        ("paddle", crop.shape, crop.tobytes()), lambda: paddle(m, crop)))
    monkeypatch.setattr(neural.NeuralOcrProvider, "_tesseract_words", lambda self, path, language, **k: once(
        ("tesseract", str(path), language), lambda: words(self, path, language, **k)))
    provider = neural.NeuralOcrProvider(directory)

    def read(image: Path, language: str, work: Path) -> str:
        page = provider.recognize(image, page_number=1, language=language, dpi=300, max_pixels=200_000_000,
                                  timeout_seconds=120, output_limit_bytes=1 << 22, work_dir=work)
        return "\n".join(block.text for block in page.blocks)

    return read


def test_the_arabic_table_page_reads_as_written(tmp_path, monkeypatch):
    directory = _installed()
    from kazma_core.documents.ocr.tesseract import TesseractProvider

    if not TesseractProvider().probe(("ara",)).available:
        pytest.skip("Tesseract with 'ara' is not installed (the vote's third reader)")
    image = _page("ar-ocr-table", tmp_path)
    read = _reader(directory, monkeypatch)
    text = read(image, "ara", tmp_path)
    for written in ("ريالاً", "المناطق.", "٣٫٧٥", "التغليف.", "المراجعة."):
        assert written in text, (written, text)
    # The final-mark vote adds no mark the page does not have: the heading
    # and the footer end in none.
    assert "النتائج." not in text and "الاصطناعي." not in text, text
    # Negative control: the same model readings under the old rules.
    _old_rules(monkeypatch)
    old = read(image, "ara", tmp_path)
    for misread in ("ربالأ", "المناطق .", "٣,٧٥", "التغليف ."):
        assert misread in old, (misread, old)
    assert "المراجعة." not in old


def _tesseract_reads(language: str) -> None:
    from kazma_core.documents.ocr.tesseract import TesseractProvider

    if not TesseractProvider().probe((language,)).available:
        pytest.skip(f"Tesseract with '{language}' is not installed (the final-mark vote's second reader)")


def test_the_degraded_scan_reads_its_decimal_point(tmp_path, monkeypatch):
    directory = _installed()
    _tesseract_reads("eng")
    image = _page("en-ocr-degraded", tmp_path)
    read = _reader(directory, monkeypatch)
    text = read(image, "eng", tmp_path)
    assert "3.75" in text and "3,75" not in text, text
    assert "packaging change." in text and "under review." in text, text
    # Negative controls: without the final-mark vote EasyOCR's colon stands;
    # without the speck removal the speck reads as a comma.
    monkeypatch.setattr(neural, "_vote_final_mark", lambda line, tesseract, beyond: line)
    assert "under review:" in read(image, "eng", tmp_path)
    _old_rules(monkeypatch)
    assert "3,75" in read(image, "eng", tmp_path)


def test_a_clean_page_s_last_full_stop_is_read_as_written(tmp_path, monkeypatch):
    # The live install, 2026-10-15: "...and were placed under review:".
    directory = _installed()
    _tesseract_reads("eng")
    image = _page("en-ocr", tmp_path)
    read = _reader(directory, monkeypatch)
    text = read(image, "eng", tmp_path)
    assert "placed under review." in text and "review:" not in text, text
    monkeypatch.setattr(neural, "_vote_final_mark", lambda line, tesseract, beyond: line)
    assert "placed under review:" in read(image, "eng", tmp_path)  # negative control
