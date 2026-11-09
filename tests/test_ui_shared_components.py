"""The shell and the shared components, defined once, as the refresh drew them.

The UI refresh's stage 2 (2026-11-09) gave the shell (sidebar, header,
menus) and the shared components the owner's approved design, and five
page agents code against one contract at the same time
(``docs/plans/UI_DESIGN_REFRESH.md``): every shared class it names lives in
``kazma.css``, once, and a page uses it and never redefines it. These gates
hold that, each with a negative control that plants the violation:

- every shared class the contract names is defined in ``kazma.css``;
- ``kazma.v5.css`` (loaded last, so it would silently win) restyles none of
  them;
- the behaviours the contract describes: the active menu item and the
  chosen tab are a filled accent pill with the on-accent ink (not the old
  tint and gradient rail), the main button is filled and the rest soft, the
  destructive confirm is a filled danger, badges are full pills, a card
  does not lift on hover, and no title is gradient text;
- no rule in either stylesheet writes a literal colour outside the two
  token blocks, apart from the declared brand marks (a ratchet that may
  only go down).
"""

from __future__ import annotations

import re

from tests.test_ui_color_tokens import (
    KAZMA_CSS,
    V5_CSS,
    _read,
    _themes,
    _token_tables,
    parse_css,
    substitute,
)

#: Every class the stage-2 contract names, by family. A page agent uses
#: these and never redefines them.
SHARED_CLASSES = (
    # buttons
    "btn", "btn-primary", "btn-secondary", "btn-success", "btn-warning", "btn-danger",
    "btn-danger-solid", "btn-outline", "btn-ghost", "btn-sm", "btn-xs", "btn-lg", "btn-icon",
    # badges, with the legacy aliases
    "badge", "badge-success", "badge-warning", "badge-danger", "badge-info", "badge-accent",
    "badge-neutral", "badge-secondary", "badge-stdio", "badge-sse", "badge-premium",
    "badge-basic", "badge-standard",
    # tones and the tinted components
    "tone-accent", "tone-success", "tone-warning", "tone-danger", "tone-info", "tone-neutral",
    "icon-tile", "icon-tile-sm",
    "stat-tile", "stat-tile-icon", "stat-tile-label", "stat-tile-value",
    "page-banner", "page-banner-title", "page-banner-crumb",
    "notice", "alert", "alert-warning", "alert-success", "alert-danger", "alert-info",
    # cards
    "card", "card-header", "card-title", "card-sub", "card-nested",
    # forms
    "form-input", "form-select", "form-textarea", "form-label", "form-hint", "form-error",
    "toggle", "toggle-slider",
    # tabs, segments, tables, menus, states
    "tabs", "tab", "tab-btn", "segmented",
    "data-table", "table-container",
    "dropdown", "dropdown-item",
    "empty-state", "progress", "progress-bar",
)


def _class_re(cls: str) -> re.Pattern[str]:
    return re.compile(r"\." + re.escape(cls) + r"(?![\w-])")


def defined_classes(css: str) -> set[str]:
    """The shared classes some rule in *css* selects (outside @keyframes)."""
    found = set()
    for rule in parse_css(css):
        if "keyframes" in rule.ctx:
            continue
        joined = " , ".join(rule.selectors)
        for cls in SHARED_CLASSES:
            if _class_re(cls).search(joined):
                found.add(cls)
    return found


def test_every_shared_class_is_defined_in_kazma_css():
    missing = sorted(set(SHARED_CLASSES) - defined_classes(_read(KAZMA_CSS)))
    assert not missing, f"shared classes the contract names that kazma.css never defines: {missing}"


def test_negative_control_a_missing_shared_class_is_caught():
    css = _read(KAZMA_CSS)
    stripped = re.sub(r"\.btn-outline(?![\w-])", ".btn-planted-gone", css)
    assert "btn-outline" in set(SHARED_CLASSES) - defined_classes(stripped)


# ── v5 restyles no shared class ──────────────────────────────────────


