"""Whether the backup models can answer, and which could (providers stage 1).

``agent.nonstop.failover.chain`` was a free-text list of model ids that
nothing checked. On the live install (2026-11-08) it named
``google/gemini-2.5-flash`` while the Google provider was off with no key,
the monthly failover drill had failed ("No backup model is configured"), and
OpenRouter and Groq -- both with keys, both working -- were used as nothing.

:func:`backup_status` says, for each model of a chain, the provider that
would serve it (the runtime's own rule, ``find_provider_for_model``) and
whether that provider can be called: switched on, and a key or a server on
this network. :func:`suggest_backups` offers a model of each other usable
provider. Settings shows both, and refuses to save a backup nothing can
serve. Blocking (settings and the vault): callers run them off the loop.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

__all__ = ["backup_status", "parse_chain", "suggest_backups", "unusable_backups"]


def parse_chain(value: Any) -> list[str]:
    """The chain's model ids from a stored string or list."""
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v or "").strip()]
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return []


def _vendor(entry: dict[str, Any]) -> str:
    """A provider's vendor: its address's host (one vendor's outage takes
    every model it serves down at once), else its name."""
    host = urlsplit(str(entry.get("base_url") or "")).hostname or ""
    return host.lower() or str(entry.get("name") or "").lower()


def _usable(registry: Any, entry: dict[str, Any]) -> bool:
    from kazma_core.runtime.live_llm import key_is_usable, url_is_local

    if not entry or not entry.get("enabled", True):
        return False
    base_url = str(entry.get("base_url") or "")
    if url_is_local(base_url):
        return True
    _url, key = registry.resolve_provider_credentials(str(entry.get("name") or ""))
    return key_is_usable(key)


def _active_vendor(registry: Any) -> str:
    profile = registry.get_active_profile() or {}
    entry = registry.get_provider(str(profile.get("provider") or "")) or {}
    return _vendor(entry) if entry else ""


def backup_status(registry: Any, chain: list[str]) -> list[dict[str, Any]]:
    """For each model of *chain*: ``{"model", "provider", "usable",
    "same_vendor"}``. ``provider`` is "" when no provider lists the model."""
    own = _active_vendor(registry)
    out = []
    for model in chain:
        entry = registry.find_provider_for_model(model) or {}
        name = str(entry.get("name") or "")
        out.append({
            "model": model,
            "provider": name,
            "usable": bool(name) and _usable(registry, entry),
            "same_vendor": bool(name) and bool(own) and _vendor(entry) == own,
        })
    return out


def unusable_backups(registry: Any, chain: list[str]) -> list[dict[str, Any]]:
    """The entries of *chain* no usable provider serves."""
    return [row for row in backup_status(registry, chain) if not row["usable"]]


#: A provider whose last check failed is not offered as a backup: on the live
#: install Z.AI was switched on with a key, its Test failing, and was the
#: second suggestion.
_FAILED_HEALTH = frozenset({"down", "chat_failing"})


def suggest_backups(registry: Any, limit: int = 4) -> list[dict[str, str]]:
    """A model of each usable provider of another vendor than the one in
    use, whose last check did not fail: ``{"provider", "display_name",
    "model"}``, the provider's own chosen models first."""
    from kazma_core.providers import default_model_for

    own = _active_vendor(registry)
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for entry in registry.list_providers() or []:
        name = str(entry.get("name") or "")
        vendor = _vendor(entry)
        if not name or vendor == own or vendor in seen or not _usable(registry, entry):
            continue
        if str(entry.get("health") or "").lower() in _FAILED_HEALTH:
            continue
        visible = registry.get_visible_models(name) or []
        model = (visible[0] if visible else "") or registry.probe_model_for(name) or default_model_for(name)
        # Only a model the runtime would send to THIS provider: a chain entry
        # is a bare id, and an id no provider lists goes to the one in use.
        owner = registry.find_provider_for_model(model) if model else None
        if not model or str((owner or {}).get("name") or "") != name:
            continue
        seen.add(vendor)
        out.append({"provider": name, "display_name": str(entry.get("display_name") or name), "model": model})
        if len(out) >= limit:
            break
    return out
