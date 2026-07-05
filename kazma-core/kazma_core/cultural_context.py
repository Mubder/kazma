"""Cultural Context Engine — Hijri calendar, Islamic events, and conversational modifiers.

Tracks cultural context (Ramadan, Eid, National Day) and returns modifiers
that influence conversation pacing and tone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ── Cultural events ───────────────────────────────────────────────────


class CulturalEvent(Enum):
    """Recognized cultural events that affect conversation style."""

    NONE = "none"
    RAMADAN = "ramadan"
    EID_AL_FITR = "eid_al_fitr"
    EID_AL_ADHA = "eid_al_adha"
    NATIONAL_DAY = "national_day"
    ISRA_UN_MIRAJ = "isra_un_miraj"
    ISLAMIC_NEW_YEAR = "islamic_new_year"


# Kuwaiti National Day (fixed Gregorian date)
_NATIONAL_DAY = (2, 25)
_NATIONAL_DAY_LIBERATION = (2, 26)


# ── Data models ───────────────────────────────────────────────────────


@dataclass
class CulturalContextState:
    """Current cultural context for a conversation."""

    current_date: date
    is_ramadan: bool = False
    is_eid: bool = False
    is_eid_al_fitr: bool = False
    is_eid_al_adha: bool = False
    is_national_day: bool = False
    is_liberation_day: bool = False
    is_isra_un_miraj: bool = False
    is_islamic_new_year: bool = False
    active_events: list[CulturalEvent] = field(default_factory=list)

    @property
    def has_cultural_event(self) -> bool:
        """True if any cultural event is active."""
        return len(self.active_events) > 0

    @property
    def greeting_extension(self) -> int:
        """Additional greeting exchanges needed during cultural events.

        Normal: 0 extra. Ramadan/Eid: +2. National Day: +1.
        """
        ext = 0
        if self.is_ramadan:
            ext += 2
        if self.is_eid:
            ext += 2
        if self.is_national_day:
            ext += 1
        return ext


# ── Hijri date conversion (lightweight) ───────────────────────────────


def _gregorian_to_hijri_approx(g: date) -> tuple[int, int, int]:
    """Approximate Gregorian → Hijri conversion.

    Uses the Tabular Islamic calendar (Type II) via a 30-year cycle.
    Accurate to ±1 day for most dates. Good enough for event detection.

    The 30-year cycle has 19 common years (354 days) and 11 leap years
    (355 days). Months alternate 30/29 days; the 12th month gets an
    extra day in leap years.  Epoch: 1 Muharram 1 AH = 19 Jul 622 CE.
    """
    _EPOCH = date(622, 7, 19)
    _CYCLE_DAYS = 10631  # 19*354 + 11*355
    _LEAP_YEARS = frozenset({2, 5, 7, 10, 13, 16, 18, 21, 24, 26, 29})

    days = (g - _EPOCH).days
    if days < 0:
        raise ValueError("Date is before the Islamic epoch (19 Jul 622 CE)")

    # --- year ---
    cycle, pos = divmod(days, _CYCLE_DAYS)
    year_in_cycle = 0
    for y in range(30):
        year_days = 355 if y in _LEAP_YEARS else 354
        if pos < year_days:
            year_in_cycle = y
            break
        pos -= year_days
    hijri_year = cycle * 30 + year_in_cycle + 1  # 1-indexed

    # --- month / day ---
    is_leap = year_in_cycle in _LEAP_YEARS
    month = 1
    for m in range(12):
        month_len = (30 if is_leap else 29) if m == 11 else (30 if m % 2 == 0 else 29)
        if pos < month_len:
            month = m + 1
            break
        pos -= month_len

    return hijri_year, month, pos + 1


def _hijri_month_name(month: int) -> str:
    """Return the Arabic name of a Hijri month."""
    names = {
        1: "محرم",
        2: "صفر",
        3: "ربيع الأول",
        4: "ربيع الثاني",
        5: "جمادى الأولى",
        6: "جمادى الآخرة",
        7: "رجب",
        8: "شعبان",
        9: "رمضان",
        10: "شوال",
        11: "ذو القعدة",
        12: "ذو الحجة",
    }
    return names.get(month, "غير معروف")


# ── Ramadan detection ─────────────────────────────────────────────────


def _detect_ramadan_hijri(hijri_year: int, hijri_month: int) -> bool:
    """Check if current Hijri date falls in Ramadan (month 9)."""
    return hijri_month == 9


# ── Public API ────────────────────────────────────────────────────────


class CulturalContext:
    """Tracks cultural context for conversations.

    Detects Ramadan, Eid, National Day, and other events.
    Returns conversation modifiers based on active cultural context.
    """

    def __init__(self, now: date | None = None) -> None:
        self._now = now or date.today()
        self._state: CulturalContextState | None = None
        self.update_context()

    def update_context(self, now: date | None = None) -> None:
        """Update cultural context based on current date.

        Can be called with a specific date for testing.
        """
        if now is not None:
            self._now = now

        g = self._now
        hijri_year, hijri_month, hijri_day = _gregorian_to_hijri_approx(g)

        events: list[CulturalEvent] = []

        # Ramadan: Hijri month 9
        is_ramadan = _detect_ramadan_hijri(hijri_year, hijri_month)
        if is_ramadan:
            events.append(CulturalEvent.RAMADAN)

        # Eid al-Fitr: Hijri month 10, day 1-3
        is_eid_al_fitr = hijri_month == 10 and hijri_day <= 3
        if is_eid_al_fitr:
            events.append(CulturalEvent.EID_AL_FITR)

        # Eid al-Adha: Hijri month 12, day 10-13
        is_eid_al_adha = hijri_month == 12 and 10 <= hijri_day <= 13
        if is_eid_al_adha:
            events.append(CulturalEvent.EID_AL_ADHA)

        is_eid = is_eid_al_fitr or is_eid_al_adha

        # Kuwaiti National Day (Feb 25) and Liberation Day (Feb 26)
        is_national_day = g.month == 2 and g.day == 25
        is_liberation_day = g.month == 2 and g.day == 26
        if is_national_day:
            events.append(CulturalEvent.NATIONAL_DAY)

        # Isra and Miraj: Rajab 27 (Hijri month 7, day 27)
        is_isra = hijri_month == 7 and hijri_day == 27
        if is_isra:
            events.append(CulturalEvent.ISRA_UN_MIRAJ)

        # Islamic New Year: Muharram 1 (Hijri month 1, day 1)
        is_islamic_new_year = hijri_month == 1 and hijri_day == 1
        if is_islamic_new_year:
            events.append(CulturalEvent.ISLAMIC_NEW_YEAR)

        self._state = CulturalContextState(
            current_date=g,
            is_ramadan=is_ramadan,
            is_eid=is_eid,
            is_eid_al_fitr=is_eid_al_fitr,
            is_eid_al_adha=is_eid_al_adha,
            is_national_day=is_national_day,
            is_liberation_day=is_liberation_day,
            is_isra_un_miraj=is_isra,
            is_islamic_new_year=is_islamic_new_year,
            active_events=events,
        )

        logger.debug(
            "Cultural context updated: hijri=%d/%d/%d, events=%s",
            hijri_year,
            hijri_month,
            hijri_day,
            [e.value for e in events],
        )

    @property
    def state(self) -> CulturalContextState:
        """Return the current cultural context state."""
        if self._state is None:
            self.update_context()
        if self._state is None:  # update_context failed
            self._state = {}
        return self._state

    def get_hijri_date(self) -> tuple[int, int, int]:
        """Return (year, month, day) in Hijri calendar."""
        return _gregorian_to_hijri_approx(self._now)

    def get_hijri_date_str(self) -> str:
        """Return formatted Hijri date string."""
        y, m, d = self.get_hijri_date()
        return f"{d} {_hijri_month_name(m)} {y}"

    def get_conversation_modifiers(self) -> dict[str, Any]:
        """Return modifiers based on cultural context.

        These modifiers influence:
        - Greeting phase length
        - Tone and formality
        - Transaction delay
        - Response style
        """
        s = self.state
        modifiers: dict[str, Any] = {
            "greeting_extension": s.greeting_extension,
            "formality_boost": 0,
            "avoid_business_during_iftar": False,
            "celebratory_tone": False,
            "patriotic_references": False,
            "seasonal_greetings": [],
        }

        if s.is_ramadan:
            modifiers["formality_boost"] += 1
            modifiers["avoid_business_during_iftar"] = True
            modifiers["seasonal_greetings"].extend(
                [
                    "رمضان كريم",
                    "كل عام وأنتم بخير",
                ]
            )

        if s.is_eid_al_fitr:
            modifiers["celebratory_tone"] = True
            modifiers["formality_boost"] += 1
            modifiers["seasonal_greetings"].extend(
                [
                    "عيد مبارك",
                    "كل عام وأنتم بخير",
                    "عيد سعيد",
                ]
            )

        if s.is_eid_al_adha:
            modifiers["celebratory_tone"] = True
            modifiers["formality_boost"] += 1
            modifiers["seasonal_greetings"].extend(
                [
                    "عيد أضحى مبارك",
                    "تقبل الله منا ومنكم",
                ]
            )

        if s.is_national_day:
            modifiers["patriotic_references"] = True
            modifiers["celebratory_tone"] = True
            modifiers["seasonal_greetings"].extend(
                [
                    "يوم الكويت الوطني سعيد",
                    "عشت الكويت",
                ]
            )

        if s.is_isra_un_miraj:
            modifiers["formality_boost"] += 1
            modifiers["seasonal_greetings"].append("إسراء و معراج مبارك")

        if s.is_islamic_new_year:
            modifiers["formality_boost"] += 1
            modifiers["seasonal_greetings"].append("سنة هجرية جديدة سعيدة")

        return modifiers

    def get_greeting_suggestions(self) -> list[str]:
        """Return culturally appropriate greeting suggestions."""
        s = self.state
        greetings: list[str] = []

        if s.is_eid_al_fitr:
            greetings.extend(
                [
                    "عيد مبارك عليكم",
                    "كل عام وأنتم بخير",
                    "تقبل الله منا ومنكم",
                ]
            )
        elif s.is_eid_al_adha:
            greetings.extend(
                [
                    "عيد أضحى مبارك",
                    "تقبل الله منا ومنكم",
                ]
            )
        elif s.is_ramadan:
            greetings.extend(
                [
                    "رمضان كريم",
                    "صيام مقبول",
                ]
            )
        elif s.is_national_day:
            greetings.extend(
                [
                    "يوم الكويت الوطني سعيد",
                    "كل عام الكويت بخير",
                ]
            )
        else:
            greetings.extend(
                [
                    "السلام عليكم",
                    "هلا والله",
                    "شلونك",
                ]
            )

        return greetings

    def is_business_appropriate(self, current_hour: int | None = None) -> bool:
        """Check if it's appropriate to conduct business transactions.

        Returns False during iftar time (Maghrib ±1h) in Ramadan.

        Args:
            current_hour: Override hour (0-23, Kuwait local time UTC+3).
                If None, uses datetime.now().hour. Accepts override for testing.
        """
        s = self.state
        if not s.is_ramadan:
            return True

        # During Ramadan, iftar is around Maghrib time (approximately 6-7 PM in Kuwait)
        # We approximate: business is less appropriate between 5:30 PM and 7:30 PM
        if current_hour is None:
            now_utc = datetime.now(UTC)
            current_hour = (now_utc.hour + 3) % 24  # UTC+3 for Kuwait

        # Kuwait iftar is around 18:00-19:00 local time
        if 17 <= current_hour <= 19:
            return False

        return True
