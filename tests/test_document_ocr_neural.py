"""The neural OCR engine (documents.ocr.engine: neural / auto), ocr/neural.py.

2026-10-15 (Q02): Tesseract reads neither Arabic letters well nor
Arabic-Indic digits at all. The engine that measured best -- PP-OCRv5 lines,
EasyOCR's recognizer, a letter vote with PaddleOCR's Arabic recognizer and
Tesseract -- runs on ONNX Runtime inside the document worker. These tests
hold each rule it was measured with, each with its negative control:

* a drawn line is put in logical order by Kazma's glyph-line rule, never by a
  recognizer's own ``get_display`` (which leaves "30-09-2026" reversed);
* detected word boxes of one line are one line again (per-word crops put
  stray characters between words);
* a word without a digit takes the two other readers' word when they agree,
  or PaddleOCR's when it is confident; a word with a digit is always
  EasyOCR's;
* the page pass skips lines inside ruled tables, and reads the page again
  in full when a table was not read cell by cell;
* near the worker's time limit the remaining pages go to Tesseract, said on
  the document;
* the models are used only when they match their manifest; the setting is
  validated; ``neural`` without the engine falls back to Tesseract, said on
  the document.

The end-to-end readings need the engine's packages and models (``python
scripts/ocr_models.py install``, or ``KAZMA_OCR_MODEL_DIR``); without them
they SKIP -- a skip is not evidence.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.models import DocumentIR, DocumentPage, Provenance, new_document_id, new_version_id
from kazma_core.documents.ocr import neural, neural_models, pipeline
from kazma_core.documents.ocr.base import OcrCapability, OcrPageResult, OcrReadiness

REPO = Path(__file__).resolve().parents[1]


# ── reading order ─────────────────────────────────────────────────────────

def test_a_drawn_arabic_line_is_read_in_logical_order():
    bidi = pytest.importorskip("bidi.algorithm")
    logical = "شحن ٤٢٥٠ وحدة في 2026-09-30."
    drawn = bidi.get_display(logical)  # the page, read left to right
    assert neural._logical(drawn) == logical
    # Negative control: the recognizers' own reordering keeps the date drawn.
    assert bidi.get_display(drawn) != logical
    assert "30-09-2026" in bidi.get_display(drawn)


def test_word_boxes_of_one_line_are_one_line_again():
    words = [(100.0, 50.0, 180.0, 90.0), (195.0, 52.0, 300.0, 88.0), (320.0, 51.0, 400.0, 90.0)]
    column = [(1200.0, 50.0, 1400.0, 90.0)]  # a second column, far away
    merged = neural._merge_line_boxes(words + column)
    assert (100.0, 50.0, 400.0, 90.0) in merged and (1200.0, 50.0, 1400.0, 90.0) in merged
    assert len(merged) == 2
    # Negative control: with no gap allowed the words stay apart.
    assert len(neural._merge_line_boxes(words + column, gap=0.0)) == 4


def test_a_row_reads_right_to_left_when_it_is_arabic():
    line = neural._Line
    arabic = [line((1000.0, 10.0, 1400.0, 50.0), 0.9, "الجنوب"), line((100.0, 10.0, 300.0, 50.0), 0.9, "١٢٠٣٥")]
    blocks, _ = neural._blocks_from_lines(arabic, page_number=1, language="ara", engine_version=None, dpi=200)
    assert [b.text for b in blocks] == ["الجنوب", "١٢٠٣٥"]
    english = [line((1000.0, 10.0, 1400.0, 50.0), 0.9, "South"), line((100.0, 10.0, 300.0, 50.0), 0.9, "12,035")]
    blocks, _ = neural._blocks_from_lines(english, page_number=1, language="eng", engine_version=None, dpi=200)
    assert [b.text for b in blocks] == ["12,035", "South"]


# ── the letter vote ───────────────────────────────────────────────────────

def test_two_readers_that_agree_correct_a_letter_never_a_number():
    easy = "بلفت الإيرادات ١٢٣٤٥٦٧ ريالا"
    letters = "بلغت الإيرادات ريالا"  # PaddleOCR: right letters, number dropped
    tesseract = ["بلغت", "الإيرادات", "1Y٣", "ريالا"]
    voted = neural._vote_words(easy, letters, 0.5, tesseract)
    assert voted == "بلغت الإيرادات ١٢٣٤٥٦٧ ريالا"
    # Negative controls: without agreement (and no confidence) EasyOCR's word
    # stands; a number is EasyOCR's whatever the others say.
    assert neural._vote_words(easy, letters, 0.5, ["بلقت", "الإيرادات"]).startswith("بلفت ")
    assert neural._vote_words("رقم ١٢٣", "رقم 999", 0.99, ["رقم", "999"]) == "رقم ١٢٣"


def test_a_confident_paddleocr_word_stands_alone():
    assert neural._vote_words("الذهائي", "النهائي", 0.95, []) == "النهائي"
    # Negative control: below the confidence the vote needs, nothing changes.
    assert neural._vote_words("الذهائي", "النهائي", 0.85, []) == "الذهائي"


# ── failures ──────────────────────────────────────────────────────────────

def test_a_model_failure_fails_the_page_and_a_defect_surfaces(monkeypatch, tmp_path):
    from kazma_core.documents.errors import DocumentOcrError

    provider = neural.NeuralOcrProvider(tmp_path / "models")
    monkeypatch.setattr(provider, "_ready_models", lambda: SimpleNamespace(version="test"))
    monkeypatch.setattr(provider, "_load", lambda path, max_pixels: (None, None))
    page = dict(page_number=1, language="eng", dpi=300, max_pixels=10**8, timeout_seconds=5,
                output_limit_bytes=1 << 20, work_dir=tmp_path)

    def raising(error: BaseException):
        def read(*_args, **_kwargs):
            raise error
        return read

    monkeypatch.setattr(provider, "_read_lines", raising(RuntimeError("model run failed")))
    with pytest.raises(DocumentOcrError, match="RuntimeError"):
        provider.recognize(tmp_path / "page.png", **page)
    # ONNX Runtime's own errors derive from Exception, not RuntimeError: they
    # are named, so a bad model fails the page as an OCR error too.
    try:
        from onnxruntime.capi import onnxruntime_pybind11_state as state
    except ImportError:
        state = None
    if state is not None:
        assert state.Fail in neural._engine_errors() and not issubclass(state.Fail, RuntimeError)
        monkeypatch.setattr(provider, "_read_lines", raising(state.Fail("bad model")))
        with pytest.raises(DocumentOcrError, match="Fail"):
            provider.recognize(tmp_path / "page.png", **page)
    # Negative control: a defect in Kazma's code is not dressed up as a page
    # the engine could not read.
    monkeypatch.setattr(provider, "_read_lines", raising(AttributeError("a bug")))
    with pytest.raises(AttributeError):
        provider.recognize(tmp_path / "page.png", **page)


# ── the models on disk ────────────────────────────────────────────────────

def _fake_model_set(root: Path) -> Path:
    root.mkdir(parents=True)
    files = {}
    for role, name in neural_models.MODEL_FILES.items():
        data = f"model {role}".encode()
        (root / name).write_bytes(data)
        files[role] = {"file": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    (root / neural_models.MANIFEST_NAME).write_text(
        json.dumps({"version": neural_models.MODEL_SET_VERSION, "files": files}), encoding="utf-8")
    return root


def test_models_are_used_only_when_they_match_their_manifest(tmp_path):
    root = _fake_model_set(tmp_path / "ocr")
    assert neural_models.verify_model_directory(root).ready
    # Negative controls: one changed byte, a missing file, another version.
    first = root / neural_models.MODEL_FILES["det"]
    first.write_bytes(b"model dez")
    status = neural_models.verify_model_directory(root)
    assert not status.ready and "does not match its manifest" in status.reason
    first.unlink()
    assert "missing" in neural_models.verify_model_directory(root).reason
    other = _fake_model_set(tmp_path / "other")
    manifest = json.loads((other / "manifest.json").read_text(encoding="utf-8"))
    (other / "manifest.json").write_text(json.dumps({**manifest, "version": "1999-01-01.1"}), encoding="utf-8")
    assert "needs" in neural_models.verify_model_directory(other).reason
    assert "ocr_models.py install" in neural_models.verify_model_directory(tmp_path / "none").reason


def test_every_model_source_is_pinned():
    for role in neural_models.MODEL_FILES:
        source = neural_models.SOURCES[role]
        assert source.url.startswith("https://"), role
        assert len(source.sha256) == 64 and source.size > 0, role
        assert source.license == "Apache-2.0", role
        if source.member is not None:
            assert len(source.member_sha256 or "") == 64, role


def test_the_installer_refuses_a_download_that_is_not_the_pinned_file(tmp_path, monkeypatch):
    import importlib.util
    import io

    spec = importlib.util.spec_from_file_location("ocr_models_script", REPO / "scripts" / "ocr_models.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    payload = b"the real file"

    def serve(body):
        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return lambda url, timeout: Response(body)

    folder = tmp_path / "downloads"
    folder.mkdir()
    target = folder / "file.onnx"
    monkeypatch.setattr(script.urllib.request, "urlopen", serve(b"a swapped file"))
    with pytest.raises(SystemExit):
        script._download("https://example.invalid/file.onnx", target,
                         sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
    assert not target.exists() and not list(folder.iterdir())
    # Negative control: the pinned bytes are accepted.
    monkeypatch.setattr(script.urllib.request, "urlopen", serve(payload))
    script._download("https://example.invalid/file.onnx", target,
                     sha256=hashlib.sha256(payload).hexdigest(), size=len(payload))
    assert target.read_bytes() == payload


def test_the_converter_runs_on_the_python_its_pins_were_tested_on(tmp_path, monkeypatch):
    """The throwaway converter took the SERVER's Python version: on the live
    install (3.11) uv could not build it, because the pinned numpy needs 3.12
    (2026-10-15). It now always names the version the pins were tested on."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ocr_models_script", REPO / "scripts" / "ocr_models.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    commands: list[list[str]] = []

    def run(command, check):
        commands.append(command)
        Path(command[-1]).write_text(json.dumps({"converted": True}), encoding="utf-8")

    monkeypatch.setattr(script, "_can_convert", lambda: False)
    monkeypatch.setattr(script.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(script.subprocess, "run", run)
    # A server on Python 3.11 (the live install's).
    monkeypatch.setattr(script, "sys", SimpleNamespace(version_info=(3, 11, 15)))
    assert script._convert(tmp_path / "w.pth", tmp_path / "out.onnx", "rec_arabic") == {"converted": True}
    python = commands[0][commands[0].index("--python") + 1]
    assert python == script.EXPORT_PYTHON == "3.12"
    # The pins name a numpy that needs 3.12: an older interpreter cannot build them.
    numpy = next(r for r in script.EXPORT_REQUIREMENTS if r.startswith("numpy=="))
    assert tuple(int(x) for x in numpy.split("==")[1].split(".")[:2]) >= (2, 5)


# ── choosing the engine ───────────────────────────────────────────────────

def _config(tmp_path, **changes) -> DocumentConfig:
    return DocumentConfig(storage_root=tmp_path / "store", security_malware_scan="off", **changes)


def test_neural_without_its_models_is_tesseract_and_says_why(tmp_path):
    provider, reason = pipeline.select_ocr_provider(_config(tmp_path, ocr_engine="neural"),
                                                    model_dir=tmp_path / "no-models")
    assert provider.__class__.__name__ == "TesseractProvider" and reason
    # auto falls back without a word; tesseract never looks at the neural engine.
    provider, reason = pipeline.select_ocr_provider(_config(tmp_path, ocr_engine="auto"),
                                                    model_dir=tmp_path / "no-models")
    assert provider.__class__.__name__ == "TesseractProvider" and reason is None


def test_the_tesseract_setting_never_probes_the_neural_engine(tmp_path, monkeypatch):
    def refuse(self, *args, **kwargs):
        raise AssertionError("the neural engine was probed")

    monkeypatch.setattr(neural.NeuralOcrProvider, "probe", refuse)
    provider, reason = pipeline.select_ocr_provider(_config(tmp_path, ocr_engine="tesseract"))
    assert provider.__class__.__name__ == "TesseractProvider" and reason is None
    # Negative control: auto does look.
    with pytest.raises(AssertionError):
        pipeline.select_ocr_provider(_config(tmp_path, ocr_engine="auto"))


def test_the_engine_setting_is_validated_on_every_write():
    from kazma_core.settings_validation import SettingRejected, validate_setting

    assert validate_setting("documents.ocr.engine", " Neural ") == ("neural", "documents")
    assert validate_setting("documents.ocr", {"engine": "tesseract", "dpi": 300})[0]["engine"] == "tesseract"
    for bad in ("paddle", "", None, 3):
        with pytest.raises(SettingRejected):
            validate_setting("documents.ocr.engine", bad)
    with pytest.raises(SettingRejected):
        validate_setting("documents", {"ocr": {"engine": "easyocr"}})


def test_the_documents_settings_route_refuses_an_engine_it_cannot_run(tmp_path):
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.templating import Jinja2Templates
    from fastapi.testclient import TestClient
    from kazma_core.config_store import ConfigStore
    from kazma_ui.settings import create_settings_router

    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    (tmp_path / "templates").mkdir()
    app = FastAPI()
    app.include_router(create_settings_router(MagicMock(), store, Jinja2Templates(directory=str(tmp_path / "templates"))))
    client = TestClient(app)
    refused = client.put("/api/settings/documents", json={"ocr_engine": "paddle", "ocr_tables": False})
    assert refused.status_code == 400 and "documents.ocr.engine" in refused.json()["detail"]
    assert store.get("documents.ocr.engine") is None and store.get("documents.ocr.tables") is None
    # Negative control: an engine Kazma runs is saved, normalized.
    assert client.put("/api/settings/documents", json={"ocr_engine": " Neural "}).status_code == 200
    assert store.get("documents.ocr.engine") == "neural"
    shown = client.get("/api/settings/documents").json()  # the live policy (the process's settings)
    assert shown["ocr_engine"] in {"auto", "neural", "tesseract"} and "selected" in shown["ocr_engine_health"]
    store.close()


def test_a_failed_engine_probe_is_shown_and_a_defect_surfaces(monkeypatch):
    import kazma_core.documents as documents
    from kazma_ui import settings as settings_module

    cfg = SimpleNamespace(ocr_languages=("ara", "eng"), ocr_engine="auto")

    def raising(error: BaseException):
        def health(*_args, **_kwargs):
            raise error
        return health

    monkeypatch.setattr(documents, "get_ocr_health", raising(OSError("the broker is down")))
    shown = settings_module._ocr_engine_health(cfg)
    assert shown["selected"] is None and shown["neural_ready"] is False and shown["reason"]
    # Negative control: a defect in Kazma's code is not shown as an engine
    # that cannot run.
    monkeypatch.setattr(documents, "get_ocr_health", raising(AttributeError("a bug")))
    with pytest.raises(AttributeError):
        settings_module._ocr_engine_health(cfg)


# ── fake providers for the page loop ──────────────────────────────────────

class _Fake:
    def __init__(self, engine: str, *, skips: bool = False, seconds: float = 0.0) -> None:
        self.engine = engine
        self.skips_table_regions = skips
        self.seconds = seconds
        self.calls: list[dict] = []

    def probe(self, requested_languages=()):
        return OcrCapability(f"fake-{self.engine}", self.engine, "1", ("ara", "eng"), ("text",),
                             OcrReadiness.READY)

    def recognize(self, image_path, *, page_number, language, **kwargs):
        import time

        self.calls.append({"page": page_number, **{k: v for k, v in kwargs.items() if k == "skip_regions"}})
        if self.seconds:
            time.sleep(self.seconds)
        return OcrPageResult(page_number, (), language, self.engine, "1", 200, None)


def _document(pages: int) -> DocumentIR:
    return DocumentIR(
        document_id=new_document_id(), version_id=new_version_id(),
        pages=tuple(DocumentPage(page_number=n, blocks=()) for n in range(1, pages + 1)),
        provenance=Provenance(source="fixture", parser="fixture"),
        metadata={"source_mime": "image/png", "warnings": []},
    )


def _image(tmp_path) -> Path:
    from PIL import Image

    path = tmp_path / "page.png"
    Image.new("L", (400, 300), 255).save(path)
    return path


def test_near_the_time_limit_tesseract_reads_the_remaining_pages(tmp_path, monkeypatch):
    import time

    image = _image(tmp_path)
    monkeypatch.setattr(pipeline, "render_image_page", lambda source, number, **kwargs: Path(
        __import__("shutil").copy(image, tmp_path / f"p{number}.png")))
    slow = _Fake("neural", seconds=0.3)
    fast = _Fake("tesseract")
    monkeypatch.setattr(pipeline, "TesseractProvider", lambda: fast)
    config = _config(tmp_path, ocr_tables=False)
    ir = pipeline.apply_ocr(image, _document(4), config, force=True, language="ara", work_dir=tmp_path,
                            provider=slow, deadline=time.monotonic() + 0.45)
    assert [c["page"] for c in slow.calls] == [1] and [c["page"] for c in fast.calls] == [2, 3, 4]
    assert ir.metadata["ocr"]["tesseract_from_page"] == 2
    assert any("read by Tesseract" in w for w in ir.metadata["warnings"])
    # Negative control: no time limit, every page with the engine asked for.
    slow.calls.clear()
    fast.calls.clear()
    ir = pipeline.apply_ocr(image, _document(4), config, force=True, language="ara", work_dir=tmp_path,
                            provider=slow)
    assert len(slow.calls) == 4 and not fast.calls and "tesseract_from_page" not in ir.metadata["ocr"]


def test_table_lines_are_left_to_the_table_pass_and_read_again_when_it_fails(tmp_path, monkeypatch):
    from kazma_core.documents.ocr import tables

    image = _image(tmp_path)
    monkeypatch.setattr(pipeline, "render_image_page", lambda source, number, **kwargs: image)
    monkeypatch.setattr(tables, "table_regions", lambda path: [(10, 10, 200, 200)])
    provider = _Fake("neural", skips=True)
    config = _config(tmp_path)
    monkeypatch.setattr(pipeline, "_read_page_tables", lambda *a, **k: ([SimpleNamespace()], None))
    monkeypatch.setattr(pipeline, "_page_with_tables", lambda result, found: result.blocks)
    pipeline.apply_ocr(image, _document(1), config, force=True, language="ara", work_dir=tmp_path,
                       provider=provider)
    assert provider.calls == [{"page": 1, "skip_regions": ((10, 10, 200, 200),)}]
    # The table pass stopped early: the page is read again in full.
    provider.calls.clear()
    monkeypatch.setattr(pipeline, "_read_page_tables", lambda *a, **k: ([], "table_deadline"))
    pipeline.apply_ocr(image, _document(1), config, force=True, language="ara", work_dir=tmp_path,
                       provider=provider)
    assert provider.calls == [{"page": 1, "skip_regions": ((10, 10, 200, 200),)}, {"page": 1}]
    # Negative control: a provider that reads the page whole skips nothing.
    whole = _Fake("tesseract")
    pipeline.apply_ocr(image, _document(1), config, force=True, language="ara", work_dir=tmp_path,
                       provider=whole)
    assert whole.calls == [{"page": 1}]


def test_lines_inside_a_skipped_region_are_never_read(monkeypatch):
    read: list[tuple] = []
    boxes = [(10.0, 10.0, 100.0, 40.0), (10.0, 300.0, 100.0, 340.0)]
    monkeypatch.setattr(neural, "_detect", lambda models, bgr: boxes)
    monkeypatch.setattr(neural, "_ink_rows", lambda grey, box: tuple(int(v) for v in box))
    monkeypatch.setattr(neural, "_remove_specks", lambda page, box: 0)
    monkeypatch.setattr(neural, "_easyocr_read", lambda *a: read.append(a) or neural.Reading("abc", 0.9, 0.9))
    provider = neural.NeuralOcrProvider(Path("unused"))
    if not _numpy():
        pytest.skip("numpy is not installed")
    grey = __import__("numpy").zeros((400, 200), dtype="uint8")
    models = SimpleNamespace(rec_latin=None, rec_arabic=None)
    lines = provider._read_lines(models, None, grey, "eng", deadline=1e18, words=[],
                                 skip_regions=((0.0, 0.0, 200.0, 100.0),))
    assert len(read) == 1 and len(lines) == 1
    read.clear()
    provider._read_lines(models, None, grey, "eng", deadline=1e18, words=[])
    assert len(read) == 2  # negative control: nothing skipped


def _numpy() -> bool:
    import importlib.util

    return importlib.util.find_spec("numpy") is not None


# ── the worker image ──────────────────────────────────────────────────────

def test_a_container_run_reads_the_image_models_never_the_host_s(tmp_path):
    from kazma_core.documents import worker_container

    source = tmp_path / "scan.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n")
    host = str(tmp_path / "host-models")
    request = {"protocol_version": 1, "source_path": str(source), "config": {"storage_root": str(tmp_path)},
               "ocr": {"force": True, "language": None, "pages": None, "models": host}}
    body, _ = worker_container.parser_container_request(request, source)
    assert body["ocr"]["models"] == neural_models.IMAGE_MODEL_DIR
    assert request["ocr"]["models"] == host  # negative control: the host request itself keeps the host's


