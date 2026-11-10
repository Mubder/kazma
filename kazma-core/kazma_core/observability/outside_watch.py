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
* :func:`outside_status` -- Settings -> Alerts, "Kazma Watch": the guard's view
  (the last heartbeat the observer acknowledged) and the observer's (the
  condition now, the week's incidents, what it knows of the owner), read
  with a request signed by the key.
* :func:`push_watch_settings`, :func:`watch_test_page`, :func:`leave_observer`
  -- Settings -> Alerts (the unified alerts plan, Stage 3): tell Kazma Watch
  the owner's time zone (``cron.timezone``: its cards' times) and the email
  recipient (``notifications.watch.email_to``); have it send one test card
  through its own channels and say which took it; stop being watched. Each is
  a request signed by the install's key, never the pairing token.
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
import re
import secrets
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
    "EMAIL_TO_KEY",
    "leave_observer",
    "outside_status",
    "pair_with_observer",
    "push_watch_settings",
    "unpair_from_observer",
    "watch_test_page",
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

#: Who Kazma Watch emails (Settings -> Alerts): kept here so it is told again
#: after a new pairing, and sent to the observer with the time zone.
EMAIL_TO_KEY = "notifications.watch.email_to"
_EMAIL_RE = re.compile(r"^[^\s@<>\"(),;:]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                       r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
#: Cloudflare Email Service's error codes (E_SENDER_NOT_VERIFIED, ...), the
#: only refusal reason Kazma Watch's test page passes on.
_EMAIL_REASON = re.compile(r"E_[A-Z_]{1,40}")

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "key": None, "value": None}


