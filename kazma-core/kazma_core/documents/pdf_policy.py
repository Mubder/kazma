"""One bounded structural active-content policy for PDF intake and mutation.

Walk dictionary/array objects, not decoded page streams or extracted text.
Additional-action dictionaries and /Next chains need no special-case recursion:
their reachable actions pass through the same policy, including indirect ones.

Refused: script (JavaScript, XFA forms, whose XML packets carry scripts),
anything that runs, opens or fetches outside the document (Launch, remote
GoTo, SubmitForm, ImportData, renditions, rich media, sound and movie
actions), embedded files (also a bare /EF without /Type), and links whose
target is not an absolute http, https or mailto URI (file: and UNC targets
leak credentials when opened). Navigation inside the document -- GoTo,
Named, Thread, ResetForm, Hide, SetOCGState, Trans -- and web links stay.
Applied on intake (sniff), on every mutation input and output, and on every
rendered PDF, always inside the bounded worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import DocumentSecurityError

_DANGEROUS_KEYS = frozenset(
    {"/JS", "/JavaScript", "/Launch", "/RichMedia", "/EmbeddedFiles", "/EF", "/XFA"}
)
_DANGEROUS_ACTIONS = frozenset(
    {
        "/JavaScript", "/JS", "/Launch", "/RichMedia", "/RichMediaExecute",
        "/GoToE", "/GoToR", "/SubmitForm", "/ImportData", "/Rendition",
        "/Sound", "/Movie",
    }
)
# Matched case-insensitively (Q04 OBS-02, 2026-10-15). PDF names are
# case-sensitive per the spec, so a wrong-case /JAVASCRIPT is inert in most
# viewers and pypdf already decodes the genuinely dangerous #-escape form
# (/J#61vaScript -> /JavaScript); folding is defence-in-depth and matches the
# sniffer's IGNORECASE regex. pypdf decodes #-escapes before we see the name.
_DANGEROUS_KEYS_FOLDED = frozenset(key.lower() for key in _DANGEROUS_KEYS)
_DANGEROUS_ACTIONS_FOLDED = frozenset(action.lower() for action in _DANGEROUS_ACTIONS)
_REMOTE_GOTO_ACTIONS_FOLDED = frozenset({"/goto", "/thread"})
_SAFE_URI_PREFIXES = ("http://", "https://", "mailto:")
# Roles whose scalar value the policy judges: resolved even when the same
# indirect object was already walked under another role.
_JUDGED_ROLES = frozenset({"/S", "/Type", "/URI"})


def _uri_text(value: Any) -> str | None:
    if isinstance(value, bytes):
        return value.decode("latin-1", errors="replace")
    if isinstance(value, str):
        return str(value)
    return None


def assert_pdf_safe(
    reader: Any, *, max_objects: int = 100_000, max_depth: int = 128
) -> None:
    """Reject active content or an indeterminate/budget-exhausted object graph.

    Called only after a PDF reader is available, so pypdf remains an optional
    import for modules which merely expose the document service.
    """
    from pypdf.generic import IndirectObject

    if max_objects < 1 or max_depth < 1:
        raise ValueError("PDF policy budgets must be positive")
    try:
        root = reader.trailer["/Root"]
        if not isinstance(root, Mapping):
            raise DocumentSecurityError("PDF security inspection could not verify the catalog")
        pending: list[tuple[Any, int, str | None]] = [(root, 0, None)]
        seen_refs: set[tuple[int, int, int]] = set()
        seen_containers: set[int] = set()
        visited = 0
        while pending:
            value, depth, role = pending.pop()
            visited += 1
            if visited > max_objects or depth > max_depth:
                raise DocumentSecurityError(
                    "PDF security inspection exceeded its structural budget"
                )
            if isinstance(value, IndirectObject):
                identity = (id(value.pdf), value.idnum, value.generation)
                # A reference can occur under /S or /Type as well as elsewhere;
                # resolve policy-sensitive scalar roles even when visited.
                if identity in seen_refs and role not in _JUDGED_ROLES:
                    continue
                seen_refs.add(identity)
                resolved = value.get_object()
                if resolved is None:
                    raise DocumentSecurityError("PDF security inspection could not verify an object reference")
                pending.append((resolved, depth + 1, role))
                continue
            if role == "/S" and str(value).lower() in _DANGEROUS_ACTIONS_FOLDED:
                raise DocumentSecurityError("PDF contains executable actions")
            if role == "/Type" and str(value).lower() == "/embeddedfile":
                raise DocumentSecurityError("PDF contains embedded-file content")
            if role == "/URI":
                # A link action's target (the catalog's /URI is a dict with
                # /Base and is walked as a container instead).
                target = _uri_text(value)
                if target is not None and not target.strip().lower().startswith(_SAFE_URI_PREFIXES):
                    raise DocumentSecurityError(
                        "PDF links to a location outside the web (file, network share or relative path)"
                    )
            if isinstance(value, (Mapping, list, tuple)):
                identity = id(value)
                if identity in seen_containers:
                    continue
                seen_containers.add(identity)
                if isinstance(value, Mapping):
                    if any(str(key).lower() in _DANGEROUS_KEYS_FOLDED for key in value):
                        raise DocumentSecurityError(
                            "PDF JavaScript, Launch, rich media or attachments are disabled"
                        )
                    # /GoTo and /Thread are in-document navigation and so are
                    # not in _DANGEROUS_ACTIONS -- but either one with an /F
                    # file specification is a REMOTE go-to (another file, a UNC
                    # share): the viewer that opens it leaks credentials or is
                    # phished. /GoToR and /GoToE (already refused) are the
                    # explicit remote forms; /GoTo + /F and /Thread + /F are
                    # the same reach by another spelling. Q04 FIND-01, 2026-10-15.
                    subtype = value.get("/S")
                    if isinstance(subtype, IndirectObject):
                        subtype = subtype.get_object()
                    if str(subtype).lower() in _REMOTE_GOTO_ACTIONS_FOLDED and any(
                        str(key).lower() == "/f" for key in value
                    ):
                        raise DocumentSecurityError(
                            "PDF references a remote file (a GoTo or Thread action with a file specification)"
                        )
                    children = ((item, str(key)) for key, item in value.items())
                else:
                    children = ((item, None) for item in value)
                for child, child_role in children:
                    if visited + len(pending) >= max_objects:
                        raise DocumentSecurityError(
                            "PDF security inspection exceeded its structural budget"
                        )
                    pending.append((child, depth + 1, child_role))
    except DocumentSecurityError:
        raise
    except Exception as exc:
        raise DocumentSecurityError(
            "PDF security inspection could not verify the document"
        ) from exc