def test_the_image_report_names_the_engine_that_reads(monkeypatch):
    from kazma_core.documents import worker_container

    ready = {"readiness": "ready", "name": "x", "version": "1", "reason": None}
    report = {"ocr": {"rasterizer": ready, "image_parser": ready, "engines": {
        "tesseract": {**ready, "name": "tesseract", "languages": ["ara", "eng"]},
        "neural": {**ready, "name": "neural", "languages": ["ara", "eng"]},
    }}}
    monkeypatch.setattr(worker_container, "_image_report", lambda: (report, ""))
    assert worker_container.image_ocr_health(("ara",), engine="auto").selected_engine == "neural"
    assert worker_container.image_ocr_health(("ara",), engine="tesseract").selected_engine == "tesseract"
    # An image built before the neural engine reports Tesseract alone.
    old = {"ocr": {"rasterizer": ready, "image_parser": ready, "languages": ["ara", "eng"],
                   "engine": {**ready, "name": "tesseract"}}}
    monkeypatch.setattr(worker_container, "_image_report", lambda: (old, ""))
    health = worker_container.image_ocr_health(("ara",), engine="neural")
    assert health.selected_engine == "tesseract" and health.readiness.value == "degraded"
    assert "neural" in (health.reason or "")


# ── the real engine (needs the packages and the installed models) ──────────

