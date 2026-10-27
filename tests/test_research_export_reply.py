"""A research export that saved nothing says so (audit 2026-10-26 AUD-056).

The document generator answers ``Error: ...`` when it writes no file. The
export routes returned that as ``{"ok": true, "message": "Error: ..."}``, so
the Research page toasted "Exported" and opened nothing. And the download
link carried the file name unencoded: a topic's '&' or '#' cut it short.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kazma_ui.research_panel import routes

REPORT = "# Report\n\n## Background\n\nhello\n"


def _body(response) -> dict:
    return json.loads(response.body)


def test_a_generator_error_is_a_failed_export() -> None:
    reply = routes._export_reply("pdf", "Error: LibreOffice is not installed")
    assert reply.status_code == 500
    body = _body(reply)
    assert body["ok"] is False
    assert body["error"] == "LibreOffice is not installed"
    assert "download_url" not in body


def test_an_answer_with_no_saved_file_is_a_failed_export() -> None:
    reply = routes._export_reply("docx", "")
    assert reply.status_code == 500
    assert _body(reply)["ok"] is False and _body(reply)["error"]


def test_a_saved_file_is_a_success_with_an_encoded_link() -> None:
    reply = routes._export_reply("pdf", "Report ready.\n  Saved to: /data/documents/Q&A #1.pdf")
    assert reply.status_code == 200
    body = _body(reply)
    assert body["ok"] is True and body["filename"] == "Q&A #1.pdf"
    assert body["download_url"] == "/api/research/download?path=Q%26A%20%231.pdf"


def test_the_route_answers_the_failure(tmp_path, monkeypatch) -> None:
    """Through the paper export: the page's ``data.error`` branch is reached."""
    from kazma_core.tools import research_pipeline as rp

    ws = tmp_path / "ws"
    run = ws / "research" / "reports" / "topic-1"
    run.mkdir(parents=True)
    (run / "report.md").write_text(REPORT, encoding="utf-8")
    monkeypatch.setattr(rp, "_candidate_report_roots", lambda: [Path(ws).resolve()])

    async def failing_generate(fmt, title, sections):
        return "Error: PDF engine unavailable"

    monkeypatch.setattr(routes, "_generate", failing_generate)
    app = FastAPI()
    app.include_router(routes.create_research_router())
    got = TestClient(app).post(
        "/api/research/papers/export",
        json={"report_path": "research/reports/topic-1/report.md", "format": "pdf"},
    )
    assert got.status_code == 500
    assert got.json()["ok"] is False
    assert got.json()["error"] == "PDF engine unavailable"
