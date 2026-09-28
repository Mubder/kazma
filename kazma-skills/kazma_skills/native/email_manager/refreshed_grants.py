"""Where a refreshed sign-in is kept (2026-09-29).

A sign-in (an OAuth grant) can sit in two places at once: the main Gmail and
the main Google Calendar share it when the Gmail sign-in covered the
calendar, and so do the main Microsoft mailbox and Outlook Calendar. A
refresh keeps its new tokens in every place that STILL holds the grant it
refreshed -- compared by refresh token, read the way the backends read it
(environment first, then the vault) -- and nowhere else:

- a place holding another grant is another account's. The Gmail refresh
  (every connector-health pass runs one) copied its grant over a Google
  Calendar signed in as a different Google account, which then showed the
  Gmail account's events under the other account's name;
- a place holding nothing was disconnected while the request ran, and
  writing there signed the account back in;
- a place holding a newer grant was signed in again meanwhile, and the old
  account must not replace the new one.

Extra accounts follow the same rule under their own names
(``accounts.persist_account_tokens(replaces=...)``). The check and the write
are not one transaction; what remains is the few milliseconds between them,
not the length of a network request.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

__all__ = [
    "keep_refreshed_google_grant",
    "keep_refreshed_microsoft_grant",
    "keep_refreshed_microsoft_mail",
]


def _holds(env_key: str, vault_key: str, refresh_token: str) -> bool:
    """True while this place still holds the grant *refresh_token*."""
    from kazma_skills.native.email_manager.credentials import cred

    return bool(refresh_token) and cred(env_key, vault_key) == refresh_token


def _not_kept(what: str) -> None:
    logger.info(
        "[oauth] a refreshed %s sign-in was not kept: nothing holds it any more "
        "(disconnected, or signed in again, while the request ran)",
        what,
    )


def keep_refreshed_google_grant(
    used_refresh: str, access: str, new_refresh: str, scopes: str = ""
) -> list[str]:
    """Keep a refresh of the main Google grant -- Gmail's refresh or Google
    Calendar's, which share this one rule. Google's token carries the whole
    grant, so either place that holds it can use it. Returns where it went
    (``gmail``, ``calendar``). Vault I/O: call it off the event loop."""
    from kazma_skills.native.calendar.credentials import VAULT_GOOGLE_REFRESH, persist_google_tokens
    from kazma_skills.native.email_manager.oauth_gmail import persist_gmail_tokens

    kept: list[str] = []
    if _holds("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token", used_refresh):
        persist_gmail_tokens(access, new_refresh, scopes=scopes)
        kept.append("gmail")
    if _holds("GOOGLE_CALENDAR_REFRESH", VAULT_GOOGLE_REFRESH, used_refresh):
        persist_google_tokens(access, new_refresh, scopes=scopes)
        kept.append("calendar")
    if not kept:
        _not_kept("Google")
    return kept


def _keep_microsoft_mail(access: str, new_refresh: str, address: str = "") -> None:
    from kazma_skills.native.email_manager.credentials import vault_retrieve, vault_store

    if access and access != "pending_refresh":
        os.environ["EMAIL_MS_ACCESS_TOKEN"] = access
        vault_store("email.microsoft.access_token", access, category="email")
    if new_refresh:
        os.environ["EMAIL_MS_REFRESH_TOKEN"] = new_refresh
        vault_store("email.microsoft.refresh_token", new_refresh, category="email")
    if address and address != (vault_retrieve("email.microsoft.oauth_address") or "").lower():
        vault_store("email.microsoft.oauth_address", address, category="email")
        logger.info("[oauth] the Microsoft mailbox's address came with its refresh: %s", address)


def keep_refreshed_microsoft_mail(
    used_refresh: str, access: str, new_refresh: str, *, address: str = ""
) -> bool:
    """Keep a refresh of the main Microsoft MAILBOX's grant, for the mailbox
    only: that refresh asks for mail scopes, so its token is not handed to
    Outlook Calendar, which refreshes its own copy. *address* is the one the
    refresh's id_token named -- how a mailbox signed in before Kazma read the
    address (2026-09-28) learns it without signing in again. Vault I/O: call
    it off the event loop."""
    if not _holds("EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token", used_refresh):
        _not_kept("Microsoft mail")
        return False
    _keep_microsoft_mail(access, new_refresh, address)
    return True


def keep_refreshed_microsoft_grant(
    used_refresh: str, access: str, new_refresh: str, scopes: str = ""
) -> list[str]:
    """Keep a refresh of the main Outlook Calendar's grant. That refresh names
    no scope, so its token carries the whole grant: the calendar keeps it,
    and so does the mailbox while it holds the same grant (a mail sign-in
    copies its grant to the calendar). Returns where it went (``calendar``,
    ``mail``). Vault I/O: call it off the event loop."""
    from kazma_skills.native.calendar.credentials import VAULT_MS_REFRESH, persist_microsoft_tokens

    kept: list[str] = []
    if _holds("MS_CALENDAR_REFRESH", VAULT_MS_REFRESH, used_refresh):
        persist_microsoft_tokens(access, new_refresh, scopes)
        kept.append("calendar")
    if _holds("EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token", used_refresh):
        _keep_microsoft_mail(access, new_refresh)
        kept.append("mail")
    if not kept:
        _not_kept("Microsoft")
    return kept
