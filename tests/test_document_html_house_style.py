"""An HTML file converted to PDF comes out in Kazma's house style.

Live 2026-10-20 (Telegram), four times: asked for a bilingual Kuwait-weather
PDF, the agent wrote its own HTML ("Segoe UI, Tahoma, Arial", fonts the
document worker does not have: the PDF came out in DejaVu and Noto) and
converted it. Memory then held the file ("kuwait has_file
reports/kuwait-weather-seasons-ar-en.html"), and every later request -- in a
new chat too -- converted it again: never the house style. Every HTML->PDF
conversion now wraps the content in the themed shell (brand fonts, headings,
tables, direction); ``keep_source_style`` keeps a file's own design.
"""

from __future__ import annotations

import pytest

from kazma_core.documents.renderer_worker import _house_style_html

#: The shape of the agent's live HTML (two language pages, its own CSS).
AGENT_HTML = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<title>طقس الكويت عبر الفصول · Kuwait Weather by Season</title>
<style>
  @page { size: A4; margin: 15mm 14mm; }
  body { font-family: "Segoe UI", Tahoma, Arial, sans-serif; font-size: 10.4pt; color: #111; }
  h1 { font-size: 17pt; border-bottom: 1.4pt solid #333; }
  table { width: 100%; border-collapse: collapse; }
  th { background: #e9e9e9; }
  .page { page-break-after: always; }
</style>
</head>
<body>
<section class="page" dir="rtl">
  <h1>طقس الكويت عبر الفصول</h1>
  <p class="lead" style="color:#333; font-family:Arial">مناخ الكويت صحراوي حار جاف.</p>
  <table><tr><th>الفصل</th><th>أعلى معدل (م°)</th></tr>
  <tr><td>الصيف</td><td><span dir="ltr">43–46</span></td></tr></table>
</section>
<section dir="ltr">
  <h1>Kuwait Weather by Season</h1>
  <table bgcolor="#eee" width="100%"><tr><th>Season</th><th>Avg high (°C)</th></tr>
  <tr><td>Summer</td><td>43–46</td></tr></table>
  <script>alert(1)</script>
</section>
</body>
</html>"""


def test_the_agents_html_comes_out_in_the_house_style():
    out = _house_style_html(AGENT_HTML)
    assert "Segoe UI" not in out and "Tahoma" not in out and "#e9e9e9" not in out
    assert "font-family:Arial" not in out and 'bgcolor' not in out and "<script" not in out
    # The house shell: the brand face and the themed stylesheet.
    assert "IBM Plex Sans Arabic" in out
    assert 'class="content-body"' in out
    # The content, in order, with its direction marks.
    assert out.index("طقس الكويت عبر الفصول</h1>") < out.index("Kuwait Weather by Season</h1>")
    assert 'dir="ltr"' in out and "43–46" in out
    # The page break the source asked for survives (between the two pages).
    assert ".page { page-break-after: always; }" in out
    assert "15mm 14mm" not in out  # the house page wins over the source's
    assert "<title>طقس الكويت عبر الفصول · Kuwait Weather by Season</title>" in out


def test_a_page_break_given_inline_is_kept():
    out = _house_style_html('<div style="color:red; break-before: page">x</div>')
    assert 'style="break-before: page"' in out and "color:red" not in out


def test_the_worker_applies_it_unless_the_source_style_is_kept(monkeypatch, tmp_path):
    """Through the worker's own dispatch (``_render``), WeasyPrint stubbed."""
    from kazma_core.documents import renderer_worker as worker

    seen: list[str] = []
    monkeypatch.setattr(worker, "_weasy_pdf", lambda output, text, assets: seen.append(text))
    monkeypatch.setattr(worker, "_assert_rendered_pdf_safe", lambda output: None, raising=False)
    source = tmp_path / "r.html"
    source.write_text(AGENT_HTML, encoding="utf-8")
    for payload in ({}, {"keep_source_style": True}):
        request = {"operation": "convert:html:pdf", "payload": payload, "renderer": "weasyprint",
                   "source_path": str(source), "source_sha256": worker._sha256(source)}
        try:
            worker._render(request, tmp_path / "o.pdf")
        except (OSError, worker.WorkerError):
            pass  # no PDF was written: the stub only records what would be rendered
    assert len(seen) == 2
    assert "Segoe UI" not in seen[0] and "IBM Plex Sans Arabic" in seen[0]
    assert "Segoe UI" in seen[1], "keep_source_style keeps the file's own design"


def test_the_tool_offers_keep_source_style():
    import inspect

    from kazma_skills.native.document_processor.tools import convert_document

    assert inspect.signature(convert_document).parameters["keep_source_style"].default is False


@pytest.mark.parametrize("source", ["<p>plain</p>", "text only", ""])
def test_any_html_shape_is_wrapped(source):
    assert 'class="content-body"' in _house_style_html(source or "<p></p>")


def test_a_converted_pdf_has_the_house_header_and_footer():
    """Live 2026-10-20: "why has this file no header and footer like the
    documents Kazma created before?" The DOCX engine draws them; the printed
    HTML page now carries the same: the short brand on top, the brand and the
    page number at the bottom, on the reading-start side."""
    out = _house_style_html(AGENT_HTML)
    assert '@top-right { content: "كاظمه";' in out
    assert '@bottom-right { content: "منظومة كاظمة للذكاء الاصطناعي    —    صفحة " counter(page);' in out
    english = _house_style_html("<html dir='ltr'><body><p>Kuwait has a hot climate.</p></body></html>")
    assert '@top-left { content: "Kazma";' in english
    assert '@bottom-left { content: "Kazma AI Platform    —    Page " counter(page);' in english


def test_a_latin_run_in_arabic_text_is_isolated_and_english_is_left_alone():
    """"50 °C" in an Arabic note drew "C° 50" (live 2026-10-20)."""
    lrm = chr(0x200E)
    out = _house_style_html(
        '<html dir="rtl"><body><p>تتجاوز الحرارة 50 °C صيفا</p>'
        '<section dir="ltr"><p>Summer passes 50 °C</p></section></body></html>')
    assert f"{lrm}50 °C{lrm}" in out
    assert "Summer passes 50 °C</p>" in out


def test_hand_written_bullets_become_a_house_list():
    """The agent wrote each note as <p>▪ ...</p>; ▪ is not in the house face and
    drew in DejaVu (live 2026-10-20, "house every thing")."""
    out = _house_style_html(
        '<html dir="rtl"><body><p>مقدمة</p>'
        '<p class="item">▪ <strong>الصيف:</strong> حار</p>'
        '<p class="item">▪ <strong>الشتاء:</strong> بارد</p>'
        '<p>خاتمة</p></body></html>')
    body = out[out.index('class="content-body"'):]
    assert "<ul><li><strong>الصيف:</strong> حار</li><li><strong>الشتاء:</strong> بارد</li></ul>" in body
    assert body.index("مقدمة") < body.index("<ul>") < body.index("خاتمة")
    assert "▪" not in body


def test_a_symbol_the_house_face_lacks_is_drawn_as_the_house_draws_it():
    body = _house_style_html("<p>Done ✔ and ● next</p>")
    assert "Done ✓ and • next" in body


def test_a_paragraph_that_only_starts_with_a_dash_word_is_left_alone():
    body = _house_style_html("<p>-5 degrees at night</p><p>Ordinary text</p>")
    assert "<ul>" not in body and "-5 degrees at night" in body
