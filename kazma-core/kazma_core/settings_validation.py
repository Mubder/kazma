"""What a setting may hold: one check for every way a value is written.

``PUT /api/settings/single``, ``PUT /api/settings`` and a settings restore
(:mod:`kazma_core.settings_restore`) all ask :func:`validate_setting`. The
checks lived in the single-setting route only, so the Settings page's batch
save and a restored backup could write what that route refuses: an unknown
server-status event switches that message off at the next boot without a
word, and a retention the sweep cannot read falls back to its default
(2026-10-01).

A rule normalizes the value or raises :class:`SettingRejected` with what the
setting may hold; the message is the caller's answer (a 400, or a line in the
restore preview).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

__all__ = ["SettingRejected", "base_key", "validate_setting"]


class SettingRejected(ValueError):
    """A value the setting cannot hold; the message says what it may hold."""


def base_key(key: str) -> str:
    """``tenant.<id>.<key>`` is ``<key>`` for its tenant
    (:func:`kazma_core.tenant_isolation.tenant_key` writes that shape)."""
    parts = key.split(".")
    if len(parts) > 2 and parts[0] == "tenant":
        return ".".join(parts[2:])
    return key


def _timezone(value: Any) -> Any:
    # A cron timezone the scheduler cannot resolve falls back to UTC with only
    # a warn-once, so a typo would silently become "always UTC".
    from zoneinfo import ZoneInfo

    name = str(value or "").strip()
    if not name:
        return ""  # an explicit clear: the UTC default
    try:
        ZoneInfo(name)
    except (ValueError, KeyError, OSError):  # ZoneInfoNotFoundError is a KeyError
        raise SettingRejected(
            f"Invalid timezone {name!r} — use an IANA name "
            "(e.g. 'Asia/Kuwait', 'Europe/London', 'UTC')."
        ) from None
    return name


def _task_retention(value: Any) -> Any:
    # Decides what the 15-minute sweep deletes: a value it cannot read is
    # refused here rather than quietly replaced by the default there.
    from kazma_core.swarm.task_store import MAX_TASK_RETENTION_DAYS, parse_task_retention_days

    days = parse_task_retention_days(value)
    if days is None:
        raise SettingRejected(
            "Swarm task retention must be a whole number of days "
            f"from 0 (keep every task) to {MAX_TASK_RETENTION_DAYS}."
        )
    return days


def _checkpoint_retention(value: Any) -> Any:
    from kazma_core.checkpoint_retention import MAX_RETENTION_DAYS, parse_retention_days

    days = parse_retention_days(value)
    if days is None:
        raise SettingRejected(
            "Step history retention must be a whole number of days "
            f"from 0 (keep every step) to {MAX_RETENTION_DAYS}."
        )
    return days


def _turn_timeout(value: Any) -> Any:
    # The limit every surface applies to a running turn (agent/turn_budget.py);
    # a value it cannot read would be ignored there with only a warning.
    from kazma_core.agent.turn_budget import MAX_TURN_TIMEOUT_SECONDS, parse_turn_timeout

    seconds = parse_turn_timeout(value)
    if seconds is None:
        raise SettingRejected(
            "The turn time limit is a whole number of seconds "
            f"from 0 (no limit) to {MAX_TURN_TIMEOUT_SECONDS}."
        )
    return seconds


def _turn_token_budget(value: Any) -> Any:
    # What a chat turn may spend (agent/turn_token_budget.py); a value it
    # cannot read would leave the default in force with only a warning.
    from kazma_core.agent.turn_token_budget import MAX_TURN_TOKEN_BUDGET, parse_turn_token_budget

    tokens = parse_turn_token_budget(value)
    if tokens is None:
        raise SettingRejected(
            "The turn token budget is a whole number of tokens "
            f"from 0 (no limit) to {MAX_TURN_TOKEN_BUDGET}."
        )
    return tokens


def _daily_cost_limit(value: Any) -> Any:
    from kazma_core.agent.turn_token_budget import MAX_DAILY_COST_LIMIT_USD, parse_daily_cost_limit

    usd = parse_daily_cost_limit(value)
    if usd is None:
        raise SettingRejected(
            "The daily cost limit is an amount in US dollars "
            f"from 0 (no limit) to {int(MAX_DAILY_COST_LIMIT_USD)}."
        )
    return usd


#: How many models the owner may price (a list a person keeps by hand).
MAX_OWNER_PRICES = 500


def _model_details(value: Any) -> dict[str, dict[str, Any]]:
    """``models.details``: the owner's details for a model
    (kazma_core.model_details, providers stage 3): a context window and an
    output limit in tokens (1,024 to 100 million), whether it sees images,
    whether it uses tools. Refused whole, never saved with an entry
    dropped."""
    import json

    from kazma_core.model_details import parse_overrides

    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else {}
        except ValueError as exc:
            raise SettingRejected("Model details must be a list of models and what is known about each.") from exc
    if not isinstance(value, dict):
        raise SettingRejected("Model details must be a list of models and what is known about each.")
    if len(value) > MAX_OWNER_PRICES:
        raise SettingRejected(f"At most {MAX_OWNER_PRICES} models can have details.")
    parsed = parse_overrides(value)
    out: dict[str, dict[str, Any]] = {}
    for name, entry in value.items():
        key = str(name or "").strip().lower()
        given = {k for k, v in (entry or {}).items() if v not in (None, "")} if isinstance(entry, dict) else {None}
        kept = set((parsed.get(key) or {}).keys())
        unknown = given - {"context_window", "max_output", "vision", "tools"}
        too_small = any(
            isinstance((entry or {}).get(f), (int, float)) and not isinstance((entry or {}).get(f), bool)
            and 0 < float(entry[f]) < 1024 for f in ("context_window", "max_output"))
        if not key or len(key) > 200 or unknown or given != kept or too_small:
            raise SettingRejected(
                "Each model needs a name and only these, each valid: a context window and an output limit "
                f"(1,024 to 100,000,000 tokens), vision and tools (true or false) ({str(name)[:80]!r} has not)."
            )
        out[key] = parsed[key]
    return out


def _model_prices(value: Any) -> dict[str, dict[str, float]]:
    """``models.prices``: the owner's price per million tokens for a model
    (kazma_core.model_prices). Every entry must be a model name and two
    amounts in range: refused whole, never saved with an entry dropped."""
    import json

    from kazma_core.model_prices import MAX_PRICE_PER_1M, parse_owner_prices

    if isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else {}
        except ValueError as exc:
            raise SettingRejected("Model prices must be a list of models and their prices.") from exc
    if not isinstance(value, dict):
        raise SettingRejected("Model prices must be a list of models and their prices.")
    if len(value) > MAX_OWNER_PRICES:
        raise SettingRejected(f"At most {MAX_OWNER_PRICES} models can be priced.")
    parsed = parse_owner_prices(value)
    out: dict[str, dict[str, float]] = {}
    for name in value:
        key = str(name or "").strip().lower()
        if not key or len(key) > 200 or key not in parsed:
            raise SettingRejected(
                f"Each model needs a name and two prices from 0 to {int(MAX_PRICE_PER_1M)} US dollars "
                f"per million tokens ({str(name)[:80]!r} has not)."
            )
        price = parsed[key]
        out[key] = {"input": price.input_per_1m, "output": price.output_per_1m}
    return out


def _lifecycle_events(value: Any) -> Any:
    # Only events Kazma sends: a name it does not know would switch that
    # message off at the next boot without a word.
    from kazma_core.lifecycle_notifier import EVENT_NAMES, parse_lifecycle_events

    known, unknown = parse_lifecycle_events(value)
    if unknown or not isinstance(value, (list, str)):
        raise SettingRejected(
            "Server status messages are a list of: "
            f"{', '.join(EVENT_NAMES)}"
            + (f" (not {', '.join(unknown)})." if unknown else ".")
        )
    return known


def _cron_bound(key: str) -> Callable[[Any], Any]:
    """A whole number in the scheduler setting's range (kazma_core.cron.settings)."""
    def normalize(value: Any) -> Any:
        from kazma_core.cron.settings import SETTING_BOUNDS

        _default, low, high = SETTING_BOUNDS[key]
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            number = None
        if number is None or not low <= number <= high:
            raise SettingRejected(f"{key} is a whole number from {low} to {high}.")
        return number

    return normalize


