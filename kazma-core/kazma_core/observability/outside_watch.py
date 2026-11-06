"""The outside watch, Kazma's side (resilience plan B1).

The observer (``deploy/observer``, a Cloudflare Worker on kazma.ai) watches
this install from outside the machine: it probes the site through the tunnel
and reads the guard's signed heartbeat, and pages the owner through a bot of
its own when the machine, the tunnel, Kazma or the guard stops. This module
is what Kazma itself does with it:

* :func:`pair_with_observer` / :func:`unpair_from_observer` -- ``kazma observer pair`` registers the
  install with the observer (the observer's ``PAIRING_TOKEN``) and writes the
  pairing file ``<install>/.kazma/observer.key`` (JSON: the install id the
  observer knows, the key, the observer's address), restricted to this
  account. The guard reads it and starts sending (``OutsideWatch`` in
  ``scripts/service/kazma_guard.py``). Pairing is the switch: an install not
  paired sends nothing.
* :func:`outside_status` -- Settings -> System, "Outside watch": the guard's view
  (the last heartbeat the observer acknowledged) and the observer's (the
  condition now, the last incident), read with a request signed by the key.
* :func:`outside_digest_line` -- the daily digest's line: up % this week and the
  week's incidents.

Every function here blocks (files, the network): call them off the loop.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_OBSERVER_URL",
    "PairingError",
    "outside_digest_line",
    "outside_status",
    "pair_with_observer",
    "unpair_from_observer",
]

DEFAULT_OBSERVER_URL = "https://status.kazma.ai"
_TIMEOUT_S = 10.0
_SUMMARY_TTL_S = 60.0
_KEY_MIN_CHARS = 32
_HEX = frozenset("0123456789abcdef")

# Short words for a condition, for the digest line.
_CAUSE_WORDS = {
    "machine_unreachable": "machine not reachable",
    "tunnel_down": "tunnel down",
    "kazma_restarting": "Kazma restarting",
    "kazma_down": "Kazma down",
    "dependency_down": "a dependency away",
    "guard_gone": "guard stopped",
    "disk_critical": "disk almost full",
    "observer_refused": "observer refused by Access",
    "unreachable": "not reachable",
}

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "key": None, "value": None}


class PairingError(RuntimeError):
    """Pairing or unpairing did not happen; the message says why."""


def _pairing_path() -> Path:
    """``<install>/.kazma/observer.key`` (the guard's ``_observer_key_path``)."""
    from kazma_core.paths import user_home

    return user_home() / "observer.key"


def _valid_install(value: object) -> bool:
    return isinstance(value, str) and len(value) == 16 and set(value) <= _HEX


def _install_hash() -> str:
    """The id this install is known by to the observer: a hash of its install
    id (``<data dir>/install_id``), never a name."""
    from kazma_core.db.shared_store_peers import install_id

    return hashlib.sha256((install_id() or "").encode("utf-8")).hexdigest()[:16]


def _read_pairing() -> dict[str, str] | None:
    """``{"install", "key", "url"}`` from the pairing file, or None (not
    paired, or a file the guard would refuse)."""
    try:
        text = _pairing_path().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("{"):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    key = str(data.get("key") or "")
    install = data.get("install")
    url = str(data.get("url") or DEFAULT_OBSERVER_URL).rstrip("/")
    if len(key) < _KEY_MIN_CHARS or not _valid_install(install) or not _allowed_url(url):
        return None
    return {"install": install, "key": key, "url": url}


def _allowed_url(url: str) -> bool:
    """https, or http to this machine (a local observer while testing)."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    if parsed.scheme == "https" and parsed.hostname:
        return True
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")


def _sign(key: str, ts: int, message: bytes) -> str:
    mac = hmac.new(key.encode("utf-8"), str(int(ts)).encode("ascii") + b"." + message, hashlib.sha256)
    return "v1=" + mac.hexdigest()


# -- the pairing file ------------------------------------------------------------


def _restrict_to_owner(path: Path) -> bool:
    """Only this account may read *path* (the guard runs as it). True when
    the restriction is in place; the guard refuses a key it cannot restrict."""
    if os.name != "nt":
        try:
            os.chmod(path, 0o600)
            return True
        except OSError:
            return False
    from kazma_core.security.file_access import _current_user_sid

    sid = _current_user_sid()
    if not sid:
        return False
    try:
        result = subprocess.run(  # noqa: S603 -- fixed program, no shell
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _write_pairing(data: dict[str, Any]) -> Path:
    """Write the pairing file: made empty, restricted, then filled, so the
    key is never readable by another account."""
    path = _pairing_path()
    tmp = path.with_name(path.name + ".new")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        if not _restrict_to_owner(tmp):
            raise PairingError(f"could not restrict {tmp} to this account; the key was not written")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    if not _restrict_to_owner(path):
        path.unlink(missing_ok=True)
        raise PairingError(f"could not restrict {path} to this account; the pairing was removed")
    return path


def _post_pairing(url: str, token: str, route: str, body: dict[str, Any]) -> dict[str, Any]:
    if not _allowed_url(url):
        raise PairingError(f"the observer's address must be https: {url}")
    try:
        with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=False, trust_env=False) as client:
            res = client.post(f"{url}{route}", json=body, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise PairingError(f"the observer did not answer ({type(exc).__name__})") from exc
    try:
        data = res.json()
    except ValueError:
        data = {}
    if res.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
        code = data.get("error") if isinstance(data, dict) else None
        raise PairingError(f"the observer refused: HTTP {res.status_code} {code or ''}".strip())
    return data


def pair_with_observer(url: str, token: str) -> dict[str, str]:
    """Register this install with the observer at *url* (its PAIRING_TOKEN)
    and write the pairing file. Returns ``{"install", "url", "path"}``."""
    url = (url or DEFAULT_OBSERVER_URL).rstrip("/")
    if not token or len(token.strip()) < 32:
        raise PairingError("the pairing token is missing or too short (the observer's PAIRING_TOKEN)")
    install = _install_hash()
    key = secrets.token_urlsafe(32)
    _post_pairing(url, token.strip(), "/v1/pair", {"install": install, "key": key})
    path = _write_pairing({"v": 1, "install": install, "key": key, "url": url})
    _forget_cached_summary()
    logger.info("[outside_watch] paired with %s as install %s", url, install)
    return {"install": install, "url": url, "path": str(path)}


def unpair_from_observer(token: str, *, forget: bool = False, url: str | None = None) -> dict[str, Any]:
    """Stop the observer watching this install (and, with *forget*, delete
    what it kept), then remove the pairing file."""
    pairing = _read_pairing()
    target = (url or (pairing or {}).get("url") or DEFAULT_OBSERVER_URL).rstrip("/")
    install = (pairing or {}).get("install") or _install_hash()
    _post_pairing(target, (token or "").strip(), "/v1/unpair", {"install": install, "forget": forget})
    _pairing_path().unlink(missing_ok=True)
    _forget_cached_summary()
    logger.info("[outside_watch] unpaired from %s (install %s, forget=%s)", target, install, forget)
    return {"install": install, "url": target, "forgotten": forget}


# -- reading back ---------------------------------------------------------------------


def _forget_cached_summary() -> None:
    with _cache_lock:
        _cache.update(at=0.0, key=None, value=None)


def _observer_summary(pairing: dict[str, str] | None = None, *, fresh: bool = False) -> dict[str, Any]:
    """The observer's summary of this install (condition, uptime this week,
    the week's incidents), read with a request signed by the key. ``{"ok":
    False, "error": code}`` when not paired or the observer cannot say.
    Kept a minute."""
    pairing = pairing or _read_pairing()
    if pairing is None:
        return {"ok": False, "error": "not_paired"}
    cache_key = (pairing["url"], pairing["install"])
    with _cache_lock:
        if not fresh and _cache["key"] == cache_key and time.monotonic() - _cache["at"] < _SUMMARY_TTL_S:
            return dict(_cache["value"])
    ts = int(time.time())
    headers = {
        "X-Kazma-Install": pairing["install"],
        "X-Kazma-Timestamp": str(ts),
        "X-Kazma-Signature": _sign(pairing["key"], ts, b"GET /v1/summary"),
    }
    try:
        with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=False, trust_env=False) as client:
            res = client.get(f"{pairing['url']}/v1/summary", headers=headers)
        data = res.json() if res.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as exc:
        value: dict[str, Any] = {"ok": False, "error": "unreachable", "detail": type(exc).__name__}
    else:
        if isinstance(data, dict):
            value = {"ok": True, **{k: data.get(k) for k in (
                "condition", "detail", "heartbeat_age_s", "up_7d_pct", "incidents_7d")}}
        else:
            value = {"ok": False, "error": "refused", "status": res.status_code}
    with _cache_lock:
        _cache.update(at=time.monotonic(), key=cache_key, value=value)
    return dict(value)


def _guard_state_path() -> Path:
    """The guard's state file: the one it handed the server
    (``KAZMA_GUARD_STATE_FILE``), else ``<install>/.kazma/guard.state.json``,
    where the guard keeps it (its ``_state_path``). ``kazma observer status``
    run from a shell has no handed path, and said "Kazma was not started by
    the guard" over a guard sending heartbeats (2026-11-06)."""
    from kazma_core.paths import user_home

    handed = (os.environ.get("KAZMA_GUARD_STATE_FILE") or "").strip()
    return Path(handed) if handed else user_home() / "guard.state.json"


