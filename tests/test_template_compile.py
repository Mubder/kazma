"""Every real Jinja template must compile.

Born from the 2026-08-27 incident: settings.html shipped with a JavaScript
``||`` inside a Jinja expression — ``TemplateSyntaxError`` at compile time,
which 500'd the ENTIRE /settings page on every load. The settings-API test
harness renders a dummy template from tmp_path, so no existing test ever
compiled the real file. Compilation (not rendering) catches the whole
class: syntax errors, bad filter names, mismatched blocks.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined

_TEMPLATES = (
    Path(__file__).resolve().parent.parent
    / "kazma-ui"
    / "kazma_ui"
    / "templates"
)

# base.html expects a `t` i18n callable at compile-agnostic render time;
# compilation itself is what we assert here, so a trivial callable suffices.
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    undefined=StrictUndefined,
    autoescape=True,
)
_ENV.globals["t"] = lambda key, *a, **k: key
_ENV.globals["theme"] = lambda: "dark"


def _all_templates() -> list[Path]:
    return sorted(_TEMPLATES.rglob("*.html"))


def test_templates_exist() -> None:
    assert len(_all_templates()) > 10, "template discovery looks broken"


@pytest.mark.parametrize("tpl", _all_templates(), ids=lambda p: p.name)
def test_template_compiles(tpl: Path) -> None:
    rel = tpl.relative_to(_TEMPLATES).as_posix()
    # get_template() compiles the source (and follows {%- extends %} chains);
    # a TemplateSyntaxError here is exactly the 500-class we guard against.
    _ENV.get_template(rel)
