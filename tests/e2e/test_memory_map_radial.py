"""The memory map is a radial hierarchy (2026-11-20, the owner's request).

The hub (you) sits at the centre; the major things -- marked major, or, when
none is marked, the hub's most connected neighbours -- sit evenly on an inner
ring; everything else sits beyond them, on the side of the major it belongs
to. The hub is the largest dot, a major smaller, the rest smaller still. The
owner: the major nodes "hide within the many nodes available".

Reached by the sidebar, in English and Arabic; positions read from the page's
own map (``window._v2gGetNodePos``, ``window._v2gGetRings``) once it settles.
"""

from __future__ import annotations

import math
import sqlite3
import time
from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

MAJORS = {
    "kazma": ["langgraph", "telegram", "arabic", "swarm"],
    "shipx": ["shipping", "kuwait", "drivers", "invoices"],
    "kca": ["members", "events", "venue", "budget"],
}
HUB_ONLY = ["coffee", "running", "chess"]


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="ok")) as h:
        yield h


def _seed(*, marked: bool) -> None:
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        ensure_primary_schema(c)
        now = time.time()
        names = list(MAJORS) + [o for objs in MAJORS.values() for o in objs] + HUB_ONLY
        for name in names:
            major = 1 if marked and name in MAJORS else 0
            c.execute("INSERT OR IGNORE INTO entities (id, tenant_id, type, name, aliases_json, is_high_stakes, "
                      "is_major, metadata_json) VALUES (?, 'default', 'concept', ?, '[]', 0, ?, '{}')",
                      (name, name.title(), major))
        c.commit()
        turn = 0
        for major, objects in MAJORS.items():
            turn += 1
            mutate_belief(c, "user", "works_on", major, predicate_type="set", extraction_method="user_stated",
                          source_session="chat-a", source_turn=turn, now=now - 900 + turn, private=True)
            for obj in objects:
                turn += 1
                mutate_belief(c, major, "involves", obj, predicate_type="set", extraction_method="user_stated",
                              source_session="chat-a", source_turn=turn, now=now - 900 + turn, private=True)
        for obj in HUB_ONLY:
            turn += 1
            mutate_belief(c, "user", "likes", obj, predicate_type="set", extraction_method="user_stated",
                          source_session="chat-a", source_turn=turn, now=now - 900 + turn, private=True)
    finally:
        c.close()


def _open_memory(browser, harness: Harness, lang: str):
    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    context = browser.new_context(viewport={"width": 1400, "height": 1000}, locale="en-US")
    context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
    pg = context.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{harness.base}/", wait_until="domcontentloaded", timeout=30000)
    pg.locator("a.nav-link[href='/memory']").first.click(force=True)
    pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
    pg.wait_for_function("() => window._v2gGetView && window._v2gGetView().dots >= 10", timeout=30000)
    pg.wait_for_function("() => window._v2gGetView().settled", timeout=60000)
    return context, pg, errors


def _layout(pg) -> dict:
    return pg.evaluate("""() => {
        const rings = window._v2gGetRings();
        const ids = ['user', 'kazma', 'shipx', 'kca', 'langgraph', 'telegram', 'arabic', 'swarm',
                     'shipping', 'kuwait', 'drivers', 'invoices', 'members', 'events', 'venue', 'budget',
                     'coffee', 'running', 'chess'];
        const pos = {};
        ids.forEach(id => { pos[id] = window._v2gGetNodePos(id); });
        return { rings, pos };
    }""")


def _polar(origin: dict, p: dict) -> tuple[float, float]:
    dx, dy = p["x"] - origin["x"], p["y"] - origin["y"]
    return math.hypot(dx, dy), math.atan2(dy, dx)


def _angle_gap(a: float, b: float) -> float:
    d = abs(a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_you_at_the_centre_the_majors_on_their_ring_the_rest_beyond(harness: Harness, lang: str) -> None:
    from playwright.sync_api import sync_playwright

    _seed(marked=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context, pg, errors = _open_memory(browser, harness, lang)
            got = _layout(pg)
            rings, pos = got["rings"], got["pos"]
            assert rings["hub"] == "user", rings
            assert sorted(rings["majors"]) == ["kazma", "kca", "shipx"], rings
            hub = pos["user"]
            assert hub["rank"] == 1 and all(pos[m]["rank"] == 2 for m in MAJORS)
            # The hub is the largest dot, a major smaller, the rest smaller still.
            minor_sizes = [pos[o]["r"] for objs in MAJORS.values() for o in objs if pos[o]]
            assert hub["r"] > max(pos[m]["r"] for m in MAJORS) > max(minor_sizes), (hub, minor_sizes)
            # The majors sit on the inner ring, evenly spaced around the hub.
            polar = {m: _polar(hub, pos[m]) for m in MAJORS}
            for m, (dist, _ang) in polar.items():
                assert 0.6 * rings["r2"] < dist < 1.4 * rings["r2"], (m, dist, rings["r2"])
            angles = sorted(a for _d, a in polar.values())
            gaps = [_angle_gap(angles[i], angles[(i + 1) % 3]) for i in range(3)]
            assert min(gaps) > math.radians(70), [math.degrees(g) for g in gaps]
            # Everything else is beyond the majors...
            farthest_major = max(d for d, _a in polar.values())
            minors = {o: _polar(hub, pos[o]) for objs in MAJORS.values() for o in objs if pos[o]}
            beyond = sum(1 for d, _a in minors.values() if d > farthest_major)
            assert beyond >= 0.8 * len(minors), (beyond, len(minors))
            # ...on the side of the major it belongs to.
            right_side = 0
            for major, objects in MAJORS.items():
                for obj in objects:
                    _d, ang = minors[obj]
                    nearest = min(MAJORS, key=lambda m: _angle_gap(ang, polar[m][1]))
                    right_side += nearest == major
            assert right_side >= 0.75 * len(minors), (right_side, len(minors))
            assert errors == [], errors
            context.close()
        finally:
            browser.close()


def test_with_nothing_marked_the_most_connected_stand_in(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    _seed(marked=False)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context, pg, errors = _open_memory(browser, harness, "en")
            rings = _layout(pg)["rings"]
            # The three projects hold five lines each; the hub's likes one.
            assert sorted(rings["majors"]) == ["kazma", "kca", "shipx"], rings
            assert errors == [], errors
            context.close()
        finally:
            browser.close()
