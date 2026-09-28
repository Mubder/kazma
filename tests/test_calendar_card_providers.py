"""Settings → Email: each calendar has its own state and its own sign-in.

The card was titled "Calendar (Google / Outlook)" but had one button,
"Connect Calendar", that only ever started Google's sign-in, and one badge
that read "Connected" when either calendar was. Asked to connect Outlook,
the owner pressed it and landed on Google (2026-09-28); Outlook's own state
(not connected) was shown nowhere.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"


def _card(src: str) -> str:
    start = src.index("<!-- Calendar (Google / Outlook)")
    end = src.index("<!-- Multi-account list -->", start)
    return src[start:end]


def _rows(card: str) -> list[str]:
    return re.split(r'<div class="calendar-provider-row"', card)[1:]


def test_each_calendar_has_its_own_row_badge_and_sign_in() -> None:
    rows = _rows(_card(TEMPLATE.read_text(encoding="utf-8")))
    assert len(rows) == 2, "one row per calendar"
    google, outlook = rows
    assert "calendar_google" in google and "connectCalendarOAuth()" in google
    assert "calendarStatus.google_connected ?" in google
    assert "calendar_outlook" in outlook and "connectMicrosoftOAuth()" in outlook
    assert "calendarStatus.outlook_connected ?" in outlook
    assert "connectCalendarOAuth()" not in outlook and "connectMicrosoftOAuth()" not in google


def test_the_google_button_says_google_in_both_languages() -> None:
    from kazma_ui.i18n import t

    assert "Google" in t("settings.calendar_connect_google", lang="en")
    assert "Google" in t("settings.calendar_connect_google", lang="ar")
    assert "Outlook" in t("settings.calendar_connect_outlook", lang="en")
    assert "Outlook" in t("settings.calendar_connect_outlook", lang="ar")


def test_negative_control_the_old_card_had_one_google_button_for_both() -> None:
    old = (
        '<!-- Calendar (Google / Outlook) -->'
        '<button @click="connectCalendarOAuth()">{{ t(\'settings.calendar_connect_google\') }}</button>'
        '<!-- Multi-account list -->'
    )
    assert _rows(_card(old)) == []
    assert "connectMicrosoftOAuth()" not in _card(old)
