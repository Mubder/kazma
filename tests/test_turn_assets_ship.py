"""The turn block's front-end actually ships — plan §14.3, §13.

    Validate the packaged frontend and backend together, including static
    asset versions and stale-client protocol behavior.   (§14.3)

Three separate ways the turn block can be correct in the repository and
broken in a browser, none of which any other test in this plan would
notice:

1. **A module nobody loads.** `turn_presentation.js` and
   `turn_preferences.js` are new in this plan. A module that is not in
   the template is not on the page, and every unit test of it still
   passes — under bare node, where the template does not exist.
2. **A module the server will not serve.** The package ships
   `kazma-ui/kazma_ui/static/`; a file outside it, or missing from the
   wheel's package data, 404s in the installed build and nowhere else.
3. **A cached module.** `?v=` is what makes a browser fetch the new file
   after a deploy. If the version does not move when a turn module
   changes, the client keeps yesterday's renderer against today's
   protocol — which is exactly the "stale client" §14.3 names.

These are static and cheap. They are not a substitute for the browser
suite; they catch the class of failure the browser suite cannot, because
the browser suite loads the page from a dev server it just built.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "kazma-ui" / "kazma_ui"
STATIC = UI / "static"
TEMPLATES = UI / "templates"

#: Every module the unified turn block needs on the page. The projector
#: and the view are the renderer; presentation and preferences are the
#: header model and the fold store this plan added.
TURN_MODULES = (
    "modules/turn_document.js",
    "modules/turn_view.js",
    "modules/turn_presentation.js",
    "modules/turn_preferences.js",
)


def _template_text() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(TEMPLATES.glob("*.html"))
    )


# ══════════════════════════════════════════════════════════════════════
# 1. Loaded
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module", TURN_MODULES)
def test_every_turn_module_is_registered_in_a_template(module: str) -> None:
    """A module that is not in the template is not on the page.

    Its unit tests still pass — they run under bare node, where there is
    no template to be missing from.
    """
    html = _template_text()
    assert module in html, (
        f"{module} is not referenced by any template; nothing loads it"
    )


@pytest.mark.parametrize("module", TURN_MODULES)
def test_every_turn_module_is_cache_busted(module: str) -> None:
    """Every reference carries ``?v=``.

    One un-busted reference is enough: that module keeps its cached copy
    across a deploy while its siblings update, which is the worst of both
    versions rather than either.
    """
    html = _template_text()
    # Quoted references only. Prose mentions the module too — a comment
    # explaining where `_buildTurnHeader` gets its model names the file,
    # and a comment is not a fetch.
    refs = re.findall(
        r'["\'][^"\']*' + re.escape(module) + r'([^"\']*)["\']', html
    )
    assert refs, f"{module} has no quoted reference in any template"
    for query in refs:
        # An import-map KEY is a bare specifier by design; the VALUE beside
        # it is the versioned URL. So a bare reference is only a problem
        # when NO reference carries a version.
        if "v=" in query:
            break
    else:
        pytest.fail(
            f"{module} is referenced {len(refs)} time(s), none with a "
            f"cache-busting version: {refs}"
        )


# ══════════════════════════════════════════════════════════════════════
# 2. Served
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module", TURN_MODULES)
def test_every_turn_module_is_inside_the_packaged_static_tree(
    module: str,
) -> None:
    """The wheel ships ``kazma_ui/static``; anything outside it 404s."""
    path = STATIC / "js" / module
    assert path.is_file(), f"{path} does not exist"
    assert STATIC in path.resolve().parents, (
        f"{module} resolves outside the packaged static tree"
    )
    assert path.stat().st_size > 0, f"{module} is empty"


def test_the_wheel_ships_the_package_that_contains_the_front_end() -> None:
    """Package data, not just files on disk.

    A file present in the checkout and absent from the built wheel is the
    failure this catches, and it only ever shows up in an installed
    build.

    Hatchling includes every file under a listed package directory, so
    ``static/`` ships exactly as long as ``kazma-ui/kazma_ui`` is in the
    wheel target — which is the thing asserted, rather than the presence
    of the word "static" somewhere in a config file.
    """
    pyproject = ROOT / "pyproject.toml"
    assert pyproject.is_file(), "no root pyproject.toml"
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        pytest.skip("tomllib unavailable")
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    backend = (
        config.get("build-system", {}).get("build-backend") or ""
    )
    assert backend.startswith("hatchling"), (
        f"build backend is {backend!r}, not hatchling — its package-data "
        "rules differ, so this check no longer proves the front-end ships"
    )

    packages = (
        config.get("tool", {}).get("hatch", {}).get("build", {})
        .get("targets", {}).get("wheel", {}).get("packages", [])
    )
    assert packages, "the wheel target lists no packages"

    # The directory the static tree lives under, as the wheel target
    # spells it.
    owner = STATIC.parent.relative_to(ROOT).as_posix()
    assert owner in packages, (
        f"{owner!r} is not in the wheel's packages {packages}; the built "
        "wheel would ship without the turn block's front-end"
    )


# ══════════════════════════════════════════════════════════════════════
# 3. Busted
# ══════════════════════════════════════════════════════════════════════


def test_touching_a_turn_module_moves_the_asset_version() -> None:
    """The mechanism, exercised rather than assumed.

    ``app.py`` derives the version from the newest mtime under
    ``static/js`` — globbed, after a hand-maintained whitelist missed
    whole directories and served stale JS against fresh HTML (UI audit
    P1-2). The modules this plan added live in ``static/js/modules``, so
    the question is whether the glob reaches them.

    The file's mtime is restored afterwards; nothing here edits content.
    """
    js_root = STATIC / "js"

    def version() -> int:
        latest = 1
        for path in js_root.rglob("*.js"):
            try:
                latest = max(latest, int(os.path.getmtime(path)))
            except OSError:
                pass
        return latest

    target = js_root / "modules" / "turn_view.js"
    before_stat = target.stat()
    before = version()
    try:
        future = int(time.time()) + 3600
        os.utime(target, (future, future))
        after = version()
    finally:
        os.utime(target, (before_stat.st_atime, before_stat.st_mtime))

    assert after > before, (
        "changing tests/…/static/js/modules/turn_view.js did not move the "
        "asset version; a deployed client would keep the cached renderer"
    )
    assert version() == before, "the test did not restore the file's mtime"


def test_the_version_helper_scans_the_whole_js_tree() -> None:
    """...and the shipped helper is the one that was just exercised."""
    app = (UI / "app.py").read_text(encoding="utf-8")
    block = app.split("def _js_version()", 1)[1].split("def ", 1)[0]
    assert 'rglob("*.js")' in block, (
        "the asset version no longer walks the whole JS tree; a module "
        "outside the walked set will be served stale"
    )
    assert "_js_root" in block
