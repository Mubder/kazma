<!-- AGENTS.md, piece ui-2-look-and-components. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **A save the server accepted never reports failure.** Only the request
  sits in the save's `try`; applying the result to the page is a separate
  step (`tests/js/test_settings_saves_report_truth.js` runs every Settings
  save against an accepting server).
- **Toasts:** use `window.showToast(msg, type, duration)` or
  `Alpine.store('toast').add(...)`. `streaming.js`'s `KazmaStream.toast`
  delegates to `$store.toast` — there is one toast system. `add(message,
  type, duration, action)` takes an optional `{label, run}` button (the
  toast closes after it); `duration` 0 stays until answered.
- **An open tab is told when Kazma was updated** (`modules/build_watch.js`,
  2026-10-16): the page carries the build that rendered it
  (`<meta name="kazma-build">`, the `build_commit()` template global,
  resolved at process start) and, when visible and every 5 minutes, compares
  it with `/health/live`'s build through `kazmaGetJson`; another build shows
  ONE "Kazma was updated -- Refresh" toast per tab. An unknown build, a
  restart of the same build or a server not answering shows nothing. Never
  scan the asset files on `/health/live`: it is the guard's probe, on the
  loop. Gates: `tests/js/test_build_watch.js`, `tests/e2e/test_update_notice.py`
  (EN/AR, after a sidebar navigation, Refresh reloads).
- **Kazma's mark has a face per theme** (2026-11-20, the owner's logo,
  `scripts/make_app_icons.py`, from `static/img/brand/kazma-logo-master.webp`):
  the dark theme shows the artwork as drawn (`kazma-logo-dark-theme.png`,
  `kazma-icon-dark-theme.png`), the light theme a two-tone mark whose white
  strokes are navy ink and blue strokes as drawn (`kazma-logo.png`,
  `kazma-icon.png`; the plain names are the one that reads on either page,
  so a place with one image uses it). Every place that shows it holds both
  `<img>`s, classed `brand-on-dark` / `brand-on-light`, and `kazma.css` hides
  the other theme's; chat.js draws it only through `_kazmaMarkHtml`. The
  website's first light mark recoloured every stroke blue, and the owner
  asked why the logo was one solid blue. The favicon and app icons are the
  artwork on the dark rounded square. Gates: `tests/test_logo_by_theme.py`
  (the website's recolour and the artwork as negative controls),
  `tests/e2e/test_sidebar_brand_and_build.py` (by the sidebar, EN/AR, both
  themes; also the sidebar card's release, build and start time, and the
  hover backdrop).
- **One colour system: a card is one step above the wall** (2026-11-09, the
  owner's refresh, `docs/plans/UI_DESIGN_REFRESH.md`; raised 2026-11-17 to
  the look kazma.ai took the day before). Every colour is a token in
  `kazma.css` (dark in `:root`, light in `[data-theme="light"]`): the surface
  scale -- the wall is the page (`#080d18` dark, `#f4f7fd` light) and the
  chrome (sidebar, header) is the wall; `--surface-card` is one step above it
  (`#101a2e`, white), and `--bg-panel` / `--bg-card` / `--bg-primary` /
  `--card-bg` are the card; overlays (menus, popovers, toasts) are the card's
  surface with a deeper shadow; `--surface-sunken`, the well (code, table
  heads, an inset block), is below the wall in both themes; inputs are
  transparent, so they take the surface they sit on -- status colours with
  `-subtle` and `-border` variants and `--on-*` inks for fills, `--cat-1..6`,
  and the card shape (`--card-radius` 14px, `--card-padding`, a layered
  `--card-shadow` per theme with a top highlight, `--card-hairline`). One
  grouped card rule paints `.card` and every card family with the card
  surface and shadow, and no family paints itself. Only a card is the card
  colour: a column or band of chrome (the chat page's session list, a
  drawer) is `--surface-chrome` with a hairline -- the session list read
  `--bg-panel` and showed as a lighter column; on a phone both drawers and
  their backdrop sit above the sticky chat composer (it covered the session
  list's footer). Gate: `tests/e2e/test_chrome_is_the_page.py` (every page
  and Settings tab, desktop and phone, both themes; the old session-list
  rule and the old stacking as controls). The dark accent is
  measured against the card (`accent_colors.DARK_CARD`): it must stand 3:1
  off the brightest dark surface it sits on. The accent is six `--accent-chosen-*`
  seeds (a dark and a light variant, each with its own readable ink,
  `kazma_ui/accent_colors.py` and `components.js` share
  `tests/fixtures/accent_variants.json`), rendered on `<html>` by the server;
  CSS derives every accent shade with `color-mix()`, and Settings applies or
  saves an accent only when the owner chose one (the retired indigo is a
  `RetiredDefault`). Gate: `tests/test_ui_color_tokens.py` (every `var(--x)`
  defined, both themes, the surface order, the card rule, 4.5:1 text in both
  themes, no selector coloured differently in the two stylesheets; each with
  its negative control) and `tests/js/test_accent_family.js`.
- **The shell and the shared components are defined once, in `kazma.css`**
  (2026-11-09, the refresh's stage 2). The shell: the sidebar is the wall
  with a hairline on its inner edge; menu items are 45px pills, hover takes
  the accent ink and moves 4px along the reading direction, the active item
  is a filled accent pill with the `--on-accent` ink; the header is
  chrome (the wall) with a hairline along its bottom edge that meets the
  menu's (transparent with no border, it ran into the page once the page
  darkened), with round 40px icon buttons, a search field
  that opens the Ctrl+K palette, and menus (the bell, the account) that are
  the overlay surface with the overlay shadow, rows leading with an icon tile. The
  shared classes a page uses and never redefines (nor `kazma.v5.css`, which
  loads last): `.btn` with `.btn-primary` (filled), `.btn-secondary` /
  `-success` / `-warning` / `-danger` (soft: the colour's 12% tint and its
  ink), `.btn-danger-solid` (a destructive confirm), `.btn-outline`,
  `.btn-ghost`, `.btn-icon`; `.badge-*` pills (an error is `.badge-danger`;
  the legacy names are aliases); `.tone-*` for `.icon-tile`, `.stat-tile` and
  `.notice` (`.alert` is the same thing: tinted, never a stripe);
  `.page-banner`; `.card-header` / `.card-title` / `.card-sub` /
  `.card-nested`; the 40px form fields, `.form-label`; `.toggle`; `.tabs`
  pills; `.segmented`; `.data-table`; `.dropdown`; `.empty-state`;
  `.progress` (the track) with `.progress-bar` (the fill). A soft button or
  a badge on another tinted surface (an approval card, a notice, the page
  banner, a stat tile) lays its wash over the card's colour
  (`--surface-card`), never over the tinted surface's: two washes stacked put
  its ink under 4.5:1. A card does not
  lift on hover, and titles and numbers are plain ink. Every
  `--accent-chosen-*` seed falls back to the theme's accent (an install
  with no chosen accent has no seeds). Logical properties only: the shell mirrors in Arabic with no
  `[dir="rtl"]` override. Canvas and SVG drawings that read tokens redraw on
  `kazma:themechange` (on `document`, detail `{theme, reason}`), dispatched
  by `syncDocumentColorScheme` when the theme changes and `applyAccentColor`
  when the accent does (Auto follows the OS while the page is open). Gates:
  `tests/test_ui_shared_components.py` (every shared class defined, none in
  v5, the active item and the chosen tab filled pills, soft buttons, no lift,
  no gradient text, soft buttons and badges 4.5:1 on every tinted surface,
  no literal colour outside the token blocks but the declared brand marks, a
  ratchet at 0; each with its negative control),
  `tests/test_ui_color_tokens.py::test_no_chosen_accent_renders_the_theme_accent`
  and `tests/js/test_theme_change_event.js`.