def v5_redefinitions(v5: str) -> list[str]:
    """v5 rules whose first compound selector names a shared class.

    A descendant use (``.setup-wall-card .btn``) is a page's layout of the
    component and is allowed; ``.badge-premium { ... }`` or ``.toggle input
    + .toggle-slider`` restyles the component itself, and v5 loads last.
    """
    out = []
    for rule in parse_css(v5):
        if "keyframes" in rule.ctx:
            continue
        for sel in rule.selectors:
            first = re.split(r"\s|>|\+|~", sel.strip(), maxsplit=1)[0]
            first = re.sub(r"^\[dir=\"rtl\"\]|^html\[[^\]]*\]", "", first)
            for cls in SHARED_CLASSES:
                if _class_re(cls).search(first):
                    out.append(f"{sel} (names .{cls})")
    return out


def test_kazma_v5_restyles_no_shared_class():
    assert not v5_redefinitions(_read(V5_CSS))


def test_negative_control_a_v5_restyle_is_caught():
    planted = _read(V5_CSS) + "\n.badge-premium { background: var(--secondary-subtle); }\n"
    assert any("badge-premium" in p for p in v5_redefinitions(planted))


# ── The behaviours the contract describes ────────────────────────────


def _rule_decls(css: str, selector: str) -> dict[str, str]:
    """The declarations of every top-level rule naming *selector* exactly
    (later rules win, as in the cascade)."""
    decls: dict[str, str] = {}
    for rule in parse_css(css):
        if rule.ctx:
            continue
        if selector in rule.selectors:
            for d in rule.decls:
                decls[d.prop] = d.value
    return decls


def _resolves_to(value: str, token: str, table: dict[str, str]) -> bool:
    """True when *value* is, through var() substitution, exactly *token*'s value."""
    try:
        return substitute(value, table) == substitute(f"var({token})", table)
    except Exception:
        return False


def behaviour_problems(kazma: str, v5: str) -> list[str]:
    problems = []
    dark, light, _ = _token_tables(kazma)
    themes = _themes(dark, light)

    def expect(selector: str, prop: str, token: str) -> None:
        decls = _rule_decls(kazma, selector)
        value = decls.get(prop)
        if value is None:
            problems.append(f"{selector} sets no {prop}")
            return
        local = {k: v for k, v in decls.items() if k.startswith("--")}  # the rule's own (--btn-tint)
        for name, table in themes.items():
            if not _resolves_to(value, token, {**table, **local}):
                problems.append(f"{selector} {prop} is {value!r}, not {token} ({name})")

    # The active menu item: a filled accent pill with the on-accent ink.
    expect(".nav-link.active", "background", "--accent")
    expect(".nav-link.active", "color", "--on-accent")
    for parsed, name in ((parse_css(kazma), "kazma.css"), (parse_css(v5), "kazma.v5.css")):
        for rule in parsed:
            if any(s.startswith(".nav-link.active") for s in rule.selectors) and name == "kazma.v5.css":
                problems.append(f"{name} restyles the active menu item: {rule.selectors}")
            if any("nav-link.active::before" in s for s in rule.selectors) and rule.get("background"):
                problems.append(f"{name}: the active item still draws a rail ({rule.selectors})")

    # The chosen tab: the same filled pill.
    for sel in (".tab.active", ".tab-btn.active", ".settings-tab.active", '.tab[aria-selected="true"]'):
        expect(sel, "background", "--accent")
        expect(sel, "color", "--on-accent")

    # Buttons: the main one filled, the rest soft, the destructive confirm filled.
    expect(".btn-primary", "background", "--accent")
    expect(".btn-primary", "color", "--on-accent")
    for cls, tint, ink in ((".btn-secondary", "--accent-subtle", "--accent-light"),
                           (".btn-success", "--success-subtle", "--success"),
                           (".btn-warning", "--warning-subtle", "--warning"),
                           (".btn-danger", "--danger-subtle", "--danger")):
        expect(cls, "background", tint)
        expect(cls, "color", ink)
    expect(".btn-danger-solid", "background", "--danger")
    expect(".btn-danger-solid", "color", "--on-danger")

    # Badges are full pills.
    radius = _rule_decls(kazma, ".badge").get("border-radius", "")
    if substitute(radius, themes["dark"]) != "999px":
        problems.append(f".badge border-radius is {radius!r}, not a full pill")

    # A card does not move on hover; no title is gradient text.
    for parsed, name in ((parse_css(kazma), "kazma.css"), (parse_css(v5), "kazma.v5.css")):
        for rule in parsed:
            hovered_card = any(re.search(r"\.card:hover|\.metric-card:hover|\.stat-card:hover|\.provider-card:hover", s)
                               for s in rule.selectors)
            if hovered_card and (rule.get("transform") or rule.get("translate")):
                problems.append(f"{name}: a card lifts on hover ({rule.selectors})")
            if rule.get("background-clip") == "text" or rule.get("-webkit-background-clip") == "text":
                problems.append(f"{name}: gradient text on {rule.selectors}")
    return problems