def _installed() -> Path:
    problem = neural._runtime_problem()
    if problem:
        pytest.skip(problem)
    directory = neural_models.model_directory()
    status = neural_models.verify_model_directory(directory)
    if not status.ready:
        pytest.skip(status.reason)
    return directory


def _arabic_line_image(tmp_path: Path) -> tuple[Path, str]:
    import pymupdf

    fonts = REPO / "kazma-core" / "kazma_core" / "documents" / "assets" / "fonts"
    text = "بلغت الإيرادات لهذا الفصل ١٢٣٤٥٦٧ ريالا في جميع المناطق"
    archive = pymupdf.Archive(str(fonts))
    css = "@font-face {font-family: F; src: url(IBMPlexSansArabic-Regular.ttf);} body {font-family: F; font-size: 14pt;}"
    story = pymupdf.Story(html=f'<body dir="rtl"><p dir="rtl">{text}</p></body>', user_css=css, archive=archive)
    pdf = tmp_path / "line.pdf"
    writer = pymupdf.DocumentWriter(str(pdf))
    device = writer.begin_page(pymupdf.Rect(0, 0, 595, 120))
    story.place(pymupdf.Rect(30, 30, 565, 100))
    story.draw(device)
    writer.end_page()
    writer.close()
    image = tmp_path / "line.png"
    with pymupdf.open(pdf) as doc:
        doc[0].get_pixmap(dpi=300).save(image)
    return image, "١٢٣٤٥٦٧"


