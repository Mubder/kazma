"""Intent engine entity resolution tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.agent.intent.entities import resolve_entities
from kazma_core.agent.intent.heuristics import detect_acts
from kazma_core.agent.intent.types import ActKind, EntitySet


class TestAttachmentResolution:
    def test_pinned_attachment_resolved(self, tmp_path, monkeypatch):
        """Attachment path inside a temp workspace → resolved."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        f = ws / "cal.pdf"
        f.write_bytes(b"%PDF-1.4 test")
        monkeypatch.setattr(
            "kazma_core.workspace.binding.resolve_active_root", lambda: ws
        )
        atts = [{"kind": "file", "mime": "application/pdf", "path": str(f), "filename": "cal.pdf"}]
        acts = detect_acts("reproduce this PDF", atts)
        entities = resolve_entities(text="reproduce this PDF", attachments=atts, acts=acts)
        assert len(entities.files) >= 1
        assert entities.files[0].filename == "cal.pdf"
        assert "source_file" not in entities.unresolved

    def test_no_attachment_unresolved(self):
        acts = detect_acts("reproduce this PDF")
        entities = resolve_entities(text="reproduce this PDF", attachments=None, acts=acts)
        assert "source_file" in entities.unresolved

    def test_two_attachments_ambiguous(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "a.pdf").write_bytes(b"%PDF-a")
        (ws / "b.pdf").write_bytes(b"%PDF-b")
        monkeypatch.setattr(
            "kazma_core.workspace.binding.resolve_active_root", lambda: ws
        )
        atts = [
            {"kind": "file", "mime": "application/pdf", "path": str(ws / "a.pdf"), "filename": "a.pdf"},
            {"kind": "file", "mime": "application/pdf", "path": str(ws / "b.pdf"), "filename": "b.pdf"},
        ]
        acts = detect_acts("reproduce this PDF", atts)
        entities = resolve_entities(text="reproduce this PDF", attachments=atts, acts=acts)
        # With no filename named in text, should be ambiguous
        assert "source_file" in entities.ambiguous or len(entities.files) != 1

    def test_unique_filename_resolved(self, tmp_path, monkeypatch):
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "cal.pdf").write_bytes(b"%PDF-cal")
        (ws / "other.pdf").write_bytes(b"%PDF-other")
        monkeypatch.setattr(
            "kazma_core.workspace.binding.resolve_active_root", lambda: ws
        )
        atts = [
            {"kind": "file", "mime": "application/pdf", "path": str(ws / "cal.pdf"), "filename": "cal.pdf"},
            {"kind": "file", "mime": "application/pdf", "path": str(ws / "other.pdf"), "filename": "other.pdf"},
        ]
        text = "reproduce cal.pdf with better templates"
        acts = detect_acts(text, atts)
        entities = resolve_entities(text=text, attachments=atts, acts=acts)
        assert len(entities.files) == 1
        assert entities.files[0].filename == "cal.pdf"

    def test_no_global_mtime_scan(self, tmp_path):
        """Do NOT pick up files from global attachments dir when attachments=[]."""
        # Create a file in a non-pinned location
        stray = tmp_path / "stray.pdf"
        stray.write_bytes(b"%PDF-stray")
        acts = detect_acts("reproduce this PDF")
        entities = resolve_entities(text="reproduce this PDF", attachments=None, acts=acts)
        # Must NOT find the stray file
        found_stray = any("stray" in f.filename for f in entities.files)
        assert not found_stray

    def test_inline_content_no_source_needed(self):
        """'write me a PDF of the notes' — inline content, no source_file required."""
        text = "write me a PDF of these notes: The meeting covered budget, timeline, and staffing"
        acts = detect_acts(text)
        entities = resolve_entities(text=text, attachments=None, acts=acts)
        # inline_content=True means no unresolved
        assert "source_file" not in entities.unresolved
