"""Entity resolution — file/path resolution from attachments and text."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from kazma_core.agent.intent.types import ActKind, EntitySet, ResolvedFile

__all__ = ["resolve_entities"]

logger = logging.getLogger(__name__)

_EXPLICIT_PATH_RE = re.compile(
    r"(?:\"([^\"]+\.\w{2,5})\"|'([^']+\.\w{2,5})'"
    r"|(\b[\w\-./\\]+\.(?:pdf|docx|xlsx|pptx|md|txt|csv)\b))",
    re.IGNORECASE,
)


def _access_check(path: Path) -> bool:
    """Workspace-containment access check."""
    try:
        from kazma_core.workspace.path_policy import check_path_access

        return check_path_access(path, "read").allowed
    except Exception:
        return False


def _resolve_candidate(raw: str, filename: str = "", mime: str = "") -> ResolvedFile | None:
    """Resolve a single file candidate. Returns None if not accessible."""
    p = Path(raw)
    if p.is_absolute():
        if p.is_file() and _access_check(p):
            return ResolvedFile(path=str(p), filename=filename or p.name, mime=mime)
        return None

    # Relative: resolve against workspace root
    try:
        from kazma_core.workspace.binding import resolve_active_root

        root = resolve_active_root()
        resolved = root / p
        if resolved.is_file() and _access_check(resolved):
            return ResolvedFile(path=str(resolved), filename=filename or p.name, mime=mime)
    except Exception:
        pass

    return None


def resolve_entities(
    *,
    text: str,
    attachments: list[dict] | None,
    acts: tuple[Any, ...],
) -> EntitySet:
    """Resolve file entities from attachments and text.

    Never scans global directories by mtime. Never returns paths that
    failed the workspace access check.
    """
    files: list[ResolvedFile] = []
    unresolved: list[str] = []
    ambiguous: list[str] = []

    # 1. Build candidates from pinned attachments
    att_candidates: list[ResolvedFile] = []
    for att in attachments or []:
        path = att.get("path") or ""
        filename = att.get("filename") or ""
        mime = att.get("mime") or ""
        if not path and not filename:
            continue
        if path:
            rf = _resolve_candidate(path, filename=filename, mime=mime)
            if rf:
                att_candidates.append(rf)
                continue
        # Filename-only: look under workspace root
        if filename:
            rf = _resolve_candidate(filename, filename=filename, mime=mime)
            if rf:
                att_candidates.append(rf)

    # 2. Explicit paths in text
    text_candidates: list[ResolvedFile] = []
    for m in _EXPLICIT_PATH_RE.finditer(text or ""):
        raw = next((g for g in m.groups() if g), "")
        if raw:
            rf = _resolve_candidate(raw)
            if rf:
                text_candidates.append(rf)

    # Merge, dedup by path
    all_files: list[ResolvedFile] = []
    seen_paths: set[str] = set()
    for rf in att_candidates + text_candidates:
        rp = rf.path.lower()
        if rp not in seen_paths:
            seen_paths.add(rp)
            all_files.append(rf)

    # 3. Check if document_generate needs a source
    act_kinds = {a.kind for a in acts}
    needs_source = ActKind.DOCUMENT_GENERATE in act_kinds

    if needs_source:
        # Check if inline content is available (from-scratch generation)
        has_inline = any(
            a.kind == ActKind.DOCUMENT_GENERATE and a.slots.get("inline_content")
            for a in acts
        )
        if not has_inline:
            if len(all_files) == 0:
                unresolved.append("source_file")
            elif len(all_files) > 1:
                # Try to disambiguate by filename stem in text
                text_lower = (text or "").lower()
                named = [
                    rf for rf in all_files
                    if Path(rf.filename).stem.lower() in text_lower
                ]
                if len(named) == 1:
                    all_files = named
                else:
                    ambiguous.append("source_file")

    files_tuple = tuple(all_files)
    return EntitySet(
        files=files_tuple,
        unresolved=tuple(unresolved),
        ambiguous=tuple(ambiguous),
    )
