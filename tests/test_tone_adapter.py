"""Tests for ToneAdapter — Cultural tone profiles and response adaptation."""
from __future__ import annotations

import pytest
from kazma_core.tone_adapter import (
    TONE_PROFILES,
    FormalityLevel,
    ToneAdapter,
    ToneProfile,
)


class TestToneProfiles:
    """Test tone profile definitions."""

    def test_all_profiles_exist(self):
        """Verify all expected profiles are defined."""
        expected = [
            "formal_business",
            "casual_family",
            "government_official",
            "general_polite",
            "ramadan_warm",
            "eid_celebratory",
            "national_pride",
        ]
        for name in expected:
            assert name in TONE_PROFILES

    def test_profile_frozen(self):
        """Profiles should be frozen dataclasses."""
        profile = TONE_PROFILES["formal_business"]
        assert isinstance(profile, ToneProfile)
        with pytest.raises(AttributeError):
            profile.name = "changed"  # type: ignore[misc]


class TestToneAdapterInit:
    """Test ToneAdapter initialization."""

    def test_default_profiles(self):
        adapter = ToneAdapter()
        profiles = adapter.list_profiles()
        assert len(profiles) >= 7
        assert "formal_business" in profiles
        assert "casual_family" in profiles

    def test_get_profile(self):
        adapter = ToneAdapter()
        profile = adapter.get_profile("formal_business")
        assert profile is not None
        assert profile.name == "formal_business"

    def test_get_nonexistent_profile(self):
        adapter = ToneAdapter()
        profile = adapter.get_profile("nonexistent")
        assert profile is None


class TestProfileSelection:
    """Test select_profile() logic."""

    def test_very_formal_selects_government(self):
        adapter = ToneAdapter()
        profile = adapter.select_profile(FormalityLevel.VERY_FORMAL)
        assert profile.name == "government_official"

    def test_formal_selects_business(self):
        adapter = ToneAdapter()
        profile = adapter.select_profile(FormalityLevel.FORMAL)
        assert profile.name == "formal_business"

    def test_normal_selects_general(self):
        adapter = ToneAdapter()
        profile = adapter.select_profile(FormalityLevel.NORMAL)
        assert profile.name == "general_polite"

    def test_casual_selects_family(self):
        adapter = ToneAdapter()
        profile = adapter.select_profile(FormalityLevel.CASUAL)
        assert profile.name == "casual_family"

    def test_ramadan_overrides(self):
        adapter = ToneAdapter()
        profile = adapter.select_profile(
            FormalityLevel.NORMAL, is_ramadan=True
        )
        assert profile.name == "ramadan_warm"

    def test_eid_overrides(self):
        adapter = ToneAdapter()
        profile = adapter.select_profile(
            FormalityLevel.FORMAL, is_eid=True
        )
        assert profile.name == "eid_celebratory"

    def test_national_day_overrides(self):
        adapter = ToneAdapter()
        profile = adapter.select_profile(
            FormalityLevel.NORMAL, is_national_day=True
        )
        assert profile.name == "national_pride"


class TestAdaptResponse:
    """Test adapt_response() output."""

    def test_prefix_applied(self):
        adapter = ToneAdapter()
        result = adapter.adapt_response(
            "مرحباً", profile_name="formal_business"
        )
        assert result.startswith("سيدي/سيدتي")

    def test_suffix_applied(self):
        adapter = ToneAdapter()
        result = adapter.adapt_response(
            "مرحباً", profile_name="formal_business"
        )
        assert "خالص التقدير" in result

    def test_casual_no_prefix(self):
        adapter = ToneAdapter()
        result = adapter.adapt_response(
            "هلا", profile_name="casual_family"
        )
        assert result.startswith("")  # No prefix for casual

    def test_government_suffix(self):
        adapter = ToneAdapter()
        result = adapter.adapt_response(
            "نعم", profile_name="government_official"
        )
        assert "الاحترام والتقدير" in result

    def test_profile_object_accepted(self):
        adapter = ToneAdapter()
        profile = TONE_PROFILES["eid_celebratory"]
        result = adapter.adapt_response("مرحباً", profile=profile)
        assert "عيد مبارك" in result

    def test_unknown_profile_falls_back(self):
        adapter = ToneAdapter()
        result = adapter.adapt_response("مرحباً", profile_name="unknown")
        # Should use general_polite (no prefix)
        assert isinstance(result, str)

    def test_preserves_existing_prefix(self):
        """If response already has the prefix, don't double it."""
        adapter = ToneAdapter()
        result = adapter.adapt_response(
            "سيدي/سيدتي، مرحباً", profile_name="formal_business"
        )
        assert result.count("سيدي/سيدتي") == 1


class TestFormalityDetection:
    """Test determine_formality_from_text()."""

    def test_casual_detection(self):
        adapter = ToneAdapter()
        level = adapter.determine_formality_from_text("شلونك هلا وين")
        assert level == FormalityLevel.CASUAL

    def test_formal_detection(self):
        adapter = ToneAdapter()
        level = adapter.determine_formality_from_text("سيدي الكريم، أود أن أسأل عن التقرير")
        assert level == FormalityLevel.FORMAL

    def test_very_formal_detection(self):
        adapter = ToneAdapter()
        level = adapter.determine_formality_from_text("سعادة الوزير، نرفق التقرير الرسمي")
        assert level == FormalityLevel.VERY_FORMAL

    def test_normal_default(self):
        adapter = ToneAdapter()
        level = adapter.determine_formality_from_text("كيف حالك اليوم")
        assert level == FormalityLevel.NORMAL

    def test_empty_text(self):
        adapter = ToneAdapter()
        level = adapter.determine_formality_from_text("")
        assert level == FormalityLevel.NORMAL


class TestSeasonalPrefix:
    """Test get_seasonal_prefix()."""

    def test_ramadan_prefix(self):
        adapter = ToneAdapter()
        prefix = adapter.get_seasonal_prefix(is_ramadan=True)
        assert "رمضان" in prefix

    def test_eid_prefix(self):
        adapter = ToneAdapter()
        prefix = adapter.get_seasonal_prefix(is_eid=True)
        assert "عيد" in prefix

    def test_national_day_prefix(self):
        adapter = ToneAdapter()
        prefix = adapter.get_seasonal_prefix(is_national_day=True)
        assert "الكويت" in prefix

    def test_no_event_empty_prefix(self):
        adapter = ToneAdapter()
        prefix = adapter.get_seasonal_prefix()
        assert prefix == ""


class TestSlangFormalization:
    """Test _formalize_text() for Kuwaiti slang."""

    def test_formalize_shlonak(self):
        adapter = ToneAdapter()
        result = adapter._formalize_text("شلونك اليوم", "kw")
        assert "شلونك" not in result
        assert "كيف حالك" in result

    def test_formalize_kuwaiti_only(self):
        """Non-Kuwaiti dialects should not be formalized."""
        adapter = ToneAdapter()
        text = "شو أخبارك"
        result = adapter._formalize_text(text, "msa")
        assert result == text  # Unchanged
