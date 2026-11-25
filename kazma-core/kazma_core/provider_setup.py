"""Setting a provider up: what was typed is checked before anything is sent or
saved, one service is one row, and rows that repeat each other are tidied.

Beside :mod:`kazma_core.provider_check` (one short message to a candidate)
and :mod:`kazma_core.provider_endpoints` (the data). Here:

* :func:`endpoint_address` -- the address of one of a provider's endpoints,
  with the person's resource, workspace or account in it;
* :func:`field_problems` -- each provider type's own fields, checked;
* :func:`rows_at` -- the rows that already are this provider at this address;
* :func:`add_key` -- one more key on a row (``provider_keys`` tries each);
* :func:`tidy_report` and :func:`tidy` -- rows that repeat each other, made
  one, and rows that are off with no key, removed. A key stored in a row is
  never lost by it (one that cannot be read, or that changed meanwhile, stops
  it), and a row that lives on a key of the environment stays as it is.

Every function reads or writes settings: off the event loop.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from kazma_core.provider_endpoints import ENDPOINTS, FIELDS, FORM_FIELDS, endpoint_fields
from kazma_core.providers import PROVIDER_PRESETS

logger = logging.getLogger(__name__)

__all__ = [
    "SetupRefused",
    "add_key",
    "address_of",
    "endpoint_address",
    "field_problems",
    "rows_at",
    "tidy",
    "tidy_report",
]

#: The most extra keys one row may hold.
_MAX_KEYS = 20


class SetupRefused(Exception):
    """A setup step that is refused, with a code the pages word; nothing was
    changed. ``field`` names what it is about, when one field is."""

    def __init__(self, code: str, message: str, field: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field


# ── What was typed ──────────────────────────────────────────────────────


def _field_ok(field: str, value: str) -> bool:
    rule = FIELDS.get(field)
    return bool(rule) and len(value) <= 200 and re.fullmatch(rule["pattern"], value) is not None


def endpoint_address(preset_id: str, endpoint_id: str, values: dict[str, Any] | None = None) -> str:
    """The address of *preset_id*'s endpoint *endpoint_id*, filled with
    *values*. Raises :class:`SetupRefused` for an unknown endpoint or a value
    its field does not take (it would name another host)."""
    for eid, template in ENDPOINTS.get(str(preset_id or "").lower(), ()):
        if eid != endpoint_id:
            continue
        address = template
        for field in endpoint_fields(template):
            value = str((values or {}).get(field) or "").strip()
            if not _field_ok(field, value):
                raise SetupRefused("bad_field", f"{field}: not a value this provider takes.", field)
            address = address.replace("{" + field + "}", value)
        return address
    raise SetupRefused("unknown_endpoint", "This provider has no such address.")


def _preset_of(entry: dict[str, Any]) -> dict[str, Any]:
    preset = str(entry.get("preset") or "").strip().lower() or str(entry.get("name") or "").strip().lower()
    return PROVIDER_PRESETS.get(preset) or {}


def _is_vertex(entry: dict[str, Any], form: str) -> bool:
    return form == "google" and str(entry.get("google_mode") or "") == "vertex_ai"


def field_problems(entry: dict[str, Any], stored: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """What is missing or malformed in *entry*'s own fields, each ``{"field",
    "code"}`` (``missing`` or ``malformed``): the address, and what its form
    asks (:data:`FORM_FIELDS`). A value unchanged from *stored* is not judged:
    a row saved before this stays as it is."""
    form = str(_preset_of(entry).get("form") or "key")
    stored = stored or {}
    problems: list[dict[str, str]] = []

    def judge(field: str, required: bool) -> None:
        value = str(entry.get(field) or "").strip()
        if stored and value == str(stored.get(field) or "").strip():
            return
        if not value:
            if required:
                problems.append({"field": field, "code": "missing"})
        elif not _field_ok(field, value):
            problems.append({"field": field, "code": "malformed"})

    if form != "google" or _is_vertex(entry, form):
        for field, required in FORM_FIELDS.get(form, ()):
            judge(field, required)
    base = str(entry.get("base_url") or "").strip()
    if base and base != str(stored.get("base_url") or "").strip() and not _named_address(base):
        problems.append({"field": "base_url", "code": "malformed"})
    return problems


def _named_address(base: str) -> bool:
    """Whether *base* names a web server: http or https (or no scheme, which
    the one normalizer completes), a host, and no control character, space or
    unfilled placeholder in it."""
    from urllib.parse import urlsplit

    from kazma_core.url_utils import normalize_provider_url

    if any(ord(c) <= 32 or ord(c) == 127 for c in base) or "{" in base or "}" in base:
        return False
    try:
        typed = urlsplit(base)
        if "://" in base and typed.scheme.lower() not in ("http", "https"):
            return False
        parts = urlsplit(normalize_provider_url(base))
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


# ── One row per provider and address ────────────────────────────────────


def address_of(url: str) -> str:
    """An address as two rows are compared by it: normalized, its scheme and
    host in lower case -- never its path, its query or a password in it, which
    tell two accounts of one server apart."""
    from urllib.parse import urlsplit

    from kazma_core.url_utils import normalize_provider_url

    text = str(url or "").strip()
    scheme, sep, rest = text.partition("://")
    text = scheme.lower() + sep + rest if sep else text
    try:
        parts = urlsplit(normalize_provider_url(text))
    except ValueError:  # a port out of range: compared as typed
        return text.rstrip("/")
    user, at, host = parts.netloc.rpartition("@")
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{user}{at}{host.lower()}{path}{query}"


def _identity_of(entry: dict[str, Any]) -> tuple[str, ...] | None:
    """What makes two rows the same provider: the wire format, the address
    and the fields its sign-in is made of; None for a row with neither (it
    repeats nothing)."""
    from kazma_core.providers import capabilities

    preset = str(entry.get("preset") or "").strip().lower() or str(entry.get("name") or "").strip().lower()
    style = str(capabilities(preset).get("api_style") or "openai")
    kind = "speech" if str(entry.get("kind") or "") == "speech" else "chat"
    base = str(entry.get("base_url") or "").strip()
    if style == "bedrock":
        region = str(entry.get("region") or "").strip().lower()
        return (style, kind, region) if region else None
    if style == "google":
        if _is_vertex(entry, "google"):
            project = str(entry.get("project_id") or "").strip().lower()
            return (style, kind, project, str(entry.get("location") or "").lower()) if project else None
        return (style, kind, address_of(base)) if base else (style, kind, "ai_studio")
    if not base:
        return None
    if style == "azure":
        return style, kind, address_of(base), str(entry.get("api_version") or "").strip().lower()
    return style, kind, address_of(base)


def _stored_keys(row: dict[str, Any]) -> list[tuple[str, str]]:
    from kazma_core.provider_keys import keys_of

    return keys_of(str(row.get("api_key") or ""), row)


def _on_env_key(registry: Any, row: dict[str, Any]) -> bool:
    """True when *row* holds no key of its own and the runtime sends one all
    the same (``<PROVIDER>_API_KEY``, ``KAZMA_API_KEY``): a key put in the row
    would take its place, so a setup or a tidy never does it silently."""
    from kazma_core.runtime.live_llm import coerce_api_key, key_is_usable

    if _stored_keys(row):
        return False
    _url, key = registry.resolve_provider_credentials(str(row.get("name") or ""))
    return key_is_usable(coerce_api_key(key))


def rows_at(registry: Any, entry: dict[str, Any], *, exclude: str = "") -> list[dict[str, Any]]:
    """The rows that already are *entry*'s provider at its address, the ones
    in use first: ``{"name", "display_name", "enabled", "has_key", "keys",
    "env_key", "local"}`` -- never a key."""
    from kazma_core.runtime.live_llm import url_is_local

    here = _identity_of(entry)
    if here is None:
        return []
    active = str((registry.get_active_profile() or {}).get("provider") or "")
    out = []
    for row in registry.list_providers():
        name = str(row.get("name") or "")
        if name == exclude or _identity_of(row) != here:
            continue
        keys = len(_stored_keys(row))
        if not keys and not row.get("enabled"):
            continue  # a catalog row nobody set up: the setup fills it in
        out.append({
            "name": name, "display_name": str(row.get("display_name") or name),
            "enabled": bool(row.get("enabled")), "has_key": keys > 0, "keys": keys,
            "in_use": name == active, "env_key": _on_env_key(registry, row),
            "local": url_is_local(str(row.get("base_url") or "")),
        })
    return sorted(out, key=lambda r: (not r["in_use"], not r["enabled"], not r["has_key"], r["name"]))


def _changed(registry: Any, name: str) -> None:
    """A row changed outside the registry's own writers: its clients are
    rebuilt."""
    from kazma_core.runtime.model_switch import notify_credentials_changed

    registry.forget_client(name)
    notify_credentials_changed(name)


def _new_key_id(rows: list[dict[str, Any]]) -> str:
    import secrets

    taken = {str(k.get("id")) for k in rows}
    while True:
        kid = "k" + secrets.token_hex(4)
        if kid not in taken:
            return kid


def _with_key(row: dict[str, Any], key: str, label: str, held: set[str]) -> bool:
    """*key* put on *row* (raw, as stored) when none of *held* is it: as its
    own key when it has none, else as an extra one. False: it has it."""
    if key in held:
        return False
    held.add(key)
    if not str(row.get("api_key") or "").strip():
        row["api_key"] = key
        return True
    extra = [dict(k) for k in (row.get("api_keys") or []) if isinstance(k, dict)]
    if len(extra) >= _MAX_KEYS:
        raise SetupRefused("too_many_keys", f"A provider holds at most {_MAX_KEYS} more keys.")
    extra.append({"id": _new_key_id(extra), "label": label, "api_key": key})
    row["api_keys"] = extra
    return True


def _raw_rows(current: Any) -> list[dict[str, Any]]:
    import json

    if isinstance(current, str):
        try:
            current = json.loads(current)
        except ValueError:
            current = None
    if not isinstance(current, list):
        raise SetupRefused("list_unreadable", "The provider list could not be read; nothing was changed.")
    return [dict(r) for r in current if isinstance(r, dict)]


def add_key(registry: Any, name: str, key: str, label: str = "", *, base_url: str = "") -> dict[str, Any]:
    """Put *key* on the row *name*: its own key when it has none (and the row
    is switched on), else one more, tried when the others fail
    (``provider_keys``). ``{"added": bool, "keys": n}``; a key it has already
    is not added twice. *base_url*: the address the key was offered for -- a
    row that is not there (any more) takes none."""
    from kazma_core.runtime.live_llm import coerce_api_key, key_is_usable

    clean = coerce_api_key(key)
    if not key_is_usable(clean) or "****" in clean:
        raise SetupRefused("bad_key", "Paste the key to add.", "api_key")
    row = next((r for r in registry.list_providers() if str(r.get("name") or "") == name), None)
    if row is None:
        raise SetupRefused("unknown_provider", "There is no such provider.")
    if registry.stored_key_is_undecryptable(name):
        raise SetupRefused("key_undecryptable", "This provider's saved key cannot be read, so nothing is added to it.")
    if _on_env_key(registry, row):
        raise SetupRefused(
            "env_key", "This provider uses a key from the environment; a key added here would take its place.")
    outcome: dict[str, Any] = {"added": False, "keys": 0}

    def update(current: Any) -> list[dict[str, Any]]:
        rows = _raw_rows(current)
        target = next((r for r in rows if str(r.get("name") or "") == name), None)
        if target is None:
            raise SetupRefused("unknown_provider", "There is no such provider.")
        fresh = registry.resolve_rows([target])[0]
        if base_url.strip() and address_of(str(fresh.get("base_url") or "")) != address_of(base_url):
            raise SetupRefused("row_moved", "This provider is not at that address any more; nothing was added.")
        held = {k for _label, k in _stored_keys(fresh)}
        outcome["keys"] = len(held | {clean})
        first = not str(target.get("api_key") or "").strip()
        outcome["added"] = _with_key(target, clean, str(label or "").strip()[:60], held)
        if first and outcome["added"]:
            target["enabled"] = True
        return rows

    registry.update_provider_rows(update)
    _changed(registry, name)
    return outcome


# ── Tidying rows that repeat each other ─────────────────────────────────


def _unreadable_key(raw: dict[str, Any], row: dict[str, Any]) -> bool:
    """True when *row* holds a secret that is stored and cannot be read now."""
    from kazma_core.config_store import is_vault_ref

    if is_vault_ref(raw.get("api_key")) and not str(row.get("api_key") or "").strip():
        return True
    shown = {str(k.get("id")): str(k.get("api_key") or "") for k in row.get("api_keys") or [] if isinstance(k, dict)}
    return any(
        is_vault_ref(k.get("api_key")) and not shown.get(str(k.get("id")))
        for k in raw.get("api_keys") or [] if isinstance(k, dict)
    )


def _person_lists() -> set[str]:
    """The provider names a person's model list names (Spaces S2-03), lower
    case. Raises ``IdentityUnavailable``. Empty where Spaces is not set up."""
    from kazma_core.spaces import identity
    from kazma_core.spaces.person_models import MODELS_POLICY

    names: set[str] = set()
    for policy in (identity.read_registry().get("policies") or {}).values():
        models = (policy or {}).get(MODELS_POLICY) if isinstance(policy, dict) else None
        if isinstance(models, dict):
            for ref in [*(models.get("allowed") or []), models.get("default")]:
                if isinstance(ref, str) and ":" in ref:
                    names.add(ref.partition(":")[0].strip().lower())
    return names


def _enforced() -> bool:
    from kazma_core.spaces.scope import spaces_enforced

    return spaces_enforced()


def _why_kept(
    registry: Any, raw: dict[str, Any], row: dict[str, Any], keep: dict[str, Any], named: set[str],
) -> str:
    """Why *row* is not merged into *keep*, as a code, or ""."""
    name = str(row.get("name") or "")
    if name.lower() in named:
        return "person_list"
    if _enforced() and row.get("enabled"):
        return "members_may_hold_keys"  # a member's own key is kept by the row's name
    if _unreadable_key(raw, row):
        return "key_undecryptable"
    if _on_env_key(registry, row) or _on_env_key(registry, keep):
        return "env_key"
    if (row.get("secret_headers") or {}) != (keep.get("secret_headers") or {}):
        return "own_headers"
    for field in ("aws_access_key_id", "aws_secret_access_key", "aws_session_token", "url_as_typed"):
        if (row.get(field) or "") != (keep.get(field) or ""):
            return "own_sign_in"
    return ""


def _rank(row: dict[str, Any], active: str) -> tuple[Any, ...]:
    name = str(row.get("name") or "")
    keys = _stored_keys(row)
    return (name != active, not (row.get("enabled") and keys), not keys, not row.get("enabled"),
            -len(row.get("models") or []), name)


def _plan(registry: Any) -> dict[str, Any]:
    """What :func:`tidy_report` shows and :func:`tidy` does, one reading."""
    from kazma_core.model_registry_store import SEEDED_PRESETS, load_providers_unresolved
    from kazma_core.provider_check import credentials_problem

    raw = {str(r.get("name") or ""): r for r in load_providers_unresolved(registry.config_store) if isinstance(r, dict)}
    # Each stored row as it is now, its keys read again (the settings cache
    # holds a key another process replaced).
    stored = {str(r.get("name") or ""): r for r in registry.resolve_rows(list(raw.values()))}
    rows = [stored.get(str(r.get("name") or ""), r) for r in registry.list_providers()]
    active = str((registry.get_active_profile() or {}).get("provider") or "")
    named = _person_lists()
    by_identity: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        name = str(row.get("name") or "")
        here = _identity_of(row)
        set_up = bool(row.get("enabled")) or bool(_stored_keys(row))
        # A catalog row nobody set up repeats nothing unless another row does
        # its work under another name (live's "zai" beside "Z.AI").
        if here is not None and name in raw:
            by_identity.setdefault(here, []).append({**row, "_set_up": set_up})
    merges: list[dict[str, Any]] = []
    in_merge: set[str] = set()
    for group in by_identity.values():
        if len(group) < 2 or not any(r["_set_up"] for r in group):
            continue
        ranked = sorted(group, key=lambda r: _rank(r, active))
        keep, rest = ranked[0], ranked[1:]
        merge, kept = [], []
        for row in rest:
            why = _why_kept(registry, raw.get(str(row["name"])) or {}, row, keep, named)
            if not why and _unreadable_key(raw.get(str(keep["name"])) or {}, keep):
                why = "key_undecryptable"
            entry = {"name": str(row["name"]), "display_name": str(row.get("display_name") or row["name"]),
                     "enabled": bool(row.get("enabled")), "keys": len(_stored_keys(row))}
            (kept if why else merge).append({**entry, "why": why} if why else entry)
        in_merge.update(str(r["name"]) for r in group)
        merges.append({"keep": str(keep["name"]), "display_name": str(keep.get("display_name") or keep["name"]),
                       "base_url": str(keep.get("base_url") or ""), "merge": merge, "kept": kept})
    unused = [
        {"name": str(r["name"]), "display_name": str(r.get("display_name") or r["name"]),
         "base_url": str(r.get("base_url") or "")}
        for r in rows
        if str(r.get("name") or "") in raw and str(r["name"]) not in in_merge and str(r["name"]) != active
        and not r.get("enabled") and str(r["name"]) not in SEEDED_PRESETS and str(r["name"]).lower() not in named
        and not r.get("secret_headers") and not _unreadable_key(raw[str(r["name"])], r)
        and credentials_problem(registry, r) is not None
    ]
    return {"merges": merges, "unused": unused, "raw": raw, "rows": {str(r.get("name") or ""): r for r in rows}}


def tidy_report(registry: Any) -> dict[str, Any]:
    """The rows that repeat each other (``merges``: the row kept, the rows
    merged into it, and those that stay with the reason) and the rows that are
    off and lack what their sign-in needs (``unused``; a catalog row every
    install has is not one). Never a key. Raises ``IdentityUnavailable``."""
    from kazma_core.security.url_credentials import mask_url_credentials

    plan = _plan(registry)
    for part in (plan["merges"], plan["unused"]):
        for entry in part:
            entry["base_url"] = mask_url_credentials(entry["base_url"])
    return {"merges": plan["merges"], "unused": plan["unused"]}


def _copy_settings(registry: Any, names: list[str], keep: str, *, drop: bool) -> None:
    """The owner's prices and details kept under a merged row's name, put
    under the kept row's too (its own entry wins); *drop*: and taken from the
    old name. Copied before the rows are written and dropped after, each any
    number of times: a tidy cut between the two leaves every price in force."""
    from kazma_core.model_details import OVERRIDES_KEY
    from kazma_core.model_prices import OWNER_PRICES_KEY

    prefixes = [n.lower() + ":" for n in names]
    for key in (OWNER_PRICES_KEY, OVERRIDES_KEY):
        stored = registry.config_store.get(key)
        if not isinstance(stored, dict) or not any(str(k).lower().startswith(tuple(prefixes)) for k in stored):
            continue

        def move(current: Any) -> dict[str, Any]:
            held = dict(current) if isinstance(current, dict) else {}
            for ref in list(held):
                for prefix in prefixes:
                    if str(ref).lower().startswith(prefix):
                        held.setdefault(f"{keep.lower()}:{str(ref)[len(prefix):]}", held[ref])
                        if drop:
                            held.pop(ref, None)
            return held

        registry.config_store.atomic_update(key, move, category="models")


def tidy(registry: Any, *, merge: list[str] | None = None, remove: list[str] | None = None) -> dict[str, Any]:
    """Merge the rows *merge* into the row each repeats, and remove the unused
    rows *remove*; only what :func:`tidy_report` offers now. The kept row
    takes every key (as extra keys), the models, and the merged names as
    ``aliases``, so a chat, a backup or a saved profile naming one still
    finds it. A row that changed meanwhile, its keys included (with the vault
    on, a new key leaves the stored row as it was), refuses the whole change."""
    from kazma_core.model_registry_store import SEEDED_PRESETS

    plan = _plan(registry)
    wanted, dropped = set(merge or []), set(remove or [])
    into = {m["name"]: g["keep"] for g in plan["merges"] for m in g["merge"] if m["name"] in wanted}
    offered = {u["name"] for u in plan["unused"]}
    if set(into) != wanted or not dropped <= offered or not (wanted or dropped):
        raise SetupRefused("plan_changed", "These rows changed meanwhile; nothing was changed. Look again.")
    rows, raw = plan["rows"], plan["raw"]
    moved: dict[str, list[tuple[str, str]]] = {}
    for name, keep in into.items():
        label = str(rows[name].get("display_name") or name)
        moved.setdefault(keep, []).extend((own or label, key) for own, key in _stored_keys(rows[name]))
    involved = sorted(set(into) | set(into.values()) | dropped)
    gone = set(into) | dropped
    merged_into = {keep: [n for n, k in into.items() if k == keep] for keep in sorted(set(into.values()))}
    for keep, merged in merged_into.items():
        _copy_settings(registry, merged, keep, drop=False)

    def update(current: Any) -> list[dict[str, Any]]:
        listed = _raw_rows(current)
        now = {str(r.get("name") or ""): r for r in listed}
        fresh = {str(r.get("name") or ""): r for r in registry.resolve_rows([now[n] for n in involved if n in now])}
        if any(now.get(n) != raw.get(n) or n not in fresh or _stored_keys(fresh[n]) != _stored_keys(rows[n])
               for n in involved):
            raise SetupRefused("plan_changed", "These rows changed meanwhile; nothing was changed. Look again.")
        for keep, merged in merged_into.items():
            target = now[keep]
            have = {k for _l, k in _stored_keys(fresh[keep])}
            for label, key in moved.get(keep, []):
                _with_key(target, key, label[:60], have)
            models = {str(m) for n in [keep, *merged] for m in (now[n].get("models") or []) if str(m).strip()}
            target["models"] = sorted(models)
            aliases = [str(a) for a in (target.get("aliases") or [])]
            for n in merged:
                aliases += [n, *[str(a) for a in (now[n].get("aliases") or [])]]
            target["aliases"] = sorted(set(aliases) - {keep})
            target["enabled"] = bool(target.get("enabled")) or any(now[n].get("enabled") for n in merged)
            if not str(target.get("preset") or "").strip():
                # What it speaks: the preset a merged row was made from.
                target["preset"] = next(
                    (p for n in merged for p in (str(now[n].get("preset") or "").lower(), n.lower())
                     if p in PROVIDER_PRESETS), "")
        return [r for r in listed if str(r.get("name") or "") not in gone]

    registry.update_provider_rows(update, removed_presets=gone & SEEDED_PRESETS)
    for keep, merged in merged_into.items():
        _copy_settings(registry, merged, keep, drop=True)
        chosen = registry.get_selected_models(keep)
        also = [m for n in merged for m in registry.get_selected_models(n)]
        if chosen and also:
            # Both rows had chosen what to show: the kept row shows both.
            registry.set_selected_models(keep, sorted(set(chosen) | set(also)))
        _changed(registry, keep)
    for name in gone:
        registry.forget_client(name)
    logger.info("[provider-setup] tidied: %d merged, %d removed", len(into), len(dropped))
    return {"merged": len(into), "removed": len(dropped), "kept": sorted(merged_into)}
