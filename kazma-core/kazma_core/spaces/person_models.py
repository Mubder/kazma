"""Models per person (Spaces S2-03, plan 4.8): which models a person may
use, and their default.

Only where Spaces is enforced; an install that does not enforce it, the
admin, and Kazma's own work (no person bound) use every model the install
offers, exactly as before.

* **The list** is the person's ``policies[account]["models"]``
  (:data:`MODELS_POLICY`): ``{"allowed": [ref, ...], "default": ref}``,
  each a ``provider:model`` reference (:mod:`kazma_core.model_refs`), as the
  pickers name them (:mod:`kazma_core.models.options`). No ``allowed`` is
  every model the install offers (the install's list); no ``default`` is
  the install's default when they may use it, else the first they may.
* **Refused at the call.** Every model call goes through a provider's
  ``chat`` or ``chat_stream``; each asks :func:`call_refusal` first
  (``LLMProvider._refuse_a_model_not_allowed``), so a model not allowed is
  never called for the person, whatever chose it: a chat turn, a scheduled
  job, a swarm worker, research. A new provider class asks it too
  (``tests/test_person_models.py`` reads every ``chat`` and ``chat_stream``
  of every provider class).
* **Their default where nothing was chosen.** A model call that names no
  model gets the person's (:func:`model_for_work`: ``ModelRegistry.get_client``
  and the supervisor's ``resolve_live_client``), so a job or a worker
  booked with no model runs on a model they may use.
* **The pickers list only theirs** (:func:`offered`): the chat's
  ``/api/models/options`` and the chat apps' ``/model`` menu; a model the
  person names that is not theirs is refused (:func:`refusal`): the chat's
  stream before anything runs, ``/model``.

Blocking functions read the identity store: off the event loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bound_asker, spaces_enforced

__all__ = [
    "MODELS_POLICY",
    "ModelsRefused",
    "apply_models",
    "call_refusal",
    "checked_models",
    "listed_models",
    "model_for_work",
    "offered",
    "refusal",
]

logger = logging.getLogger(__name__)

#: The policy key of a person's models (S2-03).
MODELS_POLICY = "models"


class ModelsRefused(ValueError):
    """A change to a person's models that is refused; nothing was changed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _PersonModels:
    """The models one person may use (``allowed``: None is every model the
    install offers) and their default (``""``: none of their own)."""

    account: str
    allowed: frozenset[str] | None = None
    default: str = ""
    admin: bool = False

    @property
    def restricted(self) -> bool:
        return not self.admin and (self.allowed is not None or bool(self.default))

    def allows(self, ref: str) -> bool:
        return self.admin or self.allowed is None or _key(ref) in {_key(r) for r in self.allowed}


def _key(ref: str) -> str:
    """A reference as compared: the provider's name in any case."""
    provider, sep, model = str(ref or "").strip().partition(":")
    return f"{provider.strip().lower()}:{model.strip()}" if sep else provider.strip()


def _models_of(registry: dict[str, Any], account_id: str) -> _PersonModels:
    """*account_id*'s models, read from *registry* (no I/O)."""
    account_id = str(account_id or "")
    accounts = registry.get("accounts") or {}
    account = accounts.get(account_id) or {}
    if account_id == OWNER_ACCOUNT_ID or str(account.get("role")) == "admin":
        return _PersonModels(account_id, admin=True)
    policy = (registry.get("policies") or {}).get(account_id) or {}
    stored = policy.get(MODELS_POLICY) if isinstance(policy, dict) else None
    if not isinstance(stored, dict):
        return _PersonModels(account_id)
    raw = stored.get("allowed")
    allowed = frozenset(str(r) for r in raw if r) if isinstance(raw, (list, tuple)) else None
    return _PersonModels(account_id, allowed, str(stored.get("default") or ""))


def _models_here(person: str | None = None) -> _PersonModels | None:
    """The bound person's models, or None when no rule applies: Spaces not
    enforced, no person, the admin, nothing set for them. Raises
    :class:`kazma_core.spaces.identity.IdentityUnavailable`. Blocking."""
    if not spaces_enforced():
        return None
    account = str(person or bound_asker() or "")
    if not account:
        return None
    from kazma_core.spaces import identity

    models = _models_of(identity.read_registry(), account)
    return models if models.restricted else None


def _lookup_errors() -> tuple[type[BaseException], ...]:
    """What reading the model registry may raise: its stores, or no
    registry yet."""
    from kazma_core.db.pg_helpers import store_errors

    return (*store_errors(), RuntimeError, LookupError, ValueError, TypeError, AttributeError, OSError)


def _registry_ref(ref: str) -> str:
    """*ref* with its provider: a bare model name is looked up as the
    registry looks it up (the provider in use first). Blocking."""
    text = str(ref or "").strip()
    if not text:
        return ""
    try:
        from kazma_core.model_refs import format_ref
        from kazma_core.model_registry import get_model_registry

        registry = get_model_registry()
        named, model = registry.split_model_ref(text)
        if named:
            return format_ref(named, model)
        owner = registry.find_provider_for_model(model) or {}
        in_use = (registry.get_active_profile() or {}).get("provider")
        return format_ref(str(owner.get("name") or in_use or ""), model)
    except _lookup_errors():  # no registry: the name as it is
        logger.debug("[person-models] could not resolve %r", text, exc_info=True)
        return text


def _install_default() -> str:
    """The install's default model, as a reference. Blocking."""
    try:
        from kazma_core.model_refs import format_ref
        from kazma_core.model_registry import get_model_registry

        profile = get_model_registry().get_active_profile() or {}
        return format_ref(str(profile.get("provider") or ""), str(profile.get("model") or ""))
    except _lookup_errors():  # no registry: no default to judge
        logger.debug("[person-models] no install default", exc_info=True)
        return ""


