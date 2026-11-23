"""A daily spending limit per person (Spaces S2-02, decision N8).

Friends use the owner's model keys. Each person but the admin has a daily
limit, counted from the per-call ledger (``llm_calls``: every row names the
person it was made for, ``asker``, S1-22):

* **tokens, always**: every model call counts, whatever its price -- a
  model of unknown price is covered by this one;
* **money, where the price is known**: dollars of the calls whose price is
  known (``0``: no money limit).

The day starts at the local midnight (``cron.timezone``), as the install's
own limit (``agent.daily_cost_limit_usd``, unchanged) does. At the limit the
person's turns stop until the next day, and they are told (N8): a turn that
starts once their spend has reached it is refused before any model call
(``graph_supervisor``), and so is a model call outside a turn made for them
-- a swarm worker's, research's (``resilient_chat``), X Studio's
(``x_api.model_selection.x_chat``). Every other model call is declared in
``tests/test_model_calls_checked.py`` with why. A turn that started
under the limit runs to its own end: its own token budget
(``agent.turn_token_budget``) bounds how far past.

Past the limit, on their own key
--------------------------------
A member's own model keys (S2-04) are not the install's, and what they
spend on them is not counted against the limit. So a turn that starts past
the limit is let in when the model it will use is one they hold a key for
(:func:`on_own_key`, asked by the supervisor for the client the turn's call
will go to), and every model call of the turn then goes on their own keys
(:func:`own_keys_only`): the turn's state carries ``own_keys_only``, every
node binds it (:func:`hold_to_own_keys`), and a call that would go out on
the install's key -- another provider for the respond synthesis, a router's
model, a failover -- is refused before it is sent
(``LLMProvider._person_call``, ``kind="daily_limit"``). A tool that pays
another API with the install's key keeps off it too
(``spaces.install_paid``); the remote embedder is the one accepted
exception. A call outside a turn (``resilient_chat``, ``x_chat``) stays
refused by :func:`call_refusal`.

Whose limit
-----------
* A person's own (``identity`` ``policies[account]["daily_limit"]``), set by
  the admin on the People page.
* Else the limit for people (``identity`` ``defaults["daily_limit"]``), set
  there too; with none set, :data:`SHIPPED_DEFAULT`.
* The admin has none unless one is set for them: the install's own limit
  covers the admin's spend.

Only where Spaces is enforced. An install with one account does no work
here: :func:`may_apply` is a check in memory, and no ledger is read for a
turn whose person has no limit.

Every function that reads the identity store or the ledger is blocking:
async code calls it through ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, bound_asker, spaces_enforced

__all__ = [
    "MAX_TOKENS",
    "MAX_USD",
    "SHIPPED_DEFAULT",
    "Limit",
    "LimitRefused",
    "call_refusal",
    "default_limit",
    "hold_to_own_keys",
    "limit_of",
    "may_apply",
    "on_own_key",
    "own_keys_only",
    "parse_limit",
    "precondition",
    "refusal_text",
    "today_start",
    "verdict",
]

logger = logging.getLogger(__name__)

#: True while the work running now is a turn that started past its person's
#: limit on their own key: only their own keys may be used. Set by the
#: supervisor and by :func:`hold_to_own_keys`; never cleared inside a turn.
_OWN_KEYS_ONLY: ContextVar[bool] = ContextVar("kazma_own_keys_only", default=False)

MAX_TOKENS = 10_000_000_000
MAX_USD = 1_000_000.0


@dataclass(frozen=True)
class Limit:
    """A day's limit: *tokens* always (above zero), *usd* where the price
    is known (``0``: no money limit)."""

    tokens: int
    usd: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"tokens": int(self.tokens), "usd": float(self.usd)}


#: The limit for people when the admin has set none (N8: both; the admin
#: raises it). Measured on the live install's ledger from 1 to 22 November
#: 2026 (read only): a turn used a median of 71,830 tokens (75th percentile
#: 80,676, 90th 189,117) -- the base prompt, the turn's notes and the
#: conversation are sent with every call. 3,000,000 tokens is about 40
#: ordinary turns a day. The model in use there has no price known to Kazma,
#: so the tokens are what binds; $1.00 binds a model whose price is known.
SHIPPED_DEFAULT = Limit(tokens=3_000_000, usd=1.0)


class LimitRefused(ValueError):
    """A limit that is not one; nothing was changed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def parse_limit(raw: Any) -> Limit | None:
    """A limit from ``{"tokens", "usd"}``; None for no limit of its own
    (``None``, or tokens empty). Raises :class:`LimitRefused`: tokens not a
    whole number from 1 to :data:`MAX_TOKENS`, dollars not from 0 to
    :data:`MAX_USD`."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LimitRefused("bad_limit", "A limit is a number of tokens and an amount.")
    tokens_raw = raw.get("tokens")
    if tokens_raw is None or str(tokens_raw).strip() == "":
        return None
    try:
        if isinstance(tokens_raw, bool):
            raise ValueError
        tokens = int(str(tokens_raw).strip())
    except (TypeError, ValueError):
        raise LimitRefused("bad_tokens", "Tokens a day is a whole number.") from None
    if not 1 <= tokens <= MAX_TOKENS:
        raise LimitRefused("bad_tokens", f"Tokens a day is from 1 to {MAX_TOKENS:,}.")
    usd_raw = raw.get("usd")
    usd = 0.0
    if usd_raw is not None and str(usd_raw).strip() != "":
        try:
            if isinstance(usd_raw, bool):
                raise ValueError
            usd = float(str(usd_raw).strip())
        except (TypeError, ValueError):
            raise LimitRefused("bad_usd", "Dollars a day is a number.") from None
        if not math.isfinite(usd) or not 0 <= usd <= MAX_USD:
            raise LimitRefused("bad_usd", f"Dollars a day is from 0 to {MAX_USD:,.0f}.")
    return Limit(tokens=tokens, usd=round(usd, 4))


def _stored(raw: Any) -> Limit | None:
    """A stored limit, or None when there is none or it is not one (said)."""
    try:
        return parse_limit(raw)
    except LimitRefused:
        logger.warning("[daily-limits] a stored limit is not one (%r); it is ignored", raw)
        return None


# ── Whose limit ─────────────────────────────────────────────────────────


def default_limit(registry: dict[str, Any]) -> Limit:
    """The limit for people who have none of their own."""
    stored = _stored((registry.get("defaults") or {}).get("daily_limit"))
    return stored or SHIPPED_DEFAULT


def limit_of(registry: dict[str, Any], account_id: str) -> tuple[Limit | None, str]:
    """``(limit, source)``: the person's own (``own``), else the limit for
    people (``default``); the admin with none of their own has none
    (``none``)."""
    policy = (registry.get("policies") or {}).get(str(account_id)) or {}
    own = _stored(policy.get("daily_limit")) if isinstance(policy, dict) else None
    if own is not None:
        return own, "own"
    if str(account_id) == OWNER_ACCOUNT_ID:
        return None, "none"
    return default_limit(registry), "default"


# ── The day ─────────────────────────────────────────────────────────────


def today_start() -> tuple[datetime, str]:
    """``(the local midnight, the zone's name)``: when today's spend starts,
    as for the install's own limit. Blocking (a settings read)."""
    from datetime import UTC, time

    from kazma_core.cron.scheduler import resolve_cron_zone

    zone, name = resolve_cron_zone()
    now = datetime.now(UTC).astimezone(zone)
    return datetime.combine(now.date(), time.min, tzinfo=zone), name


def _ledger_on() -> bool:
    from kazma_core.agent.nonstop import get_nonstop_config

    return bool(get_nonstop_config().ledger_enabled)


def may_apply() -> bool:
    """Whether a limit may apply to the work running now: Spaces is enforced
    and a person is bound. In memory, no read: an install with one account
    stops here."""
    return spaces_enforced() and bool(bound_asker())


def verdict(asker: str | None = None) -> dict[str, Any] | None:
    """The person's day: None when no limit applies (Spaces not enforced, no
    person bound, the admin with no limit of their own -- no ledger read
    then). Otherwise ``{"asker", "limit", "source", "tokens", "cost",
    "unpriced", "date", "measured", "over"}``. A spend that cannot be
    measured (the ledger off or unreadable) is ``over``: a person's limit is
    never taken as unspent. Blocking."""
    if not spaces_enforced():
        return None
    person = str(asker or bound_asker() or "")
    if not person:
        return None
    from kazma_core.spaces import identity

    try:
        limit, source = limit_of(identity.read_registry(), person)
    except identity.IdentityUnavailable:
        # Whose limit it is cannot be read: never taken as no limit.
        logger.warning("[daily-limits] the identity store cannot be read: %s's turn is refused", person)
        return {"asker": person, "limit": None, "source": "unknown", "tokens": 0, "cost": 0.0,
                "unpriced": 0, "date": "", "measured": False, "over": True,
                "reason": "identity_unavailable"}
    if limit is None:
        return None
    since, _zone = today_start()
    out: dict[str, Any] = {
        "asker": person, "limit": limit.as_dict(), "source": source, "tokens": 0, "cost": 0.0,
        "unpriced": 0, "date": since.date().isoformat(), "measured": False, "over": True,
    }
    if not _ledger_on():
        out["reason"] = "ledger_off"
        logger.warning("[daily-limits] the per-call ledger is off: %s's spend cannot be counted, "
                       "and their turns are refused until it is on", person)
        return out
    from kazma_core.observability.llm_ledger import usage_of

    used = usage_of(person, since)
    if used is None:
        out["reason"] = "ledger_unreadable"
        return out
    over = used["tokens"] >= limit.tokens or (limit.usd > 0 and used["cost"] >= limit.usd)
    out.update(tokens=int(used["tokens"]), cost=float(used["cost"]), unpriced=int(used["unpriced"]),
               measured=True, over=bool(over))
    if over:
        logger.info("[daily-limits] %s reached their daily limit (%d of %d tokens, $%.2f of $%.2f)",
                    person, used["tokens"], limit.tokens, used["cost"], limit.usd)
    return out


def refusal_text(user_text: str | None, found: dict[str, Any]) -> str:
    """The answer of a turn the person's limit refused, in their language."""
    from kazma_core.language_lock import in_their_language

    if not found.get("measured"):
        return in_their_language(
            user_text,
            "⚠️ Today's use cannot be counted right now, so your daily limit stops this request. "
            "Ask the admin, or try again later.",
            "⚠️ لا يمكن حساب استخدامك اليوم الآن، لذلك أوقف حدُّك اليومي هذا الطلب. "
            "اسأل المسؤول، أو حاول لاحقًا.",
        )
    limit = found["limit"]
    money_en = f" and ${float(found['cost']):,.2f} of ${float(limit['usd']):,.2f}" if limit["usd"] else ""
    money_ar = f" و{float(found['cost']):,.2f}$ من {float(limit['usd']):,.2f}$" if limit["usd"] else ""
    return in_their_language(
        user_text,
        (f"⚠️ You reached your daily limit: {int(found['tokens']):,} of {int(limit['tokens']):,} "
         f"tokens{money_en} today. Your requests resume after midnight, or the admin can raise it."),
        (f"⚠️ بلغت حدك اليومي: {int(found['tokens']):,} من {int(limit['tokens']):,} رمز"
         f"{money_ar} اليوم. تُستأنف طلباتك بعد منتصف الليل، أو يرفعه المسؤول."),
    )


def on_own_key(provider: str) -> bool:
    """Whether the person bound now holds a key of their own for *provider*:
    a turn past their limit whose call goes to it may start. A store that
    cannot say is no: the turn is refused, as at the limit. Blocking."""
    from kazma_core.spaces.own_keys import OwnKeyUnreadable, key_for_call

    try:
        return key_for_call(str(provider or "")) is not None
    except OwnKeyUnreadable:
        logger.warning("[daily-limits] whether %s has a key of their own cannot be read: "
                       "the turn past their limit is refused", bound_asker())
        return False


def own_keys_only() -> bool:
    """Whether only the person's own keys may be used now (a turn let in
    past their limit, :func:`on_own_key`). In memory."""
    return _OWN_KEYS_ONLY.get()


def hold_to_own_keys(state: Mapping[str, Any] | None) -> None:
    """Binds the turn's rule from its state (``own_keys_only``) for this
    node. LangGraph runs each node in a copy of its caller's context, so a
    value the supervisor set does not reach the next node: each node calls
    this first. Only ever turns the rule on -- a graph run inside such a
    turn (a sub-agent) keeps it whatever its own state says."""
    if state and bool(state.get("own_keys_only")):
        _OWN_KEYS_ONLY.set(True)


def call_refusal() -> str | None:
    """For a model call outside a turn (``resilient_chat``, ``x_chat``): the
    reason it is refused, or None. Blocking."""
    found = verdict()
    if not found or not found["over"]:
        return None
    if not found["measured"] or not found["limit"]:
        return "the daily limit of the person this work is for cannot be counted right now"
    return (f"the person this work is for reached their daily limit "
            f"({int(found['tokens']):,} of {int(found['limit']['tokens']):,} tokens today)")


# ── The precondition of a second account (S1-33) ────────────────────────


def precondition() -> tuple[bool, str]:
    """Whether the rule applies to every person: the ledger counts their
    spend, and every person but the admin has a limit (their own, else the
    limit for people). Raises when the identity store cannot be read
    (unknown is never passed). Blocking."""
    from kazma_core.spaces import identity

    if not _ledger_on():
        return False, ("the per-call ledger is off, so no person's spend is counted "
                       "(Settings: turn the per-call ledger on)")
    # Every account but the admin's resolves to a limit (limit_of): the read
    # proves the store answers and its stored limits are read as limits.
    base = default_limit(identity.read_registry())
    money = f" and ${base.usd:,.2f}" if base.usd else ""
    return True, (f"every person but the admin has a daily limit: their own, else "
                  f"{base.tokens:,} tokens{money} a day")
