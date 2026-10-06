"""Shipped defaults that changed, and whether installs already running follow.

``ConfigStore.reconcile_from_yaml`` copies merged shipped/local YAML into
the settings database the first time Kazma boots, and a stored value wins
from then on. So a default changed in a later release never reached an
install that already had the old one -- whether its owner had chosen that
value or never looked at it. Found 2026-09-29: the owner asked for fewer
restart messages; the default could change, and the live install would
still have sent all three, from the copy its first boot stored.

A changed default is declared here, once:

- ``RETIRED_DEFAULTS``: installs follow. At boot, a stored value equal to
  one the key used to ship with is replaced by today's -- once per entry
  (``RETIRED_APPLIED_KEY`` records it), so an owner who picks the old value
  again afterwards keeps it.
- ``NEW_INSTALLS_ONLY``: the new value is for new installs; a stored copy
  of the old one stays. Say why.

Retirements inspect pre-existing rows against raw shipped defaults before
missing keys are seeded. A deliberate fresh local override equal to an old
default is therefore kept; local overrides do not define product retirements.

``tests/test_shipped_config_defaults.py`` holds a snapshot of the values
``kazma.yaml`` ships and fails on a change declared in neither;
``scripts/shipped_defaults.py --write`` refreshes the snapshot once it is.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

__all__ = [
    "NEW_INSTALLS_ONLY",
    "RETIRED_APPLIED_KEY",
    "RETIRED_DEFAULTS",
    "RetiredDefault",
    "entry_id",
    "same_value",
]

#: The entries already applied to this install's settings (a sorted list of
#: ``entry_id``s). Internal.
RETIRED_APPLIED_KEY = "system.config.retired_defaults"


@dataclass(frozen=True)
class RetiredDefault:
    """A settings key whose shipped default changed; installs follow."""

    #: The settings key (dotted, as ConfigStore stores it).
    key: str
    #: Every value the key shipped with before (exact, as stored).
    old: tuple[Any, ...]
    #: The date of the change (YYYY-MM-DD); with ``key``, the entry's id.
    since: str
    #: Why it changed, for the log line and the reader.
    why: str


RETIRED_DEFAULTS: tuple[RetiredDefault, ...] = (
    RetiredDefault(
        key="notifications.lifecycle.events",
        old=(["starting", "started", "shutting_down", "startup_failed"],),
        since="2026-09-29",
        why=(
            "a reload sent three messages; one start card now says how long "
            "Kazma was down and whether each chat app connected"
        ),
    ),
    RetiredDefault(
        key="gateway.rate_limits.discord",
        old=(5,),
        since="2026-10-01",
        why=(
            "the inbound flood guard (messages per person per minute) shipped "
            "Discord's own send limit; 30, like Telegram"
        ),
    ),
    RetiredDefault(
        key="gateway.rate_limits.slack",
        old=(1,),
        since="2026-10-01",
        why=(
            "the inbound flood guard shipped Slack's own send limit: one Slack "
            "message a minute, the rest left unanswered; 30, like Telegram"
        ),
    ),
)

#: Settings keys whose new shipped value applies to new installs only: key
#: -> why a stored copy of the old value must stay.
NEW_INSTALLS_ONLY: dict[str, str] = {}


def entry_id(entry: RetiredDefault) -> str:
    return f"{entry.key}@{entry.since}"


def same_value(a: Any, b: Any) -> bool:
    """Equal as stored (JSON), so ``1`` and ``True`` stay different."""
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)