#: Where Kazma's alerts go (``ops_alerts._ops_channels``; Settings -> Alerts).
ALERT_ROUTES = ("telegram", "telegram-group", "discord", "slack")


def _alert_routes(value: Any) -> Any:
    # A name no route answers to would route alerts nowhere without a word.
    # Kept as written (a comma list or a list): ops_alerts reads both.
    if isinstance(value, str):
        names = [p.strip().lower() for p in value.split(",") if p.strip()]
    elif isinstance(value, (list, tuple)):
        names = [str(p).strip().lower() for p in value if str(p).strip()]
    else:
        raise SettingRejected("Alert routes are a list of: " + ", ".join(ALERT_ROUTES) + ".")
    unknown = [n for n in names if n not in ALERT_ROUTES]
    if unknown:
        raise SettingRejected("Alert routes are a list of: " + ", ".join(ALERT_ROUTES)
                              + f" (not {', '.join(unknown)}).")
    names = list(dict.fromkeys(names))
    return ",".join(names) if isinstance(value, str) else names


def _telegram_chat(value: Any) -> Any:
    # The guard's pager (Settings -> Alerts): a chat id (a group's starts -100)
    # or a public @name. A typo here is a pager that pages nobody.
    import re

    text = str(value if value is not None else "").strip()
    if not text:
        return ""  # unset: the guard falls back to swarm.group_chat_id
    if re.fullmatch(r"-?\d{1,20}", text) or re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{4,31}", text):
        return text
    raise SettingRejected("A Telegram chat is a number (like 123456789, or -100... for a group) "
                          "or a public @name.")


