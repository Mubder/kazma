"""A chat turn's token budget, and the optional daily cost limit.

The rule
--------
A chat turn may spend at most ``agent.turn_token_budget`` tokens: prompt
plus completion of every model call the supervisor makes for it (its
empty-answer nudge too). Unset is :data:`DEFAULT_TURN_TOKEN_BUDGET`
(500,000); ``0`` is no limit. Settings -> System sets it. A chat with
``/long`` active has none, as it has no wall-clock limit
(``agent/turn_budget.py``): a long task is the owner asking for more.

What happens
------------
At :data:`WARN_FRACTION` (70 %) the next model call carries a call-local
"SYSTEM BUDGET CHECK" note (a turn note kind, ``budget_check``): finish
with what you have. At the limit the turn stops before its next model call
with what it has, and the answer says the budget stopped it and how to go
on: a reply that continues ("continue", "كمل") grants the same turn half
its limit again past what it had used (:data:`CONTINUE_FACTOR`), its tokens
so far kept. Any other message starts a new turn with the whole budget.

Live 2026-11-04 a research turn read 1.9 million tokens of pages in one
turn and cost more than the rest of the day together; nothing in Kazma
could have said "enough".

The daily limit
---------------
``agent.daily_cost_limit_usd`` (unset or ``0``: none) is checked when a
turn starts, against what the per-call ledger recorded since the local
midnight (``cron.timezone``). At :data:`DAILY_ALERT_FRACTION` (80 %) one
ops alert a day; at the limit new turns are refused until midnight. With
the ledger switched off the day's spend is not measured: said once, never
a refusal.
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import UTC, datetime, time, tzinfo
from typing import Any

from kazma_core.db.pg_helpers import store_errors

logger = logging.getLogger(__name__)

__all__ = [
    "CONTINUE_FACTOR",
    "DAILY_ALERT_FRACTION",
    "DAILY_ALERT_STAMP",
    "DAILY_COST_LIMIT_KEY",
    "DEFAULT_TURN_TOKEN_BUDGET",
    "MAX_DAILY_COST_LIMIT_USD",
    "MAX_TURN_TOKEN_BUDGET",
    "TURN_TOKEN_BUDGET_KEY",
    "WARN_FRACTION",
    "add_call_tokens",
    "budget_note",
    "daily_cost_limit_setting",
    "daily_limit_check",
    "daily_refusal_text",
    "daily_spend",
    "effective_limit",
    "parse_daily_cost_limit",
    "parse_turn_token_budget",
    "resolve_turn_token_budget",
    "start_of_turn",
    "stop_text",
    "turn_token_budget_setting",
]

TURN_TOKEN_BUDGET_KEY = "agent.turn_token_budget"
DEFAULT_TURN_TOKEN_BUDGET = 500_000
MAX_TURN_TOKEN_BUDGET = 100_000_000
DAILY_COST_LIMIT_KEY = "agent.daily_cost_limit_usd"
MAX_DAILY_COST_LIMIT_USD = 1_000_000.0
#: A continued turn gets this many times the limit it stopped at.
CONTINUE_FACTOR = 1.5
WARN_FRACTION = 0.7
DAILY_ALERT_FRACTION = 0.8
#: The local date the 80 % alert was last raised (runtime state).
DAILY_ALERT_STAMP = "system.cost.daily_alerted"

_warned: set[str] = set()
_warned_lock = threading.Lock()


def _warn_once(key: str, message: str, *args: Any) -> None:
    with _warned_lock:
        if key in _warned:
            return
        _warned.add(key)
    logger.warning(message, *args)


# ── parsing ─────────────────────────────────────────────────────────────


def parse_turn_token_budget(value: Any) -> int | None:
    """A whole number of tokens from 0 to :data:`MAX_TURN_TOKEN_BUDGET`, or None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        tokens = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if 0 <= tokens <= MAX_TURN_TOKEN_BUDGET:
        return tokens
    return None


def parse_daily_cost_limit(value: Any) -> float | None:
    """US dollars from 0 to :data:`MAX_DAILY_COST_LIMIT_USD`, or None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        usd = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if math.isfinite(usd) and 0 <= usd <= MAX_DAILY_COST_LIMIT_USD:
        return round(usd, 4)
    return None


# ── the settings ────────────────────────────────────────────────────────


def _store(config_store: Any) -> Any:
    if config_store is None:
        from kazma_core.config_store import get_config_store

        config_store = get_config_store()
    return config_store


def turn_token_budget_setting(config_store: Any = None) -> dict[str, Any]:
    """``{"tokens", "source"}``: the configured budget (0 = none) and whether
    it is the owner's (``setting``) or the default. Blocking."""
    raw = _store(config_store).get(TURN_TOKEN_BUDGET_KEY)
    if raw is None or str(raw).strip() == "":
        return {"tokens": DEFAULT_TURN_TOKEN_BUDGET, "source": "default"}
    tokens = parse_turn_token_budget(raw)
    if tokens is None:
        _warn_once(
            f"budget:{raw}",
            "[turn-tokens] %s=%r is not a whole number from 0 to %d; the default %d applies",
            TURN_TOKEN_BUDGET_KEY, raw, MAX_TURN_TOKEN_BUDGET, DEFAULT_TURN_TOKEN_BUDGET,
        )
        return {"tokens": DEFAULT_TURN_TOKEN_BUDGET, "source": "default"}
    return {"tokens": tokens, "source": "setting"}


