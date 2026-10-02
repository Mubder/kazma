"""A count in a template reads in the request's language (2026-10-02).

The MCP page wrote "{n} tools" as ``{{ n }} {{ t('mcp.tools_suffix') }}``,
so the Arabic page read "1 أدوات" -- one with the plural that fits three to
ten. ``t_plural`` picks Arabic's six forms, but the template global was the
English-bound default the i18n patch registers: the app replaced ``t`` and
``plural_forms`` with request-language versions and not ``t_plural``, so a
template using it would have read English in Arabic. The app binds it now,
and the MCP card's count is its first use.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates


def _app_globals():
    from kazma_ui.app import KazmaAppBuilder

    builder = KazmaAppBuilder()
    builder.app = FastAPI()
    builder.agent = SimpleNamespace(config=SimpleNamespace(language="en"))
    builder._setup_templates_and_middlewares()
    return builder, builder.templates.env.globals


def _in(builder, lang: str, fn):
    token = builder._current_lang.set(lang)
    try:
        return fn()
    finally:
        builder._current_lang.reset(token)


def test_the_mcp_cards_count_reads_in_each_language() -> None:
    builder, g = _app_globals()
    counts = (0, 1, 2, 5, 14, 100)
    arabic = _in(builder, "ar", lambda: [g["t_plural"]("mcp.tool_count", n) for n in counts])
    assert arabic == ["لا أدوات", "أداة واحدة", "أداتان", "5 أدوات", "14 أداة", "100 أداة"]
    english = _in(builder, "en", lambda: [g["t_plural"]("mcp.tool_count", n) for n in counts])
    assert english == ["0 tools", "1 tool", "2 tools", "5 tools", "14 tools", "100 tools"]


def test_the_mcp_page_shows_each_count_in_its_form() -> None:
    import re

    builder, _g = _app_globals()
    page = builder.templates.env.get_template("mcp.html")
    servers = [
        {"name": name, "transport": "stdio", "command": ["x"], "url": "", "env": {},
         "status": "running", "tool_count": n, "tools": []}
        for name, n in (("one", 1), ("many", 14))
    ]
    request = SimpleNamespace(url=SimpleNamespace(path="/mcp"), cookies={}, headers={},
                              query_params={}, state=SimpleNamespace(), scope={})

    def counts(lang: str) -> list[str]:
        html = _in(builder, lang, lambda: page.render(
            servers=servers, config=SimpleNamespace(), active_page="mcp", request=request))
        return re.findall(r'class="tool-count">([^<]*)<', html)

    assert counts("ar") == ["أداة واحدة", "14 أداة"]  # it read "1 أدوات"
    assert counts("en") == ["1 tool", "14 tools"]


def test_negative_control_the_default_global_reads_english_in_arabic() -> None:
    """What any template got before the app bound it."""
    builder, _g = _app_globals()
    plain = Jinja2Templates(directory=str(builder.templates.env.loader.searchpath[0]))
    default = plain.env.globals["t_plural"]
    assert _in(builder, "ar", lambda: default("mcp.tool_count", 1)) == "1 tool"
