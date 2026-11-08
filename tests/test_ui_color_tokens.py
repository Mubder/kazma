"""The web UI's colours come from one token set, and it holds (2026-11-09).

The owner saw different colours on different pages, and cards out of step
with the page behind them. Three audits found why (scratch reports, not in
the repository): the light theme put ``--bg-elevated``/``--bg-card``/
``--bg-2`` BELOW the wall while dark put them above every card, so a box was
raised in one theme and sunken in the other; card families each picked a
different surface token; templates and scripts used tokens no stylesheet
defined (``--text``, ``--warn``, ``--primary``...), which fell back to a
literal or to nothing; the light status colours were 2.9-4.1:1 as text; and
the accent setting moved four of the dozen accent shades.

The scale now follows the owner's reference design: a card is the wall's
own colour, set apart by a hairline and a soft shadow; overlays and chrome
are the wall too; the well (code, a table head) is the one other surface,
and it sits below the wall in both themes. Each gate here has a negative
control that plants the violation and shows the gate catches it:

- every ``var(--x)`` the stylesheets, templates and page scripts use is
  defined (a stylesheet, a template's own block, or a declared runtime
  property set by script);
- every literal colour token has a light value, and no token is declared
  under ``prefers-color-scheme`` (``data-theme`` always decides);
- the surface order in each theme: card, overlay and chrome equal the wall,
  the well is below it, inputs are transparent;
- the one card rule paints every card family, with the shadow token, and no
  family rule paints its own surface;
- text, status and accent colours read 4.5:1 on the card, on their own 12%
  tint, and ink on every fill (both themes, and for every chosen accent);
- no selector is styled in both kazma.css and kazma.v5.css with different
  colour values (a ratchet at 0);
- the owner's accent: the server's theme variants equal the page script's
  (a shared fixture), the default is "none", and the retired indigo
  (``#5e6ad2``) a Save stored is retired.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "kazma-ui" / "kazma_ui"
CSS_DIR = UI / "static" / "css"
KAZMA_CSS = CSS_DIR / "kazma.css"
V5_CSS = CSS_DIR / "kazma.v5.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# ── A small CSS reader: rules with their selectors and at-rule context ──


@dataclass
class Decl:
    prop: str
    value: str
    important: bool


@dataclass
class Rule:
    selectors: list[str]
    ctx: str
    decls: list = field(default_factory=list)

    def get(self, prop: str) -> str | None:
        for d in reversed(self.decls):
            if d.prop == prop:
                return d.value
        return None


def parse_css(text: str) -> list[Rule]:
    t = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), text, flags=re.S)
    rules: list[Rule] = []
    stack: list[tuple[str, object]] = []
    buf, paren, quote = 0, 0, None
    i = 0
    while i < len(t):
        c = t[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "(":
            paren += 1
        elif c == ")":
            paren -= 1
        elif paren <= 0 and c == "{":
            pre = " ".join(t[buf:i].split())
            if pre.startswith("@"):
                stack.append(("at", pre))
            else:
                ctx = "|".join(str(p) for k, p in stack if k == "at")
                rule = Rule([" ".join(s.split()) for s in pre.split(",")], ctx)
                rules.append(rule)
                stack.append(("rule", rule))
            buf = i + 1
        elif paren <= 0 and c in ";}":
            if stack and stack[-1][0] == "rule":
                chunk = t[buf:i]
                if ":" in chunk and chunk.strip():
                    prop, _, val = chunk.partition(":")
                    val = " ".join(val.split())
                    imp = bool(re.search(r"!\s*important\s*$", val, re.I))
                    val = re.sub(r"\s*!\s*important\s*$", "", val, flags=re.I)
                    rule = stack[-1][1]
                    assert isinstance(rule, Rule)
                    rule.decls.append(Decl(prop.strip().lower(), val, imp))
            if c == "}" and stack:
                stack.pop()
            buf = i + 1
        i += 1
    return rules


def _token_tables(text: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """(dark :root tokens, light tokens, tokens declared under prefers-color-scheme)."""
    dark: dict[str, str] = {}
    light: dict[str, str] = {}
    media: list[str] = []
    for rule in parse_css(text):
        props = {d.prop: d.value for d in rule.decls if d.prop.startswith("--")}
        if not props:
            continue
        if "prefers-color-scheme" in rule.ctx:
            media.extend(props)
            continue
        if rule.ctx:
            continue
        if rule.selectors == [":root"]:
            dark.update(props)
        elif rule.selectors == ['[data-theme="light"]']:
            light.update(props)
    return dark, light, media


def _themes(dark: dict[str, str], light: dict[str, str], extra: dict[str, str] | None = None):
    d = dict(dark)
    lt = dict(dark)
    lt.update(light)
    if extra:
        d.update(extra)
        lt.update(extra)
    return {"dark": d, "light": lt}


# ── Resolving a token to a colour ──


class Undefined(Exception):
    pass


def _split_args(s: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return out


def substitute(value: str, table: dict[str, str], depth: int = 0) -> str:
    if depth > 40:
        raise Undefined("var() cycle")
    while True:
        at = value.find("var(")
        if at < 0:
            return value
        j, level = at + 4, 1
        while level:
            if value[j] == "(":
                level += 1
            elif value[j] == ")":
                level -= 1
            j += 1
        args = _split_args(value[at + 4:j - 1])
        name = args[0]
        fallback = ",".join(args[1:]).strip() if len(args) > 1 else None
        if name in table:
            rep = substitute(table[name], table, depth + 1)
        elif fallback is not None:
            rep = substitute(fallback, table, depth + 1)
        else:
            raise Undefined(name)
        value = value[:at] + rep + value[j:]


RGBA = tuple  # (r, g, b, a) with r, g, b in 0..255, a in 0..1


def parse_colour(text: str) -> RGBA:
    s = text.strip().lower()
    if s == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
    if s == "white":
        return (255.0, 255.0, 255.0, 1.0)
    if s == "black":
        return (0.0, 0.0, 0.0, 1.0)
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})", s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)), a)
    m = re.fullmatch(r"rgba?\((.*)\)", s)
    if m:
        parts = [p.strip() for p in re.split(r"[,\s/]+", m.group(1)) if p.strip()]
        r, g, b = (float(x) for x in parts[:3])
        a = float(parts[3].rstrip("%")) / (100 if parts[3].endswith("%") else 1) if len(parts) > 3 else 1.0
        return (r, g, b, a)
    m = re.fullmatch(r"color-mix\(in srgb,(.*)\)", s)
    if m:
        a_arg, b_arg = _split_args(m.group(1))

        def _part(arg: str) -> tuple[RGBA, float | None]:
            pm = re.search(r"\s(\d+(?:\.\d+)?)%\s*$", " " + arg)
            if pm:
                return parse_colour(arg[: arg.rfind(pm.group(1))].strip()), float(pm.group(1)) / 100
            return parse_colour(arg), None

        (c1, p1), (c2, p2) = _part(a_arg), _part(b_arg)
        if p1 is None and p2 is None:
            p1 = p2 = 0.5
        elif p1 is None:
            p1 = 1 - p2
        elif p2 is None:
            p2 = 1 - p1
        alpha = c1[3] * p1 + c2[3] * p2
        if alpha == 0:
            return (0.0, 0.0, 0.0, 0.0)
        rgb = tuple((c1[i] * c1[3] * p1 + c2[i] * c2[3] * p2) / alpha for i in range(3))
        return (*rgb, alpha)
    raise ValueError(f"not a colour: {text!r}")


def colour(table: dict[str, str], token: str) -> RGBA:
    return parse_colour(substitute(f"var({token})", table))


def over(top: RGBA, under: RGBA) -> RGBA:
    a = top[3]
    return tuple(top[i] * a + under[i] * (1 - a) for i in range(3)) + (1.0,)


def luminance(c: RGBA) -> float:
    def ch(v: float) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast(a: RGBA, b: RGBA) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _same(a: RGBA, b: RGBA) -> bool:
    return all(abs(x - y) < 0.6 for x, y in zip(a[:3], b[:3])) and abs(a[3] - b[3]) < 0.01


@pytest.fixture(scope="module")
def tables():
    dark, light, media = _token_tables(_read(KAZMA_CSS))
    _, _, v5_media = _token_tables(_read(V5_CSS))
    return dark, light, media + v5_media


# ── 1. Every var(--x) used is defined ────────────────────────────────

#: Custom properties a script (or the server, on <html>) sets at run time;
#: the stylesheet reads each with a fallback. Each must still appear, as
#: written, in the file named.
RUNTIME_PROPERTIES = {
    "--app-ivh": "kazma-ui/kazma_ui/static/js/app.js",
    "--ide-tree-w": "kazma-ui/kazma_ui/static/js/ide.js",
    "--ide-chat-w": "kazma-ui/kazma_ui/static/js/ide.js",
    "--accent-chosen-dark": "kazma-ui/kazma_ui/accent_colors.py",
    "--accent-chosen-dark-rgb": "kazma-ui/kazma_ui/accent_colors.py",
    "--accent-chosen-dark-on": "kazma-ui/kazma_ui/accent_colors.py",
    "--accent-chosen-light": "kazma-ui/kazma_ui/accent_colors.py",
    "--accent-chosen-light-rgb": "kazma-ui/kazma_ui/accent_colors.py",
    "--accent-chosen-light-on": "kazma-ui/kazma_ui/accent_colors.py",
}

_USE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]*[A-Za-z0-9_])\s*[,)]")
_DEF = re.compile(r"(?<![\w-])(--[A-Za-z0-9_-]+)\s*:")


def _sources() -> dict[str, str]:
    files = [KAZMA_CSS, V5_CSS]
    files += sorted((UI / "templates").rglob("*.html"))
    files += sorted(p for p in (UI / "static" / "js").rglob("*.js") if not p.name.endswith(".min.js"))
    return {str(p.relative_to(REPO)).replace("\\", "/"): _read(p) for p in files}


def undefined_uses(sources: dict[str, str]) -> dict[str, list[str]]:
    defined = set(RUNTIME_PROPERTIES)
    for name, text in sources.items():
        if name.endswith((".css", ".html")):
            defined |= set(_DEF.findall(text))
    missing: dict[str, list[str]] = defaultdict(list)
    for name, text in sources.items():
        for token in _USE.findall(text):
            if token not in defined:
                missing[token].append(name)
    return dict(missing)


def test_every_var_used_is_defined():
    missing = undefined_uses(_sources())
    assert not missing, f"var() names no stylesheet, template or declared runtime property defines: {missing}"


def test_runtime_properties_are_really_set_where_declared():
    for prop, where in RUNTIME_PROPERTIES.items():
        assert prop in _read(REPO / where), f"{prop}: declared as set by {where}, which never names it"


def test_negative_control_an_undefined_token_is_caught():
    sources = _sources()
    sources["planted.html"] = '<div style="color: var(--kazma-planted-nothing)"></div>'
    assert "--kazma-planted-nothing" in undefined_uses(sources)


# ── 2. Both themes: every literal colour token has a light value ─────

#: Literal colour tokens that are the same in both themes on purpose.
THEME_INDEPENDENT = {
    "--card-shadow": "measured from the reference design: the same layered grey shadow in both themes",
}
_COLOURISH = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(|color-mix\(")


def theme_gaps(dark: dict[str, str], light: dict[str, str]) -> list[str]:
    gaps = []
    for name, value in dark.items():
        if "var(" in value or not _COLOURISH.search(value):
            continue  # derived from another token, or not a colour
        if name not in light and name not in THEME_INDEPENDENT:
            gaps.append(f"{name}: {value} has no light value")
    gaps += [f"{name}: only in the light theme" for name in light if name not in dark]
    return gaps


def test_every_colour_token_has_both_themes(tables):
    dark, light, media = tables
    assert not theme_gaps(dark, light)
    assert not media, f"tokens under prefers-color-scheme (data-theme decides; a stale copy): {media}"


def test_negative_control_a_dark_only_colour_is_caught(tables):
    dark, light, _ = tables
    planted = dict(dark, **{"--planted-dark-only": "#123456"})
    assert any("--planted-dark-only" in g for g in theme_gaps(planted, light))
    planted_media = _token_tables(_read(KAZMA_CSS) + "\n@media (prefers-color-scheme: light) { :root:not([data-theme=\"dark\"]) { --bg: #fff; } }\n")[2]
    assert planted_media == ["--bg"]


# ── 3. The surface order in each theme ──────────────────────────────


def surface_problems(themes: dict[str, dict[str, str]]) -> list[str]:
    problems = []
    for name, table in themes.items():
        wall = colour(table, "--surface-wall")
        if wall[3] < 1:
            problems.append(f"{name}: the wall is not opaque")
        for token in ("--surface-card", "--surface-overlay", "--surface-chrome", "--bg", "--bg-panel",
                      "--bg-surface", "--bg-elevated", "--bg-card", "--bg-primary"):
            if not _same(colour(table, token), wall):
                problems.append(f"{name}: {token} is not the wall (cards are set apart by shadow, not fill)")
        sunken = colour(table, "--surface-sunken")
        if sunken[3] < 1 or luminance(sunken) >= luminance(wall):
            problems.append(f"{name}: --surface-sunken is not below the wall")
        for token in ("--bg-2", "--bg-deep"):
            if not _same(colour(table, token), sunken):
                problems.append(f"{name}: {token} is not the well")
        if colour(table, "--surface-raised")[3] != 0:
            problems.append(f"{name}: --surface-raised (inputs) is not transparent")
        if colour(table, "--scrim")[3] <= 0:
            problems.append(f"{name}: --scrim is clear")
    return problems


def test_surface_order_in_each_theme(tables):
    dark, light, _ = tables
    assert not surface_problems(_themes(dark, light))


def test_negative_control_the_old_inverted_scale_is_caught(tables):
    """The old light table: --bg-elevated below the wall, cards white on ice blue."""
    dark, light, _ = tables
    old_light = dict(light, **{"--surface-wall": "#f0f4fa", "--bg-elevated": "#e6edf7", "--surface-card": "#ffffff"})
    problems = surface_problems(_themes(dark, old_light))
    assert any("light: --surface-card" in p for p in problems)
    assert any("light: --bg-elevated" in p for p in problems)
    old_dark = dict(dark, **{"--surface-sunken": "#121b2e"})  # above the dark wall
    assert any("dark: --surface-sunken" in p for p in surface_problems(_themes(old_dark, light)))


# ── 4. The one card rule ────────────────────────────────────────────

CARD_FAMILIES = (
    ".card", ".metric-card", ".chart-card", ".skill-card", ".mcp-card", ".provider-card",
    ".pkg-metric", ".pkg-layer-card", ".x-control-hero", ".setup-wall-card", ".dash-mobile-card",
    ".settings-group", ".pc-panel", ".ide-review",
)
NESTED_CARDS = (".settings-group", ".pc-panel", ".ide-review")
_SURFACE_PROPS = ("background", "background-color", "border", "border-color", "border-radius", "box-shadow")


def card_rule_problems(kazma: str, v5: str) -> list[str]:
    problems = []
    rules = parse_css(kazma)
    main = [r for r in rules if not r.ctx and set(CARD_FAMILIES) <= set(r.selectors)]
    if len(main) != 1:
        return [f"expected one card rule naming every family, found {len(main)}"]
    card = main[0]
    want = {
        "background": "var(--surface-card)",
        "border-radius": "var(--card-radius)",
        "box-shadow": "var(--card-shadow)",
    }
    for prop, value in want.items():
        if card.get(prop) != value:
            problems.append(f"card rule {prop} is {card.get(prop)!r}, not {value}")
    if "var(--card-border)" not in (card.get("border") or ""):
        problems.append("card rule border does not use --card-border")
    nested = [r for r in rules if not r.ctx and set(r.selectors) == set(NESTED_CARDS)]
    if len(nested) != 1 or nested[0].get("border-color") != "var(--card-border-nested)":
        problems.append("nested cards do not take the hairline (--card-border-nested)")
    allowed = {id(card)} | {id(r) for r in nested}
    for name, parsed in (("kazma.css", rules), ("kazma.v5.css", parse_css(v5))):
        for rule in parsed:
            if id(rule) in allowed:
                continue
            hit = [s for s in rule.selectors if s in CARD_FAMILIES]
            if not hit:
                continue
            painted = [d.prop for d in rule.decls if d.prop in _SURFACE_PROPS]
            if painted:
                problems.append(f"{name}: {hit} paints its own surface ({painted}) -- the card rule decides")
    return problems


def test_one_card_rule_with_the_shadow():
    assert not card_rule_problems(_read(KAZMA_CSS), _read(V5_CSS))


def test_negative_control_a_family_painting_itself_is_caught():
    kazma, v5 = _read(KAZMA_CSS), _read(V5_CSS)
    assert any(".skill-card" in p for p in card_rule_problems(kazma + "\n.skill-card { background: var(--tint); }\n", v5))
    assert any("box-shadow" in p for p in card_rule_problems(kazma.replace(
        "  box-shadow: var(--card-shadow);\n}\n.settings-group,", "}\n.settings-group,", 1), v5))


# ── 5. Contrast: text, status, accent and ink ───────────────────────

TEXT_ON_CARD = ("--text-primary", "--text-secondary", "--text-tertiary", "--text-muted",
                "--success", "--warning", "--danger", "--info", "--accent-light",
                "--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5", "--cat-6")
TEXT_ON_WELL = ("--text-primary", "--text-secondary", "--text-tertiary", "--text-muted")
ON_OWN_TINT = (("--success", "--success-subtle"), ("--warning", "--warning-subtle"),
               ("--danger", "--danger-subtle"), ("--info", "--info-subtle"),
               ("--accent-light", "--accent-subtle"))
INK_ON_FILL = (("--on-accent", "--accent"), ("--on-success", "--success"), ("--on-danger", "--danger"),
               ("--on-warning", "--warning"), ("--on-info", "--info"), ("--on-secondary", "--secondary"),
               ("--text-inverse", "--accent"))


def contrast_problems(themes: dict[str, dict[str, str]], minimum: float = 4.5) -> list[str]:
    problems = []
    for name, table in themes.items():
        card = colour(table, "--surface-card")
        well = colour(table, "--surface-sunken")
        for token in TEXT_ON_CARD:
            r = contrast(over(colour(table, token), card), card)
            if r < minimum:
                problems.append(f"{name}: {token} on the card {r:.2f}:1")
        for token in TEXT_ON_WELL:
            r = contrast(over(colour(table, token), well), well)
            if r < minimum:
                problems.append(f"{name}: {token} on the well {r:.2f}:1")
        for text, tint in ON_OWN_TINT:
            bg = over(colour(table, tint), card)
            r = contrast(over(colour(table, text), bg), bg)
            if r < minimum:
                problems.append(f"{name}: {text} on {tint} {r:.2f}:1")
        for ink, fill in INK_ON_FILL:
            bg = over(colour(table, fill), card)
            r = contrast(over(colour(table, ink), bg), bg)
            if r < minimum:
                problems.append(f"{name}: {ink} on {fill} {r:.2f}:1")
    return problems


def test_text_status_and_ink_read_in_both_themes(tables):
    dark, light, _ = tables
    assert not contrast_problems(_themes(dark, light))


def test_negative_control_the_old_light_status_colours_are_caught(tables):
    dark, light, _ = tables
    old = dict(light, **{"--warning": "#ca8a04", "--success": "#059669"})
    problems = contrast_problems(_themes(dark, old))
    assert any("light: --warning on the card" in p for p in problems)
    assert any("light: --success on the card" in p for p in problems)
    old_ink = dict(dark, **{"--on-accent": "#ffffff"})  # white on the dark-theme blue: 3.68:1
    assert any("dark: --on-accent on --accent" in p for p in contrast_problems(_themes(old_ink, light)))


# ── 6. The owner's accent ───────────────────────────────────────────

FIXTURE = json.loads((REPO / "tests" / "fixtures" / "accent_variants.json").read_text(encoding="utf-8"))


def test_server_accent_variants_match_the_shared_fixture():
    from kazma_ui.accent_colors import _variants as accent_variants
    from kazma_ui.accent_colors import accent_style

    for hex_value, want in FIXTURE["cases"].items():
        assert accent_variants(hex_value) == want, hex_value
    for bad in FIXTURE["not_chosen"]:
        assert accent_variants(bad) is None
        assert accent_style(bad) == ""
    style = accent_style("#10b981")
    assert style.startswith("--accent-chosen-dark:") and "--accent" + ":" not in style


def test_every_chosen_accent_reads_in_both_themes(tables):
    dark, light, _ = tables
    for hex_value, props in FIXTURE["cases"].items():
        themes = _themes(dark, light, props)
        problems = []
        for name, table in themes.items():
            card = colour(table, "--surface-card")
            accent = colour(table, "--accent")
            if contrast(colour(table, "--on-accent"), accent) < 4.5:
                problems.append(f"{name}: ink on the accent")
            if contrast(accent, card) < 3.0:
                problems.append(f"{name}: the accent against the card")
            if contrast(over(colour(table, "--accent-light"), card), card) < 4.5:
                problems.append(f"{name}: accent text on the card")
        assert not problems, f"{hex_value}: {problems}"


def test_negative_control_an_unadjusted_accent_is_caught(tables):
    """Setting --accent itself (the old way) puts one hex in both themes."""
    dark, light, _ = tables
    raw = {"--accent-chosen-dark": "#93c5fd", "--accent-chosen-dark-on": "#040815",
           "--accent-chosen-light": "#93c5fd", "--accent-chosen-light-on": "#ffffff"}
    table = _themes(dark, light, raw)["light"]
    assert contrast(colour(table, "--on-accent"), colour(table, "--accent")) < 4.5


def test_the_default_accent_is_none(tmp_path):
    from kazma_core.config_store import ConfigStore
    from kazma_core.settings_manager import DEFAULT_APPEARANCE, SettingsManager

    assert DEFAULT_APPEARANCE["accent_color"] == ""
    store = ConfigStore(db_path=str(tmp_path / "settings.db"))
    try:
        sm = SettingsManager(store)
        got = sm.get_appearance()
        assert got["accent_color"] == "" and got["accent_color_chosen"] is False
        sm.save_appearance({"accent_color": "#10b981"})
        got = sm.get_appearance()
        assert got["accent_color"] == "#10b981" and got["accent_color_chosen"] is True
        sm.save_appearance({"accent_color": ""})  # back to the theme's accent
        assert store.get("appearance.accent_color", None) is None
        assert sm.get_appearance()["accent_color_chosen"] is False
        store.set("appearance.accent_color", "not a colour", category="appearance")
        assert sm.get_appearance()["accent_color_chosen"] is False
    finally:
        store.close()


def test_the_stored_indigo_default_is_retired(tmp_path):
    from kazma_core.config_store import ConfigStore

    (tmp_path / "kazma.yaml").write_text("ui:\n  port: 9090\n", encoding="utf-8")
    store = ConfigStore(db_path=str(tmp_path / "settings.db"), yaml_path=str(tmp_path / "kazma.yaml"))
    try:
        store.set("appearance.accent_color", "#5e6ad2", category="appearance")
        store.reconcile_from_yaml()
        assert store.get("appearance.accent_color", None) is None, "a stored retired indigo follows"
    finally:
        store.close()
    other = tmp_path / "other"
    other.mkdir()
    (other / "kazma.yaml").write_text("ui:\n  port: 9090\n", encoding="utf-8")
    store = ConfigStore(db_path=str(other / "settings.db"), yaml_path=str(other / "kazma.yaml"))
    try:
        store.set("appearance.accent_color", "#10b981", category="appearance")
        store.reconcile_from_yaml()
        assert store.get("appearance.accent_color", None) == "#10b981", "an owner's own accent is kept"
    finally:
        store.close()


# ── 7. kazma.css and kazma.v5.css never disagree on a selector ──────

_COLOUR_PROP = re.compile(
    r"^(color|background(-color|-image)?|border(-(top|right|bottom|left|inline-start|inline-end|block-start|block-end))?(-color)?"
    r"|outline(-color)?|box-shadow|-webkit-text-fill-color|fill|stroke|text-decoration-color)$"
)
#: Selector + property pairs styled in both files with different colour
#: values. A ratchet: it may only go down (it was 29 selectors before the
#: 2026-11-09 cleanup).
CROSS_FILE_BASELINE = 0


def cross_file_conflicts(kazma: str, v5: str) -> list[str]:
    values: dict[tuple[str, str, str], dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for name, text in (("kazma.css", kazma), ("kazma.v5.css", v5)):
        for rule in parse_css(text):
            for sel in rule.selectors:
                for d in rule.decls:
                    if _COLOUR_PROP.match(d.prop):
                        values[(sel, rule.ctx, d.prop)][name].add(d.value)
    out = []
    for (sel, ctx, prop), by_file in values.items():
        if len(by_file) == 2 and by_file["kazma.css"] != by_file["kazma.v5.css"]:
            out.append(f"{sel} {prop}{' @' + ctx if ctx else ''}: {dict(by_file)}")
    return out


def test_no_selector_disagrees_across_the_two_stylesheets():
    conflicts = cross_file_conflicts(_read(KAZMA_CSS), _read(V5_CSS))
    assert len(conflicts) <= CROSS_FILE_BASELINE, conflicts


def test_negative_control_a_cross_file_disagreement_is_caught():
    v5 = _read(V5_CSS) + "\n.card { background: #ff0000; }\n"
    assert any(c.startswith(".card background") for c in cross_file_conflicts(_read(KAZMA_CSS), v5))