def test_the_shell_and_components_behave_as_the_contract_says():
    assert not behaviour_problems(_read(KAZMA_CSS), _read(V5_CSS))


def test_negative_control_the_old_active_item_and_a_lifting_card_are_caught():
    kazma, v5 = _read(KAZMA_CSS), _read(V5_CSS)
    old_active = v5 + "\n.nav-link.active { background: var(--accent-subtle); color: var(--accent-light); }\n"
    assert any("restyles the active menu item" in p for p in behaviour_problems(kazma, old_active))
    tinted = kazma + "\n.nav-link.active { background: var(--accent-subtle); color: var(--accent-light); }\n"
    assert any(".nav-link.active background" in p for p in behaviour_problems(tinted, v5))
    lifting = kazma + "\n.card:hover { transform: translateY(-4px); }\n"
    assert any("lifts on hover" in p for p in behaviour_problems(lifting, v5))
    grey = kazma + "\n.btn-secondary { background: var(--bg-panel); color: var(--text-secondary); }\n"
    assert any(".btn-secondary background" in p for p in behaviour_problems(grey, v5))
    gradient = kazma + "\n.page-title { background-clip: text; }\n"
    assert any("gradient text" in p for p in behaviour_problems(gradient, v5))


# ── Soft buttons read 4.5:1 on a tinted surface too ──────────────────

#: Soft buttons and their ink.
SOFT_BUTTONS = {".btn-secondary": "--accent-light", ".btn-success": "--success",
                ".btn-warning": "--warning", ".btn-danger": "--danger"}
#: The tinted surfaces a soft button sits on (an approval card, notices,
#: the page banner, stat tiles).
TINTED_SURFACES = ("--warning-subtle", "--danger-subtle", "--success-subtle", "--info-subtle",
                   "--accent-subtle", "--neutral-subtle")
_ON_A_TINT_SELECTORS = (".hitl-approval-card", ".turn-approvals", ".notice", ".page-banner", ".stat-tile")


#: Badges and their ink: the same wash on the same surfaces.
SOFT_BADGES = {".badge-accent": "--accent-light", ".badge-success": "--success",
               ".badge-warning": "--warning", ".badge-danger": "--danger",
               ".badge-info": "--info", ".badge-neutral": "--neutral"}


def wall_under_the_tint(kazma: str, things: dict[str, str], var: str) -> bool:
    """A rule lays the tint (*var*) over the wall inside the tinted surfaces."""
    for rule in parse_css(kazma):
        if rule.ctx:
            continue
        sel = " , ".join(rule.selectors)
        names = list(things) if var == "--btn-tint" else [".badge"]
        if all(s in sel for s in _ON_A_TINT_SELECTORS) and all(b in sel for b in names):
            bg = rule.get("background") or ""
            if f"var({var})" in bg and "var(--surface-card)" in bg:
                return True
    return False


def soft_button_problems(kazma: str, minimum: float = 4.5) -> list[str]:
    from tests.test_ui_color_tokens import colour, contrast, over, parse_colour

    dark, light, _ = _token_tables(kazma)
    themes = _themes(dark, light)
    problems = []
    for things, var in ((SOFT_BUTTONS, "--btn-tint"), (SOFT_BADGES, "--badge-tint")):
        based = wall_under_the_tint(kazma, things, var)
        if not based:
            problems.append(f"no rule lays {var} over the wall on a tinted surface")
        problems += _tint_problems(kazma, things, var, based, themes, minimum,
                                   colour, contrast, over, parse_colour)
    return problems