def _email_address(value: Any) -> Any:
    # Kazma Watch's email recipient (Settings -> Alerts, sent to the observer).
    from kazma_core.observability.outside_watch import valid_email

    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    if not valid_email(text):
        raise SettingRejected("An email address looks like name@example.com.")
    return text


def _strict_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "on", "yes"):
        return True
    if text in ("false", "0", "off", "no", ""):
        return False
    raise SettingRejected("This setting is on or off (true or false).")


def _tenant_mode(value: Any) -> Any:
    # Under enforcement memory belongs to each principal's tenant whatever the
    # mode; a per-platform or per-user mode there split one user's memory
    # between the page and the turns (Memory audit M07). Refused, until one
    # resolver serves both for those modes.
    from kazma_core.memory.config import memory_tenant_enforced

    mode = str(value or "").strip().lower()
    if mode not in ("shared", "per_platform", "per_user"):
        raise SettingRejected("Memory isolation is one of: shared, per_platform, per_user.")
    if mode != "shared" and memory_tenant_enforced():
        raise SettingRejected(
            "With tenants enforced (production or several users), memory is already kept per "
            "tenant: only 'shared' is available here.")
    return mode


def _ocr_engine(value: Any) -> Any:
    # An engine the document policy cannot read would make every document
    # job refuse to run (an invalid stored policy fails closed).
    from kazma_core.documents.config import OCR_ENGINES, ocr_engine_choice

    try:
        return ocr_engine_choice(value)
    except ValueError:
        raise SettingRejected(f"The OCR engine is one of: {', '.join(OCR_ENGINES)}.") from None


def _embedding_key_env(value: Any) -> str:
    """A variable's name, and not one of Kazma's own (FA-126): the embedder
    sends that variable's value to the embedding endpoint."""
    from kazma_core.memory.embedder import API_KEY_ENV_KEY, embedding_key_env_allowed

    name = str(value or "").strip()
    if name and not embedding_key_env_allowed(name):
        raise SettingRejected(
            f"{API_KEY_ENV_KEY} must name the variable that holds the embedding key, "
            "not one of Kazma's own KAZMA_ variables."
        )
    return name


#: The languages Kazma's prompts and interface catalog are written in
#: (``kazma_ui.i18n.SUPPORTED_LANGUAGES``, held equal by a test).
AGENT_LANGUAGES = frozenset({"ar", "en"})


def _agent_language(value: Any) -> str:
    """``agent.language``: a language Kazma speaks (2026-11, FA-251: "xx" was
    stored, Settings showed it, and the prompt fell back to English)."""
    text = str(value or "").strip().lower()
    if text not in AGENT_LANGUAGES:
        raise SettingRejected(f"agent.language must be one of {sorted(AGENT_LANGUAGES)}")
    return text


def _chat_reasoning(value: Any) -> str:
    """``agent.chat_reasoning``: one of the levels the chat's model calls send
    (``agent/chat_reasoning.LEVELS``; 2026-11, roadmap theme 7)."""
    from kazma_core.agent.chat_reasoning import LEVELS, parse_level

    level = parse_level(value)
    if level is None:
        raise SettingRejected(f"agent.chat_reasoning must be one of {list(LEVELS)}")
    return level


