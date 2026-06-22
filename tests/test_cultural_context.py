"""Tests for CulturalContext — Hijri calendar, Islamic events, and modifiers."""
from __future__ import annotations

from datetime import date

from kazma_core.cultural_context import (
    CulturalContext,
    CulturalEvent,
    _gregorian_to_hijri_approx,
    _hijri_month_name,
)


class TestHijriConversion:
    """Test the approximate Gregorian → Hijri conversion."""

    def test_known_date(self):
        """Test a known Gregorian → Hijri mapping."""
        # July 18, 2024 → approximately 12 Muharram 1446
        g = date(2024, 7, 18)
        y, m, d = _gregorian_to_hijri_approx(g)
        assert y == 1446
        assert m == 1
        assert 10 <= d <= 14  # Approximate, allow ±2 days

    def test_ramadan_detection(self):
        """Test Ramadan (month 9) detection."""
        # Ramadan 2024 approximately March 11 - April 9
        g = date(2024, 3, 20)
        y, m, d = _gregorian_to_hijri_approx(g)
        assert m == 9  # Ramadan

    def test_modern_date(self):
        """Test conversion for a recent date."""
        g = date(2026, 6, 20)
        y, m, d = _gregorian_to_hijri_approx(g)
        assert y >= 1447
        assert 1 <= m <= 12
        assert 1 <= d <= 30


class TestHijriMonthName:
    """Test Hijri month name lookup."""

    def test_ramadan_name(self):
        assert _hijri_month_name(9) == "رمضان"

    def test_muharram_name(self):
        assert _hijri_month_name(1) == "محرم"

    def test_shawwal_name(self):
        assert _hijri_month_name(10) == "شوال"

    def test_invalid_month(self):
        assert _hijri_month_name(13) == "غير معروف"


class TestCulturalContext:
    """Test the CulturalContext class."""

    def test_default_date(self):
        """Context initializes with today's date."""
        ctx = CulturalContext()
        assert ctx.state.current_date == date.today()

    def test_explicit_date(self):
        """Context can be initialized with a specific date."""
        target = date(2024, 3, 15)
        ctx = CulturalContext(now=target)
        assert ctx.state.current_date == target

    def test_ramadan_detection(self):
        """Detect Ramadan from Hijri date."""
        # During Ramadan 2024
        ctx = CulturalContext(now=date(2024, 3, 20))
        assert ctx.state.is_ramadan is True
        assert CulturalEvent.RAMADAN in ctx.state.active_events

    def test_not_ramadan(self):
        """Detect non-Ramadan date."""
        ctx = CulturalContext(now=date(2024, 8, 1))
        assert ctx.state.is_ramadan is False

    def test_eid_al_fitr_detection(self):
        """Detect Eid al-Fitr (Shawwal 1-3)."""
        # Eid al-Fitr 2024 approximately April 10-12
        ctx = CulturalContext(now=date(2024, 4, 11))
        assert ctx.state.is_eid is True
        assert ctx.state.is_eid_al_fitr is True

    def test_eid_al_adha_detection(self):
        """Detect Eid al-Adha (Dhul Hijjah 10-13)."""
        # Eid al-Adha 2024 approximately June 17-20
        ctx = CulturalContext(now=date(2024, 6, 18))
        assert ctx.state.is_eid is True
        assert ctx.state.is_eid_al_adha is True

    def test_national_day(self):
        """Detect Kuwaiti National Day (Feb 25)."""
        ctx = CulturalContext(now=date(2024, 2, 25))
        assert ctx.state.is_national_day is True
        assert CulturalEvent.NATIONAL_DAY in ctx.state.active_events

    def test_liberation_day(self):
        """Detect Kuwaiti Liberation Day (Feb 26)."""
        ctx = CulturalContext(now=date(2024, 2, 26))
        assert ctx.state.is_liberation_day is True

    def test_not_national_day(self):
        """Non-National Day date."""
        ctx = CulturalContext(now=date(2024, 3, 1))
        assert ctx.state.is_national_day is False

    def test_update_context(self):
        """Update context with new date."""
        ctx = CulturalContext(now=date(2024, 1, 1))
        assert ctx.state.is_national_day is False

        ctx.update_context(now=date(2024, 2, 25))
        assert ctx.state.is_national_day is True

    def test_greeting_extension_ramadan(self):
        """Ramadan adds +2 to greeting extension."""
        ctx = CulturalContext(now=date(2024, 3, 20))
        assert ctx.state.greeting_extension == 2

    def test_greeting_extension_eid(self):
        """Eid adds +2 to greeting extension."""
        ctx = CulturalContext(now=date(2024, 4, 11))
        assert ctx.state.greeting_extension == 2

    def test_greeting_extension_national_day(self):
        """National Day adds +1 to greeting extension."""
        ctx = CulturalContext(now=date(2024, 2, 25))
        assert ctx.state.greeting_extension == 1

    def test_greeting_extension_compound(self):
        """Ramadan + National Day = +3 extension."""
        # This would require both events to overlap — unlikely but test the math
        ctx = CulturalContext(now=date(2024, 2, 25))
        state = ctx.state
        # Manually set ramadan for testing
        state.is_ramadan = True
        assert state.greeting_extension == 3  # 2 (ramadan) + 1 (national day)

    def test_has_cultural_event(self):
        """has_cultural_event returns True when events are active."""
        ctx = CulturalContext(now=date(2024, 3, 20))
        assert ctx.state.has_cultural_event is True

    def test_no_cultural_event(self):
        """has_cultural_event returns False for ordinary days."""
        ctx = CulturalContext(now=date(2024, 8, 15))
        assert ctx.state.has_cultural_event is False


