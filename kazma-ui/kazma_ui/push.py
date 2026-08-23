"""Web Push for task completion (Turn Delivery V2 plan P5).

Covers the one case cursor-resume cannot: Chrome Memory Saver fully
DISCARDS a background tab — on revival there is no live socket, no
journal cursor in memory, and the reply only appears after reload. A
Service Worker + Web Push wakes a notification even then.

Design (industry standard: RFC 8030 Web Push + VAPID auth):
- VAPID keypair is generated once and persisted to ConfigStore
  (``notifications.push.vapid_public_key`` / ``..._private_key``).
- Browser subscriptions (PushSubscription JSON) persist to ConfigStore as
  a bounded JSON list keyed ``notifications.push.subscriptions`` —
  single-operator scale; endpoint hash is the identity.
- :func:`notify_push_turn_complete` fires from the delivery broker's
  terminal-frame path: ONE choke point, both transports, fire-and-forget,
  never raises into the turn.
- Everything lazy-imports ``pywebpush``. Not installed / no keys ⇒ the
  feature is OFF and every entry point is a cheap no-op
  (same graceful-degradation contract as prometheus_client metrics).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "push_available",
    "get_vapid_public_key",
    "ensure_vapid_keys",
    "subscribe",
    "unsubscribe",
    "list_subscriptions",
    "notify_push_turn_complete",
]

_SUBS_KEY = "notifications.push.subscriptions"
_MAX_SUBSCRIPTIONS = 50
_PUSH_TTL_SECONDS = 24 * 3600


def push_available() -> bool:
    """True when pywebpush is importable AND a VAPID public key exists."""
    try:
        import pywebpush  # noqa: F401
    except Exception:
        return False
    return bool(get_vapid_public_key())


def get_vapid_public_key() -> str:
    try:
        from kazma_core.config_store import get_config_store

        return str(get_config_store().get("notifications.push.vapid_public_key") or "")
    except Exception:
        logger.debug("[Push] vapid public key read failed", exc_info=True)
        return ""


def ensure_vapid_keys() -> tuple[str, str] | None:
    """Generate + persist a VAPID keypair once. Returns (public, private)."""
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        pub = str(store.get("notifications.push.vapid_public_key") or "")
        priv = str(store.get("notifications.push.vapid_private_key") or "")
        if pub and priv:
            return pub, priv
        from py_vapid import Vapid

        v = Vapid()
        # py_vapid 2.x: generate() populates key material in-memory.
        try:
            v.generate_keys()
        except Exception:
            pass
        priv_pem = v.private_pem()
        pub_b64 = v.application_server_key
        if not priv_pem or not pub_b64:
            return None
        store.set("notifications.push.vapid_public_key", pub_b64.decode() if isinstance(pub_b64, bytes) else pub_b64)
        store.set("notifications.push.vapid_private_key", priv_pem)
        return (
            pub_b64.decode() if isinstance(pub_b64, bytes) else pub_b64,
            priv_pem,
        )
    except Exception:
        logger.debug("[Push] VAPID generation failed", exc_info=True)
        return None


def _load_subs() -> list[dict[str, Any]]:
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get(_SUBS_KEY)
        subs = json.loads(raw) if isinstance(raw, str) else (raw or [])
        return subs if isinstance(subs, list) else []
    except Exception:
        return []


def _save_subs(subs: list[dict[str, Any]]) -> None:
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set(_SUBS_KEY, json.dumps(subs[-_MAX_SUBSCRIPTIONS:]))
    except Exception:
        logger.debug("[Push] subscription save failed", exc_info=True)


def _endpoint_hash(sub: dict[str, Any]) -> str:
    ep = str((sub or {}).get("endpoint") or "")
    return hashlib.sha256(ep.encode("utf-8")).hexdigest()[:24]


def subscribe(subscription: dict[str, Any]) -> dict[str, Any]:
    """Persist a browser PushSubscription JSON. Idempotent per endpoint."""
    if not isinstance(subscription, dict) or not subscription.get("endpoint"):
        return {"status": "error", "error": "invalid subscription"}
    subs = _load_subs()
    hid = _endpoint_hash(subscription)
    subs = [s for s in subs if s.get("_id") != hid]
    record = dict(subscription)
    record["_id"] = hid
    subs.append(record)
    _save_subs(subs)
    return {"status": "ok", "count": len(subs)}


def unsubscribe(endpoint: str) -> dict[str, Any]:
    hid = hashlib.sha256(str(endpoint or "").encode("utf-8")).hexdigest()[:24]
    subs = [s for s in _load_subs() if s.get("_id") != hid]
    _save_subs(subs)
    return {"status": "ok", "count": len(subs)}


def list_subscriptions() -> list[dict[str, Any]]:
    return [s for s in _load_subs() if s.get("endpoint")]


async def notify_push_turn_complete(summary: str) -> int:
    """Fire-and-forget push to every subscription. Returns delivered count.

    Dead subscriptions (410 Gone) are pruned. Never raises.
    """
    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return 0
    keys = ensure_vapid_keys()
    if not keys:
        return 0
    pub, priv = keys
    subs = list_subscriptions()
    if not subs:
        return 0

    def _send_one(sub: dict[str, Any]) -> bool:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.get("endpoint"),
                    "keys": sub.get("keys") or {},
                },
                data=json.dumps({"title": "Kazma \u2014 task finished",
                                 "body": summary[:300]}),
                vapid_private_key=priv,
                vapid_claims={"sub": "mailto:admin@kazma.local"},
                ttl=_PUSH_TTL_SECONDS,
            )
            return True
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 410:
                unsubscribe(str(sub.get("endpoint") or ""))
            logger.debug("[Push] send failed (%s)", status or "unknown")
            return False
        except Exception:
            logger.debug("[Push] send failed", exc_info=True)
            return False

    loop = asyncio.get_running_loop()
    results = await asyncio.gather(*(loop.run_in_executor(None, _send_one, s) for s in subs))
    return sum(1 for ok in results if ok)