def daily_cost_limit_setting(config_store: Any = None) -> dict[str, Any]:
    """``{"usd", "source"}``: the daily limit (0 = none). Blocking."""
    raw = _store(config_store).get(DAILY_COST_LIMIT_KEY)
    if raw is None or str(raw).strip() == "":
        return {"usd": 0.0, "source": "default"}
    usd = parse_daily_cost_limit(raw)
    if usd is None:
        _warn_once(
            f"daily:{raw}",
            "[turn-tokens] %s=%r is not an amount from 0 to %d; no daily limit applies",
            DAILY_COST_LIMIT_KEY, raw, int(MAX_DAILY_COST_LIMIT_USD),
        )
        return {"usd": 0.0, "source": "default"}
    return {"usd": usd, "source": "setting"}


def resolve_turn_token_budget(thread_id: str | None = None) -> int | None:
    """This turn's base token budget, or None for no limit (module doc).

    Never raises: a settings store it cannot read leaves the default in
    force (a broken store is no reason to let a turn spend without bound).
    Blocking: async code calls it through ``asyncio.to_thread``.
    """
    try:
        if thread_id:
            from kazma_core.agent.long_task import long_task_status

            if long_task_status(str(thread_id)).get("active"):
                return None
        tokens = int(turn_token_budget_setting().get("tokens") or 0)
    except store_errors():  # an unreadable store must not stop a turn
        logger.warning(
            "[turn-tokens] the settings store could not be read; the default "
            "budget of %d tokens applies", DEFAULT_TURN_TOKEN_BUDGET, exc_info=True,
        )
        tokens = DEFAULT_TURN_TOKEN_BUDGET
    return tokens or None


# ── a turn's accounting ─────────────────────────────────────────────────


def start_of_turn(state: Any, user_text: str | None, base: int | None) -> dict[str, Any]:
    """The state patch for a new user turn.

    A reply that continues a turn the budget stopped keeps that turn's tokens
    and grants the "more" its stop message promised: the tokens it had used
    plus (:data:`CONTINUE_FACTOR` - 1) times the limit it had -- never less
    than :data:`CONTINUE_FACTOR` times that limit. Measured from the limit
    alone, a turn whose last call ran far past it (one call can read a whole
    page set) would stop again before a single call. Anything else starts at
    zero with the base budget.
    """
    from kazma_core.agent.long_task import is_continuation_reply

    text = str(user_text or "").strip()
    if base and state.get("turn_budget_stopped") and text and is_continuation_reply(text):
        had = int(state.get("turn_token_budget_granted") or 0) or int(base)
        used = int(state.get("turn_tokens") or 0)
        more = math.ceil(had * CONTINUE_FACTOR) - had
        granted = max(had + more, used + more)
        logger.info("[turn-tokens] continuing a stopped turn: %d of %d tokens used, %d granted",
                    used, had, granted)
        return {"turn_budget_stopped": False, "turn_token_budget_granted": granted}
    return {"turn_tokens": 0, "turn_budget_stopped": False, "turn_token_budget_granted": 0}


def effective_limit(state: Any, base: int | None) -> int | None:
    """The limit this turn runs under: a continuation's grant, else *base*."""
    if not base:
        return None
    return int(state.get("turn_token_budget_granted") or 0) or int(base)


def add_call_tokens(used: int, usage: Any) -> int:
    """*used* plus one model call's tokens (its total, else prompt + completion)."""
    if not isinstance(usage, dict):
        return int(used or 0)
    total = usage.get("total_tokens")
    try:
        spent = int(total) if total is not None else (
            int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
        )
    except (TypeError, ValueError):
        spent = 0
    return int(used or 0) + max(0, spent)


def budget_note(used: int, limit: int) -> dict[str, str]:
    """The call-local note at :data:`WARN_FRACTION` (a ``budget_check`` turn note)."""
    return {
        "role": "system",
        "content": (
            f"SYSTEM BUDGET CHECK: this turn has used {used:,} of its {limit:,} tokens. "
            "Finish now: answer the user with what you already have, say plainly what "
            "is left undone, and call no more tools unless one call completes the answer."
        ),
    }


def stop_text(user_text: str | None, used: int, limit: int) -> str:
    """The answer of a turn the budget stopped, in the user's language."""
    from kazma_core.language_lock import in_their_language

    more = math.ceil(limit * CONTINUE_FACTOR) - limit
    return in_their_language(
        user_text,
        (
            f"⚠️ I stopped before finishing: this turn used {used:,} tokens and its budget "
            f"is {limit:,} (Settings → System). The steps it took are kept in this chat. "
            f"Reply **continue** to give this turn {more:,} more tokens and pick up where "
            f"it stopped, or send a new request."
        ),
        (
            f"⚠️ توقفت قبل أن أنتهي: استهلك هذا الطلب {used:,} رمزًا وميزانيته {limit:,} "
            f"(الإعدادات ← النظام). الخطوات التي نفذها محفوظة في هذه المحادثة. اكتب **كمل** "
            f"لأمنحه {more:,} رمزًا إضافيًا وأكمل من حيث توقف، أو أرسل طلبًا جديدًا."
        ),
    )


