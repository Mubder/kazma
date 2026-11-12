"""Pictures in generated documents (2026-11-12, the owner's request).

A document tool names a picture with ``![caption](path)`` on a line of its
own (PDF, DOCX) or a slide's ``image`` (PPTX). In the Kazma process each one
is checked and staged (``documents.image_inputs``: a file the file tools may
read, or one of Kazma's own image folders; never an address; a PNG, JPEG, GIF
or WebP by its first bytes; bounded), and the renderer worker decodes, cleans
and saves it again before an engine reads it (``documents.image_clean``: four
decoders only, a pixel bound, EXIF orientation applied and every byte of
metadata dropped). The engines then place each picture where its line stood.

Before this the document tools took no pictures at all; the engines'
picture code skipped any it could not embed without a word.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.documents import image_inputs
from kazma_core.documents.config import DocumentConfig
from kazma_core.documents.image_clean import CleanImage, ImageRejected, clean_images
from kazma_core.documents.image_inputs import DocumentImages
from kazma_core.documents.image_refs import inline_images, parts, rewrite, without_images
from kazma_core.documents.service import DocumentService
from PIL import Image

GPS_TAG = 0x8825


def clean_image(source: Path, out_dir: Path) -> CleanImage:
    """One approved image through the worker's cleaner."""
    return clean_images(source.parent, [source.name], out_dir)[source.name]

ORIENTATION_TAG = 0x0112


# --------------------------------------------------------------------------- #
# pictures to test with
# --------------------------------------------------------------------------- #
def _photo_with_gps(path: Path, *, size: tuple[int, int] = (300, 200), orientation: int = 6) -> Path:
    """A JPEG as a phone writes it: a GPS position and an orientation tag."""
    img = Image.new("RGB", size, (200, 30, 30))
    for x in range(size[0] // 2):  # the left half blue, to see the rotation
        for y in range(size[1]):
            img.putpixel((x, y), (30, 30, 200))
    exif = Image.Exif()
    exif[ORIENTATION_TAG] = orientation
    exif[GPS_TAG] = {1: "N", 2: (29.0, 22.0, 30.0), 3: "E", 4: (47.0, 58.0, 40.0)}
    img.save(path, format="JPEG", exif=exif.tobytes(), comment=b"secret camera serial 1234")
    return path


def _png_with_alpha_and_text(path: Path) -> Path:
    from PIL import PngImagePlugin

    img = Image.new("RGBA", (120, 80), (0, 128, 0, 0))
    img.putpixel((10, 10), (255, 0, 0, 255))
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "home address 12 Hidden St")
    img.save(path, format="PNG", pnginfo=info)
    return path


def _png(path: Path, size: tuple[int, int] = (400, 300), color: tuple[int, int, int] = (20, 110, 220)) -> Path:
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


# --------------------------------------------------------------------------- #
# the grammar (one for the Kazma process and the worker)
# --------------------------------------------------------------------------- #
def test_an_image_line_is_a_figure_and_nothing_else_is() -> None:
    body = "\n".join([
        "Intro paragraph.",
        "![Sales by region](charts/sales.png)",
        "![With spaces](<my pictures/a (1).png> \"title\")",
        "```",
        "![not a figure](inside/code.png)",
        "```",
        "Closing.",
    ])
    found = [p for p in parts(body) if not isinstance(p, str)]
    assert [(r.caption, r.source) for r in found] == [
        ("Sales by region", "charts/sales.png"), ("With spaces", "my pictures/a (1).png"),
    ]
    assert inline_images(body) == []
    assert inline_images("See ![x](a.png) here, and `![code](b.png)`.") == ["![x](a.png)"]
    assert without_images(body).splitlines()[1] == "Sales by region"
    renamed = rewrite(body, lambda ref: "image-01.png")
    assert "![Sales by region](image-01.png)" in renamed
    assert "![not a figure](inside/code.png)" in renamed, "a code block is left as written"


