"""Reconcile Kazma's shared command menus and verify each written scope."""

from __future__ import annotations

import json
import logging
from typing import Any

from kazma_core.agent.command_catalog import menu_commands

logger = logging.getLogger(__name__)


async def reconcile_commands(http: Any) -> dict[str, str]:
    """Default and localized menus, without altering owner-specific chat scopes."""
    results: dict[str, str] = {}
    for scope in ("default", "all_private_chats", "all_group_chats"):
        for lang in ("", "en", "ar"):
            key = f"{scope}:{lang or 'default'}"
            expected = menu_commands(lang)
            payload = {"commands": expected, "scope": {"type": scope}, "language_code": lang}
            try:
                response = await http.post("/setMyCommands", json=payload)
                response.raise_for_status()
                if not response.json().get("ok"):
                    raise ValueError("registration refused")
                actual = await http.get(
                    "/getMyCommands", params={"scope": json.dumps(payload["scope"]), "language_code": lang}
                )
                actual.raise_for_status()
                data = actual.json()
                results[key] = "verified" if data.get("ok") and data.get("result") == expected else "mismatch"
            except Exception as exc:
                # HTTP exception text includes the URL, which includes the bot token.
                results[key] = f"failed ({type(exc).__name__})"
            if results[key] != "verified":
                logger.warning("[telegram] command menu %s: %s", key, results[key])
    return results