class PairingError(RuntimeError):
    """Pairing or unpairing did not happen; the message says why, ``code``
    in a word a page can say in its own language (``bad_url``, ``bad_token``,
    ``unreachable``, ``refused``, ``not_restricted``)."""

    def __init__(self, message: str, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


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
    the restriction is in place; the guard refuses a key it cannot restrict.
    (``security.file_access.restrict_to_owner``, the shared one.)"""
    from kazma_core.security.file_access import restrict_to_owner

    return restrict_to_owner(path)


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
            raise PairingError(f"could not restrict {tmp} to this account; the key was not written",
                               "not_restricted")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    if not _restrict_to_owner(path):
        path.unlink(missing_ok=True)
        raise PairingError(f"could not restrict {path} to this account; the pairing was removed",
                           "not_restricted")
    return path


def _post_pairing(url: str, token: str, route: str, body: dict[str, Any]) -> dict[str, Any]:
    if not _allowed_url(url):
        raise PairingError(f"the observer's address must be https: {url}", "bad_url")
    try:
        with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=False, trust_env=False) as client:
            res = client.post(f"{url}{route}", json=body, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise PairingError(f"the observer did not answer ({type(exc).__name__})", "unreachable") from exc
    try:
        data = res.json()
    except ValueError:
        data = {}
    if res.status_code != 200 or not isinstance(data, dict) or not data.get("ok"):
        code = data.get("error") if isinstance(data, dict) else None
        raise PairingError(f"the observer refused: HTTP {res.status_code} {code or ''}".strip(),
                           "bad_token" if code == "bad_pairing_token" else "refused")
    return data


def pair_with_observer(url: str, token: str) -> dict[str, str]:
    """Register this install with the observer at *url* (its PAIRING_TOKEN)
    and write the pairing file. Returns ``{"install", "url", "path"}``."""
    url = (url or DEFAULT_OBSERVER_URL).rstrip("/")
    if not token or len(token.strip()) < 32:
        raise PairingError("the pairing token is missing or too short (the observer's PAIRING_TOKEN)",
                           "bad_token")
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
                "condition", "detail", "heartbeat_age_s", "up_7d_pct", "incidents_7d", "settings")}}
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
    """What Settings -> Alerts shows: paired or not, the guard's view, the
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


# -- Settings -> Alerts (the unified alerts plan, Stage 3) -----------------------------


def _signed_post(pairing: dict[str, str], route: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """POST *body* to the observer's *route*, signed by the install's key the
    way the heartbeat is (``"<ts>." + body``). ``(status, answer)``; status 0
    when the observer did not answer."""
    raw = json.dumps({"v": 1, "install": pairing["install"], **body},
                     sort_keys=True, separators=(",", ":")).encode("utf-8")
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json",
        "X-Kazma-Install": pairing["install"],
        "X-Kazma-Timestamp": str(ts),
        "X-Kazma-Signature": _sign(pairing["key"], ts, raw),
    }
    try:
        with httpx.Client(timeout=_TIMEOUT_S, follow_redirects=False, trust_env=False) as client:
            res = client.post(f"{pairing['url']}{route}", content=raw, headers=headers)
    except httpx.HTTPError as exc:
        return 0, {"error": "unreachable", "detail": type(exc).__name__}
    try:
        data = res.json()
    except ValueError:
        data = {}
    return res.status_code, data if isinstance(data, dict) else {}


def valid_email(value: str) -> bool:
    """A plain address (``name@domain.tld``), 254 characters at most."""
    return len(value) <= 254 and bool(_EMAIL_RE.match(value))


def watch_settings_wanted(store: Any = None) -> dict[str, str | None]:
    """What Kazma Watch should know: the owner's time zone (``cron.timezone``,
    else the scheduler's own answer) and the email recipient."""
    from kazma_core.cron.scheduler import resolve_cron_timezone_name

    if store is None:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
    zone = str(store.get("cron.timezone") or "").strip() or resolve_cron_timezone_name()[0]
    email = str(store.get(EMAIL_TO_KEY) or "").strip()
    return {"timezone": zone or None, "email_to": email if email and valid_email(email) else None}


def push_watch_settings(store: Any = None) -> dict[str, Any]:
    """Tell Kazma Watch the time zone and the email recipient. ``{"ok": True,
    "settings"}`` or ``{"ok": False, "error"}`` (``not_paired``,
    ``unreachable``, ``refused``, ``replayed``, ``bad_timezone``, ...)."""
    pairing = _read_pairing()
    if pairing is None:
        return {"ok": False, "error": "not_paired"}
    wanted = watch_settings_wanted(store)
    status, data = _signed_post(pairing, "/v1/settings", wanted)
    _forget_cached_summary()
    if status == 200 and data.get("ok"):
        logger.info("[outside_watch] Kazma Watch was told the time zone %s", wanted["timezone"])
        return {"ok": True, "settings": data.get("settings") or wanted}
    error = str(data.get("error") or ("unreachable" if status == 0 else "refused"))
    logger.warning("[outside_watch] Kazma Watch was not told the settings: %s (HTTP %s)", error, status)
    return {"ok": False, "error": error, "status": status}


def watch_test_page() -> dict[str, Any]:
    """Have Kazma Watch send one test card through its own channels.
    ``{"ok", "channels": {"telegram": "sent|failed|off", "email": ...},
    "reasons": {"email": "E_..."}}`` -- why Cloudflare's Email Service refused
    the email, its own code -- or ``{"ok": False, "error"}`` (``too_soon``
    within 20 s of the last)."""
    pairing = _read_pairing()
    if pairing is None:
        return {"ok": False, "error": "not_paired"}
    status, data = _signed_post(pairing, "/v1/test-page", {})
    if status == 200 and isinstance(data.get("channels"), dict):
        channels = {k: v for k, v in data["channels"].items()
                    if k in ("telegram", "email") and v in ("sent", "failed", "off")}
        raw_reasons = data.get("reasons") if isinstance(data.get("reasons"), dict) else {}
        reasons = {k: v for k, v in raw_reasons.items()
                   if k == "email" and channels.get(k) == "failed"
                   and isinstance(v, str) and _EMAIL_REASON.fullmatch(v)}
        ok = any(v == "sent" for v in channels.values())
        logger.info("[outside_watch] Kazma Watch test page: %s",
                    ", ".join(f"{k} {v}{' (' + reasons[k] + ')' if k in reasons else ''}"
                              for k, v in sorted(channels.items())) or "no channel")
        return {"ok": ok, "channels": channels, "reasons": reasons}
    error = str(data.get("error") or ("unreachable" if status == 0 else "refused"))
    return {"ok": False, "error": error, "status": status}


def leave_observer(*, forget: bool = False) -> dict[str, Any]:
    """Stop Kazma Watch watching this install, with a request signed by its
    own key (the page has no pairing token), then remove the pairing file.
    The file stays when the observer did not agree: removed alone, the guard
    stops its heartbeats and the observer pages "machine unreachable"."""
    pairing = _read_pairing()
    if pairing is None:
        return {"ok": False, "error": "not_paired"}
    status, data = _signed_post(pairing, "/v1/leave", {"forget": bool(forget)})
    # An install the observer no longer knows (unpaired from the command line,
    # or its key replaced) is not watched there: the file is all that is left.
    gone = status == 401 and data.get("error") == "unknown_install"
    if not gone and (status != 200 or not data.get("ok")):
        error = str(data.get("error") or ("unreachable" if status == 0 else "refused"))
        return {"ok": False, "error": error, "status": status}
    _pairing_path().unlink(missing_ok=True)
    _forget_cached_summary()
    logger.info("[outside_watch] left %s (install %s, forget=%s)", pairing["url"], pairing["install"], forget)
    return {"ok": True, "install": pairing["install"], "url": pairing["url"], "forgotten": bool(forget)}