def _their_choice(models: _PersonModels) -> str:
    """The model a person's work runs with in place of one they may not
    use: their default, else the install's when they may use it, else the
    first they may use ("" when they may use none: the call is refused)."""
    if models.default:
        return models.default
    install = _install_default()
    if install and models.allows(install):
        return install
    return sorted(models.allowed)[0] if models.allowed else ""


def refusal(ref: str, *, person: str | None = None) -> str | None:
    """Why the bound person (or *person*) may not use *ref*, or None.
    Blocking."""
    from kazma_core.spaces.identity import IdentityUnavailable

    try:
        models = _models_here(person)
    except IdentityUnavailable:
        return "whose models these are cannot be read right now; try again"
    if models is None or models.allows(_registry_ref(ref)):
        return None
    return (f"the model {ref} is not one this person may use; the admin chooses their models in "
            "Settings -> People")


def call_refusal(provider: str, model: str) -> str | None:
    """For a model call: why *provider*'s *model* may not be called for the
    person the work is for, or None. A call with no person bound (Kazma's
    own work) is never refused. Blocking."""
    from kazma_core.model_refs import format_ref

    if not spaces_enforced() or not bound_asker():
        return None
    text = str(model or "").strip()
    # A model that already names its provider (a ``provider:model`` a
    # caller passed on) is judged as it is; a bare id is the client's.
    return refusal(text if not provider or _names_a_provider(text) else format_ref(provider, text))


def _names_a_provider(ref: str) -> bool:
    """Whether *ref* is ``provider:model`` for a provider row (Ollama's
    ``llama3:8b`` is a bare id). Blocking."""
    if ":" not in ref:
        return False
    try:
        from kazma_core.model_registry import get_model_registry

        return bool(get_model_registry().split_model_ref(ref)[0])
    except _lookup_errors():  # no registry: a bare id
        return False


def model_for_work(asked: str | None) -> str | None:
    """The model the bound person's work runs with: *asked* when they may
    use it (or no rule applies), else their choice (:func:`_their_choice`);
    None keeps the install's default. Never raises (an unreadable store
    keeps *asked*: the call itself is refused then). Blocking."""
    from kazma_core.spaces.identity import IdentityUnavailable

    try:
        models = _models_here()
    except IdentityUnavailable:
        return asked
    if models is None:
        return asked
    if asked and models.allows(_registry_ref(asked)):
        return asked
    if not asked and not models.default:
        install = _install_default()
        if not install or models.allows(install):
            return None  # the install's default is theirs to use: kept
    # A model they may not use -- a stored run_model, a router's hint, a
    # checkpointed last_model (the name the provider returned, which may not
    # be the configured id) -- gives way to one they may (the S2-03 second
    # review: with the install's default allowed it was kept, and the call
    # refused the turn).
    return _their_choice(models) or asked or None


def offered(listed: dict[str, Any], *, person: str | None = None) -> dict[str, Any]:
    """The picker's list (:func:`kazma_core.models.options.model_options`)
    as the person sees it: only the models they may use, and their default.
    Blocking."""
    from kazma_core.spaces.identity import IdentityUnavailable

    try:
        models = _models_here(person)
    except IdentityUnavailable:
        return {**listed, "options": [], "unavailable": True}
    if models is None:
        return listed
    options = [o for o in listed.get("options") or [] if models.allows(str(o.get("ref") or ""))]
    default = str(listed.get("default") or "")
    if models.default:
        default = models.default
    elif not models.allows(default):
        default = str(options[0]["ref"]) if options else ""
    return {**listed, "options": options, "default": default}


# ── People: the admin's list per person ──────────────────────────────────


def checked_models(account_id: str, allowed: Any, default: Any, registry: dict[str, Any]) -> tuple[list[str] | None, str]:
    """*allowed* (a list of references, or None: every model) and *default*
    (a reference, or empty) as a person's models, refused
    (:class:`ModelsRefused`): not a list of references, a default they may
    not use, or the admin (who has every model)."""
    if _models_of(registry, account_id).admin:
        raise ModelsRefused("admin_has_every_model", "The admin has every model.")
    if allowed is not None and (not isinstance(allowed, (list, tuple))
                                or not all(isinstance(r, str) and r.strip() for r in allowed)):
        raise ModelsRefused("bad_models", "Name the models as a list of provider:model references.")
    if default is not None and not isinstance(default, str):
        raise ModelsRefused("bad_models", "Name the default as a provider:model reference.")
    clean = sorted({r.strip() for r in allowed}) if allowed is not None else None
    chosen = str(default or "").strip()
    if chosen and clean is not None and _key(chosen) not in {_key(r) for r in clean}:
        raise ModelsRefused("default_not_allowed", "The default must be one of the models they may use.")
    if clean == []:
        raise ModelsRefused("no_models", "Leave a person at least one model.")
    return clean, chosen


def apply_models(registry: dict[str, Any], account_id: str, allowed: list[str] | None, default: str) -> None:
    """Write checked models into *registry*'s policies (in place)."""
    policy = dict(registry["policies"].get(account_id) or {})
    if allowed is None and not default:
        policy.pop(MODELS_POLICY, None)
    else:
        stored: dict[str, Any] = {}
        if allowed is not None:
            stored["allowed"] = list(allowed)
        if default:
            stored["default"] = default
        policy[MODELS_POLICY] = stored
    registry["policies"][account_id] = policy


def listed_models(registry: dict[str, Any], account_id: str) -> dict[str, Any]:
    """What People shows for a person: ``{"allowed": [ref, ...] or None,
    "default": ref, "admin": bool}``."""
    models = _models_of(registry, account_id)
    return {"allowed": sorted(models.allowed) if models.allowed is not None else None,
            "default": models.default, "admin": models.admin}