def _guard_view() -> dict[str, Any] | None:
    """The guard's record of its heartbeats (state file, ``outside_watch``)."""
    path = _guard_state_path()
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError):
        return None
    rec = data.get("outside_watch") if isinstance(data, dict) else None
    if not isinstance(rec, dict):
        return None
    ack = rec.get("last_ack")
    return {
        "state": rec.get("state") if isinstance(rec.get("state"), str) else None,
        "last_ack": ack if isinstance(ack, (int, float)) else None,
        "mode": rec.get("mode") if isinstance(rec.get("mode"), str) else None,
    }


def outside_status(*, fresh: bool = False) -> dict[str, Any]:
    """What Settings -> System shows: paired or not, the guard's view, the
    observer's (``fresh`` skips the minute's cache). Blocking (files, a
    signed request)."""
    pairing = _read_pairing()
    if pairing is None:
        return {"paired": False, "guard": _guard_view(), "observer": None,
                "pair_command": "kazma observer pair"}
    return {
        "paired": True,
        "install": pairing["install"],
        "url": pairing["url"],
        "guard": _guard_view(),
        "observer": _observer_summary(pairing, fresh=fresh),
    }


def _minutes(incident: dict[str, Any], now: float) -> int:
    start = incident.get("started_at")
    end = incident.get("ended_at") or now
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return 0
    return max(1, round((end - start) / 60))


def outside_digest_line(now: float | None = None) -> str | None:
    """The daily digest's line, or None when this install is not paired."""
    pairing = _read_pairing()
    if pairing is None:
        return None
    summary = _observer_summary(pairing, fresh=True)
    if not summary.get("ok"):
        return f"Outside watch: the observer did not answer ({summary.get('error')})"
    now = time.time() if now is None else now
    pct = summary.get("up_7d_pct")
    up = f"up {pct} % this week" if isinstance(pct, (int, float)) else "no readings this week yet"
    incidents = [i for i in (summary.get("incidents_7d") or []) if isinstance(i, dict)]
    if not incidents:
        return f"Outside watch: {up}, no incidents"
    from kazma_core.english_count import count_noun

    parts = "; ".join(
        f"{_CAUSE_WORDS.get(str(i.get('cause')), str(i.get('cause')))}, {_minutes(i, now)} min"
        + ("" if i.get("ended_at") else ", ongoing")
        for i in incidents[:5]
    )
    more = f" and {len(incidents) - 5} more" if len(incidents) > 5 else ""
    return f"Outside watch: {up}, {count_noun(len(incidents), 'incident')} ({parts}{more})"
