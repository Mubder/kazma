"""Webhook Setup Helper — Configure Telegram's webhook URL for Kazma.

Provides a lightweight async function to register (or deregister)
the Kazma webhook endpoint with Telegram's Bot API.

Usage:
    # From Python
    import asyncio
    from kazma_comms.setup_telegram import setup_webhook
    asyncio.run(setup_webhook("https://my-server.com"))

    # From CLI
    python -m kazma_comms.setup_telegram \\
        --public-url https://kazma.example.com \\
        --token 123456:ABC-DEF...
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT = 20.0


# ── Core functions ──────────────────────────────────────────────────────


async def setup_webhook(
    public_url: str,
    *,
    token: str | None = None,
    drop_pending_updates: bool = True,
    max_connections: int = 40,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Register the Kazma webhook endpoint with Telegram.

    Performs a POST to the Telegram Bot API's ``setWebhook`` method,
    pointing it at ``{public_url}/api/webhooks/telegram/{token}``.

    Args:
        public_url: The public-facing base URL of your Kazma server
                    (e.g., 'https://kazma.example.com').
        token: Bot token. If None, reads TELEGRAM_BOT_TOKEN from env.
        drop_pending_updates: If True (default), drops any updates that
                              arrived while the webhook was not registered.
                              Prevents a flood of stale messages on startup.
        max_connections: Maximum number of simultaneous HTTPS connections
                         Telegram will use to deliver updates (default 40).
        timeout: HTTP request timeout in seconds.

    Returns:
        Dict with keys:
            ok: bool — whether the operation succeeded.
            description: str — Telegram's description of the result.
            url: str — The webhook URL that was registered.

    Raises:
        ValueError: If no token is available.
        httpx.HTTPError: On network or API errors.

    Example:
        >>> import asyncio
        >>> result = asyncio.run(setup_webhook("https://kazma.example.com"))
        >>> print(result["ok"])   # True
        >>> print(result["url"])  # https://kazma.example.com/api/webhooks/telegram/...
    """
    resolved_token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not resolved_token:
        raise ValueError(
            "No Telegram bot token provided. Set TELEGRAM_BOT_TOKEN environment "
            "variable or pass `token=...` explicitly."
        )

    webhook_url = f"{public_url.rstrip('/')}/api/webhooks/telegram/{resolved_token}"

    payload: dict[str, Any] = {
        "url": webhook_url,
        "drop_pending_updates": drop_pending_updates,
        "max_connections": max_connections,
    }

    api_url = f"{TELEGRAM_API_BASE}/bot{resolved_token}/setWebhook"

    logger.info("[Setup] Registering webhook: %s", webhook_url)
    logger.debug("[Setup] Payload: %s", json.dumps(payload, indent=2))

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(api_url, json=payload)
        response.raise_for_status()
        data = response.json()

    ok = data.get("ok", False)
    description = data.get("description", "No description")

    if ok:
        logger.info("[Setup] ✅ Webhook registered successfully: %s", description)
    else:
        logger.error("[Setup] ❌ Webhook registration failed: %s (code=%d)", description, response.status_code)

    return {
        "ok": ok,
        "description": description,
        "url": webhook_url,
        "result": data.get("result", True),
    }


async def delete_webhook(
    *,
    token: str | None = None,
    drop_pending_updates: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Deregister the webhook, reverting to long-polling mode.

    Args:
        token: Bot token. If None, reads TELEGRAM_BOT_TOKEN from env.
        drop_pending_updates: If True, drops any pending updates.
        timeout: HTTP request timeout.

    Returns:
        Dict with ok, description, and result fields.
    """
    resolved_token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not resolved_token:
        raise ValueError("No Telegram bot token provided.")

    payload: dict[str, Any] = {"drop_pending_updates": drop_pending_updates}

    api_url = f"{TELEGRAM_API_BASE}/bot{resolved_token}/deleteWebhook"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(api_url, json=payload)
        response.raise_for_status()
        data = response.json()

    logger.info("[Setup] Webhook deleted: %s", data.get("description", ""))
    return {
        "ok": data.get("ok", False),
        "description": data.get("description", ""),
        "result": data.get("result", True),
    }


async def get_webhook_info(
    *,
    token: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Fetch the current webhook configuration from Telegram.

    Args:
        token: Bot token. If None, reads TELEGRAM_BOT_TOKEN from env.
        timeout: HTTP request timeout.

    Returns:
        Dict with current webhook status: url, pending_update_count,
        last_error_date, max_connections, etc.
    """
    resolved_token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not resolved_token:
        raise ValueError("No Telegram bot token provided.")

    api_url = f"{TELEGRAM_API_BASE}/bot{resolved_token}/getWebhookInfo"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(api_url)
        response.raise_for_status()
        data = response.json()

    if data.get("ok"):
        result = data.get("result", {})
        logger.info("[Setup] Webhook info: url=%s, pending=%d", result.get("url", "(none)"), result.get("pending_update_count", 0))
    return data


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════


def _main() -> None:
    """CLI entry point for webhook setup.

    Usage:
        python -m kazma_comms.setup_telegram --public-url https://myserver.com
        python -m kazma_comms.setup_telegram --delete

    Set the TELEGRAM_BOT_TOKEN environment variable or pass it with --token.
    """
    parser = argparse.ArgumentParser(
        description="Kazma Telegram Webhook Setup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Register webhook
  python -m kazma_comms.setup_telegram --public-url https://kazma.example.com

  # Register with explicit token
  python -m kazma_comms.setup_telegram --public-url https://kazma.example.com --token 123:abc

  # Delete webhook (revert to polling)
  python -m kazma_comms.setup_telegram --delete

  # Get current webhook info
  python -m kazma_comms.setup_telegram --info
""",
    )

    parser.add_argument("--public-url", "-u", help="Public URL of your Kazma server")
    parser.add_argument("--token", "-t", help="Telegram bot token (or set TELEGRAM_BOT_TOKEN env var)")
    parser.add_argument("--delete", action="store_true", help="Delete the webhook (revert to polling)")
    parser.add_argument("--info", action="store_true", help="Show current webhook info")
    parser.add_argument("--keep-pending", action="store_true", help="Keep pending updates (default: drop them)")
    parser.add_argument(
        "--max-connections",
        type=int,
        default=40,
        help="Max simultaneous HTTPS connections (default: 40)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    token = args.token or os.environ.get("TELEGRAM_BOT_TOKEN", "")

    async def run() -> None:
        if args.info:
            result = await get_webhook_info(token=token or None)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        if args.delete:
            result = await delete_webhook(
                token=token or None,
                drop_pending_updates=not args.keep_pending,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return

        if not args.public_url:
            parser.error("--public-url is required for webhook setup (or use --delete / --info)")

        result = await setup_webhook(
            public_url=args.public_url,
            token=token or None,
            drop_pending_updates=not args.keep_pending,
            max_connections=args.max_connections,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))

    try:
        import asyncio
        asyncio.run(run())
    except Exception as exc:
        logger.error("Setup failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    _main()
