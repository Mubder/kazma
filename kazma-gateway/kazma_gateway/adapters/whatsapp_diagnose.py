"""The WhatsApp connector's Test: what Meta says about the number, joined with
what Kazma's webhook received (2026-11-14).

Each check answers a question a person would ask, with the step that fixes
it (shared pieces: ``kazma_gateway.connector_test``):

- ``token``: does Meta accept the access token for this phone number id?
- ``app_secret``: is the Meta app's secret saved? Every webhook call is
  signed with it, and without it Kazma answers no call.
- ``webhook``: the address and verify token to give Meta, and whether Meta
  has called it since Kazma started.
- ``latest``: the last message a person sent -- did it reach Kazma?
- ``window``: WhatsApp's 24-hour rule (worth knowing, not wrong).
- ``allowed``: who may write in?
- ``listening``: is the number answering, and what has the webhook received?

WhatsApp has no history to read back (unlike Discord and Slack): what a
message became is what the running adapter recorded.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kazma_gateway.adapters.whatsapp_api import DEFAULT_API_VERSION, WhatsAppClient, WhatsAppError
from kazma_gateway.adapters.whatsapp_parse import wa_digits
from kazma_gateway.adapters.whatsapp_receive import WHATSAPP_REASONS
from kazma_gateway.connector_test import Checks, allowlist_check, judge_message, listening, when

logger = logging.getLogger(__name__)

__all__ = ["diagnose"]

_WEBHOOK_PATH = "/api/webhooks/whatsapp"
_META_SETUP = "in your Meta app: WhatsApp → Configuration → Webhook"
_NEVER = (
    "Meta did not deliver it to Kazma's webhook. In your Meta app, WhatsApp → Configuration: the "
    "webhook's callback URL must be Kazma's address shown above, and the messages field must be "
    "subscribed (Webhook fields → messages → Subscribe)."
)


def _webhook_address(public_url: str) -> str:
    base = (public_url or "").strip().rstrip("/")
    return f"{base}{_WEBHOOK_PATH}" if base else ""


async def diagnose(
    token: str,
    *,
    phone_number_id: str = "",
    app_secret: str = "",
    verify_token: str = "",
    api_version: str = "",
    public_url: str = "",
    allowed_users: list[str] | None = None,
    owner_ids: list[str] | tuple[str, ...] = (),
    open_to_everyone: bool | None = None,
    live: dict[str, Any] | None = None,
    client: WhatsAppClient | None = None,
) -> dict[str, Any]:
    """The Test's result: ``{"success", "bot_name", "error", "checks"}``."""
    checks = Checks()
    add = checks.add
    allowed = [wa_digits(u) for u in (allowed_users or []) if wa_digits(u)]
    bot_name: str | None = None

    if not phone_number_id:
        add("token", False, (
            "No Phone number ID saved. Copy it from your Meta app: WhatsApp → API Setup → "
            "Phone number ID, paste it here and Save."
        ))
    else:
        own = client is None
        api = client or WhatsAppClient(token, phone_number_id, api_version=api_version or DEFAULT_API_VERSION)
        try:
            info = await api.phone_number()
            shown = info.get("display_phone_number") or phone_number_id
            bot_name = str(info.get("verified_name") or shown)
            quality = info.get("quality_rating")
            add("token", True, (
                f"Meta accepts the access token for {shown} ({info.get('verified_name') or 'no verified name'})"
                + (f", quality rating {quality}." if quality else ".")
            ))
        except WhatsAppError as exc:
            add("token", False, f"Meta refused: {exc}. Check the access token and the Phone number ID, then Save.")
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.debug("[whatsapp.test] phone number lookup failed", exc_info=True)
            add("token", False, f"Kazma could not reach Meta ({type(exc).__name__}).")
        finally:
            if own:
                await api.aclose()

    if app_secret:
        add("app_secret", True, "The Meta app's secret is saved: Kazma checks Meta's signature on every webhook call.")
    else:
        add("app_secret", False, (
            "No app secret saved, so Kazma answers no webhook call (it cannot tell Meta's calls from "
            "anyone else's). Copy it from your Meta app: App settings → Basic → App secret, paste it here and Save."
        ))

    address = _webhook_address(public_url)
    events = (live or {}).get("events") or {}
    if not address:
        add("webhook", False, (
            "Meta must reach Kazma over the internet, and Kazma does not know its public address: set "
            "KAZMA_PUBLIC_URL (for example your Cloudflare Tunnel's address) and restart. The webhook is "
            f"that address followed by {_WEBHOOK_PATH}."
        ))
    elif not verify_token:
        add("webhook", False, (
            f"No verify token saved. Make one (any long random text), save it here, then {_META_SETUP}: "
            f"callback URL {address}, the same verify token, Verify and save."
        ))
    elif events.get("webhook_verified") or live and live.get("messages"):
        heard = "verified the webhook" if events.get("webhook_verified") else "delivered messages"
        add("webhook", True, f"Meta {heard} at {address} since Kazma started.")
    else:
        add("webhook", None, (
            f"Meta has not called {address} since Kazma started. If WhatsApp is new here, {_META_SETUP}: "
            "callback URL as shown, the verify token saved here, Verify and save; then subscribe the "
            "messages field. Then send the number a message and Test again."
        ))

    last = (live or {}).get("last_person") or {}
    if last.get("id"):
        ok, detail = judge_message(
            message_id=last.get("id"),
            author_id=last.get("author_id"),
            who=f"+{last.get('author_id')}",
            stamp=when(last.get("at")),
            live=live,
            # The record says whether its author was refused, in WhatsApp's words.
            allowed=[],
            place="The last message a person sent",
            platform="WhatsApp",
            never_hint=_NEVER,
            reasons=WHATSAPP_REASONS,
        )
        add("latest", ok, detail)
    else:
        add("latest", None, (
            "No message has reached Kazma's webhook since it started. Send the business number a "
            "WhatsApp message from your phone, then Test again."
        ))

    add("window", None, (
        "WhatsApp lets a business write to a person only within 24 hours of their last message "
        "(after that, approved templates only). Kazma answers inside that window; a reminder or "
        "alert due later reaches your other chat apps, not WhatsApp."
    ))

    add("allowed", *allowlist_check(
        allowed,
        owner_ids=list(owner_ids),
        open_to_everyone=open_to_everyone,
        live=live,
        platform="WhatsApp",
        find_id="your number with its country code, for example +965 9999 1234",
        field="Allowed numbers",
        id_word="number",
    ))
    add("listening", *listening(
        live, "WhatsApp",
        not_running=(
            "Kazma's WhatsApp connection is not running: the access token or the Phone number ID is "
            "missing, or WhatsApp is switched off."
        ),
    ))
    return checks.result(bot_name)
