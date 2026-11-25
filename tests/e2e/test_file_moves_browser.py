"""Moving one's own file on the Documents page's Files panel (Spaces S2-12,
piece 7), EN/AR.

Reem, signed in, reaches the Documents page by the sidebar in her own
space: the panel lists her space's files, and her file offers "Share
with…" the plain shared space she is in. The dialog says what sharing
means before anything moves; then the file is gone from her own space's
folder and in the shared space's. There, the same file offers "Take out",
said first too, it goes back, and the shared space shows the line of it. A
move whose answer is lost on the way is said as what the list shows. The
routes, the folders, the maker
records and the people are the real app's.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_people_page_browser import _modal_action, _soft_nav, _t  # noqa: E402

NAME = "خطة الرحلة.txt"


def _plain(text: str) -> str:
    return " ".join(text.replace("\u2068", "").replace("\u2069", "").split())


def _reem_and_family() -> tuple[dict, str, str]:
    """Reem, her session, and the shared space Family she is in."""
    from kazma_core.security import web_sessions
    from kazma_core.spaces import people
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

    reem = people.create_person("Reem", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
    family = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)["id"]
    people.add_member(family, reem["id"], by=OWNER_ACCOUNT_ID)
    session = web_sessions.create_session(role="operator", actor="login", username="Reem", user_id=reem["id"])
    # "You were added to Family" is told at her next page load: read here,
    # so no notice stands over the panel (tests/e2e/test_space_membership_browser.py has it).
    told = people.notices_for(reem["id"])
    if told:
        people.mark_seen(reem["id"], max(n["seq"] for n in told))
    return reem, family, session


def _folder(space: str):
    from kazma_core.spaces.files import FILES, space_dir

    folder = space_dir(space) / FILES
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _documents_by_sidebar(pg, base: str, space: str) -> None:
    pg.goto(f"{base}/memory?space={space}", wait_until="domcontentloaded")
    pg.wait_for_function("() => !!window.kazmaSpace && window.kazmaSpace.spaces.length > 1", timeout=20000)
    _soft_nav(pg, "/documents")
    pg.locator("#space-files").wait_for(state="visible", timeout=30000)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_share_and_take_out_a_file_on_the_documents_page(lang):
    from kazma_core.spaces import scope
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "file-moves-test-secret-" + "x" * 24
        scope.set_enforced(True)
        reem, family, session = _reem_and_family()
        own = reem["personal_space"]
        (_folder(own) / NAME).write_bytes(b"the trip plan")
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base},
                                 {"name": "kazma-session", "value": session, "url": h.base}])
            pg = context.new_page()

            # ── In her own space: Share with Family ──────────────────────
            _documents_by_sidebar(pg, h.base, own)
            row = pg.locator(f'[data-space-file="{NAME}"]')
            row.wait_for(state="visible", timeout=20000)
            assert row.locator("bdi[translate='no']").inner_text().strip() == NAME
            assert pg.locator(f'[data-file-take-out="{NAME}"]').is_hidden(), "nothing to take out of one's own space"
            pg.locator(f'[data-file-share="{NAME}"]').select_option(family)
            pg.locator("#modal-title").wait_for(state="visible", timeout=10000)
            dialog = pg.locator(".modal", has=pg.locator("#modal-title"))
            said = _plain(dialog.inner_text())
            assert _plain(_t("documents.files.share_title", lang, space="Family")) in said, said
            assert _plain(_t("documents.files.share_confirm", lang)) in said
            with pg.expect_response(lambda r: r.url.endswith("/api/space-files/move")) as answer:
                _modal_action(pg, _t("documents.share", lang))
            assert answer.value.status == 200, answer.value.text()
            row.wait_for(state="detached", timeout=20000)
            assert not (_folder(own) / NAME).exists()
            assert (_folder(family) / NAME).read_bytes() == b"the trip plan"

            # ── In the shared space: Take out ────────────────────────────
            _documents_by_sidebar(pg, h.base, family)
            take = pg.locator(f'[data-file-take-out="{NAME}"]')
            take.wait_for(state="visible", timeout=20000)
            take.click()
            pg.locator("#modal-title").wait_for(state="visible", timeout=10000)
            said = _plain(dialog.inner_text())
            assert _plain(_t("documents.files.take_out_title", lang, space="Family")) in said, said
            assert _plain(_t("documents.files.take_out_confirm", lang)) in said
            with pg.expect_response(lambda r: r.url.endswith("/api/space-files/move")) as answer:
                _modal_action(pg, _t("documents.take_out", lang))
            assert answer.value.status == 200, answer.value.text()
            pg.locator(f'[data-space-file="{NAME}"]').wait_for(state="detached", timeout=20000)
            assert (_folder(own) / NAME).read_bytes() == b"the trip plan"
            assert not (_folder(family) / NAME).exists()
            line = pg.locator(f'[data-file-taken-out="{NAME}"]')
            line.wait_for(state="visible", timeout=20000)
            assert line.locator("bdi[translate='no']").inner_text().strip() == NAME
            assert "Reem" in line.inner_text(), line.inner_text()

            # ── The answer is lost on the way: said as the list shows it ─
            _documents_by_sidebar(pg, h.base, own)
            pg.locator(f'[data-space-file="{NAME}"]').wait_for(state="visible", timeout=20000)

            def lost(route):
                route.fetch()  # the server moves the file
                route.abort()

            pg.route("**/api/space-files/move", lost)
            pg.locator(f'[data-file-share="{NAME}"]').select_option(family)
            pg.locator("#modal-title").wait_for(state="visible", timeout=10000)
            _modal_action(pg, _t("documents.share", lang))
            pg.get_by_text(_t("documents.files.lost_gone", lang)).first.wait_for(state="visible", timeout=20000)
            assert (_folder(family) / NAME).exists() and not (_folder(own) / NAME).exists()
            pg.locator(f'[data-space-file="{NAME}"]').wait_for(state="detached", timeout=20000)
        finally:
            browser.close()
            scope.set_enforced(False)
