"""Ready-made automations: scheduled tasks set up in one click (REMAINING 4.3).

Each recipe is an ordinary scheduled task (``kind="task"``) that only reads
(``capabilities="read"``: the fire is offered read and safe tools, never one
that sends, writes or deletes; ``cron/capabilities.py``), booked through the
same path as the Scheduled page's own form, so every rule of a scheduled task
holds: the delivery address captured at booking, the operator's zone, the
outbox, the approval card when HITL is on. A recipe only fills in the
instruction and the time.

- ``days`` and ``at`` make the timing (``"weekdays at 09:00"``); the person may
  change the time, never the days.
- ``prompt`` is the instruction the fire's model gets; a recipe with a field
  (``needs``: a topic) has it put in, at most :data:`TOPIC_MAX` characters.
- ``uses`` names what the person should have connected (email, calendar); a
  task with nothing connected says so in its answer, it does not fail.
- The answer's language: the page's (``language_line``).

The page's words for each recipe are the catalog's
(``scheduled.recipe.<id>.title`` / ``.what``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["RECIPES", "TOPIC_MAX", "Recipe", "RecipeError", "booking_for", "recipe_list"]

TOPIC_MAX = 120
_CLOCK = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class RecipeError(ValueError):
    """A recipe that does not exist, a time that is not one, a field missing."""


@dataclass(frozen=True)
class Recipe:
    id: str
    days: str          # "daily", "weekdays", "every fri"
    at: str            # the default time, HH:MM in the operator's zone
    prompt: str
    uses: tuple[str, ...] = ()
    needs: tuple[str, ...] = ()


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        "morning_briefing", "daily", "07:30",
        "Morning briefing for me. Use only your read tools. 1) Today's calendar events: time and title. "
        "2) Unread emails from the last 24 hours that look important: sender and subject, one line each. "
        "3) My scheduled reminders and tasks for today. Keep it short, in bullet lists. If something is "
        "not connected, say so in one line and go on.",
        uses=("calendar", "email"),
    ),
    Recipe(
        "inbox_triage", "weekdays", "09:00",
        "Inbox triage. Read my unread emails from the last 24 hours with your read tools only: do not "
        "reply to, move, label or delete anything. Sort them into: Needs my reply, For my information, "
        "Can wait. One line each: sender, subject, and why. End with the one email I should answer first. "
        "If email is not connected, say so in one line.",
        uses=("email",),
    ),
    Recipe(
        "weekly_report", "every fri", "16:00",
        "My week in review. From your memory of our conversations this week and my scheduled tasks: what "
        "I worked on, what got done, what is still open, and what is coming up next week. Short sections "
        "with bullet points.",
    ),
    Recipe(
        "news_digest", "daily", "08:00",
        "News digest on: {topic}. Search the web for the most important news on it from the last 24 "
        "hours. At most 5 items: a one-line headline, two lines on why it matters, and the source link. "
        "Leave out anything older than two days. If there is no real news, say so in one line.",
        needs=("topic",),
    ),
)

_BY_ID = {r.id: r for r in RECIPES}


def recipe_list() -> list[dict[str, object]]:
    """What the page shows: each recipe's id, days, default time, what it uses and needs."""
    return [{"id": r.id, "days": r.days, "at": r.at, "uses": list(r.uses), "needs": list(r.needs)}
            for r in RECIPES]


def _language_line(language: str) -> str:
    return " Write the answer in Arabic." if language == "ar" else ""


def booking_for(recipe_id: str, *, at: str = "", topic: str = "", language: str = "en") -> dict[str, str]:
    """``{timing, prompt, kind, capabilities}`` for the Scheduled page's booking.
    Raises :class:`RecipeError` for an unknown recipe, a time that is not
    ``HH:MM`` or a missing topic."""
    recipe = _BY_ID.get(recipe_id)
    if recipe is None:
        raise RecipeError(f"no ready-made automation is called {recipe_id!r}")
    at = (at or recipe.at).strip()
    match = _CLOCK.fullmatch(at)
    if match is None:
        raise RecipeError(f"{at!r} is not a time (HH:MM)")
    at = f"{int(match.group(1)):02d}:{match.group(2)}"
    prompt = recipe.prompt
    if "topic" in recipe.needs:
        topic = " ".join((topic or "").split())
        if not topic:
            raise RecipeError("a topic is needed")
        if len(topic) > TOPIC_MAX:
            raise RecipeError(f"the topic is longer than {TOPIC_MAX} characters")
        prompt = prompt.replace("{topic}", topic)
    return {"timing": f"{recipe.days} at {at}", "prompt": prompt + _language_line(language),
            "kind": "task", "capabilities": "read"}