def test_the_neural_engine_reads_the_arabic_indic_number_tesseract_misses(tmp_path):
    directory = _installed()
    image, number = _arabic_line_image(tmp_path)
    read = neural.NeuralOcrProvider(directory).recognize(
        image, page_number=1, language="ara", dpi=300, max_pixels=50_000_000, timeout_seconds=60,
        output_limit_bytes=1 << 20, work_dir=tmp_path)
    assert read.engine == "neural"
    assert number in " ".join(b.text for b in read.blocks)
    # Negative control: Tesseract's reading of the same line has no such number.
    from kazma_core.documents.ocr.tesseract import TesseractProvider

    tesseract = TesseractProvider()
    if not tesseract.probe(("ara",)).available:
        pytest.skip("Tesseract with 'ara' is not installed (the control)")
    control = tesseract.recognize(image, page_number=1, language="ara", dpi=300, max_pixels=50_000_000,
                                  timeout_seconds=60, output_limit_bytes=1 << 20, work_dir=tmp_path)
    assert number not in " ".join(b.text for b in control.blocks)


def test_the_worker_reads_with_the_engine_the_setting_names(tmp_path):
    directory = _installed()
    from kazma_core.documents.service import DocumentService

    image, number = _arabic_line_image(tmp_path)
    service = DocumentService(config=_config(tmp_path, ocr_engine="neural"))
    ir = service.parse_ingested_blob(image, force_ocr=True, ocr_language="ara")
    assert ir.metadata["ocr"]["engine"] == "neural"
    assert number in " ".join(b.text for p in ir.pages for b in p.blocks)
    assert directory.is_dir()
