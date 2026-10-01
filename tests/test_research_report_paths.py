"""A research route reads a report, never any other file (2026-10-01).

``POST /api/research/papers/export`` took ``report_path`` from the body and
read it when it was an absolute path (or ``..`` led out of the reports
folder); the export landed in the documents folder, which
``/api/research/download`` serves -- any file the server could read could be
fetched. ``GET /api/research/eval`` scored any path on disk (its size, link
and word counts: an oracle for files outside Kazma). ``papers/file`` had been
fixed for the same thing on its own. One rule now serves all four:
``research_pipeline.resolve_report_file``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPORT = "# Report\n\n## Background\n\nhello https://a.example/x https://b.example/y\n"


@pytest.fixture()
def roots(tmp_path, monkeypatch):
    """A workspace with one report, and a secret outside any reports folder."""
    from kazma_core.tools import research_pipeline as rp

    ws = tmp_path / "ws"
    run = ws / "research" / "reports" / "topic-1"
    run.mkdir(parents=True)
    (run / "report.md").write_text(REPORT, encoding="utf-8")
    secret = tmp_path / "secret.env"
    secret.write_text("owner-secret=demo-value\n", encoding="utf-8")
    (ws / "notes.md").write_text("# not a report\n", encoding="utf-8")
    monkeypatch.setattr(rp, "_candidate_report_roots", lambda: [ws.resolve()])
    return ws, run / "report.md", secret


def test_a_report_resolves_by_its_relative_path(roots) -> None:
    from kazma_core.tools.research_pipeline import resolve_report_file

    ws, report, _secret = roots
    assert resolve_report_file("research/reports/topic-1/report.md") == report.resolve()
    assert resolve_report_file("research\\reports\\topic-1\\report.md") == report.resolve()


@pytest.mark.parametrize("raw", [
    "secret",  # placeholder: replaced by the absolute secret path below
    "research/reports/../../secret.env",
    "research/reports/topic-1/../../../notes.md",
    "notes.md",
    "/secret.env",
    "",
    "research/reports/topic-1/report.md\x00",
])
def test_anything_else_does_not(roots, raw) -> None:
    from kazma_core.tools.research_pipeline import resolve_report_file

    _ws, _report, secret = roots
    if raw == "secret":
        raw = str(secret)
    assert resolve_report_file(raw) is None


def test_a_stored_absolute_path_still_has_to_be_a_report(roots) -> None:
    """A session's own report_path may be absolute; it still has to lie in a
    reports folder."""
    from kazma_core.tools.research_pipeline import resolve_report_file

    _ws, report, secret = roots
    assert resolve_report_file(str(report), stored=True) == report.resolve()
    assert resolve_report_file(str(report)) is None  # a request may not name one
    assert resolve_report_file(str(secret), stored=True) is None


@pytest.mark.skipif(os.name == "nt", reason="creating a symlink needs privileges on Windows")
def test_a_link_out_of_the_reports_folder_is_refused(roots) -> None:
    from kazma_core.tools.research_pipeline import resolve_report_file

    ws, _report, secret = roots
    link = ws / "research" / "reports" / "topic-1" / "link.md"
    link.symlink_to(secret)
    assert resolve_report_file("research/reports/topic-1/link.md") is None


@pytest.fixture()
def client(roots, monkeypatch):
    from kazma_ui.research_panel.routes import create_research_router

    seen: list[str] = []

    async def fake_generate(fmt, title, sections):
        seen.append("\n".join(s["body"] for s in sections))
        return "Saved to: /tmp/out.md"

    monkeypatch.setattr("kazma_ui.research_panel.routes._generate", fake_generate)
    app = FastAPI()
    app.include_router(create_research_router())
    c = TestClient(app)
    c.exported = seen
    return c


def test_the_paper_export_reads_only_a_report(client, roots) -> None:
    _ws, _report, secret = roots
    refused = client.post(
        "/api/research/papers/export", json={"report_path": str(secret), "format": "markdown"}
    )
    assert refused.status_code == 404
    traversal = client.post(
        "/api/research/papers/export",
        json={"report_path": "research/reports/../../secret.env", "format": "markdown"},
    )
    assert traversal.status_code == 404
    assert client.exported == []  # nothing was read, so nothing was exported

    ok = client.post(
        "/api/research/papers/export",
        json={"report_path": "research/reports/topic-1/report.md", "format": "markdown"},
    )
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert any("hello" in body for body in client.exported)


def test_the_scorer_measures_only_a_report(client, roots) -> None:
    _ws, _report, secret = roots
    assert client.get("/api/research/eval", params={"path": str(secret)}).status_code == 404
    scored = client.get(
        "/api/research/eval", params={"path": "research/reports/topic-1/report.md"}
    )
    assert scored.status_code == 200 and scored.json()["eval"]["meta"]["url_count"] == 2


def test_the_report_file_route_serves_only_a_report(client, roots) -> None:
    _ws, _report, secret = roots
    assert client.get("/api/research/papers/file", params={"path": str(secret)}).status_code == 404
    got = client.get(
        "/api/research/papers/file", params={"path": "research/reports/topic-1/report.md"}
    )
    assert got.status_code == 200 and "hello" in got.text


def test_the_old_export_resolution_read_any_absolute_file(roots) -> None:
    """Negative control: the export's old resolution, as it was."""
    from kazma_core.tools import research_pipeline as rp

    _ws, _report, secret = roots
    report_path = str(secret).replace("\\", "/")
    target: Path | None = None
    if Path(report_path).is_absolute() and Path(report_path).is_file():
        target = Path(report_path)
    else:
        for root in rp._candidate_report_roots():
            cand = (root / report_path).resolve()
            if cand.is_file():
                target = cand
                break
    assert target is not None and "owner-secret" in target.read_text(encoding="utf-8")
