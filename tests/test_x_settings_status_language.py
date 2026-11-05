"""Settings -> X says whether auto-reply is live in the page's language.

Live 2026-11-05, on the Arabic page: the status line under the X auto-reply
heading ("Live in draft mode, poller running, 2 subjects.") and the hold
reason under stance checking were the server's English sentences, marked
``translate="no"`` as if they were content. They are interface words. The
server now sends a code and its values (``live_reason_code`` /
``live_reason_values``, ``auto_qualification_hold_code``); the page words
each code from the catalog (``settings.xt.reason.<code>``,
``settings.xt.hold.<code>``). Only a subject policy's own errors stay as the
server wrote them: they are diagnostics.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js"
_TEMPLATE = _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "settings.html"

_ARABIC = re.compile(r"[؀-ۿ]")
_PLURAL_FORMS = ("zero", "one", "two", "few", "many", "other")


def _codes() -> tuple[list[str], list[str]]:
    from kazma_core.x_api.qualification import HOLD_TEXT
    from kazma_ui.x_reply_api import _LIVE_TEXT

    return sorted(_LIVE_TEXT), sorted(HOLD_TEXT)


def _catalog() -> dict:
    from kazma_ui.i18n import TRANSLATIONS

    return TRANSLATIONS


# ── The server sends codes ────────────────────────────────────────────────


class _Connector:
    def __init__(self, ok: bool = True):
        self._ok = ok

    def can_post(self) -> bool:
        return self._ok


def _status(monkeypatch, *, poller=True, hold_code=None, connector=True, **over):
    from kazma_ui.x_reply_api import _live_status

    from tests.test_x_reply_settings_api import _cfg

    monkeypatch.setattr("kazma_ui.x_reply_api._poller_running", lambda: poller)
    return _live_status(_cfg(**over), _Connector(connector), hold_code=hold_code)


def test_each_state_is_a_code_with_its_values(monkeypatch):
    assert _status(monkeypatch, connector=False) == ("connector_not_ready", {})
    assert _status(monkeypatch, enabled=False) == ("disabled", {})
    assert _status(monkeypatch, mode="off") == ("mode_off", {})
    assert _status(monkeypatch, summoners=()) == ("no_summoners", {})
    assert _status(monkeypatch, poller=False) == ("poller_stopped", {"mode": "draft"})
    assert _status(monkeypatch, subjects=()) == ("voice_only", {"mode": "draft"})
    code, values = _status(monkeypatch)
    assert code == "live" and values["mode"] == "draft" and values["n"] >= 1
    assert _status(monkeypatch, mode="auto", hold_code="evaluation_missing") == (
        "auto_held", {"hold": "evaluation_missing"},
    )


def test_the_english_sentence_is_still_whole(monkeypatch):
    """Drafts, logs and the API's ``live_reason`` keep the English text."""
    from kazma_ui.x_reply_api import _live_text

    assert _live_text("live", {"mode": "draft", "n": 2}) == (
        "Live in draft mode, poller running, 2 subjects."
    )
    assert "human-reviewed" in _live_text("auto_held", {"hold": "evaluation_missing"})
    assert _live_text("policy_errors", {"errors": "duplicate id x"}).endswith("duplicate id x")


# ── Every code is in the catalog, in both languages ───────────────────────


def test_every_code_has_its_words_in_english_and_arabic():
    catalog = _catalog()
    live_codes, hold_codes = _codes()
    missing = []
    for code in live_codes:
        keys = (
            [f"settings.xt.reason.live.{form}" for form in _PLURAL_FORMS]
            if code == "live" else [f"settings.xt.reason.{code}"]
        )
        for key in keys:
            entry = catalog.get(key) or {}
            if not entry.get("en") or not _ARABIC.search(entry.get("ar", "")):
                missing.append(key)
    for code in hold_codes:
        entry = catalog.get(f"settings.xt.hold.{code}") or {}
        if not entry.get("en") or not _ARABIC.search(entry.get("ar", "")):
            missing.append(f"settings.xt.hold.{code}")
    for mode in ("draft", "auto"):
        if not _ARABIC.search((catalog.get(f"settings.xt.mode_name.{mode}") or {}).get("ar", "")):
            missing.append(f"settings.xt.mode_name.{mode}")
    assert not missing, missing


def test_the_catalogs_english_is_the_servers_english():
    """One sentence per code: the page's English is what the server says."""
    from kazma_core.x_api.qualification import HOLD_TEXT
    from kazma_ui.x_reply_api import _LIVE_TEXT

    catalog = _catalog()
    for code, text in HOLD_TEXT.items():
        assert catalog[f"settings.xt.hold.{code}"]["en"] == text
    for code, text in _LIVE_TEXT.items():
        if code in ("live", "policy_errors"):
            continue
        assert catalog[f"settings.xt.reason.{code}"]["en"] == text, code


# ── The page words the codes ──────────────────────────────────────────────


