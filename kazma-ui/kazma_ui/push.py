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
  never raises into the turn. It honours the Settings switch "notify when a
  task finishes" (:func:`turn_complete_notifications_on`, the one reader of
  ``notifications.turn_complete``): until 2026-09-28 only the page read it,
  so a device that had subscribed kept getting pushes after the owner
  switched them off.
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
    "turn_complete_notifications_on",
]

_SUBS_KEY = "notifications.push.subscriptions"
_MAX_SUBSCRIPTIONS = 50
_PUSH_TTL_SECONDS = 24 * 3600


def turn_complete_notifications_on() -> bool:
    """The Settings switch "notify when a task finishes"
    (``notifications.turn_complete``: '1'/'0', unset = on). Read live; never
    raises (an unreadable store keeps the default, on)."""
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get("notifications.turn_complete")
    except Exception:
        logger.debug("[Push] turn-complete switch unreadable; defaulting on", exc_info=True)
        return True
    if raw is None:
        return True
    return str(raw).strip().lower() not in ("0", "false", "off", "no")


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
    except Exception:
        return []
    return _as_list(raw)


def _endpoint_refusal(endpoint: str) -> str | None:
    """Why the server will not POST to *endpoint*, or None when it may.

    AUD-103: any URL a browser sent was stored as a subscription and every
    finished turn POSTed to it -- a client could point the server at an
    internal host (a metadata service, the database's admin port). A push
    service is a public https host (FCM, Mozilla, Apple, Microsoft), so the
    endpoint must be https and pass the SSRF check every server-side fetch
    uses, with its name resolving. Resolves DNS: call off the event loop.
    """
    from urllib.parse import urlsplit

    text = str(endpoint or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        return "invalid push endpoint"
    if parts.scheme != "https" or not parts.hostname:
        return "push endpoint must be an https URL"
    try:
        from kazma_core.security.ssrf import validate_url

        validate_url(text, block_unresolved=True)
    except (ValueError, OSError) as exc:  # SSRFError is a ValueError
        from kazma_core.errors import validation_error

        return f"push endpoint refused: {validation_error(exc)}"
    return None


def _endpoint_hash(sub: dict[str, Any]) -> str:
    ep = str((sub or {}).get("endpoint") or "")
    return hashlib.sha256(ep.encode("utf-8")).hexdigest()[:24]


def _as_list(raw: Any) -> list[dict[str, Any]]:
    try:
        subs = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:  # not JSON: no readable subscriptions
        return []
    return subs if isinstance(subs, list) else []


def _update_subs(change: Any) -> int:
    """Apply ``change(list) -> list`` to the stored subscriptions; return the count.

    AUD-103: subscribe, unsubscribe and the 410 prune each loaded the list,
    changed it and saved it, so two at once (a browser subscribing while a
    finished turn pruned a dead device) dropped one of the changes. One
    ``atomic_update`` (BEGIN IMMEDIATE / FOR UPDATE) holds the row across the
    read and the write. Blocking: call off the event loop.
    """
    from kazma_core.config_store import get_config_store

    count = 0

    def _apply(raw: Any) -> str:
        nonlocal count
        subs = change(_as_list(raw))[-_MAX_SUBSCRIPTIONS:]
        count = len(subs)
        return json.dumps(subs)

    get_config_store().atomic_update(_SUBS_KEY, _apply)
    return count


def subscribe(subscription: dict[str, Any]) -> dict[str, Any]:
    """Persist a browser PushSubscription JSON. Idempotent per endpoint.

    Blocking (DNS and a settings write): call off the event loop.
    """
    if not isinstance(subscription, dict) or not subscription.get("endpoint"):
        return {"status": "error", "error": "invalid subscription"}
    refusal = _endpoint_refusal(str(subscription.get("endpoint")))
    if refusal:
        return {"status": "error", "error": refusal}
    hid = _endpoint_hash(subscription)
    record = dict(subscription)
    record["_id"] = hid
    count = _update_subs(lambda subs: [s for s in subs if s.get("_id") != hid] + [record])
    return {"status": "ok", "count": count}


def unsubscribe(endpoint: str) -> dict[str, Any]:
    """Remove a subscription by endpoint. Blocking: call off the event loop."""
    hid = hashlib.sha256(str(endpoint or "").encode("utf-8")).hexdigest()[:24]
    count = _update_subs(lambda subs: [s for s in subs if s.get("_id") != hid])
    return {"status": "ok", "count": count}


def list_subscriptions() -> list[dict[str, Any]]:
    return [s for s in _load_subs() if s.get("endpoint")]


def _push_plan() -> tuple[tuple[str, str], list[dict[str, Any]]] | None:
    """The keys and subscriptions a finished turn is pushed to, or None: the
    owner switched the notifications off, no VAPID keys, or no device.
    Settings-store reads: run off the event loop."""
    if not turn_complete_notifications_on():
        return None
    keys = ensure_vapid_keys()
    if not keys:
        return None
    subs = list_subscriptions()
    if not subs:
        return None
    return keys, subs


async def notify_push_turn_complete(summary: str) -> int:
    """Fire-and-forget push to every subscription. Returns delivered count.

    Dead subscriptions (410 Gone) are pruned. Never raises.
    """
    try:
        from pywebpush import webpush, WebPushException
    except Exception:
        return 0
    plan = await asyncio.to_thread(_push_plan)
    if plan is None:
        return 0
    (pub, priv), subs = plan

    def _send_one(sub: dict[str, Any]) -> bool:
        # AUD-103: checked again at send time -- a stored endpoint from an
        # older build, or a name that now resolves to an internal address.
        refusal = _endpoint_refusal(str(sub.get("endpoint") or ""))
        if refusal:
            logger.warning("[Push] subscription not sent: %s", refusal)
            return False
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
                try:
                    unsubscribe(str(sub.get("endpoint") or ""))
                except Exception:
                    # The dead device is tried (and pruned) again next turn.
                    logger.warning("[Push] could not prune a dead subscription", exc_info=True)
            logger.debug("[Push] send failed (%s)", status or "unknown")
            return False
        except Exception:
            logger.debug("[Push] send failed", exc_info=True)
            return False

    results = await asyncio.gather(*(asyncio.to_thread(_send_one, s) for s in subs))
    return sum(1 for ok in results if ok)
