"""Tell the operator a connector's credentials died, before they need them.

The Google grant on this install expired on 2026-08-27 and nothing said so.
Gmail was unusable and 29 consecutive backups went local-only, both
discovered by someone going looking rather than being told. The failure was
recorded faithfully in logs and a manifest -- the same silent-failure shape
the alerting layer exists for, in a path that predated it.

Testing-mode expiry
-------------------
This project's OAuth publishing status is deliberately "Testing": passing
Google's app verification would mean owning and hosting a public home page
for what is a single-user personal agent, which is a lot of work for no
benefit. The cost of that choice is that Google expires the refresh token
every 7 days, on the dot.

A predictable weekly break is only a problem if it is a surprise. Because
the connect time is recorded, this warns a day AHEAD -- "reconnect today" --
rather than reporting a breakage the operator has already tripped over.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "TESTING_MODE_TTL_DAYS",
    "WARN_BEFORE_DAYS",
    "ConnectorStatus",
    "google_grant_age_days",
    "check_google",
]

# Google's documented lifetime for refresh tokens issued by a project whose
# OAuth publishing status is "Testing".
TESTING_MODE_TTL_DAYS = 7.0

# Warn once the grant is this close to the cliff. One day is enough notice
# to act and short enough that the warning still means "today".
WARN_BEFORE_DAYS = 1.0

# The condition persists until a human re-consents, so a short cooldown
# would fire every run and be tuned out. Twelve hours means at most two
# reminders in the final day.
_COOLDOWN_S = 12 * 3600


@dataclass
class ConnectorStatus:
    name: str
    ok: bool = False
    detail: str = ""
    age_days: float | None = None
    expires_in_days: float | None = None
    skipped: str = ""

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"connector": self.name, "ok": self.ok}
        if self.detail:
            d["detail"] = self.detail[:300]
        if self.age_days is not None:
            d["age_days"] = round(self.age_days, 2)
        if self.expires_in_days is not None:
            d["expires_in_days"] = round(self.expires_in_days, 2)
        if self.skipped:
            d["skipped"] = self.skipped
        return d


def _vault_get(key: str) -> str:
    try:
        from kazma_core.security.vault import get_vault

        vault = get_vault()
        return str(vault.retrieve(key) or "") if vault else ""
    except Exception:  # noqa: BLE001
        return ""


def google_grant_age_days(now: float | None = None) -> float | None:
    """How long ago the Google refresh token was minted, or None if unknown.

    Grants created before this was recorded return None rather than 0 --
    guessing "just connected" for an unknown age would suppress exactly the
    warning an old grant needs.
    """
    raw = _vault_get("email.gmail.connected_at")
    if not raw:
        return None
    try:
        stamp = float(raw)
    except ValueError:
        return None
    return max(0.0, ((now if now is not None else time.time()) - stamp) / 86400.0)


def _alert(key: str, title: str, detail: str, severity: str) -> None:
    try:
        from kazma_core.observability.ops_alerts import alert

        alert(key, title, detail, severity=severity, cooldown_s=_COOLDOWN_S)
    except Exception:  # noqa: BLE001
        logger.debug("[connector-health] alert failed", exc_info=True)


async def check_google(*, alert_on_findings: bool = True) -> ConnectorStatus:
    """Probe the Google grant and report ahead of its expiry. Never raises.

    The guarantee is enforced here rather than assumed from the helpers.
    Every one of them has its own guard, which is exactly why it was easy
    to believe the whole function was safe -- it was not, and this runs on
    a background scheduler where an escaped exception is a health check
    that quietly stops checking.
    """
    try:
        return await _check_google(alert_on_findings=alert_on_findings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[connector-health] google check failed", exc_info=True)
        return ConnectorStatus(name="google", ok=False,
                               detail=f"check failed: {exc}")


async def _check_google(*, alert_on_findings: bool = True) -> ConnectorStatus:
    st = ConnectorStatus(name="google")

    if not _vault_get("email.gmail.refresh_token"):
        st.ok = True
        st.skipped = "no Google account connected"
        return st

    age = google_grant_age_days()
    st.age_days = age
    if age is not None:
        st.expires_in_days = max(0.0, TESTING_MODE_TTL_DAYS - age)

    live_ok, live_detail = await _probe_google()
    st.ok = live_ok
    st.detail = live_detail

    if not live_ok:
        if alert_on_findings:
            _alert(
                "connector.google_expired",
                "Google sign-in has expired -- Gmail is not working.",
                f"{live_detail[:200]} Reconnect in Settings -> Email -> "
                "Disconnect, then Connect with Google. Backups are unaffected: "
                "the offsite copy goes through rclone on its own credential.",
                "critical",
            )
        return st

    if st.expires_in_days is not None and st.expires_in_days <= WARN_BEFORE_DAYS:
        if alert_on_findings:
            _alert(
                "connector.google_expiring",
                "Google sign-in expires today -- reconnect when convenient.",
                f"This project's OAuth status is Testing, so Google expires the "
                f"grant every {TESTING_MODE_TTL_DAYS:.0f} days. It is "
                f"{age:.1f} days old. Settings -> Email -> Disconnect, then "
                "Connect with Google.",
                "warn",
            )
    return st


async def _probe_google() -> tuple[bool, str]:
    """Ask Google, rather than inferring from the clock.

    A grant can be revoked from the account's third-party access page long
    before the 7 days are up, and a clock-only check would call that healthy
    right until someone tried to use it.
    """
    try:
        from kazma_core.backup.cloud_sync import get_sync_provider

        provider = get_sync_provider()
        if provider is None:
            return True, "no Google provider configured"
        res = await provider.test_connection()
        if res.get("ok"):
            return True, str(res.get("message") or "connected")
        return False, str(res.get("error") or "unknown error")
    except Exception as exc:  # noqa: BLE001
        return False, f"probe failed: {exc}"
