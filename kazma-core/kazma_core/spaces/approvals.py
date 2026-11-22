"""Who answers an approval card (Spaces S2-05; decisions N4 and N21).

An approval card is a question Kazma asks before it does something for a
person. Once Spaces is enforced, a card goes to the person it asks for and
nobody else answers it: another member of the same shared space sees the
card and cannot decide it. One rule (:func:`_answerers`) says who may answer,
and every surface asks it -- the chat's approve route and its pending list,
the decision recorder every surface writes through (``kazma_ui.hitl_decision``,
the chat apps' buttons included), the swarm's approvals and task actions,
the bell and Web Push.

Who answers a card asked for *asker* in *space*:

* **the admin**, for an always-ask action or a folder grant (``safety.hitl.
  ALWAYS_HITL_TOOLS``) of a person other than the admin who holds
  Development, in a shared space with a project folder (decision N21: the
  admin approves those, from day one);
* else **the asker's approvers**, when the admin set some on the asker's
  account (decision N4: the identity store's ``policies[asker]["approvers"]``,
  a list of accounts; empty for everyone, so it changes nothing yet, and no
  page sets it until the owner turns the option on). They answer in the
  asker's place, never beside them: the point of an approver is that the
  asker does not approve their own cards;
* else **the asker**;
* a card that names no asker (work no person asked for, a card from before
  Spaces) is answered by the space's owners.

Answering a card for someone never makes their turn yours: the resumed turn
runs as its asker (``kazma_ui.routes_direct.misc``), whoever pressed.

Nothing here acts unless Spaces is enforced. Blocking (the identity store):
async callers use ``asyncio.to_thread``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from kazma_core.spaces.scope import OWNER_SPACE_ID, bound_asker, spaces_enforced

__all__ = [
    "APPROVERS_DECIDE",
    "APPROVERS_POLICY",
    "IDENTITY_UNAVAILABLE",
    "NOT_YOURS",
    "Answerers",
    "answer_refusal",
    "approvers_now",
    "card_fields",
    "own_click_refusal",
    "standing_grant_refusal",
    "tool_names",
]

#: The policy key of an account's approvers (N4).
APPROVERS_POLICY = "approvers"

#: The refusal code for a person who may not answer a card.
NOT_YOURS = "not_yours"
#: The refusal code when who answers cannot be read.
IDENTITY_UNAVAILABLE = "identity_unavailable"
#: The refusal code for a person whose cards go to approvers asking to skip
#: their own cards (N4).
APPROVERS_DECIDE = "approvers_decide"


@dataclass(frozen=True)
class Answerers:
    """The accounts that may answer one card, and why it is them."""

    accounts: frozenset[str]
    #: ``asker``, ``approvers``, ``admin`` (N21) or ``space_owners``.
    why: str
    #: Their names, in the order of :attr:`ordered`, for a card that says
    #: who it waits for.
    names: tuple[str, ...] = ()
    ordered: tuple[str, ...] = ()


def tool_names(tools: Any = None, tool: str = "") -> tuple[str, ...]:
    """The tool names a card asks about: its ``tools`` list (``[{"name"}]``
    or names) and its ``tool``."""
    names: list[str] = []
    for item in tools or ():
        name = item.get("name") if isinstance(item, dict) else item
        if name:
            names.append(str(name))
    if tool:
        names.append(str(tool))
    return tuple(dict.fromkeys(names))


def _always_ask(names: Iterable[str]) -> bool:
    """An always-ask action or a folder grant among *names* (N21's cards)."""
    from kazma_core.safety.call_preflight import ALWAYS_ASK_TOOLS

    return any(name in ALWAYS_ASK_TOOLS for name in names)


def _admin_of(registry: dict[str, Any]) -> str:
    from kazma_core.spaces.identity import _is_admin_account
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

    for account_id, account in (registry.get("accounts") or {}).items():
        if isinstance(account, dict) and _is_admin_account(account):
            return str(account_id)
    return OWNER_ACCOUNT_ID


def _holds_development(registry: dict[str, Any], account_id: str) -> bool:
    from kazma_core.spaces.identity import _is_admin_account

    account = (registry.get("accounts") or {}).get(account_id)
    if not isinstance(account, dict):
        return False
    policy = (registry.get("policies") or {}).get(account_id) or {}
    held = policy.get("development") if isinstance(policy, dict) else None
    return held if isinstance(held, bool) else _is_admin_account(account)


def _approvers(registry: dict[str, Any], account_id: str) -> tuple[str, ...]:
    """The accounts set to approve for *account_id* (N4), each an account
    that exists, never the asker themself; empty when none."""
    policy = (registry.get("policies") or {}).get(account_id) or {}
    raw = policy.get(APPROVERS_POLICY) if isinstance(policy, dict) else None
    if not isinstance(raw, (list, tuple)):
        return ()
    accounts = registry.get("accounts") or {}
    return tuple(dict.fromkeys(
        str(a) for a in raw if str(a or "") in accounts and str(a) != account_id))


def _space_owners(registry: dict[str, Any], space_id: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(
        str(m.get("account")) for m in registry.get("memberships") or ()
        if isinstance(m, dict) and m.get("space") == space_id and m.get("role") == "owner"))


def _name(registry: dict[str, Any], account_id: str) -> str:
    account = (registry.get("accounts") or {}).get(account_id) or {}
    return str(account.get("name") or "").strip() or account_id


def _answerers(space_id: str, asker: str | None, tools: Iterable[str] = ()) -> Answerers:
    """Who may answer a card *asker* is asked in *space_id* about *tools*
    (the module's rules). Reads the identity store whether or not Spaces is
    enforced; raises ``IdentityUnavailable`` when it cannot answer."""
    from kazma_core.spaces.identity import read_registry

    registry = read_registry()
    space = str(space_id or "") or OWNER_SPACE_ID
    who = str(asker or "")
    accounts = registry.get("accounts") or {}
    names = tuple(tools)
    ordered: tuple[str, ...]
    if who and who in accounts:
        admin = _admin_of(registry)
        shared_host = (space != OWNER_SPACE_ID
                       and ((registry.get("spaces") or {}).get(space) or {}).get("kind") == "host")
        if shared_host and who != admin and _always_ask(names) and _holds_development(registry, who):
            ordered, why = (admin,), "admin"
        elif approvers := _approvers(registry, who):
            ordered, why = approvers, "approvers"
        else:
            ordered, why = (who,), "asker"
    else:
        ordered, why = _space_owners(registry, space) or (_admin_of(registry),), "space_owners"
    return Answerers(accounts=frozenset(ordered), why=why,
                     names=tuple(_name(registry, a) for a in ordered), ordered=ordered)


def answer_refusal(space_id: str, asker: str | None, tools: Iterable[str] = (), *,
                   caller: str | None = None) -> str | None:
    """Why *caller* (the account bound now when None) may not answer this
    card, or None when they may: ``not_yours`` (work that names no person
    answers nothing), or ``identity_unavailable`` when who answers cannot be
    read (never guessed). None always while the install does not enforce
    Spaces."""
    if not spaces_enforced():
        return None
    from kazma_core.spaces.identity import IdentityUnavailable

    person = caller or bound_asker()
    if not person:
        return NOT_YOURS
    try:
        found = _answerers(space_id, asker, tools)
    except IdentityUnavailable:
        return IDENTITY_UNAVAILABLE
    return None if person in found.accounts else NOT_YOURS


def card_fields(space_id: str, asker: str | None, tools: Iterable[str] = ()) -> dict[str, Any]:
    """What a card carries about who answers it, once Spaces is enforced:
    ``asker`` (whose card it is), ``answered_by`` (the accounts) and
    ``answered_by_names``. Empty while it is not enforced (the card is as
    before), and when the identity store cannot answer (the route asks
    again when the card is answered)."""
    if not spaces_enforced():
        return {}
    from kazma_core.spaces.identity import IdentityUnavailable

    out: dict[str, Any] = {"asker": str(asker or "")}
    try:
        found = _answerers(space_id, asker, tools)
    except IdentityUnavailable:
        return out
    out["answered_by"] = list(found.ordered)
    out["answered_by_names"] = list(found.names)
    return out


def standing_grant_refusal(person: str | None = None) -> str | None:
    """Why the person bound now may not give *person* (themself when None)
    a standing approval -- YOLO, ``/unrestricted``, "Allow tool", a task
    grant -- or None when they may (decision N4).

    A person whose cards the admin sent to approvers does not approve their
    own cards, so they cannot turn the cards off for themself either:
    ``approvers_decide``. An approver's grant for the card's asker, made from
    the card they answered, is theirs to make (the card was checked when it
    was answered): only a grant for oneself is asked about here.
    ``identity_unavailable`` when the identity store cannot answer (never a
    grant on a guess). None always while the install does not enforce
    Spaces. Blocking (the identity store)."""
    if not spaces_enforced():
        return None
    caller = bound_asker() or ""
    who = str(person or caller)
    if not who or who != caller:
        return None
    from kazma_core.spaces.identity import IdentityUnavailable, read_registry

    try:
        registry = read_registry()
    except IdentityUnavailable:
        return IDENTITY_UNAVAILABLE
    return APPROVERS_DECIDE if _approvers(registry, who) else None


def own_click_refusal(lang: str = "en") -> str | None:
    """Why the person bound now may not act on X with their own click in X
    Studio -- Post now, Delete (decision N4): the admin sent their cards to
    approvers, so a post of theirs needs an approver's yes on the chat's
    card, never their own click. The line names the approvers, in *lang*
    ("en" or "ar"). None when they may (no approvers; Spaces off). Raises
    ``identity.IdentityUnavailable`` when the store cannot be read. Blocking."""
    if not spaces_enforced():
        return None
    who = bound_asker() or ""
    if not who:
        return None
    from kazma_core.spaces.identity import read_registry

    registry = read_registry()
    approvers = _approvers(registry, who)
    if not approvers:
        return None
    names = "، ".join(_name(registry, a) for a in approvers) if lang == "ar" else ", ".join(
        _name(registry, a) for a in approvers)
    if lang == "ar":
        return (f"منشوراتك على X تحتاج موافقة {names}: اطلب من Kazma في المحادثة أن ينشر أو يحذف، "
                f"فيجيب {names} على البطاقة.")
    return (f"Your posts on X need {names}'s approval: ask Kazma in chat to post or delete it, "
            f"and {names} answers the card.")


def approvers_now(person: str | None = None) -> frozenset[str] | None:
    """The approvers of *person* (the person bound now when None), read when
    a standing approval is used (the S2-05 second review: YOLO or a grant
    made before the admin set approvers kept skipping the person's cards).
    None when no approvers apply (Spaces off, no person, none set): every
    standing approval holds. Otherwise only one an approver made holds; an
    identity store that cannot be read answers an empty set (none holds).
    Blocking."""
    if not spaces_enforced():
        return None
    who = str(person or bound_asker() or "")
    if not who:
        return None
    from kazma_core.spaces.identity import IdentityUnavailable, read_registry

    try:
        registry = read_registry()
    except IdentityUnavailable:
        return frozenset()
    found = _approvers(registry, who)
    return frozenset(found) if found else None


def made_by_a_person() -> str:
    """Who is making a standing approval now: the account bound, recorded on
    the grant (``granted_by``) so :func:`approvers_now` can tell an
    approver's from the person's own."""
    return bound_asker() or ""
