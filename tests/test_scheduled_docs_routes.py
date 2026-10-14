"""Every route the Scheduled and X docs name is served by the app (Scheduled audit SCH-DOC, SCH-S09).

The X guide told the operator X Studio reschedules with
``PUT /api/scheduled/x/{id}``; the page used ``/api/x/queue/{op}/reschedule``
and that route was later retired. Each route the X guide and the Scheduled
section of the API reference name -- with its method when the text gives
one -- must be in the built app's route table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "docs"

#: (page, the heading its Scheduled part starts at, or None for the whole page)
PAGES = (
    ("guide/x-publisher.md", None),
    ("reference/api-routes.md", "## Scheduled (cron + X)"),
)

_TABLE_ROW = re.compile(r"^\|\s*(GET|POST|PUT|DELETE|PATCH)\s*\|\s*`(/api/[^`\s]+)`", re.M)
_INLINE = re.compile(r"`(?:(GET|POST|PUT|DELETE|PATCH)\s+)?(/api/[^`\s]+)`")


def _part(text: str, heading: str | None) -> str:
    if heading is None:
        return text
    start = text.index(heading)
    nxt = text.find("\n## ", start + len(heading))
    return text[start:nxt if nxt != -1 else len(text)]


def named_routes(text: str) -> set[tuple[str, str]]:
    """(method or '', path) for every ``/api/...`` route the text names."""
    found = {(m, p) for m, p in _TABLE_ROW.findall(text)}
    in_rows = {p for _m, p in found}
    found |= {(m or "", p) for m, p in _INLINE.findall(text) if m or p not in in_rows}
    out = set()
    for method, path in found:
        path = path.rstrip(".,;:)")
        if "..." in path or "*" in path:
            continue  # a family ("/api/x/reply/*"), not one route
        # "/api/x/queue/{operation_id}/reschedule|cancel" names two.
        head, _, tail = path.rpartition("/")
        for leaf in tail.split("|"):
            out.add((method, f"{head}/{leaf}"))
    return out


def _route_rx(path: str) -> re.Pattern:
    parts = re.split(r"(\{[^}]+\})", path)
    return re.compile("^" + "".join("[^/]+" if p.startswith("{") else re.escape(p) for p in parts) + "$")


def missing(named: set[tuple[str, str]], served: set[tuple[str, str]]) -> list[str]:
    rxs = [(m, _route_rx(p)) for m, p in served]
    out = []
    for method, path in sorted(named):
        concrete = re.sub(r"\{[^}]+\}", "x1", path)
        if not any(rx.match(concrete) and (not method or method == m) for m, rx in rxs):
            out.append(f"{method or 'ANY'} {path}")
    return out


@pytest.fixture(scope="module")
def served() -> set[tuple[str, str]]:
    from kazma_ui.app import create_app

    def walk(routes):
        for route in routes:
            inner = getattr(route, "original_router", None)
            yield from walk(inner.routes) if inner is not None else [route]

    return {(m, r.path) for r in walk(create_app().routes) for m in (getattr(r, "methods", None) or ())
            if getattr(r, "path", "").startswith("/api/")}


@pytest.mark.parametrize(("page", "heading"), PAGES)
def test_every_route_the_docs_name_is_served(page, heading, served):
    named = named_routes(_part((DOCS / page).read_text(encoding="utf-8"), heading))
    assert named, f"{page}: no route found -- the reader is broken"
    assert missing(named, served) == [], f"{page} names routes the app does not serve"


def test_negative_control_the_old_planner_line_is_caught(served):
    old = "- Upcoming X posts with a datetime-local field + **Reschedule** (`PUT /api/scheduled/x/{id}`) and **Cancel**."
    assert missing(named_routes(old), served) == ["PUT /api/scheduled/x/{id}"]
    table = "| DELETE | `/api/scheduled/x/{post_id}` | Session + CSRF | Cancel a pending X post. |"
    assert missing(named_routes(table), served) == ["DELETE /api/scheduled/x/{post_id}"]
