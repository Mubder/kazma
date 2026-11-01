"""The paper the agent wrote from a research run, kept beside that run.

A research run registers its working report (sources, evidence, synthesis)
as a paper on the Research page. The paper the agent then writes from it --
a Markdown file turned into a PDF or a Word document -- was on no page: live
2026-10-31 the owner opened the Research page after a Kuwait history run and
found the working report and the chat's raw web searches, while the 29-page
paper sat in the workspace and the document folder.

When ``generate_pdf`` or ``generate_docx`` succeeds in a chat turn that ran
``run_research_pipeline``, the document (and the Markdown it was made from)
is copied into that run's folder under ``final/`` and named on the run's
paper entry (``final``). The run is found by the turn alone
(``proposals.current_turn``, stamped on the entry as ``turn`` when it was
registered): a document made in another turn, or in a chat that ran no
research, is never taken for a research paper. A later call in the same turn
replaces the copy, so the entry names the last version.

Blocking (the settings store, file copies): call it from a thread.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

FINAL_DIR = "final"
#: A paper larger than this is not copied (send_file's limit).
MAX_BYTES = 50 * 1024**2
_KINDS = {"pdf": "pdf_path", "docx": "docx_path"}


def _run_entry(turn: str) -> dict[str, Any] | None:
    """The newest research paper this turn registered, or None."""
    from kazma_core.config_store import get_config_store

    items = get_config_store().get("research.papers_index") or []
    if not isinstance(items, list):
        return None
    for item in items:  # newest first (``_register_paper`` prepends)
        if isinstance(item, dict) and item.get("turn") == turn and item.get("report_path"):
            return item
    return None


def _copy(source: Path, target: Path) -> None:
    """Replace *target* with a complete copy of *source*, never a torn one."""
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".kazma-final-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as out, source.open("rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def keep_final_paper(
    *, turn: str, title: str, document: str | Path, kind: str,
    markdown: str | Path | None = None,
) -> dict[str, Any] | None:
    """Keep *document* as the finished paper of the research run *turn* made.

    Returns the run's ``final`` entry (title, the kept files' paths relative
    to the workspace, when), or None when this turn ran no research or the
    document cannot be kept (too large, missing). Raises on a store or file
    failure: the caller says so, it never fails the document it made.
    """
    if not turn or kind not in _KINDS:
        return None
    entry = _run_entry(turn)
    if entry is None:
        return None
    from kazma_core.tools.research_pipeline import resolve_report_file

    report = resolve_report_file(str(entry["report_path"]))
    if report is None:
        return None
    doc = Path(document)
    if not doc.is_file() or doc.stat().st_size > MAX_BYTES:
        return None
    folder = report.parent / FINAL_DIR
    relative = PurePosixPath(str(entry["report_path"]).replace("\\", "/")).parent / FINAL_DIR
    kept: dict[str, Any] = {}
    _copy(doc, folder / f"paper.{kind}")
    kept[_KINDS[kind]] = str(relative / f"paper.{kind}")
    source = Path(markdown) if markdown else None
    if source is not None and source.is_file() and source.stat().st_size <= MAX_BYTES:
        _copy(source, folder / "paper.md")
        kept["md_path"] = str(relative / "paper.md")
    kept["title"] = str(title or "").strip()[:300] or str(entry.get("topic") or "")[:300]
    kept["updated_at"] = datetime.now(UTC).isoformat()

    from kazma_core.config_store import get_config_store

    report_path = entry["report_path"]
    final: dict[str, Any] = {}

    def attach(current: Any) -> Any:
        items = current if isinstance(current, list) else []
        for item in items:
            if isinstance(item, dict) and item.get("report_path") == report_path:
                merged = dict(item.get("final") or {})
                merged.update(kept)
                item["final"] = merged
                final.update(merged)
        return items

    items = get_config_store().atomic_update("research.papers_index", attach, category="research")
    if not final:
        return None
    try:
        from kazma_core.tools.research_pipeline import _papers_index_path, _write_source_metadata

        index = _papers_index_path()
        if index.parent.is_dir():
            _write_source_metadata(index, items)
    except (OSError, ValueError) as exc:
        # The settings store holds the entry the page reads first.
        logger.warning("[research] finished paper kept, workspace index not updated: %s", exc)
    logger.info("[research] finished paper kept with research run %s: %s",
                entry.get("id"), ", ".join(sorted(k for k in kept if k.endswith("_path"))))
    return final
