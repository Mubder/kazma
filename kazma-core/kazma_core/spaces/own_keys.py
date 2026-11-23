"""A member's own model keys (Spaces S2-04, plan O2 and 4.9).

Members use the install's model keys, each under a daily limit (O2). A
member may instead use a key of their own for a provider the install has:
"Use my own key" in Settings -> My account. Only where Spaces is enforced,
and never for the admin (the install's keys are the admin's).

* **Stored in their personal space, sealed with its key** (4.9): the vault
  name ``space:<personal space>:model_key.<provider>`` (:func:`_secret_name`),
  a space's secret, so S1-26 seals it with the space's own key and S1-27's
  count, export and erase of that space reach it. It is read through
  ``SecretVault.for_space`` -- the person's own work in a shared space uses
  it too -- and only for the person it belongs to (:func:`key_for_call`
  reads the key of the person bound now, never another's). No route, page
  or log line gives the key back: the person is told only that one is set.
* **Used at the call.** Every model call goes through a provider class's
  ``chat`` or ``chat_stream``, and each asks ``LLMProvider._person_call``
  first (S2-03's hook): for a person with their own key for that provider
  the call is made by a client carrying their key alone
  (``LLMProvider._own_key_client``). It never falls back to the install's
  key, nor switches to the install's other keys (providers stage 4): a key
  of theirs that is refused fails the call, said as theirs.
* **Their spend on their own key is theirs**: the answer such a call gave
  is marked (``LLMResponse.own_key``), the ledger records the row from it
  (``own_key``, ``llm_ledger.answered_with_own_key``: never worked out
  from a provider's name), and neither their daily limit
  (``llm_ledger.usage_of``) nor the install's (``usage_since``) counts it.
* **Only a provider signed in with a plain key** (:func:`_takes_own_key`):
  a Bearer, ``x-api-key`` or ``api-key`` header, not a server on this
  network (no key), not AWS or Google's own sign-in.

Blocking functions read the identity store and the vault: off the event
loop.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bound_asker, spaces_enforced

logger = logging.getLogger(__name__)

__all__ = [
    "OwnKeyRefused",
    "OwnKeyUnreadable",
    "key_for_call",
    "providers_of",
    "refusal",
    "remove_key",
    "key_space",
    "set_key",
]

#: The part of a vault name after the space that marks a model key.
_KEY_NAME = "model_key."
#: The sign-ins a member may bring their own key for (``providers._p``'s
#: ``auth``): a key in a header.
_KEYED_AUTH = frozenset({"bearer", "x-api-key", "api-key"})
#: How long a person's key, or the knowledge that they have none, is kept in
#: memory. A change made in this process is seen at once (:func:`_forget`);
#: one made by another process within this many seconds.
_CACHE_S = 30.0
#: Keys longer than this are not keys.
_KEY_MAX = 4096


class OwnKeyRefused(ValueError):  # noqa: N818 -- named for what happened, as people.PeopleRefused
    """A change to a person's own keys that is refused; nothing changed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class OwnKeyUnreadable(RuntimeError):  # noqa: N818 -- named for the state
    """Whether the person has a key of their own could not be read (the
    identity store or the vault is away). The call is refused: it is never
    made with the install's key in its place."""


# ── whose and where ─────────────────────────────────────────────────────


def refusal(account: str) -> tuple[str, str] | None:
    """``(code, sentence)`` when *account* has no own keys to set, else None:
    Spaces not enforced, the admin, or no personal space. Blocking."""
    if not spaces_enforced():
        return "spaces_off", "Your own model keys come with Spaces."
    if not account:
        return "no_account", "Sign in to use your own key."
    if account == OWNER_ACCOUNT_ID:
        return "admin_uses_install_keys", "The admin's keys are the install's, under Providers."
    if not _personal_space(account):
        return "no_personal_space", "Your own keys are kept in your personal space, and you have none."
    return None


def _personal_space(account: str) -> str:
    """*account*'s personal space, or "" (none, or being erased). Raises
    ``IdentityUnavailable``. Blocking."""
    from kazma_core.spaces import identity

    for space in (identity.read_registry().get("spaces") or {}).values():
        if space.get("personal_of") == account and not space.get("erasing_at"):
            return str(space.get("id") or "")
    return ""


def _secret_name(space: str, provider: str) -> str:
    """The vault name of a key for *provider*, kept in *space*."""
    from kazma_core.spaces.connected_accounts import SPACE_SECRET_PREFIX

    return f"{SPACE_SECRET_PREFIX}{space}:{_KEY_NAME}{str(provider).strip().lower()}"


def _takes_own_key(entry: dict[str, Any] | None) -> bool:
    """Whether a provider row is signed in with a plain key a member may
    bring their own of: its preset's sign-in is a key in a header, and it is
    not a server on this network."""
    from kazma_core.providers import PROVIDER_PRESETS, is_local_preset
    from kazma_core.runtime.live_llm import url_is_local

    if not entry:
        return False
    name = str(entry.get("name") or "").strip().lower()
    preset = str(entry.get("preset") or "").strip().lower() or name
    if is_local_preset(preset) or url_is_local(str(entry.get("base_url") or "")):
        return False
    auth = str((PROVIDER_PRESETS.get(preset) or {}).get("auth") or "bearer")
    return auth in _KEYED_AUTH


# ── the cache ───────────────────────────────────────────────────────────

_cache_lock = threading.Lock()
#: ``(account, provider) -> (until, key or "")``.
_cache: dict[tuple[str, str], tuple[float, str]] = {}