# --------------------------------------------------------------------------- #
# cleaning, in the worker
# --------------------------------------------------------------------------- #
def test_a_phone_photo_loses_its_position_and_stands_upright(tmp_path) -> None:
    source = _photo_with_gps(tmp_path / "image-01.jpg")
    with Image.open(source) as raw:  # the negative control: what the raw file carries
        assert GPS_TAG in raw.getexif() and raw.size == (300, 200)
    clean = clean_image(source, tmp_path / "out")
    out = tmp_path / "out" / clean.name
    assert clean.name == "image-01.jpg"
    with Image.open(out) as img:
        assert (img.width, img.height) == (200, 300), "orientation 6 turned it upright"
        assert len(img.getexif()) == 0
        assert "comment" not in img.info and "exif" not in img.info
    data = out.read_bytes()
    assert b"Exif" not in data and b"secret camera serial" not in data


def test_a_png_keeps_its_transparency_and_loses_its_text(tmp_path) -> None:
    source = _png_with_alpha_and_text(tmp_path / "image-01.png")
    assert b"Hidden St" in source.read_bytes()
    clean = clean_image(source, tmp_path / "out")
    out = tmp_path / "out" / clean.name
    assert b"Hidden St" not in out.read_bytes()
    with Image.open(out) as img:
        assert img.mode == "RGBA" and img.getpixel((10, 10)) == (255, 0, 0, 255)
        assert img.getpixel((0, 0))[3] == 0


def test_a_polyglot_keeps_only_its_pixels(tmp_path) -> None:
    source = _png(tmp_path / "image-01.png", (50, 40))
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("run.js", "alert('hidden')")
    source.write_bytes(source.read_bytes() + payload.getvalue())
    out = tmp_path / "out" / clean_image(source, tmp_path / "out").name
    assert b"PK\x03\x04" not in out.read_bytes() and b"hidden" not in out.read_bytes()


def test_a_decompression_bomb_is_refused(tmp_path) -> None:
    bomb = tmp_path / "image-01.png"
    Image.new("1", (8000, 6000)).save(bomb, format="PNG")  # 48 MP in a small file
    assert bomb.stat().st_size < 200_000
    with pytest.raises(ImageRejected, match="pixels"):
        clean_image(bomb, tmp_path / "out")


