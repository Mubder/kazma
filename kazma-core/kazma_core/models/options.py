"""The models a person can choose, for every picker (providers stage 3).

The web chat's picker, the sidebar's, the chat apps' ``/model`` menu and the
terminal app each built their own list from the provider rows: bare names
(the same name under two providers could not be told apart), a keyless rule
of their own, five models per provider on Discord and Slack, duplicates
merged in the terminal app. They read this one list now: every model a
working chat provider offers, named ``provider:model``
(:mod:`kazma_core.model_refs`), with what is known about it
(:mod:`kazma_core.model_details`), and which one is the default.
"""

from __future__ import annotations

from typing import Any

__all__ = ["model_options"]


def _offered(registry: Any, row: dict[str, Any]) -> list[str]:
    """A provider's chat models: those the owner picked, else every model it
    lists or found."""
    from kazma_core.models.modality import chat_models

    name = str(row.get("name") or "")
    visible = registry.get_visible_models(name) or []
    if visible:
        return chat_models(visible)
    found = registry.get_discovered_models(name) or []
    return chat_models([*(row.get("models") or []), *found])


def model_options(registry: Any, *, details: bool = True) -> dict[str, Any]:
    """``{"default": ref, "options": [...]}``. Each option: ``ref``,
    ``provider``, ``provider_label``, ``model``, ``local``, and (with
    *details*) ``context_window``, ``vision``, ``tools``, ``priced``.
    Only providers that can answer a chat are offered: switched on, not a
    speech service, with what their sign-in needs. Blocking (settings and
    the vault): off the event loop."""
    from kazma_core.model_details import details_for
    from kazma_core.model_refs import format_ref
    from kazma_core.models.modality import row_is_speech
    from kazma_core.provider_check import credentials_problem
    from kazma_core.providers import is_local_preset
    from kazma_core.runtime.live_llm import url_is_local

    options: list[dict[str, Any]] = []
    for row in registry.list_providers() or []:
        name = str(row.get("name") or "")
        if not name or not row.get("enabled", True):
            continue
        if row_is_speech(row, registry.get_discovered_models(name) or []):
            continue
        if credentials_problem(registry, row) is not None:
            continue
        preset = str(row.get("preset") or "").strip().lower()
        local = is_local_preset(preset or name) or url_is_local(str(row.get("base_url") or ""))
        for model in _offered(registry, row):
            option: dict[str, Any] = {
                "ref": format_ref(name, model), "provider": name,
                "provider_label": str(row.get("display_name") or name), "model": model, "local": local,
            }
            if details:
                known = details_for(name, model, preset=preset, store=getattr(registry, "_config_store", None))
                option.update({
                    "context_window": known.context_window, "vision": known.vision, "tools": known.tools,
                    "priced": local or known.input_per_1m is not None,
                })
            options.append(option)
    profile = registry.get_active_profile() or {}
    default = format_ref(str(profile.get("provider") or ""), str(profile.get("model") or ""))
    return {"default": default, "options": options}