- **`x-cloak` is GLOBAL — do not re-introduce the blink.** The rule
  `[x-cloak] { display: none !important; }` lives once in `kazma.css`
  (`base.html` then loads `kazma.v5.css` — last-wins for overlapping
  selectors; do not duplicate HITL/card rules). Any `x-show`-gated panel
  MUST also carry `x-cloak`. Never put `display:flex` (or any `display`) in
  an inline `style` on an `x-show` element — the inline declaration wins
  over Alpine's `display:none` toggle; put flex layout in a CSS class
  instead (see `.system-alerts-banner`).
- **A boolean binding is a boolean** (2026-11-23, found building S2-04;
  corrected the same day). The vendored Alpine (3.14.8) sets a boolean
  attribute (`disabled`, `hidden`, `required`, `readonly`, `open`...) for
  every value but `null`, `undefined` and `false` (`0` and `""` set it), and
  turns an `undefined` into `""` when the expression's text holds a `.`
  (measured in Chromium): `:disabled="busy[p.provider]"` or
  `:disabled="e.protected"` DISABLES the control while the value is missing,
  and a bare `:disabled="busy"` does not. Bind `!!value`, a comparison or a
  value that is always `true` or `false`. Gate:
  `tests/test_alpine_boolean_bindings.py` (every dotted binding of an
  attribute on Alpine's own list, read from the vendored file, is provably a
  boolean or declared; 20 were not, all latent; planted shapes as controls),
  `tests/e2e/test_alpine_boolean_binding_trap.py` (the gate flags exactly
  what the browser disables).
- **Responsive grids:** use `class="two-col-grid"` (collapses to one column
  ≤768px via `kazma.css`) on any inline `grid-template-columns:1fr 1fr;` —
  bare inline 2-col grids don't collapse and crush on mobile.