def test_an_eps_never_reaches_ghostscript(tmp_path) -> None:
    eps = tmp_path / "image-01.png"
    eps.write_bytes(b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\nshowpage\n")
    with Image.open(eps) as opened:  # the negative control: Pillow's own choice
        assert opened.format == "EPS"
    with pytest.raises(ImageRejected, match="could not be read"):
        clean_image(eps, tmp_path / "out")


def test_odd_but_real_images_come_out_right(tmp_path) -> None:
    gif = tmp_path / "image-01.gif"
    frames = [Image.new("P", (30, 20), i) for i in (1, 2, 3)]
    frames[0].save(gif, save_all=True, append_images=frames[1:], format="GIF")
    assert clean_image(gif, tmp_path / "out").name == "image-01.png"

    cmyk = tmp_path / "image-02.jpg"
    Image.new("CMYK", (30, 20), (0, 255, 255, 0)).save(cmyk, format="JPEG")
    out = clean_image(cmyk, tmp_path / "out")
    with Image.open(tmp_path / "out" / out.name) as img:
        assert img.mode == "RGB" and img.getpixel((5, 5))[0] > 200, "CMYK red stays red"

    deep = tmp_path / "image-03.png"
    Image.new("I;16", (30, 20), 30000).save(deep, format="PNG")
    out = clean_image(deep, tmp_path / "out")
    with Image.open(tmp_path / "out" / out.name) as img:
        assert img.getpixel((5, 5))[0] < 200, "16-bit grey is not clipped to white"

    big = _png(tmp_path / "image-04.png", (5000, 1000))
    out = clean_image(big, tmp_path / "out")
    assert (out.width, out.height) == (2400, 480)


# --------------------------------------------------------------------------- #
# gathering, in the Kazma process
# --------------------------------------------------------------------------- #
def _workspace() -> Path:
    from kazma_core.workspace.binding import resolve_active_root

    root = resolve_active_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.mark.asyncio
async def test_images_are_staged_under_names_of_the_call_and_removed_after(tmp_path) -> None:
    ws = _workspace()
    pic = _png(ws / "pic-staged.png")
    async with DocumentImages() as images:
        sections = await images.in_sections([
            {"heading": "A", "body": "Text.\n![One](pic-staged.png)\n![Again](pic-staged.png)"},
        ])
        assert sections[0]["body"] == "Text.\n![One](image-01.png)\n![Again](image-01.png)"
        assert [p.name for p in images.paths] == ["image-01.png"]
        folder = images.paths[0].parent
        assert images.paths[0].read_bytes() == pic.read_bytes()
        assert len(images.digests) == 1
    assert not folder.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("source,said", [
    ("https://example.com/a.png", "download_image"),
    ("data:image/png;base64,AAAA", "download_image"),
    ("missing-picture.png", "not found"),
])
async def test_what_is_not_a_readable_picture_file_is_refused(source, said) -> None:
    _workspace()
    async with DocumentImages() as images:
        with pytest.raises(RefusedBeforeEffect, match=said):
            await images.in_body(f"![x]({source})", "section 1")


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_picture_is_refused_by_its_bytes() -> None:
    ws = _workspace()
    (ws / "page.png").write_text("<html><script>alert(1)</script></html>", encoding="utf-8")
    async with DocumentImages() as images:
        with pytest.raises(RefusedBeforeEffect, match="not a PNG, JPEG, GIF or WebP"):
            await images.in_body("![x](page.png)", "section 1")


@pytest.mark.asyncio
async def test_a_kazma_secret_file_is_never_a_picture() -> None:
    from kazma_core.paths import data_dir

    secret = data_dir() / ".env"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("KAZMA_VAULT_KEY=x\n", encoding="utf-8")
    async with DocumentImages() as images:
        with pytest.raises(RefusedBeforeEffect, match="cannot be read"):
            await images.in_body(f"![x](<{secret}>)", "section 1")


@pytest.mark.asyncio
async def test_an_image_inside_a_sentence_is_refused_not_dropped() -> None:
    async with DocumentImages() as images:
        with pytest.raises(RefusedBeforeEffect, match="line of its own"):
            await images.in_body("Look at ![this](a.png) here.", "section 2")


@pytest.mark.asyncio
async def test_kazmas_own_image_folders_are_readable(tmp_path) -> None:
    from kazma_core.paths import data_dir

    (data_dir() / "images").mkdir(parents=True, exist_ok=True)
    generated = _png(data_dir() / "images" / "gen-1.png")
    attachment = data_dir() / "attachments" / "photo.jpg"
    attachment.parent.mkdir(parents=True, exist_ok=True)
    _photo_with_gps(attachment)
    async with DocumentImages() as images:
        body = await images.in_body(f"![a](<{generated}>)\n![b](<{attachment}>)", "section 1")
    assert body == "![a](image-01.png)\n![b](image-02.jpg)"


@pytest.mark.asyncio
async def test_limits_refuse_before_anything_is_made(monkeypatch) -> None:
    ws = _workspace()
    for n in range(3):
        _png(ws / f"limit-{n}.png", (20, 20), (n, n, n))
    monkeypatch.setattr(image_inputs, "MAX_IMAGES", 2)
    async with DocumentImages() as images:
        with pytest.raises(RefusedBeforeEffect, match="at most 2 images"):
            await images.in_body("\n".join(f"![x](limit-{n}.png)" for n in range(3)), "s")
    monkeypatch.setattr(image_inputs, "MAX_IMAGE_BYTES", 10)
    async with DocumentImages() as images:
        with pytest.raises(RefusedBeforeEffect, match="may be at most"):
            await images.in_body("![x](limit-0.png)", "s")


@pytest.mark.asyncio
async def test_a_markdown_files_pictures_are_beside_it() -> None:
    ws = _workspace()
    folder = ws / "report-with-figures"
    (folder / "figures").mkdir(parents=True, exist_ok=True)
    _png(folder / "figures" / "fig1.png")
    async with DocumentImages() as images:
        body = await images.in_body("![Figure 1](figures/fig1.png)", "s", base=folder / "report.md")
    assert body == "![Figure 1](image-01.png)"


# --------------------------------------------------------------------------- #
# the whole path: staged, cleaned in the worker, placed by each engine
# --------------------------------------------------------------------------- #
def _config(tmp_path: Path) -> DocumentConfig:
    return replace(DocumentConfig(storage_root=tmp_path / "store", ocr_enabled=False,
                                  worker_timeout_seconds=60))


async def _generate(tmp_path: Path, fmt: str, payload: dict, *, slides: bool = False):
    async with DocumentImages() as images:
        if slides:
            payload = {**payload, "slides": await images.in_slides(payload["slides"])}
        else:
            payload = {**payload, "sections": await images.in_sections(payload["sections"])}
        result = await DocumentService(config=_config(tmp_path)).generate(
            fmt, payload, tenant_id="t", workspace_id="w", actor_id="a",
            export_dir=tmp_path / "out", approved_assets=images.paths,
        )
    assert result.ok, (result.code, result.message)
    return Path(result.data.export_path)


@pytest.mark.asyncio
async def test_a_pdf_places_each_picture_where_its_line_stood(tmp_path) -> None:
    import fitz

    ws = _workspace()
    _png(ws / "chart-pdf.png", (800, 400))
    out = await _generate(tmp_path, "pdf", {"title": "Figures", "sections": [{
        "heading": "Results",
        "body": "Alpha paragraph before.\n\n![Chart of sales](chart-pdf.png)\n\nBravo paragraph after.",
    }]})
    doc = fitz.open(out)
    try:
        page = doc[0]
        images = page.get_images(full=True)
        assert len(images) == 1
        box = page.get_image_rects(images[0][0])[0]
        alpha = page.search_for("Alpha paragraph")[0]
        bravo = page.search_for("Bravo paragraph")[0]
        caption = page.search_for("Chart of sales")[0]
        assert alpha.y1 <= box.y0 < box.y1 <= caption.y0 < bravo.y0
        assert "image-01" not in page.get_text(), "the staged name never shows"
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_an_arabic_pdf_places_its_picture_through_libreoffice(tmp_path) -> None:
    """Arabic text is drawn through the DOCX route (LibreOffice shapes it)."""
    import fitz
    from kazma_core.documents.binaries import find_soffice

    if not find_soffice():
        pytest.skip("LibreOffice is not installed here")
    ws = _workspace()
    _png(ws / "chart-ar.png", (800, 400))
    out = await _generate(tmp_path, "pdf", {"title": "تقرير", "sections": [{
        "heading": "النتائج",
        "body": "فقرة قبل الصورة.\n\n![مخطط المبيعات](chart-ar.png)\n\nفقرة بعد الصورة.",
    }]})
    doc = fitz.open(out)
    try:
        page = doc[0]
        images = page.get_images(full=True)
        assert len(images) == 1
        box = page.get_image_rects(images[0][0])[0]
        text = page.get_text("dict")
        lines = [(line["bbox"][1], "".join(s["text"] for s in line["spans"]))
                 for block in text["blocks"] if block.get("type") == 0 for line in block["lines"]]
        before = [y for y, t in lines if "قبل" in t]
        after = [y for y, t in lines if "بعد" in t]
        assert before and after and before[0] < box.y0 and box.y1 < after[0]
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_a_docx_carries_the_cleaned_picture_and_its_caption(tmp_path) -> None:
    ws = _workspace()
    _photo_with_gps(ws / "trip.jpg")
    out = await _generate(tmp_path, "docx", {"title": "Trip", "sections": [{
        "heading": "Photos", "body": "Before.\n![On the road](trip.jpg)\nAfter.",
    }]})
    with zipfile.ZipFile(out) as zf:
        media = [n for n in zf.namelist() if n.startswith("word/media/")]
        assert len(media) == 1
        picture = zf.read(media[0])
        body = zf.read("word/document.xml").decode("utf-8")
    assert b"Exif" not in picture and b"secret camera serial" not in picture
    with Image.open(io.BytesIO(picture)) as img:
        assert img.height > img.width, "upright"
    order = [body.index("Before."), body.index("<pic:pic"), body.index("On the road"), body.index("After.")]
    assert order == sorted(order)


@pytest.mark.asyncio
async def test_an_arabic_caption_reads_right_to_left_in_an_english_docx(tmp_path) -> None:
    ws = _workspace()
    _png(ws / "map.png")
    out = await _generate(tmp_path, "docx", {"title": "Report", "sections": [{
        "heading": "Map", "body": "English text.\n![خريطة الكويت](map.png)",
    }]})
    import re

    with zipfile.ZipFile(out) as zf:
        body = zf.read("word/document.xml").decode("utf-8")
    paragraphs = re.findall(r"<w:p[ >].*?</w:p>", body, re.S)
    caption = next(p for p in paragraphs if "خريطة" in p)
    assert '<w:bidi w:val="1"/>' in caption
    english = next(p for p in paragraphs if "English text." in p)
    assert '<w:bidi w:val="1"/>' not in english


@pytest.mark.asyncio
@pytest.mark.parametrize("lang,text_side", [("en", "left"), ("ar", "right")])
async def test_a_slide_puts_its_picture_beside_its_text(tmp_path, lang, text_side) -> None:
    from pptx import Presentation

    ws = _workspace()
    _png(ws / f"slide-{lang}.png", (600, 400))
    bullets = ["First point", "Second point"] if lang == "en" else ["النقطة الأولى", "النقطة الثانية"]
    out = await _generate(tmp_path, "pptx", {"title": "Deck", "slides": [
        {"heading": "Overview" if lang == "en" else "نظرة عامة", "bullets": bullets,
         "image": f"slide-{lang}.png", "image_caption": "Caption"},
        {"heading": "Picture only", "image": f"slide-{lang}.png"},
    ]}, slides=True)
    deck = Presentation(str(out))
    first, second = deck.slides[1], deck.slides[2]
    pictures = [s for s in first.shapes if s.shape_type == 13]
    assert len(pictures) == 1
    text = next(s for s in first.shapes if s.has_text_frame and bullets[0] in s.text_frame.text)
    picture = pictures[0]
    if text_side == "left":
        assert text.left + text.width <= picture.left
    else:
        assert picture.left + picture.width <= text.left
    assert any(s.has_text_frame and s.text_frame.text == "Caption" for s in first.shapes)
    alone = [s for s in second.shapes if s.shape_type == 13][0]
    assert alone.width > picture.width, "a slide with no text gives the picture the body"


@pytest.mark.asyncio
async def test_an_image_the_worker_cannot_read_fails_the_document(tmp_path) -> None:
    ws = _workspace()
    broken = ws / "broken.png"
    broken.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)  # starts like a PNG, is not one
    async with DocumentImages() as images:
        payload = {"title": "Broken", "sections": await images.in_sections(
            [{"heading": "x", "body": "![x](broken.png)"}])}
        result = await DocumentService(config=_config(tmp_path)).generate(
            "docx", payload, tenant_id="t", workspace_id="w", actor_id="a",
            export_dir=tmp_path / "out", approved_assets=images.paths,
        )
        message = images.explain(result.message)
    assert not result.ok and result.code == "invalid_image"
    assert "broken.png" in message, "the error names the user's file"
    assert not list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else True


