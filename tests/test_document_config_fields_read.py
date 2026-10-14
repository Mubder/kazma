"""Every document setting changes something: each field has a reader.

2026-10-13 (DOC-A2, found by the Documents documentation review).
``documents.security.fence_llm_content``, ``allow_encrypted_documents`` and
``allow_external_render_resources`` were read from the settings store,
validated and carried in every policy snapshot, and nothing acted on them:
document text is always fenced, encrypted files are always refused, external
render resources are always denied. The gate built for them found five more
(``documents.intake.remote_fetch_max_redirects`` / ``timeout_seconds``,
``documents.ocr.page_concurrency``, ``documents.retention.failed_days``,
``documents.quotas.tenant_daily_pages``). All eight were removed. None was
shipped in ``kazma.yaml`` or written by any page, so no stored copy was
seeded to retire; a copy someone typed is a row nothing reads.

A field must be read outside ``documents/config.py`` (``.name`` or
``getattr(..., "name")``) in product code. Negative control: a fake field.
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_CONFIG = "kazma-core/kazma_core/documents/config.py"


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "kazma-*/kazma_*/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in sorted(set(listed)) if rel != _CONFIG}


def _unread(names, sources) -> list[str]:
    missing = []
    for name in names:
        pattern = re.compile(rf"\.{re.escape(name)}\b|getattr\([^\n]*?[\"']{re.escape(name)}[\"']")
        if not any(pattern.search(source) for source in sources.values()):
            missing.append(name)
    return missing


def test_every_document_setting_has_a_reader():
    from kazma_core.documents.config import DocumentConfig

    names = [field.name for field in dataclasses.fields(DocumentConfig)]
    assert _unread(names, _product_sources()) == []


def test_a_setting_nothing_reads_is_caught():
    # Negative control: the shape of the eight removed settings.
    sources = {"x.py": "if config.security_malware_scan == 'on':\n    pass\nflag = getattr(get_cfg(), 'render_embed_html_fonts', True)\n"}
    assert _unread(["security_malware_scan", "render_embed_html_fonts", "security_fence_llm_content"], sources) == [
        "security_fence_llm_content",
    ]
