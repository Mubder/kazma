"""The Memory page can protect an entity and lift it (2026-09-28).

``POST /api/memory/v2/entities/{id}/protect`` existed, the entity list
reported ``protected`` and the page disabled Delete on it -- and nothing on
the page could set or clear the flag. An entity protected by any other path
could never be deleted from the page. The list now also says ``core`` (the
entities that are always protected and cannot be unprotected), and the page
shows Protect / Unprotect for every other entity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_memory_routes_tenant_scope import _seed, memory_client  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[1]


def _entity(client, eid: str) -> dict:
    rows = client.get("/api/memory/v2/entities", params={"limit": 200}).json()["entities"]
    return next(r for r in rows if r["id"] == eid)


def test_protect_and_unprotect_round_trip(memory_client) -> None:  # noqa: F811
    _seed(memory_client.tmp)
    c = memory_client.client
    assert _entity(c, "alice")["protected"] is False
    assert _entity(c, "alice")["core"] is False

    r = c.post("/api/memory/v2/entities/alice/protect", json={"protected": True}).json()
    assert r["ok"] is True
    assert _entity(c, "alice")["protected"] is True
    assert c.delete("/api/memory/v2/entities/alice").json()["ok"] is False, "protected: no delete"

    r = c.post("/api/memory/v2/entities/alice/protect", json={"protected": False}).json()
    assert r["ok"] is True
    assert _entity(c, "alice")["protected"] is False


def test_a_core_entity_is_marked_and_stays_protected(memory_client) -> None:  # noqa: F811
    _seed(memory_client.tmp)
    import sqlite3

    con = sqlite3.connect(memory_client.tmp / "memory_state.db")
    con.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('user', 'alpha', 'person', 'You')")
    con.commit()
    con.close()
    c = memory_client.client
    row = _entity(c, "user")
    assert row["core"] is True and row["protected"] is True
    r = c.post("/api/memory/v2/entities/user/protect", json={"protected": False}).json()
    assert r["ok"] is False and "core" in r["error"]


def test_the_page_offers_the_switch_for_every_other_entity() -> None:
    html = (REPO / "kazma-ui/kazma_ui/templates/memory.html").read_text(encoding="utf-8")
    js = (REPO / "kazma-ui/kazma_ui/static/js/memory.js").read_text(encoding="utf-8")
    row = html[html.index('<template x-for="e in entities"'):]
    row = row[: row.index("</template>")]
    assert row.count('@click="toggleProtect(e)"') == 2
    assert 'x-show="!e.core && !e.protected"' in row and 'x-show="!e.core && e.protected"' in row
    method = js[js.index("async toggleProtect(e)"):]
    method = method[: method.index("\n    },")]
    assert "/protect" in method and "protected: want" in method and "e.core" in method


@pytest.mark.parametrize("key", [
    "memory.pg.protect", "memory.pg.unprotect", "memory.pg.core_always_protected",
    "memory.pg.unprotect_first", "memory.page.protected_entity", "memory.page.unprotected_entity",
])
def test_the_words_exist_in_both_languages(key: str) -> None:
    from kazma_ui.i18n import TRANSLATIONS

    assert TRANSLATIONS[key]["en"] and TRANSLATIONS[key]["ar"]
