"""Restricted-resource checks for HTML and Markdown rendering."""

from __future__ import annotations

import re

from .errors import DocumentSecurityError

#: Each way a document names a resource, scanned on its own (Sonar S8786,
#: 2026-11-16). As one alternation an HTML value swallowed a Markdown image
#: after it (``src=data:x ![a](http://elsewhere/p.png)`` passed: the value
#: started with ``data:``), and the Markdown alt read every ``![`` after it to
#: the end of the text, quadratic on a run of them. The alt no longer crosses
#: a later ``![``: that one is tried on its own and names the same address.
_RESOURCE_REFERENCES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"""(?:href|src|srcset)\s*=\s*["']?\s*(?P<value>[^"'>]+)""",
        r"@import\s+(?P<value>[^;\s]+)",
        r"""url\s*\(\s*["']?(?P<value>[^)"']+)""",
        r"!\[(?:[^\]!]++|!(?!\[))*+\]\(\s*(?P<value>[^)\s]+)",
    )
)


def validate_restricted_render_resources(
    text: str, *, approved_asset_names: frozenset[str] = frozenset()
) -> None:
    """Reject all fetches except embedded data and explicitly copied asset names."""

    for pattern in _RESOURCE_REFERENCES:
        for match in pattern.finditer(text):
            value = match.group("value").strip().strip("\"'")
            lowered = value.lower()
            if lowered.startswith("data:"):
                continue
            if value.startswith("#"):
                continue
            normalized = value.replace("\\", "/").lstrip("./")
            if (
                normalized
                and "/" not in normalized
                and normalized in approved_asset_names
            ):
                continue
            raise DocumentSecurityError(
                "External or unapproved local resources are forbidden during rendering",
                code="external_resource_denied",
            )
