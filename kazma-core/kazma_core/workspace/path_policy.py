"""Single path-access policy for file/IDE tools.

``check_path_access`` is the SoT for "may this resolved path be used?":

0. Write to one of Kazma's own databases → deny, unconditionally
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

#: SQLite family suffixes, plus the sidecars. The sidecars matter as much as
#: the main file: a writer that can append to ``hitl_gates.db-wal`` decides
#: what the next reader sees without ever opening ``hitl_gates.db``.
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_DB_SIDECARS = ("-wal", "-shm", "-journal")


def control_plane_db_names() -> frozenset[str]:
    """Filenames of Kazma's own databases, lowercased.

    One list, so a guard and a disclosure cannot disagree about what counts.
    Used by the write refusal below, and by the approval card, which warns a
    human when a danger-tier command mentions one of these by name.

    Derived from ``kazma_core.paths`` rather than hardcoded, so a store added
    there is covered without anyone remembering this function — the same
    reason the refusal matches by suffix instead of by an enumerated list.
    """
    names: set[str] = {"hitl_gates.db"}          # not exposed via paths
    try:
        from kazma_core import paths as _paths

        for helper in (
            "vault_db_path", "checkpoints_db", "settings_db", "snapshots_db",
            "swarm_tasks_db", "audit_db", "rbac_db", "hub_registry_db",
            "primary_memory_db", "memory_ops_db", "knowledge_graph_db",
        ):
            fn = getattr(_paths, helper, None)
            if fn is None:
                continue
            try:
                names.add(Path(str(fn())).name.lower())
            except Exception:  # noqa: BLE001 — a name we cannot resolve is skipped
                continue
    except Exception:  # noqa: BLE001
        pass
    return frozenset(n for n in names if n)


def _is_control_plane_store(resolved: Path) -> bool:
    """True if *resolved* is one of Kazma's own databases.

    The ladder below is an allowlist, so until 2026-09-21 the gate registry
    was safe only by POSITION: the default coding sandbox is
    ``data_dir()/workspace``, which makes ``data_dir()/hitl_gates.db`` a
    sibling and therefore outside it. That is a coincidence of layout, not a
    guarantee. It stops holding the moment ``allow_absolute_paths()`` is on
    (the dev escape hatch — measured: write allowed), a workspace is bound at
    or above the data dir, or a session grant covers it.

    What is behind these files is not ordinary data. ``hitl_gates.db`` is the
    decision-truth store: flip a row from pending to approved and the resume
    chokepoint believes a human authorised a danger-tier action, which
    bypasses the strongest safety control in the system. ``rbac.db`` decides
    who may do what, ``audit.db`` is the evidence trail, and ``vault.db``
    holds secrets. None of them has any business being written by a file
    tool, under any workspace.

    Matched by suffix rather than by an enumerated list of filenames so a
    store added later is covered on the day it is added. Scoped to
    ``data_dir()`` and excluding the sandbox, so a user's own ``.db`` in
    their project or scratch area is untouched.
    """
    try:
        from kazma_core.paths import data_dir

        root = data_dir().resolve()
    except Exception:
        # Cannot classify. Fall through to the normal ladder rather than
        # denying: this is defence in depth on top of an already restrictive
        # allowlist, and failing closed here would block a user's own project
        # database because of an unrelated resolution error.
        return False

    if not path_under_root(resolved, root):
        return False
    # data_dir()/workspace is the default coding sandbox — the user's scratch
    # area, not control plane. A database they create there is theirs.
    if path_under_root(resolved, root / "workspace"):
        return False

    name = resolved.name.lower()
    for sidecar in _DB_SIDECARS:
        if name.endswith(sidecar):
            name = name[: -len(sidecar)]
            break
    return name.endswith(_DB_SUFFIXES)


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

    # 0) Kazma's own databases are never writable by file tools. Checked
    #    BEFORE the allow ladder so no workspace, grant or escape hatch can
    #    reach them — see _is_control_plane_store.
    if need == "write" and _is_control_plane_store(resolved):
        return PathAccessResult(
            allowed=False,
            reason="Kazma control-plane store — never writable by file tools",
            mode=need,
            via="denied",
            resolved=str(resolved),
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