def _page_texts(lang: str, states: list[dict]) -> list[dict]:
    """Run the page's own methods for each state, in one node process."""
    catalog = {k: v for k, v in _catalog().items() if k.startswith("settings.xt.")}
    harness = (
        "globalThis.window = globalThis;\n"
        f"globalThis.document = {{documentElement: {{lang: {json.dumps(lang)}}}}};\n"
        f"window.KAZMA_LANG = {json.dumps(lang)};\n"
        f"window.KAZMA_I18N = {json.dumps(catalog, ensure_ascii=False)};\n"
        "window.tOr = (k, fb) => { const e = window.KAZMA_I18N[k];"
        " return e ? (e[window.KAZMA_LANG] || e.en || fb) : fb; };\n"
        "window.kazmaT = function(key, fallback, vars) { var text = window.tOr(key, fallback);"
        " if (vars) for (var k in vars) text = text.split('{' + k + '}').join(String(vars[k]));"
        " return text; };\n"
        "window.kazmaCount = function(key, n, vars) { var lang = window.KAZMA_LANG;"
        " var cat = window.KazmaFormat.pluralCategory(n);"
        " var form = function(c) { var e = window.KAZMA_I18N[key + '.' + c];"
        " return e ? (e[lang] || e.en || '') : ''; };"
        " var text = form(cat) || form('other') || '{n}';"
        " var fill = Object.assign({ n: n }, vars || {});"
        " for (var k in fill) text = text.split('{' + k + '}').join(String(fill[k]));"
        " return text; };\n"
        "const fs = require('fs');\n"
        f"eval(fs.readFileSync({json.dumps(str(_JS / 'locale_format.js'))}, 'utf8'));\n"
        f"eval(fs.readFileSync({json.dumps(str(_JS / 'settings_integrations.js'))}, 'utf8'));\n"
        "const m = window.KazmaSettingsMixins.integrations();\n"
        f"const states = {json.dumps(states, ensure_ascii=False)};\n"
        "console.log(JSON.stringify(states.map(function(s) { m.xReply = s;"
        " return {reason: m.xReplyLiveReason.call(m), hold: m.xReplyHoldText.call(m)}; })));\n"
    )
    out = subprocess.run(
        ["node", "-"], input=harness, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _page_text(lang: str, x_reply: dict) -> dict:
    return _page_texts(lang, [x_reply])[0]


_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _states() -> list[dict]:
    states = [
        {"live_reason_code": code, "live_reason_values": {"mode": "draft"}}
        for code in _codes()[0] if code not in ("live", "auto_held")
    ]
    for n in (1, 2, 5, 11):
        states.append({"live_reason_code": "live", "live_reason_values": {"mode": "auto", "n": n}})
    for hold in _codes()[1]:
        states.append({"live_reason_code": "auto_held", "live_reason_values": {"hold": hold},
                       "auto_qualification_hold_code": hold})
    return states


@_node
def test_every_state_reads_in_arabic_on_the_arabic_page():
    states = _states()
    for state, text in zip(states, _page_texts("ar", states)):
        assert text["reason"] and _ARABIC.search(text["reason"]), state
        assert not re.search(r"[A-Za-z]{4,}", text["reason"].replace("Kazma", "").replace("roast", "")), (
            state, text["reason"],
        )
        if state.get("auto_qualification_hold_code"):
            assert _ARABIC.search(text["hold"]), state
            assert text["hold"] in text["reason"]


@_node
def test_the_english_page_reads_the_servers_sentences():
    from kazma_ui.x_reply_api import _live_text

    states = _states()
    for state, text in zip(states, _page_texts("en", states)):
        values = dict(state["live_reason_values"])
        values.setdefault("mode", "draft")
        if state["live_reason_code"] == "policy_errors":
            # The errors themselves follow in their own span, as written.
            expected = _live_text("policy_errors", {"errors": "duplicate id x"})
            assert expected == text["reason"] + " duplicate id x"
            continue
        assert text["reason"] == _live_text(state["live_reason_code"], values), state


@_node
def test_arabic_counts_take_their_own_forms():
    two, eleven = _page_texts("ar", [
        {"live_reason_code": "live", "live_reason_values": {"mode": "draft", "n": n}}
        for n in (2, 11)
    ])
    assert "موضوعان" in two["reason"]
    assert "11 موضوعًا" in eleven["reason"]


# ── The template shows the worded text, never the server's English ────────


def _raw_server_english(template: str) -> list[str]:
    """Lines that print the server's English status or hold sentence."""
    return [
        line.strip() for line in template.splitlines()
        if re.search(r"x-text=\"[^\"]*xReply\.(live_reason|auto_qualification_hold)\b(?!_)", line)
    ]


def test_the_template_words_the_codes():
    template = _TEMPLATE.read_text(encoding="utf-8")
    assert _raw_server_english(template) == []
    assert "xReplyLiveReason()" in template
    assert "xReplyHoldText()" in template


def test_the_old_template_is_the_negative_control():
    """The lines the live Arabic page rendered in English."""
    old = (
        '<span translate="no" x-text="\' — \' + xReply.live_reason"></span>\n'
        '<span x-show="xReply.mode === \'auto\' && xReply.auto_qualification_hold" x-cloak\n'
        '      style="display:block;color:var(--warning,#c80);" x-text="xReply.auto_qualification_hold"></span>\n'
    )
    assert len(_raw_server_english(old)) == 2
