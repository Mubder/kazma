"""Cultural context enrichment for the system prompt.

Injects seasonal/cultural awareness (Ramadan, Eid, Kuwait National Day,
Hijri date) into the system prompt so the LLM naturally produces
culturally appropriate greetings and responses.

This is the live wiring that connects CulturalContext (previously dead
code) to the running agent's system prompt.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_cultural_prompt_suffix() -> str:
    """Build a cultural context suffix for the system prompt.

    Returns an empty string if no cultural events are active.
    Safe to call at startup — all failures return empty string.
    """
    try:
        from kazma_core.cultural_context import (
            CulturalContext,
            _gregorian_to_hijri_approx,
            _hijri_month_name,
        )

        ctx = CulturalContext()
        state = ctx.state
        modifiers = ctx.get_conversation_modifiers()

        parts: list[str] = []

        # Hijri date awareness — these are module-level functions, not
        # methods on CulturalContext; calling them as ctx._foo(...) raised
        # AttributeError, silently swallowed below, so this suffix never
        # fired despite being wired into the system prompt.
        hijri = _gregorian_to_hijri_approx(state.current_date)
        month_name = _hijri_month_name(hijri[1])
        parts.append(
            f"\n\nCULTURAL CONTEXT — Today is {hijri[0]} {month_name} {hijri[2]} AH "
            f"({state.current_date.isoformat()} Gregorian)."
        )

        # Active events
        seasonal = modifiers.get("seasonal_greetings", [])
        if seasonal:
            parts.append(
                f"\nActive cultural event: {', '.join(seasonal)}. "
                f"Incorporate an appropriate greeting naturally if the user greets you."
            )

        if state.is_ramadan:
            parts.append(
                "\nIt is currently Ramadan. When appropriate, you may say "
                "'رمضان كريم' or 'رمضان مبارك'. Be mindful that the user may "
                "be fasting."
            )

        if state.is_eid:
            parts.append(
                "\nIt is currently Eid. When appropriate, you may say "
                "'عيد مبارك' or 'كل عام وأنتم بخير'."
            )

        if state.is_national_day:
            parts.append(
                "\nToday is Kuwait National Day (February 25). You may "
                "acknowledge this if the conversation allows."
            )

        if state.is_liberation_day:
            parts.append(
                "\nToday is Kuwait Liberation Day (February 26)."
            )

        suffix = "".join(parts)
        if suffix:
            logger.info("[Cultural] System prompt enriched: Ramadan=%s Eid=%s NationalDay=%s",
                        state.is_ramadan, state.is_eid, state.is_national_day)
        return suffix

    except Exception as exc:
        logger.debug("[Cultural] Context enrichment failed: %s", exc)
        return ""