# --------------------------------------------------------------------------- #
# the tools
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_generate_pdf_takes_the_pictures_of_its_markdown_file() -> None:
    import fitz
    from kazma_skills.native.document_generator.tools import generate_pdf

    ws = _workspace()
    folder = ws / "tool-report"
    (folder / "img").mkdir(parents=True, exist_ok=True)
    _png(folder / "img" / "growth.png", (900, 500))
    (folder / "report.md").write_text(
        "# Growth\n\nRevenue grew in every quarter of the year.\n\n"
        "![Quarterly growth](img/growth.png)\n\nThe outlook remains strong for next year.\n",
        encoding="utf-8",
    )
    answer = await generate_pdf(title="Growth report", markdown_path="tool-report/report.md")
    path = Path(answer.split("Saved to:")[1].split("\n")[0].strip())
    doc = fitz.open(path)
    try:
        assert sum(len(page.get_images()) for page in doc) == 1
        assert "Quarterly growth" in "".join(page.get_text() for page in doc)
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_generate_pptx_takes_a_slides_picture() -> None:
    from kazma_skills.native.document_processor.tools import generate_pptx
    from pptx import Presentation

    ws = _workspace()
    _png(ws / "tool-slide.png", (640, 480))
    answer = await generate_pptx(title="Deck", slides=[
        {"heading": "With a picture", "bullets": ["One"], "image": "tool-slide.png"},
    ])
    saved = [line for line in answer.splitlines() if ".pptx" in line][0]
    path = Path(saved.split(":", 1)[1].strip())
    deck = Presentation(str(path))
    assert [s.shape_type for s in deck.slides[1].shapes].count(13) == 1


