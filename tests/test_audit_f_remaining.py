"""Audit F remaining: soft-nav scope, TUI mouths, swarm notify hook."""

from __future__ import annotations

from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent


def test_soft_nav_does_not_hard_reload_inspectors():
    nav = (_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "modules" / "nav.js").read_text(
        encoding="utf-8"
    )
    # Every page soft-navs; chat/ide/swarm teardown is in page scripts.
    hard = nav.split("HARD_RELOAD_ALWAYS")[1].split("]")[0]
    assert "'/chat'" not in hard
    assert "'/ide'" not in hard
    assert "'/swarm'" not in hard
    assert "'/settings'" not in hard
    assert "'/dashboard'" not in hard
    assert "'/agents'" not in hard
    assert "'/skills'" not in hard
    assert "'/mcp'" not in hard
    assert "'/memory'" not in hard
    assert "'/documents'" not in hard
    assert "'/replay'" not in hard
    assert "isSoftNavPageScript" in nav
    assert "memory_console.js" in nav
    assert "runInlinePageScripts" in nav
    assert "teardownLiveSockets" in nav


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
