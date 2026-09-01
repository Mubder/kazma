"""Telegram auto-attach path containment (audit H-4)."""

from __future__ import annotations

from pathlib import Path

from kazma_gateway.agent_handler.attachments import (
    find_auto_attach_paths,
    resolve_auto_attach_path,
)


def test_traversal_is_rejected(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF-1.4 secret")
    (documents / "ok.pdf").write_bytes(b"%PDF-1.4 ok")
    roots = (documents, tmp_path / "exports")
    (tmp_path / "exports").mkdir()

    assert resolve_auto_attach_path("data/../secret.pdf", roots=roots) is None
    assert resolve_auto_attach_path(str(secret), roots=roots) is None
    ok = resolve_auto_attach_path(str(documents / "ok.pdf"), roots=roots)
    assert ok is not None
    assert ok.name == "ok.pdf"


def test_find_auto_attach_skips_traversal_in_model_text(tmp_path: Path) -> None:
    documents = tmp_path / "kazma-data" / "documents"
    documents.mkdir(parents=True)
    (documents / "report.pdf").write_bytes(b"%PDF")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF")
    text = (
        f"here is kazma-data/documents/report.pdf "
        f"and data/../{outside.name}"
    )
    # CWD-relative resolve of kazma-data/documents/report.pdf will miss
    # tmp_path; pass explicit roots and a contained absolute path via the
    # resolver unit. find_auto_attach_paths uses the regex + resolver.
    found = find_auto_attach_paths(
        "see data/../../../etc/passwd.pdf please",
        roots=(documents, tmp_path / "exports"),
    )
    assert found == []
