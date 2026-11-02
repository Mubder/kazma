"""Single path-access policy for file/IDE tools.

``check_path_access`` is the SoT for "may this resolved path be used?":

0. Read or write of one of Kazma's own databases → deny, unconditionally,
   naming the tool that reads it (``kazma_core.store_registry``)
1. Under active workspace → allow
2. Under durable ``workspace.extra_roots`` with sufficient mode → allow  
3. Under session path grant for current thread → allow  
4. Global ``allow_absolute_paths()`` (dev escape hatch) → allow  
5. Else deny (with a smooth agent-facing recovery hint)
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from kazma_core.workspace.binding import (
    allow_absolute_paths,
    resolve_active_root,
    resolve_tool_path,
)
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
    "code_mentions_control_plane",
    "control_plane_store_targeted",
    "denied_message",
    "is_path_allowed",
    "resolve_and_check",
]

logger = logging.getLogger(__name__)

#: SQLite family suffixes, plus the sidecars. The sidecars matter as much as
#: the main file: a writer that can append to ``hitl_gates.db-wal`` decides
#: what the next reader sees without ever opening ``hitl_gates.db``.
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_DB_SIDECARS = ("-wal", "-shm", "-journal")


def control_plane_db_names() -> frozenset[str]:
    """Filenames of Kazma's own databases that are safe to match by name.

    One list, so a guard and a disclosure cannot disagree about what counts.
    Used by the approval card, which warns a human when a danger-tier
    command mentions one of these, and by the ``python_exec`` refusal.

    Delegates to :func:`kazma_core.store_registry.distinctive_store_names`:
    every declared store plus anything resolved through ``kazma_core.paths``.
    Until 2026-09-25 this was the ``paths`` list alone, which had never
    heard of ``agent_artifacts.db`` — so ``python_exec`` let an
    ``open('kazma-data/agent_artifacts.db', 'rb')`` through.
    """
    from kazma_core.store_registry import distinctive_store_names

    return distinctive_store_names()


def _is_control_plane_store(resolved: Path) -> bool:
    """True if *resolved* is one of Kazma's own databases.

    Delegates to :func:`kazma_core.store_registry.is_kazma_store`, the one
    predicate every door shares. The history below is why it sits before
    the allow ladder.

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
    their project or scratch area is untouched. If the data dir cannot be
    resolved it answers False and the normal ladder applies — failing closed
    would block a user's own project database over an unrelated error.
    """
    from kazma_core.store_registry import is_kazma_store

    return is_kazma_store(resolved)


def _strip_db_sidecars(name: str) -> str:
    lowered = name.lower()
    for sidecar in _DB_SIDECARS:
        if lowered.endswith(sidecar):
            return lowered[: -len(sidecar)]
    return lowered


def _looks_like_store_token(token: str) -> bool:
    from kazma_core.store_registry import is_secret_file_name

    name = _strip_db_sidecars(Path(token).name)
    if name in control_plane_db_names() or is_secret_file_name(name):
        return True
    return name.endswith(_DB_SUFFIXES)


def _is_env_name(token: str) -> bool:
    """An env-file name (``.env``, ``.env.local``): Kazma's only where it
    sits, never by name alone (a project's own ``.env`` is the user's)."""
    from kazma_core.store_registry import SECRET_FILE_NAMES, is_secret_file_name

    name = Path(token).name.lower()
    return is_secret_file_name(name) and name not in SECRET_FILE_NAMES


def control_plane_store_targeted(
    token: str,
    *,
    cwd: str | Path | None = None,
) -> str | None:
    """Return the resolved path when *token* points at a control-plane store.

    Absolute paths, paths relative to *cwd*, and a store basename resolved
    under ``data_dir()`` all count. A user's ``workspace/project.db`` does
    not: that tree is excluded by :func:`_is_control_plane_store`.
    """
    text = (token or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    if not text or text.startswith("-") or not _looks_like_store_token(text):
        return None
    candidates: list[Path] = []
    raw = Path(text)
    if raw.is_absolute():
        candidates.append(raw)
    elif cwd is not None:
        candidates.append(Path(cwd) / text)
    # ``.env`` means the file where the command runs; the data dir's own
    # copies are reached by their path (``backups/.../.env``).
    if not raw.is_absolute() and not _is_env_name(text):
        try:
            from kazma_core.paths import data_dir

            root = data_dir()
            # A relative name means the file where the command runs. The
            # data dir's file of that name counts when there is no such file
            # there (the command may ``cd`` into the data dir first), never
            # over the user's own: ``sqlite3 app.db`` in a workspace holding
            # app.db was refused as a Kazma store (2026-11, FA-217).
            in_data_dir = root / text
            here = Path(cwd) / text if cwd is not None else None
            if here is None or (in_data_dir.exists() and not here.exists()):
                candidates.append(in_data_dir)
            # A bare ``hitl_gates.db`` is the store even when cwd is the
            # sandbox. Do not do this for every ``*.db``: ``project.db``
            # under the sandbox is the user's file.
            base = _strip_db_sidecars(raw.name)
            if base in control_plane_db_names():
                candidates.append(root / raw.name)
        except Exception:
            logger.debug("[path_policy] data_dir unavailable", exc_info=True)
    for cand in candidates:
        try:
            resolved = cand.expanduser().resolve()
        except OSError:
            continue
        if _is_control_plane_store(resolved):
            return str(resolved)
    return None


#: Where ``python_exec``'s container sees the workspace when the host path
#: cannot be a container path (a Windows path). A POSIX host mounts the
#: workspace at its own path, so absolute paths in a snippet mean the same
#: file inside and out.
SANDBOX_WORKSPACE_MOUNT = "/workspace"


def _sandbox_mount_is_fixed() -> bool:
    """True where the container sees the workspace at :data:`SANDBOX_WORKSPACE_MOUNT`."""
    import os

    return os.name == "nt"


def sandbox_workspace_target(ws_path: str) -> str:
    """The container path the workspace *ws_path* is mounted at."""
    return SANDBOX_WORKSPACE_MOUNT if _sandbox_mount_is_fixed() else ws_path


def _sandbox_token_on_host(token: str, root: Path) -> str | None:
    """The host path a snippet's ``/workspace/...`` names, or None.

    On Windows the container sees the active workspace at ``/workspace``, so
    ``open('/workspace/.env')`` reads the workspace's ``.env``; resolved as
    a Windows path it was ``<drive>:\\workspace\\.env``, nothing of Kazma's,
    and the 2026-11 audit's snippet reached the approval card and read the
    live install's ``.env``.
    """
    if not _sandbox_mount_is_fixed():
        return None
    text = token.replace("\\", "/")
    if text != SANDBOX_WORKSPACE_MOUNT and not text.startswith(SANDBOX_WORKSPACE_MOUNT + "/"):
        return None
    rest = text[len(SANDBOX_WORKSPACE_MOUNT):].lstrip("/")
    return str(root / rest) if rest else str(root)


def code_mentions_control_plane(code: str) -> str | None:
    """Return the store name when *code* names a control-plane database.

    ``python_exec`` does not go through :func:`check_path_access`. A script
    that opens ``hitl_gates.db`` by name is the same access the file tools
    already refuse. Quoted paths count too — absolute, or relative to the
    server's working directory or the active workspace — so
    ``'kazma-data/agent_artifacts.db'`` (the 2026-09-25 dump) is caught even
    for a store whose bare name is too generic to match. A path under the
    container's ``/workspace`` means the active workspace's file (Windows).
    Dynamic construction that never spells the filename still gets through
    this check; the container shows Kazma's folders and secret files empty
    (``code_exec``, :func:`kazma_core.store_registry.kazma_paths_inside`).
    """
    low = (code or "").lower()
    if not low:
        return None
    from kazma_core.store_registry import SECRET_FILE_NAMES

    for name in sorted(control_plane_db_names() | SECRET_FILE_NAMES):
        if name and name in low:
            return name
    bases: list[Path] = [Path.cwd()]
    try:
        bases.append(Path(resolve_active_root()))
    except (OSError, RuntimeError, ValueError, ImportError):
        logger.debug("[path_policy] no active workspace; cwd only", exc_info=True)
    for token in re.findall(r"""['"]([^'"]+)['"]""", code):
        token = _sandbox_token_on_host(token, bases[-1]) or token
        absolute = token.startswith(("/", "\\")) or (
            len(token) > 2 and token[1] == ":"
        )
        if absolute:
            hit = control_plane_store_targeted(token)
        elif _is_env_name(token):
            # The snippet runs in the active workspace (code_exec), so a
            # relative ``.env`` is that folder's; the server's own working
            # directory is not where it runs.
            hit = control_plane_store_targeted(token, cwd=bases[-1])
        elif _strip_db_sidecars(Path(token).name).endswith(_DB_SUFFIXES):
            hit = next(
                (h for h in (control_plane_store_targeted(token, cwd=b) for b in bases) if h),
                None,
            )
        else:
            continue
        if hit:
            return Path(hit).name
    return None


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
    if res.via == "store":
        # A path grant cannot open a Kazma store (rule 0 sits before the
        # grants), so offering request_path_access here would only buy a
        # useless approval. Say what the store is and which tool reads it.
        return f"Safety: {res.reason}"
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


async def resolve_and_check(
    path: str | Path,
    mode: AccessMode | str = "read",
) -> tuple[Path, PathAccessResult]:
    """Where *path* points and whether a tool may use it, in a worker thread.

    ``resolve_tool_path`` reads the active workspace, and
    ``check_path_access`` the workspace, the durable roots and the session
    grants: store reads (settings on Postgres), which the async file tools
    made on the event loop until 2026-10-02. ``asyncio.to_thread`` copies the
    turn's context, so the per-task workspace scope and the thread id reach
    the check. Async code calls this, never the two directly
    (``tests/test_static_gates.py``).
    """

    def _run() -> tuple[Path, PathAccessResult]:
        resolved = resolve_tool_path(path)
        return resolved, check_path_access(resolved, mode)

    return await asyncio.to_thread(_run)


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
        # A relative path means the workspace, the same as in every tool.
        resolved = resolve_tool_path(path, root=workspace)
    except OSError as exc:
        return PathAccessResult(
            allowed=False,
            reason=f"invalid path: {exc}",
            mode=need,
            via="denied",
            resolved=str(path),
            workspace=str(workspace),
        )

    # 0) Kazma's own databases are never opened by file tools. Checked
    #    BEFORE the allow ladder so no workspace, grant or escape hatch can
    #    reach them — see _is_control_plane_store.
    #
    #    Writes were refused from 2026-09-21. Reads were left open on the
    #    grounds that they cannot damage anything — true, and beside the
    #    point: raw SQLite bytes are noise to the model, every other door
    #    (SQL tools, python_exec, shell_exec) already refused these files,
    #    and on 2026-09-25 file_read on agent_artifacts.db and
    #    chat_sessions.db was one leg of a 67-call dig for drafts the model
    #    had no tool to read. The refusal names the tool that does read it.
    #    Kazma's secret files (.env, restic.pass, the broker token) are
    #    refused here too (is_kazma_store): until 2026-11-02 file_read and
    #    file_search returned the live install's .env and restic.pass.
    if _is_control_plane_store(resolved):
        from kazma_core.store_registry import is_kazma_secret_file, store_refusal

        return PathAccessResult(
            allowed=False,
            reason=(
                ("Kazma secret file — " if is_kazma_secret_file(resolved) else "Kazma control-plane store — ")
                + ("never writable" if need == "write" else "not readable")
                + " by file tools. "
                + store_refusal(resolved, door="file tools")
            ),
            mode=need,
            via="store",
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
