"""Restricted-resource checks for HTML and Markdown rendering."""

from __future__ import annotations

import re

from .errors import DocumentSecurityError

_RESOURCE_REFERENCE = re.compile(
    r"""(?ix)
    (?:href|src|srcset)\s*=\s*["']?\s*(?P<html>[^"'>]+)
    |@import\s+(?P<import>[^;\s]+)
    |url\s*\(\s*["']?(?P<css>[^)"']+)
    |!\[[^\]]*\]\(\s*(?P<markdown>[^)\s]+)
    """
)


def validate_restricted_render_resources(
    text: str, *, approved_asset_names: frozenset[str] = frozenset()
) -> None:
    """Reject all fetches except embedded data and explicitly copied asset names."""

    for match in _RESOURCE_REFERENCE.finditer(text):
        raw = next((value for value in match.groupdict().values() if value), "")
        value = raw.strip().strip("\"'")
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