@pytest.mark.asyncio
async def test_generate_docx_refuses_a_web_address_before_anything_is_made() -> None:
    from kazma_skills.native.document_generator.tools import generate_docx

    with pytest.raises(RefusedBeforeEffect, match="download_image"):
        await generate_docx(title="x", sections=[{"heading": "h", "body": "![a](https://example.com/a.png)"}])


class _Fetched:
    def __init__(self, body: bytes, *, status: int = 200, truncated: bool = False,
                 content_type: str = "image/png", url: str = "https://pics.example/cat.png") -> None:
        import httpx

        self.response = httpx.Response(status, content=body, headers={"content-type": content_type})
        self.text = ""
        self.url = url
        self.truncated = truncated


@pytest.mark.asyncio
async def test_download_image_saves_a_picture_by_what_its_bytes_are(monkeypatch, tmp_path) -> None:
    import kazma_core.web_acquire.http as http
    from kazma_core.tools.image_download import download_image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="JPEG")
    calls: list[str] = []

    async def fetch(url, **kw):
        calls.append(url)
        return _Fetched(buf.getvalue(), content_type="application/octet-stream")

    monkeypatch.setattr(http, "fetch_public", fetch)
    answer = await download_image("https://pics.example/cat.png", name="my cat")
    saved = Path(answer.split("Picture saved: ")[1].split(" (")[0])
    assert saved.name == "my-cat.jpg" and saved.parent.name == "images"
    assert saved.read_bytes() == buf.getvalue()
    again = await download_image("https://pics.example/cat.png", name="my cat")
    assert "my-cat-2.jpg" in again, "never over an earlier file"
    assert calls == ["https://pics.example/cat.png"] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fetched,said", [
    (_Fetched(b"<svg xmlns='http://www.w3.org/2000/svg'/>", content_type="image/svg+xml"), "SVG"),
    (_Fetched(b"<html></html>", content_type="text/html"), "text/html"),
    (_Fetched(b"\x89PNG\r\n\x1a\n" + b"0" * 10, truncated=True), "larger than"),
    (_Fetched(b"", status=404), "HTTP 404"),
])
async def test_download_image_saves_nothing_that_is_not_a_whole_picture(monkeypatch, fetched, said) -> None:
    import kazma_core.web_acquire.http as http
    from kazma_core.agent.tool_refusal import KnownEffectError
    from kazma_core.tools.image_download import download_image

    async def fetch(url, **kw):
        return fetched

    monkeypatch.setattr(http, "fetch_public", fetch)
    folder = _workspace() / "downloads" / "images"
    before = set(folder.iterdir()) if folder.exists() else set()
    with pytest.raises(KnownEffectError, match=said):
        await download_image("https://pics.example/x")
    assert (set(folder.iterdir()) if folder.exists() else set()) == before


