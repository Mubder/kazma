"""The WeasyPrint HTML->PDF path against WeasyPrint 70's fetcher API.

Found 2026-10-13 when the container worker image was the first place
WeasyPrint's native libraries were present: the renderer imported
``default_url_fetcher`` (gone in WeasyPrint 70, the locked floor), and with no
approved assets ``iterdir()`` on the missing asset folder raised, which a
catch-all reported as "external resource denied". Both made every HTML->PDF
job fail. This file pins both against a stub of the 70 API, so it runs where
WeasyPrint's native libraries are absent; the real library is exercised in
``tests/test_document_worker_container_docker.py``.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

import pytest
from kazma_core.documents import renderer_worker
from kazma_core.documents.renderer_worker import WorkerError

from tests._module_stubs import stub_modules


class _FatalURLFetchingError(Exception):
    pass


class _URLFetcher:
    """WeasyPrint 70's base fetcher, reduced to what the renderer relies on."""

    def __init__(self, timeout=10, ssl_context=None, http_headers=None,
                 allowed_protocols=None, allow_redirects=True, fail_on_errors=False, **kwargs):
        self.allowed_protocols = allowed_protocols
        self.allow_redirects = allow_redirects
        self.fail_on_errors = fail_on_errors
        self.fetched: list[str] = []

    def fetch(self, url: str, headers: Any = None) -> str:
        self.fetched.append(url)
        return f"fetched:{url}"


def _weasyprint_70(rendered: list[dict[str, Any]]) -> dict[str, Any]:
    package = types.ModuleType("weasyprint")
    urls = types.ModuleType("weasyprint.urls")
    urls.URLFetcher = _URLFetcher
    urls.FatalURLFetchingError = _FatalURLFetchingError

    class HTML:
        def __init__(self, *, string: str, base_url: str, url_fetcher: Any) -> None:
            rendered.append({"string": string, "base_url": base_url, "fetcher": url_fetcher})

        def write_pdf(self, output: Path) -> None:
            Path(output).write_bytes(b"%PDF-1.7 stub")

    package.HTML = HTML
    package.urls = urls
    # No default_url_fetcher: WeasyPrint 70 removed it.
    return {"weasyprint": package, "weasyprint.urls": urls}


def test_html_without_assets_renders(tmp_path: Path) -> None:
    rendered: list[dict[str, Any]] = []
    output = tmp_path / "output.pdf"
    with stub_modules(_weasyprint_70(rendered)):
        renderer_worker._weasy_pdf(output, "<p>Hello مرحبا</p>", tmp_path / "assets")
    assert output.read_bytes().startswith(b"%PDF")
    fetcher = rendered[0]["fetcher"]
    assert isinstance(fetcher, _URLFetcher)
    assert fetcher.allowed_protocols == {"data", "file"}
    assert fetcher.allow_redirects is False and fetcher.fail_on_errors is True


def test_the_fetcher_allows_data_and_approved_assets_only(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "logo.png").write_bytes(b"png")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    rendered: list[dict[str, Any]] = []
    with stub_modules(_weasyprint_70(rendered)):
        renderer_worker._weasy_pdf(tmp_path / "output.pdf", '<img src="logo.png">', assets)
        fetcher = rendered[0]["fetcher"]
        assert fetcher.fetch("data:image/png;base64,AAAA") == "fetched:data:image/png;base64,AAAA"
        assert fetcher.fetch((assets / "logo.png").as_uri()).startswith("fetched:file:")
        for denied in ("https://example.com/x.png", outside.as_uri(),
                       (assets / ".." / "secret.txt").as_uri(), "ftp://example.com/x"):
            with pytest.raises(_FatalURLFetchingError):
                fetcher.fetch(denied)
    assert all("secret" not in url for url in fetcher.fetched)


def test_a_denied_fetch_stops_the_render_as_external_resource_denied(tmp_path: Path) -> None:
    rendered: list[dict[str, Any]] = []
    modules = _weasyprint_70(rendered)

    def write_pdf(self: Any, output: Path) -> None:
        rendered[-1]["fetcher"].fetch("https://example.com/tracker.png")

    modules["weasyprint"].HTML.write_pdf = write_pdf
    with stub_modules(modules), pytest.raises(WorkerError) as caught:
        renderer_worker._weasy_pdf(tmp_path / "output.pdf", "<p>x</p>", tmp_path / "assets")
    assert caught.value.code == "external_resource_denied"


def test_an_unapproved_reference_is_refused_before_rendering(tmp_path: Path) -> None:
    rendered: list[dict[str, Any]] = []
    with stub_modules(_weasyprint_70(rendered)), pytest.raises(WorkerError) as caught:
        renderer_worker._weasy_pdf(
            tmp_path / "output.pdf", '<img src="https://example.com/x.png">', tmp_path / "assets"
        )
    assert caught.value.code == "external_resource_denied"
    assert rendered == []  # nothing reached WeasyPrint


def test_the_old_import_fails_against_weasyprint_70() -> None:
    # Negative control: the import the renderer used until 2026-10-13.
    rendered: list[dict[str, Any]] = []
    with stub_modules(_weasyprint_70(rendered)), pytest.raises(ImportError):
        from weasyprint import default_url_fetcher  # noqa: F401


def test_the_health_probe_reads_unavailable_when_the_renderer_cannot_import(monkeypatch) -> None:
    from kazma_core.documents import renderers

    rendered: list[dict[str, Any]] = []
    modules = _weasyprint_70(rendered)
    with stub_modules(modules):
        assert renderers._weasyprint_health() == (renderers.RendererReadiness.READY, None)
    broken = dict(modules)
    broken["weasyprint.urls"] = types.ModuleType("weasyprint.urls")  # 70's names missing
    with stub_modules(broken):
        readiness, reason = renderers._weasyprint_health()
    assert readiness is renderers.RendererReadiness.UNAVAILABLE and "ImportError" in (reason or "")
