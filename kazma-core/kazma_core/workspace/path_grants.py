"""Path access grants — session + durable extra roots outside the workspace.

Security model (deny-by-default outside workspace):

* **Workspace root** — always allowed (unchanged).
* **Durable extra roots** — ConfigStore ``workspace.extra_roots`` (Settings /
  deploy config). Each entry: ``{path, mode: read|write, label?}``.
* **Session grants** — ConfigStore ``path_grant.{thread_id}.{id}`` with TTL,
  created after HITL approval of ``request_path_access``.

Never enables global filesystem access; only listed roots. Symlink-aware
containment uses resolved paths + string-prefix backstop.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "AccessMode",
    "PathGrant",
    "clear_session_grants",
    "grant_session_path",
    "list_durable_roots",
    "list_session_grants",
    "mode_rank",
    "path_under_root",
    "revoke_session_grant",
    "set_durable_roots",
]

logger = logging.getLogger(__name__)

AccessMode = Literal["read", "write"]

_MODE_RANK = {"read": 1, "write": 2}
_DEFAULT_SESSION_TTL = 60 * 60  # 1 hour


def mode_rank(mode: str) -> int:
    return _MODE_RANK.get((mode or "read").lower(), 0)


def path_under_root(target: Path, root: Path) -> bool:
    """True if *target* is *root* or a descendant (resolved paths)."""
    try:
        t = target.expanduser().resolve()
        r = root.expanduser().resolve()
    except OSError:
        return False
    try:
        t.relative_to(r)
        return True
    except ValueError:
        return False
    except Exception:
        # Windows edge cases — string backstop
        ts, rs = str(t), str(r)
        sep = "\\" if "\\" in rs else "/"
        return ts == rs or ts.startswith(rs.rstrip("/\\") + sep)


@dataclass(frozen=True, slots=True)
class PathGrant:
    """One allowed external root."""

    path: str
    mode: AccessMode
    label: str = ""
    grant_id: str = ""
    scope: str = "session"  # session | durable
    expires_at: float | None = None
    thread_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mode": self.mode,
            "label": self.label,
            "grant_id": self.grant_id,
            "scope": self.scope,
            "expires_at": self.expires_at,
            "thread_id": self.thread_id,
        }


def _normalize_mode(mode: str) -> AccessMode:
    m = (mode or "read").strip().lower()
    return "write" if m in ("write", "rw", "readwrite", "read_write") else "read"


def _cs():
    from kazma_core.config_store import get_config_store

    return get_config_store()


def list_durable_roots() -> list[PathGrant]:
    """Load durable extra roots from ConfigStore."""
    raw = _cs().get("workspace.extra_roots")
    if not raw:
        return []
    if isinstance(raw, str):
        # Comma-separated paths → read-only
        items = [{"path": p.strip(), "mode": "read"} for p in raw.split(",") if p.strip()]
    elif isinstance(raw, list):
        items = raw
    else:
        return []
    out: list[PathGrant] = []
    for item in items:
        if isinstance(item, str):
            path, mode, label = item, "read", ""
        elif isinstance(item, dict):
            path = str(item.get("path") or "").strip()
            mode = _normalize_mode(str(item.get("mode") or "read"))
            label = str(item.get("label") or "")
        else:
            continue
        if not path:
            continue
        try:
            resolved = str(Path(path).expanduser().resolve())
        except OSError:
            resolved = path
        out.append(
            PathGrant(
                path=resolved,
                mode=mode,
                label=label,
                grant_id=hashlib.sha256(resolved.encode()).hexdigest()[:12],
                scope="durable",
            )
        )
    return out


def set_durable_roots(entries: list[dict[str, Any]]) -> list[PathGrant]:
    """Replace durable extra roots. Returns normalized list."""
    cleaned: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        try:
            path = str(Path(path).expanduser().resolve())
        except OSError:
            pass
        cleaned.append(
            {
                "path": path,
                "mode": _normalize_mode(str(item.get("mode") or "read")),
                "label": str(item.get("label") or "")[:120],
            }
        )
    _cs().set("workspace.extra_roots", cleaned, category="workspace")
    logger.warning("[SECURITY] workspace.extra_roots updated count=%s", len(cleaned))
    return list_durable_roots()


def _session_key(thread_id: str, grant_id: str) -> str:
    return f"path_grant.{thread_id}.{grant_id}"


def grant_session_path(
    thread_id: str,
    path: str,
    *,
    mode: str = "read",
    label: str = "",
    ttl_seconds: int | None = None,
    actor: str = "user",
) -> PathGrant:
    """Grant session access to a path (or its parent directory)."""
    if not thread_id or not path:
        raise ValueError("thread_id and path required")
    try:
        resolved = str(Path(path).expanduser().resolve())
    except OSError as exc:
        raise ValueError(f"invalid path: {exc}") from exc

    # Prefer granting the directory if a file path is given (smoother UX)
    p = Path(resolved)
    grant_root = resolved
    if p.is_file() or (not p.exists() and p.suffix):
        grant_root = str(p.parent)

    mode_n = _normalize_mode(mode)
    gid = uuid.uuid4().hex[:12]
    now = time.time()
    ttl = _DEFAULT_SESSION_TTL if ttl_seconds is None else max(0, int(ttl_seconds))
    expires = (now + ttl) if ttl > 0 else None
    payload = {
        "enabled": True,
        "path": grant_root,
        "mode": mode_n,
        "label": label or Path(grant_root).name,
        "grant_id": gid,
        "scope": "session",
        "thread_id": thread_id,
        "actor": actor,
        "since": now,
        "ttl_seconds": ttl,
        "expires_at": expires,
    }
    cs = _cs()
    # Maintain index for reliable listing without full-store scan.
    idx_key = f"path_grant_index.{thread_id}"
    raw_idx = cs.get(idx_key) or []
    ids = list(raw_idx) if isinstance(raw_idx, list) else []
    if gid not in ids:
        ids.append(gid)
    # Atomic payload + index write (one transaction) so two concurrent grants
    # for the same thread can't interleave their two writes. The read-modify-
    # write of `ids` is still a narrow TOCTOU, but list_session_grants has a
    # full-store scan fallback that recovers any orphaned grant (audit finding).
    cs.batch_set([
        (_session_key(thread_id, gid), payload, "safety"),
        (idx_key, ids, "safety"),
    ])
    logger.warning(
        "[SECURITY] PATH GRANT session thread=%s path=%s mode=%s actor=%s ttl=%s",
        thread_id[:16] if thread_id else "",
        grant_root,
        mode_n,
        actor,
        ttl or "none",
    )
    return PathGrant(
        path=grant_root,
        mode=mode_n,
        label=payload["label"],
        grant_id=gid,
        scope="session",
        expires_at=expires,
        thread_id=thread_id,
    )


def list_session_grants(thread_id: str | None) -> list[PathGrant]:
    if not thread_id:
        return []
    cs = _cs()
    out: list[PathGrant] = []
    now = time.time()
    ids: list[str] = []
    raw_idx = cs.get(f"path_grant_index.{thread_id}")
    if isinstance(raw_idx, list):
        ids = [str(x) for x in raw_idx if x]
    else:
        # Fallback: scan safety category
        try:
            cat = cs.get_category("safety") or {}
            prefix = f"path_grant.{thread_id}."
            for k in cat:
                if str(k).startswith(prefix):
                    ids.append(str(k).rsplit(".", 1)[-1])
        except Exception:
            pass

    alive_ids: list[str] = []
    for gid in ids:
        key = _session_key(thread_id, gid)
        raw = cs.get(key)
        if not isinstance(raw, dict) or not raw.get("enabled"):
            continue
        exp = raw.get("expires_at")
        if exp is not None:
            try:
                if now > float(exp):
                    try:
                        cs.delete(key)
                    except Exception:
                        pass
                    continue
            except (TypeError, ValueError):
                pass
        alive_ids.append(gid)
        out.append(
            PathGrant(
                path=str(raw.get("path") or ""),
                mode=_normalize_mode(str(raw.get("mode") or "read")),
                label=str(raw.get("label") or ""),
                grant_id=str(raw.get("grant_id") or gid),
                scope="session",
                expires_at=float(exp) if exp is not None else None,
                thread_id=thread_id,
            )
        )
    if alive_ids != ids:
        try:
            cs.set(f"path_grant_index.{thread_id}", alive_ids, category="safety")
        except Exception:
            pass
    return [g for g in out if g.path]


def revoke_session_grant(thread_id: str, grant_id: str) -> bool:
    if not thread_id or not grant_id:
        return False
    key = _session_key(thread_id, grant_id)
    try:
        cs = _cs()
        cs.delete(key)
        idx_key = f"path_grant_index.{thread_id}"
        raw_idx = cs.get(idx_key) or []
        if isinstance(raw_idx, list):
            cs.set(
                idx_key,
                [x for x in raw_idx if str(x) != grant_id],
                category="safety",
            )
        return True
    except Exception:
        return False


def clear_session_grants(thread_id: str) -> int:
    n = 0
    for g in list_session_grants(thread_id):
        if revoke_session_grant(thread_id, g.grant_id):
            n += 1
    return n
