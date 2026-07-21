"""Product self-knowledge + Arabic brand spelling for Kazma."""

from __future__ import annotations

from kazma_core.language_lock import language_lock_message
from kazma_core.product_knowledge import (
    ARABIC_NAME_FORBIDDEN,
    ARABIC_NAME_PRIMARY,
    ARABIC_NAME_VARIANT,
    LATIN_NAME,
    build_product_knowledge,
    identity_line,
    knowledge_already_present,
)


class TestArabicBrand:
    def test_primary_uses_zaa_not_zay(self) -> None:
        """Brand must use ظ (U+0638), not ز (U+0632)."""
        assert "ظ" in ARABIC_NAME_PRIMARY
        assert "ز" not in ARABIC_NAME_PRIMARY
        assert ARABIC_NAME_FORBIDDEN == "كازما"
        assert "ز" in ARABIC_NAME_FORBIDDEN

    def test_knowledge_forbids_wrong_spelling(self) -> None:
        block = build_product_knowledge()
        assert ARABIC_NAME_PRIMARY in block
        assert ARABIC_NAME_VARIANT in block
        assert ARABIC_NAME_FORBIDDEN in block
        assert "never" in block.lower() or "Never" in block or "wrong" in block.lower()
        assert LATIN_NAME in block
        assert "HITL" in block
        assert "swarm" in block.lower() or "Swarm" in block
        assert "IDE" in block or "workspace" in block.lower()

    def test_identity_line(self) -> None:
        line = identity_line()
        assert LATIN_NAME in line
        assert ARABIC_NAME_PRIMARY in line
        assert ARABIC_NAME_FORBIDDEN in line

    def test_knowledge_already_present(self) -> None:
        empty = "You are a helpful bot."
        assert not knowledge_already_present(empty)
        full = empty + "\n\n" + build_product_knowledge()
        assert knowledge_already_present(full)


class TestLanguageLockBrand:
    def test_arabic_lock_enforces_correct_name(self) -> None:
        msg = language_lock_message("مرحبا كيف أستخدم كاظمه؟")
        assert "ARABIC" in msg
        assert ARABIC_NAME_PRIMARY in msg
        assert ARABIC_NAME_FORBIDDEN in msg

    def test_english_lock_uses_latin_name(self) -> None:
        msg = language_lock_message("How do I use the IDE?")
        assert "ENGLISH" in msg
        assert LATIN_NAME in msg


class TestDefaultPromptBrand:
    def test_yaml_system_prompt_has_correct_arabic(self) -> None:
        from pathlib import Path

        yaml_text = Path("kazma.yaml").read_text(encoding="utf-8")
        assert ARABIC_NAME_PRIMARY in yaml_text
        assert ARABIC_NAME_FORBIDDEN in yaml_text  # as "never كازما"
        # Forbidden form must only appear as negation context near "never"
        # — ensure primary brand is present as the positive form.
        assert "كاظمه" in yaml_text
