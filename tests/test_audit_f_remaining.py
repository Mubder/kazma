"""Audit F remaining: soft-nav scope, TUI mouths, swarm notify hook."""

from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent


def test_soft_nav_does_not_hard_reload_inspectors():
    nav = (_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "modules" / "nav.js").read_text(
        encoding="utf-8"
    )
    # SSE / Alpine-app shells stay hard. Inspectors go through soft-nav.
    assert "'/chat'" in nav
    assert "'/ide'" in nav
    assert "'/memory'" not in nav.split("HARD_RELOAD_ALWAYS")[1].split("]")[0]
    assert "'/documents'" not in nav.split("HARD_RELOAD_ALWAYS")[1].split("]")[0]
    assert "'/replay'" not in nav.split("HARD_RELOAD_ALWAYS")[1].split("]")[0]
    assert "documents" in nav  # PAGE_SCRIPT_RE


def test_tui_documents_and_dashboard_are_mouths():
    docs = (_ROOT / "kazma-tui" / "kazma_tui" / "documents.py").read_text(encoding="utf-8")
    dash = (_ROOT / "kazma-tui" / "kazma_tui" / "dashboard.py").read_text(encoding="utf-8")
    tasks = (
        _ROOT / "kazma-ui" / "kazma_ui" / "swarm_panel" / "routes_tasks.py"
    ).read_text(encoding="utf-8")
    assert "get_ingestion_service" not in docs
    assert "/api/documents" in docs
    assert "/api/swarm/status" in dash
    assert "maybe_notify_dispatch" in tasks


def test_tutorial_does_not_claim_tui_email_voice_tabs():
    tut = (_ROOT / "kazma-tui" / "kazma_tui" / "widgets" / "tutorial.py").read_text(
        encoding="utf-8"
    )
    assert "Email / voice / image" in tut
    assert "not extra TUI tabs" in tut