@pytest.mark.asyncio
async def test_download_image_refuses_what_is_not_a_web_address() -> None:
    from kazma_core.tools.image_download import download_image

    for url in ("file:///etc/passwd", "C:/Windows/win.ini", "ftp://x/y.png", ""):
        with pytest.raises(RefusedBeforeEffect):
            await download_image(url)


def test_download_image_is_declared_like_every_fetching_writer() -> None:
    from kazma_core.safety.hitl import TOOL_TIERS
    from kazma_core.safety.outside_text_fetch import FETCH_URL_FIELDS
    from kazma_core.safety.side_effects import EffectKind, get_effect_profile
    from kazma_core.store_registry import TOOL_WRITES

    assert TOOL_TIERS["download_image"] == "write"
    assert FETCH_URL_FIELDS["download_image"] == ("url",), "asks after outside text, like read_url"
    assert get_effect_profile("download_image").effect is EffectKind.WRITE_FS
    assert TOOL_WRITES["download_image"].targets == ("workspace",)


def test_a_hash_inside_a_code_block_is_not_a_heading() -> None:
    """The Markdown file's split into sections skips fenced code: a Python
    comment in a code sample cut the sample in two and made it a heading."""
    from kazma_skills.native.document_generator.tools import _markdown_sections

    md = "# One\n\nBefore.\n\n```python\n# a comment\nx = 1\n```\n\n# Two\nbody"
    sections = _markdown_sections(md)
    assert [s["heading"] for s in sections] == ["# One", "# Two"]
    assert "# a comment" in sections[0]["body"] and "x = 1" in sections[0]["body"]

    def old_split(text: str) -> list[str]:  # the split before 2026-11-12
        return [line for line in text.splitlines() if line.startswith("#")]

    assert old_split(md) == ["# One", "# a comment", "# Two"], "the negative control"