def _max_iterations(value: Any) -> int:
    """``agent.max_iterations``: the range the runtime clamps to (FA-251:
    -7 and 999999 were stored, and the turn silently used 5 or 100)."""
    from kazma_core.agent.long_task import _MAX_ITER, _MIN_ITER

    number = _shipped_type_value("agent.max_iterations", value, int)
    if not _MIN_ITER <= number <= _MAX_ITER:
        raise SettingRejected(f"agent.max_iterations must be {_MIN_ITER}-{_MAX_ITER}")
    return number


def _offsite_keep(value: Any) -> int:
    """``backups.offsite.keep``: a whole number, 0 keeps every offsite copy.

    Refused rather than read as the default (the reader's forgiving rule,
    :func:`kazma_core.backup.universal.offsite_keep`, is for a row already
    stored): a form that sends -1 or "seven" must hear it was not saved."""
    from kazma_core.backup.universal import OFFSITE_KEEP_KEY

    number = _shipped_type_value(OFFSITE_KEEP_KEY, value, int)
    if number < 0:
        raise SettingRejected(f"{OFFSITE_KEEP_KEY} must be 0 (keep every copy) or more")
    return number


def shipped_values() -> dict[str, Any]:
    """Every setting ``kazma.yaml`` ships, dotted key -> value (blocking: a
    parse cached by the files' version)."""
    from kazma_core.config_loader import install_yaml_section
    from kazma_core.config_store import shipped_settings

    return {key: value for key, value, _category in shipped_settings(install_yaml_section())}


_TRUE = frozenset({"true", "1", "yes", "on"})
_FALSE = frozenset({"false", "0", "no", "off"})


def _shipped_type_value(key: str, value: Any, kind: type) -> Any:
    """*value* as the type ``kazma.yaml`` ships *key* with: a switch, a whole
    number or a number, from the value itself or its text (a form sends
    "30"). Anything else is refused (FA-251: a key with no rule took any
    value on every path)."""
    if kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        text = str(value).strip().lower() if isinstance(value, str) else None
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        raise SettingRejected(f"{key} is a switch: true or false")
    if isinstance(value, bool):
        raise SettingRejected(f"{key} is a number, not a switch")
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip()):
        try:
            number = float(str(value).strip()) if isinstance(value, str) else float(value)
        except ValueError:
            raise SettingRejected(f"{key} is a number") from None
        if kind is int:
            if not number.is_integer():
                raise SettingRejected(f"{key} is a whole number")
            return int(number)
        return number
    raise SettingRejected(f"{key} is a number")


def _shipped_type_rule(name: str) -> Callable[[Any], Any] | None:
    """The type rule of a number or switch ``kazma.yaml`` ships, or None."""
    shipped = shipped_values().get(name)
    kind = (bool if isinstance(shipped, bool) else int if isinstance(shipped, int)
            else float if isinstance(shipped, float) else None)
    if kind is None:
        return None

    def check(value: Any) -> Any:
        if value is None or value == "":
            return value  # unset: the runtime's default
        return _shipped_type_value(name, value, kind)

    return check


