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

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any
from kazma_core.http_tls import shared_ssl_context

logger = logging.getLogger(__name__)

__all__ = [
    "TESTING_MODE_TTL_DAYS",
    "WARN_BEFORE_DAYS",
    "ConnectorStatus",
    "google_grant_age_days",
    "google_calendar_grant_age_days",
    "check_google",
    "check_google_calendar",
    "check_connectors",
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
    #: The space whose connection this is, when it is not the owner's
    #: (Spaces S1-17); empty for the owner's space, as before.
    space: str = ""

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"connector": self.name, "ok": self.ok}
        if self.space:
            d["space"] = self.space
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
    """The bound space's connection secret *key* (the mail code's own
    reader, ``credentials.vault_retrieve``: the owner's space reads the
    names it always had; Spaces S1-17)."""
    from kazma_skills.native.email_manager.credentials import vault_retrieve

    return vault_retrieve(key)


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
    # Every token read is a vault read: off the loop (audit 2026-10-26 AUD-111).
    st = ConnectorStatus(name="google")

    if not await asyncio.to_thread(_vault_get, "email.gmail.refresh_token"):
        st.ok = True
        st.skipped = "no Google account connected"
        return st

    age = await asyncio.to_thread(google_grant_age_days)
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

    Gmail profile is the SoT for this check (the original incident was
    Gmail going silent). Drive used to be the probe via get_sync_provider;
    if Drive wasn't the offsite backend the live probe was skipped and
    only the clock ran.
    """
    try:
        from kazma_skills.native.email_manager.oauth_gmail import (
            refresh_gmail_access_token,
        )

        refresh = await asyncio.to_thread(_vault_get, "email.gmail.refresh_token")
        access = await asyncio.to_thread(_vault_get, "email.gmail.access_token")
        if refresh:
            try:
                access, _ = await refresh_gmail_access_token(refresh)
            except Exception as exc:  # noqa: BLE001
                return False, str(exc)[:200]
        if not access:
            return False, "no Gmail access token"
        import httpx

        async with httpx.AsyncClient(timeout=20.0, verify=shared_ssl_context()) as client:
            r = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {access}"},
            )
            if r.status_code < 400:
                return True, "connected"
            return False, (r.text or f"HTTP {r.status_code}")[:200]
    except Exception as exc:  # noqa: BLE001
        return False, f"probe failed: {exc}"


def google_calendar_grant_age_days(now: float | None = None) -> float | None:
    raw = _vault_get("calendar.google.connected_at")
    if not raw:
        return None
    try:
        stamp = float(raw)
    except ValueError:
        return None
    return max(0.0, ((now if now is not None else time.time()) - stamp) / 86400.0)


async def check_google_calendar(*, alert_on_findings: bool = True) -> ConnectorStatus:
    """Probe Google Calendar independently of Gmail. Never raises."""
    try:
        return await _check_google_calendar(alert_on_findings=alert_on_findings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[connector-health] calendar check failed", exc_info=True)
        return ConnectorStatus(
            name="google_calendar", ok=False, detail=f"check failed: {exc}"
        )


async def _check_google_calendar(*, alert_on_findings: bool = True) -> ConnectorStatus:
    st = ConnectorStatus(name="google_calendar")
    if not await asyncio.to_thread(_vault_get, "calendar.google.refresh_token"):
        st.ok = True
        st.skipped = "no Google Calendar connected"
        return st

    age = await asyncio.to_thread(google_calendar_grant_age_days)
    st.age_days = age
    if age is not None:
        st.expires_in_days = max(0.0, TESTING_MODE_TTL_DAYS - age)

    live_ok, live_detail = await _probe_google_calendar()
    st.ok = live_ok
    st.detail = live_detail

    if not live_ok:
        if alert_on_findings:
            _alert(
                "connector.google_calendar_expired",
                "Google Calendar sign-in has expired — calendar tools will sandbox.",
                f"{live_detail[:200]} Reconnect in Settings -> Email -> "
                "Connect Calendar (or Connect with Google). Gmail is a "
                "separate grant.",
                "critical",
            )
        return st

    if st.expires_in_days is not None and st.expires_in_days <= WARN_BEFORE_DAYS:
        if alert_on_findings:
            _alert(
                "connector.google_calendar_expiring",
                "Google Calendar sign-in expires today — reconnect when convenient.",
                f"OAuth status is Testing, so Google expires the grant every "
                f"{TESTING_MODE_TTL_DAYS:.0f} days. It is {age:.1f} days old. "
                "Settings -> Email -> Connect Calendar.",
                "warn",
            )
    return st


async def _probe_google_calendar() -> tuple[bool, str]:
    try:
        from kazma_skills.native.calendar.oauth_google import (
            probe_calendar_api,
            refresh_google_calendar_access_token,
        )

        refresh = await asyncio.to_thread(_vault_get, "calendar.google.refresh_token")
        access = await asyncio.to_thread(_vault_get, "calendar.google.access_token")
        if refresh:
            try:
                access, _ = await refresh_google_calendar_access_token(refresh)
            except Exception as exc:  # noqa: BLE001
                return False, str(exc)[:200]
        if not access:
            return False, "no Calendar access token"
        import httpx

        async with httpx.AsyncClient(timeout=20.0, verify=shared_ssl_context()) as client:
            ok, reason = await probe_calendar_api(client, access)
            return ok, reason if ok else (reason or "calendar probe failed")
    except Exception as exc:  # noqa: BLE001
        return False, f"probe failed: {exc}"


def _spaces_to_check() -> list[tuple[str, str]]:
    """(space, account) of every space whose connections are checked, the
    owner's first: the owner's alone while Spaces is not enforced; once it
    is, every space, each as the account its own work acts for. Blocking
    (the identity store)."""
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, spaces_enforced

    if not spaces_enforced():
        return [(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)]
    from kazma_core.spaces import identity

    spaces = sorted(identity.read_registry().get("spaces") or {})
    out: list[tuple[str, str]] = []
    for space in [OWNER_SPACE_ID, *(s for s in spaces if s != OWNER_SPACE_ID)]:
        found = identity.work_scope(space, None)
        if found is not None:
            out.append(found)
    return out


def _bound_to(space: str, account: str) -> Any:
    """The scope a space's check runs in: nothing while Spaces is not
    enforced (the owner's, as before), the space and its account once it is."""
    import contextlib

    from kazma_core.spaces.scope import bind_scope, spaces_enforced

    return bind_scope(space, account) if spaces_enforced() else contextlib.nullcontext()


async def check_connectors(*, alert_on_findings: bool = True) -> list[ConnectorStatus]:
    """Run every connector health check, space by space (Spaces S1-17): each
    space's grants are refreshed and probed in that space, so a refresh
    writes only its own. Only the owner's space raises the owner's alerts:
    another space's mailbox is not the owner's to hear about (O1), and its
    status is logged. Failures in one must not skip the rest."""
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    out: list[ConnectorStatus] = []
    for space, account in await asyncio.to_thread(_spaces_to_check):
        owners = space == OWNER_SPACE_ID
        with _bound_to(space, account):
            for check in (check_google, check_google_calendar):
                status = await check(alert_on_findings=alert_on_findings and owners)
                status.space = "" if owners else space
                out.append(status)
    return out