def test_negative_control_without_the_split_a_picture_line_is_only_text() -> None:
    """The model builder before 2026-11-12: the image line was body text."""
    from kazma_core.documents.content_model import ImageBlock
    from kazma_core.documents.image_clean import CleanImage
    from kazma_core.documents.renderer_worker import _build_model_and_profile

    payload = {"title": "T", "sections": [{"heading": "H", "body": "a\n![cap](image-01.png)\nb"}]}
    old, _ = _build_model_and_profile(payload)  # no cleaned images: the old path
    assert not any(isinstance(b, ImageBlock) for b in old.blocks)
    new, _ = _build_model_and_profile(payload, {"image-01.png": CleanImage("image-01.png", 800, 400)})
    figures = [b for b in new.blocks if isinstance(b, ImageBlock)]
    assert len(figures) == 1 and figures[0].caption == "cap" and figures[0].width_in == 6.5


def test_a_figure_fits_the_page_and_a_small_one_is_not_blown_up() -> None:
    from kazma_core.documents.renderer_worker import _figure_width

    assert _figure_width(2400, 1200) == 6.5
    assert _figure_width(1000, 4000) == 2.0, "a tall picture is held to 8 inches high"
    assert _figure_width(192, 96) == 2.0, "drawn at its own size"


def test_the_staged_request_names_only_the_staged_files() -> None:
    """What reaches the worker: ``image-NN`` names, never the user's paths."""
    body = rewrite("![a](<C:/Users/someone/secret folder/x.png>)", lambda ref: "image-01.png")
    assert body == "![a](image-01.png)" and json.dumps(body).count("someone") == 0