@functools.cache
def _rules() -> dict[str, tuple[Callable[[Any], Any], str | None]]:
    """key -> (normalize, category the key is stored under or None).

    Built on first use: the key names are the owning modules' own constants,
    and importing those modules here at import time would load the swarm
    package for every settings read."""
    from kazma_core.agent.chat_reasoning import CHAT_REASONING_KEY
    from kazma_core.agent.turn_budget import TURN_TIMEOUT_KEY
    from kazma_core.agent.turn_token_budget import DAILY_COST_LIMIT_KEY, TURN_TOKEN_BUDGET_KEY
    from kazma_core.backup.universal import OFFSITE_KEEP_KEY
    from kazma_core.checkpoint_retention import RETENTION_KEY
    from kazma_core.cron.settings import FIRE_WITHOUT_HITL_KEY, SETTING_BOUNDS
    from kazma_core.documents.config import OCR_ENGINE_KEY
    from kazma_core.lifecycle_notifier import EVENTS_KEY
    from kazma_core.memory.config import TENANT_MODE_KEY
    from kazma_core.memory.embedder import API_KEY_ENV_KEY
    from kazma_core.model_details import OVERRIDES_KEY as MODEL_DETAILS_KEY
    from kazma_core.model_prices import OWNER_PRICES_KEY
    from kazma_core.observability.ops_alerts import NOTICES_TO_CHATS_KEY
    from kazma_core.observability.outside_watch import EMAIL_TO_KEY
    from kazma_core.runs.runbooks import RUNBOOKS_KEY, validate_runbooks
    from kazma_core.swarm.task_store import TASK_RETENTION_KEY

    return {
        API_KEY_ENV_KEY: (_embedding_key_env, "embedding"),
        OCR_ENGINE_KEY: (_ocr_engine, "documents"),
        "cron.timezone": (_timezone, None),
        # The scheduler's bounds (Scheduled audit SCH-E06, E14, S05).
        **{key: (_cron_bound(key), None) for key in SETTING_BOUNDS},
        FIRE_WITHOUT_HITL_KEY: (_strict_bool, None),
        TENANT_MODE_KEY: (_tenant_mode, "memory"),
        TASK_RETENTION_KEY: (_task_retention, "swarm"),
        RETENTION_KEY: (_checkpoint_retention, "system"),
        TURN_TIMEOUT_KEY: (_turn_timeout, "agent"),
        TURN_TOKEN_BUDGET_KEY: (_turn_token_budget, "agent"),
        DAILY_COST_LIMIT_KEY: (_daily_cost_limit, "agent"),
        CHAT_REASONING_KEY: (_chat_reasoning, "agent"),
        EVENTS_KEY: (_lifecycle_events, "notifications"),
        # Settings -> Alerts (the unified alerts plan, Stage 3).
        "notifications.ops.channels": (_alert_routes, "notifications"),
        NOTICES_TO_CHATS_KEY: (_strict_bool, "notifications"),
        EMAIL_TO_KEY: (_email_address, "notifications"),
        "guard.telegram.chat_id": (_telegram_chat, "guard"),
        RUNBOOKS_KEY: (validate_runbooks, None),
        "agent.language": (_agent_language, "agent"),
        "agent.max_iterations": (_max_iterations, "agent"),
        OWNER_PRICES_KEY: (_model_prices, "models"),
        MODEL_DETAILS_KEY: (_model_details, "models"),
        OFFSITE_KEEP_KEY: (_offsite_keep, "backups"),
    }


def validate_setting(key: str, value: Any) -> tuple[Any, str | None]:
    """``(value to store, category to store it under or None)``.

    Raises :class:`SettingRejected` for a value the setting cannot hold. A
    key with no rule passes unchanged."""
    from kazma_core.safety.deployment_policy import (
        CONTAINER_REQUIRED_KEY,
        DOCUMENT_WORKER_ISOLATION_KEY,
        WORKSPACE_ROOTS_KEY,
        normalize_container_required,
        normalize_document_worker_isolation,
        normalize_workspace_roots,
    )

    name = base_key(key)
    try:
        if name == WORKSPACE_ROOTS_KEY:
            return normalize_workspace_roots(value), "security"
        if name == CONTAINER_REQUIRED_KEY:
            return normalize_container_required(value), "security"
        if name == DOCUMENT_WORKER_ISOLATION_KEY:
            return normalize_document_worker_isolation(value), "security"
        # A parent mapping save or backup restore must enforce the same rules.
        if name in ("security", "security.execution") and isinstance(value, dict):
            return {child: validate_setting(name + "." + child, item)[0]
                    for child, item in value.items()}, "security"
        if name == "memory" and isinstance(value, dict) and "tenant_mode" in value:
            return {**value, "tenant_mode": _tenant_mode(value["tenant_mode"])}, "memory"
        if name == "documents.ocr" and isinstance(value, dict) and "engine" in value:
            return {**value, "engine": _ocr_engine(value["engine"])}, "documents"
        if (name == "documents" and isinstance(value, dict) and isinstance(value.get("ocr"), dict)
                and "engine" in value["ocr"]):
            ocr = {**value["ocr"], "engine": _ocr_engine(value["ocr"]["engine"])}
            return {**value, "ocr": ocr}, "documents"
    except ValueError as exc:
        raise SettingRejected(str(exc)) from None
    rule = _rules().get(name)
    if rule is None:
        typed = _shipped_type_rule(name)
        return (value if typed is None else typed(value)), None
    normalize, category = rule
    return normalize(value), category
