"""Ready-made automations (REMAINING 4.3): each recipe books an ordinary
scheduled task that only reads, through the Scheduled page's own booking path
(the real scheduler and store), at its days and the chosen time, answered in
the page's language; the page shows a card for every recipe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from kazma_core.cron.recipes import RECIPES, RecipeError, booking_for, recipe_list
from kazma_core.cron.timing import parse_recurrence
from tests.test_scheduled_api import _CSRF, _row, real_cron  # noqa: F401 -- the fixture

ROOT = Path(__file__).resolve().parents[1]


def test_every_recipe_books_a_read_only_recurring_task() -> None:
    assert {r.id for r in RECIPES} == {"morning_briefing", "inbox_triage", "weekly_report", "news_digest"}
    for recipe in RECIPES:
        booking = booking_for(recipe.id, topic="electric cars")
        assert booking["kind"] == "task" and booking["capabilities"] == "read"
        assert parse_recurrence(booking["timing"]) is not None, booking["timing"]
        assert "{topic}" not in booking["prompt"]
    assert booking_for("news_digest", topic="electric cars")["prompt"].startswith("News digest on: electric cars.")
    assert booking_for("weekly_report", at="9:05")["timing"] == "every fri at 09:05"
    assert booking_for("inbox_triage", language="ar")["prompt"].endswith("Write the answer in Arabic.")
    assert not booking_for("inbox_triage", language="en")["prompt"].endswith("Arabic.")


@pytest.mark.parametrize("recipe_id, kw", [("no_such", {}), ("morning_briefing", {"at": "25:00"}),
                                           ("morning_briefing", {"at": "7pm"}), ("news_digest", {"topic": "  "}),
                                           ("news_digest", {"topic": "x" * 200})])
def test_what_cannot_be_booked_is_refused(recipe_id, kw) -> None:
    with pytest.raises(RecipeError):
        booking_for(recipe_id, **kw)


def test_the_page_has_a_card_and_words_for_every_recipe() -> None:
    from kazma_ui.i18n.catalog import merged

    page = (ROOT / "kazma-ui" / "kazma_ui" / "templates" / "scheduled.html").read_text(encoding="utf-8")
    cards = set(re.findall(r"recipe_card\('([a-z_]+)'", page))
    assert cards == {r["id"] for r in recipe_list()}
    catalog = merged()
    for recipe in RECIPES:
        for part in ("title", "what"):
            entry = catalog[f"scheduled.recipe.{recipe.id}.{part}"]
            assert entry["en"] and entry["ar"]


def test_a_recipe_is_booked_through_the_pages_own_path(real_cron) -> None:  # noqa: F811
    c = real_cron.client
    assert c.get("/api/scheduled/recipes").json()["recipes"][0]["id"] == "morning_briefing"
    res = c.post("/api/scheduled/recipes/news_digest", json={"at": "06:45", "topic": "electric cars"}, headers=_CSRF)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    row = _row(real_cron.db, job_id)
    assert row["prompt"].startswith("News digest on: electric cars.")
    assert row["kind"] == "task" and row["capabilities"] == "read"
    assert c.post("/api/scheduled/recipes/no_such", json={}, headers=_CSRF).status_code == 404
    bad = c.post("/api/scheduled/recipes/news_digest", json={"at": "06:45"}, headers=_CSRF)
    assert bad.status_code == 422 and bad.json()["code"] == "invalid"
    assert c.post("/api/scheduled/recipes/inbox_triage", json={}).status_code == 403, "same-origin like every change"