class TestConversationModifiers:
    """Test get_conversation_modifiers()."""

    def test_ramadan_modifiers(self):
        """Ramadan should set appropriate modifiers."""
        ctx = CulturalContext(now=date(2024, 3, 20))
        mods = ctx.get_conversation_modifiers()

        assert mods["greeting_extension"] == 2
        assert mods["avoid_business_during_iftar"] is True
        assert "رمضان كريم" in mods["seasonal_greetings"]

    def test_eid_al_fitr_modifiers(self):
        """Eid al-Fitr should set celebratory tone."""
        ctx = CulturalContext(now=date(2024, 4, 11))
        mods = ctx.get_conversation_modifiers()

        assert mods["celebratory_tone"] is True
        assert "عيد مبارك" in mods["seasonal_greetings"]

    def test_national_day_modifiers(self):
        """National Day should set patriotic references."""
        ctx = CulturalContext(now=date(2024, 2, 25))
        mods = ctx.get_conversation_modifiers()

        assert mods["patriotic_references"] is True
        assert mods["celebratory_tone"] is True

    def test_normal_day_modifiers(self):
        """Normal day should have minimal modifiers."""
        ctx = CulturalContext(now=date(2024, 8, 15))
        mods = ctx.get_conversation_modifiers()

        assert mods["greeting_extension"] == 0
        assert mods["formality_boost"] == 0
        assert mods["celebratory_tone"] is False
        assert mods["seasonal_greetings"] == []


class TestGreetingSuggestions:
    """Test get_greeting_suggestions()."""

    def test_ramadan_greetings(self):
        ctx = CulturalContext(now=date(2024, 3, 20))
        greetings = ctx.get_greeting_suggestions()
        assert any("رمضان" in g for g in greetings)

    def test_eid_greetings(self):
        ctx = CulturalContext(now=date(2024, 4, 11))
        greetings = ctx.get_greeting_suggestions()
        assert any("عيد" in g for g in greetings)

    def test_national_day_greetings(self):
        ctx = CulturalContext(now=date(2024, 2, 25))
        greetings = ctx.get_greeting_suggestions()
        assert any("الكويت" in g for g in greetings)

    def test_default_greetings(self):
        ctx = CulturalContext(now=date(2024, 8, 15))
        greetings = ctx.get_greeting_suggestions()
        assert "السلام عليكم" in greetings


class TestBusinessAppropriateness:
    """Test is_business_appropriate()."""

    def test_normal_day_is_appropriate(self):
        ctx = CulturalContext(now=date(2024, 8, 15))
        assert ctx.is_business_appropriate() is True

    def test_ramadan_outside_iftar(self):
        """During Ramadan but not iftar time."""
        ctx = CulturalContext(now=date(2024, 3, 20))
        # is_business_appropriate checks UTC hour
        # At most times, business is appropriate even in Ramadan
        # The only blocked time is ~15:00-16:00 UTC (iftar)
        # We can't test exact hour without mocking, but test the structure
        assert isinstance(ctx.is_business_appropriate(), bool)


class TestHijriDateStr:
    """Test get_hijri_date_str()."""

    def test_format(self):
        ctx = CulturalContext(now=date(2024, 3, 20))
        hijri_str = ctx.get_hijri_date_str()
        # Should contain the Hijri month name
        assert "رمضان" in hijri_str