def _forget(account: str) -> None:
    with _cache_lock:
        for held in [k for k in _cache if k[0] == account]:
            _cache.pop(held, None)


def _read_key(account: str, provider: str) -> str:
    """*account*'s own key for *provider*, or "" (none). Raises
    :class:`OwnKeyUnreadable`. Blocking."""
    held_key = (account, str(provider).strip().lower())
    now = time.monotonic()
    with _cache_lock:
        held = _cache.get(held_key)
    if held is not None and held[0] > now:
        return held[1]
    from kazma_core.db.pg_helpers import store_errors
    from kazma_core.security.vault import SpaceKeyMissing, get_vault
    from kazma_core.spaces.identity import IdentityUnavailable

    try:
        space = _personal_space(account)
        vault = get_vault() if space else None
        # No vault: no key of anyone's can be stored, so none is used.
        key = (vault.for_space(space).retrieve(_secret_name(space, provider)) or "") if vault else ""
    except (IdentityUnavailable, SpaceKeyMissing, *store_errors()) as exc:
        raise OwnKeyUnreadable(
            "whether you use your own key could not be read right now; try again"
        ) from exc
    with _cache_lock:
        _cache[held_key] = (now + _CACHE_S, key)
    return key


# ── at the call ─────────────────────────────────────────────────────────


def key_for_call(provider: str) -> tuple[str, str, dict[str, Any]] | None:
    """For a model call to *provider*: ``(account, key, provider row)`` when
    the person the work is for has their own key for it, else None. None
    while Spaces is not enforced (in memory: nothing is read), with no
    person bound (Kazma's own work), for the admin, and for a provider that
    takes no key. Raises :class:`OwnKeyUnreadable`. Blocking."""
    if not spaces_enforced() or not provider:
        return None
    account = str(bound_asker() or "")
    if not account or account == OWNER_ACCOUNT_ID:
        return None
    key = _read_key(account, provider)
    if not key:
        return None
    entry = _provider_row(provider)
    if not _takes_own_key(entry):
        return None
    return account, key, entry or {}


def _provider_row(provider: str) -> dict[str, Any] | None:
    """The install's row for *provider*, or None. Blocking (settings)."""
    from kazma_core.model_registry import peek_model_registry

    registry = peek_model_registry()
    return registry.get_provider(provider) if registry is not None else None


def key_space(account: str) -> str:
    """The personal space *account*'s own keys are kept in, or "" (none).
    Raises ``IdentityUnavailable``. Blocking."""
    return _personal_space(account)


# ── what the person sees and changes ────────────────────────────────────


def providers_of(account: str, registry: Any) -> list[dict[str, Any]]:
    """The providers *account* may bring their own key for: each that serves
    a model they may use (S2-03) and is signed in with a plain key, with
    ``own`` (a key of theirs is kept), never the key. Raises
    ``IdentityUnavailable`` and :class:`OwnKeyUnreadable`. Blocking."""
    from kazma_core.models.options import model_options
    from kazma_core.spaces.person_models import offered

    listed = offered(model_options(registry, details=False), person=account)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in listed.get("options") or []:
        name = str(option.get("provider") or "")
        if not name or name in seen:
            continue
        seen.add(name)
        if not _takes_own_key(registry.get_provider(name)):
            continue
        out.append({"provider": name, "label": str(option.get("provider_label") or name),
                    "model": str(option.get("model") or ""),
                    "own": bool(_read_key(account, name))})
    return sorted(out, key=lambda p: p["label"].lower())


def _checked(account: str, provider: str, registry: Any) -> str:
    """The person's personal space, when *provider* is one they may bring a
    key for; else :class:`OwnKeyRefused`. Blocking."""
    why = refusal(account)
    if why:
        raise OwnKeyRefused(*why)
    names = {p["provider"] for p in providers_of(account, registry)}
    if str(provider) not in names:
        raise OwnKeyRefused("unknown_provider", "There is no such provider for your own key.")
    return _personal_space(account)


def set_key(account: str, provider: str, key: str, registry: Any) -> None:
    """Keep *key* as *account*'s own key for *provider*, in their personal
    space. Raises :class:`OwnKeyRefused`. The key is checked by the caller
    first (a call that answers). Blocking."""
    clean = str(key or "").strip()
    if not clean or len(clean) > _KEY_MAX or any(ord(ch) < 33 or ord(ch) > 126 for ch in clean):
        raise OwnKeyRefused("bad_key", "A key is one line of letters, digits and signs.")
    space = _checked(account, provider, registry)
    from kazma_core.security.vault import get_vault

    vault = get_vault()
    if vault is None:
        raise OwnKeyRefused("vault_unavailable", "Kazma's vault is not set up, so no key can be kept.")
    vault.for_space(space).store(_secret_name(space, provider), clean, category="model_key")
    _forget(account)
    logger.info("[own-keys] a key of their own kept: account=%s provider=%s", account, provider)


def remove_key(account: str, provider: str, registry: Any) -> bool:
    """Forget *account*'s own key for *provider*: their calls use the
    install's key again. True when one was kept. Any provider's: a key of
    theirs for a provider no longer offered to them can still be removed.
    Raises :class:`OwnKeyRefused`. Blocking."""
    _ = registry
    why = refusal(account)
    if why:
        raise OwnKeyRefused(*why)
    space = _personal_space(account)
    from kazma_core.security.vault import get_vault

    vault = get_vault()
    removed = bool(vault.for_space(space).delete(_secret_name(space, provider))) if vault else False
    _forget(account)
    if removed:
        logger.info("[own-keys] a key of their own removed: account=%s provider=%s", account, provider)
    return removed