def _tint_problems(kazma, things, var, based, themes, minimum, colour, contrast, over, parse_colour):
    problems = []
    for selector, ink in things.items():
        tints = [_rule_decls(kazma, selector).get(var)]
        if var == "--btn-tint":
            # A hover may keep the tint (and ring the button); a hover that
            # deepens it must still read.
            hover = _rule_decls(kazma, selector + ":hover:not(:disabled)")
            if hover.get("background"):
                problems.append(f"{selector}:hover paints its own background (set {var})")
            if hover.get(var):
                tints.append(hover[var])
        for name, table in themes.items():
            card = colour(table, "--surface-card")
            for tint_value in tints:
                if not tint_value:
                    problems.append(f"{selector}: no {var}")
                    continue
                tint = parse_colour(substitute(tint_value, table))
                for surface in (None,) + TINTED_SURFACES:
                    under = card if (surface is None or based) else over(colour(table, surface), card)
                    bg = over(tint, under)
                    r = contrast(over(colour(table, ink), bg), bg)
                    if r < minimum:
                        problems.append(f"{name}: {selector} ({tint_value}) on {surface or 'the card'} {r:.2f}:1")
    return problems


def test_soft_buttons_and_badges_read_on_every_tinted_surface():
    assert not soft_button_problems(_read(KAZMA_CSS))


def test_negative_control_two_washes_stacked_are_caught():
    """Without the wall under the tint: the approval card's Approve in light,
    and a success badge on the page banner (3.96:1, the Dashboard's banner)."""
    kazma = _read(KAZMA_CSS).replace("var(--btn-tint)), var(--surface-card)", "var(--btn-tint))")
    problems = soft_button_problems(kazma)
    assert any("no rule lays --btn-tint" in p for p in problems)
    assert any("light: .btn-success" in p and "--warning-subtle" in p for p in problems), problems
    kazma = _read(KAZMA_CSS).replace("var(--badge-tint)), var(--surface-card)", "var(--badge-tint))")
    problems = soft_button_problems(kazma)
    assert any("no rule lays --badge-tint" in p for p in problems)
    assert any("light: .badge-success" in p and "--accent-subtle" in p for p in problems), problems


# ── No literal colour outside the token blocks (a ratchet) ───────────

#: Brand colours of other products, the one literal colour a rule may
#: write: each selector with why.
BRAND_MARKS = {
    ".dot-telegram": "Telegram's brand blue (a chat's platform dot)",
    ".dot-discord": "Discord's brand blurple (a chat's platform dot)",
    ".dot-slack": "Slack's brand aubergine (a chat's platform dot)",
}

#: Literal colours left in the two stylesheets outside the token blocks
#: and the brand marks. A ratchet: it may only go down (it was 91 before
#: the 2026-11-09 shell refresh, the brand marks included).
LITERAL_BASELINE = 0

_LITERAL = re.compile(
    r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\((?![^)]*var\()[^)]*\)"
    r"|(?<![\w-])(?:white|black|red|green|blue|yellow|orange|purple|gray|grey|pink|silver|navy|teal"
    r"|cyan|magenta|lime|maroon|olive|aqua|fuchsia)(?![\w-])",
    re.I,
)


def literal_colours(kazma: str, v5: str) -> list[str]:
    out = []
    for name, text in (("kazma.css", kazma), ("kazma.v5.css", v5)):
        for rule in parse_css(text):
            token_block = not rule.ctx and rule.selectors in ([":root"], ['[data-theme="light"]'])
            if not rule.ctx and len(rule.selectors) == 1 and rule.selectors[0] in BRAND_MARKS:
                continue
            for d in rule.decls:
                if token_block and d.prop.startswith("--"):
                    continue
                for m in _LITERAL.finditer(d.value):
                    out.append(f"{name}: {', '.join(rule.selectors)} {{ {d.prop}: {d.value} }} [{m.group(0)}]")
    return out


def test_no_literal_colour_outside_the_token_blocks():
    found = literal_colours(_read(KAZMA_CSS), _read(V5_CSS))
    assert len(found) <= LITERAL_BASELINE, found


def test_the_declared_brand_marks_exist():
    rules = {s for r in parse_css(_read(KAZMA_CSS)) for s in r.selectors}
    stale = [sel for sel in BRAND_MARKS if sel not in rules]
    assert not stale, f"brand marks declared but no longer in kazma.css: {stale}"


def test_negative_control_a_planted_literal_is_caught():
    kazma = _read(KAZMA_CSS) + "\n.planted-card { background: #123456; }\n"
    assert any("#123456" in f for f in literal_colours(kazma, _read(V5_CSS)))
    v5 = _read(V5_CSS) + "\n.planted { color: var(--warning, #f59e0b); }\n"
    assert any("#f59e0b" in f for f in literal_colours(_read(KAZMA_CSS), v5))
