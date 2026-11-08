"""Providers checked in the background (providers stage 4).

A provider's health was known only when someone pressed Test, or when a chat
failed on it. Once an hour (:data:`EVERY_S`) the maintenance cadence asks
every switched-on chat provider with what its sign-in needs for its model
list -- no model call, no tokens -- and keeps what it found
(``registry.provider_check.<name>``: ``{"ok", "code", "at", "failures"}``,
Kazma's own state).

- Unreachable twice in a row: the provider's card says Unreachable
  (``health: down``), and when it is the provider in use the owner is told
  once (``llm.provider_unreachable.<name>``); its next answer says
  Not tested again (a list that answers is not a chat that works) and, for
  the provider in use, one recovery note.
- A key the provider refused: when the provider has other keys, that key
  rests and the next is used (kazma_core.provider_keys), so chats move off
  it before one fails on it.

A provider with no model list (Azure, Bedrock, Vertex) is not checked here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["EVERY_S", "STAMP_KEY", "check_key", "health_sweep"]

EVERY_S = 3600.0
STAMP_KEY = "registry.provider_health_sweep"
_CHECK_TIMEOUT_S = 20.0


def check_key(provider: str) -> str:
    """The settings row holding *provider*'s last background check."""
    return f"registry.provider_check.{provider}"


def _checkable(registry: Any) -> list[dict[str, Any]]:
    from kazma_core.models.modality import row_is_speech
    from kazma_core.provider_check import credentials_problem
    from kazma_core.providers import PROVIDER_PRESETS

    rows = []
    for row in registry.list_providers() or []:
        name = str(row.get("name") or "")
        if not name or not row.get("enabled", True) or not row.get("base_url"):
            continue
        if row_is_speech(row, registry.get_discovered_models(name) or []):
            continue
        preset = PROVIDER_PRESETS.get(str(row.get("preset") or name).lower()) or {}
        if preset and not preset.get("models_endpoint"):
            continue
        if credentials_problem(registry, row) is not None:
            continue
        rows.append(row)
    return rows


async def _check_all(registry: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    from kazma_core.bounded_cancel import wait_for_bounded

    from kazma_core.db.pg_helpers import store_errors

    async def one(name: str) -> Any:
        try:
            return await wait_for_bounded(registry.discover(name), _CHECK_TIMEOUT_S, what="provider check")
        except (TimeoutError, *store_errors()) as exc:  # a check that could not run is no answer
            logger.debug("[provider-health] %s not checked: %s", name, type(exc).__name__)
            return None

    names = [str(r["name"]) for r in rows]
    found = await asyncio.gather(*(one(n) for n in names))
    return dict(zip(names, found, strict=True))


def _refused_key(registry: Any, row: dict[str, Any]) -> None:
    """The key in use was refused: rest it if the provider has another."""
    if not row.get("api_keys"):
        return
    name = str(row.get("name") or "")
    _url, key = registry.resolve_provider_credentials(name)

    class _RefusedError(Exception):
        pass

    registry.switch_provider_key(name, key, _RefusedError("HTTP 401: the key was refused"))


def health_sweep(*, registry: Any = None, now: float | None = None, force: bool = False) -> dict[str, str]:
    """Check every checkable provider when an hour has passed since the last
    pass (*force*: now). Returns ``{provider: code}`` (``ok``, or why not);
    empty when it was not due. Blocking: the maintenance thread."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    now = time.time() if now is None else now
    if not force:
        try:
            last = float(store.get(STAMP_KEY) or 0)
        except (TypeError, ValueError):
            last = 0.0
        if now - last < EVERY_S:
            return {}
    store.set(STAMP_KEY, now, category="registry")
    if registry is None:
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
    rows = _checkable(registry)
    if not rows:
        return {}
    answers = asyncio.run(_check_all(registry, rows))
    active = str((registry.get_active_profile() or {}).get("provider") or "").lower()
    out: dict[str, str] = {}
    for row in rows:
        name = str(row["name"])
        found = answers.get(name)
        if found is None:
            continue  # not checked: nothing learned
        code = "ok" if found.ok else str(found.error or "failed")
        out[name] = code
        _record(store, registry, row, code, now, is_active=name.lower() == active)
    return out


def _record(store: Any, registry: Any, row: dict[str, Any], code: str, now: float, *, is_active: bool) -> None:
    from kazma_core.observability.ops_alerts import alert

    name = str(row["name"])
    before = store.get(check_key(name)) or {}
    failures = 0 if code == "ok" else int((before.get("failures") if isinstance(before, dict) else 0) or 0) + 1
    store.set(check_key(name), {"ok": code == "ok", "code": code, "at": now, "failures": failures},
              category="registry")
    unreachable = code in ("unreachable", "blocked_url") or code.startswith("http_5")
    refused = code in ("http_401", "http_403")
    if refused:
        _refused_key(registry, row)
    if unreachable and failures == 2:
        registry.set_provider_health(name, "down")
        if is_active:
            alert(f"llm.provider_unreachable.{name}", f"{name} cannot be reached",
                  f"The provider in use, {name}, did not answer its model list twice in a row "
                  "(an hour apart). Chats may fail or fall back to the backup model.", severity="warn")
    elif code == "ok" and str(row.get("health") or "") == "down":
        registry.set_provider_health(name, "unknown")
        if is_active:
            alert(f"llm.provider_reachable.{name}", f"{name} answers again",
                  f"The provider in use, {name}, answers again. Test it to see that chat works.",
                  severity="info")