# ── the daily limit ─────────────────────────────────────────────────────


def _local_midnight(zone: tzinfo) -> datetime:
    now = datetime.now(UTC).astimezone(zone)
    return datetime.combine(now.date(), time.min, tzinfo=zone)


def daily_spend() -> dict[str, Any]:
    """What the model calls cost since the local midnight. Blocking.

    ``measured`` is False when the per-call ledger is switched off or cannot
    be read: then nothing here may refuse a turn.
    """
    from kazma_core.agent.nonstop import get_nonstop_config
    from kazma_core.cron.scheduler import resolve_cron_zone
    from kazma_core.observability.llm_ledger import usage_since

    zone, zone_name = resolve_cron_zone()
    since = _local_midnight(zone)
    out: dict[str, Any] = {
        "measured": False, "cost": 0.0, "tokens": 0, "calls": 0,
        "since": since.isoformat(), "zone": zone_name, "date": since.date().isoformat(),
    }
    if not get_nonstop_config().ledger_enabled:
        out["reason"] = "ledger_off"
        return out
    usage = usage_since(since)
    if usage is None:
        out["reason"] = "ledger_unreadable"
        return out
    # ``unpriced``: calls with no known price (kazma_core.model_prices),
    # whose dollars are not in ``cost``: the limit counts only what is known.
    out.update(measured=True, cost=float(usage["cost"]), tokens=int(usage["tokens"]),
               calls=int(usage["calls"]), unpriced=int(usage.get("unpriced") or 0))
    return out


def _alert_once_today(day: str, spent: float, limit: float) -> None:
    """One ops alert per local day at :data:`DAILY_ALERT_FRACTION`."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    if str(store.get(DAILY_ALERT_STAMP) or "") == day:
        return
    store.set(DAILY_ALERT_STAMP, day)
    from kazma_core.observability.ops_alerts import alert

    over = spent >= limit
    alert(
        "cost.daily_limit",
        "Daily model cost limit reached" if over else "Daily model cost nearing its limit",
        (
            f"Model calls today cost ${spent:,.2f} of the ${limit:,.2f} daily limit "
            f"(Settings → System). "
            + ("New chat turns are refused until midnight." if over
               else "At the limit new chat turns are refused until midnight.")
        ),
        severity="error" if over else "warn",
    )


def daily_limit_check() -> dict[str, Any]:
    """The daily limit's verdict for a turn that is starting. Never raises.

    ``{"limit", "spent", "measured", "over"}``; ``over`` only when the spend
    was measured and reached the limit. Raises the 80 % alert once a day.
    Blocking: async code calls it through ``asyncio.to_thread``.
    """
    verdict: dict[str, Any] = {"limit": 0.0, "spent": None, "measured": False, "over": False}
    try:
        limit = float(daily_cost_limit_setting().get("usd") or 0.0)
        verdict["limit"] = limit
        if limit <= 0:
            return verdict
        spend = daily_spend()
        if not spend["measured"]:
            _warn_once(
                f"unmeasured:{spend.get('reason')}",
                "[turn-tokens] a daily cost limit of $%.2f is set, but today's spend is not "
                "measured (%s); no turn is refused for it", limit, spend.get("reason"),
            )
            return verdict
        spent = float(spend["cost"])
        unpriced = int(spend.get("unpriced") or 0)
        verdict.update(spent=spent, measured=True, over=spent >= limit, date=spend["date"],
                       unpriced=unpriced)
        if unpriced:
            _warn_once(
                f"unpriced:{spend['date']}",
                "[turn-tokens] %d model calls today have no known price and are not in the "
                "daily cost limit's $%.2f (Settings -> Providers & Connectors -> Models -> Model prices)",
                unpriced, spent,
            )
        if spent >= limit * DAILY_ALERT_FRACTION:
            _alert_once_today(str(spend["date"]), spent, limit)
    except store_errors():  # a store the check cannot read must not stop a turn
        logger.warning("[turn-tokens] the daily cost limit could not be checked", exc_info=True)
    return verdict


def daily_refusal_text(user_text: str | None, spent: float, limit: float) -> str:
    """A turn refused by the daily limit, in the user's language."""
    from kazma_core.language_lock import in_their_language

    return in_their_language(
        user_text,
        (
            f"⚠️ Today's model cost limit is reached: ${spent:,.2f} of ${limit:,.2f} "
            "(Settings → System). New requests resume after midnight, or raise the limit."
        ),
        (
            f"⚠️ بلغت تكلفة النموذج اليوم حدها: {spent:,.2f}$ من {limit:,.2f}$ "
            "(الإعدادات ← النظام). تُستأنف الطلبات بعد منتصف الليل، أو ارفع الحد."
        ),
    )
