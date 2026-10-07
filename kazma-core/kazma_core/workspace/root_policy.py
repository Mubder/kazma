"""Deployment root confinement for workspace selection, creation and switching."""
from __future__ import annotations

import os
from pathlib import Path


def validate_root(path: Path) -> Path:
    """Validate a proposed root without changing the active workspace binding."""
    root = path.resolve()
    allowed = os.environ.get("KAZMA_WORKSPACE_ROOT", "").strip()
    from kazma_core.safety.deployment_policy import configured_workspace_roots, policy_read_failures

    try:
        configured = configured_workspace_roots()
    except policy_read_failures() as exc:
        raise PermissionError("Workspace root policy is unavailable; access refused.") from exc
    production = os.environ.get("KAZMA_PRODUCTION", "").strip().lower() in (
        "1", "true", "on", "yes",
    )
    if production and not allowed and not configured:
        raise PermissionError(
            "KAZMA_WORKSPACE_ROOT or Settings security.workspace_roots is required in production. "
            "Configure the allowed workspace directories."
        )
    if allowed:
        if not Path(allowed).is_absolute():
            raise PermissionError("KAZMA_WORKSPACE_ROOT must be absolute.")
        try:
            root.relative_to(Path(allowed).resolve())
        except ValueError:
            raise PermissionError("Workspace path is outside the allowed workspace root.") from None
    if configured and not any(root.is_relative_to(parent) for parent in configured):
        raise PermissionError("Workspace path is outside the configured workspace roots.")
    return root
