"""Deployment root confinement for workspace selection, creation and switching."""
from __future__ import annotations

import os
from pathlib import Path


def validate_root(path: Path) -> Path:
    """Validate a proposed root without changing the active workspace binding."""
    root = path.resolve()
    allowed = os.environ.get("KAZMA_WORKSPACE_ROOT", "").strip()
    production = os.environ.get("KAZMA_PRODUCTION", "").strip().lower() in (
        "1", "true", "on", "yes",
    )
    if production and not allowed:
        raise PermissionError(
            "KAZMA_WORKSPACE_ROOT is required in production. "
            "Set it to the parent directory of allowed workspaces."
        )
    if allowed:
        try:
            root.relative_to(Path(allowed).resolve())
        except ValueError:
            raise PermissionError("Workspace path is outside the allowed workspace root.") from None
    return root
