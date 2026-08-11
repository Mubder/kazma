"""Single path-access policy for file/IDE tools.

``check_path_access`` is the SoT for "may this resolved path be used?":

1. Under active workspace → allow  
2. Under durable ``workspace.extra_roots`` with sufficient mode → allow  
3. Under session path grant for current thread → allow  
4. Global ``allow_absolute_paths()`` (dev escape hatch) → allow  
5. Else deny (with a smooth agent-facing recovery hint)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from kazma_core.workspace.binding import allow_absolute_paths, resolve_active_root
from kazma_core.workspace.path_grants import (
    AccessMode,
    list_durable_roots,
    list_session_grants,
    mode_rank,
    path_under_root,
)

__all__ = [
    "PathAccessResult",
    "check_path_access",
    "denied_message",
    "is_path_allowed",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PathAccessResult:
    allowed: bool
    reason: str
    mode: AccessMode
    via: str  # workspace | durable | session | absolute | denied
    resolved: str
    workspace: str
    grant_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "mode": self.mode,
            "via": self.via,
            "resolved": self.resolved,
            "workspace": self.workspace,
            "grant_path": self.grant_path,
        }


def denied_message(
    path: str,
    mode: AccessMode = "read",
    *,
    result: PathAccessResult | None = None,
) -> str:
    """Agent-facing denial with recovery instructions (smooth UX)."""
    res = result or check_path_access(path, mode)
    action = "read" if mode == "read" else "write/modify"
    return (
        f"Safety: {action} outside the active workspace is not allowed.\n"
        f"  path: {res.resolved}\n"
        f"  workspace: {res.workspace}\n"
        "To proceed, request a path grant (user must approve):\n"
        f"  request_path_access(path={path!r}, mode={mode!r}, scope='session')\n"
        "Or add a durable extra folder in Settings → Workspace → Extra folders.\n"
        "After grant, retry the same file tool."
    )


def check_path_access(
    path: str | Path,
    mode: AccessMode | str = "read",
    *,
    thread_id: str | None = None,
) -> PathAccessResult:
    """Return whether *path* may be accessed at *mode*."""
    need: AccessMode = "write" if str(mode).lower() in ("write", "rw") else "read"
    workspace = resolve_active_root()
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError as exc:
        return PathAccessResult(
            allowed=False,
            reason=f"invalid path: {exc}",
            mode=need,
            via="denied",
            resolved=str(path),
            workspace=str(workspace),
        )

    # 1) Workspace
    if path_under_root(resolved, workspace):
        return PathAccessResult(
            allowed=True,
            reason="inside active workspace",
            mode=need,
            via="workspace",
            resolved=str(resolved),
            workspace=str(workspace),
            grant_path=str(workspace),
        )

    need_r = mode_rank(need)

    # 2) Durable extra roots
    for grant in list_durable_roots():
        try:
            root = Path(grant.path)
        except Exception:
            continue
        if path_under_root(resolved, root) and mode_rank(grant.mode) >= need_r:
            return PathAccessResult(
                allowed=True,
                reason=f"durable extra root ({grant.mode})",
                mode=need,
                via="durable",
                resolved=str(resolved),
                workspace=str(workspace),
                grant_path=grant.path,
            )

    # 3) Session grants
    tid = thread_id
    if tid is None:
        try:
            from kazma_core.safety.hitl import get_current_thread_id

            tid = get_current_thread_id()
        except Exception:
            tid = None
    for grant in list_session_grants(tid):
        try:
            root = Path(grant.path)
        except Exception:
            continue
        if path_under_root(resolved, root) and mode_rank(grant.mode) >= need_r:
            return PathAccessResult(
                allowed=True,
                reason=f"session grant ({grant.mode})",
                mode=need,
                via="session",
                resolved=str(resolved),
                workspace=str(workspace),
                grant_path=grant.path,
            )

    # 4) Dev escape hatch
    if allow_absolute_paths():
        return PathAccessResult(
            allowed=True,
            reason="allow_absolute_paths enabled",
            mode=need,
            via="absolute",
            resolved=str(resolved),
            workspace=str(workspace),
        )

    return PathAccessResult(
        allowed=False,
        reason="outside workspace; no grant",
        mode=need,
        via="denied",
        resolved=str(resolved),
        workspace=str(workspace),
    )


def is_path_allowed(
    path: str | Path,
    mode: AccessMode | str = "read",
    *,
    thread_id: str | None = None,
) -> bool:
    return check_path_access(path, mode, thread_id=thread_id).allowed
